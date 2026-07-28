# AGENTS.md

## Setup

```bash
pip install -r requirements.txt
playwright install chromium  # one-time only
cp .env.example .env          # fill in MELTWATER_USERNAME, MELTWATER_PASSWORD
```

**Excel export requires pandas**: `pip install pandas` (openpyxl is in requirements.txt, but pandas needed for DataFrame operations in `storage/writer.py:82-124`)
## Entry points

**CLI** (main interface):
```bash
python download_only.py                            # download with defaults
python download_only.py --search-id 24946297               # override search_id
python download_only.py --from "2026-04-18 12:00" --to "2026-04-19 12:00"  # custom date range
python download_only.py --format json                      # json only (default: both json+excel)
python download_only.py --auto-intercept             # force re-intercept query config
```

**FastAPI** (optional REST interface, see `API.md`):
```bash
uvicorn api:app --reload --host 0.0.0.0 --port 8000
```

Default date range: today 00:00 to 23:59 (Beijing Time) - set in `download_only.py:114-117`

**Auto-split by day**: If date range > 1 day, automatically splits into daily batches to avoid 10,000-item API limit. Each day downloaded separately, then merged. Implemented in `download_only.py:199-234` using `utils/helpers.py:split_date_range_by_day()`.

## Critical gotchas

**Timezone**: All input times are Beijing Time (UTC+8), auto-converted to UTC for API in `scraper/api_client.py:458-463`. Conversion happens once - do NOT convert again in calling code.

**Startup delay**: 10-30s silence after launch is normal (Chromium start, Auth0 login, token fetch). Not frozen.

**Search ID behavior**: `--search-id` looks for `data/intercepted_msearch_{search_id}.json` first. If missing, auto-intercepts on first run. To force re-intercept: use `--auto-intercept` flag.

**Credentials**: `.env` requires `MELTWATER_USERNAME` and `MELTWATER_PASSWORD`. `config.yaml` uses `${VAR}` syntax - substitution in `main.py:load_config()` via regex.

**Auth flow**: Multi-step Auth0 (email → Enter → password → Login). `scraper/browser.py:45-68` intercepts token from `resetToken` endpoint, saves to `data/token.json`. Token cached: next run loads from file, validates via test API call, re-logins only if expired.

**API client** (`scraper/api_client.py`):
- `ensure_token()` - loads cache → validates → re-login if needed
- `msearch_all()` - auto-paginated, limit=100/page
- Endpoint: `https://unified-search.meltwater.io/1.0/accounts/62bd23a40490b900113ddaca/msearch`

## Output

All files to `data/` (gitignored):
- `meltwater_feed_YYYYMMDD_HHMMSS.json` - raw data
- `meltwater_feed_YYYYMMDD_HHMMSS.xlsx` - Excel format

## Date formats

Supports: `YYYY-MM-DD`, `YYYY-MM-DD HH:MM`, `YYYY-MM-DD HH:MM:SS`, `YYYY/MM/DD`, `YYYY/MM/DD HH:MM`

Parsed in `utils/helpers.py:parse_date()`