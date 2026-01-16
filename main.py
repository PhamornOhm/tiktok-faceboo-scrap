# Social Media Scraper API
# รวม Facebook + TikTok scrapers เป็น API เดียว

from dotenv import load_dotenv
load_dotenv()

import os
import asyncio      
from contextlib import asynccontextmanager
from fastapi import FastAPI

# Import routers and managers from sub-modules
from modify_v1 import fb_router, fb_manager_start, fb_manager_stop
from tiktok_scrap_api import tiktok_router, tiktok_manager_start, tiktok_manager_stop

# ================== OPENAPI TAGS ==================
# กำหนด tags เพื่อแยกหมวดหมู่ใน Swagger UI
tags_metadata = [
    # ===== Facebook Section =====
    {"name": "Facebook", "description": "**Facebook Scraper API**"},
    {"name": "FB Profiles", "description": "สร้างโปรไฟล์และล็อกอิน Facebook"},
    {"name": "FB Scraping", "description": "ดึงโพสต์จากกลุ่ม Facebook"},
    {"name" : "FB Snapshot", "description": "ทำการ screenshot หน้าจอเพื่อแจ้งให้ user ได้เห็นการทำงาน"},
    {"name": "FB Sessions", "description": "จัดการ Sessions ที่กำลังทำงาน"},
    {"name": "FB Maintenance", "description": "ดูแลรักษาและลบโปรไฟล์"},
    
    # ===== TikTok Section =====
    {"name": "TikTok", "description": "**TikTok Scraper API**"},
    {"name": "TikTok Profiles", "description": "สร้างโปรไฟล์และ QR Login TikTok"},
    {"name": "TikTok Scraping", "description": "ดึงวิดีโอตาม Keyword"},
    {"name": "TikTok Snapshot", "description": "ทำการ screenshot หน้าจอเพื่อแจ้งให้ user ได้เห็นการทำงาน"},
    {"name": "TikTok Sessions", "description": "จัดการ Sessions ที่กำลังทำงาน"},
    {"name": "TikTok Maintenance", "description": "ดูแลรักษาและลบโปรไฟล์"},
    
    # ===== General =====
    {"name": "Health", "description": "ตรวจสอบสถานะ API"},
]

# ================== LIFESPAN ==================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Unified lifespan for both Facebook and TikTok session managers"""
    # Startup
    await fb_manager_start(app)
    await tiktok_manager_start(app)
    
    yield
    
    # Shutdown
    await fb_manager_stop(app)
    await tiktok_manager_stop(app)

# ================== FASTAPI APP ==================
app = FastAPI(
    title="Social Media Scraper API",
    version="1.0.0",
    description="รวม Facebook และ TikTok scrapers ไว้ในที่เดียว",
    openapi_tags=tags_metadata,
    lifespan=lifespan
)

# ================== INCLUDE ROUTERS ==================
app.include_router(fb_router, prefix="/fb")
app.include_router(tiktok_router, prefix="/tiktok")

# ================== ROOT ENDPOINT ==================
@app.get("/", tags=["Health"])
async def root():
    return {
        "status": "ok",
        "message": "Social Media Scraper API",
        "platforms": ["Facebook", "TikTok"],
        "docs": "/docs"
    }

@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}

# ================== MAIN ==================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("API_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
