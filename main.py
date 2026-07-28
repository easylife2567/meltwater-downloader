"""配置加载工具"""
import os
import re
import yaml
from pathlib import Path
from loguru import logger


def load_config(path: str = None) -> dict:
    """加载配置文件，支持环境变量替换"""
    if path is None:
        from utils.paths import get_config_path
        path = str(get_config_path("config.yaml"))
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    def replace_env_vars(match):
        var = match.group(1)
        value = os.environ.get(var, "")
        if not value:
            logger.warning(f"Environment variable {var} not set")
        return value

    content = re.sub(r'\$\{(\w+)\}', replace_env_vars, content)
    return yaml.safe_load(content)
