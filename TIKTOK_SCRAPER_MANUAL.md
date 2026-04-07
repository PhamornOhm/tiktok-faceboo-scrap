#  TikTok Scraper Developer Manual (คู่มือสำหรับนักพัฒนา)

ยินดีต้อนรับสู่คู่มือการใช้งานและพัฒนาระบบ **TikTok Scraper API**! เอกสารฉบับนี้รวบรวมทุกสิ่งที่ Developer จำเป็นต้องรู้ ตั้งแต่โครงสร้างระบบ การติดตั้ง ไปจนถึงรายละเอียด API แต่ละเส้นอย่างเจาะลึก พร้อมตัวอย่าง JSON ครบถ้วน

> **System Goal:** ระบบนี้ถูกออกแบบมาเพื่อดึงข้อมูลวิดีโอจาก TikTok อย่างมีประสิทธิภาพ โดยใช้เทคนิค Hybrid Scraper (Playwright + Internal API Injection) เพื่อให้ได้ข้อมูลที่ครบถ้วนและลดโอกาสถูกจับได้

---

##  สารบัญ (Table of Contents)

1.  [ Architecture Overview (โครงสร้างระบบ)](#-architecture-overview-โครงสร้างระบบ)
2.  [ Quick Start (เริ่มต้นใช้งาน)](#-quick-start-เริ่มต้นใช้งาน)
3.  [ Configuration (การตั้งค่า)](#-configuration-การตั้งค่า)
4.  [ API Reference (รายละเอียด API)](#-api-reference-รายละเอียด-api)
    *   [1. Profile & Login (จัดการบัญชี)](#group-1-profile--login-จัดการบัญชี)
    *   [2. Scraping Operations (การดึงข้อมูล)](#group-2-scraping-operations-การดึงข้อมูล)
    *   [3. Maintenance & Monitoring (การดูแลระบบ)](#group-3-maintenance--monitoring-การดูแลระบบ)
5.  [ Code Structure & Logic](#-code-structure--logic-เจาะลึกการทำงาน)
6.  [ Troubleshooting](#-troubleshooting-การแก้ปัญหาทั่วไป)

---

##  Architecture Overview (โครงสร้างระบบ)

###  Core Components
ระบบประกอบด้วย 3 ส่วนหลัก:
1.  **FastAPI Gateway (`main.py`)**: ประตูหน้าบ้านสำหรับรับ Request ทั้งหมด และ Route ไปยัง Module ที่เหมาะสม
2.  **Scraper Engine (`tiktok_scrap_api.py`)**: หัวใจหลักในการทำงาน
    *   **Browser Control**: ใช้ `Playwright` ควบคุม Chrome แบบ Headless/Headful
    *   **Anti-Detection**: มีระบบ `SessionWarmer` และ `Human-like behavior` (scroll, mouse move)
    *   **Data Extraction**: ดึงข้อมูลจาก JSON ภายในหน้าเว็บ (`__UNIVERSAL_DATA_FOR_REHYDRATION__`) แทนการ Parse HTML ล้วนๆ
3.  **Persistence Layer (`T_DATA/`)**:
    *   **User Profiles**: เก็บ Session, Cookies, LocalStorage แยกราย User
    *   **Dedup Database**: `scraped_data/scraped_urls.json` ป้องกันการดึงซ้ำ

---

##  Quick Start (เริ่มต้นใช้งาน)

### 1. Prerequisites
*   OS: Linux / macOS / Windows
*   Python: 3.10+
*   Node.js: Required for Playwright

### 2. Installation
```bash
# 1. Clone & Setup Venv
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. Install Dependencies
pip install -r requirements.txt

# 3. Install Browser
playwright install chromium
```

### 3. Run Server
```bash
# รันด้วย Python โดยตรง
python3 main.py

# หรือรันด้วย Uvicorn (Hot Reload)
uvicorn main:app --reload --port 8000
```
เข้าใช้งาน Swagger UI ได้ที่: `http://localhost:8000/docs`

---

##  Configuration (การตั้งค่า)

สร้างไฟล์ `.env` ที่ Root Directory:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `API_PORT` | `8000` | Port ที่ API จะทำงาน |
| `BASE_PROFILE_DIR` | `T_DATA/profiles` | โฟลเดอร์เก็บข้อมูล Browser Profile แยกราย User |
| `MAX_SCRAPE_RESULTS` | `100` | จำนวนวิดีโอสูงสุดต่อการค้นหา 1 ครั้ง (Safety limit) |
| `MAX_DAYS_OLD` | `1` | กรองวิดีโอที่เก่ากว่า X วันทิ้ง (เพื่อเอาแค่ข้อมูลสด) |
| `ENABLE_DEDUP` | `true` | เปิด/ปิด ระบบป้องกัน URL ซ้ำ (`scraped_urls.json`) |
| `API_TOKEN` | - | (Optional) Token สำหรับ Authentication |

---

##  API Reference (รายละเอียด API)

### 1. Profile & Login (จัดการบัญชี)

#### 1.1 Create Profile & Login
> **Endpoint:** `POST /tiktok/create-profile-and-login`

- Endpoint นี้ใช้สำหรับเริ่มต้นใช้งานครั้งแรกและใช้สำหรับ Login ได้เช่นกันหากมี username ที่เคยสมัครไว้แล้ว 
- ขั้นตอนการสมัครให้คุณใส่ชื่อบัญชีที่ต้องการใช้ในช่อง username เช่น "testai" หลังจากนั้นให้กด execute  ได้เลย 
- ระบบจะเปิดตัว Browser ขึ้นมาให้ทำการ Login โดยในที่นี้ จะเป็นการ Login ด้วย Qr Code ให้ทำการเปิดแอพ TikTok แล้วทำการสแกน Qr Code
- เมื่อสแกนเสร็จแล้วจะทำการบันทึก session และตัว Browser จะทำการปิดตัวอัตโนมัติ
- เมื่อทำตามขั้นตอนที่ได้กล่าวไปแล้ว ก็สามารถใช้งาน Scrap ได้เลย


**Request JSON:**
```json
{
  "username": "bot_account_01",
  "headless": false
}
```

**Response JSON:**
```json
{
  "status": "waiting_for_login",
  "username_safe": "bot_account_01",
  "profile_dir": "T_DATA/profiles/bot_account_01",
  "message": "รอสแกน QR Code ใน Chrome (timeout 5 นาที) ให้เรียก /check-login-status เพื่อเช็คสถานะ",
  "is_logged_in": false
}
```

---

#### 1.2 Confirm Login
> **Endpoint:** `POST /tiktok/login-confirm`

- Endpoint นี้ใช้สำหรับยืนยันการ Login หลังจาก Login with Email/Password
- เหตุผลที่ต้องมี Endpoint นี้ก็เพราะว่า การ Login with Email/Password จะถูกใช้ก็ต่อเมื่อ Endpoint Qr ไม่สามารถใช้งานได้
    และสาเหตุจริงๆ การ Login ด้วยวิธีนี้ จะโดน Captcha และ OTP และหาก เราแก้ Captcha พลาด ตอน Login เสร็จระบบอาจจะไม่ทำการปิด Browser และไม่บันทึก session และถ้าเป็นแบบนั้นให้เรามา Confirm Login ทันทีเพื่อบันทึก Session


**Request JSON:**
```json
{
  "username": "bot_account_01",
  "headless": true
}
```

**Response JSON:**
```json
{
  "status": "success",
  "username_safe": "bot_account_01",
  "profile_dir": "T_DATA/profiles/bot_account_01",
  "message": "Login สำเร็จ! พร้อมใช้งานในโหมด Headless",
  "is_logged_in": true
}
```

---

#### 1.3 Login with Email/Password (Alternative)
> **Endpoint:** `POST /tiktok/login-email-password`

- ทางเลือกสำรองสำหรับการ Login โดยไม่ต้องสแกน QR (มีความเสี่ยงติด Puzzle/OTP สูงกว่า)
- ให้ใส่ username ที่ต้องการ และนำ Email และ Password ที่สมัครกับ TikTok ไว้แล้วมาใส่ในช่อง Email และ Password และกด Execute

**Request JSON:**
```json
{
  "username": "bot_account_01",
  "email": "myemail@example.com",
  "password": "mypassword123",
  "headless": false
}
```

---

#### 1.4 Check Login Status
> **Endpoint:** `GET /tiktok/check-login-status/{username}`

- ตรวจสอบสถานะปัจจุบันของ Browser ว่า Login อยู่หรือไม่

**Response JSON:**
```json
{
  "status": "checked",
  "is_logged_in": true,
  "total_cookies": 15,
  "session_cookies_count": 1,
  "message": "Login แล้ว - พร้อม confirm"
}
```

---

### 2. Scraping Operations (การดึงข้อมูล)

#### 2.1 Scrape Videos (Synchronous)
> **Endpoint:** `POST /tiktok/scrape-videos`

- Endpoint นี้ใช้สำหรับดึงข้อมูลวิดีโอจาก TikTok โดยใช้ Keyword ที่ต้องการ
- ให้ใส่ username ของท่านเอง และใส่ keyword ที่ต้องการ 
- ใส่จำนวนผลลัพธ์ที่ต้องการ **(แนะนำ 20-30 )**
- headless True คือ การทำงานโดยไม่เปิด browser ถ้าอยากเห็นกระบวนการทำงานให้ปรับเป็น False


**Request JSON:**
```json
{
  "username": "bot_account_01",
  "keywords": ["gadget", "review"],
  "max_results": 10,
  "headless": true
}
```

**Response JSON:**
```json
{
  "status": "success",
  "data": [
    {
      "url": "https://www.tiktok.com/@user/video/1234567890",
      "scraped_at": "2024-02-16 10:30:00",
      "description": "รีวิวหูฟังตัวใหม่ เสียงดีมาก #gadget #review",
      "author": "tech_guru",
      "author_nickname": "Tech Guru Thailand",
      "views": "15000",
      "likes": "1200",
      "comments": "340",
      "post_date": "2024-02-15 10:30",
      "hashtags": ["gadget", "review"],
      "keyword": "gadget"
    }
  ],
  "keywords_searched": ["gadget", "review"],
  "total_found": 15,
  "total_scraped": 10,
  "skipped_duplicate": 2,
  "skipped_old": 3,
  "keyword_stats": {
      "gadget": {"found": 8, "scraped": 5},
      "review": {"found": 7, "scraped": 5}
  }
}
```
- จากผลลัพธ์ที่ได้หากไม่ได้ข้อมูลวิดิโอตามที่ต้องการ สาเหตุอาจจะมาจาก คลิปที่เจอซ้ำ หรือ เก่ามากกเกินไปจากที่กำหนดไว้นั่นทำให้ระบบข้ามวิดิโอเหล่านั้นไป 

---

#### 2.2 Scrape Videos Webhook (Asynchronous)
> **Endpoint:** `POST /tiktok/scrape-videos-webhook`

**ระบบฝากงาน (Asynchronous) - ยิงแล้วไปทำอย่างอื่นได้เลย**

เหมาะสำหรับงานที่ต้องดึงข้อมูลจำนวนมาก (ซึ่งอาจใช้เวลานานหลายนาที)
- คุณส่งคำสั่งไปที่ API  ให้ใส่ข้อมูลเหมือนกับ scrap แบบธรรมดาได้เลย แค่เปลี่ยนวิธีการทำงานเป็นแบบ Asynchronous
- API ตอบกลับทันทีว่า "ได้รับงานแล้วนะ นี่คือเลข Job ID ของคุณ" (ไม่ต้องรอโหลดเสร็จ)
- ระบบจะทำงานเบื้องหลังของมันไปเรื่อยๆ
- เมื่อเสร็จแล้ว ระบบจะเอาข้อมูลทั้งหมด ไปส่งให้คุณที่ Link (`webhook_url`) ที่คุณระบุไว้

**Request JSON:**
```json
{
  "username": "bot_account_01",
  "keywords": ["iphone 16", "apple"],
  "webhook_url": "https://your-backend.com/api/receive-tiktok-data",
  "max_results": 50,
  "headless": true
}
```

**Response JSON (Immediate):**
```json
{
  "ok": true,
  "task_id": "20240216-103000-a1b2c3",
  "username_safe": "bot_account_01",
  "status": "running",
  "queue_position": 1,
  "message": "เริ่มงานดึงข้อมูลแล้ว จะส่งผลลัพธ์ไปยัง webhook_url เมื่อเสร็จ",
  "webhook_url": "https://your-backend.com/api/receive-tiktok-data"
}
```

**Webhook Payload (Sent to your URL when finished):**
*   *Format เดียวกับ Response ของ 2.1 พร้อม field `ok`: true/false และ `task_id`*

---

#### 2.3 Warm Session
> **Endpoint:** `POST /tiktok/warm-session`

- ใช้สำหรับ "วอร์มเครื่อง" บัญชี TikTok ให้ดูเหมือนคนเล่นจริงๆ ก่อนเริ่มดึงข้อมูล (Scraping) เพื่อลดความเสี่ยงในการโดนแบน หรือโดน Rate Limit
- ระบบจะเปิดหน้า Feed "For You"
- ทำการเลื่อนดูคลิป (Scroll Down) ไปเรื่อยๆ
- สุ่มระยะเวลาในการดูแต่ละคลิป (5-10 วินาที) เหมือนคนกำลังดูจริง
- สุ่มกด Like บ้างเป็นครั้งคราว (ถ้าเปิดโหมด LLM Agent จะฉลาดกว่านี้)
- ควรเรียกใช้วันละ 1-2 ครั้ง หรือก่อนเริ่มงานใหญ่ๆ

**Request JSON:**
```json
{
  "username": "bot_account_01",
  "duration": 2  
}
```

**Response JSON:**
```json
{
  "status": "success",
  "duration_seconds": 120.5,
  "total_actions": 15,
  "message": "Session warmed for 120.5 seconds with 15 LLM actions"
}
```

---

### 3. Maintenance & Monitoring (การดูแลระบบ)

#### 3.1 Session Snapshot
> **Endpoint:** `GET /tiktok/snapshot/{username}`

- Endpoint นี้ใช้ดูหน้าจอของบอทแบบ Real-time (Binary Image Response) ใช้เพื่อตรวจสอบว่ากำลังทำงานอยู่หรือไม่ หรือ ตรวจสอบว่าโดน Captcha ไหม โดนบล้อคไหม
- **Response:** `image/png` (รูปภาพหน้าจอ)

#### 3.2 List Active Sessions
> **Endpoint:** `GET /tiktok/sessions`

- ดูว่าตอนนี้มีบอทตัวไหนกำลังทำงาน (Running) หรือเปิดค้างไว้เฉยๆ (Idle)

**Response JSON:**
```json
{
  "active_sessions": [
    {
      "username": "bot_account_01",
      "locked": true,
      "idle_seconds": 0
    },
    {
      "username": "bot_account_02",
      "locked": false,
      "idle_seconds": 300
    }
  ],
  "total": 2
}
```

#### 3.3 Kill Session
> **Endpoint:** `DELETE /tiktok/sessions/{username}`

- สั่งปิด Browser ของบอทตัวนั้นทันที (เช่น กรณีค้าง หรือกิน Ram เยอะเกินไป)

**Response JSON:**
```json
{
  "status": "success", 
  "message": "Session for bot_account_01 closed and cleared from memory."
}
```

#### 3.4 List All Profiles
> **Endpoint:** `GET /tiktok/profiles`

- ดูรายชื่อโฟลเดอร์ Profile ทั้งหมดที่มีในเครื่อง (`T_DATA/profiles/`)

**Response JSON:**
```json
{
  "total_count": 2,
  "profiles": [
    {
      "username": "bot_account_01",
      "profile_dir": "T_DATA/profiles/bot_account_01",
      "exists": true,
      "size_mb": 150.5,
      "is_active": true
    },
     {
      "username": "bot_account_old",
      "profile_dir": "T_DATA/profiles/bot_account_old",
      "exists": true,
      "size_mb": 120.0,
      "is_active": false
    }
  ]
}
```

#### 3.5 Delete Profile (Full Reset)
> **Endpoint:** `POST /tiktok/profiles/delete?username=bot01`

**คำเตือน:** จะลบทุกอย่างของ User นั้น! โปรดระวังด้วยนะจ้ะ

**Response JSON:**
```json
{
  "status": "success", 
  "message": "Profile and session for bot_account_01 deleted successfully."
}
```

---

## 🛠️ Code Structure & Logic (เจาะลึกการทำงาน)

### ไฟล์สำคัญ (Key Files)
*   `tiktok_scrap_api.py`: 
    *   **Class `TikTokScraper`**: ควบคุม Playwright, ฟังก์ชัน `click`, `type`, `scroll` แบบสุ่ม (Human-like)
    *   **Function `extract_from_json_robust`**: พยายามดึงข้อมูลจากตัวแปร `__UNIVERSAL_DATA_FOR_REHYDRATION__` ในหน้าเว็บก่อน ซึ่งจะได้วันเวลาโพสต์ที่แม่นยำกว่าหน้าเว็บปกติ
*   `scraped_data/scraped_urls.json`: 
    *   เก็บ URL ที่เคยดึงไปแล้ว เพื่อไม่ให้ดึงซ้ำ (Dedup Logic)
    *   ถ้าตั้งค่า `ENABLE_DEDUP=true` ระบบจะเช็คไฟล์นี้ก่อน Scrape เสมอ

---

##  Troubleshooting (การแก้ปัญหาทั่วไป)

###  1. Login แล้วหลุดบ่อย
*   **สาเหตุ:** Cookies หมดอายุ หรือ TikTok จับได้
*   **วิธีแก้:** เรียก `/tiktok/profiles/delete` แล้วสร้าง Profile ใหม่

###  2. ติด Captcha (จิ๊กซอว์ / หมุนรูป)
*   **อาการ:** บอทนิ่งไปนาน (Timeout) หรือ Snapshot เห็นรูปจิ๊กซอว์
*   **วิธีแก้:**
    *   หยุด Scrape ชั่วคราว (1-2 ชั่วโมง)
    *   หรือเปิด Headful Mode แล้วเข้าไปเลื่อนจิ๊กซอว์เอง

###  3. Browser Crash / กิน RAM
*   **สาเหตุ:** Playwright เปิด Chrome หลายตัวพร้อมกัน
*   **วิธีแก้:**
    *   ใช้ `/check-login-status` เช็คว่า Browser เปิดอยู่ไหม
    *   ใช้ `/sessions/{username}` (DELETE) เพื่อปิด PID ที่ค้าง

###  4. ได้ข้อมูลน้อยกว่าที่ขอ
*   **สาเหตุ:**
    *   วิดีโอส่วนใหญ่เก่าเกินค่า `MAX_DAYS_OLD`ในที่นี้ตั้ง base ไว้แค่ 1 วันเพื่อที่จะหาวิดิโอใหม่ที่สุด (หากต้องการข้อมูลที่เก่ากว่านี้ให้แก้ในโค้ดได้ตามที่ต้องการ)
    *   URL อาจะซ้ำกับที่เคยดึง (`ENABLE_DEDUP`)

---
