"""回归测试：单会话长时间爬取的健壮性（修复根因 A/B/C/D 后）。

用 FakePage 离线验证：
- msearch 错误分支返回 ([], {}) 而非裸 []（根因 A）
- page.evaluate 异常被捕获、超时被兜底，不再传播崩溃（根因 B/C）
- msearch_all 不再因单次错误崩溃，而是重试后优雅停止（根因 A+D）
- 瞬时错误后能恢复（根因 D）

跑：
    python -m pytest tests/test_crawl_failures.py -v
"""
import asyncio

import pytest
from datetime import datetime

from scraper.api_client import MeltwaterAPIClient
from tests.conftest import FakePage, run_async

FROM = datetime(2026, 1, 1)
TO = datetime(2026, 1, 2)


def _client(evaluate_result=None, evaluate_exc=None, page=None) -> MeltwaterAPIClient:
    """带假 page 的 client；塞 fake token 绕过 access_token 守卫；退避置 0 加速测试。"""
    p = page or FakePage(evaluate_result=evaluate_result, evaluate_exc=evaluate_exc)
    c = MeltwaterAPIClient(p)
    c.access_token = "fake_token"
    c.RETRY_BACKOFF_BASE = 0  # 测试中不真等
    return c


# ───────────────────────── 根因 A：错误分支返回 ([], {}) ─────────────────────────

class TestMsearchErrorReturn:
    @pytest.mark.parametrize("evaluate_result", [
        pytest.param({"error": "network boom"}, id="fetch_error"),
        pytest.param({"status": 500, "body": "Internal Server Error", "ok": False}, id="5xx"),
        pytest.param({"status": 429, "body": "Too Many Requests", "ok": False}, id="429"),
        pytest.param({"status": 200, "body": "<html>Bad Gateway</html>", "ok": True}, id="json_decode"),
    ])
    def test_error_returns_empty_tuple(self, evaluate_result):
        result = run_async(_client(evaluate_result=evaluate_result).msearch("x", FROM, TO))
        assert result == ([], {})


# ───────────────────────── 根因 B/C：evaluate 异常与超时不崩溃 ─────────────────────────

class TestMsearchCallRobustness:
    def test_evaluate_exception_propagates_from_msearch(self):
        """msearch 本身不捕获 evaluate 异常（由 msearch_all 兜底，见下个测试）。"""
        c = _client(evaluate_exc=RuntimeError("Execution context was destroyed"))
        with pytest.raises(RuntimeError):
            run_async(c.msearch("x", FROM, TO))

    def test_evaluate_exception_no_crash_in_msearch_all(self):
        """Fix B：msearch_all 兜住 msearch 抛出的异常 -> 重试后优雅返回空，不崩溃。"""
        c = _client(evaluate_exc=RuntimeError("Target page has been closed"))
        assert run_async(c.msearch_all("x", FROM, TO)) == []


# ───────────────────────── 根因 D：单页错误重试、瞬时错误可恢复 ─────────────────────────

class TestMsearchAllRetry:
    def test_persistent_error_retries_then_stops(self):
        c = _client(evaluate_result={"status": 503, "body": "Service Unavailable", "ok": False})
        result = run_async(c.msearch_all("x", FROM, TO))
        assert result == []  # 不崩溃
        # 初始 1 次 + 重试 MAX_PAGE_RETRIES 次
        assert c.page.evaluate_calls == 1 + c.MAX_PAGE_RETRIES

    def test_transient_error_recovers(self):
        """第 1 次错误、第 2 次成功 -> 重试后拿到数据。"""
        class OneErrorThenSuccess:
            def __init__(self):
                self.calls = 0
            async def evaluate(self, script: str) -> object:
                self.calls += 1
                if self.calls == 1:
                    return {"status": 503, "body": "Service Unavailable", "ok": False}
                return {
                    "status": 200, "ok": True,
                    "body": '{"response":{"total":1,"hits":[{"id":"1","gyda":{"title":"t"}}]}}',
                }
        page = OneErrorThenSuccess()
        c = _client(page=page)
        result = run_async(c.msearch_all("x", FROM, TO))
        assert len(result) == 1
        assert page.calls == 2  # 1 次失败 + 1 次成功

    def test_genuine_empty_does_not_retry(self):
        """API 正常返回 0 条（有 response）-> 直接停止，不重试。"""
        c = _client(evaluate_result={
            "status": 200, "ok": True,
            "body": '{"response":{"total":0,"hits":[]}}',
        })
        result = run_async(c.msearch_all("x", FROM, TO))
        assert result == []
        assert c.page.evaluate_calls == 1  # 真正无数据，只调 1 次


# ───────────────────────── 根因 C：msearch 调用超时被兜底 ─────────────────────────

class TestMsearchTimeout:
    def test_timeout_retried_then_stops(self):
        """单次 msearch 挂住 -> wait_for 超时 -> 视为失败重试 -> 优雅停止，不挂死。"""
        class SlowPage:
            def __init__(self):
                self.calls = 0
            async def evaluate(self, script: str) -> object:
                self.calls += 1
                await asyncio.sleep(10)  # 模拟挂住的请求
                return {"status": 200, "ok": True, "body": "{}"}

        page = SlowPage()
        c = MeltwaterAPIClient(page)
        c.access_token = "fake_token"
        c.RETRY_BACKOFF_BASE = 0
        c.MSEARCH_TIMEOUT = 0.2  # 压小超时，避免测试等 120s
        result = run_async(c.msearch_all("x", FROM, TO))
        assert result == []  # 超时被兜底，不挂死
        assert page.calls == 1 + c.MAX_PAGE_RETRIES  # 重试了 MAX 次
