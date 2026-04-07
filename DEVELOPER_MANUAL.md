# Social Media Scraper API — คู่มือสำหรับนักพัฒนา (Developer Manual)

เอกสารฉบับนี้สรุปโครงสร้างโค้ด การพัฒนา และแนวทางการขยายโปรเจค

โครงสร้างหลัก
- `main.py` — entry point ของ FastAPI, include routers
- `modify_v1.py` — Facebook scraping module (routes, `BrowserUser`, session manager)
- `tiktok_scrap_api.py` — TikTok scraping module (routes, settings, dedup helpers)
- `page_script.js` — สคริปต์ที่ inject ลงในเพจผ่าน Playwright
- `FDATA/`, `T_DATA/` — ไดเรกทอรีเก็บโปรไฟล์, logs และ outputs
- `scraped_data/` — ไฟล์ผลลัพธ์จากการสแครป

เพิ่ม Endpoint ใหม่
1. สร้าง Pydantic model สำหรับ request/response
2. เพิ่ม route ในไฟล์ที่เหมาะสม (`modify_v1.py` หรือ `tiktok_scrap_api.py`)
3. ใส่ tag และ docstring ให้ชัดเจนเพื่อให้แสดงใน Swagger UI
4. ทดสอบผ่าน Swagger UI และเขียน unit tests ถ้ามี

Session & Profile Management
- ใช้ profile isolation: แต่ละผู้ใช้มีโฟลเดอร์โปรไฟล์ใน `FDATA/profiles` หรือ `T_DATA/profiles`
- lifecycle ของ session จัดการผ่าน async context manager / FastAPI lifespan handlers

Deduplication (TikTok)
- ฟังก์ชัน `load_scraped_urls()` / `save_scraped_url()` ใช้บันทึก URL ที่ scrape แล้ว

Logging & Debugging
- ตรวจสอบ log ใน `FDATA/logs/` และ per-user logs
- ใช้ snapshot endpoints เพื่อตรวจสอบสถานะ UI ของ Playwright

Testing
- หากเพิ่มฟีเจอร์ใหม่ ให้เพิ่ม unit/integration tests
- รัน local test suite (ถ้ามี) ก่อนเปิด PR

ข้อแนะนำการพัฒนา
- รักษาสไตล์โค้ดเดิมและหลีกเลี่ยงการเปลี่ยนแปลง API สาธารณะโดยไม่แจ้งทีม
- แยกความรับผิดชอบให้ชัดเจน (router, manager, helpers)
- เพิ่มการจัดการข้อผิดพลาดและ rate-limiting ก่อน deploy

Deploy สั้นๆ
- สร้าง environment พร้อม Playwright browsers
- ตั้งค่า `.env` สำหรับ production (API_TOKEN, GROQ_API_KEY, BASE_PROFILE_DIR)
- ใช้ `uvicorn`/`gunicorn` กับ process manager หรือ Docker

ไฟล์อ้างอิงสำคัญ
- [main.py](main.py#L1)
- [modify_v1.py](modify_v1.py#L1)
- [tiktok_scrap_api.py](tiktok_scrap_api.py#L1)
- [AGENTS.md](AGENTS.md#L1)

หากต้องการ ผมสามารถสร้างตัวอย่าง unit test, Postman collection หรือ Dockerfile ให้ต่อได้
