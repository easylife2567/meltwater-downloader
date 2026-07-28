#!/usr/bin/env python3
"""
FastAPI 接口封装 - Meltwater 数据下载服务
使用方法：
    uvicorn api:app --reload --host 0.0.0.0 --port 8000
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List
from enum import Enum

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

# 导入现有模块
from scraper.browser import BrowserSession
from scraper.api_client import MeltwaterAPIClient
from storage.writer import save, save_excel
from main import load_config
from utils.helpers import parse_date, split_date_range_by_day
from utils.paths import get_data_path

# 创建 FastAPI 应用
app = FastAPI(
    title="Meltwater Downloader API",
    description="Meltwater 媒体监测数据下载服务",
    version="1.0.0"
)

# 任务状态枚举
class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

# 输出格式枚举
class OutputFormat(str, Enum):
    JSON = "json"
    EXCEL = "excel"
    BOTH = "both"

# 请求模型
class DownloadRequest(BaseModel):
    search_id: Optional[str] = Field(None, description="搜索ID，不提供则使用配置文件默认值")
    from_date: Optional[str] = Field(None, description="开始日期，格式: YYYY-MM-DD 或 YYYY-MM-DD HH:MM，默认为今日00:00")
    to_date: Optional[str] = Field(None, description="结束日期，格式: YYYY-MM-DD 或 YYYY-MM-DD HH:MM，默认为今日23:59")
    output_format: OutputFormat = Field(OutputFormat.BOTH, description="输出格式: json, excel, both")
    auto_intercept: bool = Field(False, description="是否自动拦截查询配置")
    keywords: Optional[str] = Field(None, description="搜索关键词，逗号分隔，完全替换搜索预设关键词")
    max_items: Optional[int] = Field(3000, description="最大采集条数，按天均分配额，0表示不限")

    class Config:
        json_schema_extra = {
            "example": {
                "search_id": "24946297",
                "from_date": "2026-05-18 00:00",
                "to_date": "2026-05-18 23:59",
                "output_format": "both",
                "auto_intercept": False,
                "keywords": "AI,人工智能,machine learning"
            }
        }

# 响应模型
class TaskResponse(BaseModel):
    task_id: str = Field(..., description="任务ID")
    status: TaskStatus = Field(..., description="任务状态")
    message: str = Field(..., description="状态消息")

class TaskStatusResponse(BaseModel):
    task_id: str
    status: TaskStatus
    message: str
    created_at: str
    completed_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

class DownloadResult(BaseModel):
    total_items: int = Field(..., description="下载的数据条数")
    output_files: List[str] = Field(..., description="生成的文件路径列表")
    time_range: Dict[str, str] = Field(..., description="时间范围")

# 全局任务存储（生产环境应使用 Redis 或数据库）
tasks: Dict[str, Dict[str, Any]] = {}

# 后台下载任务
async def background_download_task(
    task_id: str,
    search_id: Optional[str],
    from_date: Optional[datetime],
    to_date: Optional[datetime],
    output_format: str,
    auto_intercept: bool,
    keywords: Optional[str] = None,
    max_items: Optional[int] = 3000,
):
    """后台执行下载任务（max_items=0 表示不限）"""
    try:
        tasks[task_id]["status"] = TaskStatus.RUNNING
        tasks[task_id]["message"] = "正在下载数据..."

        if keywords:
            logger.info(f"[Task {task_id}] 使用关键词表达式: {keywords}")

        logger.info(f"[Task {task_id}] 开始下载任务")

        # 加载配置
        config = load_config()

        # 使用默认值
        if not search_id:
            search_id = config.get("scraper", {}).get("search_id", "24944745")

        if not from_date or not to_date:
            # 默认今日00:00到23:59（北京时间）
            now = datetime.now()
            from_date = from_date or now.replace(hour=0, minute=0, second=0, microsecond=0)
            to_date = to_date or now.replace(hour=23, minute=59, second=59, microsecond=0)

        # 创建浏览器会话
        session = BrowserSession(config)
        await session.start()

        try:
            # 创建API客户端
            api_client = MeltwaterAPIClient(session.page, browser_session=session)

            # 检查是否需要拦截查询配置
            intercepted_file = get_data_path(f"intercepted_msearch_{search_id}.json")
            custom_query = None

            if auto_intercept or not intercepted_file.exists():
                logger.info(f"[Task {task_id}] 需要拦截查询配置...")
                success = await intercept_search_query(session, search_id)
                if not success:
                    raise Exception("拦截查询配置失败")

            # 读取拦截的查询配置
            if intercepted_file.exists():
                import json
                with open(intercepted_file, "r", encoding="utf-8") as f:
                    custom_query = json.load(f)
                logger.info(f"[Task {task_id}] 使用拦截的查询配置")

            # 确保token可用
            await api_client.ensure_token()

            # 检查时间范围是否大于1天
            time_diff = to_date - from_date
            days = time_diff.days

            all_items = []

            if days >= 1:
                # 按天分割下载
                date_ranges = split_date_range_by_day(from_date, to_date)
                logger.info(f"[Task {task_id}] 时间范围跨度 {days + 1} 天，将按天分批下载（共 {len(date_ranges)} 批）")

                # 按天均分总配额
                if max_items and max_items > 0:
                    per_day = max_items // len(date_ranges)
                    remainder = max_items % len(date_ranges)
                else:
                    per_day = None
                    remainder = 0

                for idx, (day_start, day_end) in enumerate(date_ranges, 1):
                    day_quota = per_day + (1 if max_items and max_items > 0 and idx <= remainder else 0) if (max_items and max_items > 0) else None
                    logger.info(f"[Task {task_id}] 第 {idx}/{len(date_ranges)} 批: {day_start.strftime('%Y-%m-%d %H:%M')} 到 {day_end.strftime('%Y-%m-%d %H:%M')}")

                    day_items = await api_client.msearch_all(
                        search_id=search_id,
                        from_date=day_start,
                        to_date=day_end,
                        limit=100,
                        custom_query=custom_query,
                        keywords=keywords,
                        max_items=day_quota,
                    )

                    if day_items:
                        logger.info(f"[Task {task_id}] 第 {idx} 批获取 {len(day_items)} 条数据")
                        all_items.extend(day_items)

                    if idx < len(date_ranges):
                        await asyncio.sleep(1)

                logger.info(f"[Task {task_id}] 所有批次下载完成，共获取 {len(all_items)} 条数据")
            else:
                # 时间范围小于1天，直接下载
                logger.info(f"[Task {task_id}] 开始下载数据: {from_date.strftime('%Y-%m-%d %H:%M')} 到 {to_date.strftime('%Y-%m-%d %H:%M')}")

                all_items = await api_client.msearch_all(
                    search_id=search_id,
                    from_date=from_date,
                    to_date=to_date,
                    limit=100,
                    custom_query=custom_query,
                    keywords=keywords,
                    max_items=max_items,
                )

            if all_items:
                logger.info(f"[Task {task_id}] 成功获取 {len(all_items)} 条数据")

            if not all_items:
                raise Exception("未获取到任何数据")

            # 保存数据
            output_files = []

            if output_format in ["json", "both"]:
                json_file = save(all_items, config)
                output_files.append(str(json_file))
                logger.info(f"[Task {task_id}] JSON文件已保存: {json_file}")

            if output_format in ["excel", "both"]:
                try:
                    excel_file = save_excel(all_items, output_dir=config["output"]["dir"])
                    output_files.append(str(excel_file))
                    logger.info(f"[Task {task_id}] Excel文件已保存: {excel_file}")
                except ImportError:
                    logger.warning(f"[Task {task_id}] pandas未安装，跳过Excel导出")

            # 更新任务状态
            tasks[task_id]["status"] = TaskStatus.COMPLETED
            tasks[task_id]["message"] = "下载完成"
            tasks[task_id]["completed_at"] = datetime.now().isoformat()
            tasks[task_id]["result"] = {
                "total_items": len(all_items),
                "output_files": output_files,
                "time_range": {
                    "from": from_date.strftime("%Y-%m-%d %H:%M:%S"),
                    "to": to_date.strftime("%Y-%m-%d %H:%M:%S")
                }
            }

            logger.info(f"[Task {task_id}] 任务完成")

        finally:
            await session.close()

    except Exception as e:
        logger.error(f"[Task {task_id}] 任务失败: {e}")
        tasks[task_id]["status"] = TaskStatus.FAILED
        tasks[task_id]["message"] = "下载失败"
        tasks[task_id]["completed_at"] = datetime.now().isoformat()
        tasks[task_id]["error"] = str(e)

async def intercept_search_query(session: BrowserSession, search_id: str) -> bool:
    """拦截指定search_id的查询配置"""
    import json

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

    explore_url = f"https://app.meltwater.com/a/explore/results?searchId={search_id}"
    logger.info(f"访问页面: {explore_url}")

    try:
        await session.page.goto(explore_url, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(5)
    except Exception as e:
        logger.warning(f"访问页面超时，但可能已拦截到请求: {e}")

    session.page.remove_listener("request", handle_request)

    if msearch_requests:
        output_file = get_data_path(f"intercepted_msearch_{search_id}.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(msearch_requests[0], f, indent=2, ensure_ascii=False)
        logger.info(f"✓ 查询配置已保存: {output_file}")
        return True
    else:
        logger.warning(f"⚠️  未能拦截到 search_id={search_id} 的查询配置")
        return False

# API 端点
@app.get("/", tags=["Root"])
async def root():
    """API 根路径"""
    return {
        "name": "Meltwater Downloader API",
        "version": "1.0.0",
        "docs": "/docs",
        "status": "running"
    }

@app.post("/download", response_model=TaskResponse, tags=["Download"])
async def create_download_task(
    request: DownloadRequest,
    background_tasks: BackgroundTasks
):
    """
    创建下载任务

    - **search_id**: 搜索ID（可选，默认使用配置文件）
    - **from_date**: 开始日期（可选，默认今日00:00）
    - **to_date**: 结束日期（可选，默认今日23:59）
    - **output_format**: 输出格式 (json/excel/both)
    - **auto_intercept**: 是否自动拦截查询配置
    """
    # 生成任务ID
    task_id = str(uuid.uuid4())

    # 解析日期
    from_date = None
    to_date = None

    try:
        if request.from_date:
            from_date = parse_date(request.from_date)
        if request.to_date:
            to_date = parse_date(request.to_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 创建任务记录
    tasks[task_id] = {
        "task_id": task_id,
        "status": TaskStatus.PENDING,
        "message": "任务已创建，等待执行",
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
        "result": None,
        "error": None
    }

    # 添加后台任务
    background_tasks.add_task(
        background_download_task,
        task_id,
        request.search_id,
        from_date,
        to_date,
        request.output_format.value,
        request.auto_intercept,
        request.keywords,
        request.max_items,
    )

    logger.info(f"创建下载任务: {task_id}")

    return TaskResponse(
        task_id=task_id,
        status=TaskStatus.PENDING,
        message="任务已创建，正在处理中"
    )

@app.get("/tasks/{task_id}", response_model=TaskStatusResponse, tags=["Tasks"])
async def get_task_status(task_id: str):
    """
    查询任务状态

    - **task_id**: 任务ID
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    return TaskStatusResponse(**tasks[task_id])

