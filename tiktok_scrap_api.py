from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, BrowserContext, Page, Browser
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi import FastAPI, HTTPException, Request, Depends, Path as PathParam, BackgroundTasks, APIRouter
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from datetime import datetime, timezone
#from slowapi import Limiter, _rate_limit_exceeded_handler
#from slowapi.util import get_remote_address
#from slowapi.errors import RateLimitExceeded
import pytz 
import asyncio
import json
import os
import sys
import time
import shutil
import random
import urllib.parse
import httpx
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any, Literal, Union

from dotenv import load_dotenv
load_dotenv()

# ================== LOGGING SETUP ==================
logger = logging.getLogger("tiktok_scraper")
handler = RotatingFileHandler("scraper.log", maxBytes=10**7, backupCount=5)
handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Console handler
console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
logger.addHandler(console)

# ================== SETTINGS ==================
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"  # อนุญาตให้มี extra fields ใน .env โดยไม่ error
    )
      
    base_profile_dir: str = "T_DATA/profiles"
    api_idle_timeout_sec: int = 600  # 10 นาที 
    groq_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None  # OpenAI API Key
    llm_provider: str = "openai"  # "openai" หรือ "groq"
    browser_use_llm_model: str = "gpt-4o-mini"  # default เป็น OpenAI model
    api_token: str = "your-secret-token-here"  # ต้องเปลี่ยนใน .env
    max_scrape_results: int = 100
    rate_limit: str = "10/minute"
    max_days_old: int = 1        # กรองวิดีโอเก่ากว่า 1 วัน (ดึงแค่ล่าสุด)
    enable_dedup: bool = True           # เปิดการกรอง URL ซ้ำ
    scraped_urls_file: str = "scraped_data/scraped_urls.json"  # ไฟล์เก็บ URL ที่ดึงแล้ว

settings = Settings()

# ================== HYBRID IMPORTS ==================
try:
    from groq import Groq
    from browser_use.llm import ChatOpenAI  # ใช้จาก browser_use แทน langchain_openai
    from browser_use import Agent, BrowserSession, Controller
    LLM_AGENT_ENABLED = True

    class ChatGroqCustom:
        """Custom Groq LLM wrapper for browser-use"""
        def __init__(self, model: str = "llama-3.1-8b-instant", api_key: str = None):
            self._model_name = model
            self.api_key = api_key or settings.groq_api_key
            if not self.api_key:
                raise ValueError("GROQ_API_KEY not found!")
            self.client = Groq(api_key=self.api_key)
            logger.info(f"ChatGroqCustom initialized: model={model}")
        
        @property
        def model_name(self): return self._model_name
        @property
        def model(self): return self._model_name
        @property
        def provider(self): return "groq"
        
        async def generate_response(self, messages, **kwargs):
            max_retries = 3
            retry_count = 0
            while retry_count < max_retries:
                try:
                    formatted_messages = []
                    for msg in messages:
                        if isinstance(msg, dict):
                            formatted_messages.append(msg)
                        else:
                            formatted_messages.append({
                                "role": getattr(msg, 'role', 'user'),
                                "content": getattr(msg, 'content', str(msg))
                            })
                    response = self.client.chat.completions.create(
                        model=self._model_name,
                        messages=formatted_messages,
                        max_tokens=2000,
                        temperature=0.3,
                        timeout=30.0
                    )
                    if not response or not response.choices or not response.choices[0].message:
                        raise ValueError("Invalid response from Groq API")
                    result = response.choices[0].message.content
                    if not result:
                        raise ValueError("Empty content in response")
                    return result
                except Exception as e:
                    retry_count += 1
                    logger.warning(f"Groq API attempt {retry_count} failed: {e}")
                    if retry_count >= max_retries:
                        logger.error("Groq API max retries exceeded")
                        return "I cannot complete this task due to API issues."
                    wait_time = 2 ** retry_count
                    await asyncio.sleep(wait_time)
               
        def _get_api_key(self): return self.api_key
    
except ImportError:
    LLM_AGENT_ENABLED = False
    logger.warning("browser_use not installed, LLM agent disabled")
    class ChatGroqCustom: pass
    class Controller: pass
    class BrowserSession: pass
    class Agent: pass

# ================== CONFIG ==================
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

def to_safe_name(username_full: str) -> str:
    return username_full.split("@", 1)[0] if "@" in username_full else username_full

def user_dirs(username_safe: str) -> Dict[str, str]:
    d = {"profile_dir": str(Path(settings.base_profile_dir).joinpath(username_safe))}
    for p in d.values():
        Path(p).mkdir(parents=True, exist_ok=True)
    return d

def profile_has_data(profile_dir: str) -> bool:
    session_file = Path(profile_dir).joinpath("session_state.json")
    return session_file.exists() and session_file.stat().st_size > 50

# ================== BROWSER PID UTILITIES ==================
import socket
import subprocess

def get_free_port() -> int:
    """ขอพอร์ตว่างจากระบบ"""
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port

def find_pid_by_port(port: int) -> Optional[int]:
    """ค้นหา PID ของ Chrome/Chromium ที่ใช้ remote-debugging-port"""
    try:
        # Linux/Mac
        command = f'pgrep -f "chrome.*--remote-debugging-port={port}"'
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            pid = int(result.stdout.strip().splitlines()[0])
            return pid
        return None
    except (ValueError, FileNotFoundError):
        return None

# ================== URL DEDUPLICATION ==================
def load_scraped_urls() -> set:
    """โหลด URLs ที่เคย scrape แล้วจากไฟล์ JSON"""
    try:
        path = Path(settings.scraped_urls_file)
        if path.exists():
            import json
            return set(json.loads(path.read_text()))
    except Exception as e:
        logger.warning(f"Cannot load scraped URLs: {e}")
    return set()

