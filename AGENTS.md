# AGENTS.md - Social Media Scraper API

คู่มือสำหรับ AI Agents ที่ต้องการทำงานกับโปรเจค Social Media Scraper API

## 📁 โครงสร้างโปรเจค

```
facebook&tiktok/
├── main.py              # FastAPI entry point รวม FB + TikTok routers
├── modify_v1.py         # Facebook scraper module (~2800 lines)
├── tiktok_scrap_api.py  # TikTok scraper module (~2100 lines)
├── page_script.js       # Playwright injection script
├── requirements.txt     # Python dependencies
├── FDATA/               # Facebook data storage
├── T_DATA/              # TikTok data storage
└── scraped_data/        # Scraped data output
```

## 🛠️ Tech Stack

- **Framework**: FastAPI + Uvicorn
- **Browser Automation**: Playwright (async)
- **LLM Integration**: browser-use, Groq API, OpenAI
- **Data Parsing**: BeautifulSoup4, lxml
- **Config**: pydantic-settings, python-dotenv

## 🚀 การรันโปรเจค

```bash
# 1. ติดตั้ง dependencies
pip install -r requirements.txt

# 2. ติดตั้ง Playwright browsers
playwright install chromium

# 3. ตั้งค่า .env (ถ้ามี)
# GROQ_API_KEY=xxx
# API_PORT=8000

# 4. รัน API server
python main.py
# หรือ
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## 🔌 API Endpoints Overview

### Facebook (`/fb`)
| Tag | Description |
|-----|-------------|
| FB Profiles | สร้างโปรไฟล์และล็อกอิน |
| FB Scraping | ดึงโพสต์จากกลุ่ม Facebook |
| FB Snapshot | Screenshot หน้าจอ |
| FB Sessions | จัดการ Sessions |
| FB Maintenance | ดูแลรักษาโปรไฟล์ |

### TikTok (`/tiktok`)
| Tag | Description |
|-----|-------------|
| TikTok Profiles | สร้างโปรไฟล์และ QR Login |
| TikTok Scraping | ดึงวิดีโอตาม Keyword |
| TikTok Snapshot | Screenshot หน้าจอ |
| TikTok Sessions | จัดการ Sessions |
| TikTok Maintenance | ดูแลรักษาโปรไฟล์ |

### Health
- `GET /` - Root status
- `GET /health` - Health check

## 📖 Key Functions & Classes

### `modify_v1.py` (Facebook)
- `fb_router` - FastAPI router สำหรับ FB endpoints
- `fb_manager_start()` / `fb_manager_stop()` - Session lifecycle
- `BrowserUser` - จัดการ browser session + LLM agent
- `extract_post_from_like_element()` - ดึงข้อมูลโพสต์จาก HTML
- `user_dirs()` - path utilities สำหรับแต่ละ user

### `tiktok_scrap_api.py` (TikTok)
- `tiktok_router` - FastAPI router สำหรับ TikTok endpoints
- `tiktok_manager_start()` / `tiktok_manager_stop()` - Session lifecycle
- `Settings` - Pydantic settings class
- `ChatGroqCustom` - Custom Groq LLM wrapper for browser-use
- `load_scraped_urls()` / `save_scraped_url()` - URL deduplication
- `is_video_within_days()` - ตรวจสอบวันที่โพสต์วิดีโอ

## ⚙️ Configuration (.env)

```env
# API
API_PORT=8000
API_TOKEN=your-secret-token-here

# Directories
BASE_PROFILE_DIR=T_DATA/profiles

# Scraping
MAX_SCRAPE_RESULTS=100
MAX_DAYS_OLD=1
ENABLE_DEDUP=true

# LLM (Optional)
GROQ_API_KEY=xxx
BROWSER_USE_LLM_MODEL=gpt-5-mini
```

## 🧪 Testing

```bash
# ตรวจสอบว่า API รันได้
curl http://localhost:8000/health

# ดู Swagger UI
open http://localhost:8000/docs
```

## 📝 Development Notes

1. **Session Management**: ใช้ asynccontextmanager lifespan สำหรับจัดการ startup/shutdown
2. **Profile Isolation**: แต่ละ user มี browser profile แยก เก็บใน `FDATA/` และ `T_DATA/`
3. **Logging**: มีทั้ง global logger และ per-user logger
4. **Deduplication**: TikTok มีระบบบันทึก URL ที่ scrape แล้วป้องกันซ้ำ

## 🔧 Common Tasks for AI Agents

### เพิ่ม Endpoint ใหม่
1. เลือกไฟล์ที่เหมาะสม (`modify_v1.py` หรือ `tiktok_scrap_api.py`)
2. สร้าง Pydantic model สำหรับ request/response
3. เพิ่ม route function ด้วย appropriate tags
4. Router จะถูก include อัตโนมัติผ่าน `main.py`

### Debug Scraping Issues
1. ตรวจสอบ `scraper.log` สำหรับ error messages
2. ใช้ Snapshot endpoints เพื่อดูสถานะหน้าจอ
3. ตรวจสอบ profile data ใน `FDATA/` หรือ `T_DATA/`

### Update Dependencies
```bash
pip freeze > requirements.txt
```
