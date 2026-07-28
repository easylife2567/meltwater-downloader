"""Meltwater API 客户端 - 支持登录、token刷新和数据获取"""
import json
import asyncio
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from playwright.async_api import Page
from loguru import logger

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.paths import get_data_path


class MeltwaterAPIClient:
    """Meltwater API 客户端"""
    
    BASE_URL = "https://unified-search.meltwater.io/1.0"
    ACCOUNT_ID = "62bd23a40490b900113ddaca"
    TOKEN_FILE = "token.json"  # token缓存文件名
    MAX_PAGE_RETRIES = 3       # 单页连续失败重试次数（Fix D）
    RETRY_BACKOFF_BASE = 2     # 退避基数（秒）：BASE**n
    RETRY_BACKOFF_CAP = 60     # 退避上限（秒）
    MSEARCH_TIMEOUT = 120      # 单次 msearch 调用超时（秒，Fix C）

    def __init__(self, page: Page, browser_session=None):
        self.page = page
        self.browser_session = browser_session  # 保存browser session引用以获取拦截的token
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_expires_at: Optional[datetime] = None
        self.token_file_path = get_data_path(self.TOKEN_FILE)
    
    def _save_token_to_file(self):
        """保存token到文件"""
        try:
            token_data = {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "saved_at": datetime.now().isoformat()
            }
            self.token_file_path.parent.mkdir(exist_ok=True)
            with open(self.token_file_path, "w", encoding="utf-8") as f:
                json.dump(token_data, f, indent=2, ensure_ascii=False)
            logger.info(f"💾 Token已保存到缓存")
        except Exception as e:
            logger.warning(f"保存token失败: {e}")
    
    def _load_token_from_file(self) -> bool:
        """从文件加载token"""
        try:
            if not self.token_file_path.exists():
                return False
            
            with open(self.token_file_path, "r", encoding="utf-8") as f:
                token_data = json.load(f)
            
            self.access_token = token_data.get("access_token")
            self.refresh_token = token_data.get("refresh_token")
            saved_at = token_data.get("saved_at")
            
            if self.access_token:
                # 解析保存时间
                try:
                    saved_time = datetime.fromisoformat(saved_at)
                    time_diff = datetime.now() - saved_time
                    hours = int(time_diff.total_seconds() / 3600)
                    minutes = int((time_diff.total_seconds() % 3600) / 60)
                    
                    if hours > 0:
                        time_str = f"{hours}小时{minutes}分钟前"
                    else:
                        time_str = f"{minutes}分钟前"
                    
                    logger.info(f"📦 找到缓存的token（保存于{time_str}）")
                except:
                    logger.info(f"📦 找到缓存的token")
                
                return True
            else:
                return False
        except Exception as e:
            return False
    
    async def verify_token(self) -> bool:
        """
        验证当前token是否有效
        通过调用一个简单的API来测试token
        
        Returns:
            bool: token是否有效
        """
        if not self.access_token:
            return False
        
        logger.info("🔍 验证token有效性...")
        
        # 使用一个简单的API调用来验证token
        # 这里使用msearch API，但只请求1条数据
        from_date = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=1)
        to_date = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
        
        api_url = f"{self.BASE_URL}/accounts/{self.ACCOUNT_ID}/msearch"
        query = self._build_search_query("24944745", from_date, to_date, 1, 0)
        
        result = await self.page.evaluate(f"""
            async () => {{
                const url = '{api_url}';
                const body = {json.dumps(query)};
                const token = '{self.access_token}';
                
                try {{
                    const response = await fetch(url, {{
                        method: 'POST',
                        headers: {{
                            'Accept': '*/*',
                            'Content-Type': 'application/json',
                            'Origin': 'https://app.meltwater.com',
                            'Referer': 'https://app.meltwater.com/',
                            'authorization': 'Bearer ' + token,
                            'x-product-type': 'explore-dataservice',
                            'x-credit-pool-id': 'mi-explore-brand-volume-ip'
                        }},
                        body: JSON.stringify(body),
                        credentials: 'include'
                    }});
                    
                    return {{ 
                        status: response.status, 
                        ok: response.ok
                    }};
                }} catch (e) {{
                    return {{ error: e.message }};
                }}
            }}
        """)
        
        if result.get("ok"):
            logger.info("✓ Token验证通过")
            return True
        elif result.get("status") == 401:
            logger.warning("✗ Token已过期")
            return False
        else:
            logger.warning(f"✗ Token验证失败")
            return False
    
    async def ensure_token(self) -> bool:
        """
        确保有可用的token
        优先使用缓存的token，如果无效则重新登录
        
        Returns:
            bool: 是否成功获取有效token
        """
        logger.info("🔑 检查token缓存...")
        
        # 1. 尝试从缓存加载token
        if self._load_token_from_file():
            # 2. 验证token是否有效
            if await self.verify_token():
                logger.info("✓ 使用缓存的token（无需重新登录）")
                return True
            else:
                logger.info("⚠️  缓存的token已失效，需要重新登录获取新token...")
        else:
            logger.info("ℹ️  未找到有效的token缓存，需要重新登录...")
        
        # 3. 缓存无效或不存在，重新登录获取token
        logger.info("🔄 正在重新获取token...")
        if await self.login_and_get_token():
            # 4. 保存新token到缓存
            self._save_token_to_file()
            logger.info("✓ 新token已保存到缓存")
            return True
        
        logger.error("❌ 无法获取有效的token")
        return False
    
    async def login_and_get_token(self) -> bool:
        """获取 API token（事件驱动，替代原固定 sleep 的多级回退）。

        顺序：
        1) browser_session 已拦截到的 token（访问 explore 时 handler 捕获）
        2) 等待在途的 resetToken 响应（wait_for_api_token，最多 5s）
        3) 主动触发 resetToken 并网络层拦截（_call_reset_token_api，绕过 CORS）
        4) localStorage 兜底

        前提：调用前通常已完成登录（download_only 流程中 ensure_token 之前先 login +
        访问 explore）。这里不再重复 login()。
        """
        logger.info("正在获取初始token...")
        bs = self.browser_session

        # 方法1: 已拦截到的 token
        if bs and getattr(bs, "api_token", None):
            self.access_token = bs.api_token
            logger.info(f"从browser拦截获取到token: {self.access_token[:30]}...")
            return True

        # 方法2: 等待在途的 resetToken 响应（不再 sleep(2) 猜时间）
        if bs:
            token = await bs.wait_for_api_token(timeout=5)
            if token:
                self.access_token = token
                logger.info(f"等待拦截到 token: {token[:30]}...")
                return True

        # 方法3: 主动触发 resetToken 并拦截（导航 explore + expect_response，绕过 CORS）
        logger.info("主动触发 resetToken 获取 token…")
        token_data = await self._call_reset_token_api()
        if token_data and token_data.get("access_token"):
            self.access_token = token_data["access_token"]
            if bs:
                bs.api_token = self.access_token
            logger.info(f"通过 resetToken 拦截获取到 token: {self.access_token[:30]}...")
            return True

        # 方法4: localStorage 兜底
        token_data = await self._extract_token_from_storage()
        if token_data and token_data.get("access_token"):
            self.access_token = token_data.get("access_token")
            self.refresh_token = token_data.get("refresh_token")
            logger.info(f"从localStorage获取到token: {self.access_token[:30]}...")
            return True

        logger.error("无法获取token")
        return False

    async def _extract_token_from_storage(self) -> Optional[Dict[str, str]]:
        """从浏览器存储中提取token"""
        try:
            result = await self.page.evaluate("""
                () => {
                    // 检查 localStorage
                    const keys = Object.keys(localStorage);
                    for (const key of keys) {
                        try {
                            const item = localStorage.getItem(key);
                            if (!item) continue;
                            
                            // 尝试解析JSON
                            let data;
                            try {
                                data = JSON.parse(item);
                            } catch {
                                // 如果不是JSON，检查是否是JWT token
                                if (item.startsWith('eyJ')) {
                                    return { access_token: item };
                                }
                                continue;
                            }
                            
                            // 查找包含token的对象
                            if (data.access_token || data.accessToken || data.token) {
                                return {
                                    access_token: data.access_token || data.accessToken || data.token,
                                    refresh_token: data.refresh_token || data.refreshToken
                                };
                            }
                        } catch (e) {
                            console.error('Error parsing storage item:', e);
                        }
                    }
                    
                    // 检查 sessionStorage
                    const sessionKeys = Object.keys(sessionStorage);
                    for (const key of sessionKeys) {
                        try {
                            const item = sessionStorage.getItem(key);
                            if (!item) continue;
                            
                            let data;
                            try {
                                data = JSON.parse(item);
                            } catch {
                                if (item.startsWith('eyJ')) {
                                    return { access_token: item };
                                }
                                continue;
                            }
                            
                            if (data.access_token || data.accessToken || data.token) {
                                return {
                                    access_token: data.access_token || data.accessToken || data.token,
                                    refresh_token: data.refresh_token || data.refreshToken
                                };
                            }
                        } catch (e) {
                            console.error('Error parsing session item:', e);
                        }
                    }
                    
                    return null;
                }
            """)
            return result
        except Exception as e:
            logger.debug(f"无法访问localStorage (页面可能未加载): {e}")
            return None
    
    async def _trigger_token_generation(self):
        """访问页面触发token生成"""
        try:
            await self.page.goto("https://app.meltwater.com/a/explore", wait_until="networkidle", timeout=10000)
            await asyncio.sleep(2)
        except Exception as e:
            logger.warning(f"触发token生成失败: {e}")
    
    async def _call_reset_token_api(self) -> Optional[Dict[str, str]]:
        """触发并拦截 resetToken 响应来获取新 API token。

        重要：不能用 page.evaluate(fetch(...)) 直接调 resetToken——会被 CORS 拦死
        （响应不带 Access-Control-Allow-Credentials，实测 reset_api 返回 None）。
        改用 page.expect_response 在网络层捕获响应体，绕过 CORS。
        """
        try:
            async with self.page.expect_response(
                lambda r: "resetToken" in r.url, timeout=30000
            ) as resp_info:
                try:
                    await self.page.goto(
                        "https://app.meltwater.com/a/explore/list",
                        wait_until="domcontentloaded", timeout=15000,
                    )
                except Exception as e:
                    # 导航异常不致命——resetToken 可能仍会到达
                    logger.debug(f"explore 导航异常（resetToken 可能仍会到达）: {e}")
            resp = await resp_info.value
            data = await resp.json()
            token = ((data.get("token") or data.get("access_token")
                      or data.get("accessToken")) if isinstance(data, dict) else None)
            if token:
                logger.info("✓ 通过 resetToken 响应拦截到新 token")
                return {
                    "access_token": token,
                    "refresh_token": data.get("refresh_token") or data.get("refreshToken"),
                }
            logger.warning(
                "resetToken 响应无可用 token 字段: "
                f"{list(data.keys()) if isinstance(data, dict) else type(data).__name__}"
            )
        except Exception as e:
            logger.warning(f"拦截 resetToken 响应失败: {e}")
        return None
    
    async def reset_token(self) -> bool:
        """
        通过refresh token更新access token
        
        Returns:
            bool: 是否成功更新token
        """
        if not self.refresh_token:
            logger.warning("没有refresh_token，尝试重新获取...")
            return await self.login_and_get_token()
        
        logger.info("正在刷新token...")
        
        # Meltwater可能使用Auth0或自定义的token刷新端点
        # 这里提供一个通用的实现框架
        refresh_url = "https://app.meltwater.com/api/auth/refresh"  # 需要根据实际情况调整
        
        result = await self.page.evaluate(f"""
            async () => {{
                try {{
                    const response = await fetch('{refresh_url}', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json',
                        }},
                        body: JSON.stringify({{
                            refresh_token: '{self.refresh_token}'
                        }}),
                        credentials: 'include'
                    }});
                    
                    if (response.ok) {{
                        const data = await response.json();
                        return {{ success: true, data: data }};
                    }} else {{
                        return {{ success: false, status: response.status }};
                    }}
                }} catch (e) {{
                    return {{ success: false, error: e.message }};
                }}
            }}
        """)
        
        if result.get("success"):
            data = result.get("data", {})
            self.access_token = data.get("access_token") or data.get("accessToken")
            self.refresh_token = data.get("refresh_token") or data.get("refreshToken") or self.refresh_token
            logger.info(f"Token刷新成功: {self.access_token[:30] if self.access_token else 'None'}...")
            return True
        else:
            logger.error(f"Token刷新失败: {result}")
            # 如果刷新失败，尝试重新登录
            return await self.login_and_get_token()
    
    def _build_source_any_queries(self, sources: list) -> list:
        """构建平台来源 anyQueries 数组。

        Twitter/X 因为 Meltwater 索引中的历史分类原因，需要单独作为 term 查询；
        其余平台统一放入 terms 数组。mediaType=ot 兜底覆盖在线文本媒体。

        输出格式与 Meltwater 原生查询一致：
            anyQueries: [
                { field: "metaData.source.socialOriginType", values: [...], type: "terms" },
                { allQueries: [{ field: "metaData.mediaType", value: "ot", type: "term" }], type: "all" },
                { allQueries: [{ field: "metaData.source.socialOriginType", value: "twitter", type: "term" }], type: "all" },
            ]
        """
        entries = []

        # 非 twitter 平台 → terms 合并查询
        non_twitter = [s for s in sources if s.lower() != "twitter"]
        if non_twitter:
            entries.append({
                "field": "metaData.source.socialOriginType",
                "values": non_twitter,
                "type": "terms",
            })

        # mediaType=ot 兜底
        entries.append({
            "allQueries": [
                {"field": "metaData.mediaType", "value": "ot", "type": "term"}
            ],
            "type": "all",
        })

        # twitter 单独 term
        if any(s.lower() == "twitter" for s in sources):
            entries.append({
                "allQueries": [
                    {"field": "metaData.source.socialOriginType", "value": "twitter", "type": "term"}
                ],
                "type": "all",
            })

        return entries

    def _filter_sources(self, query: Dict, sources: list) -> int:
        """将查询中已有的 socialOriginType 过滤块整体替换为用户指定的平台列表。

        搜索 rune.allQueries 下的 anyQueries 块：如果其中任一条目包含
        metaData.source.socialOriginType，则重建整个 anyQueries 列表。
        返回替换次数（0 或 1）。
        """
        replaced = 0

        def _replace(container, key, index=None):
            nonlocal replaced
            val = container[key] if index is None else container[key][index]
            if not isinstance(val, dict):
                return
            any_queries = val.get("anyQueries")
            if isinstance(any_queries, list):
                has_origin = any(
                    isinstance(e, dict)
                    and e.get("field") == "metaData.source.socialOriginType"
                    for e in any_queries
                )
                if has_origin:
                    val["anyQueries"] = self._build_source_any_queries(sources)
                    replaced += 1
                    return
            for v in val.values():
                if isinstance(v, (dict, list)):
                    _replace(v, None)

        import copy

        def _walk(obj):
            nonlocal replaced
            if replaced:
                return
            if isinstance(obj, dict):
                # 找到包含 anyQueries 且其中有 socialOriginType 的节点
                aq = obj.get("anyQueries")
                if isinstance(aq, list):
                    has_origin = any(
                        isinstance(e, dict)
                        and e.get("field") == "metaData.source.socialOriginType"
                        for e in aq
                    )
                    if has_origin:
                        obj["anyQueries"] = self._build_source_any_queries(sources)
                        replaced += 1
                        return
                for v in obj.values():
                    if isinstance(v, (dict, list)):
                        _walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    if isinstance(item, (dict, list)):
                        _walk(item)

        _walk(query)

        if replaced == 0:
            logger.warning(
                "未找到已有的 socialOriginType 过滤块，请确认该 search_id 在 Meltwater 中已配置平台来源过滤"
            )

        return replaced

    def _inject_keywords(self, query: Dict, expression: str):
        """将布尔关键词表达式注入到查询的 rune 结构中。

        采用替换 values 数组的方式，保留原有的 rune 骨架结构不变。
        支持的语法:
            - OR / 逗号 = 同组内任意匹配
            - AND       = 组间必须同时满足
            - NOT       = 排除后续词组
            - 括号 ()   = 分组
            - 引号 ""   = 短语（内部空格不拆分）

        示例:
            ("蔡徐坤" OR "坤坤") AND "篮球"
            (南海, 南沙群岛) AND (海警, 海军) NOT (cn, 中国)
        """
        # ── 词法分析 ──
        tokens = []
        i = 0
        s = expression
        while i < len(s):
            ch = s[i]
            if ch in "(),":
                tokens.append(ch)
                i += 1
            elif ch == '"':
                j = i + 1
                while j < len(s) and s[j] != '"':
                    j += 1
                tokens.append(s[i + 1 : j])
                i = j + 1
            elif ch.isspace():
                i += 1
            else:
                j = i
                while j < len(s) and s[j] not in "(),\"" and not s[j].isspace():
                    j += 1
                word = s[i:j]
                upper = word.upper()
                if upper in ("AND", "OR", "NOT"):
                    tokens.append(upper)
                else:
                    tokens.append(word)
                i = j

        pos = 0

        def peek():
            return tokens[pos] if pos < len(tokens) else None

        def consume():
            nonlocal pos
            t = tokens[pos]
            pos += 1
            return t

        # ── 递归下降解析器 ──
        # Grammar:
        #   expr     → and_expr (NOT and_expr)*
        #   and_expr → or_expr (AND or_expr)*
        #   or_expr  → atom (OR/',' atom)*
        #   atom     → '(' expr ')' | phrase

        def match(expected):
            if peek() == expected:
                return consume()
            return None

        # 括号内多AND组暂存队列：parse_atom拍平时会丢失AND结构，
        # 遇到 >1 个inner_and组时只返回第一组，其余排队等parse_and认领
        _pending_and = []

        def parse_expr():
            """expr → and_expr (NOT and_expr)*"""
            and_groups = parse_and()
            not_groups = []
            while peek() == "NOT":
                consume()
                not_groups.extend(parse_and())
            return and_groups, not_groups

        def parse_and():
            """and_expr → or_expr (AND or_expr)*"""
            groups = [parse_or()]
            while peek() == "AND":
                consume()
                groups.append(parse_or())
            if _pending_and:
                groups.extend(_pending_and)
                _pending_and.clear()
            return groups

        def parse_or():
            """or_expr → atom (OR/',' atom)* — 返回该组的关键词列表"""
            keywords = list(parse_atom())
            while peek() in (",", "OR"):
                consume()
                keywords.extend(parse_atom())
            return keywords

        def parse_atom():
            """atom → '(' expr ')' | phrase — 返回关键词列表"""
            if peek() == "(":
                consume()
                inner_and, inner_not = parse_expr()
                match(")")  # consume ')'
                if len(inner_and) == 1:
                    return list(inner_and[0])
                # 多个AND组：返回第一组，其余排队到_pending_and
                _pending_and.extend([list(g) for g in inner_and[1:]])
                return list(inner_and[0])
            else:
                words = []
                while peek() and peek() not in (",", ")", "AND", "OR", "NOT"):
                    words.append(consume())
                return [" ".join(words)] if words else []

        # ── 解析执行 ──
        and_groups, not_groups = parse_expr()

        # 展平所有 AND 关键词
        all_keywords = []
        for g in and_groups:
            all_keywords.extend(g)

        if not all_keywords:
            logger.warning(f"关键词表达式解析后为空: {expression}")
            return

        # ── 注入 rune ──
        # 将布尔表达式映射为 Meltwater rune 原生结构。
        #
        # 有 NOT 时（Meltwater 原生格式）：
        #   anyQueries: [{ allQueries: [{ matchQuery: {allQueries: [正组...]},
        #                                 notMatchQuery: {allQueries: [负组...]},
        #                                 type: "not" }], type: "all" }, appTags]
        #
        # 无 NOT 时：
        #   anyQueries: [{ anyQueries: [正组...], type: "all" }, appTags]
        #
        # 每个正/负组 = { anyQueries: [{fields words}, {contentTags}], type: "any" }
        import copy as _copy

        def _fill_keywords(node, words):
            """递归替换模板中的关键词 values 和 contentTags values/value"""
            if isinstance(node, dict):
                if "values" in node and isinstance(node["values"], list) and "field" not in node:
                    node["values"] = list(words)
                if "field" in node and node.get("field") == "body.contentTags":
                    if "values" in node and isinstance(node["values"], list):
                        node["values"] = list(words)
                    elif "value" in node:
                        node["value"] = words[0] if words else ""
                for v in node.values():
                    _fill_keywords(v, words)
            elif isinstance(node, list):
                for child in node:
                    _fill_keywords(child, words)

        def _find_keyword_leaf(node):
            """递归查找最内层的关键词组模板。

            关键词组特征：anyQueries 数组包含 words 条目（有 fields 数组）
            和 contentTags 条目（field="body.contentTags"），type="any"。
            返回找到的第一个这样的 dict，或 None。
            """
            if isinstance(node, dict):
                aq = node.get("anyQueries")
                if isinstance(aq, list) and len(aq) >= 2:
                    has_words = any(
                        isinstance(e, dict) and "fields" in e for e in aq
                    )
                    has_tags = any(
                        isinstance(e, dict) and e.get("field") == "body.contentTags"
                        for e in aq
                    )
                    if has_words and has_tags and node.get("type") == "any":
                        return node
                for v in node.values():
                    if isinstance(v, (dict, list)):
                        found = _find_keyword_leaf(v)
                        if found:
                            return found
            elif isinstance(node, list):
                for item in node:
                    if isinstance(item, (dict, list)):
                        found = _find_keyword_leaf(item)
                        if found:
                            return found
            return None

        def _find_app_tags(node):
            """提取 applicationTags 条目（如有），保留到新结构中。"""
            if isinstance(node, dict):
                if node.get("field") == "metaData.applicationTags":
                    return _copy.deepcopy(node)
                for v in node.values():
                    if isinstance(v, (dict, list)):
                        found = _find_app_tags(v)
                        if found:
                            return found
            elif isinstance(node, list):
                for item in node:
                    if isinstance(item, (dict, list)):
                        found = _find_app_tags(item)
                        if found:
                            return found
            return None

        and_list = query["requests"][0]["request"]["query"]["and"]
        for item in and_list:
            if "lowLevel" not in item:
                continue
            rune = item["lowLevel"]["rune"]
            old_queries = rune["allQueries"]

            if not old_queries:
                logger.warning("rune.allQueries 为空，无法注入关键词")
                break

            leaf = _find_keyword_leaf(old_queries[0])
            app_tags = _find_app_tags(old_queries[0])

            if leaf is None:
                logger.warning("无法从 rune 中提取关键词叶模板，跳过注入")
                break

            def _make_group(words):
                entry = _copy.deepcopy(leaf)
                _fill_keywords(entry, words)
                return entry

            positive_entries = [_make_group(g) for g in and_groups]
            negative_entries = [_make_group(g) for g in not_groups]

            # 构建关键词容器（与 Meltwater 原生结构逐层对齐）。
            if negative_entries:
                # 有 NOT：allQueries[{ matchQuery, notMatchQuery, type:"not" }]
                inner = {
                    "matchQuery": {
                        "allQueries": positive_entries,
                        "type": "all",
                    },
                    "notMatchQuery": {
                        "allQueries": negative_entries,
                        "type": "all",
                    },
                    "type": "not",
                }
                kw_container = {
                    "allQueries": [inner],
                    "type": "all",
                }
            else:
                # 无 NOT：allQueries[{ allQueries[pos_groups], type:"all" }]
                kw_container = {
                    "allQueries": [{
                        "allQueries": positive_entries,
                        "type": "all",
                    }],
                    "type": "all",
                }

            # 组装外层 anyQueries
            aq_inner = [kw_container]
            if app_tags:
                aq_inner.append(app_tags)
            new_kw_entry = {"anyQueries": aq_inner, "type": "any"}

            # 替换 rune.allQueries：新关键词条目 + 原有非关键词条目
            rune["allQueries"] = [new_kw_entry] + old_queries[1:]

            not_info = f" NOT{not_groups}" if not_groups else ""
            logger.info(
                f"关键词已注入: {expression} → {len(and_groups)} 个AND组{not_info}"
            )

    async def msearch(
        self,
        search_id: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        limit: int = 25,
        start: int = 0,
        custom_query: Optional[Dict] = None,
        keywords: Optional[str] = None,
        sources: Optional[List[str]] = None,
        sort_by: str = "date",
        _retry_count: int = 0
    ) -> tuple[List[Dict[str, Any]], Dict]:
        """
        通过msearch API获取数据

        Args:
            search_id: 搜索ID
            from_date: 开始日期（默认为昨天中午12点）
            to_date: 结束日期（默认为今天中午12点）
            limit: 每页数量
            start: 起始位置
            custom_query: 自定义查询（可选）
            keywords: 替换搜索关键词列表（可选，完全替换原有关键词）
            _retry_count: 内部重试计数器

        Returns:
            tuple: (新闻列表, 响应数据)
        """
        if _retry_count >= 3:
            logger.error("重试次数过多，放弃")
            return [], {}
        
        if not self.access_token:
            logger.error("没有access_token，请先登录")
            return [], {}
        
        # 设置默认日期：昨天中午12点到今天中午12点（北京时间）
        if from_date is None:
            from_date = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=1)
        if to_date is None:
            to_date = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
        
        # 将北京时间转换为UTC时间（北京时间 = UTC+8）
        # 如果datetime对象没有时区信息，假设它是北京时间，转换后标记为UTC防止递归重试时重复转换
        from datetime import timezone as tz
        if from_date.tzinfo is None:
            from_date = (from_date - timedelta(hours=8)).replace(tzinfo=tz.utc)
        if to_date.tzinfo is None:
            to_date = (to_date - timedelta(hours=8)).replace(tzinfo=tz.utc)
        
        logger.info(f"正在调用msearch API (search_id={search_id}, from={from_date.strftime('%Y-%m-%d %H:%M')}, to={to_date.strftime('%Y-%m-%d %H:%M')}, start={start}, limit={limit}, retry={_retry_count})...")
        
        # 构建查询
        if custom_query:
            query = custom_query
            # 更新pagination、date和searchId参数
            if "requests" in query and len(query["requests"]) > 0:
                if "request" in query["requests"][0]:
                    # 更新pagination
                    if "pagination" not in query["requests"][0]["request"]:
                        query["requests"][0]["request"]["pagination"] = {}
                    query["requests"][0]["request"]["pagination"]["start"] = start
                    query["requests"][0]["request"]["pagination"]["limit"] = limit
                    
                    # 移除group参数以获取所有数据（不进行去重）
                    if "group" in query["requests"][0]["request"]["pagination"]:
                        del query["requests"][0]["request"]["pagination"]["group"]
                        logger.info("已移除 group 参数，将获取所有数据（不去重）")

                    # 更新排序
                    query["requests"][0]["request"]["pagination"]["sort"] = {
                        "on": sort_by, "order": "desc"
                    }

                    # 更新date参数
                    if "query" in query["requests"][0]["request"] and "and" in query["requests"][0]["request"]["query"]:
                        # 查找并更新date filter
                        date_filter_found = False
                        for i, filter_item in enumerate(query["requests"][0]["request"]["query"]["and"]):
                            if isinstance(filter_item, dict) and "date" in filter_item:
                                filter_item["date"]["from"] = from_date.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                                filter_item["date"]["to"] = to_date.strftime("%Y-%m-%dT%H:%M:%S.999Z")
                                date_filter_found = True
                                break
                        
                        # 如果没有找到date filter，添加一个
                        if not date_filter_found:
                            query["requests"][0]["request"]["query"]["and"].append({
                                "date": {
                                    "from": from_date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                                    "to": to_date.strftime("%Y-%m-%dT%H:%M:%S.999Z")
                                }
                            })
                        
                        # 更新searchId参数（更新所有requests中的searchId）
                        updated_count = self._update_search_id_in_query(query, search_id)
                        if updated_count > 0:
                            logger.info(f"✓ 总共更新了 {updated_count} 个 searchId 引用")
        else:
            query = self._build_search_query(search_id, from_date, to_date, limit, start, sort_by)

        # 如果用户指定了关键词，替换现有 rune 中的 values
        if keywords:
            self._inject_keywords(query, keywords)

        # 如果指定了平台来源，替换 socialOriginType
        if sources:
            replaced = self._filter_sources(query, sources)
            logger.info(f"📡 已指定平台来源: {sources} (替换 {replaced} 处)")

        # 调用API
        api_url = f"{self.BASE_URL}/accounts/{self.ACCOUNT_ID}/msearch"
        
        result = await self.page.evaluate(f"""
            async () => {{
                const url = '{api_url}';
                const body = {json.dumps(query)};
                const token = '{self.access_token}';
                
                try {{
                    const response = await fetch(url, {{
                        method: 'POST',
                        headers: {{
                            'Accept': '*/*',
                            'Content-Type': 'application/json',
                            'Origin': 'https://app.meltwater.com',
                            'Referer': 'https://app.meltwater.com/',
                            'authorization': 'Bearer ' + token,
                            'x-product-type': 'explore-dataservice',
                            'x-credit-pool-id': 'mi-explore-brand-volume-ip'
                        }},
                        body: JSON.stringify(body),
                        credentials: 'include'
                    }});
                    
                    const text = await response.text();
                    
                    return {{ 
                        status: response.status, 
                        body: text,
                        ok: response.ok
                    }};
                }} catch (e) {{
                    console.error('API call error:', e);
                    return {{ error: e.message }};
                }}
            }}
        """)
        
        # 处理响应
        if result.get("error"):
            logger.error(f"API调用错误: {result['error']}")
            return [], {}

        status = result.get("status")
        body = result.get("body", "")
        
        # 记录详细的响应信息用于调试
        logger.info(f"API响应状态: {status}")
        if not result.get("ok"):
            logger.error(f"API响应内容: {body[:500]}")
        
        if status == 401:
            logger.warning(f"Token认证失败: {body[:300]}")
            if _retry_count < 2:
                logger.info("token 失效，通过 resetToken 响应刷新…")
                # 清掉旧 token，强制重新拦截
                if self.browser_session:
                    self.browser_session.api_token = None
                # _call_reset_token_api 现在用 expect_response 网络层拦截（绕过 CORS）
                token_data = await self._call_reset_token_api()
                if token_data and token_data.get("access_token"):
                    self.access_token = token_data["access_token"]
                    if self.browser_session:
                        self.browser_session.api_token = self.access_token
                    self._save_token_to_file()
                    logger.info("✓ 通过 resetToken 获取到新 token，重试请求...")
                    return await self.msearch(
                        search_id=search_id, from_date=from_date, to_date=to_date,
                        limit=limit, start=start, custom_query=custom_query,
                        keywords=keywords, sources=sources, sort_by=sort_by,
                        _retry_count=_retry_count + 1,
                    )
                logger.error("无法刷新 token")
            else:
                logger.error("重试次数过多，放弃")
            return [], {}
        
        if not result.get("ok"):
            logger.error(f"API返回错误状态: {status}")
            return [], {}
        
        # 解析响应
        try:
            data = json.loads(result["body"])
            logger.info(f"API返回数据结构: {list(data.keys()) if isinstance(data, dict) else type(data)}")
            
            # 保存完整响应用于调试
            debug_path = get_data_path("api_response_debug.json")
            debug_path.parent.mkdir(exist_ok=True)
            with open(debug_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info(f"完整响应已保存到: {debug_path}")
            
            if isinstance(data, dict):
                if 'error' in data:
                    logger.error(f"API返回错误: {data['error']}")
                if 'results' in data:
                    logger.info(f"results字段包含 {len(data['results'])} 个元素")
                if 'responses' in data:
                    logger.info(f"responses字段包含 {len(data['responses'])} 个元素")
                if 'response' in data:
                    logger.info(f"response字段类型: {type(data['response'])}")
        except json.JSONDecodeError as e:
            logger.error(f"解析响应失败: {e}")
            return [], {}
        
        items = self._parse_response(data)
        logger.info(f"获取到 {len(items)} 条数据")
        
        # 返回items和响应数据（用于获取nextPagination）
        return items, data
    
    async def msearch_all(
        self,
        search_id: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        limit: int = 100,
        custom_query: Optional[Dict] = None,
        max_items: Optional[int] = None,
        keywords: Optional[str] = None,
        sources: Optional[List[str]] = None,
        sort_by: str = "date",
    ) -> List[Dict[str, Any]]:
        """
        下载所有数据（自动分页）
        
        Args:
            search_id: 搜索ID
            from_date: 开始日期（默认为昨天中午12点）
            to_date: 结束日期（默认为今天中午12点）
            limit: 每页数量（建议100）
            custom_query: 自定义查询（可选）
            max_items: 最大获取数量（可选，None表示获取全部）
        
        Returns:
            List[Dict]: 所有新闻列表
        """
        all_items = []
        start = 0
        total = None
        page = 1
        consecutive_errors = 0  # Fix D：连续失败计数

        # 设置默认日期
        if from_date is None:
            from_date = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=1)
        if to_date is None:
            to_date = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)

        # 注意：时区转换在 msearch() 方法中统一处理，这里不需要转换
        logger.info(f"开始下载数据: {from_date.strftime('%Y-%m-%d %H:%M')} 到 {to_date.strftime('%Y-%m-%d %H:%M')} (北京时间)")

        while True:
            logger.info(f"正在获取第 {page} 页 (start={start}, limit={limit})...")

            # Fix B+C：包裹 msearch 调用。wait_for 防单请求挂死（C），
            # try/except 捕获 evaluate 异常等（B），都归一为 ([], {}) 走下面的重试（D）。
            try:
                items, response_data = await asyncio.wait_for(
                    self.msearch(
                        search_id=search_id,
                        from_date=from_date,
                        to_date=to_date,
                        limit=limit,
                        start=start,
                        custom_query=custom_query,
                        keywords=keywords,
                        sources=sources,
                        sort_by=sort_by,
                    ),
                    timeout=self.MSEARCH_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.error(f"第 {page} 页请求超时（{self.MSEARCH_TIMEOUT}s），视为失败")
                items, response_data = [], {}
            except Exception as e:
                logger.error(f"第 {page} 页 msearch 异常: {e}")
                items, response_data = [], {}

            if not items:
                # Fix D：区分"错误返回空"（response_data 为 {}）和"真正无数据"
                if not response_data:
                    if consecutive_errors < self.MAX_PAGE_RETRIES:
                        consecutive_errors += 1
                        wait = min(self.RETRY_BACKOFF_BASE ** consecutive_errors, self.RETRY_BACKOFF_CAP)
                        logger.warning(
                            f"第 {page} 页获取失败，{wait}s 后重试（{consecutive_errors}/{self.MAX_PAGE_RETRIES}）"
                        )
                        await asyncio.sleep(wait)
                        continue
                    logger.error(f"第 {page} 页连续 {self.MAX_PAGE_RETRIES} 次失败，停止")
                    break
                logger.info(f"第 {page} 页没有数据，停止")
                break

            consecutive_errors = 0  # 成功，重置
            
            all_items.extend(items)
            
            # 获取total信息
            if total is None and 'response' in response_data:
                total = response_data['response'].get('total', 0)
                logger.info(f"总共有 {total} 条数据")
            
            logger.info(f"已获取 {len(all_items)}/{total or '?'} 条数据")
            
            # 检查是否达到最大数量
            if max_items and len(all_items) >= max_items:
                logger.info(f"已达到最大数量 {max_items}，停止")
                all_items = all_items[:max_items]
                break
            
            # 检查是否还有下一页
            if 'response' in response_data:
                next_pagination = response_data['response'].get('nextPagination')
                if not next_pagination:
                    logger.info("没有更多数据了（没有nextPagination）")
                    break
                
                # 检查group.from字段，这表示还有多少数据可以获取
                group_info = next_pagination.get('group', {})
                group_from = group_info.get('from', 0) if isinstance(group_info, dict) else 0
                
                # 更新start参数
                new_start = next_pagination.get('start', start + limit)
                
                # 注意：我们已经在请求中移除了group参数，目的是获取所有数据（不去重）
                # 即使 API响应中包含group信息，也不应该重新添加group参数
                if new_start == start:
                    if group_from > 0:
                        logger.warning(f"API返回了group.from={group_from}，但我们已移除group参数以获取所有数据（不去重）")
                    logger.info("nextPagination.start没有变化，停止")
                    break
                else:
                    start = new_start
                
                # 检查是否已经获取完所有数据
                if total and len(all_items) >= total:
                    logger.info("已获取所有数据")
                    break
            else:
                logger.warning("响应中没有nextPagination信息，停止")
                break
            
            page += 1
            
            # 添加延迟避免请求过快
            await asyncio.sleep(0.5)
        
        logger.info(f"✓ 总共获取了 {len(all_items)} 条数据")
        return all_items
    
    def _update_search_id_in_query(self, query_obj: Any, search_id: str) -> int:
        """
        递归更新查询中的所有searchId
        
        Args:
            query_obj: 查询对象（可能是dict、list或其他类型）
            search_id: 新的搜索ID
            
        Returns:
            int: 更新的searchId数量
        """
        import re
        
        count = 0
        
        if isinstance(query_obj, dict):
            for key, value in query_obj.items():
                # 检查是否是 metaData.applicationTags 字段
                if key == "field" and value == "metaData.applicationTags":
                    # 查找同级的 value 字段
                    if "value" in query_obj:
                        old_value = query_obj["value"]
                        # 更新 searchId
                        new_value = re.sub(r'searchId=\d+', f'searchId={search_id}', old_value)
                        if new_value != old_value:
                            query_obj["value"] = new_value
                            logger.info(f"✓ 已更新 searchId: {old_value} -> {new_value}")
                            count += 1
                
                # 递归处理嵌套结构（继续搜索，不要提前返回）
                count += self._update_search_id_in_query(value, search_id)
        
        elif isinstance(query_obj, list):
            for item in query_obj:
                count += self._update_search_id_in_query(item, search_id)
        
        return count
    
    def _build_search_query(
        self,
        search_id: str,
        from_date: datetime,
        to_date: datetime,
        limit: int,
        start: int,
        sort_by: str = "date",
    ) -> Dict:
        """
        构建搜索查询 - 使用 savedSearch 方式
        
        当指定search_id时，使用savedSearch参数而不是复杂的过滤器
        """
        
        to_str = to_date.strftime("%Y-%m-%dT%H:%M:%S.999Z")
        from_str = from_date.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        
        return {
            "requests": [
                {
                    "id": "query_1",
                    "dataSource": "general-index",
                    "request": {
                        "query": {
                            "and": [
                                {
                                    "savedSearch": search_id
                                },
                                {
                                    "date": {
                                        "from": from_str,
                                        "to": to_str
                                    }
                                }
                            ]
                        },
                        "timezone": "Asia/Shanghai",
                        "pagination": {
                            "sort": {"on": sort_by, "order": "desc"},
                            "limit": limit,
                            "start": start
                        },
                        "documentFormat": {"highlight": True}
                    },
                    "outliers": None,
                    "interval": None,
                    "operation": "search",
                    "language": None,
                    "analysis": None,
                    "metrics": None
                }
            ]
        }
    
    def _parse_response(self, data: Dict) -> List[Dict[str, Any]]:
        """解析API响应"""
        items = []
        
        if not data:
            return items
        
        # 新的API响应格式：单个response对象
        if 'response' in data and isinstance(data['response'], dict):
            hits = data['response'].get('hits', [])
            logger.info(f"从response.hits中找到 {len(hits)} 条记录")
            for hit in hits:
                gyda = hit.get('gyda', {})
                if not gyda:
                    continue
                    
                title = gyda.get('title') or ''
                ingress = gyda.get('ingress') or ''
                body = gyda.get('body') or ''
                
                # 提取作者信息
                main_author = gyda.get('mainAuthor', {})
                author_name = ''
                author_handle = ''
                author_url = ''
                if isinstance(main_author, dict):
                    author_handle = main_author.get('handle', '')
                    # 从authors数组中获取更多信息
                    authors = gyda.get('authors', [])
                    if authors and len(authors) > 0:
                        author_info = authors[0]
                        author_name = author_info.get('name', '')
                        author_url = author_info.get('url', '')
                
                # 提取社交媒体指标
                social_scores = gyda.get('socialScores', {})
                metrics = gyda.get('metrics', {})
                
                # 提取命名实体
                named_entities = gyda.get('namedEntities', [])
                entities_str = ', '.join([f"{e.get('name', '')}({e.get('type', '')})" for e in named_entities[:10]]) if named_entities else ''
                
                # 提取关键词和关键短语
                keywords = gyda.get('keywords', [])
                key_phrases = gyda.get('keyPhrases', [])
                
                item = {
                    # 基本信息
                    "id": hit.get('id', gyda.get('externalId', '')),
                    "document_id": gyda.get('documentId', ''),
                    "external_id": gyda.get('externalId', ''),
                    
                    # 内容
                    "title": title[:500] if title else '',
                    "ingress": ingress[:500] if ingress else '',
                    "content": body[:2000] if body else '',  # 增加到2000字符
                    "full_content": body,  # 完整内容
                    
                    # URL和来源
                    "url": gyda.get('url', gyda.get('sourceUrl', '')),
                    "original_url": gyda.get('originalUrl', ''),
                    "source_url": gyda.get('sourceUrl', ''),
                    
                    # 时间
                    "published_at": gyda.get('date', ''),
                    "fetching_time": gyda.get('fetchingTime', ''),
                    
                    # 来源信息
                    "source": gyda.get('source', {}).get('name', '') if isinstance(gyda.get('source'), dict) else gyda.get('provider', ''),
                    "provider": gyda.get('provider', ''),
                    "source_id": gyda.get('sourceId', ''),
                    "media_type": gyda.get('mediaType', ''),
                    "information_type": gyda.get('informationType', ''),
                    
                    # 作者信息
                    "author": author_name or author_handle,
                    "author_handle": author_handle,
                    "author_name": author_name,
                    "author_url": author_url,
                    "author_verified": main_author.get('verifiedAccount', None) if isinstance(main_author, dict) else None,
                    "author_authority": main_author.get('authority', None) if isinstance(main_author, dict) else None,
                    
                    # 语言和地理
                    "language": gyda.get('language', ''),
                    "country": gyda.get('country', ''),
                    "region": gyda.get('region', ''),
                    "place": gyda.get('place', ''),
                    
                    # 情感分析
                    "sentiment": gyda.get('sentiment', ''),
                    "original_sentiment": gyda.get('originalSentiment', ''),
                    
                    # 影响力指标
                    "reach": gyda.get('reach', 0),
                    "potential_reach": gyda.get('potentialReach', 0),
                    "local_reach": gyda.get('localReach', 0),
                    "global_reach": gyda.get('globalReach', 0),
                    "ave": gyda.get('ave', 0),  # Advertising Value Equivalency
                    "emv": gyda.get('emv', 0),  # Earned Media Value
                    
                    # 社交媒体指标
                    "tw_followers": social_scores.get('tw_followers', 0) if social_scores else 0,
                    "tw_following": social_scores.get('tw_following', 0) if social_scores else 0,
                    "tw_retweets": social_scores.get('tw_retweets', 0) if social_scores else 0,
                    "tw_likes": social_scores.get('tw_likes', 0) if social_scores else 0,
                    "tw_replies": social_scores.get('tw_replies', 0) if social_scores else 0,
                    "fb_likes": social_scores.get('fb_likes', 0) if social_scores else 0,
                    "fb_shares": social_scores.get('fb_shares', 0) if social_scores else 0,
                    "fb_post_reactions": social_scores.get('fb_post_reactions', 0) if social_scores else 0,
                    
                    # 内容分析
                    "keywords": ', '.join(keywords) if keywords else '',
                    "key_phrases": ', '.join(key_phrases[:20]) if key_phrases else '',  # 前20个关键短语
                    "named_entities": entities_str,
                    
                    # 匹配信息
                    "match_sentence": gyda.get('matchSentence', ''),
                    "discussion_type": gyda.get('discussionType', ''),
                    
                    # 其他元数据
                    "is_hosted": gyda.get('isHosted', False),
                    "is_nsfw": gyda.get('isNsfw', False),
                    "restriction": gyda.get('restriction', ''),
                    "embed_url": gyda.get('embedUrl', ''),
                    "images": len(gyda.get('images') or []),  # 图片数量
                    "links": len(gyda.get('links') or []),  # 链接数量
                }
                items.append(item)
            return items
        
        # 旧的API响应格式：results或responses数组
        results = data.get("results", []) or data.get("responses", [])
        
        for resp in results:
            docs = resp.get("documents", []) if isinstance(resp, dict) else []
            for doc in docs:
                item = {
                    "id": doc.get("id", ""),
                    "title": doc.get("title", ""),
                    "content": doc.get("ingress", "") or doc.get("content", "")[:500],
                    "url": doc.get("url", ""),
                    "published_at": doc.get("publishedAt", ""),
                    "source": doc.get("source", {}).get("name", "") if isinstance(doc.get("source"), dict) else "",
                    "language": doc.get("language", ""),
                    "author": doc.get("author", ""),
                    "sentiment": doc.get("sentiment", ""),
                }
                items.append(item)
        
        return items
