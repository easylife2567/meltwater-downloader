"""单元测试：Meltwater API token 拦截逻辑。

这些测试只覆盖 *逻辑层* 的失败模式（已抽到 BrowserSession.pick_token /
_handle_token_response），无需浏览器、无需凭证，离线即可跑。

要定位 *运行时* 根因（token 响应没到达、body 读不出来、时序），请跑
test_token_live.py 里的实时诊断。

跑：
    python -m pytest tests/test_token_interception.py -v
"""
import asyncio

import pytest

from scraper.browser import BrowserSession
from tests.conftest import (
    FakeResponse, run_async, make_session,
    RESET_TOKEN_URL, OAUTH_URL, UNRELATED_URL,
)


# ───────────────────────── is_token_response_url ─────────────────────────

class TestIsTokenResponseUrl:
    def test_matches_resetToken_endpoint(self):
        assert BrowserSession.is_token_response_url(RESET_TOKEN_URL)

    def test_matches_oauth(self):
        assert BrowserSession.is_token_response_url(OAUTH_URL)

    def test_does_not_match_msearch(self):
        assert not BrowserSession.is_token_response_url(UNRELATED_URL)

    def test_does_not_match_plain_feed(self):
        assert not BrowserSession.is_token_response_url("https://x.com/api/feed")


# ───────────────────────── pick_token（纯逻辑） ─────────────────────────
# pick_token(url, status, data, current_token) -> str | None
#   非 None = 用该值覆盖 api_token；None = 保持不变

class TestPickTokenHappyPath:
    def test_resetToken_with_token_key_when_empty(self):
        assert BrowserSession.pick_token(RESET_TOKEN_URL, 200, {"token": "abc"}, None) == "abc"

    def test_resetToken_with_token_key_overwrites_existing_oauth(self):
        # resetToken 是真正的 API token，即使先拦截到了 OAuth token 也应覆盖
        assert BrowserSession.pick_token(RESET_TOKEN_URL, 200, {"token": "api"}, "oauth_x") == "api"

    def test_resetToken_with_access_token_key_and_no_current(self):
        # resetToken 响应里没有 'token' 键、只有 access_token，且当前无 token -> 取 access_token
        assert BrowserSession.pick_token(RESET_TOKEN_URL, 200, {"access_token": "abc"}, None) == "abc"

    def test_oauth_url_picks_access_token_when_empty(self):
        assert BrowserSession.pick_token(OAUTH_URL, 200, {"access_token": "x"}, None) == "x"

    def test_picks_first_non_empty_among_alternate_keys(self):
        data = {"access_token": "", "token": "", "idToken": "x"}
        assert BrowserSession.pick_token(OAUTH_URL, 200, data, None) == "x"


class TestPickTokenFailureModes:
    """这些测试文档化当前逻辑里会导致 token 拦截失败的路径。"""

    def test_BUG_resetToken_with_access_token_key_dropped_when_oauth_already_intercepted(self):
        """根因候选 #1：OAuth token 先到位，resetToken 响应字段名是 access_token 而非 'token'。

        此时 resetToken 分支（要求 data 含 'token' 键）不命中，
        elif 分支又要求当前无 token（但已有 OAuth token），于是返回 None ->
        真正的 API token 被丢弃，留下无 API 权限的 OAuth token。
        """
        result = BrowserSession.pick_token(
            RESET_TOKEN_URL, 200, {"access_token": "real_api"}, current_token="oauth_earlier"
        )
        assert result is None  # 当前行为：丢弃真 API token（BUG）

    def test_non_resetToken_url_does_not_overwrite_existing(self):
        # 非 resetToken 响应不会覆盖已有 token（仅当当前为空才取）
        assert BrowserSession.pick_token(OAUTH_URL, 200, {"access_token": "new"}, "existing") is None

    def test_status_401_returns_none(self):
        assert BrowserSession.pick_token(RESET_TOKEN_URL, 401, {"token": "x"}, None) is None

    def test_status_500_returns_none(self):
        assert BrowserSession.pick_token(RESET_TOKEN_URL, 500, {"token": "x"}, None) is None

    def test_non_dict_data_returns_none_without_crash(self):
        # 响应体不是 JSON 对象（如数组、字符串）时不应崩
        assert BrowserSession.pick_token(RESET_TOKEN_URL, 200, ["a", "b"], None) is None
        assert BrowserSession.pick_token(RESET_TOKEN_URL, 200, "notjson", None) is None

    def test_no_token_key_returns_none(self):
        assert BrowserSession.pick_token(RESET_TOKEN_URL, 200, {"foo": "bar"}, None) is None

    def test_resetToken_with_empty_token_value_returns_empty_string(self):
        # pick_token 返回 ''（注意：'' 是 falsy，_handle_token_response 不会真正写入）
        assert BrowserSession.pick_token(RESET_TOKEN_URL, 200, {"token": ""}, None) == ""


# ───────────────────────── _handle_token_response（含 I/O） ─────────────────────────

