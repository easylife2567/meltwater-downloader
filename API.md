# Meltwater Downloader API 文档

FastAPI 接口封装，提供 RESTful API 访问 Meltwater 数据下载功能。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

确保 `.env` 文件包含必要的配置：

```bash
MELTWATER_USERNAME=your_username
MELTWATER_PASSWORD=your_password
```

### 3. 启动服务

```bash
# 开发模式（自动重载）
uvicorn api:app --reload --host 0.0.0.0 --port 8000

# 生产模式
uvicorn api:app --host 0.0.0.0 --port 8000 --workers 4
```

### 4. 访问 API 文档

启动后访问：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## API 端点

### 1. 创建下载任务

**POST** `/download`

创建一个异步下载任务。

**请求体：**

```json
{
  "search_id": "24946297",
  "from_date": "2026-05-18 00:00",
  "to_date": "2026-05-18 23:59",
  "output_format": "both",
  "auto_intercept": false
}
```

**参数说明：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| search_id | string | 否 | 搜索ID，不提供则使用配置文件默认值 |
| from_date | string | 否 | 开始日期，格式: `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM`，默认今日00:00 |
| to_date | string | 否 | 结束日期，格式: `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM`，默认今日23:59 |
| output_format | string | 否 | 输出格式: `json`, `excel`, `both`（默认: `both`） |
| auto_intercept | boolean | 否 | 是否强制重新拦截查询配置（默认: `false`） |

**响应：**

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "任务已创建，正在处理中"
}
```

**示例：**

```bash
# 使用默认配置（今日数据）
curl -X POST "http://localhost:8000/download" \
  -H "Content-Type: application/json" \
  -d '{}'

# 指定日期范围
curl -X POST "http://localhost:8000/download" \
  -H "Content-Type: application/json" \
  -d '{
    "from_date": "2026-05-18 00:00",
    "to_date": "2026-05-18 23:59",
    "output_format": "json"
  }'

# 指定 search_id
curl -X POST "http://localhost:8000/download" \
  -H "Content-Type: application/json" \
  -d '{
    "search_id": "24946297",
    "from_date": "2026-05-17",
    "to_date": "2026-05-18"
  }'
```

### 2. 查询任务状态

**GET** `/tasks/{task_id}`

查询指定任务的状态和结果。

**响应：**

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "message": "下载完成",
  "created_at": "2026-05-18T10:00:00",
  "completed_at": "2026-05-18T10:05:30",
  "result": {
    "total_items": 1523,
    "output_files": [
      "/path/to/data/meltwater_feed_20260518_100530.json",
      "/path/to/data/meltwater_feed_20260518_100530.xlsx"
    ],
    "time_range": {
      "from": "2026-05-18 00:00:00",
      "to": "2026-05-18 23:59:59"
    }
  },
  "error": null
}
```

**任务状态：**

- `pending`: 等待执行
- `running`: 正在执行
- `completed`: 执行完成
- `failed`: 执行失败

**示例：**

```bash
curl -X GET "http://localhost:8000/tasks/550e8400-e29b-41d4-a716-446655440000"
```

### 3. 列出所有任务

**GET** `/tasks`

列出所有任务，支持按状态筛选。

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| status | string | 否 | 按状态筛选: `pending`, `running`, `completed`, `failed` |
| limit | integer | 否 | 返回数量限制（1-100，默认: 10） |

**响应：**

```json
{
  "total": 5,
  "tasks": [
    {
      "task_id": "550e8400-e29b-41d4-a716-446655440000",
      "status": "completed",
      "message": "下载完成",
      "created_at": "2026-05-18T10:00:00",
      "completed_at": "2026-05-18T10:05:30",
      "result": {...}
    }
  ]
}
```

**示例：**

```bash
# 列出所有任务
curl -X GET "http://localhost:8000/tasks"

# 只列出已完成的任务
curl -X GET "http://localhost:8000/tasks?status=completed"

# 列出最近20个任务
curl -X GET "http://localhost:8000/tasks?limit=20"
```

### 4. 下载文件

**GET** `/download/{task_id}/file`

下载任务生成的文件。

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| file_type | string | 否 | 文件类型: `json` 或 `excel`（默认: `json`） |

**示例：**

```bash
# 下载 JSON 文件
curl -X GET "http://localhost:8000/download/550e8400-e29b-41d4-a716-446655440000/file?file_type=json" \
  -o meltwater_data.json

# 下载 Excel 文件
curl -X GET "http://localhost:8000/download/550e8400-e29b-41d4-a716-446655440000/file?file_type=excel" \
  -o meltwater_data.xlsx
```

### 5. 删除任务

**DELETE** `/tasks/{task_id}`

删除指定任务记录。

**响应：**

```json
{
  "message": "任务已删除",
  "task_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**示例：**

```bash
curl -X DELETE "http://localhost:8000/tasks/550e8400-e29b-41d4-a716-446655440000"
```

### 6. 健康检查

**GET** `/health`

检查服务健康状态。

**响应：**

```json
{
  "status": "healthy",
  "timestamp": "2026-05-18T10:00:00",
  "tasks_count": 5
}
```

**示例：**

```bash
curl -X GET "http://localhost:8000/health"
```

## 完整使用流程

### Python 示例

```python
import requests
import time

# API 基础 URL
BASE_URL = "http://localhost:8000"

# 1. 创建下载任务
response = requests.post(f"{BASE_URL}/download", json={
    "from_date": "2026-05-18 00:00",
    "to_date": "2026-05-18 23:59",
    "output_format": "both"
})

