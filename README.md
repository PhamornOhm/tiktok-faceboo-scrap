# 📱 Social Media Scraper API

> API สำหรับ scrape ข้อมูลจาก Facebook และ TikTok ด้วย FastAPI และ Playwright

## 🎯 ภาพรวม
โปรเจคนี้ออกแบบมาเพื่อให้สามารถ:
- สร้างและจัดการโปรไฟล์ browser สำหรับ Facebook / TikTok
- ใช้งาน browser automation เพื่อเข้าถึงและดึงข้อมูลโพสต์หรือวิดีโอ
- เก็บข้อมูลผลลัพธ์ไว้ในโฟลเดอร์แยกตามผู้ใช้งาน
- ใช้งานร่วมกับ LLM (Groq / OpenAI) สำหรับ browser-use automation ได้หากต้องการ

---

## 🚀 เริ่มใช้งาน

### 1. ติดตั้ง dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. สร้างไฟล์ `.env`
ตัวอย่างไฟล์ `.env`:

```env
API_PORT=8000
API_TOKEN=your-secret-token-here
BASE_PROFILE_DIR=T_DATA/profiles
MAX_SCRAPE_RESULTS=100
MAX_DAYS_OLD=1
ENABLE_DEDUP=true
GROQ_API_KEY=your-groq-api-key
BROWSER_USE_LLM_MODEL=gpt-5-mini
```

> หมายเหตุ: ส่วน `API_TOKEN` จะช่วยให้ API ป้องกันการเข้าถึงโดยไม่ได้รับอนุญาต หากไม่ใช้งานให้เว้นว่างไว้หรือกำหนดค่าเป็นข้อความใดก็ได้

### 3. รันเซิร์ฟเวอร์

```bash
python3 main.py
# หรือ
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 4. เปิดดูเอกสาร API

เข้าไปที่:

- `http://localhost:8000/docs` - Swagger UI
- `http://localhost:8000/redoc` - ReDoc

---

## 📁 โครงสร้างโปรเจคสำคัญ

```
facebook&tiktok/
├── main.py              # FastAPI entry point รวม Facebook และ TikTok routers
├── modify_v1.py         # Facebook scraping module
├── tiktok_scrap_api.py  # TikTok scraping module
├── page_script.js       # สคริปต์ Inject เข้าเว็บโดย Playwright
├── requirements.txt     # Dependencies ของโปรเจค
├── .env                 # การตั้งค่า environment
├── FDATA/               # เก็บ Facebook profiles, logs, outputs
├── T_DATA/              # เก็บ TikTok profiles, logs, outputs
└── scraped_data/        # เก็บข้อมูล scraped แบบรวม
```

---

## 🔌 API Endpoints หลัก

### Health Check

| Method | Endpoint  | Description         |
|--------|-----------|---------------------|
| GET    | `/`       | ตรวจสอบสถานะ API   |
| GET    | `/health` | ตรวจสอบสุขภาพระบบ |

### Facebook Endpoints (`/fb`)

| ฟีเจอร์         | คำอธิบาย                                        |
|------------------|--------------------------------------------------|
| FB Profiles      | สร้างโปรไฟล์, ล็อกอิน, จัดการ session              |
| FB Scraping      | ดึงโพสต์จากกลุ่ม Facebook                         |
| FB Snapshot      | ถ่าย screenshot ของหน้าเว็บระหว่างทำงาน          |
| FB Sessions      | ดูแล session และ logout                          |
| FB Maintenance   | ลบหรือรีเซ็ตโปรไฟล์, ตรวจสอบไฟล์ profile          |

### TikTok Endpoints (`/tiktok`)