def save_scraped_url(url: str):
    """บันทึก URL ที่ scrape แล้วลงไฟล์ JSON"""
    try:
        import json
        path = Path(settings.scraped_urls_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        urls = load_scraped_urls()
        urls.add(url)
        # เก็บแค่ 10,000 URLs ล่าสุด (ป้องกันไฟล์ใหญ่เกิน)
        if len(urls) > 10000:
            urls = set(list(urls)[-10000:])
        path.write_text(json.dumps(list(urls), ensure_ascii=False, indent=2))
        logger.debug(f"Saved URL to dedup file: {url}")
    except Exception as e:
        logger.warning(f"Cannot save scraped URL: {e}")

def is_video_within_days(post_date_str: str, max_days: int) -> bool:
    """
    ตรวจสอบว่าวิดีโอโพสต์ไม่เกิน max_days วัน
    รองรับหลายรูปแบบ: timestamp (int), ISO date, relative (e.g., "2024-12-15")
    Returns True ถ้าวิดีโออยู่ในช่วงเวลาที่กำหนด หรือ parse ไม่ได้
    """
    if not post_date_str or max_days <= 0:
        return True  # ไม่กรอง
    
    try:
        # กรณี timestamp (Unix epoch)
        if isinstance(post_date_str, (int, float)) or post_date_str.isdigit():
            post_date = datetime.fromtimestamp(int(post_date_str))
        else:
            # ลอง parse หลายรูปแบบ
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"]:
                try:
                    post_date = datetime.strptime(post_date_str, fmt)
                    break
                except ValueError:
                    continue
            else:
                # Parse ไม่ได้ ให้ผ่าน
                logger.warning(f"Cannot parse post_date: {post_date_str}")
                return True
        
        days_ago = (datetime.now() - post_date).days
        return days_ago <= max_days
    except Exception as e:
        logger.warning(f"Error checking video age: {e}")
        return True  # ถ้า error ให้ผ่าน

# ================== SESSION WARMER ==================
class SessionWarmer:
    def __init__(self, page: Page, browser_pid: Optional[int] = None):
        self.page = page
        self.browser_pid = browser_pid
        if LLM_AGENT_ENABLED and browser_pid is not None:
            self.controller = Controller()
            
            # เลือก LLM Provider ตาม settings
            if settings.llm_provider == "openai" and settings.openai_api_key:
                self.llm_model = ChatOpenAI(
                    model=settings.browser_use_llm_model,
                    api_key=settings.openai_api_key
                )
                logger.info(f"Using OpenAI: {settings.browser_use_llm_model}")
            elif settings.groq_api_key:
                self.llm_model = ChatGroqCustom(
                    model=settings.browser_use_llm_model,
                    api_key=settings.groq_api_key
                )
                logger.info(f"Using Groq: {settings.browser_use_llm_model}")
            else:
                raise ValueError("No LLM API key found! Set OPENAI_API_KEY or GROQ_API_KEY in .env")
            
            self.agent_initialized = True
            logger.info(f"LLM Agent initialized with browser_pid={browser_pid}")
        else:
            self.controller = None
            self.llm_model = None
            self.agent_initialized = False
            if LLM_AGENT_ENABLED and browser_pid is None:
                logger.warning("LLM Agent disabled: browser_pid not available, using fallback")
    
    async def warm_session(self, duration_minutes: int = 2, actions: List[str] = None):
        if not self.agent_initialized:
            return await self._fallback_warming(duration_minutes)
        
        if actions is None:
            actions = ['scroll', 'watch', 'like']
        
        logger.info(f"SESSION WARMING START - {duration_minutes} minutes with LLM Agent")
        
        duration_seconds = duration_minutes * 60
        start_time = time.time()
        total_actions = 0
        
        try:
            browser_session = BrowserSession(
                browser_pid=self.browser_pid,
                viewport={"width": 1260, "height": 768}
            )
            
            # Loop จนกว่าจะครบเวลา
            while (time.time() - start_time) < duration_seconds:
                elapsed = time.time() - start_time
                remaining = duration_seconds - elapsed
                
                if remaining < 10:  # ถ้าเหลือน้อยกว่า 10 วินาที ก็หยุด
                    break
                
                # สุ่มเลือก task ให้หลากหลาย
                task_options = [
                    "Scroll down the TikTok For You page slowly, pause to look at 2-3 videos",
                    "Watch the current video for 5-10 seconds, then scroll to next video",
                    "Browse TikTok naturally - scroll, pause, maybe like a video if interesting",
                    "Look at the current TikTok video, scroll down after a few seconds",
                    "Explore TikTok - scroll through videos like a real user would"
                ]
                task = random.choice(task_options)
                
                logger.info(f"Warming: {elapsed:.0f}s/{duration_seconds}s - Running task: {task[:50]}...")
                
                try:
                    agent = Agent(
                        task=task,
                        browser_session=browser_session,
                        llm=self.llm_model,
                        controller=self.controller,
                        max_steps=5  # จำกัด steps ต่อ task เพื่อให้ทำซ้ำได้หลายรอบ
                    )
                    await agent.run()
                    total_actions += 1
                    
                    # พักระหว่าง task
                    await asyncio.sleep(random.uniform(2, 5))
                    
                except Exception as task_error:
                    logger.warning(f"Task error (continuing): {task_error}")
                    await asyncio.sleep(3)
            
            elapsed = time.time() - start_time
            logger.info(f"Session warming completed in {elapsed:.1f}s with {total_actions} actions")
            return {
                'status': 'success',
                'duration_seconds': round(elapsed, 1),
                'total_actions': total_actions,
                'actions_planned': actions,
                'message': f'Session warmed for {elapsed:.1f} seconds with {total_actions} LLM actions'
            }
        except Exception as e:
            logger.error(f"LLM session warming failed: {e}", exc_info=True)
            return await self._fallback_warming(duration_minutes)
    
    async def _fallback_warming(self, duration_minutes: int):
        try:
            # ไปหน้า For You ถ้ายังไม่ได้อยู่
            if 'tiktok.com' not in self.page.url:
                logger.info("Navigating to TikTok For You page...")
                await self.page.goto('https://www.tiktok.com/foryou', timeout=30000)
                await self.page.wait_for_timeout(3000)
            
            duration_seconds = duration_minutes * 60
            elapsed = 0
            videos_watched = 0
            logger.info(f"Starting fallback warming for {duration_seconds}s...")
            
            # Click ที่หน้าเพื่อให้ focus
            await self.page.click('body')
            await self.page.wait_for_timeout(1000)
            
            while elapsed < duration_seconds:
                # ใช้ Arrow Down เพื่อเลื่อนไปวิดีโอถัดไป (TikTok ใช้ video swiper)
                await self.page.keyboard.press('ArrowDown')
                videos_watched += 1
                
                # รอดูวิดีโอ 5-10 วินาที (random)
                watch_time = random.randint(5, 10)
                await self.page.wait_for_timeout(watch_time * 1000)
                elapsed += watch_time
                
                # Log progress ทุก 30 วินาที
                if elapsed % 30 < watch_time:
                    logger.info(f"Warming progress: {elapsed}s/{duration_seconds}s (watched {videos_watched} videos)")
                
                # ทุก 20 วินาที ลอง Like (20% chance)
                if elapsed % 20 < watch_time:
                    try:
                        if random.random() < 0.2:
                            await self.page.keyboard.press('l')  # L = Like shortcut
                            logger.info("Liked a video!")
                    except:
                        pass
            
            logger.info(f"Fallback session warming completed: watched {videos_watched} videos in {elapsed}s")
            return {
                'status': 'success',
                'duration_seconds': elapsed,
                'videos_watched': videos_watched,
                'method': 'fallback',
                'message': f'Session warmed: watched {videos_watched} videos in {elapsed} seconds'
            }
        except Exception as e:
            logger.error(f"Session warming failed: {e}", exc_info=True)
            return {'status': 'failed', 'error': str(e), 'message': 'Session warming failed'}

# ================== TIKTOK SCRAPER ==================
class TikTokScraper:
    def __init__(self, browser_data_dir: str, headless: bool = True):
        self.headless = headless
        self.session_state_file = Path(browser_data_dir).joinpath("session_state.json")
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.playwright = None
        self.browser_pid: Optional[int] = None  # สำหรับ SessionWarmer

    async def init_browser(self):
        """เริ่มต้น browser และ Context ชั่วคราว + โหลดสถานะ Session"""
        if self.playwright is None:
            self.playwright = await async_playwright().start()

        if self.browser is None: 
            # Playwright รองรับเฉพาะ headless=True หรือ False (ไม่ใช่ string "new" เหมือน Selenium)
            headless_mode = self.headless  # True หรือ False
            
            stealth_args = [
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-gpu',
                '--disable-software-rasterizer',
                '--disable-extensions',
                '--disable-setuid-sandbox',
                '--window-size=1920,1080',
            ]
            
            self.browser = await self.playwright.chromium.launch(
                headless=headless_mode,
                channel='chrome',
                args=stealth_args,
            )
            logger.info("Browser launched successfully")

        context_args = {
            "user_agent": 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
            "viewport": {'width': 1260, 'height': 768},
            "locale": 'th-TH',
            "timezone_id": 'Asia/Bangkok'
        }
        
        if self.session_state_file.exists():
            context_args["storage_state"] = str(self.session_state_file)

        self.context = await self.browser.new_context(**context_args)
        self.page = await self.context.new_page()
        self.page.set_default_timeout(60000)
    
    # ================== HUMAN-LIKE BEHAVIOR ==================
    async def human_delay(self, min_sec: float = 1.0, max_sec: float = 3.0):
        """หน่วงเวลาแบบสุ่มเหมือนมนุษย์"""
        delay = random.uniform(min_sec, max_sec)
        await asyncio.sleep(delay)
    
    async def human_mouse_move(self):
        """เลื่อนเมาส์แบบสุ่มเหมือนมนุษย์"""
        if not self.page:
            return
        try:
            # เลื่อนเมาส์ไปตำแหน่งสุ่ม
            x = random.randint(200, 1000)
            y = random.randint(200, 600)
            await self.page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.1, 0.3))
        except:
            pass
    
    async def human_scroll(self, direction: str = "down", smooth: bool = True):
        """Scroll แบบนุ่มนวลเหมือนมนุษย์"""
        if not self.page:
            return
        try:
            if smooth:
                # Scroll ทีละนิด 3-5 ครั้ง
                for _ in range(random.randint(2, 4)):
                    scroll_amount = random.randint(100, 300)
                    if direction == "up":
                        scroll_amount = -scroll_amount
                    await self.page.mouse.wheel(0, scroll_amount)
                    await asyncio.sleep(random.uniform(0.2, 0.5))
            else:
                await self.page.evaluate('window.scrollBy(0, window.innerHeight * 0.8)')
            
            await asyncio.sleep(random.uniform(0.5, 1.0))
        except:
            pass
    
    async def human_type(self, selector: str, text: str):
        """พิมพ์ข้อความแบบมนุษย์ (ช้าๆ)"""
        if not self.page:
            return
        try:
            element = await self.page.query_selector(selector)
            if element:
                await element.click()
                await asyncio.sleep(random.uniform(0.3, 0.6))
                # พิมพ์ทีละตัวอักษร
                for char in text:
                    await self.page.keyboard.type(char, delay=random.randint(50, 150))
                await asyncio.sleep(random.uniform(0.3, 0.8))
        except:
            pass
    
    async def simulate_human_before_action(self):
        """จำลองพฤติกรรมมนุษย์ก่อนทำ action สำคัญ"""
        await self.human_mouse_move()
        await self.human_delay(0.5, 1.5)
        # บางครั้ง scroll นิดหน่อย
        if random.random() < 0.3:
            await self.human_scroll(smooth=True)


    async def save_session(self):
        if self.context:
            try:
                await self.context.storage_state(path=str(self.session_state_file))
                await asyncio.sleep(1)
                logger.info("Session saved successfully")
            except Exception as e:
                logger.error(f"Failed to save session: {e}", exc_info=True)

    async def check_login_status(self):
        """ตรวจสอบสถานะ login (ปรับปรุงรองรับ Desktop View)"""
        if not self.page: return False
        try:
            current_url = self.page.url
            
            # ถ้าอยู่หน้า login = ยังไม่ได้ login
            if 'login' in current_url.lower():
                return False
            
            # ตรวจสอบ cookies ก่อน (เร็วที่สุด)
            cookies = await self.context.cookies()
            has_session_cookie = any(
                'sessionid' in cookie['name'].lower() and 
                cookie.get('value') and 
                len(cookie.get('value', '')) > 10
                for cookie in cookies
            )
            
            # ถ้ามี session cookie แล้ว = login แล้ว (ไม่ต้องเปิดหน้า TikTok)
            if has_session_cookie:
                logger.info("Found valid session cookie - logged in")
                return True
            
            # ถ้าไม่มี cookie ให้ลองเปิดหน้า TikTok ตรวจสอบ
            logger.info("No session cookie, checking TikTok page...")
            
            # เปิดหน้า TikTok (timeout สั้น)
            try:
                await self.page.goto('https://www.tiktok.com', wait_until='domcontentloaded', timeout=10000)
                await self.page.wait_for_timeout(3000)
            except Exception as e:
                logger.warning(f"Failed to load TikTok: {e}")
                return False
            
            # เช็คอีกครั้งว่า redirect ไป login หรือไม่
            if 'login' in self.page.url.lower():
                return False
            
            # ตรวจหา avatar/login element
            login_selectors = [
                'div[data-e2e="nav-avatar"]',
                'span[data-e2e="top-level-avatar"]',
                'img[data-e2e="user-avatar"]',
            ]
            
            try:
                await self.page.wait_for_selector(
                    ', '.join(login_selectors),
                    timeout=3000
                )
                logger.info("Found avatar element - logged in")
                return True
            except:
                pass
            
            # ตรวจ cookies อีกครั้ง
            cookies = await self.context.cookies()
            has_session_cookie = any(
                'sessionid' in cookie['name'].lower() and 
                cookie.get('value') and 
                len(cookie.get('value', '')) > 10
                for cookie in cookies
            )
            
            if has_session_cookie:
                logger.info("Found session cookie after page load - logged in")
                return True
            
            logger.warning("No login indicators found")
            return False
            
        except Exception as e:
            logger.error(f"Error checking login status: {e}")
            return False

    async def search_and_get_video_urls(self, keyword, max_results=10, debug=False):
        if not self.page:
            raise RuntimeError("Browser not initialized.")
        
        max_results = min(max_results, settings.max_scrape_results)
        
        logger.info(f"Searching for keyword: '{keyword}'")
        
        # ไปหน้า For You ก่อน + delay เพื่อหลีกเลี่ยง rate limit
        logger.info("Going to For You page first...")
        await self.page.goto('https://www.tiktok.com/foryou', wait_until='networkidle')
        await self.page.wait_for_timeout(3000)
        
        # Random delay 10-30 วินาที เพื่อให้ดูเหมือนคนจริง
        delay = random.uniform(10, 30)
        logger.info(f"Waiting {delay:.1f}s before search (anti rate-limit)...")
        await asyncio.sleep(delay)
        
        # Scroll หน้า For You นิดหน่อยให้ดูเหมือนใช้งานจริง
        await self.page.evaluate('window.scrollBy(0, window.innerHeight * 0.5)')
        await self.page.wait_for_timeout(2000)
        
        logger.info("Ready to search...")
        
        # แล้วค่อยไป search/video
        encoded_keyword = urllib.parse.quote(keyword)
        # sort_type=2 = Most Recent, publish_time=1 = 24 ชั่วโมงที่ผ่านมา
        search_url = f"https://www.tiktok.com/search/video?q={encoded_keyword}&sort_type=2&publish_time=1"
        
        logger.info(f"URL: {search_url}")
        
        await self.page.goto(search_url)
        await self.page.wait_for_timeout(5000)
        
        video_urls = set()  # ใช้ set เพื่อป้องกัน URL ซ้ำ
        scroll_count = 0
        max_scrolls = 10
        no_new_results_count = 0
        
        while len(video_urls) < max_results and scroll_count < max_scrolls:
            # ดึง URL จากหน้าปัจจุบัน
            links = await self.page.evaluate("""
                () => {
                    const videoLinks = [];
                    const debugInfo = {found_containers: 0, found_links: 0, selectors_tried: []};
                    
                    // ลองหลาย selectors
                    const selectors = [
                        '[data-e2e="search_video-item"]',
                        '[data-e2e="search-video-item-container"]',
                        '[class*="DivItemContainerV2"]',
                        '[class*="DivWrapper"]',
                        'div[data-e2e="search-card-video"]'
                    ];
                    
                    for (const selector of selectors) {
                        const containers = document.querySelectorAll(selector);
                        debugInfo.selectors_tried.push({selector, count: containers.length});
                        
                        if (containers.length > 0) {
                            debugInfo.found_containers = containers.length;
                            containers.forEach(container => {
                                const link = container.querySelector('a[href*="/video/"]');
                                if (link) {
                                    const url = link.href.split('?')[0];
                                    if (!videoLinks.includes(url)) {
                                        videoLinks.push(url);
                                    }
                                }
                            });
                            if (videoLinks.length > 0) break;
                        }
                    }
                    
                    // ถ้ายังไม่เจอ ให้หาจาก search result area เท่านั้น
                    if (videoLinks.length === 0) {
                        const searchArea = document.querySelector('[data-e2e="search-common-area"], main, [role="main"]');
                        if (searchArea) {
                            const allLinks = searchArea.querySelectorAll('a[href*="/video/"]');
                            allLinks.forEach(link => {
                                const url = link.href.split('?')[0];
                                if (/\\/video\\/\\d+/.test(url) && !videoLinks.includes(url)) {
                                    videoLinks.push(url);
                                }
                            });
                        }
                    }
                    
                    debugInfo.found_links = videoLinks.length;
                    console.log('TikTok Debug:', JSON.stringify(debugInfo));
                    
                    return videoLinks;
                }
            """)
            
            previous_count = len(video_urls)
            
            # เพิ่ม URL ใหม่เข้า set
            for url in links:
                if len(video_urls) >= max_results:
                    break
                video_urls.add(url)
            
            current_count = len(video_urls)
            
            # ถ้าไม่มี URL ใหม่เพิ่มขึ้น
            if current_count == previous_count:
                no_new_results_count += 1
                logger.info(f"No new results found (attempt {no_new_results_count}/3)")
                
                if no_new_results_count >= 3:
                    logger.warning(f"No new results after {no_new_results_count} attempts, stopping")
                    # Screenshot debug เพื่อดูว่า TikTok แสดงอะไร
                    try:
                        debug_path = f"/app/logs/debug_no_results_{keyword.replace(' ', '_')}.png"
                        await self.page.screenshot(path=debug_path, full_page=True)
                        logger.info(f"Debug screenshot saved: {debug_path}")
                    except Exception as ss_err:
                        logger.warning(f"Could not save debug screenshot: {ss_err}")
                    break
            else:
                no_new_results_count = 0
                logger.info(f"Found {current_count}/{max_results} videos")
            
            # ใช้ scroll แบบง่ายๆ เหมือน test.py
            await self.page.evaluate('window.scrollBy(0, window.innerHeight * 1.5)')
            await self.page.wait_for_timeout(3000)
            scroll_count += 1
        
        video_list = list(video_urls)[:max_results]
        logger.info(f"Found {len(video_list)} video URLs for keyword: '{keyword}'")
        
        # Debug: แสดง URL 3 อันแรก
        if video_list and debug:
            logger.info("Sample URLs:")
            for i, url in enumerate(video_list[:3], 1):
                logger.info(f"  {i}. {url}")
        
        return video_list

    async def extract_from_json_robust(self, video_url: str) -> Dict[str, Any]:
        import re
        video_id_match = re.search(r'/video/(\d+)', video_url)
        video_id = video_id_match.group(1) if video_id_match else None
        
        data = {
            'url': video_url, 
            'scraped_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'description': '', 
            'author': '', 
            'author_nickname': '', 
            'views': '0',
            'likes': '0', 
            'comments': '0', 
            'post_date': '', 
            'hashtags': []
        }
        
        def safe_get(obj, path, default=None):
            keys = path.split('.')
            current = obj
            for key in keys:
                if isinstance(current, dict):
                    current = current.get(key)
                else:
                    return default
                if current is None:
                    return default
            return current if current is not None else default
        
        def validate_string(value, min_length=1):
            if not value or not isinstance(value, str):
                return None
            value = str(value).strip()
            return value if len(value) >= min_length else None
        
        def validate_number(value):
            if not value: return '0'
            if isinstance(value, (int, float)): return str(int(value))
            if isinstance(value, str):
                cleaned = value.replace(',', '').replace(' ', '').upper()
                if 'K' in cleaned:
                    try: return str(int(float(cleaned.replace('K', '')) * 1000))
                    except: pass
                elif 'M' in cleaned:
                    try: return str(int(float(cleaned.replace('M', '')) * 1000000))
                    except: pass
                try: return str(int(float(cleaned)))
                except: pass
            return '0'
        
        try:
            json_result = await self.page.evaluate("""
                () => {
                    const result = {source: null, raw_data: null};
                    try {
                        const el = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
                        if (el) {
                            const univ = JSON.parse(el.textContent);
                            const item = univ.__DEFAULT_SCOPE__?.['webapp.video-detail']?.itemInfo?.itemStruct;
                            if (item) {
                                result.source = 'UNIVERSAL_DATA';
                                result.raw_data = {
                                    desc: item.desc,
                                    author: item.author?.uniqueId,
                                    nickname: item.author?.nickname,
                                    views: item.stats?.playCount,
                                    likes: item.stats?.diggCount,
                                    comments: item.stats?.commentCount,
                                    createTime: item.createTime,
                                    challenges: (item.challenges || []).map(c => c.title)
                                };
                                return result;
                            }
                        }
                    } catch (e) {}
                    return result;
                }
            """)
            
            if json_result and json_result.get('raw_data'):
                raw = json_result['raw_data']
                data['description'] = validate_string(raw.get('desc'), 2) or ""
                data['author'] = validate_string(raw.get('author')) or ""
                data['author_nickname'] = validate_string(raw.get('nickname')) or ""
                data['views'] = validate_number(raw.get('views'))
                data['likes'] = validate_number(raw.get('likes'))
                data['comments'] = validate_number(raw.get('comments'))
                
                ts = raw.get('createTime')
                if ts:
                    try:
                        utc_time = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                        thai_time = utc_time.astimezone(pytz.timezone('Asia/Bangkok'))
                        data['post_date'] = thai_time.strftime('%Y-%m-%d %H:%M')
                    except: 
                        pass
                data['hashtags'] = raw.get('challenges', [])
        except Exception as e:
            logger.error(f"JSON extraction error for {video_url}: {e}")
        
        return data

    def parse_with_bs4_fallback(self, html_content: str, video_url: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html_content, 'html.parser')
        data = {
            'url': video_url, 
            'scraped_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'description': '', 
            'author': '', 
            'author_nickname': '', 
            'views': '0',
            'likes': '0', 
            'comments': '0', 
            'post_date': '', 
            'hashtags': []
        }
        desc_elem = soup.select_one('h1[data-e2e="browse-video-desc"]')
        if desc_elem: 
            data['description'] = desc_elem.get_text(strip=True)
        author_elem = soup.select_one('[data-e2e="browse-username"]')
        if author_elem: 
            data['author'] = author_elem.get_text(strip=True).replace('@', '')
        return data

    async def quick_check_video_date(self, video_url: str) -> tuple[bool, str]:
        """
        เช็ควันที่วิดีโอแบบเร็ว โดยไม่ scrape รายละเอียดทั้งหมด
        Returns: (is_recent: bool, post_date: str)
        """
        try:
            await self.page.goto(video_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)  # รอแค่ 2 วินาที (เร็วกว่า full scrape)
            
            # ดึงแค่ createTime จาก JSON
            result = await self.page.evaluate("""
                () => {
                    try {
                        const el = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
                        if (el) {
                            const univ = JSON.parse(el.textContent);
                            const item = univ.__DEFAULT_SCOPE__?.['webapp.video-detail']?.itemInfo?.itemStruct;
                            if (item && item.createTime) {
                                return { createTime: item.createTime };
                            }
                        }
                    } catch (e) {}
                    return null;
                }
            """)
            
            if result and result.get('createTime'):
                create_time = result['createTime']
                post_date = datetime.fromtimestamp(int(create_time)).strftime('%Y-%m-%d %H:%M')
                is_recent = is_video_within_days(post_date, settings.max_days_old)
                return (is_recent, post_date)
            
            return (True, '')  # ถ้าหาไม่เจอ ให้ผ่าน
        except Exception as e:
            logger.warning(f"Quick date check failed for {video_url}: {e}")
            return (True, '')  # ถ้า error ให้ผ่าน

    async def scrape_video_text_data(self, video_url):
        if not self.page: 
            raise RuntimeError("Browser not initialized.")
        
        logger.info(f"Scraping: {video_url}")
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                #  ตรวจสอบว่า browser ยังเปิดอยู่หรือไม่
                if self.page.is_closed():
                    logger.warning(f"Browser closed, aborting scrape: {video_url}")
                    return {
                        'url': video_url,
                        'error': 'Browser closed during scraping',
                        'status': 'aborted',
                        'scraped_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }
                
                await self.page.goto(video_url, wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(4)
                
                data = await self.extract_from_json_robust(video_url)
                
                if not data.get('description') or not data.get('author'):
                    meta_data = await self.page.evaluate("""
                        () => ({
                            desc: document.querySelector('meta[property="og:description"]')?.content,
                            author: document.querySelector('meta[property="og:title"]')?.content?.split(' | ')[0]
                        })
                    """)
                    if not data.get('description'): 
                        data['description'] = meta_data.get('desc', '')
                    if not data.get('author'): 
                        data['author'] = meta_data.get('author', '')

                if not data.get('description') or not data.get('author'):
                    html_content = await self.page.content()
                    bs4_data = self.parse_with_bs4_fallback(html_content, video_url)
                    for key in ['description', 'author']:
                        if not data.get(key) and bs4_data.get(key): 
                            data[key] = bs4_data[key]
                
                success = data.get('description') or data.get('author')
                if success:
                    logger.info(f"Successfully scraped: {video_url}")
                    return data
                else:
                    logger.warning(f"No data extracted from {video_url}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    return data
                    
            except Exception as e:
                #  ตรวจสอบว่าเป็น error จากการปิด browser หรือไม่
                error_msg = str(e).lower()
                if 'closed' in error_msg or 'target' in error_msg:
                    logger.warning(f"Browser closed during scraping: {video_url}")
                    return {
                        'url': video_url,
                        'error': 'Browser closed',
                        'status': 'aborted',
                        'scraped_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }
                
                logger.error(f"Scrape attempt {attempt + 1} failed for {video_url}: {e}")
                if attempt == max_retries - 1:
                    return {
                        'url': video_url,
                        'error': str(e),
                        'status': 'failed',
                        'scraped_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }
                await asyncio.sleep(2 ** attempt)

    async def close(self):
        try:
            if self.page:
                await self.page.close()
                self.page = None
            if self.context:
                await self.context.close()
                self.context = None
            if self.browser:
                await self.browser.close()
                self.browser = None
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
            logger.info("Browser closed successfully")
        except Exception as e:
            logger.error(f"Error closing browser: {e}", exc_info=True)

# ================== USER SESSION ==================
class UserSession:
    def __init__(self, username_full: str):
        self.username_full = username_full
        self.username_safe = to_safe_name(username_full)
        d = user_dirs(self.username_safe)
        self.browser_data_dir = d["profile_dir"]
        self.scraper: Optional[TikTokScraper] = None
        self.lock = asyncio.Lock()
        self.last_used = time.monotonic()
        self.closed = False
        self.stop_flag = False

    def touch(self): 
        self.last_used = time.monotonic()

    def should_stop(self) -> bool:
        """ตรวจสอบว่าควรหยุดทำงานหรือไม่"""
        return getattr(self, '_stop_flag', False) or self.closed

    async def cleanup(self, force_close: bool = False):
        try:
            if self.scraper:
                if force_close:
                    # บังคับปิดทุกอย่างให้แน่ใจ
                    if self.scraper.page:
                        try:
                            await self.scraper.page.close()
                        except:
                            pass
                    if self.scraper.context:
                        try:
                            await self.scraper.context.close()
                        except:
                            pass
                    if self.scraper.browser:
                        try:
                            await self.scraper.browser.close()
                        except:
                            pass
                    if self.scraper.playwright:
                        try:
                            await self.scraper.playwright.stop()
                        except:
                            pass
                await self.scraper.close()
        except Exception as e:
            logger.error(f"Cleanup error for {self.username_safe}: {e}")
        finally: 
            self.scraper = None
            # รอให้ OS ปล่อย file handles
            if force_close:
                await asyncio.sleep(1)

    async def ensure_scraper(self, headless: bool = True) -> TikTokScraper:
        if self.closed: 
            raise RuntimeError("Session closed")
        if self.scraper is None or self.scraper.headless != headless:
            await self.cleanup()
            self.scraper = TikTokScraper(self.browser_data_dir, headless=headless)
        await self.scraper.init_browser()
        return self.scraper

    async def close(self, force: bool = False):
        if self.closed: 
            return
        self._stop_flag = True  # ส่งสัญญาณหยุดก่อน
        await asyncio.sleep(0.5)  # รอให้ task อ่าน flag
        await self.cleanup(force_close=force)
        self.closed = True
        logger.info(f"Session closed for {self.username_safe}")

# ================== SESSION MANAGER ==================
class SessionManager:
    def __init__(self, idle_timeout_sec: int = None):
        self.idle_timeout_sec = idle_timeout_sec or settings.api_idle_timeout_sec
        self.sessions: Dict[str, UserSession] = {}
        self._janitor_task: Optional[asyncio.Task] = None
        self._stop_evt = asyncio.Event()

    def get_or_create(self, username_full: str) -> UserSession:
        sess = self.sessions.get(username_full)
        if not sess or sess.closed:
            sess = UserSession(username_full)
            self.sessions[username_full] = sess
            logger.info(f"Created new session for {username_full}")
        return sess

    async def run_for_user(self, username_full: str, coro_func):
        sess = self.get_or_create(username_full)
        async with sess.lock:
            sess.touch()
            try: 
                return await coro_func(sess)
            finally: 
                sess.touch()

    async def janitor_loop(self):
        while not self._stop_evt.is_set():
            try:
                now = time.monotonic()
                for uname, sess in list(self.sessions.items()):
                    idle_time = now - sess.last_used
                    remaining = self.idle_timeout_sec - idle_time
                    
                    # Debug log ทุก 30 วินาที
                    if int(idle_time) % 30 == 0 and int(idle_time) > 0:
                        logger.info(f"[DEBUG] Session '{uname}': idle {idle_time:.0f}s, timeout in {remaining:.0f}s")
                    
                    if not sess.lock.locked() and idle_time > self.idle_timeout_sec:
                        logger.info(f"[TIMEOUT] Closing browser for '{uname}' after {idle_time:.0f}s idle")
                        await sess.close()
                        self.sessions.pop(uname, None)
                        logger.info(f"[TIMEOUT] Browser closed for '{uname}'")
                await asyncio.sleep(10.0)
            except Exception as e:
                logger.error(f"Janitor loop error: {e}", exc_info=True)

    def start(self):
        if self._janitor_task is None: 
            self._janitor_task = asyncio.create_task(self.janitor_loop())
            logger.info("Session manager started")

    async def stop(self):
        self._stop_evt.set()
        if self._janitor_task: 
            self._janitor_task.cancel()
        for sess in list(self.sessions.values()): 
            await sess.close()
        logger.info("Session manager stopped")

# ================== FASTAPI APP ==================
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.tiktok_manager = SessionManager()
    app.state.tiktok_manager.start()
    logger.info("Application started")
    yield
    await app.state.tiktok_manager.stop()
    logger.info("Application shutdown")

# ================== EXPORT FUNCTIONS FOR MAIN.PY ==================
async def tiktok_manager_start(app):
    """Start TikTok session manager - called from main.py lifespan"""
    app.state.tiktok_manager = SessionManager()
    app.state.tiktok_manager.start()
    logger.info("TikTok session manager started")

async def tiktok_manager_stop(app):
    """Stop TikTok session manager - called from main.py lifespan"""
    if hasattr(app.state, 'tiktok_manager'):
        await app.state.tiktok_manager.stop()
        logger.info("TikTok session manager stopped")

def get_manager(request: Request) -> "SessionManager":
    return request.app.state.tiktok_manager

# ================== API ROUTER ==================
tiktok_router = APIRouter()

class LoginResponse(BaseModel):
    status: Literal[
        "success",
        "waiting_for_login",
        "logged_in",
        "failed"
    ]
    username_safe: str
    profile_dir: str
    message: str
    is_logged_in: bool

# Rate Limiting
#limiter = Limiter(key_func=get_remote_address)
#app.state.limiter = limiter
#app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Security
# security = HTTPBearer()

# async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
#     if credentials.credentials != settings.api_token:
#         logger.warning(f"Invalid token attempt: {credentials.credentials[:10]}...")
#         raise HTTPException(status_code=403, detail="Invalid authentication token")
#     return credentials.credentials

# ================== PYDANTIC MODELS ==================
class ProfileInfo(BaseModel):
    username: str
    profile_dir: str
    exists: bool
    size_mb: float
    is_active: bool

class ProfilesListResponse(BaseModel):
    total_count: int
    profiles: List[ProfileInfo]

class ScrapeRequest(BaseModel):
    username: str
    keyword: Optional[str] = None  # รองรับ keyword เดียว (backward compatible)
    keywords: Optional[List[str]] = None  # รองรับหลาย keywords
    max_results: int = 10  # จำนวนต่อ keyword
    headless: bool = True
    # max_days_old: int = 15
    
    @field_validator('username')
    @classmethod
    def validate_username(cls, v):
        if not v or len(v) < 3:
            raise ValueError('Username must be at least 3 characters')
        if len(v) > 100:
            raise ValueError('Username too long')
        return v
    
    @field_validator('keyword')
    @classmethod
    def validate_keyword(cls, v):
        if v is not None:
            if len(v) < 1:
                raise ValueError('Keyword cannot be empty')
            if len(v) > 200:
                raise ValueError('Keyword too long')
        return v
    
    @field_validator('keywords')
    @classmethod
    def validate_keywords(cls, v):
        if v is not None:
            if not isinstance(v, list):
                raise ValueError('keywords must be a list')
            if len(v) == 0:
                raise ValueError('keywords list cannot be empty')
            if len(v) > 10:
                raise ValueError('Cannot search more than 10 keywords at once')
            for kw in v:
                if not kw or len(kw) < 1:
                    raise ValueError('Each keyword cannot be empty')
                if len(kw) > 200:
                    raise ValueError('Keyword too long')
        return v
    
    @field_validator('max_results')
    @classmethod
    def validate_max_results(cls, v):
        if v < 1:
            raise ValueError('max_results must be at least 1')
        if v > settings.max_scrape_results:
            raise ValueError(f'max_results cannot exceed {settings.max_scrape_results}')
        return v
    
    def get_keywords(self) -> List[str]:
        """รวม keyword เดียว และ keywords list เข้าด้วยกัน"""
        result = []
        if self.keyword:
            result.append(self.keyword)
        if self.keywords:
            result.extend(self.keywords)
        # ลบ duplicate และ return
        return list(dict.fromkeys(result))  # preserve order, remove duplicates


class LoginRequest(BaseModel):
    username: str
    headless: bool = False
    
    @field_validator('username')
    @classmethod
    def validate_username(cls, v):
        if not v or len(v) < 3:
            raise ValueError('Username must be at least 3 characters')
        return v

class ScrapeWebhookRequest(BaseModel):
    """Request model for webhook-based scraping"""
    username: str
    keyword: Optional[str] = None
    keywords: Optional[List[str]] = None
    max_results: int = 10
    headless: bool = True
    webhook_url: str = Field(..., description="URL ที่ระบบจะ POST ผลลัพธ์ไปเมื่อทำงานเสร็จ")
    
    @field_validator('username')
    @classmethod
    def validate_username(cls, v):
        if not v or len(v) < 3:
            raise ValueError('Username must be at least 3 characters')
        return v
    
    @field_validator('webhook_url')
    @classmethod
    def validate_webhook_url(cls, v):
        if not v or not v.strip():
            raise ValueError('webhook_url is required')
        return v.strip()
    
    def get_keywords(self) -> List[str]:
        """รวม keyword เดียว และ keywords list เข้าด้วยกัน"""
        result = []
        if self.keyword:
            result.append(self.keyword)
        if self.keywords:
            result.extend(self.keywords)
        return list(dict.fromkeys(result))

class WebhookQueuedResponse(BaseModel):
    """Response model when webhook job is queued"""
    ok: bool = Field(..., description="สถานะการรับงาน")
    task_id: str = Field(..., description="รหัสงาน")
    username_safe: str = Field(..., description="ชื่อผู้ใช้ (Sanitized)")
    status: Literal["queued", "running"] = Field(..., description="สถานะงาน")
    queue_position: int = Field(..., description="ลำดับคิว")
    message: str = Field(..., description="ข้อความสถานะ")
    webhook_url: str = Field(..., description="URL ที่จะส่งผลลัพธ์ไป")

# ================== API ENDPOINTS ==================


@tiktok_router.post("/create-profile-and-login", response_model=LoginResponse, tags=["TikTok Profiles"] ,summary="สร้างโปรไฟล์ และ Login Tiktok")
async def create_profile(req: LoginRequest, manager: SessionManager = Depends(get_manager)):
    """
    - ใส่ username ของท่าน แล้วกด execute หลังจากหน้าต่างเด้งขึ้นมาให้กดไป
    - ที่ qr scan แล้วทำการเปิดแอพ tiktok เลือกไปที่หัวข้อ scan qr code จากมือถือของท่าน
    - แล้วสแกน qr code หลังจาก scan แล้วให้รอจนกว่าจะมีเครื่องหมายติ๊กถูกสีเขียวบน qr
    - เมื่อเสร็จแล้วก็ให้เรียก /login-confrim เพื่อบันทึก session
    """
    async def _job(sess: UserSession):
        # เปิด browser แบบ NON-headless
        scraper = await sess.ensure_scraper(headless=False)
        
        # เช็คว่า login อยู่แล้วหรือยัง
        is_logged_in = await scraper.check_login_status()
        if is_logged_in:
            # ปิด browser เพราะ login อยู่แล้ว
            logger.info("🔒 Already logged in, closing browser...")
            await sess.cleanup()
            
            return {
                "status": "logged_in",
                "username_safe": sess.username_safe,
                "profile_dir": sess.browser_data_dir,
                "message": "Login อยู่แล้ว พร้อมใช้งาน /scrape-videos",
                "is_logged_in": True,
            }

        # เปิดหน้า login (QR Code)
        logger.info("🔓 Opening TikTok QR login page...")
        await scraper.page.goto(
            "https://www.tiktok.com/login/qrcode",
            wait_until="domcontentloaded",
            timeout=30000
        )
        
        # รอให้ QR Code โหลดเสร็จ
        try:
            await scraper.page.wait_for_selector(
                'img[alt*="QR"], canvas, [class*="qrcode"]',
                timeout=10000
            )
            logger.info("QR Code loaded")
        except:
            logger.warning("QR Code selector not found, but page loaded")
        
        # รอให้ redirect หลังสแกน QR (timeout 5 นาที)
        logger.info("Waiting for QR scan and redirect...")
        try:
            await scraper.page.wait_for_url(
                lambda url: '/login' not in url,  # รอจน URL ไม่มี /login
                timeout=300000  # 5 นาที
            )
            logger.info("Login redirect detected!")
            
            # รอให้หน้าโหลดเสร็จ
            await scraper.page.wait_for_timeout(5000)
            
            # เช็คอีกครั้งว่า login สำเร็จหรือไม่
            is_logged_in = await scraper.check_login_status()
            
            if is_logged_in:
                # save session ทันที
                await scraper.save_session()
                
                # ปิด browser หลัง login สำเร็จ (ประหยัด RAM)
                logger.info("🔒 Closing browser after successful login...")
                await sess.cleanup()
                
                return {
                    "status": "success",
                    "username_safe": sess.username_safe,
                    "profile_dir": sess.browser_data_dir,
                    "message": "Login สำเร็จ! Session ถูก save และปิด browser แล้ว พร้อมใช้งาน /scrape-videos",
                    "is_logged_in": True,
                }
            else:
                return {
                    "status": "waiting_for_login",
                    "username_safe": sess.username_safe,
                    "profile_dir": sess.browser_data_dir,
                    "message": "Redirect แล้วแต่ยังไม่พบ login session ให้เรียก /check-login-status หรือ /login-confirm",
                    "is_logged_in": False,
                }
                
        except asyncio.TimeoutError:
            # timeout = ยังไม่ได้สแกน QR
            return {
                "status": "waiting_for_login",
                "username_safe": sess.username_safe,
                "profile_dir": sess.browser_data_dir,
                "message": "รอสแกน QR Code ใน Chrome (timeout 5 นาที) ให้เรียก /check-login-status เพื่อเช็คสถานะ",
                "is_logged_in": False,
            }

    return await manager.run_for_user(req.username, _job)


@tiktok_router.post("/login-confirm", response_model=LoginResponse, tags=["TikTok Profiles"],summary="เรียกหลังจาก Login เสร็จแล้ว เพื่อ save session และเปลี่ยนเป็น headless")
async def login_confirm(req: LoginRequest, manager: SessionManager = Depends(get_manager)):
    """
    - เรียกหลังจาก Login เสร็จแล้ว เพื่อ save session และเปลี่ยนเป็น headless
    """
    async def _job(sess: UserSession):
        scraper = sess.scraper
        if not scraper or not scraper.page:
            raise HTTPException(400, "ยังไม่ได้เปิด browser - กรุณาเรียก /create-profile ก่อน")

        # เช็คว่า login แล้วหรือยัง
        is_logged_in = await scraper.check_login_status()
        if not is_logged_in:
            return {
                "status": "failed",
                "username_safe": sess.username_safe,
                "profile_dir": sess.browser_data_dir,
                "message": "ยังไม่ได้ login - กรุณา Login ให้เสร็จก่อน",
                "is_logged_in": False,
            }

        # Login สำเร็จ → save session
        logger.info("💾 Saving session...")
        await scraper.save_session()

        # ปิดแล้วเปิดใหม่เป็น headless
        logger.info("🔄 Restarting browser in headless mode...")
        await sess.cleanup()
        await sess.ensure_scraper(headless=True)

        return {
            "status": "success",
            "username_safe": sess.username_safe,
            "profile_dir": sess.browser_data_dir,
            "message": "Login สำเร็จ! พร้อมใช้งานในโหมด Headless",
            "is_logged_in": True,
        }

    return await manager.run_for_user(req.username, _job)

@tiktok_router.get("/check-login-status/{username}", tags=["TikTok Profiles"])
async def check_login_status_endpoint(
    username: str,
    manager: SessionManager = Depends(get_manager)
):
    """
    ตรวจสอบสถานะ Login แบบ Real-time (เรียกได้ขณะที่กำลังรอ Login)
    """
    username_full = username.strip()
    
    async def _job(sess: UserSession):
        if sess.scraper is None:
            return {
                "status": "no_browser",
                "message": "ยังไม่ได้เปิด Browser - กรุณาเรียก /create-profile ก่อน",
                "is_logged_in": False
            }
        
        try:
            is_logged_in = await sess.scraper.check_login_status()
            
            # ข้อมูลเพิ่มเติมเพื่อ debug
            cookies = await sess.scraper.context.cookies() if sess.scraper.context else []
            session_cookies = [c for c in cookies if 'sessionid' in c['name'].lower()]
            
            return {
                "status": "checked",
                "is_logged_in": is_logged_in,
                "total_cookies": len(cookies),
                "session_cookies_count": len(session_cookies),
                "message": "Login แล้ว - พร้อม confirm" if is_logged_in else "รอ Login ในหน้า Chrome"
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"เกิดข้อผิดพลาด: {str(e)}",
                "is_logged_in": False
            }
    
    try:
        return await manager.run_for_user(username_full, _job)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@tiktok_router.post("/scrape-videos",
          tags=["TikTok Scraping"], summary="ดึงข้อมูลจากวิดิโอบน Tiktok")
#@limiter.limit(settings.rate_limit)
async def scrape_videos(
    request: Request,
    req: ScrapeRequest,
    # token: str = Depends(verify_token)
):
    """
    ดึงข้อมูลวิดีโอ TikTok ตาม keyword
    
    - ใส่ `keyword` สำหรับค้นหา 1 คำ หรือ `keywords` สำหรับค้นหาหลายคำ
    - ใส่ `max_results` จำนวนวิดีโอต่อ keyword
    - Response จะมี field `keyword` ใน data เพื่อบอกว่าวิดีโอมาจากคำค้นหาไหน
    
    ตัวอย่าง:
    ```json
    {"username": "bon", "keywords": ["isuzu", "toyota"], "max_results": 10}
    ```
    """
    # ดึง keywords ทั้งหมด (รวม keyword เดียว + keywords list)
    keywords = req.get_keywords()
    if not keywords:
        raise HTTPException(status_code=400, detail="Please provide 'keyword' or 'keywords'")
    
    async def _job(sess: UserSession):
        scraper = await sess.ensure_scraper(headless=req.headless)
        
        # เรียก check_login_status เสมอ (ไป tiktok.com ก่อน) เหมือน test.py
        # เพื่อให้ behavior ดูเป็นธรรมชาติ ไม่ถูกจับ CAPTCHA
        try:
            is_logged_in = await scraper.check_login_status()
            if not is_logged_in:
                raise HTTPException(
                    status_code=401, 
                    detail="Not logged in. Please create profile and login first."
                )
            logger.info("Login verified, proceeding to scrape")
                
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error checking login: {e}")
            raise HTTPException(status_code=401, detail=f"Login check failed: {str(e)}")
        
        # โหลด URLs ที่เคย scrape แล้ว (สำหรับ dedup)
        scraped_urls = load_scraped_urls() if settings.enable_dedup else set()
        if settings.enable_dedup:
            logger.info(f"Loaded {len(scraped_urls)} previously scraped URLs for dedup")
        
        all_data = []
        total_found = 0
        skipped_dedup = 0
        skipped_old = 0
        keyword_stats = {}
        
        # วนลูปตาม keywords ทั้งหมด
        for kw_index, current_keyword in enumerate(keywords, 1):
            logger.info(f"=== Searching keyword {kw_index}/{len(keywords)}: '{current_keyword}' ===")
            
            # ค้นหา URLs สำหรับ keyword นี้
            urls = await scraper.search_and_get_video_urls(current_keyword, req.max_results)
            total_found += len(urls)
            keyword_stats[current_keyword] = {"found": len(urls), "scraped": 0}
            
            for i, url in enumerate(urls, 1):
                # ตรวจสอบว่าควรหยุดหรือไม่
                if sess.should_stop():
                    logger.warning(f"Scraping stopped by user at keyword '{current_keyword}' video {i}/{len(urls)}")
                    return {
                        "status": "aborted",
                        "message": "Scraping stopped by user",
                        "data": all_data,
                        "keywords_searched": keywords[:kw_index],
                        "total_found": total_found,
                        "total_scraped": len(all_data),
                        "skipped_duplicate": skipped_dedup,
                        "skipped_old": skipped_old,
                        "keyword_stats": keyword_stats
                    }
                
                # ตรวจสอบว่า URL นี้ถูก scrape ไปแล้วหรือยัง
                if settings.enable_dedup and url in scraped_urls:
                    logger.info(f"[{current_keyword}] Skipping duplicate URL ({i}/{len(urls)}): {url}")
                    skipped_dedup += 1
                    continue
                
                # เพิ่ม URL ที่กำลังจะ scrape เข้า set เพื่อป้องกัน duplicate ข้าม keywords
                scraped_urls.add(url)
                
                logger.info(f"[{current_keyword}] Scraping video {i}/{len(urls)}")
                # เพิ่ม delay ให้ดูเหมือนคนจริง (5-10 วินาที)
                await asyncio.sleep(random.uniform(5, 10))
                
                try:
                    # เช็ควันที่ก่อน (เร็ว ~2 วินาที)
                    if settings.max_days_old > 0:
                        is_recent, post_date = await scraper.quick_check_video_date(url)
                        if not is_recent:
                            logger.info(f"[{current_keyword}] Skipping old video ({post_date}): {url}")
                            skipped_old += 1
                            continue
                    
                    # ถ้าวันที่ผ่าน ค่อย scrape รายละเอียด
                    data = await scraper.extract_from_json_robust(url)
                    
                    if data.get('status') == 'aborted':
                        logger.warning(f"Browser closed, stopping scrape")
                        break
                    
                    if data.get('author') or data.get('description'):
                        # เพิ่ม keyword ที่ใช้ค้นหาลงใน data
                        data['keyword'] = current_keyword
                        all_data.append(data)
                        keyword_stats[current_keyword]["scraped"] += 1
                        logger.info(f"[{current_keyword}] Successfully scraped: {url}")
                        # บันทึก URL ที่ scrape สำเร็จ
                        if settings.enable_dedup:
                            save_scraped_url(url)
                except Exception as e:
                    logger.error(f"[{current_keyword}] Error scraping {url}: {e}")
                    if 'closed' in str(e).lower():
                        logger.warning("Browser closed, stopping scrape")
                        break
            
            # พักระหว่าง keywords (ลดโอกาสถูก rate limit)
            if kw_index < len(keywords):
                delay = random.uniform(15, 30)
                logger.info(f"Waiting {delay:.1f}s before next keyword...")
                await asyncio.sleep(delay)
        
        logger.info(f"Scrape completed: {len(all_data)}/{total_found} videos from {len(keywords)} keywords")
        return {
            "status": "success", 
            "data": all_data,
            "keywords_searched": keywords,
            "total_found": total_found,
            "total_scraped": len(all_data),
            "skipped_duplicate": skipped_dedup,
            "skipped_old": skipped_old,
            "keyword_stats": keyword_stats
        }
    
    return await request.app.state.tiktok_manager.run_for_user(req.username, _job)

# ================== WEBHOOK HELPER ==================
async def _post_webhook(url: str, payload: Dict[str, Any], timeout_sec: int = 15):
    """POST ผลลัพธ์ไปยัง webhook_url"""
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.post(url, json=payload)
            logger.info(f"Webhook POST => {url} status={resp.status_code}")
    except Exception as e:
        logger.warning(f"Webhook POST failed: {e}")

def make_task_id() -> str:
    """สร้าง task ID แบบ unique"""
    import uuid
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]

@tiktok_router.post("/scrape-videos-webhook",
    response_model=WebhookQueuedResponse,
    tags=["TikTok Scraping"],
    summary="ดึงข้อมูลวิดีโอ TikTok (โหมด Webhook)",
    description="รับงานเข้าคิวและทำงานเบื้องหลัง เมื่อเสร็จจะ POST ผลลัพธ์ไปยัง webhook_url ที่ระบุ"
)
async def scrape_videos_webhook(
    request: Request,
    req: ScrapeWebhookRequest
):
    """
    ดึงข้อมูลวิดีโอ TikTok แบบ Background (Async)
    - ตอบกลับทันทีว่า "รับงานแล้ว"
    - ทำงานเบื้องหลังและ POST ผลลัพธ์ไปยัง webhook_url เมื่อเสร็จ
    """
    manager = request.app.state.tiktok_manager
    
    # Validate keywords
    keywords = req.get_keywords()
    if not keywords:
        raise HTTPException(status_code=400, detail="Please provide 'keyword' or 'keywords'")
    
    username_safe = to_safe_name(req.username)
    task_id = make_task_id()
    webhook_url = req.webhook_url
    
    # Check queue position
    sess = manager.get_or_create(req.username)
    running = sess.lock.locked()
    position = 1 if not running else 2
    
    async def _bg_job(sess: UserSession):
        """Background job to scrape and POST result to webhook"""
        try:
            scraper = await sess.ensure_scraper(headless=req.headless)
            
            # Check login
            try:
                is_logged_in = await scraper.check_login_status()
                if not is_logged_in:
                    await _post_webhook(webhook_url, {
                        "ok": False,
                        "task_id": task_id,
                        "username_safe": sess.username_safe,
                        "error": "Not logged in. Please create profile and login first."
                    })
                    return
            except Exception as e:
                await _post_webhook(webhook_url, {
                    "ok": False,
                    "task_id": task_id,
                    "username_safe": sess.username_safe,
                    "error": f"Login check failed: {str(e)}"
                })
                return
            
            # Load dedup URLs
            scraped_urls = load_scraped_urls() if settings.enable_dedup else set()
            logger.info(f"[Webhook][{task_id}] Loaded {len(scraped_urls)} URLs for dedup check")
            
            all_data = []
            total_found = 0
            skipped_dedup = 0
            skipped_old = 0
            keyword_stats = {}
            
            # Scrape each keyword
            for kw_index, current_keyword in enumerate(keywords, 1):
                logger.info(f"[Webhook][{task_id}] Searching keyword {kw_index}/{len(keywords)}: '{current_keyword}'")
                
                urls = await scraper.search_and_get_video_urls(current_keyword, req.max_results)
                total_found += len(urls)
                keyword_stats[current_keyword] = {"found": len(urls), "scraped": 0}
                logger.info(f"[Webhook][{task_id}] Found {len(urls)} video URLs for '{current_keyword}'")
                
                for i, url in enumerate(urls, 1):
                    if sess.should_stop():
                        logger.warning(f"[Webhook] Scraping stopped at keyword '{current_keyword}'")
                        break
                    
                    # Dedup check
                    if settings.enable_dedup and url in scraped_urls:
                        skipped_dedup += 1
                        logger.info(f"[Webhook][{task_id}] SKIP duplicate ({i}/{len(urls)}): {url[-30:]}")
                        continue
                    
                    scraped_urls.add(url)
                    logger.info(f"[Webhook][{current_keyword}] Scraping video {i}/{len(urls)}")
                    await asyncio.sleep(random.uniform(5, 10))
                    
                    try:
                        # Quick date check
                        if settings.max_days_old > 0:
                            is_recent, post_date = await scraper.quick_check_video_date(url)
                            if not is_recent:
                                skipped_old += 1
                                logger.info(f"[Webhook][{task_id}] SKIP old video ({post_date}): {url[-30:]}")
                                continue
                        
                        # Scrape video data
                        data = await scraper.extract_from_json_robust(url)
                        
                        if data.get('status') == 'aborted':
                            break
                        
                        if data.get('author') or data.get('description'):
                            data['keyword'] = current_keyword
                            all_data.append(data)
                            keyword_stats[current_keyword]["scraped"] += 1
                            logger.info(f"[Webhook][{task_id}] OK scraped: @{data.get('author', 'unknown')} - {data.get('description', '')[:50]}")
                            
                            if settings.enable_dedup:
                                save_scraped_url(url)
                    except Exception as e:
                        logger.error(f"[Webhook] Error scraping {url}: {e}")
                        if 'closed' in str(e).lower():
                            break
                
                # Delay between keywords
                if kw_index < len(keywords):
                    await asyncio.sleep(random.uniform(15, 30))
            
            # POST success result to webhook
            logger.info(f"[Webhook][{task_id}] DONE: {len(all_data)} scraped, {skipped_dedup} skipped (dup), {skipped_old} skipped (old)")
            await _post_webhook(webhook_url, {
                "ok": True,
                "task_id": task_id,
                "username_safe": sess.username_safe,
                "status": "success",
                "data": all_data,
                "keywords_searched": keywords,
                "total_found": total_found,
                "total_scraped": len(all_data),
                "skipped_duplicate": skipped_dedup,
                "skipped_old": skipped_old,
                "keyword_stats": keyword_stats
            })
            
        except Exception as e:
            logger.error(f"[Webhook] Background job error: {e}")
            await _post_webhook(webhook_url, {
                "ok": False,
                "task_id": task_id,
                "username_safe": username_safe,
                "error": str(e)
            })
    
    async def _schedule():
        try:
            await manager.run_for_user(req.username, _bg_job)
        except Exception as e:
            logger.error(f"[Webhook] Schedule error: {e}")
    
    # Start background task
    asyncio.create_task(_schedule())
    
    return {
        "ok": True,
        "task_id": task_id,
        "username_safe": username_safe,
        "status": "queued" if position > 1 else "running",
        "queue_position": position,
        "message": f"งานถูกเข้าคิว ลำดับ #{position}" if position > 1 else "เริ่มงานดึงข้อมูลแล้ว จะส่งผลลัพธ์ไปยัง webhook_url เมื่อเสร็จ",
        "webhook_url": webhook_url
    }

@tiktok_router.post("/warm-session",
          tags=["TikTok Scraping"],summary="ให้ session ร้อนเพื่อให้ไม่ถูก block")
#@limiter.limit("5/minute")
async def warm_session(
    request: Request,
    username: str,
    duration: int = 2,
    # token: str = Depends(verify_token)
):
    if duration < 1 or duration > 10:
        raise HTTPException(status_code=400, detail="Duration must be between 1-10 minutes")
    
    async def _job(sess: UserSession):
        scraper = await sess.ensure_scraper(headless=False)
        browser_pid = getattr(scraper, 'browser_pid', None)
        warmer = SessionWarmer(scraper.page, browser_pid=browser_pid)
        return await warmer.warm_session(duration)
    
    return await request.app.state.tiktok_manager.run_for_user(username, _job)

@tiktok_router.get("/snapshot/{username}",
         tags=["TikTok Snapshot"],
         summary="ถ่ายภาพหน้าจอ browser ของ user เพื่อดูว่าบอททำงานอยู่ไหม",
         responses={
             200: {"content": {"image/png": {}}},
             404: {"description": "Session not found"},
             400: {"description": "Browser not running"},
             408: {"description": "Session timeout"}
         })
async def take_snapshot(username: str, request: Request):
    """
    ถ่ายภาพหน้าจอ browser ของ user ที่กำลังทำงานอยู่
    - ใช้เพื่อ monitor ว่าบอทกำลังทำอะไร
    - รองรับ CAPTCHA debugging
    """
    manager = request.app.state.tiktok_manager
    
    if username not in manager.sessions:
        return JSONResponse(
            status_code=404,
            content={
                "error_type": "SESSION_NOT_FOUND",
                "message": f"Session '{username}' ไม่พบ - ต้อง scrape ก่อน"
            }
        )
    
    sess = manager.sessions[username]
    
    if not sess.scraper:
        return JSONResponse(
            status_code=408,
            content={
                "error_type": "SESSION_TIMEOUT",
                "message": f"Session '{username}' timeout แล้ว"
            }
        )
    
    if not sess.scraper.page:
        return JSONResponse(
            status_code=400,
            content={
                "error_type": "BROWSER_CLOSED",
                "message": f"Browser ของ '{username}' ปิดอยู่"
            }
        )
    
    try:
        # ถ่ายภาพหน้าจอเป็น bytes
        screenshot_bytes = await sess.scraper.page.screenshot(
            type="png",
            full_page=False  # แค่หน้าที่เห็น ไม่ต้อง full page
        )
        
        logger.info(f"Screenshot taken for user: {username}")
        
        return Response(
            content=screenshot_bytes,
            media_type="image/png",
            headers={
                "Content-Disposition": f"inline; filename=snapshot_{username}.png"
            }
        )
    except Exception as e:
        logger.error(f"Screenshot failed for {username}: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "error_type": "SCREENSHOT_FAILED",
                "message": f"ถ่ายภาพไม่สำเร็จ: {str(e)}"
            }
        )

@tiktok_router.get("/sessions",
         tags=["TikTok Sessions"],summary="ดู session ที่กำลังใช้งาน")
async def list_sessions(request: Request):
    return {
        "active_sessions": [    
            {
                "username": k,
                "locked": v.lock.locked(),
                "idle_seconds": int(time.monotonic() - v.last_used)
            } 
            for k, v in request.app.state.tiktok_manager.sessions.items()
        ],
        "total": len(request.app.state.tiktok_manager.sessions)
    }

@tiktok_router.delete("/sessions/{username}", 
            tags=["TikTok Sessions"],summary="ปิดเบราว์เซอร์และเคลียร์ Session ของผู้ใช้รายบุคคล (ไม่ลบไฟล์โปรไฟล์)")
async def close_user_session(username: str, request: Request):
    """ปิดเบราว์เซอร์และเคลียร์ Session ของผู้ใช้รายบุคคล (ไม่ลบไฟล์โปรไฟล์)"""
    manager = request.app.state.tiktok_manager
    if username in manager.sessions:
        sess = manager.sessions[username]
        
        # ตรวจสอบว่ามีงานค้างอยู่หรือไม่
        if sess.lock.locked():
            raise HTTPException(
                status_code=400, 
                detail="Cannot close session: User is currently performing a task."
            )
            
        await sess.close()
        manager.sessions.pop(username, None)
        logger.info(f"Manual session closure for: {username}")
        return {"status": "success", "message": f"Session for {username} closed and cleared from memory."}
    
    raise HTTPException(status_code=404, detail=f"No active session found for user: {username}")




@tiktok_router.get("/profiles", 
         response_model=ProfilesListResponse,
         tags=["TikTok Maintenance"],summary="ดูรายการโปรไฟล์ผู้ใช้ทั้งหมดที่ถูกสร้างไว้ในระบบ")
async def list_all_profiles(request: Request):
    """ดูรายการโปรไฟล์ผู้ใช้ทั้งหมดที่ถูกสร้างไว้ในระบบ"""
    base_path = Path(settings.base_profile_dir)
    if not base_path.exists():
        return {"total_count": 0, "profiles": []}

    profiles_data = []
    for item in base_path.iterdir():
        if item.is_dir():
            # คำนวณขนาดโฟลเดอร์ (หน่วย MB)
            size_bytes = sum(f.stat().st_size for f in item.glob('**/*') if f.is_file())
            size_mb = round(size_bytes / (1024 * 1024), 2)
            
            username = item.name
            is_active = username in request.app.state.tiktok_manager.sessions
            
            profiles_data.append(ProfileInfo(
                username=username,
                profile_dir=str(item),
                exists=True,
                size_mb=size_mb,
                is_active=is_active
            ))

    return {
        "total_count": len(profiles_data),
        "profiles": profiles_data
    }

@tiktok_router.post("/profiles/delete", 
        tags=["TikTok Maintenance"],summary="ปิด Session และลบโฟลเดอร์ข้อมูลโปรไฟล์ทิ้งถาวร")
async def full_delete_profile(username: str, request: Request):
    """ปิด Session และลบโฟลเดอร์ข้อมูลโปรไฟล์ทิ้งถาวร"""
    safe_name = to_safe_name(username)
    profile_path = Path(settings.base_profile_dir).joinpath(safe_name)
    manager = request.app.state.tiktok_manager

    # 1. ปิด Session ก่อนถ้าเปิดอยู่
    if username in manager.sessions:
        sess = manager.sessions[username]
        if sess.lock.locked():
             raise HTTPException(status_code=400, detail="User is busy, cannot delete.")
        await sess.close()
        manager.sessions.pop(username, None)

    # 2. ลบโฟลเดอร์
    if profile_path.exists():
        try:
            # ใช้ shutil.rmtree สำหรับลบ directory ที่มีไฟล์ข้างใน
            shutil.rmtree(profile_path)
            logger.info(f"Full profile deletion: {username}")
            return {"status": "success", "message": f"Profile and session for {username} deleted successfully."}
        except Exception as e:
            logger.error(f"Error deleting directory: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to delete profile files: {str(e)}")
            
    return {"status": "not_found", "message": "No profile folder found on disk."}




# @tiktok_router.post("/close-session")
# async def close_session(
#     username: str,
#     # token: str = Depends(verify_token)
# ):
#     if username in request.app.state.tiktok_manager.sessions:
#         sess = request.app.state.tiktok_manager.sessions[username]
#         await sess.close()
#         request.app.state.tiktok_manager.sessions.pop(username, None)
#         logger.info(f"Closed session: {username}")
#         return {"status": "closed", "username": username}
#     return {"status": "not_found", "username": username}

# Standalone run (commented - use main.py instead)
# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="0.0.0.0", port=8000)