# 📱 Social Media Scraper API

> API สำหรับ scrape ข้อมูลจาก Facebook และ TikTok แบบ Unified

[![FastAPI](https://img.shields.io/badge/FastAPI-0.128.0-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com/)
[![Playwright](https://img.shields.io/badge/Playwright-1.57.0-2EAD33?style=flat-square&logo=playwright)](https://playwright.dev/)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python)](https://python.org/)

---

## ✨ Features

- 🔵 **Facebook Scraping** - ดึงโพสต์จากกลุ่ม Facebook
- 🎵 **TikTok Scraping** - ค้นหาและดึงวิดีโอตาม Keyword
- 🤖 **LLM Integration** - รองรับ Groq, OpenAI สำหรับ browser automation
- 📸 **Screenshot/Snapshot** - ถ่ายรูปหน้าจอระหว่างทำงาน
- 🔐 **Profile Management** - จัดการ browser profile แยกแต่ละ user
- 🚫 **Deduplication** - ป้องกัน scrape ข้อมูลซ้ำอัตโนมัติ

---

## 🚀 Quick Start

### 1. ติดตั้ง Dependencies

```bash
# สร้าง Virtual Environment
python3 -m venv venv
source venv/bin/activate

# ติดตั้ง packages
pip install -r requirements.txt

# ติดตั้ง Playwright browsers
playwright install chromium
```

### 2. ตั้งค่า Environment Variables

สร้างไฟล์ `.env`:

```env
# API Configuration
API_PORT=8000
API_TOKEN=your-secret-token-here

# Directories
BASE_PROFILE_DIR=T_DATA/profiles

# Scraping Settings
MAX_SCRAPE_RESULTS=100
MAX_DAYS_OLD=1
ENABLE_DEDUP=true

# LLM (Optional - สำหรับ browser-use)
GROQ_API_KEY=your-groq-api-key
BROWSER_USE_LLM_MODEL=gpt-4-mini
```

### 3. รัน API Server

```bash
python3 main.py
# หรือ
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 4. เปิด API Docs

เข้าไปที่ **http://localhost:8000/docs** เพื่อดู Swagger UI

---

## 📁 Project Structure

```
facebook&tiktok/
├── main.py              # FastAPI entry point (รวม FB + TikTok routers)
├── modify_v1.py         # Facebook scraper module
├── tiktok_scrap_api.py  # TikTok scraper module
├── page_script.js       # Playwright injection script
├── requirements.txt     # Python dependencies
├── .env                 # Environment variables (สร้างเอง)
├── FDATA/               # Facebook data & profiles
├── T_DATA/              # TikTok data & profiles
└── scraped_data/        # Scraped data output
```

---

## 🔌 API Endpoints

### Health Check

| Method | Endpoint   | Description        |
|--------|------------|--------------------|
| GET    | `/`        | Root status        |
| GET    | `/health`  | Health check       |

### Facebook (`/fb`)

| Category        | Description                     |
|-----------------|---------------------------------|
| FB Profiles     | สร้างโปรไฟล์และล็อกอิน          |
| FB Scraping     | ดึงโพสต์จากกลุ่ม Facebook       |
| FB Snapshot     | Screenshot หน้าจอ               |
| FB Sessions     | จัดการ Sessions                 |
| FB Maintenance  | ดูแลรักษาและลบโปรไฟล์          |

### TikTok (`/tiktok`)

| Category           | Description                  |
|--------------------|------------------------------|
| TikTok Profiles    | สร้างโปรไฟล์และ QR Login    |
| TikTok Scraping    | ค้นหาวิดีโอตาม Keyword       |
| TikTok Snapshot    | Screenshot หน้าจอ            |
| TikTok Sessions    | จัดการ Sessions              |
| TikTok Maintenance | ดูแลรักษาและลบโปรไฟล์       |

---

## 🧪 Testing

```bash
# ตรวจสอบว่า API รันได้
curl http://localhost:8000/health

# Expected response:
# {"status": "healthy"}
```

---

## 🛠️ Tech Stack

| Component          | Technology                     |
|--------------------|--------------------------------|
| **Framework**      | FastAPI + Uvicorn              |
| **Browser**        | Playwright (async)             |
| **LLM**            | browser-use, Groq, OpenAI      |
| **Data Parsing**   | BeautifulSoup4, lxml           |
| **Configuration**  | pydantic-settings, python-dotenv |

---

## 📝 Development Notes

- **Session Management**: ใช้ `asynccontextmanager` lifespan สำหรับ startup/shutdown
- **Profile Isolation**: แต่ละ user มี browser profile แยก เก็บใน `FDATA/` และ `T_DATA/`
- **Logging**: มี global logger และ per-user logger (`scraper.log`)
- **Deduplication**: TikTok มีระบบบันทึก URL ที่ scrape แล้วป้องกันซ้ำ

---

## 📄 License

MIT License - ใช้งานได้ฟรี
