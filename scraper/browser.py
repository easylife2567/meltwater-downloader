"""浏览器启动与登录封装"""
import asyncio
import sys
from pathlib import Path
from typing import Optional
from playwright.async_api import async_playwright, Page, Browser, BrowserContext
from loguru import logger

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.paths import get_data_path


class BrowserSession:
    def __init__(self, config: dict):
        self.cfg = config
        self._playwright = None
        self.browser: Browser = None
        self.context: BrowserContext = None
        self.page: Page = None
        self.api_token = None  # 存储拦截到的 API token
        self._token_future: Optional[asyncio.Future] = None  # wait_for_api_token 的等待点

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def start(self):
        self._playwright = await async_playwright().start()

        self.browser = await self._playwright.chromium.launch(
            headless=self.cfg["scraper"]["headless"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        self.page = await self.context.new_page()

        # 注入反检测脚本
        await self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        """)

        # 拦截 API token 响应（逻辑抽到 _handle_token_response / pick_token，便于单测）
        self.page.on("response", self._handle_token_response)
        self.page.on("console", lambda msg: logger.debug(f"Browser: {msg.text}"))
        self.page.on("requestfailed", lambda req: logger.debug(f"Request failed: {req.url}"))

        logger.info("Browser started (anti-detection)")

    # ── Token 拦截逻辑（从原 handle_response 闭包抽出，便于单测） ──

    @staticmethod
    def is_token_response_url(url: str) -> bool:
        """URL 是否可能是 token 相关响应。与原闭包的匹配规则一致。"""
        return any(k in url for k in ('token', 'oauth', 'auth', 'resetToken'))

    @staticmethod
    def pick_token(url: str, status: int, data, current_token) -> Optional[str]:
        """从一条 token 相关响应中挑选要保留的 token。

        返回值含义：
        - 非 None：用该值覆盖 self.api_token
        - None：保持 self.api_token 不变

        与原闭包选择逻辑一致：
        - resetToken URL 且 data 含 'token' 键 -> 始终覆盖（resetToken 是真正的 API token）
        - 否则，仅当当前无 token 时，才从备选键取值
        - 非 200 或非 dict -> 不取
        """
        if status != 200:
            return None
        if not isinstance(data, dict):
            return None
        if 'resetToken' in url and 'token' in data:
            return data['token']
        if not current_token:
            return (data.get('access_token') or data.get('accessToken')
                    or data.get('token') or data.get('idToken') or data.get('id_token'))
        return None

    async def _handle_token_response(self, response) -> None:
        """处理每一条响应，命中 token 相关 URL 时尝试提取 token。"""
        if not self.is_token_response_url(response.url):
            return
        try:
            logger.info(f"Token-related response: {response.url} {response.status}")
            if response.status != 200:
                return
            data = await response.json()
            keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
            logger.info(f"Token endpoint response keys: {keys}")
            token = self.pick_token(response.url, response.status, data, self.api_token)
            if token:
                self.api_token = token
                logger.info(f"✓ Intercepted token from {response.url}: {token[:30]}...")
                self._notify_token_waiter(token)
        except Exception as e:
            es = str(e)
            # 200 响应却读不出 body（Network.getResponseBody）= token 可能静默丢失，升级到 INFO
            if response.status == 200 and ("getResponseBody" in es or "No resource" in es):
                logger.info(f"⚠️ 200 响应 body 读取失败，token 可能丢失: {response.url} -> {es}")
            else:
                logger.debug(f"token response parse skipped: {response.url} ({type(e).__name__})")

    def _notify_token_waiter(self, token: str) -> None:
        """若有 wait_for_api_token 在等，把刚拿到的 token 通知它。"""
        fut = self._token_future
        if fut is not None and not fut.done():
            try:
                fut.set_result(token)
            except asyncio.InvalidStateError:
                pass  # 已被取消/已设置，忽略

    async def wait_for_api_token(self, timeout: float = 30.0) -> Optional[str]:
        """等待 resetToken 响应被拦截到。

        - 若 api_token 已存在，立即返回；
        - 否则挂起等待，直到 handler 拦截到 token 或超时。

        替代原来的固定 sleep(2)/sleep(3)，消除"resetToken 晚到"的时序竞争。
        """
        if self.api_token:
            return self.api_token
        if self._token_future is None or self._token_future.done():
            self._token_future = asyncio.get_running_loop().create_future()
        try:
            return await asyncio.wait_for(self._token_future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"等待 token 拦截超时（{timeout}s）")
            return None

    async def _fill_email(self, auth: dict) -> bool:
        """在 Meltwater 登录页填入邮箱并点击继续按钮。返回 True 表示成功跳转。"""
        page = self.page

        email_input = await page.query_selector("input[type='email']")
        if not email_input:
            logger.error("Email input not found on login page")
            return False

        logger.info("Email input found, filling...")
        await email_input.click()
        await email_input.fill(auth["username"])
        await asyncio.sleep(0.5)

        # 点击 Continue/Next 按钮（Web Component，无法用文本定位）
        btn = await page.query_selector("button[type='button']")
        if btn:
            logger.info("Clicking continue button...")
            await btn.evaluate("el => el.click()")
        else:
            logger.warning("No continue button found, trying Enter key")
            await page.keyboard.press("Enter")

        # 等待跳转到 Auth0
        try:
            await page.wait_for_url("**/authorize**", timeout=15000)
            logger.info(f"Redirected to Auth0: {page.url[:80]}")
            return True
        except Exception:
            # 可能已经跳转到 Auth0 密码页（URL 含 u/login 或 authorize）
            current = page.url
            if "authorize" in current or "u/login" in current:
                logger.info(f"On Auth0 page: {current[:80]}")
                return True
            logger.warning(f"No redirect to Auth0; URL: {current[:80]}")
            return False

    async def _fill_password(self, auth: dict) -> bool:
        """在 Auth0 密码页填入密码并点击登录按钮。返回 True 表示成功提交。"""
        page = self.page

        # 等待密码输入框出现
        try:
            await page.wait_for_selector("input[type='password']", timeout=10000)
        except Exception:
            logger.error("Password input not found on Auth0 page")
            return False

        await asyncio.sleep(1)

        pwd_input = await page.query_selector("input[type='password']")
        if pwd_input:
            await pwd_input.click()
            await pwd_input.fill(auth["password"])
            logger.info("Password entered")
        else:
            return False

        await asyncio.sleep(0.5)

        # 点击 Login/Sign in 按钮
        login_btn = await page.query_selector("button[type='submit']")
        if not login_btn:
            login_btn = await page.query_selector("button[name='submit']")
        if not login_btn:
            login_btn = await page.query_selector("button[value='submit']")
        if login_btn:
            logger.info("Clicking login button...")
            await login_btn.evaluate("el => el.click()")
            return True
        else:
            logger.warning("Login button not found, trying Enter")
            await page.keyboard.press("Enter")
            return True

    async def _check_captcha(self) -> bool:
        """检测页面是否出现 CAPTCHA 人机验证。"""
        try:
            content = await self.page.content()
            lower = content.lower()
            if "captcha" in lower or "are you a robot" in lower or "verify you are human" in lower:
                logger.warning("⚠️ CAPTCHA detected on page")
                return True
        except Exception:
            pass  # 页面正在跳转，无法读取内容
        return False

    async def login(self) -> bool:
        auth = self.cfg["auth"]
        site = self.cfg["site"]

        logger.info(f"Username: {auth['username']}")
        logger.info(f"Password: {auth['password'][:2]}**")

        # ── Step 1: 导航到登录页 ──
        logger.info(f"Navigating to login page: {site['login_url']}")
        try:
            await self.page.goto(site["login_url"], wait_until="load", timeout=30000)
        except Exception:
            pass

        await asyncio.sleep(6)

        current_url = self.page.url
        logger.info(f"Current URL: {current_url}")

        # ── Step 2: 填写邮箱（若未跳转至 Auth0） ──
        if "authorize" in current_url or "auth0" in current_url:
            logger.info("Already on Auth0 page, skipping email step")
        else:
            await self._fill_email(auth)
            # 给 Auth0 重定向更多时间（可能有多次重试）
            for i in range(6):
                await asyncio.sleep(2)
                current_url = self.page.url
                if "authorize" in current_url or "u/login" in current_url:
                    break

        # ── Step 3: 填写密码 ──
        current_url = self.page.url
        if "authorize" in current_url or "auth0" in current_url or "u/login" in current_url:
            await self._fill_password(auth)
            await asyncio.sleep(3)

        # ── Step 4: 检查 CAPTCHA ──
        if await self._check_captcha():
            logger.error("CAPTCHA 人机验证拦截，headless 模式无法通过")
            await self.page.screenshot(path=str(get_data_path("captcha_detected.png")))
            return False

        # ── Step 5: 截图调试 ──
        try:
            await self.page.screenshot(path=str(get_data_path("after_submit.png")))
        except Exception:
            pass

        # ── Step 6: 验证登录成功 ──
        try:
            await self.page.wait_for_url("**/home**", timeout=20000)
            logger.info("Login successful - redirected to home page")
            return True
        except Exception:
            current_url = self.page.url
            logger.info(f"URL after login attempt: {current_url[:120]}")

            # 检查是否还在 Auth0 密码页（登录失败）
            if "u/login" in current_url or "password" in current_url:
                logger.error("Still on Auth0 password page — credentials may be wrong or MFA required")
                return False

            if "home" in current_url or "dashboard" in current_url or "sso-callback" in current_url:
                logger.info("Login successful")
                return True

            if "mfa" in current_url.lower() or "verification" in current_url.lower():
                logger.error("MFA/2FA required — cannot proceed in headless mode")
                return False

            # 兜底：检查页面成功指示器
            try:
                await self.page.wait_for_selector("[class*='dashboard'], [class*='home']", timeout=5000)
                logger.info("Login successful (detected dashboard elements)")
                return True
            except Exception:
                pass

            logger.error("Login failed — could not verify success")
            return False

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser closed")

    async def get_token(self) -> str:
        """获取 Meltwater API token"""
        import asyncio

        # 等待一段时间确保 token 被拦截
        await asyncio.sleep(2)

        if self.api_token:
            logger.info(f"Using intercepted API token: {self.api_token[:30]}...")
            return self.api_token

        logger.error("No API token was intercepted during login")
        return ""
