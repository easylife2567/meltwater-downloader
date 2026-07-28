"""Shared test fixtures and helpers for token-interception tests."""
import asyncio
import sys
from pathlib import Path

# 让测试可以直接 import scraper.* / main 等
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def run_async(coro):
    """同步运行一个协程，避免引入 pytest-asyncio 依赖。"""
    return asyncio.run(coro)


class FakeResponse:
    """Playwright Response 的最小替身，供 _handle_token_response 单测使用。

    复刻被用到的方法/属性：
    - .url: str
    - .status: int
    - .json(): async，返回预设 data 或抛预设异常
    - .json_called: 记录 json() 是否被调用（用于断言非 200 时不读 body）
    """

    def __init__(self, url, status=200, json_data=None, json_exc=None):
        self.url = url
        self.status = status
        self._json_data = json_data
        self._json_exc = json_exc
        self.json_called = False

    async def json(self):
        self.json_called = True
        if self._json_exc is not None:
            raise self._json_exc
        return self._json_data


class FakePage:
    """Playwright Page 的最小替身，供 api_client 单测使用（无需浏览器）。

    只复刻 msearch 用到的 page.evaluate：返回预设结果，或抛预设异常。
    """

    def __init__(self, evaluate_result: object = None, evaluate_exc: BaseException | None = None):
        self._evaluate_result: object = evaluate_result
        self._evaluate_exc: BaseException | None = evaluate_exc
        self.evaluate_calls: int = 0

    async def evaluate(self, script: str) -> object:
        self.evaluate_calls += 1
        if self._evaluate_exc is not None:
            raise self._evaluate_exc
        return self._evaluate_result


def make_session(api_token=None):
    """构造一个未启动浏览器的 BrowserSession，仅用于测试拦截逻辑。

    BrowserSession.__init__ 不做任何 I/O，pick_token / _handle_token_response
    也不依赖 page，所以这样就能单测。
    """
    from scraper.browser import BrowserSession
    s = BrowserSession({})
    s.api_token = api_token
    return s


RESET_TOKEN_URL = (
    "https://live.gaf-identity-provider.meltwater.io/auth/resetToken"
    "?rememberMe=false&isActive=true"
)
OAUTH_URL = "https://auth.meltwater.com/oauth/token"
UNRELATED_URL = "https://unified-search.meltwater.io/1.0/accounts/x/msearch"