class TestHandleTokenResponse:
    def test_sets_token_from_resetToken(self):
        s = make_session(api_token=None)
        resp = FakeResponse(RESET_TOKEN_URL, 200, {"token": "abc"})
        run_async(s._handle_token_response(resp))
        assert s.api_token == "abc"

    def test_json_exception_does_not_crash_preserves_existing_token(self):
        """根因 #2：response.json() 抛异常（Playwright “Network.getResponseBody: No resource
        with given identifier found”、响应已被释放、跨导航读取 body）时，token 会丢失。

        改造后：不崩溃、api_token 保持原值；且对 200 响应的 body 读取失败会升级到 INFO 日志
        （这里只验证状态，日志不断言）。
        """
        s = make_session(api_token="existing")
        resp = FakeResponse(
            RESET_TOKEN_URL, 200,
            json_data={"token": "would_be_lost"},
            json_exc=Exception("Protocol error (Network.getResponseBody): No resource with given identifier found"),
        )
        run_async(s._handle_token_response(resp))  # 不应抛
        assert s.api_token == "existing"  # 没拿到新 token，保持原值

    def test_ignores_non_matching_url_without_reading_body(self):
        s = make_session(api_token=None)
        resp = FakeResponse(UNRELATED_URL, 200, {"token": "abc"})
        run_async(s._handle_token_response(resp))
        assert s.api_token is None
        assert resp.json_called is False  # 不相关 URL 不应读 body

    def test_skips_body_read_on_non200(self):
        # 与原闭包一致：非 200 不调用 response.json()
        s = make_session(api_token=None)
        resp = FakeResponse(RESET_TOKEN_URL, 401, json_data={"token": "x"})
        run_async(s._handle_token_response(resp))
        assert s.api_token is None
        assert resp.json_called is False

    def test_does_not_set_empty_token(self):
        s = make_session(api_token=None)
        resp = FakeResponse(RESET_TOKEN_URL, 200, {"token": ""})
        run_async(s._handle_token_response(resp))
        assert s.api_token is None  # '' falsy，不写入


# ───────────────────────── 真实时序模拟 ─────────────────────────

class TestTokenSequence:
    """模拟登录期间 OAuth token 与 resetToken token 的到达顺序。"""

    def test_oauth_then_resetToken_with_token_key_correctly_overwrites(self):
        s = make_session(api_token=None)
        run_async(s._handle_token_response(FakeResponse(OAUTH_URL, 200, {"access_token": "oauth_tok"})))
        assert s.api_token == "oauth_tok"
        run_async(s._handle_token_response(FakeResponse(RESET_TOKEN_URL, 200, {"token": "api_tok"})))
        assert s.api_token == "api_tok"  # resetToken 正确覆盖

    def test_BUG_oauth_then_resetToken_with_access_token_key_drops_real_token(self):
        """根因候选 #1 的端到端复现：

        1) OAuth 响应先到，api_token 被设成 OAuth token；
        2) resetToken 响应后到，但字段名是 access_token（无 'token' 键）->
           pick_token 返回 None -> 真 API token 被丢弃；
        3) 最终 api_token 仍是 OAuth token，用它调 msearch 会 401。
        """
        s = make_session(api_token=None)
        run_async(s._handle_token_response(FakeResponse(OAUTH_URL, 200, {"access_token": "oauth_tok"})))
        run_async(s._handle_token_response(FakeResponse(RESET_TOKEN_URL, 200, {"access_token": "api_tok"})))
        assert s.api_token == "oauth_tok"  # 当前行为：保留了错的 token（BUG）


# ───────────────────────── wait_for_api_token（事件驱动等待） ─────────────────────────

class TestWaitForApiToken:
    """wait_for_api_token：事件驱动等待 resetToken，替代原来的固定 sleep。"""

    def test_returns_immediately_if_token_already_set(self):
        async def run():
            s = make_session(api_token="already")
            return await s.wait_for_api_token(timeout=0.5)
        assert run_async(run()) == "already"

    def test_returns_none_on_timeout(self):
        async def run():
            s = make_session(api_token=None)
            return await s.wait_for_api_token(timeout=0.1)
        assert run_async(run()) is None

    def test_resolves_when_handler_captures_token(self):
        async def run():
            s = make_session(api_token=None)
            wait_task = asyncio.create_task(s.wait_for_api_token(timeout=2))
            await asyncio.sleep(0.01)  # 让等待任务注册好 future
            # 模拟 handler 拦截到 resetToken 响应
            await s._handle_token_response(FakeResponse(RESET_TOKEN_URL, 200, {"token": "abc"}))
            return await wait_task
        assert run_async(run()) == "abc"

    def test_timeout_does_not_break_later_capture(self):
        """等待超时后，handler 仍能正常工作（cancelled future 不影响后续）。"""
        async def run():
            s = make_session(api_token=None)
            first = await s.wait_for_api_token(timeout=0.1)  # 超时 -> None
            assert first is None
            # 之后 handler 捕获到 token -> 新一次等待立即拿到
            await s._handle_token_response(FakeResponse(RESET_TOKEN_URL, 200, {"token": "xyz"}))
            return await s.wait_for_api_token(timeout=1)
        assert run_async(run()) == "xyz"
