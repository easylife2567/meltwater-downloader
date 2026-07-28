"""路径工具函数 - 提供项目根目录和数据目录的路径"""
import os
from pathlib import Path

_project_root = None


def get_project_root() -> Path:
    """获取项目根目录（基于本文件位置查找，缓存结果）"""
    global _project_root
    if _project_root is not None:
        return _project_root
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "config.yaml").exists():
            _project_root = current
            return _project_root
        current = current.parent
    _project_root = Path(__file__).resolve().parent
    return _project_root


def get_data_dir() -> Path:
    """获取数据目录"""
    data_dir = get_project_root() / "data"
    data_dir.mkdir(exist_ok=True)
    return data_dir


def get_data_path(filename: str) -> Path:
    """获取数据文件的完整路径"""
    return get_data_dir() / filename


def get_config_path(filename: str = "config.yaml") -> Path:
    """获取配置文件的完整路径（相对于项目根目录）"""
    return get_project_root() / filename
