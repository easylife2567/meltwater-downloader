"""通用辅助函数"""
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple
from datetime import datetime, timedelta

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent))
from utils.paths import get_data_path, get_project_root


def load_intercepted_query() -> Dict:
    """加载拦截到的查询"""
    query_file = get_data_path("intercepted_msearch_request.json")
    with open(query_file, "r", encoding="utf-8") as f:
        real_query = json.load(f)
    
    # 使用第二个查询（带pagination的那个）
    if len(real_query["requests"]) > 1:
        return {"requests": [real_query["requests"][1]]}
    else:
        return real_query


def parse_date(date_str: str) -> datetime:
    """解析日期字符串，支持多种格式"""
    formats = [
        "%Y-%m-%d",           # 2026-04-19
        "%Y-%m-%d %H:%M",     # 2026-04-19 12:00
        "%Y-%m-%d %H:%M:%S",  # 2026-04-19 12:00:00
        "%Y/%m/%d",           # 2026/04/19
        "%Y/%m/%d %H:%M",     # 2026/04/19 12:00
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    
    raise ValueError(f"无法解析日期: {date_str}，支持的格式: YYYY-MM-DD, YYYY-MM-DD HH:MM, YYYY/MM/DD 等")


def split_date_range_by_day(from_date: datetime, to_date: datetime) -> List[Tuple[datetime, datetime]]:
    """
    将日期范围按天分割
    
    Args:
        from_date: 开始日期
        to_date: 结束日期
        
    Returns:
        List[Tuple[datetime, datetime]]: 日期范围列表，每个元素是(开始时间, 结束时间)
    """
    date_ranges = []
    
    # 计算时间跨度（天数）
    time_diff = to_date - from_date
    days = time_diff.days
    
    # 如果时间跨度小于等于1天，直接返回原始范围
    if days < 1:
        return [(from_date, to_date)]
    
    # 按天分割
    current_date = from_date
    while current_date < to_date:
        # 计算当天的结束时间（23:59:59）
        day_end = current_date.replace(hour=23, minute=59, second=59, microsecond=0)
        
        # 如果day_end超过了to_date，使用to_date
        if day_end > to_date:
            day_end = to_date
    
        date_ranges.append((current_date, day_end))
        
        # 移动到下一天的00:00:00
        current_date = (current_date + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    
    return date_ranges