task_id = response.json()["task_id"]
print(f"任务已创建: {task_id}")

# 2. 轮询任务状态
while True:
    response = requests.get(f"{BASE_URL}/tasks/{task_id}")
    task = response.json()
    
    print(f"任务状态: {task['status']} - {task['message']}")
    
    if task["status"] in ["completed", "failed"]:
        break
    
    time.sleep(5)  # 每5秒检查一次

# 3. 下载结果文件
if task["status"] == "completed":
    # 下载 JSON
    response = requests.get(
        f"{BASE_URL}/download/{task_id}/file",
        params={"file_type": "json"}
    )
    with open("result.json", "wb") as f:
        f.write(response.content)
    
    # 下载 Excel
    response = requests.get(
        f"{BASE_URL}/download/{task_id}/file",
        params={"file_type": "excel"}
    )
    with open("result.xlsx", "wb") as f:
        f.write(response.content)
    
    print(f"下载完成，共 {task['result']['total_items']} 条数据")
else:
    print(f"任务失败: {task.get('error')}")
```

### JavaScript 示例

```javascript
const BASE_URL = 'http://localhost:8000';

async function downloadData() {
  // 1. 创建下载任务
  const createResponse = await fetch(`${BASE_URL}/download`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      from_date: '2026-05-18 00:00',
      to_date: '2026-05-18 23:59',
      output_format: 'both'
    })
  });
  
  const { task_id } = await createResponse.json();
  console.log(`任务已创建: ${task_id}`);
  
  // 2. 轮询任务状态
  while (true) {
    const statusResponse = await fetch(`${BASE_URL}/tasks/${task_id}`);
    const task = await statusResponse.json();
    
    console.log(`任务状态: ${task.status} - ${task.message}`);
    
    if (task.status === 'completed') {
      console.log(`下载完成，共 ${task.result.total_items} 条数据`);
      
      // 3. 下载文件
      window.open(`${BASE_URL}/download/${task_id}/file?file_type=json`);
      window.open(`${BASE_URL}/download/${task_id}/file?file_type=excel`);
      break;
    }
    
    if (task.status === 'failed') {
      console.error(`任务失败: ${task.error}`);
      break;
    }
    
    await new Promise(resolve => setTimeout(resolve, 5000));
  }
}

downloadData();
```

## 高级功能

### 按天自动分割下载

当日期范围超过1天时，API 会自动按天分割下载，避免触及 API 的 10,000 条限制：

```bash
curl -X POST "http://localhost:8000/download" \
  -H "Content-Type: application/json" \
  -d '{
    "from_date": "2026-05-15",
    "to_date": "2026-05-18"
  }'
```

系统会自动分为 4 批次下载（5月15日、16日、17日、18日），然后合并结果。

### 自动拦截查询配置

首次使用新的 `search_id` 时，需要拦截查询配置：

```bash
curl -X POST "http://localhost:8000/download" \
  -H "Content-Type: application/json" \
  -d '{
    "search_id": "24946297",
    "auto_intercept": true
  }'
```

拦截成功后，配置会保存到 `data/intercepted_msearch_{search_id}.json`，后续请求会自动使用。

## 注意事项

1. **时区**: 所有输入时间均为北京时间（UTC+8），API 会自动转换为 UTC
2. **启动延迟**: 首次请求可能需要 10-30 秒（浏览器启动、登录、token 获取）
3. **并发限制**: 当前实现使用内存存储任务状态，生产环境建议使用 Redis 或数据库
4. **文件存储**: 下载的文件保存在 `data/` 目录，需要定期清理
5. **Excel 导出**: 需要安装 pandas: `pip install pandas`

## 生产部署建议

### 使用 Docker

创建 `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 安装 Playwright 浏览器
RUN playwright install chromium
RUN playwright install-deps chromium

# 复制应用代码
COPY . .

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
```

构建和运行：

```bash
docker build -t meltwater-api .
docker run -d -p 8000:8000 --env-file .env meltwater-api
```

### 使用 Supervisor

创建 `/etc/supervisor/conf.d/meltwater-api.conf`:

```ini
[program:meltwater-api]
command=/path/to/venv/bin/uvicorn api:app --host 0.0.0.0 --port 8000 --workers 4
directory=/path/to/meltwater-downloader
user=www-data
autostart=true
autorestart=true
stderr_logfile=/var/log/meltwater-api.err.log
stdout_logfile=/var/log/meltwater-api.out.log
```

### 使用 Nginx 反向代理

```nginx
server {
    listen 80;
    server_name api.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # 增加超时时间（下载任务可能需要较长时间）
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }
}
```

## 故障排查

### 问题：任务一直处于 pending 状态

**原因**: 后台任务可能未正确启动

**解决**: 检查日志，确认没有异常错误

### 问题：Token 获取失败

**原因**: 
- `.env` 文件配置错误
- Meltwater 账号密码错误
- 网络连接问题

**解决**: 
1. 检查 `.env` 文件配置
2. 验证账号密码
3. 检查网络连接

### 问题：下载的数据为空

**原因**:
- 时间范围内没有数据
- search_id 配置错误
- 查询配置未正确拦截

**解决**:
1. 确认时间范围内有数据
2. 使用 `auto_intercept: true` 重新拦截配置
3. 检查 `data/intercepted_msearch_{search_id}.json` 文件

## 技术支持

如有问题，请查看：
- API 文档: http://localhost:8000/docs
- 项目文档: `AGENTS.md`
- 日志输出: 控制台或日志文件
