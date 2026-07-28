"""数据持久化：支持 JSON、CSV 和 Excel 输出"""
import csv
import json
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.paths import get_project_root


def _resolve_dir(dirname: str) -> str:
    """将相对目录解析为项目根目录下的绝对路径"""
    if os.path.isabs(dirname):
        return dirname
    return str(get_project_root() / dirname)


def save(items: List[Dict[str, Any]], config: dict) -> str:
    """
    将抓取结果保存到文件。
    返回输出文件路径。
    """
    out_cfg = config["output"]
    out_dir = _resolve_dir(out_cfg["dir"])
    os.makedirs(out_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fmt = out_cfg["format"].lower()
    filename = f"{out_cfg['filename']}_{timestamp}.{fmt}"
    filepath = os.path.join(out_dir, filename)

    if fmt == "json":
        _save_json(items, filepath)
    elif fmt == "csv":
        _save_csv(items, filepath)
    elif fmt == "excel" or fmt == "xlsx":
        xlsx_path = filepath.replace(f".{fmt}", ".xlsx")
        _save_excel(items, xlsx_path)
        filepath = xlsx_path  # 修正为实际扩展名

        # 自动执行格式转换
        try:
            from transfer.transform import transform_file
            transform_name = out_cfg.get("transform_name", "")
            transform_file(xlsx_path, input_name=transform_name)
        except Exception as e:
            logger.warning(f"Auto-transform skipped: {e}")
    else:
        raise ValueError(f"Unsupported format: {fmt}")

    logger.info(f"Saved {len(items)} items -> {filepath}")
    return filepath


def save_excel(items: List[Dict[str, Any]], output_dir: str = "data", filename: str = None,
               auto_transform: bool = True, transform_name: str = "") -> str:
    """
    将数据保存为Excel文件

    Args:
        items: 数据列表
        output_dir: 输出目录
        filename: 文件名（可选，默认自动生成）
        auto_transform: 是否自动执行格式转换
        transform_name: 转换时写入 Input Name 列的值

    Returns:
        str: 输出文件路径
    """
    output_dir = _resolve_dir(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"meltwater_feed_{timestamp}.xlsx"

    filepath = os.path.join(output_dir, filename)
    _save_excel(items, filepath)

    logger.info(f"Saved {len(items)} items -> {filepath}")

    # 自动执行格式转换
    if auto_transform:
        try:
            from transfer.transform import transform_file
            transform_file(filepath, input_name=transform_name)
        except Exception as e:
            logger.warning(f"Auto-transform skipped: {e}")

    return filepath


def _save_json(items: List[Dict[str, Any]], path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _save_csv(items: List[Dict[str, Any]], path: str):
    if not items:
        return
    fieldnames = list(items[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(items)


def _save_excel(items: List[Dict[str, Any]], path: str):
    """保存为Excel文件"""
    if not items:
        return
    
    try:
        import pandas as pd
    except ImportError:
        logger.error("需要安装pandas库: pip install pandas openpyxl")
        raise
    
    # 转换为DataFrame
    df = pd.DataFrame(items)
    
    # 调整列顺序，把重要的列放在前面
    preferred_columns = [
        # 基本标识
        'id', 'document_id', 'external_id',
        # 内容
        'title', 'content', 'ingress', 
        # URL
        'url', 'original_url', 'source_url',
        # 时间
        'published_at', 'fetching_time',
        # 来源
        'source', 'provider', 'media_type', 'information_type',
        # 作者
        'author', 'author_name', 'author_handle', 'author_url', 'author_verified', 'author_authority',
        # 语言和地理
        'language', 'country', 'region',
        # 情感
        'sentiment', 'original_sentiment',
        # 影响力
        'reach', 'potential_reach', 'ave', 'emv',
        # 社交媒体指标
        'tw_followers', 'tw_following', 'tw_retweets', 'tw_likes', 'tw_replies',
        'fb_likes', 'fb_shares', 'fb_post_reactions',
        # 内容分析
        'keywords', 'key_phrases', 'named_entities', 'match_sentence',
        # 其他
        'discussion_type', 'is_hosted', 'is_nsfw', 'restriction', 'images', 'links'
    ]
    existing_columns = [col for col in preferred_columns if col in df.columns]
    other_columns = [col for col in df.columns if col not in preferred_columns]
    df = df[existing_columns + other_columns]
    
    # 保存为Excel
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Data')
        
        # 自动调整列宽
        worksheet = writer.sheets['Data']
        from openpyxl.utils import get_column_letter
        
        for idx, col in enumerate(df.columns, 1):
            try:
                col_str = df[col].fillna("").astype(str)
                max_length = max(
                    col_str.apply(len).max(),
                    len(str(col)),
                )
            except Exception:
                max_length = len(str(col))
            # 限制最大宽度为100
            adjusted_width = min(max_length + 2, 100)
            column_letter = get_column_letter(idx)
            worksheet.column_dimensions[column_letter].width = adjusted_width
