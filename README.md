# 舆情数据下载工具

自动化 Meltwater 舆情数据采集工具。

## 快速开始

```bash
# 安装依赖（仅需一次）
pip install -r requirements.txt
playwright install chromium

# 配置 .env 文件
# MELTWATER_USERNAME=your_email@example.com
# MELTWATER_PASSWORD=your_password

# 运行
python download_only.py --keywords "耿同学" --max-items 100
```

## 完整参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--search-id` | str | config.yaml | Meltwater 搜索ID |
| `--from` | str | 今日 00:00 | 开始日期 `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM` |
| `--to` | str | 今日 23:59 | 结束日期，同上格式 |
| `--keywords` | str | 搜索预设 | 关键词布尔表达式 |
| `--max-items` | int | 3000（默认3000条） | 采集上限，`0` = 不限 |
| `--format` | str | both | 输出格式：`json` / `excel` / `both` |
| `--sources` | str | 全部 | 指定平台，逗号分隔 |
| `--sort` | str | reach | 排序方式 |
| `--auto-intercept` | flag | - | 强制重新拦截查询配置 |

## 关键词语法

表达式最外层必须用引号包裹（shell要求）。中文单词不需要内部引号：

```bash
# 单关键词
--keywords "蔡徐坤"

# OR 匹配（逗号或 OR 都可以）
--keywords "AI, 人工智能, machine learning"
--keywords "AI OR 人工智能 OR machine learning"

# AND 组合
--keywords "王树国 AND 福耀科技大学"

# 嵌套：AND + OR
--keywords "王树国 AND (福耀科技大学 OR 福耀科大) AND (学术不端 OR 抄袭)"

# NOT 排除
--keywords "AI AND 教育 NOT (广告 OR 推广)"
```

多词短语（含空格）需要内部双引号：
```bash
--keywords "\"machine learning\" AND 教育"
```

> `AND` → 必须同时满足 / `OR` `,` → 满足其一即可 / `NOT` → 排除 / `()` → 分组

## 平台筛选

```bash
--sources "youtube,twitter,facebook,instagram,tiktok,reddit"
--sources "twitter"
# 不指定 = 全平台
```

> 前提：Meltwater 中对应 search_id 的查询必须已配置了平台来源过滤，否则 `--sources` 无法匹配替换目标。

| 分类 | 可用值 |
|------|--------|
| Twitter/X | `twitter` |
| Meta | `facebook`, `instagram`, `threads` |
| Google | `youtube` |
| ByteDance | `tiktok`, `douyin` |
| 论坛/社区 | `reddit` |
| 职场 | `linkedin` |
| 直播 | `twitch` |
| 图片 | `pinterest` |
| 即时通讯 | `snapchat`, `linevoom`, `kakaotalk`, `wechat` |
| 新兴平台 | `bluesky` |
| 国内平台 | `sina_weibo`（微博）, `wechat`（微信）, `douyin`（抖音）, `bilibili`（B站）, `little_red_book`（小红书）, `youku`（优酷） |
| 内容类型 | `social_reviews`, `social_comments`, `social_message_boards`, `social_blogs` |

以上 24 个值均来自 Meltwater 原生拦截验证（search_id=28893311，全平台含国内除X）。其中内容类型（`social_*`）非独立平台，而是社交评论/论坛/博客等泛类别。

## 排序方式

```bash
--sort reach       # 触达量（默认）
--sort relevance   # 相关度
--sort date        # 日期
--sort engagement  # 互动量
--sort social_echo # 社交回声
--sort views       # 观看量
--sort sentiment   # 情感
```

## 常用示例

```bash
# 1. 简单采集，今日100条
python download_only.py --keywords "蔡徐坤" --max-items 100

# 2. 指定日期
python download_only.py --from "2026-06-18" --to "2026-06-28 23:59" --keywords "王树国 AND 福耀科技大学"

# 3. 复杂布尔 + 国外平台 + 相关度排序
python download_only.py ^
    --from "2026-07-01" --to "2026-07-15 23:59" ^
    --keywords "(文革 OR 文化大革命) AND (高校 OR 大学) NOT 广告" ^
    --sources "youtube,twitter,facebook,reddit" ^
    --sort relevance ^
    --max-items 500

# 4. 只输出 Excel
python download_only.py --keywords "数据安全" --format excel
```

## 输出

文件保存在 `data/` 目录：
- `meltwater_feed_YYYYMMDD_HHMMSS.json`
- `meltwater_feed_YYYYMMDD_HHMMSS.xlsx`

输出字段：`date`, `title`, `body`, `url`, `source`, `author`, `reach`, `sentiment`, `media_type` 等。

## 配置说明

### 环境变量（.env）

```bash
MELTWATER_USERNAME=your_email@example.com
MELTWATER_PASSWORD=your_password
```

### 配置文件（config.yaml）

```yaml
scraper:
  search_id: "28531155"  # 默认搜索ID（如果获取不到配置，可以改一个会话ID，即可正常使用）
  headless: true

output:
  dir: "data"
  filename: "meltwater_feed"
```

## 时区说明

- 输入时间：北京时间（UTC+8）
- API 调用：自动转换为 UTC 时间
- 返回数据中的时间为 UTC 格式（如 `2026-07-19T22:52:52.000Z`），+8小时即为北京时间

## Search ID 机制

1. 如果指定了 `--search-id`，查找 `data/intercepted_msearch_{search_id}.json`
2. 如果文件不存在，自动拦截该 search_id 的查询配置
3. 使用 `--auto-intercept` 可强制重新拦截

## 常见问题

### 登录失败

1. 检查 `.env` 中的用户名密码
2. 查看 `data/after_*.png` 截图确认登录流程
3. 检查网络连接

### 数据条数不对

- 检查 `--max-items` 设置，设为 `0` 获取全部
- 多天采集时按天均分配额，单天可能被截断

### 运行很慢

- 排序字段影响速度：`reach`、`relevance` 需全量排序，数据量大时慢；`date` 最快
- 减少时间跨度可提速

## 项目结构

```
├── download_only.py          # 主程序入口
├── main.py                   # 配置加载
├── config.yaml               # 配置文件
├── scraper/
│   ├── browser.py            # 浏览器控制与登录
│   └── api_client.py         # API 客户端与查询构建
├── storage/
│   └── writer.py             # 数据写入
├── utils/
│   ├── paths.py              # 路径处理
│   └── helpers.py            # 辅助函数
└── data/                     # 数据输出目录
```
