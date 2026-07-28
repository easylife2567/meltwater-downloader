"""实时诊断：token 拦截为什么在实际运行中失败。

默认不跑（需要凭证 + RUN_LIVE_TESTS=1）。它会执行真实登录 + 访问 explore 页，
把每一条 token 相关响应都记录下来并打印报告，让你看清运行时到底哪一步丢了 token。

跑法：
    RUN_LIVE_TESTS=1 python -m pytest tests/test_token_live.py -s
或直接：
    RUN_LIVE_TESTS=1 python tests/test_token_live.py

报告里重点看：
- 有没有 resetToken 响应到达（resetToken responses 计数）
- resetToken 响应的 keys 是 ['token'] 还是 ['access_token', ...]（决定是否触发根因 #1）
- json_ok=False / json_error（根因 #2：body 读不出来）
- 各响应的时间 t（resetToken 是否在 ensure_token 的等待窗口之后才到 -> 时序问题）
- _call_reset_token_api 直接调用的结果（绕过拦截，验证 resetToken 端点本身是否可用）
- verify_token（用拦截到的 token 调 1 条 msearch，验证 token 是否真是 API token）
"""
import asyncio
import os
import time

import pytest
from dotenv import load_dotenv

from scraper.browser import BrowserSession
from scraper.api_client import MeltwaterAPIClient
from main import load_config

load_dotenv()

_LIVE = bool(os.getenv("RUN_LIVE_TESTS")) and bool(os.getenv("MELTWATER_USERNAME"))

pytestmark = pytest.mark.skipif(
    not _LIVE,
    reason="需要 RUN_LIVE_TESTS=1 且 .env 里有 MELTWATER_USERNAME/PASSWORD",
)


def _redact(obj):
    """脱敏：token 只显示前 40 字符，避免 JWT 里的 PII 进日志。"""
    if isinstance(obj, dict):
        a = obj.get("access_token")
        return {
            "access_token": (a[:40] + "...") if a else None,
            "refresh_token": obj.get("refresh_token"),
        }
    return obj


def _print_report(report):
    print("\n" + "=" * 72)
    print("TOKEN INTERCEPTION DIAGNOSTIC")
    print("=" * 72)
    print(f"login_ok                : {report['login_ok']}")
    print(f"token-related responses : {len(report['responses'])}")
    for i, r in enumerate(report["responses"]):
        print(f"  [{i}] {r}")
    print(f"resetToken responses    : {report['resetToken_count']}")
    print(f"final session.api_token : {report['final_token']}")
    print(f"_call_reset_token_api   : {_redact(report['reset_api'])}")
    print(f"verify_token (1-item)   : {report['verify']}")
    print("=" * 72)


def test_diagnose_token_interception():
    report = asyncio.run(_run_diagnosis())
    _print_report(report)
    # 诊断的核心是“打印出来供人判断”，只做最低断言：登录本身要成功
    assert report["login_ok"], "登录本身就失败了，先检查凭证/网络（可能 headless 被风控、或需要 MFA）"


async def _run_diagnosis() -> dict:
    config = load_config()
    captured: list = []
    t0 = time.monotonic()

    async with BrowserSession(config) as session:
        # 额外观察者：记录每条 token 相关响应（生产用的 _handle_token_response 仍在跑）
        async def observe(response):
            if not BrowserSession.is_token_response_url(response.url):
                return
            rec = {
                "t": round(time.monotonic() - t0, 2),
                "url": response.url,
                "status": response.status,
            }
            try:
                data = await response.json()
                rec["json_ok"] = True
                rec["keys"] = list(data.keys()) if isinstance(data, dict) else type(data).__name__
            except Exception as e:
                rec["json_ok"] = False
                rec["json_error"] = f"{type(e).__name__}: {e}"
            captured.append(rec)

        session.page.on("response", observe)

        login_ok = await session.login()

        # 访问 explore 触发 resetToken / msearch
        try:
            await session.page.goto(
                "https://app.meltwater.com/a/explore/list",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await asyncio.sleep(5)
        except Exception as e:
            captured.append({"note": "explore nav failed", "error": str(e)})

        client = MeltwaterAPIClient(session.page, browser_session=session)

        # 直接调 resetToken API，绕过“响应拦截”，看端点本身返回什么
        try:
            reset_api = await client._call_reset_token_api()
        except Exception as e:
            reset_api = {"error": f"{type(e).__name__}: {e}"}

        # 用拦截到的 token 验证是否真是 API token（会消耗 1 次 msearch 调用）
        if session.api_token:
            client.access_token = session.api_token
            try:
                verify = await client.verify_token()
            except Exception as e:
                verify = f"{type(e).__name__}: {e}"
        else:
            verify = "no token intercepted"

        final_token = (session.api_token[:40] + "...") if session.api_token else None

    resetToken_count = sum(1 for r in captured if "resetToken" in r.get("url", ""))
    return {
        "login_ok": login_ok,
        "responses": captured,
        "resetToken_count": resetToken_count,
        "final_token": final_token,
        "reset_api": reset_api,
        "verify": verify,
    }


if __name__ == "__main__":
    if not _LIVE:
        print("先设置 RUN_LIVE_TESTS=1，并在 .env 里填 MELTWATER_USERNAME/PASSWORD")
        raise SystemExit(1)
    _print_report(asyncio.run(_run_diagnosis()))