@app.get("/tasks", tags=["Tasks"])
async def list_tasks(
    status: Optional[TaskStatus] = Query(None, description="按状态筛选"),
    limit: int = Query(10, ge=1, le=100, description="返回数量限制")
):
    """
    列出所有任务

    - **status**: 按状态筛选（可选）
    - **limit**: 返回数量限制
    """
    task_list = list(tasks.values())

    # 按状态筛选
    if status:
        task_list = [t for t in task_list if t["status"] == status]

    # 按创建时间倒序排序
    task_list.sort(key=lambda x: x["created_at"], reverse=True)

    # 限制数量
    task_list = task_list[:limit]

    return {
        "total": len(task_list),
        "tasks": task_list
    }

@app.get("/download/{task_id}/file", tags=["Download"])
async def download_file(
    task_id: str,
    file_type: str = Query("json", description="文件类型: json 或 excel")
):
    """
    下载任务生成的文件

    - **task_id**: 任务ID
    - **file_type**: 文件类型 (json/excel)
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = tasks[task_id]

    if task["status"] != TaskStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="任务尚未完成")

    if not task.get("result") or not task["result"].get("output_files"):
        raise HTTPException(status_code=404, detail="未找到输出文件")

    # 查找对应类型的文件
    output_files = task["result"]["output_files"]
    target_file = None

    for file_path in output_files:
        if file_type == "json" and file_path.endswith(".json"):
            target_file = file_path
            break
        elif file_type == "excel" and file_path.endswith(".xlsx"):
            target_file = file_path
            break

    if not target_file or not Path(target_file).exists():
        raise HTTPException(status_code=404, detail=f"未找到 {file_type} 文件")

    # 返回文件
    filename = Path(target_file).name
    media_type = "application/json" if file_type == "json" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    return FileResponse(
        path=target_file,
        filename=filename,
        media_type=media_type
    )

@app.delete("/tasks/{task_id}", tags=["Tasks"])
async def delete_task(task_id: str):
    """
    删除任务记录

    - **task_id**: 任务ID
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    del tasks[task_id]

    return {"message": "任务已删除", "task_id": task_id}

@app.get("/health", tags=["Health"])
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "tasks_count": len(tasks)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
