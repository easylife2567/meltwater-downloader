#!/usr/bin/env python3
"""
仅下载数据脚本 - 不进行AI分析

使用方法：
    python download_only.py
    python download_only.py --search-id 24944745
    python download_only.py --from "2026-04-18" --to "2026-04-19"
    python download_only.py --format json  # 或 excel, both
    python download_only.py --search-id 24908211 --auto-intercept  # 自动拦截新的search_id
"""

import asyncio
import sys
import argparse
import json
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv
from loguru import logger

# 添加项目根目录到Python路径（必须在其他本地导入之前）
_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.paths import get_project_root

# 从项目根目录加载 .env
load_dotenv(get_project_root() / ".env")

from scraper.browser import BrowserSession
from scraper.api_client import MeltwaterAPIClient
from storage.writer import save, save_excel
from main import load_config
from utils.helpers import parse_date, split_date_range_by_day
from utils.paths import get_data_path


async def intercept_search_query(session: BrowserSession, search_id: str) -> bool:
    """
    拦截指定search_id的查询配置
    
    Args:
        session: 浏览器会话
        search_id: 搜索ID
        
    Returns:
        bool: 是否成功拦截
    """
    logger.info(f"正在拦截 search_id={search_id} 的查询配置...")
    
    msearch_requests = []
    
    async def handle_request(request):
        if 'msearch' in request.url and request.method == "POST":
            logger.info(f"✓ 拦截到 msearch 请求")
            if request.post_data:
                try:
                    data = json.loads(request.post_data)
                    msearch_requests.append(data)
                except:
                    pass
    
    session.page.on("request", handle_request)
    
    # 访问explore页面触发API请求
    explore_url = f"https://app.meltwater.com/a/explore/results?searchId={search_id}"
    logger.info(f"访问页面: {explore_url}")
    
    try:
        await session.page.goto(explore_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        logger.warning(f"访问页面失败: {e}")

    # 等待足够时间让 SPA 完成渲染并发起 msearch 请求
    await asyncio.sleep(300)

    # 移除监听器
    session.page.remove_listener("request", handle_request)
    
    if msearch_requests:
        # 保存到专用文件
        output_file = get_data_path(f"intercepted_msearch_{search_id}.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(msearch_requests[0], f, indent=2, ensure_ascii=False)
        logger.info(f"✓ 查询配置已保存: {output_file}")
        return True
    else:
        logger.warning(f"⚠️  未能拦截到 search_id={search_id} 的查询配置")
        return False


async def download_data(
    search_id: str = None,
    from_date: datetime = None,
    to_date: datetime = None,
    output_format: str = "both",
    auto_intercept: bool = False,
    keywords: str = None,
    max_items: int = 3000,
    sources: str = None,
    sort_by: str = "date",
):
    """
    仅下载数据，不进行AI分析

    max_items: 总采集上限，按天均分配额（0=不限）

    Args:
        search_id: 搜索ID（可选，默认从config.yaml读取）
        from_date: 开始日期（可选，默认为今日00:00）
        to_date: 结束日期（可选，默认为今日23:59）
        output_format: 输出格式 (json, excel, both)
        auto_intercept: 是否自动拦截查询配置
    """
    config = load_config()
    
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {message}")
    
    # 如果没有指定search_id，从配置文件读取
    if search_id is None:
        search_id = config.get("scraper", {}).get("search_id", "24944745")
    
    # 设置默认日期范围：今日 00:00 到 23:59（北京时间）
    if from_date is None:
        from_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if to_date is None:
        to_date = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0)
    
    logger.info(f"下载参数:")
    logger.info(f"  Search ID: {search_id}")
    logger.info(f"  时间范围: {from_date.strftime('%Y-%m-%d %H:%M')} 到 {to_date.strftime('%Y-%m-%d %H:%M')} (北京时间)")
    logger.info(f"  输出格式: {output_format}")
    logger.info(f"  自动拦截: {'是' if auto_intercept else '否'}")
    logger.info(f"  关键词: {keywords if keywords else '（使用搜索预设）'}")
    logger.info(f"  总量上限: {max_items if max_items > 0 else '不限'}")
    logger.info(f"  平台来源: {sources if sources else '全部'}")
    logger.info(f"  排序方式: {sort_by}")
    logger.info("")

    # 解析平台来源列表
    source_list = [s.strip() for s in sources.split(",")] if sources else None
    
    # 启动浏览器会话
    async with BrowserSession(config) as session:
        # 检查是否需要拦截查询配置
        query_file = get_data_path(f"intercepted_msearch_{search_id}.json")
        need_intercept = True  # 每次都重新拦截，token更稳定
        
        # 只有在需要拦截查询配置时才先登录
        if need_intercept:
            logger.info("正在登录 Meltwater（需要拦截查询配置）...")
            ok = await session.login()
            if not ok:
                logger.error("❌ 登录失败")
                sys.exit(1)
            
            logger.info("✓ 登录成功")
            logger.info("")
            
            if not query_file.exists():
                logger.info(f"⚠️  未找到 search_id={search_id} 的查询配置文件")
                logger.info(f"   将自动拦截查询配置...")
            
            intercept_ok = await intercept_search_query(session, search_id)
            if not intercept_ok:
                logger.error("❌ 拦截查询配置失败")
                logger.error("   请检查 search_id 是否正确，或手动运行: python test_intercept_api.py")
                sys.exit(1)
            logger.info("")
        else:
            logger.info(f"✓ 找到查询配置文件: intercepted_msearch_{search_id}.json")
            logger.info("")
        
        # 创建API客户端
        api_client = MeltwaterAPIClient(session.page, browser_session=session)
        
        # 确保有有效的token（会自动检查缓存，只在需要时才登录）
        token_ok = await api_client.ensure_token()
        if not token_ok:
            logger.error("❌ 获取token失败")
            sys.exit(1)
        
        logger.info("")
        
        # 加载拦截的查询配置
        query_file = get_data_path(f"intercepted_msearch_{search_id}.json")
        custom_query = None
        
        if query_file.exists():
            logger.info(f"正在加载查询配置: intercepted_msearch_{search_id}.json")
            try:
                with open(query_file, "r", encoding="utf-8") as f:
                    real_query = json.load(f)
                
                # 使用第二个查询（带pagination的那个）
                if len(real_query.get("requests", [])) > 1:
                    custom_query = {"requests": [real_query["requests"][1]]}
                    logger.info("✓ 查询配置加载成功（使用第2个request）")
                else:
                    custom_query = real_query
                    logger.info("✓ 查询配置加载成功")
            except Exception as e:
                logger.warning(f"⚠️  加载查询配置失败: {e}")
                logger.warning("   将使用 savedSearch 方式")
        else:
            logger.warning(f"⚠️  查询配置文件不存在，将使用 savedSearch 方式")
        
        logger.info("")
        
        # 检查时间范围是否大于1天
        time_diff = to_date - from_date
        days = time_diff.days

        all_items = []

        if days >= 1:
            # 按天分割下载
            date_ranges = split_date_range_by_day(from_date, to_date)
            logger.info(f"时间范围跨度 {days + 1} 天，将按天分批下载（共 {len(date_ranges)} 批）")

            # 按天均分总配额
            if max_items and max_items > 0:
                per_day = max_items // len(date_ranges)
                remainder = max_items % len(date_ranges)
                logger.info(f"总配额 {max_items} 条，每天 {per_day} 条（前 {remainder} 天各多 1 条）")
            else:
                per_day = None
                remainder = 0
            logger.info("")

            for idx, (day_start, day_end) in enumerate(date_ranges, 1):
                # 计算当天配额（余数优先分给前几天）
                if max_items and max_items > 0:
                    day_quota = per_day + (1 if idx <= remainder else 0)
                    logger.info(f"第 {idx}/{len(date_ranges)} 批配额: {day_quota} 条")
                else:
                    day_quota = None

                logger.info("=" * 70)
                logger.info(f"第 {idx}/{len(date_ranges)} 批: {day_start.strftime('%Y-%m-%d %H:%M')} 到 {day_end.strftime('%Y-%m-%d %H:%M')} (北京时间)")
                logger.info("=" * 70)

                day_items = await api_client.msearch_all(
                    search_id=search_id,
                    from_date=day_start,
                    to_date=day_end,
                    limit=100,
                    custom_query=custom_query,
                    keywords=keywords,
                    max_items=day_quota,
                    sources=source_list,
                    sort_by=sort_by,
                )

                if day_items:
                    logger.info(f"✓ 第 {idx} 批获取 {len(day_items)} 条数据")
                    all_items.extend(day_items)
                else:
                    logger.warning(f"⚠️  第 {idx} 批未获取到数据")

                logger.info(f"累计已获取 {len(all_items)} 条数据")
                logger.info("")

                # 批次间添加短暂延迟
                if idx < len(date_ranges):
                    await asyncio.sleep(1)

            logger.info("=" * 70)
            logger.info(f"✓ 所有批次下载完成，共获取 {len(all_items)} 条数据")
            logger.info("=" * 70)
            logger.info("")
        else:
            # 时间范围小于1天，直接下载
            logger.info(f"开始下载数据: {from_date.strftime('%Y-%m-%d %H:%M')} 到 {to_date.strftime('%Y-%m-%d %H:%M')} (北京时间)")
            if max_items > 0:
                logger.info(f"总配额: {max_items} 条")

            all_items = await api_client.msearch_all(
                search_id=search_id,
                from_date=from_date,
                to_date=to_date,
                limit=100,
                custom_query=custom_query,
                keywords=keywords,
                max_items=max_items,
                sources=source_list,
                sort_by=sort_by,
            )

            if all_items:
                logger.info(f"✓ 成功获取 {len(all_items)} 条数据")
            logger.info("")
        
        if not all_items:
            logger.warning("⚠️  未获取到任何数据")
            return
        
        # 保存数据
        output_files = []
        
        if output_format in ["json", "both"]:
            logger.info("正在保存 JSON 文件...")
            json_path = save(all_items, config)
            output_files.append(json_path)
            logger.info(f"✓ JSON 文件已保存: {json_path}")
        
        if output_format in ["excel", "both"]:
            logger.info("正在保存 Excel 文件...")
            excel_path = save_excel(all_items, output_dir=config["output"]["dir"])
            output_files.append(excel_path)
            logger.info(f"✓ Excel 文件已保存: {excel_path}")
        
        logger.info("")
        logger.info("=" * 70)
        logger.info("✅ 数据下载完成！")
        logger.info("=" * 70)
        logger.info(f"共下载 {len(all_items)} 条数据")
        for path in output_files:
            logger.info(f"  - {path}")


def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(
        description="舆情数据下载工具（仅下载，不分析）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 使用默认参数（今日00:00到23:59）
  python download_only.py
  
  # 指定search_id
  python download_only.py --search-id 24944745
  
  # 指定时间范围
  python download_only.py --from "2026-04-25" --to "2026-04-26"
  
  # 指定输出格式
  python download_only.py --format excel
  
  # 自动拦截新的search_id（如果查询文件不存在）
  python download_only.py --search-id 24908211
  
  # 强制重新拦截查询配置
  python download_only.py --search-id 24944745 --auto-intercept
  
  # 完整参数
  python download_only.py --search-id 24944745 --from "2026-04-25 00:00" --to "2026-04-26 23:59" --format both
        """
    )
    
    parser.add_argument(
        "--search-id",
        type=str,
        help="搜索ID（默认从config.yaml读取）"
    )
    
    parser.add_argument(
        "--from",
        dest="from_date",
        type=str,
        help="开始日期，格式: YYYY-MM-DD 或 YYYY-MM-DD HH:MM（默认：今日00:00）"
    )
    
    parser.add_argument(
        "--to",
        dest="to_date",
        type=str,
        help="结束日期，格式: YYYY-MM-DD 或 YYYY-MM-DD HH:MM（默认：今日23:59）"
    )
    
    parser.add_argument(
        "--format",
        type=str,
        choices=["json", "excel", "both"],
        default="both",
        help="输出格式：json, excel, 或 both（默认：both）"
    )
    
    parser.add_argument(
        "--auto-intercept",
        action="store_true",
        help="自动拦截查询配置（如果查询文件不存在，会自动拦截）"
    )

    parser.add_argument(
        "--keywords",
        type=str,
        help="搜索关键词，逗号分隔，完全替换搜索中的原有关键词。示例: --keywords 'AI,人工智能,machine learning'"
    )

    parser.add_argument(
        "--max-items",
        type=int,
        default=3000,
        help="最大采集条数，按天均分配额（默认：3000）。设置为0表示不限"
    )

    parser.add_argument(
        "--sources",
        type=str,
        default=None,
        help="指定平台来源，逗号分隔。如: --sources youtube,twitter,facebook,instagram,tiktok,reddit"
    )

    parser.add_argument(
        "--sort",
        type=str,
        default="date",
        choices=["reach", "date", "relevance", "engagement", "social_echo", "views", "sentiment"],
        help="排序方式（默认：reach）。可选: reach, date, relevance, engagement, social_echo, views, sentiment"
    )

    args = parser.parse_args()
    
    # 打印欢迎信息
    print("=" * 70)
    print(" " * 20 + "舆情数据下载工具")
    print(" " * 22 + "(仅下载，不分析)")
    print("=" * 70)
    print()
    
    # 解析日期参数
    from_date = None
    to_date = None
    
    try:
        if args.from_date:
            from_date = parse_date(args.from_date)
        if args.to_date:
            to_date = parse_date(args.to_date)
    except ValueError as e:
        print()
        print(f"❌ 日期格式错误: {e}")
        return 1
    
    try:
        asyncio.run(download_data(
            search_id=args.search_id,
            from_date=from_date,
            to_date=to_date,
            output_format=args.format,
            auto_intercept=args.auto_intercept,
            keywords=args.keywords,
            max_items=args.max_items,
            sources=args.sources,
            sort_by=args.sort,
        ))
        return 0
    except KeyboardInterrupt:
        print()
        print("⚠️  用户中断执行")
        return 130
    except Exception as e:
        print()
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