| ฟีเจอร์               | คำอธิบาย                                  |
|------------------------|--------------------------------------------|
| TikTok Profiles        | สร้างโปรไฟล์, Login ด้วย QR, จัดการ session |
| TikTok Scraping        | ค้นหาวิดีโอและดึงข้อมูลตาม keyword          |
| TikTok Snapshot        | ถ่าย screenshot หน้าเว็บ TikTok             |
| TikTok Sessions        | ดูแล session และจัดการ profile              |
| TikTok Maintenance     | ลบหรือรีเซ็ตโปรไฟล์, ตรวจสอบไฟล์ profile   |

---

## 💡 วิธีใช้งานเบื้องต้น

### 1. สร้างโปรไฟล์ TikTok
- เรียก endpoint ที่เกี่ยวข้องกับ TikTok profile
- ระบบจะสร้างโฟลเดอร์โปรไฟล์ใน `T_DATA/profiles/<username>/`
- ใช้ระบบ QR Login หรือ session persistence

### 2. สร้างโปรไฟล์ Facebook
- เรียก endpoint Facebook profile
- ระบบจะสร้างโปรไฟล์ใน `FDATA/profiles/<username>/`
- ใช้ browser profile แยกเพื่อป้องกัน session ขัดกัน

### 3. สแครปข้อมูล
- ใช้ Facebook หรือ TikTok scraping endpoint
- ข้อมูลที่ได้จะเก็บใน `scraped_data/` และภายในโฟลเดอร์ profile
- หากเปิด `ENABLE_DEDUP=true` ระบบจะตรวจ URL ที่เคย scrape แล้วก่อนบันทึก

---

## 🛠️ สถาปัตยกรรมและเทคโนโลยี

- FastAPI + Uvicorn: เป็น API server
- Playwright: ควบคุม browser แบบ headless/real browser
- BeautifulSoup4, lxml: ใช้ parse HTML เมื่อจำเป็น
- pydantic-settings / python-dotenv: อ่าน config จาก `.env`
- browser-use + Groq/OpenAI: รองรับ LLM สำหรับงาน browser automation แบบที่ต้องใช้ agent

---

## 🧩 ข้อมูลสำคัญในโค้ด

### `main.py`
- include routers จาก `modify_v1.py` และ `tiktok_scrap_api.py`
- จัดการ lifespan ของ FastAPI

### `modify_v1.py`
- Facebook router และ endpoint ต่าง ๆ
- class `BrowserUser` สำหรับจัดการ browser session และ profile
- ฟังก์ชันจัดการ session, login, scrape, snapshot

### `tiktok_scrap_api.py`
- TikTok router และ endpoint ต่าง ๆ
- settings class สำหรับ config
- helper สำหรับ deduplication URL
- logic สร้าง profile, login, scrape, snapshot

### `page_script.js`
- สคริปต์เล็ก ๆ ที่ inject ลงในเพจผ่าน Playwright
- ช่วยเก็บข้อมูลจาก DOM หรือสั่งให้ browser ทำงาน

---

## ✅ คำแนะนำการใช้งาน

- ใช้ profile แยกสำหรับแต่ละ account เพื่อป้องกัน session ปนกัน
- ตรวจสอบ log เมื่อมีปัญหา: `FDATA/logs/` และ `T_DATA/logs/`
- ถ้าจะใช้งาน LLM ให้ตั้งค่า `GROQ_API_KEY`
- เปลี่ยน `API_PORT` หรือ `BASE_PROFILE_DIR` ได้จาก `.env`

---

## 🔧 การทดสอบและตรวจสอบ

```bash
curl http://localhost:8000/health
```

- หากสำเร็จจะได้ JSON ตอบกลับว่า API ทำงานได้
- ใช้ Swagger UI (`/docs`) เพื่อเรียก endpoint และทดสอบ payload

---

## 📌 หมายเหตุ

- โปรเจคนี้ยังไม่ใช่ระบบ production-ready หากต้องใช้งานจริง ควรเพิ่ม:
  - authentication/authorization ที่เข้มงวด
  - rate limiting
  - error handling ที่ละเอียดขึ้น
  - การจัดการ resource ของ Playwright ให้ปลอดภัย

---

## 📄 License

MIT License
