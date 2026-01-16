# ระบบ API สำหรับดึงโพสต์จาก Facebook Groups ด้วย Playwright

from dotenv import load_dotenv
load_dotenv()

import os
import re
import sys
import time
import glob
import json
import uuid
import httpx
import shutil
import random
import socket
import uvicorn
import asyncio
import logging
import calendar
import traceback
import subprocess
import contextvars
import io
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Sequence, Tuple, Any, Literal, Union
from bs4.element import Tag
from bs4 import BeautifulSoup
from PIL import Image
from pydantic import BaseModel, Field
from fastapi.responses import PlainTextResponse, Response
from fastapi import FastAPI, HTTPException, Request, Query, Body, Path as PathParam, APIRouter
from playwright.async_api import async_playwright, Page, BrowserContext
from browser_use.llm import ChatOpenAI
from browser_use import Agent, BrowserSession
from browser_use import Controller
from logging.handlers import RotatingFileHandler

# ============================================================
# CONFIG
# ============================================================
# ส่วนการตั้งค่าค่าคงที่ (config) โดยอ่านค่าจากตัวแปรแวดล้อม (ENV) หรือใช้ค่าดีฟอลต์
# - BASE_PROFILE_DIR: ตำแหน่งเก็บโปรไฟล์เบราว์เซอร์แยกตามผู้ใช้
# - BASE_OUTPUT_DIR: ตำแหน่งเก็บผลลัพธ์การดึงข้อมูล (ไฟล์ JSON)
# - BASE_LOG_DIR: ตำแหน่งเก็บ log แยกตามผู้ใช้ และ log กลาง
# - API_IDLE_TIMEOUT_SEC: กำหนดเวลาว่างสูงสุดก่อนปิด session อัตโนมัติ
# - DEFAULT_LLM_MODEL: รุ่นของโมเดล LLM สำหรับ agent ในงาน random task
# - RECHROME_POLICY/RECHROME_EVERY_N: นโยบายรีสตาร์ทเบราว์เซอร์เพื่อลดสะสม state ที่อาจผิดพลาด
BASE_PROFILE_DIR     = os.getenv("BASE_PROFILE_DIR", "FDATA/profiles")      # โฟลเดอร์โปรไฟล์ต่อผู้ใช้ (username_safe)
BASE_OUTPUT_DIR      = os.getenv("BASE_OUTPUT_DIR", "FDATA/outputs")        # โฟลเดอร์เก็บผลลัพธ์ JSON
BASE_LOG_DIR         = os.getenv("BASE_LOG_DIR", "FDATA/logs")              # โฟลเดอร์เก็บ log
API_IDLE_TIMEOUT_SEC = int(os.getenv("API_IDLE_TIMEOUT_SEC", "1800"))       # ปิด session ถ้า idle เกินค่านี้ (วินาที)
DEFAULT_LLM_MODEL    = os.getenv("BROWSER_USE_LLM_MODEL", "gpt-5-mini")
VERBOSE              = True

# Re-Chrome policy
# - RECHROME_POLICY: 'never' | 'before_each' | 'every_n'
#     * never (default): ไม่รีสตาร์ทอัตโนมัติ
#     * before_each: รีสตาร์ทก่อนเริ่มงาน scrape-posts ทุกครั้ง
#     * every_n: รีสตาร์ทเมื่อถึงรอบที่ n ตาม RECHROME_EVERY_N
RECHROME_POLICY = os.getenv("RECHROME_POLICY", "never").strip().lower()
try:
    RECHROME_EVERY_N = int(os.getenv("RECHROME_EVERY_N", "0"))
except Exception:
    RECHROME_EVERY_N = 0

# ============================================================
# Utilities: Path/Dirs
# ============================================================
# ยูทิลิตี้สำหรับจัดการโฟลเดอร์/พาธ และฟังก์ชัน IO ขั้นพื้นฐาน
def ensure_dirs():
    # สร้างโฟลเดอร์หลักสำหรับ profiles/logs/outputs หากยังไม่มี
    Path(BASE_PROFILE_DIR).mkdir(parents=True, exist_ok=True)
    Path(BASE_LOG_DIR).mkdir(parents=True, exist_ok=True)
    Path(BASE_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

def user_dirs(username_safe: str) -> Dict[str, str]:
    # สร้าง/คืนค่าโฟลเดอร์ย่อยสำหรับผู้ใช้หนึ่งคน (แยกตาม username_safe)
    d = {
        "profile_dir": str(Path(BASE_PROFILE_DIR).joinpath(username_safe)),
        "log_dir":     str(Path(BASE_LOG_DIR).joinpath(username_safe)),
        "out_dir":     str(Path(BASE_OUTPUT_DIR).joinpath(username_safe)),
    }
    for p in d.values():
        Path(p).mkdir(parents=True, exist_ok=True)
    return d

def to_safe_name(username_full: str) -> str:
    # แปลงชื่อผู้ใช้ให้เป็นชื่อที่ปลอดภัยต่อการใช้เป็นชื่อโฟลเดอร์/ไฟล์ โดยตัดส่วนหลัง '@' ออกหากมี
    return username_full.split("@", 1)[0] if "@" in username_full else username_full

def make_task_id() -> str:
    # สร้างรหัสงานที่ไม่ซ้ำ: yyyyMMdd-HHMMSS-สุ่ม6ตัว
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]

def now_ts() -> str:
    # คืนค่าสตริง timestamp ปัจจุบันสำหรับ log/ผลลัพธ์
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def save_json(path: str, data: Any):
    # บันทึกข้อมูลเป็นไฟล์ JSON (UTF-8, indent 2)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def append_jsonl(path: str, record: Dict[str, Any]):
    # เขียนบรรทัด JSON ต่อท้ายไฟล์ .jsonl (ใช้เก็บ error/event log)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

# ============================================================
# Logging (Global + Per-User) + Context
# ============================================================
# ระบบ logging แบบมีทั้ง logger กลาง และ logger ต่อผู้ใช้ (ตาม username_safe)
# ใช้ contextvars เพื่ออ้างอิงผู้ใช้ปัจจุบัน (สำหรับ log/trace)

_LOGGER_CACHE: Dict[str, logging.Logger] = {}
_GLOBAL_LOGGER_NAME = "app.global"

# contextvars จะช่วยผูกค่า (user/task) เข้ากับ context ของ asyncio task ปัจจุบัน
CURRENT_USER_SAFE: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_user_safe", default=None)
CURRENT_TASK_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_task_id", default=None)

def _configure_logger(name: str, log_file: str, err_file: Optional[str] = None) -> logging.Logger:
    # ตั้งค่า logger ที่จะเขียนทั้งลง console และไฟล์ พร้อมหมุน (rotate) log เมื่อใหญ่
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(fmt="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # แสดงบน stdout
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # บันทึกไฟล์หลัก
    fh = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # บันทึกไฟล์ error แยก (ถ้ามี)
    if err_file:
        eh = RotatingFileHandler(err_file, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
        eh.setLevel(logging.ERROR)
        eh.setFormatter(fmt)
        logger.addHandler(eh)

    # ปิดการส่งต่อ log ให้ root logger
    logger.propagate = False
    return logger

def get_user_logger(username_safe: str) -> logging.Logger:
    # คืน logger เฉพาะผู้ใช้ จาก cache หากเคยสร้างแล้ว
    if username_safe in _LOGGER_CACHE:
        return _LOGGER_CACHE[username_safe]
    d = user_dirs(username_safe)
    log_file = str(Path(d["log_dir"]).joinpath(f"{username_safe}.log"))
    err_file = str(Path(d["log_dir"]).joinpath(f"{username_safe}.error.log"))
    logger = _configure_logger(f"user.{username_safe}", log_file, err_file)
    _LOGGER_CACHE[username_safe] = logger
    return logger

def get_global_logger() -> logging.Logger:
    # คืน logger กลาง
    if _GLOBAL_LOGGER_NAME in _LOGGER_CACHE:
        return _LOGGER_CACHE[_GLOBAL_LOGGER_NAME]
    log_file = str(Path(BASE_LOG_DIR).joinpath("app.log"))
    err_file = str(Path(BASE_LOG_DIR).joinpath("app.error.log"))
    logger = _configure_logger(_GLOBAL_LOGGER_NAME, log_file, err_file)
    _LOGGER_CACHE[_GLOBAL_LOGGER_NAME] = logger
    return logger

def record_error_json(username_safe: Optional[str], where: str, exc: BaseException, extra: Optional[Dict[str, Any]] = None):
    # บันทึกข้อผิดพลาดเป็น jsonl ทั้งแบบ global และในโฟลเดอร์ผู้ใช้ (ถ้ามี)
    data = {
        "ts": now_ts(),
        "task_id": CURRENT_TASK_ID.get(),
        "user": username_safe,
        "where": where,
        "type": exc.__class__.__name__,
        "message": str(exc),
        "traceback": traceback.format_exc(),
        "extra": extra or {},
    }
    append_jsonl(str(Path(BASE_LOG_DIR).joinpath("errors.jsonl")), data)
    if username_safe:
        udir = user_dirs(username_safe)
        append_jsonl(str(Path(udir["log_dir"]).joinpath("errors.jsonl")), data)

# ---- Compatibility logging helpers used by scraping functions ----
def section(title: str):
    # พิมพ์หัวข้อ section ลง log โดยอ้างอิงผู้ใช้จาก contextvars
    user = CURRENT_USER_SAFE.get()
    logger = get_user_logger(user) if user else get_global_logger()
    logger.info("=" * 80)
    logger.info(f"[{user}] {title}" if user else title)
    logger.info("-" * 80)

def log(msg: str, level: str = "INFO"):
    # ฟังก์ชัน log พร้อมระดับ (INFO/WARN/ERROR)
    user = CURRENT_USER_SAFE.get()
    logger = get_user_logger(user) if user else get_global_logger()
    lvl = level.upper()
    if lvl in ("ERROR",):
        logger.error(msg)
    elif lvl in ("WARN", "WARNING"):
        logger.warning(msg)
    else:
        logger.info(msg)

def warn(msg: str):
    # ช็อตคัตสำหรับ warning
    log(msg, level="WARN")

def err(msg: str):
    # ช็อตคัตสำหรับ error
    log(msg, level="ERROR")

def convert_timeago_to_date(timeago: str) -> str:
    # ฟังก์ชันแปลงข้อความเวลาแบบไทย (เช่น "เมื่อวานนี้", "3 ชั่วโมง") หรือรูปแบบวันที่ไทย
    # ให้เป็น timestamp ในรูปแบบ "%Y/%m/%d %H:%M:%S"
    # ใช้ regex ที่คอมไพล์ครั้งเดียว โดยเก็บไว้ใน attribute ของฟังก์ชันเพื่อลด overhead
    if not hasattr(convert_timeago_to_date, "_init"):
        THAI_MONTHS = {
            "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5, "มิถุนายน": 6,
            "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12
        }
        MONTHS_PATTERN = "|".join(sorted(THAI_MONTHS.keys(), key=len, reverse=True))
        convert_timeago_to_date._DT_FMT = "%Y/%m/%d %H:%M:%S"
        convert_timeago_to_date._THAI_MONTHS = THAI_MONTHS
        # "12 มีนาคม 2023"
        convert_timeago_to_date._DATE_YMD_RE = re.compile(
            rf"^\s*(\d{{1,2}})\s+({MONTHS_PATTERN})\s+(\d{{4}})\s*$"
        )
        # "12 มีนาคม เวลา 15:30 น."
        convert_timeago_to_date._DATE_TIME_RE = re.compile(
            rf"^\s*(\d{{1,2}})\s+({MONTHS_PATTERN})\s+เวลา\s+(\d{{1,2}}):(\d{{2}})\s*น\.?\s*$"
        )
        # เคสสัมพัทธ์
        convert_timeago_to_date._NOW_RE = re.compile(r"^\s*ตอนนี้\s*$")
        convert_timeago_to_date._YESTERDAY_RE = re.compile(r"^\s*เมื่อวานนี้\s*$")
        convert_timeago_to_date._SECONDS_RE = re.compile(r"^\s*(\d+)\s*วินาที(?:ที่แล้ว)?\s*$")
        convert_timeago_to_date._MINUTES_RE = re.compile(r"^\s*(\d+)\s*นาที(?:ที่แล้ว)?\s*$")
        convert_timeago_to_date._HOURS_RE = re.compile(r"^\s*(\d+)\s*ชั่วโมง(?:ที่แล้ว)?\s*$")
        convert_timeago_to_date._DAYS_RE = re.compile(r"^\s*(\d+)\s*วัน(?:ที่แล้ว)?\s*$")
        convert_timeago_to_date._WEEKS_RE = re.compile(r"^\s*(\d+)\s*สัปดาห์(?:ที่แล้ว)?\s*$")
        convert_timeago_to_date._MONTHS_RE = re.compile(r"^\s*(\d+)\s*เดือน(?:ที่แล้ว)?\s*$")
        convert_timeago_to_date._YEARS_RE = re.compile(r"^\s*(\d+)\s*ปี(?:ที่แล้ว)?\s*$")
        convert_timeago_to_date._init = True

    s = (timeago or "").strip()
    now = datetime.now()
    fmt = convert_timeago_to_date._DT_FMT
    THAI_MONTHS = convert_timeago_to_date._THAI_MONTHS

    # 'วัน เดือน ปี' เช่น '12 มีนาคม 2023' -> เวลา 00:00:00
    m = convert_timeago_to_date._DATE_YMD_RE.match(s)
    if m:
        day = int(m.group(1))
        month = THAI_MONTHS[m.group(2)]
        year = int(m.group(3))
        try:
            dt = datetime(year, month, day, 0, 0, 0)
            return dt.strftime(fmt)
        except ValueError:
            return "รูปแบบไม่รองรับ"

    # 'วัน เดือน เวลา hh:mm น.' เช่น '12 มีนาคม เวลา 15:30 น.' (ใช้ปีปัจจุบัน, วินาที = 00)
    m = convert_timeago_to_date._DATE_TIME_RE.match(s)
    if m:
        day = int(m.group(1))
        month = THAI_MONTHS[m.group(2)]
        hour = int(m.group(3))
        minute = int(m.group(4))
        try:
            dt = datetime(now.year, month, day, hour, minute, 0)
            return dt.strftime(fmt)
        except ValueError:
            return "รูปแบบไม่รองรับ"

    # สัมพัทธ์: ตอนนี้/เมื่อวาน/วินาที/นาที/ชั่วโมง/วัน/สัปดาห์/เดือน/ปี ที่แล้ว
    if convert_timeago_to_date._NOW_RE.match(s):
        return now.strftime(fmt)
    if convert_timeago_to_date._YESTERDAY_RE.match(s):
        return (now - timedelta(days=1)).strftime(fmt)

    m = convert_timeago_to_date._SECONDS_RE.match(s)
    if m:
        return (now - timedelta(seconds=int(m.group(1)))).strftime(fmt)
    m = convert_timeago_to_date._MINUTES_RE.match(s)
    if m:
        return (now - timedelta(minutes=int(m.group(1)))).strftime(fmt)
    m = convert_timeago_to_date._HOURS_RE.match(s)
    if m:
        return (now - timedelta(hours=int(m.group(1)))).strftime(fmt)
    m = convert_timeago_to_date._DAYS_RE.match(s)
    if m:
        return (now - timedelta(days=int(m.group(1)))).strftime(fmt)
    m = convert_timeago_to_date._WEEKS_RE.match(s)
    if m:
        return (now - timedelta(weeks=int(m.group(1)))).strftime(fmt)

    m = convert_timeago_to_date._MONTHS_RE.match(s)
    if m:
        # ลดเดือนแบบปฏิทิน (รักษาเวลาเดิม) และปรับวันไม่เกินสิ้นเดือน
        months_back = int(m.group(1))
        total_months = (now.year * 12 + (now.month - 1)) - months_back
        year = total_months // 12
        month = total_months % 12 + 1
        day = min(now.day, calendar.monthrange(year, month)[1])
        try:
            dt = now.replace(year=year, month=month, day=day)
        except ValueError:
            dt = now.replace(year=year, month=month, day=calendar.monthrange(year, month)[1])
        return dt.strftime(fmt)

    m = convert_timeago_to_date._YEARS_RE.match(s)
    if m:
        years_back = int(m.group(1))
        try:
            dt = now.replace(year=now.year - years_back)
        except ValueError:
            # กรณี 29 ก.พ. แล้วปีปลายทางไม่มี 29 ก.พ.
            dt = now.replace(year=now.year - years_back, month=2, day=28)
        return dt.strftime(fmt)

    return "รูปแบบไม่รองรับ"

def extract_post_from_like_element(el: Tag) -> Optional[Tuple[str, Dict[str, Any]]]:
    # ดึงข้อมูลหลักจากโพสต์ในกลุ่ม (จาก BeautifulSoup element)
    # - ข้อความโพสต์
    # - ลิงก์โพสต์/เวลา (timeago -> แปลง time)
    # - ผู้โพสต์ (user)
    # - raw_data
    # คืนค่า: [ข้อความโพสต์, dict ข้อมูล]
    msg_div = el.select_one('div[data-ad-preview="message"]')
    text = msg_div.get_text(strip=True) if msg_div else ""

    # ลิงก์โพสต์
    post_data: Dict[str, Any] = {}
    anchor_post = el.select_one('a[href*="/posts/"]')
    if anchor_post and anchor_post.get('href'):
        href_url = (anchor_post.get('href') or "").split("/?")[0]
        post_data["share_url"] = href_url
        post_data["timeago"] = anchor_post.get_text(strip=True)
        post_data["time"] = convert_timeago_to_date(post_data["timeago"])
    else:
        post_data["share_url"] = ""
        post_data["timeago"] = ""
        post_data["time"] = ""

    # ผู้โพสต์ (ดึงจากลิงก์ /user/)
    parent_lv16_message = el.get_text(separator="\n")
    anchor_user = el.select_one('a[href*="/user/"]')
    if anchor_user and anchor_user.get('href'):
        user_href = anchor_user.get('href') or ""
        try:
            user_id = user_href.split("/user/")[1].split("/?")[0]
        except Exception:
            user_id = ""
        post_data["user_url"] = f"facebook.com/profile.php?id={user_id}" if user_id else ""
        post_data["user_post_name"] = parent_lv16_message.split("\n")[0] if parent_lv16_message else ""
    else:
        post_data["userUrl"] = ""
        post_data["user_post_name"] = ""

    post_data["raw_data"] = el.text
    return [text, post_data]

def extract_key_values_from_latest_json(folder: str, target_key: str, encoding: str = "utf-8-sig"):
    """
    อ่านไฟล์ JSON ล่าสุดจากโฟลเดอร์ แล้วค้นหา value ของ key ที่ระบุแบบ recursive
    รองรับทั้ง:
      - ไฟล์ .json (โครงสร้างเป็น object หรือ array)
      - ไฟล์ .ndjson / JSON Lines (หนึ่ง JSON ต่อหนึ่งบรรทัด)
    คืนค่าเป็นทูเพิล:
      (json_path, results_per_unit, all_values)
    - json_path: พาธของไฟล์ JSON ล่าสุดที่อ่านได้
    - results_per_unit: รายการผลลัพธ์ต่อหน่วยข้อมูล
        * ถ้าเป็นไฟล์ .json ที่เป็น object: [(1, [values...])]
        * ถ้าเป็นไฟล์ .json ที่เป็น array: [(index, [values...])] สำหรับแต่ละ element
        * ถ้าเป็นไฟล์ .ndjson: [(line_number, [values...])] สำหรับแต่ละบรรทัดที่ parse ได้
    - all_values: ลิสต์รวมค่าทั้งหมดที่พบของ key ตามลำดับที่ค้นเจอ
    """
    # ฟังก์ชันย่อย: ค้นหา key แบบลึก (recursive) ทั้งใน dict/list
    def _deep_find_values(obj: Any, key: str) -> List[Any]:
        found: List[Any] = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == key:
                    found.append(v)
                found.extend(_deep_find_values(v, key))
        elif isinstance(obj, list):
            for item in obj:
                found.extend(_deep_find_values(item, key))
        return found

    # หาไฟล์ .json/.ndjson ล่าสุดในโฟลเดอร์
    json_files = glob.glob(os.path.join(folder, "*.json"))
    ndjson_files = glob.glob(os.path.join(folder, "*.ndjson"))
    files = json_files + ndjson_files
    if not files:
        # ถ้าไม่มีไฟล์เลย คืนค่า None ทั้งหมด (ผู้เรียกต้องเช็คเอง)
        return None, None, None
        # หรืออาจ raise FileNotFoundError ได้หากต้องการ

    json_path = max(files, key=os.path.getmtime)  # เลือกไฟล์ที่แก้ไขล่าสุด
    results_per_unit: List[Tuple[int, List[Any]]] = []
    all_values: List[Any] = []

    # พยายาม parse เป็น JSON ปกติทั้งไฟล์ก่อน
    try:
        with open(json_path, "r", encoding=encoding) as f:
            data = json.load(f)
        if isinstance(data, dict):
            vals = _deep_find_values(data, target_key)
            results_per_unit.append((1, vals))
            all_values.extend(vals)
        elif isinstance(data, list):
            for i, item in enumerate(data, start=1):
                vals = _deep_find_values(item, target_key)
                results_per_unit.append((i, vals))
                all_values.extend(vals)
        else:
            # root เป็น primitive: ไม่มีอะไรให้ค้นหาต่อ
            pass
        return json_path, results_per_unit, all_values
    except Exception:
        # ถ้า parse ทั้งไฟล์ไม่ผ่าน (อาจเป็น NDJSON) -> โหมดอ่านทีละบรรทัด
        pass

    # โหมด NDJSON: อ่านทีละบรรทัดและพยายาม json.loads
    with open(json_path, "r", encoding=encoding) as f:
        for idx, line in enumerate(f, start=1):
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                continue  # ข้ามบรรทัดที่ไม่ใช่ JSON ที่ถูกต้อง
            vals = _deep_find_values(obj, target_key)
            results_per_unit.append((idx, vals))
            all_values.extend(vals)
    return json_path, results_per_unit, all_values

# Browser-Use
def find_pid_by_port(port: int):
    # ค้นหา PID ของ Chromium/Chrome ที่เปิด --remote-debugging-port=port เพื่อผูก BrowserSession ของ agent
    try:
        command = f'pgrep -f "chromium.*--remote-debugging-port={port}"'
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            pid = int(result.stdout.strip().splitlines()[0])
            return pid
        return None
    except (ValueError, FileNotFoundError):
        return None

def get_free_port() -> int:
    # ขอพอร์ตว่างจากระบบ (bind 127.0.0.1:0 แล้วอ่านพอร์ตที่ถูกกำหนด)
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port

class BrowserUser:
    # คลาส wrapper สำหรับใช้งาน browser_use Agent/Controller/BrowserSession ผูกกับ PID ของ chromium
    def __init__(self, browser_pid=None):
        self.browser_pid = browser_pid
        self.browser_session = None
        self.llm_model = None
        self.controller = None
        self.autoweb_init = False

    async def _autoweb_init(self):
        # เตรียม controller + browser_session + llm สำหรับ agent หนึ่งตัว
        if self.autoweb_init: return
        self.controller = Controller()
        self.browser_session = BrowserSession(
            browser_pid=self.browser_pid,
            viewport={"width": 1920, "height": 1080},
            allowed_domains=['https://*.com']  # จำกัดโดเมนได้หากต้องการ
        )
        self.llm_model = ChatOpenAI(model=DEFAULT_LLM_MODEL)
        self.autoweb_init = True

    async def _run_autoweb(self, task):
        # รัน agent หนึ่งงาน โดยกำหนดข้อความ task (prompt/goal) ส่งให้ LLM ควบคุมเบราว์เซอร์
        if not self.autoweb_init:
            await self._autoweb_init()
        agent = Agent(
            task=task,
            browser_session=self.browser_session,
            llm=self.llm_model,
            controller=self.controller,
            max_steps=20  # จำกัดจำนวนขั้นสูงสุดต่อ task
        )
        return await agent.run()

# ============================================================
# Core Scraper (Playwright) - FROM YOUR SNIPPET (จัดระเบียบ)
# ============================================================
class FBScrape:
    # คลาสศูนย์กลางครอบ Playwright: เปิด context/page, ควบคุมเครื่องมือ basic (goto/scroll/type/คลิก)
    def __init__(self, browser_data_dir: Optional[str] = None, start_page: str = "google.com", verbose: bool = True, use_autoweb: bool = True):
        self.browser_data_dir = browser_data_dir          # โฟลเดอร์โปรไฟล์ (Persistent context) หรือ None (non-persistent)
        self.start_page = start_page                      # หน้าแรกที่จะไปหลังเปิดเบราว์เซอร์
        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self.did_lazy_init = False                        # ป้องกัน init ซ้ำ
        self.browser_user = use_autoweb                   # ถ้าใช้ autoweb จะเก็บ BrowserUser หลังทราบ PID
        self.page_script_path = os.path.join("page_script.js")  # สคริปต์ที่ inject ใส่เพจทุกครั้ง
        self.verbose = verbose
        self.remote_debugging_port = get_free_port()      # เลือกพอร์ตว่างสำหรับ remote debugging
        # NEW: lock สำหรับ serialize งาน sink (randomtask) ต่อโปรไฟล์/เบราว์เซอร์
        self._sink_lock = asyncio.Lock()
        if self.verbose:
            section("เริ่มต้นคลาส FBScrape")
            log(f"browser_data_dir={self.browser_data_dir or '(None)'} | start_page={self.start_page}")

    async def _human_sleep(self, min_s: float = 0.3, max_s: float = 1.2):
        # Sleep แบบสุ่มเพื่อเลียนแบบมนุษย์ ลดความเสี่ยงโดนจับว่าอัตโนมัติ
        delay = random.uniform(min_s, max_s)
        if self.verbose:
            log(f"พักแบบสุ่ม {delay:.2f}s เพื่อความเป็นธรรมชาติ")
        await asyncio.sleep(delay)

    async def _handle_new_page(self, new_page: Page):
        # callback เมื่อมีแท็บ/เพจใหม่เปิดขึ้นใน context
        if self.verbose:
            log(f"ตรวจพบแท็บ/เพจใหม่: {new_page.url or '(loading...)'}")
        try:
            await new_page.wait_for_load_state(timeout=30000)
            if self.verbose:
                log(f"แท็บใหม่โหลดเสร็จ: {new_page.url}")
        except Exception as e:
            warn(f"แท็บใหม่ ({new_page.url}) โหลดไม่เสร็จสมบูรณ์หรือใช้เวลานานเกินไป: {e}")

        # ปิดเพจเดิม (ถ้ามี) แล้วสลับไปใช้เพจใหม่เป็นตัวหลัก
        if self._page and not self._page.is_closed():
            try:
                await self._page.close()
                if self.verbose:
                    log("ปิดเพจเดิมเรียบร้อย")
            except Exception as e:
                warn(f"ปิดเพจเดิมไม่สำเร็จ: {e}")

        self._page = new_page
        self._page.set_default_timeout(60000)

        # Inject init script หากไฟล์มีอยู่
        if os.path.exists(self.page_script_path):
            try:
                await self._page.add_init_script(path=self.page_script_path)
                if self.verbose:
                    log(f"โหลด init script: {self.page_script_path}")
            except Exception as e:
                warn(f"เพิ่ม init script ไม่สำเร็จ: {e}")
        else:
            if self.verbose:
                log("ไม่มี init script ให้โหลด (ข้าม)")

    async def _lazy_init(self):
        # เปิด Playwright + สร้าง context/page (ครั้งเดียว) รองรับทั้งโหมด persistent และ non-persistent
        if self.did_lazy_init:
            return

        section("เตรียมเบราว์เซอร์ (Lazy Init)")
        if self._playwright is None:
            if self.verbose:
                log("เริ่ม Playwright")
            self._playwright = await async_playwright().start()

        if self._context is None:
            if self.browser_data_dir is None:
                # non-persistent: เปิด browser ปกติ (ไม่ผูกกับโปรไฟล์)
                if self.verbose:
                    log("โหมด non-persistent (ไม่ใช้โปรไฟล์)")
                browser = await self._playwright.chromium.launch(
                    headless=False,
                    args=['--disable-blink-features=AutomationControlled',
                          f"--remote-debugging-port={self.remote_debugging_port}",
                          "--force-device-scale-factor=0.8",
                          "--high-dpi-support=1"]
                )
                self._context = await browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
                    locale="th_TH",
                    extra_http_headers={"Accept-Language": "th;q=0.8,en;q=0.2"}
                )
            else:
                # persistent: เปิด browser พร้อมผูก directory โปรไฟล์ (เก็บ cookie/เซสชัน)
                if self.verbose:
                    log(f"โหมด persistent (ใช้โปรไฟล์): {self.browser_data_dir}")
                self._context = await self._playwright.chromium.launch_persistent_context(
                    self.browser_data_dir,
                    headless=False,
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
                    locale="th_TH",
                    extra_http_headers={"Accept-Language": "th;q=0.8,en;q=0.2"},
                    args=['--disable-blink-features=AutomationControlled',
                          f"--remote-debugging-port={self.remote_debugging_port}",
                          "--force-device-scale-factor=0.8",
                          "--high-dpi-support=1"]
                )

        # เมื่อ context สร้างแล้ว: สมัคร event "page" เพื่อจับแท็บใหม่ แล้วเปิดหน้าแรก
        self._context.on("page", lambda p: asyncio.create_task(self._handle_new_page(p)))
        self._page = await self._context.new_page()
        self._page.set_default_timeout(60000)
        assert self._page is not None

        # Inject init script ในเพจแรก
        if os.path.exists(self.page_script_path):
            try:
                await self._page.add_init_script(path=self.page_script_path)
                if self.verbose:
                    log(f"โหลด init script (ในเพจใหม่): {self.page_script_path}")
            except Exception as e:
                warn(f"เพิ่ม init script ในเพจใหม่ไม่สำเร็จ: {e}")

        if self.verbose:
            log(f"ไปยังหน้าเริ่มต้น: {self.start_page}")
        await self._page.goto(self.start_page if "http" in self.start_page else "https://" + self.start_page)
        await self._page.wait_for_load_state()
        await self._human_sleep(0.5, 2.0)

        # ค้นหา PID ของ Chromium ที่ผูกกับพอร์ต remote debugging เพื่อเอาไปสร้าง BrowserUser
        pid = find_pid_by_port(self.remote_debugging_port)
        if pid:
            if self.browser_user:
                self.browser_user = BrowserUser(browser_pid=pid)
            log(f"พบเบราว์เซอร์! Process ID (PID) คือ: {pid}")
        else:
            log(f"ไม่สามารถหา PID ของเบราว์เซอร์ที่ใช้พอร์ต {self.remote_debugging_port} ได้")

        self.did_lazy_init = True
        if self.verbose:
            log("เตรียมเบราว์เซอร์เสร็จสมบูรณ์")

    async def close(self):
        # ปิดทรัพยากรทั้งหมด (page/context/playwright) อย่างปลอดภัย
        section("ปิดทรัพยากรเบราว์เซอร์")
        if self._page is not None:
            try:
                await self._page.close()
                if self.verbose:
                    log("ปิดเพจแล้ว")
            except Exception as e:
                warn(f"ปิดเพจล้มเหลว: {e}")
            self._page = None

        if self._context is not None:
            try:
                await self._context.close()
                if self.verbose:
                    log("ปิด context แล้ว")
            except Exception as e:
                warn(f"ปิด context ล้มเหลว: {e}")
            self._context = None

        if self._playwright is not None:
            try:
                await self._playwright.stop()
                if self.verbose:
                    log("หยุด Playwright แล้ว")
            except Exception as e:
                warn(f"หยุด Playwright ล้มเหลว: {e}")
            self._playwright = None

    async def _execute_tool(self, name, position=None, value=None, enter=None, clear_before=True):
        # ตัวรวม action หลักที่ใช้บ่อย:
        # - click/hover/scroll/goto/type/history_back/keyboard
        # - ทำ log + sleep เลียนแบบมนุษย์
        await self._lazy_init()
        if self.verbose:
            extra = []
            if position is not None:
                extra.append(f"pos={tuple(round(p, 2) for p in position)}")
            if value is not None and name in ("goto", "type", "scroll"):
                shown = (value if name == "goto" else (str(value)[:40] + ("..." if len(str(value)) > 40 else "")))
                extra.append(f"value={shown}")
            if enter:
                extra.append("enter=True")

        if name == "click":
            # คลิกเมาส์: ทำ hover ก่อนเล็กน้อยเพื่อให้เหมือนจริง
            await self._execute_tool("hover", position)
            log(f"เครื่องมือ: {name} | " + ", ".join(extra))
            await self._page.mouse.down()
            await self._page.mouse.up()

        elif name == "goto":
            # ไปยัง URL ที่กำหนด (เติม https:// ให้อัตโนมัติถ้าไม่มี)
            if value is None:
                raise ValueError("ต้องระบุ URL ที่จะเยี่ยมชม")
            log(f"เครื่องมือ: {name} | " + ", ".join(extra))
            await self._page.goto(value if "http" in value else "https://" + value)
            await self._page.wait_for_load_state()
            await self._human_sleep(1.0, 3.0)

        elif name == "scroll":
            # เลื่อนหน้าจอด้วย mouse wheel
            if value is None:
                raise ValueError("ต้องระบุจำนวนพิกเซลที่จะเลื่อน")
            if position:
                await self._execute_tool("hover", position)
            log(f"เครื่องมือ: {name} | " + ", ".join(extra))
            await self._page.mouse.wheel(0, value)
            await self._execute_tool("hover", [0, 0])

        elif name == "hover":
            # เลื่อนเมาส์ไปยังตำแหน่งแบบมี steps สุ่ม
            if position is None:
                raise ValueError("ต้องระบุตำแหน่งที่จะแสดงการเลื่อนเมาส์")
            log(f"เครื่องมือ: {name} | " + ", ".join(extra))
            await self._page.mouse.move(position[0], position[1], steps=random.randint(10, 30))

        elif name == "type":
            # พิมพ์ข้อความลง element ที่ focus (คลิกก่อนถ้ามี position)
            if value is None:
                raise ValueError("ต้องระบุข้อความที่จะพิมพ์")
            if position is not None:
                await self._execute_tool("click", position)
            if clear_before:
                # กดเลือกทั้งหมด + delete เพื่อล้าง input ก่อนพิมพ์
                select_all = "Meta+A" if sys.platform == "darwin" else "Control+A"
                await self._page.keyboard.press(select_all)
                await self._page.keyboard.press("Delete")
            shown = str(value)[:40] + ("..." if len(str(value)) > 40 else "")
            log(f"เครื่องมือ: {name} | value={shown}")
            await self._page.keyboard.type(value)
            if enter:
                await self._page.keyboard.press("Enter")

        elif name == "history_back":
            # ย้อนกลับในประวัติการเข้าเว็บ
            log(f"เครื่องมือ: {name} | " + ", ".join(extra))
            await self._page.go_back()

        elif name == "keyboard":
            # กดคีย์บอร์ด command เดี่ยวๆ เช่น "Home", "End"
            log(f"เครื่องมือ: {name} | " + ", ".join(extra))
            await self._page.keyboard.press(value)

        else:
            # คำสั่งไม่รู้จัก
            raise ValueError(f"เครื่องมือ '{name}' ไม่รู้จัก")

        await self._human_sleep(0.5, 1.0)

# ===== helper functions (จากสไนเป็ต, รักษาโครงและ log เดิม) =====
def random_point_in_rect(rect, margin=5, margin_ratio=0.1):
    # เลือกจุดสุ่มภายในกรอบสี่เหลี่ยม (ลดการคลิกตรงขอบ/มุม) ให้เหมือนการคลิกของมนุษย์
    x = rect.get('x', rect.get('left'))
    y = rect.get('y', rect.get('top'))
    w = rect['width']
    h = rect['height']
    if x is None or y is None:
        raise KeyError("rect ต้องมีคีย์ x,y หรือ left,top")

    if margin_ratio is not None:
        mx = float(w) * float(margin_ratio)
        my = float(h) * float(margin_ratio)
    else:
        mx = float(margin); my = float(margin)

    # จำกัด margin ไม่เกินครึ่งหนึ่ง
    mx = max(0.0, min(mx, w / 2.0))
    my = max(0.0, min(my, h / 2.0))

    rx = random.uniform(x + mx, x + w - mx)
    ry = random.uniform(y + my, y + h - my)
    return [rx, ry]

async def get_scroll_distance_to_element(page: Page, target: str, index: int = 0, scope_selector: str = '[role="feed"]') -> dict:
    """
    คำนวณระยะเลื่อนจาก viewport ไปยัง element ตามตัวระบุที่ยืดหยุ่น (CSS หรือ key=value)
    - ใช้ page.evaluate() เรียก script ฝั่ง DOM เพื่อหา element และตำแหน่ง
    - คืนค่าทั้งทิศทาง ('up'/'down'/'in_view') และระยะในพิกเซล
    """
    import re

    def build_css_selector(expr: str) -> str:
        # รองรับการป้อน target ในหลายรูปแบบ:
        # - CSS ตรงๆ (เริ่มด้วย . # [ หรือมี combinator)
        # - key=value เช่น aria-posinset=1
        # - key-only -> แปลเป็น [key]
        s = (expr or "").strip()
        if not s:
            return ""
        if s[0] in ".#[" or any(ch in s for ch in (" ", ">", "+", "~", ":")):
            return s
        m = re.match(r'^([a-zA-Z_][\w\-\:]*)(?:\s*=\s*[\'"]?(.+?)[\'"]?)?$', s)
        if not m:
            return s
        key, value = m.groups()
        key_l = key.lower()
        if value is None:
            return "[class]" if key_l == "class" else f"[{key}]"
        if key_l == "class":
            classes = re.split(r'[.\s]+', value.strip())
            classes = [c for c in classes if c]
            return "".join(f".{c}" for c in classes) if classes else "[class]"
        value_esc = value.replace('"', '\\"')
        return f'[{key}="{value_esc}"]'

    css_selector = build_css_selector(target)
    if not css_selector:
        return {'found': False, 'reason': 'empty_target'}

    script = """
    (args) => {
        const { css, scopeSelector, occIndex } = args;
        const scope = scopeSelector ? document.querySelector(scopeSelector) : document;
        if (!scope) return { found: false, reason: 'no_scope', scope_tried: scopeSelector };

        const nodeList = scope.querySelectorAll(css);
        const list = Array.from(nodeList);
        if (list.length === 0) {
            return { found: false, reason: 'no_match', css, scope_used: scopeSelector || 'document', total_matches: 0 };
        }
        if (occIndex < 0 || occIndex >= list.length) {
            return { found: false, reason: 'index_out_of_range', css, scope_used: scopeSelector || 'document', total_matches: list.length, requested_index: occIndex };
        }

        const element = list[occIndex];
        const rect = element.getBoundingClientRect();
        const currentScrollY = window.pageYOffset || document.documentElement.scrollTop || 0;
        const elementTopAbs = rect.top + currentScrollY;
        const viewportHeight = window.innerHeight;
        const distance = Math.round(rect.top);

        let direction = 'in_view';
        if (rect.top < 0) direction = 'up';
        else if (rect.bottom > viewportHeight) direction = 'down';

        return {
            found: true,
            css,
            scope_used: scopeSelector || 'document',
            match_index: occIndex,
            total_matches: list.length,
            distance_px: distance,
            direction,
            element_top_abs: Math.round(elementTopAbs),
            current_scroll_y: Math.round(currentScrollY),
            viewport_height: viewportHeight,
            element_height: Math.round(rect.height)
        };
    }
    """

    try:
        result = await page.evaluate(script, {
            "css": css_selector,
            "scopeSelector": scope_selector,
            "occIndex": int(index) if isinstance(index, int) else 0
        })
        if not result or not result.get('found'):
            log(f"คำนวณระยะเลื่อน: ไม่พบ element (reason={result.get('reason', 'unknown')}) css={css_selector}", "WARN")
            return {'found': False, **({k: v for k, v in (result or {}).items() if k != 'found'})}

        log(f"คำนวณระยะเลื่อน: css={result.get('css')} idx={result.get('match_index')} "
            f"distance={result['distance_px']}px direction={result['direction']}")
        return {
            'found': True,
            'distance_px': result['distance_px'],
            'direction': result['direction'],
            'element_top': result['element_top_abs'],
            'current_scroll_y': result['current_scroll_y'],
            'total_matches': result.get('total_matches'),
            'match_index': result.get('match_index'),
            'css': result.get('css'),
            'scope_used': result.get('scope_used'),
            'viewport_height': result.get('viewport_height'),
            'element_height': result.get('element_height')
        }
    except Exception as e:
        err(f"เกิดข้อผิดพลาดระหว่างรันสคริปต์คำนวณระยะเลื่อน: {e}")
        return {'found': False, 'reason': 'exception', 'error': str(e)}

async def get_element(
    fb: FBScrape,
    elements,
    element_id=None,
    role=None,
    aria_name=None,
    class_name=None,
    class_match_threshold: float = 0.9
):
    # คัดกรอง elements จากข้อมูล interactive rects ของ MultimodalWebSurfer
    # สนับสนุนฟิลเตอร์: id/role/aria_name/class_name (คล้าย fuzzy match ของ class)
    await fb._lazy_init()
    candidates = list(elements.values())
    if fb.verbose:
        log(f"เริ่มคัดกรอง element | ทั้งหมด={len(candidates)}")

    # ฟิลเตอร์พื้นฐานตาม id/role/aria_name ที่ส่งเข้ามา
    filters = {"id": element_id, "role": role, "aria_name": aria_name}
    for key, value_to_check in filters.items():
        if value_to_check is None:
            continue
        before = len(candidates)
        if isinstance(value_to_check, list):
            candidates = [e for e in candidates if e.get(key) in value_to_check]
        else:
            candidates = [e for e in candidates if e.get(key) == value_to_check]
        if fb.verbose:
            log(f"ฟิลเตอร์ {key}={value_to_check} | {before} -> {len(candidates)}")

    # ฟิลเตอร์ class แบบอัตราส่วนการ match (>= threshold)
    if class_name:
        thr = float(class_match_threshold)
        if thr > 1:
            thr = thr / 100.0
        thr = min(max(thr, 0.0), 1.0)

        need = set(class_name.split() if isinstance(class_name, str) else class_name)

        def class_match_ratio(e):
            element_cls = set((e.get("class_name") or "").split())
            if not need:
                return 1.0
            return len(need & element_cls) / len(need)

        scored = [(e, class_match_ratio(e)) for e in candidates]
        scored = [pair for pair in scored if pair[1] >= thr]
        scored.sort(key=lambda x: x[1], reverse=True)
        candidates = [e for e, _ in scored]
        if fb.verbose:
            log(f"ฟิลเตอร์ class_name (>= {thr:.2f}) | เหลือ {len(candidates)}")

    if not candidates and fb.verbose:
        warn("ไม่พบ element ที่ตรงตามเงื่อนไข")

    return candidates if candidates else None

async def filter_element(
    fb: FBScrape,
    role=None,
    class_name=None,
    aria_name=None,
):
    # ฟิลเตอร์ element แบบผสม:
    # - ใช้ MultimodalWebSurfer.getInteractiveRects() เพื่อได้ metadata
    # - ใช้ BeautifulSoup อ่าน HTML เพื่อจับคู่กับ aria-label/class ตามที่ต้องการ
    elements = await fb._page.evaluate("MultimodalWebSurfer.getInteractiveRects();")
    html_content = await fb._page.content()
    soup = BeautifulSoup(html_content, 'html.parser')

    filters = {}
    if role: filters['role'] = role
    if aria_name: filters['aria-label'] = aria_name

    # หา container ตาม role/aria-label
    feed_container = soup.find_all(attrs=filters)

    # หากระบุตัว class_name ให้เจาะลึกลงไปใน container
    if class_name is not None:
        feed_container_cache = feed_container
        feed_container = []
        for i in feed_container_cache:
            if i is not None: feed_container.extend(i.find_all(attrs={"class": class_name}))

    if len(feed_container) <= 0:
        return None, None

    grop_list = {}
    index = 0

    # สร้าง mapping ของลิงก์/ข้อความ/class จาก group ใน DOM (ช่วยใช้จับคู่กับ interactive rects)
    for feed_container_i in feed_container:
        post_groups = feed_container_i.find_all('div', recursive=True)
        for _, group in enumerate(post_groups, 1):
            x = group.find('a')
            if x is not None:
                grop_list[index] = [x.get('href'), list(group.stripped_strings), ' '.join(x.get('class'))]
                index += 1

    grop_list = [v for k, v in grop_list.items()]

    # เทียบเคียงระหว่าง interactive rects vs soup group
    elements_cache = {}
    for k, v in elements.items():
        for item in grop_list:
            if v.get('aria_name', '').split('\n')[0] in (item[1][0] if item[1] else '') and v.get('class_name', '') in (item[2] if item[2] else ''):
                elements_cache[k] = v

    return elements_cache, grop_list

async def closebt(fb: FBScrape):
    # ปิด modal/overlay โดยพยายามหา aria_name="ปิด" แล้วคลิก
    elements = await fb._page.evaluate("MultimodalWebSurfer.getInteractiveRects();")
    close_button = await get_element(fb, elements, aria_name="ปิด")
    if close_button:
        log("พบปุ่ม 'ปิด' ที่อาจเป็น modal, ทำการคลิก")
        for i in close_button:
            await fb._execute_tool("click", random_point_in_rect(i['rects'][0]))

async def login(fb: FBScrape, email: str, password: str):
    # ล็อกอินเข้า Facebook โดยอาศัย interactive rects + type/enter
    section("ขั้นตอนเข้าสู่ระบบ (Login)")
    await fb._lazy_init()

    if "facebook.com/login" in fb._page.url:
        # หน้า login (desktop)
        elements = await fb._page.evaluate("MultimodalWebSurfer.getInteractiveRects();")
        x = await get_element(fb, elements, aria_name=["ปฏิเสธคุกกี้ที่ไม่จำเป็น"])
        if x:
            log("พบ ปฏิเสธคุกกี้ที่ไม่จำเป็น")
            await fb._execute_tool("click", random_point_in_rect(x[0]['rects'][0]))
            await fb._page.wait_for_timeout(250)

        log("อยู่ในหน้า Login ของ Facebook")
        try:
            elements = await fb._page.evaluate("MultimodalWebSurfer.getInteractiveRects();")
        except Exception:
            elements = {}
            warn("ไม่สามารถอ่าน InteractiveRects (อาจไม่มีสคริปต์สนับสนุน)")

        # หาช่อง email/pass
        Name = await get_element(fb, elements, element_id=["email", "อีเมลหรือหมายเลขโทรศัพท์มือถือ"])
        Pass = await get_element(fb, elements, element_id=["pass", "รหัสผ่าน"])
        if Name and Pass:
            log("กรอกอีเมลและรหัสผ่าน")
            await fb._execute_tool("type", position=random_point_in_rect(Name[0]['rects'][0]), value=email)
            await fb._human_sleep(2.0, 3.0)
            await fb._execute_tool("type", position=random_point_in_rect(Pass[0]['rects'][0]), value=password, enter=True)
            await fb._page.wait_for_load_state()
            await fb._human_sleep(5.0, 7.0)

            if "facebook.com" in fb._page.url:
                log("เข้าสู่ระบบสำเร็จ")
                return True
            else:
                warn("เข้าสู่ระบบไม่สำเร็จ (URL ไม่เปลี่ยนไปยัง Facebook หลัก)")
        else:
            warn("ไม่พบฟิลด์ Email/Password")
        return False

    elif "facebook.com" in fb._page.url:
        # กรณีเริ่มต้นอยู่ในหน้า FB แล้ว (อาจล็อกอินแล้ว)
        log("อยู่ใน Facebook แล้ว (ไม่ต้องเข้าสู่ระบบ)")
        return True

    warn("ไม่ได้อยู่ในหน้า Login และไม่ใช่หน้า Facebook")
    return False

async def directourl(fb: FBScrape, url: str):
    # เปิดหน้ากลุ่มที่ต้องการโดยตรง (เติม https://www. และอาจเติม query sorting แบบสุ่ม)
    section(f"เปิด URL โดยตรง: {url}")
    await fb._lazy_init()
    await fb._execute_tool("goto", value= "https://www." + url + ("/?sorting_setting=CHRONOLOGICAL" if random.randint(0, 1) else "/"))
    await fb._page.wait_for_load_state()
    ok = url in fb._page.url
    log(f"สถานะการเปิด URL: {'สำเร็จ' if ok else 'ไม่สำเร็จ'} (current={fb._page.url})")
    return ok

async def directgrouppage(fb: FBScrape, url: str):
    # พยายามเข้าหน้ากลุ่มผ่านเมนู "กลุ่ม" ภายใน Facebook
    section(f"พยายามเข้าหน้ากลุ่มผ่านเมนู: {url}")
    await fb._lazy_init()
    try:
        elements = await fb._page.evaluate("MultimodalWebSurfer.getInteractiveRects();")
    except Exception:
        elements = {}
        warn("ไม่สามารถอ่าน InteractiveRects (อาจไม่มีสคริปต์สนับสนุน)")

    status = False
    grupP = await get_element(fb, elements, aria_name="กลุ่ม")
    if grupP is not None:
        log("คลิกเมนู 'กลุ่ม'")
        await fb._execute_tool("click", random_point_in_rect(grupP[0]['rects'][0]))
        await fb._page.wait_for_load_state()
        if "facebook.com/groups/feed" in fb._page.url:
            log("เข้าสู่หน้าฟีดกลุ่มสำเร็จ")
            status = True
        else:
            warn("คลิกเมนู 'กลุ่ม' แต่ไม่ได้ไปยังหน้าฟีดกลุ่ม")

    if status:
        # หากเข้า feed ได้แล้ว ลอง 2 กลยุทธ์: (A) เลือกกลุ่มจากรายการใน feed, (B) กด "ดูทั้งหมด"
        await closebt(fb)
        rand = random.randint(0, 1)
        for _ in range(2):
            if "facebook.com/groups/feed" in fb._page.url and rand == 0:
                # กลยุทธ์ A
                log("กลยุทธ์ A: หาในฟีดกลุ่มโดยตรง")
                EB = False
                elements, grop_list = await filter_element(fb, role="navigation", class_name="x78zum5 xdt5ytf x1iyjqo2 x1n2onr6")
                log(f"รายการกลุ่มที่ตรวจพบในฟีด: {len(grop_list)}")
                for item in grop_list:
                    if url in item[0]:
                        for k, v in elements.items():
                            if v.get('aria_name', '') != '' and v.get('aria_name', '').split('\n')[0] == (item[1][0] if item[1] else ''):
                                log(f"พบกลุ่มเป้าหมาย: {item[1][0] if item[1] else item[0]}")
                                await fb._execute_tool("click", random_point_in_rect(elements[k]['rects'][0]))
                                await fb._page.wait_for_load_state()
                                if item[0].rstrip("/") in fb._page.url:
                                    log("เข้าหน้ากลุ่มสำเร็จ (ผ่านฟีด)")
                                    return True
                                else:
                                    EB = True
                                    break
                        if EB: 
                            break

            if "facebook.com/groups/feed" in fb._page.url and rand == 1:
                # กลยุทธ์ B
                log("กลยุทธ์ B: กด 'ดูทั้งหมด' เพื่อตามหากลุ่ม")
                elements = await fb._page.evaluate("MultimodalWebSurfer.getInteractiveRects();")
                grupP = await get_element(fb, elements, aria_name="ดูทั้งหมด")
                if grupP is not None:
                    log("คลิก 'ดูทั้งหมด'")
                    await fb._execute_tool("click", random_point_in_rect(grupP[0]['rects'][0]))
                    await fb._page.wait_for_load_state()
                    if "facebook.com/groups/joins/?nav_source=tab" in fb._page.url:
                        log("เข้าสู่หน้ารวมกลุ่มแล้ว")
                        EB = False
                        elements, grop_list = await filter_element(fb, role="main")
                        log(f"รายการกลุ่มในหน้า 'ดูทั้งหมด': {len(grop_list)}")
                        for item in grop_list:
                            if url in item[0]:
                                for k, v in elements.items():
                                    if v.get('aria_name', '') != '' and v.get('aria_name', '') != 'ดูกลุ่ม' and v.get('aria_name', '').split('\n')[0] == (item[1][0] if item[1] else ''):
                                        log(f"พบกลุ่มเป้าหมาย: {item[1][0] if item[1] else item[0]}")
                                        await fb._execute_tool("click", random_point_in_rect(elements[k]['rects'][0]))
                                        await fb._page.wait_for_load_state()
                                        if item[0].rstrip("/") in fb._page.url:
                                            log("เข้าหน้ากลุ่มสำเร็จ (ผ่าน 'ดูทั้งหมด')")
                                            return True
                                        else:
                                            EB = True
                                            break
                                if EB: 
                                    break
            rand = int(not rand)

    warn("ไม่สามารถนำทางเข้าหน้ากลุ่มด้วยเมนูได้")
    return False

async def goto_post_page(fb: FBScrape, url: str):
    # เลือกกลยุทธ์แบบสุ่ม (เปิดตรง/เข้าเมนู/รายการแนะนำ/กลับหน้าแรกแล้วลองใหม่) เพื่อไปยังหน้ากลุ่ม
    section(f"นำทางไปหน้าโพสต์ของกลุ่ม: {url}")
    await fb._lazy_init()
    await closebt(fb)
    status = False

    rands = [0, 1, 2, 3]  # สุ่มลำดับกลยุทธ์
    random.shuffle(rands)

    for _ in range(3):
        if not rands:
            break
        rand = rands.pop()

        if rand == 0:
            log("กลยุทธ์: เปิด URL โดยตรง")
            await closebt(fb)
            status = await directourl(fb, url)
            if status:
                return True

        if rand == 1:
            log("กลยุทธ์: เข้าผ่านเมนู 'กลุ่ม'")
            await closebt(fb)
            status = await directgrouppage(fb, url)
            if status:
                return True

        if rand == 2:
            log("กลยุทธ์: ค้นหาจากรายการกลุ่มที่แนะนำ/เข้าร่วม")
            await closebt(fb)
            elements, _= await filter_element(fb, "navigation", aria_name=["รายชื่อกลุ่ม", "ทางลัด"])
            if elements:
                EB = False
                elements, grop_list = await filter_element(fb, "navigation", aria_name=["รายชื่อกลุ่ม", "ทางลัด"])
                log(f"พบรายการกลุ่ม: {len(grop_list)}")
                for item in grop_list:
                    if url in item[0]:
                        for k, v in elements.items():
                            if v.get('aria_name', '').split('\n')[0] == (item[1][0] if item[1] else ''):
                                log(f"พบกลุ่มเป้าหมาย: {item[1][0] if item[1] else item[0]}")
                                await fb._execute_tool("click", random_point_in_rect(elements[k]['rects'][0]))
                                await fb._page.wait_for_load_state()
                                if item[0].rstrip("/") in fb._page.url:
                                    log("เข้าสู่หน้ากลุ่มสำเร็จ")
                                    return True
                                else:
                                    EB = True
                                    break
                        if EB: 
                            break

        if rand == 3:
            # กลับหน้าแรก Facebook แล้วสุ่มกลยุทธ์ใหม่
            log("กลยุทธ์: กลับหน้าแรก Facebook แล้วลองใหม่")
            await closebt(fb)
            elements = await fb._page.evaluate("MultimodalWebSurfer.getInteractiveRects();")
            FB = await get_element(fb, elements, role="link", aria_name="Facebook")
            if FB:
                await fb._execute_tool("click", position=random_point_in_rect(FB[0]['rects'][0]))
                await fb._page.wait_for_load_state()
            rands = [0, 1, 2]
            random.shuffle(rands)

    warn("นำทางไปหน้าโพสต์ของกลุ่มไม่สำเร็จ")
    return status

async def randomtask(fb: FBScrape, region: Optional[str] = None, num_items: Optional[int] = None):
    """
    ทำงาน background แบบ 'random-scrape' (sink)
    - serialize งานด้วย fb._sink_lock: ถ้ามีงานอยู่จะรอคิวจนงานเดิมเสร็จ
    - คงพฤติกรรม cooldown เดิม (SINK_COOLDOWN_SEC)
    - บันทึกผลลัพธ์เป็นไฟล์ JSON ลง BASE_OUTPUT_DIR/_sink
    - ใช้ browser_use Agent + LLM ช่วย simulate การใช้งานเหมือนคน
    """
    await fb._lazy_init()

    # ใช้ lock ต่อโปรไฟล์เพื่อ serialize งาน sink ทั้งหมด
    lock = getattr(fb, "_sink_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        setattr(fb, "_sink_lock", lock)

    async with lock:
        # กันการรันซ้อนพร้อมกับ state เดิม (ภายใต้ lock ปกติจะไม่มีชน แต่อยู่เพื่อความปลอดภัย)
        if getattr(fb, "_sink_running", False):
            log("SINK: พบสถานะกำลังรัน ภายใต้ lock — จะรอจนเสร็จ", "WARN")

        setattr(fb, "_sink_running", True)
        try:
            # ตรวจ cooldown: ป้องกันการยิงงานแบบถี่เกินไป
            now_mono = time.monotonic()
            last_run = getattr(fb, "_sink_last_run", 0.0)
            cooldown_sec = int(os.getenv("SINK_COOLDOWN_SEC", "1800"))
            if last_run and (now_mono - last_run) < cooldown_sec:
                remain = max(0.0, cooldown_sec - (now_mono - last_run))
                return {"ok": False, "reason": "cooldown", "next_in_sec": round(remain, 1)}

            # กำหนดพารามิเตอร์เริ่มต้น
            if region is None:
                region = os.getenv("SINK_REGION", "ประเทศไทย")
            if num_items is None:
                _env_num = os.getenv("SINK_NUM_ITEMS", "").strip()
                try:
                    num_items = int(_env_num) if _env_num else None
                except Exception:
                    num_items = None

            # ไปหน้าเบาๆ (google.com) เพื่อลดภาระก่อนเริ่ม agent
            try:
                await fb._execute_tool("goto", value="google.com")
                await fb._page.wait_for_load_state()
            except Exception:
                pass

            # เตรียม BrowserUser (ถ้ายังไม่มี) โดยหา PID ของ Chromium จากพอร์ต remote debugging
            if not isinstance(fb.browser_user, BrowserUser):
                pid = find_pid_by_port(getattr(fb, "remote_debugging_port", 9222))
                if not pid:
                    pid = find_pid_by_port(9222)
                if not pid:
                    warn("SINK: ไม่พบ Chromium PID ที่เปิด remote debugging — ยกเลิก")
                    return {"ok": False, "reason": "no_browser_pid"}
                fb.browser_user = BrowserUser(browser_pid=pid)

            # สร้างข้อความ task สำหรับ agent
            task = build_random_daily_task(region=region, num_items=num_items)
            log(f"SINK: เริ่ม agent ด้วย task = {task[:120]}{'...' if len(task) > 120 else ''}")

            # เรียก agent.run
            agent_result = await fb.browser_user._run_autoweb(f"{task}")
            try:
                output = agent_result.final_result()
            except Exception:
                output = agent_result

            # เซฟผลลัพธ์
            out_dir = Path(BASE_OUTPUT_DIR).joinpath("_sink")
            out_dir.mkdir(parents=True, exist_ok=True)
            task_id = make_task_id()
            payload = {
                "task_id": task_id,
                "ts": now_ts(),
                "task": task,
                "output": output,
                "region": region,
                "num_items": num_items,
            }
            out_path = str(out_dir.joinpath(f"{task_id}.json"))
            save_json(out_path, payload)
            log(f"SINK: บันทึกผลลัพธ์ -> {out_path}")

            # อัปเดตสถานะเวลาล่าสุดและนับรอบ
            setattr(fb, "_sink_last_run", time.monotonic())
            try:
                setattr(fb, "_sink_count", int(getattr(fb, "_sink_count", 0)) + 1)
            except Exception:
                pass

            return {"ok": True, "task_id": task_id, "output_file": out_path, "output": output}

        except Exception as e:
            # จับ error และบันทึก
            err(f"SINK: ล้มเหลว: {e}")
            record_error_json(CURRENT_USER_SAFE.get(), "sink", e)
            return {"ok": False, "error": str(e)}

        finally:
            # กลับหน้าเบาๆ และปลด running flag เสมอ
            try:
                pid = find_pid_by_port(getattr(fb, "remote_debugging_port", 9222))
                if pid: fb.browser_user = BrowserUser(browser_pid=pid)
                await fb._execute_tool("goto", value="google.com")
                await fb._page.wait_for_load_state()
            except Exception:
                pass
            setattr(fb, "_sink_running", False)

async def gethtml_feed_element(fb, idx):
    # ดึง element ของโพสต์ในฟีดตาม index (aria-posinset = idx+1)
    html_content_local = await fb._page.content()
    soup_local = BeautifulSoup(html_content_local, 'html.parser')
    if not soup_local:
        return None
    feed_container_local = soup_local.find(attrs={"role": "feed"})
    if not feed_container_local:
        return None
    return feed_container_local.find(attrs={"aria-posinset": f"{idx + 1}"})

async def getdata(fb: FBScrape, num_post: int = 3, last_post=None):
    # ดึงข้อมูลโพสต์จากหน้ากลุ่มที่เปิดอยู่:
    # ขั้นตอนหลัก:
    # 1) ปิด modal, ปรับเรียงลำดับโพสต์เป็น "โพสต์ใหม่"
    # 2) สกรอลล์เพื่อให้มีโพสต์เพียงพอ
    # 3) กลับไปบนสุด แล้วย้อนลงทีละโพสต์: กด "ดูเพิ่มเติม" (ถ้ามี) แล้ว parse ข้อมูลด้วย BS4
    section(f"ดึงข้อมูลโพสต์ | ต้องการจำนวนโพสต์: {num_post if num_post else 'ทั้งหมดที่หาได้'}")
    await fb._lazy_init()

    # ปิด modal "ปิด" ถ้าขึ้นมา
    elements = await fb._page.evaluate("MultimodalWebSurfer.getInteractiveRects();")
    x = await get_element(fb, elements, role="button", aria_name=["Close", "ปิด"], class_name="x1i10hfl xjqpnuy xc5r6h4 xqeqjp1 x1phubyo x13fuv20 x18b5jzi x1q0q8m5 x1t7ytsu x1ypdohk xdl72j9 x2lah0s xe8uvvx xdj266r x14z9mp xat24cr x1lziwak x2lwn1j xeuugli x16tdsg8 x1hl2dhg xggy1nq x1ja2u2z x1t137rt x1q0g3np x87ps6o x1lku1pv x1a2a7pz x6s0dn4 x1iwo8zk x1033uif x179ill4 x1b60jn0 x972fbf x10w94by x1qhh985 x14e42zd x9f619 x78zum5 xl56j7k xexx8yu xyri2b x18d9i69 x1c1uobl x1n2onr6 xc9qbxq x14qfxbe x1qhmfi1")
    if x:
        log("พบ modal/ปุ่มปิด => ทำการปิด")
        await fb._execute_tool("click", random_point_in_rect(x[0]['rects'][0]))
        await fb._page.wait_for_timeout(250)

    # เปลี่ยน sorting เป็น "โพสต์ใหม่"
    elements = await fb._page.evaluate("MultimodalWebSurfer.getInteractiveRects();")
    x = await get_element(fb, elements, aria_name=["เกี่ยวข้องมากที่สุด"])
    if x:
        log("พบ เกี่ยวข้องมากที่สุด")
        await fb._execute_tool("click", random_point_in_rect(x[0]['rects'][0]))
        await fb._page.wait_for_timeout(250)

    elements = await fb._page.evaluate("MultimodalWebSurfer.getInteractiveRects();")
    x = await get_element(fb, elements, aria_name=["โพสต์ใหม่\nแสดงโพสต์ล่าสุดก่อน"])
    if x:
        log("พบ โพสต์ใหม่")
        await fb._execute_tool("click", random_point_in_rect(x[0]['rects'][0]))
        await fb._page.wait_for_timeout(250)

    await fb._page.wait_for_load_state()
    await fb._human_sleep(3, 5)

    # สกรอลล์เพื่อโหลดโพสต์ให้เพียงพอ
    safety = 0
    while True:
        await closebt(fb)
        html_content = await fb._page.content()
        soup = BeautifulSoup(html_content, 'html.parser')
        if not soup:
            warn("อ่าน DOM ไม่สำเร็จ")
            return None

        feed_container = soup.find(attrs={"role": "feed"})
        if not feed_container:
            warn("ไม่พบคอนเทนเนอร์ฟีด")
            break

        post_groups = feed_container.find_all(attrs={"role": "article"})
        post_comp = False

        # หาก last_post ถูกส่งมา: พยายามจำกัด num_post ตามโพสต์ล่าสุดที่มีอยู่ในไฟล์เดิม (ลดซ้ำ)
        if last_post is not None:
            _,_ , shf = extract_key_values_from_latest_json(folder=last_post + '/', target_key="share_url")

        if shf is not None and bool(shf):
            for post in post_groups:
                if last_post is not None:
                    if shf is not None:
                        anchor_post = post.select_one('a[href*="/posts/"]')
                        if anchor_post and anchor_post.get('href'):
                            href_url = (anchor_post.get('href') or "").split("/?")[0]
                            if int(post.get('aria-posinset', "0")) != 0:
                                if href_url in shf:
                                    # จำกัดจำนวนโพสต์ไม่ให้เกินโพสต์ก่อนหน้าที่พบ
                                    num_post = num_post if num_post <= int(post.get('aria-posinset', "0")) - 1 else int(post.get('aria-posinset', "0")) - 1
                                    num_post = 1 if num_post <= 0 else num_post
                                    post_comp = True
                                    break

        # ถ้าโหลดโพสต์ได้ครบตาม num_post แล้ว: ออกจากลูป
        for post in post_groups:
            if int(post.get('aria-posinset', "0")) == num_post:
                post_comp = True
                break

        elements = await fb._page.evaluate("MultimodalWebSurfer.getInteractiveRects();")
        see_more = await get_element(
            fb,
            elements,
            role="button",
            aria_name="ดูเพิ่มเติม"
        )
        if see_more:
            log(f"พบปุ่ม 'ดูเพิ่มเติม' {len(see_more)} ตำแหน่ง => ทำการคลิกทั้งหมด")
            await fb._execute_tool("click", [see_more[0]['rects'][0]['x'] + see_more[0]['rects'][0]['width'] / 2, see_more[0]['rects'][0]['y'] + see_more[0]['rects'][0]['height'] / 2])
            await fb._page.wait_for_timeout(120)

        if (num_post == 0 or len(post_groups) >= num_post) and post_comp: break

        # เลื่อนหน้าจอเพิ่มเพื่อโหลดโพสต์เพิ่ม
        delta = await fb._page.evaluate("() => Math.floor(window.innerHeight * 1.5)")
        elements, _= await filter_element(fb, role="main")
        if elements and len(elements):
            rect = elements[random.choice(list(elements.keys()))]['rects'][0]
            await fb._execute_tool("scroll", random_point_in_rect(rect), value=delta)
        else:
            center = await fb._page.evaluate("() => ({x: Math.floor(window.innerWidth/2), y: Math.floor(window.innerHeight/2)})")
            position = [center["x"], center["y"]]
            await fb._execute_tool("scroll", position, value=delta)

        safety += 1
        if safety > 20:
            warn("เลื่อนหน้าจอครบลิมิตความปลอดภัยแล้ว (หยุด)")
            break

    # กลับไปบนสุดของหน้า
    await fb._execute_tool("keyboard", value="Home")

    output = []
    html_content = await fb._page.content()
    soup = BeautifulSoup(html_content, 'html.parser')
    if not soup:
        warn("อ่าน DOM ไม่สำเร็จ (รอบสุดท้าย)")
        return None

    feed_container = soup.find(attrs={"role": "feed"})
    if feed_container:
        post_groups_init = feed_container.find_all(attrs={"role": "article"})
        total_init = len(post_groups_init)
        log(f"จำนวนกลุ่มโพสต์ในฟีด (init): {total_init}")

        # ไล่เก็บทีละโพสต์
        for idx in range(total_init):
            group = await gethtml_feed_element(fb, idx)
            if group is None:
                warn("ไม่พบ element ของโพสต์ตาม index (หยุดวน)")
                break

            # คำนวณระยะเลื่อนเฉพาะโพสต์นี้ให้อยู่ในมุมมองก่อน (เพื่อกด 'ดูเพิ่มเติม' ได้)
            info = await get_scroll_distance_to_element(fb._page, f"[aria-posinset='{idx + 1}']", 0)
            if not info or not info.get('found'):
                continue
            delta = info['distance_px']
            if delta != 0:
                elements, _= await filter_element(fb, role="main")
                if elements and len(elements):
                    rect = elements[random.choice(list(elements.keys()))]['rects'][0]
                    await fb._execute_tool("scroll", random_point_in_rect(rect), value=delta - 160)
                else:
                    center = await fb._page.evaluate("() => ({x: Math.floor(window.innerWidth/2), y: Math.floor(window.innerHeight/2)})")
                    position = [center["x"], center["y"]]
                    await fb._execute_tool("scroll", position, value=delta - 160)

            # กด 'ดูเพิ่มเติม' ในโพสต์ (ถ้ามี)
            elements = await fb._page.evaluate("MultimodalWebSurfer.getInteractiveRects();")
            see_more = await get_element(
                fb,
                elements,
                role="button",
                aria_name="ดูเพิ่มเติม",
            )
            if see_more:
                log(f"พบปุ่ม 'ดูเพิ่มเติม' {len(see_more)} ตำแหน่ง => ทำการคลิกทั้งหมด")
                await fb._execute_tool("click", [see_more[0]['rects'][0]['x'] + see_more[0]['rects'][0]['width'] / 2, see_more[0]['rects'][0]['y'] + see_more[0]['rects'][0]['height'] / 2])
                await fb._page.wait_for_timeout(120)

            # อ่าน DOM ใหม่หลังคลิก และ extract ข้อมูลโพสต์
            group_after = await gethtml_feed_element(fb, idx)
            if group_after is None:
                break
            text = extract_post_from_like_element(group_after)
            output.append(text)

            if len(output) >= num_post:
                log("ครบตามจำนวนโพสต์ที่ต้องการ (หยุด)")
                break

    log(f"สรุปจำนวนโพสต์ที่ดึงได้: {len(output)}")
    return output[:num_post] if num_post else output

# ============================================================
# Helpers: ตรวจโปรไฟล์
# ============================================================
def profile_has_data(profile_dir: str) -> bool:
    # ตรวจว่าโฟลเดอร์โปรไฟล์มีอยู่และไม่ว่าง (คาดว่ามีข้อมูล session/cookie)
    try:
        p = Path(profile_dir)
        return p.exists() and any(p.iterdir())
    except Exception:
        return False

# ============================================================
# PostScrape (ปรับไม่ auto-login ใน run_task และ fallout เมื่อ session หลุด)
# ============================================================
class PostScrape:
    # คลาสห่อหุ้มงาน scraping หลัก: ตรวจ login/นำทาง/ดึงโพสต์
    def __init__(self, fb: FBScrape, browser_data_dir: Optional[str], username_full: Optional[str], password: Optional[str]):
        section("สร้างอ็อบเจ็กต์ PostScrape")
        self.fb = fb
        self.browser_data_dir = browser_data_dir
        self.username_full = username_full
        self.password = password
        self.login_state = False
        log(f"username_full={'(กำหนดแล้ว)' if self.username_full else '(ไม่กำหนด)'}")

    async def _get_context(self):
        # ดึง BrowserContext ปัจจุบันจาก FBScrape
        await self.fb._lazy_init()
        ctx = getattr(self.fb, "_context", None)
        if ctx is None and getattr(self.fb, "_page", None):
            try:
                ctx = self.fb._page.context
            except Exception:
                ctx = None
        return ctx

    async def is_logged_in(self) -> bool:
        # ตรวจว่า session ยังล็อกอินอยู่หรือไม่:
        # - ดู cookie c_user ใน domain facebook.com
        # - หรือดู URL ว่าอยู่หน้า login หรือไม่
        await self.fb._lazy_init()
        try:
            ctx = await self._get_context()
            cookies = []
            if ctx is not None:
                cookies = await ctx.cookies()
            elif getattr(self.fb, "_page", None):
                # fallback: อ่าน document.cookie (อาจไม่ครบถ้วน)
                doc_cookie = await self.fb._page.evaluate("() => document.cookie")
                for kv in (doc_cookie or "").split(";"):
                    name = kv.split("=")[0].strip()
                    val = "=".join(kv.split("=")[1:]).strip() if "=" in kv else ""
                    cookies.append({"name": name, "value": val, "domain": "facebook.com"})

            for c in cookies:
                if c.get("name") == "c_user" and c.get("value"):
                    dom = (c.get("domain") or "")
                    if "facebook.com" in dom or dom.endswith(".facebook.com"):
                        return True
        except Exception as e:
            warn(f"is_logged_in: cookie check failed: {e}")

        try:
            pg = getattr(self.fb, "_page", None)
            if pg:
                url = pg.url or ""
                if "facebook.com/login" in url or "/login" in url:
                    return False
        except Exception:
            pass

        return False

    async def ensure_login(self):
        """
        ใช้ใน create_profile เท่านั้น: พยายามเข้าสู่ระบบเมื่อยังไม่ได้ login
        - ถ้า login แล้วจะข้าม
        - ถ้าไม่ login และไม่มี username/password -> จะไม่สามารถ login ให้ได้
        """
        section("ตรวจสอบสถานะการเข้าสู่ระบบ")
        await self.fb._lazy_init()

        if await self.is_logged_in():
            # ถ้าอยู่ domain อื่นให้พากลับไปหน้า FB
            if "facebook.com" not in self.fb._page.url:
                await self.fb._execute_tool("goto", value="facebook.com/?locale=th_TH")
                await self.fb._page.wait_for_load_state()
            self.login_state = True
            log("พบ session เดิมที่ยังใช้งานได้ => ข้าม login")
            return

        # ยังไม่ได้ login -> เปิดหน้า login
        try:
            await self.fb._execute_tool("goto", value="facebook.com/login/?locale=th_TH")
            await self.fb._page.wait_for_load_state()
        except Exception as e:
            warn(f"เปิดหน้า home ล้มเหลว: {e}")

        cur_url = self.fb._page.url if getattr(self.fb, "_page", None) else ""
        if "facebook.com/login" in cur_url or "/login" in cur_url:
            log("จำเป็นต้องเข้าสู่ระบบ")
            if not self.username_full or not self.password:
                self.login_state = False
                warn("ไม่ได้กำหนด username/password")
                return
            ok = await login(self.fb, self.username_full, self.password)
            self.login_state = bool(ok)
            log(f"ผลการเข้าสู่ระบบ: {'สำเร็จ' if self.login_state else 'ล้มเหลว'}")
        else:
            self.login_state = True
            log("อยู่ในสถานะที่เข้าสู่ระบบแล้ว (ผ่านหน้า home)")

    async def run_task(self, num_post: int, group: Sequence[Dict[str, str]], last_post: str = None):
        """
        ดึงโพสต์จากแต่ละกลุ่มตามรายการ group:
        - ไม่พยายาม login อัตโนมัติ (ต้องเรียก create-profile มาก่อน)
        - ถ้า session หลุดระหว่างทำ -> ยกเลิกทั้งงาน (fallout)
        - บันทึก error ต่อกลุ่มและไปกลุ่มถัดไป (ยกเว้นกรณี FB_SESSION_LOST)
        """
        section("เริ่มงานดึงข้อมูลโพสต์จากกลุ่ม")
        if not group:
            warn("ไม่มีรายการกลุ่มให้ทำงาน")
            return []

        await self.fb._lazy_init()

        # ต้องอยู่ในสถานะ login ตั้งแต่ต้น
        if not await self.is_logged_in():
            self.login_state = False
            err("FB_SESSION_LOST: ยังไม่ได้ login หรือ session หมดอายุ")
            raise RuntimeError("FB_SESSION_LOST: ยังไม่ได้ login หรือ session หมดอายุ")

        # ensure อยู่หน้า FB
        if "facebook.com" not in self.fb._page.url:
            try:
                await self.fb._execute_tool("goto", value="facebook.com/?locale=th_TH")
                await self.fb._page.wait_for_load_state()
            except Exception as e:
                warn(f"นำทางไปหน้า FB ล้มเหลว (จะลองต่อ): {e}")

        self.login_state = True
        data = []

        for idx, g in enumerate(group, start=1):
            # เตรียมค่า URL และชื่อกลุ่ม
            g_url = g.get("url") # .replace("facebook.com", "facebook.com")  # เหมือน placeholder ไม่ได้ทำอะไร
            g_url = re.sub(r"^https?://(?:(www|web)\.)?", "", g_url).strip("/")
            g_chat_id = g.get("chat_id","0")

            # สำหรับ check group name ว่าได้ส่งมาหรือไม่
            if g.get("name", 0) == 0 or g.get("name", "string") == "string": g_name = g_url
            else: g_name = g.get("name") or g_url

            section(f"[{idx}/{len(group)}] กลุ่ม: {g_name} | URL: {g_url}")
            try:
                # นำทางไปหน้ากลุ่มด้วยกลยุทธ์ต่างๆ
                status = await goto_post_page(self.fb, g_url)
                if g_url not in self.fb._page.url:
                    status = False
                if not status:
                    warn("กลยุทธ์นำทางล้มเหลว => fallback เปิด URL โดยตรง")
                    status = await directourl(self.fb, g_url)
                if not status:
                    raise RuntimeError(f"Not Complete: cannot navigate to {g_url}")

                # ตรวจซ้ำว่ายัง login อยู่หรือไม่
                if not await self.is_logged_in():
                    raise RuntimeError("FB_SESSION_LOST: session หลุดระหว่างทำงาน — กรุณา login ใหม่ด้วย /create-profile")

                # ดึงชื่อกลุ่มจาก DOM ถ้าชื่อยังเป็นค่าเริ่มต้น
                html_content = await self.fb._page.content()
                soup = BeautifulSoup(html_content, 'html.parser')
                page_name = soup.find(attrs={"href": re.compile(f"{g_url}", re.IGNORECASE), "tabindex": "0", "role": "link"})
                if page_name is not None and g_name == g_url:
                    g_name = page_name.text
                    if g_name == "": g_name = page_name.get("aria-label", f"{g_url}")

                # ดึงโพสต์
                posts = await getdata(self.fb, num_post, last_post)
                data.append([g_url, posts, g_name,g_chat_id])
                log(f"ดึงข้อมูลเสร็จสำหรับกลุ่ม: {g_name} | จำนวนโพสต์: {len(posts) if posts else 0}")

            except Exception as e:
                # บันทึก error ต่อกลุ่ม
                err(f"เกิดข้อผิดพลาดที่กลุ่ม {g_name}: {e}")
                record_error_json(CURRENT_USER_SAFE.get(), "PostScrape.run_task.group", e, {"group_url": g_url, "group_name": g_name})
                data.append([g_url, []])

                # ถ้าเป็น session หลุด ให้หยุดทั้งงานทันที
                if "FB_SESSION_LOST" in str(e):
                    raise
                continue

        # log สรุปแบบปลอดภัย
        for e, i in enumerate(data):
            try:
                cnt = len(i[1]) if isinstance(i[1], list) else 0
                log(f"post: {e} => {cnt}")
            except Exception:
                pass

        section("งานเสร็จสิ้นทั้งหมด")
        return data

# ============================================================
# Session Manager (ต่อ username_full): คิว + timeout + ปิด browser
# ============================================================
class UserSession:
    # จัดการ session ต่อผู้ใช้หนึ่งคน:
    # - เก็บโฟลเดอร์โปรไฟล์
    # - ดูแล FBScrape instance
    # - มี lock serialize งาน
    # - บริหาร last_used/closed
    def __init__(self, username_full: str):
        self.username_full = username_full
        self.username_safe = to_safe_name(username_full)
        d = user_dirs(self.username_safe)
        self.browser_data_dir = d["profile_dir"]
        self.out_dir = d["out_dir"]
        self.fb: Optional[FBScrape] = None
        self.lock = asyncio.Lock()
        self.last_used = time.monotonic()
        self.closed = False

    def touch(self):
        # อัปเดตเวลาใช้งานล่าสุด
        self.last_used = time.monotonic()

    async def ensure_fb(self) -> FBScrape:
        # สร้าง/คืนค่า FBScrape สำหรับ session นี้
        if self.closed:
            raise RuntimeError("Session ถูกปิดไปแล้ว")
        if self.fb is None:
            try:
                self.fb = FBScrape(self.browser_data_dir, verbose=VERBOSE)
                await self.fb._lazy_init()
                log(f"[{self.username_safe}] เปิดเบราว์เซอร์ด้วยโปรไฟล์: {self.browser_data_dir}")
            except Exception as e:
                err(f"[{self.username_safe}] เปิดเบราว์เซอร์ล้มเหลว: {e}")
                record_error_json(self.username_safe, "UserSession.ensure_fb", e)
                raise
        return self.fb

    # NEW: รีสตาร์ทเบราว์เซอร์ (Re-Chrome)
    async def restart_browser(self):
        """
        ปิดและเปิดเบราว์เซอร์ใหม่ตามโปรไฟล์นี้ (ทำภายใต้ session lock เท่านั้น)
        - ใช้เมื่อ policy ระบุให้รีสตาร์ทเพื่อลด state สะสม
        """
        if self.closed:
            raise RuntimeError("Session ถูกปิดไปแล้ว (ไม่สามารถรีสตาร์ทได้)")
        try:
            if self.fb:
                try:
                    await self.fb.close()
                except Exception as e:
                    warn(f"[{self.username_safe}] ปิดเบราว์เซอร์ก่อนรีสตาร์ทล้มเหลว: {e}")
                self.fb = None
            self.fb = FBScrape(self.browser_data_dir, verbose=VERBOSE)
            await self.fb._lazy_init()
            log(f"[{self.username_safe}] RE-CHROME: เปิดเบราว์เซอร์ใหม่สำเร็จ")
        except Exception as e:
            err(f"[{self.username_safe}] RE-CHROME: ล้มเหลว: {e}")
            record_error_json(self.username_safe, "UserSession.restart_browser", e)
            raise

    async def close(self):
        # ปิด session (ปิดบราว์เซอร์ + มาร์ค closed)
        if self.closed:
            return
        try:
            if self.fb:
                await self.fb.close()
                log(f"[{self.username_safe}] ปิดเบราว์เซอร์แล้ว")
        except Exception as e:
            err(f"[{self.username_safe}] ปิดเบราว์เซอร์ล้มเหลว: {e}")
            record_error_json(self.username_safe, "UserSession.close", e)
        finally:
            self.fb = None
            self.closed = True

class SessionManager:
    # ตัวจัดการ session ทั้งหมดในระบบ:
    # - จัดเก็บ mapping username_full -> UserSession
    # - จัดการคิวงานแบบ serialize ต่อผู้ใช้ (ผ่าน sess.lock)
    # - มี janitor loop ปิด session ที่ idle เกินเวลาที่กำหนด
    def __init__(self, idle_timeout_sec: int = 300):
        self.idle_timeout_sec = idle_timeout_sec
        self.sessions: Dict[str, UserSession] = {}
        self._janitor_task: Optional[asyncio.Task] = None
        self._stop_evt = asyncio.Event()

    def get_or_create(self, username_full: str) -> UserSession:
        # ดึง session เดิมหรือสร้างใหม่ถ้ายังไม่มี
        sess = self.sessions.get(username_full)
        if not sess or sess.closed:
            sess = UserSession(username_full)
            self.sessions[username_full] = sess
        return sess

    async def run_for_user(self, username_full: str, coro_func):
        # รันงานหนึ่งชิ้นภายใต้ session lock ของผู้ใช้นั้น
        sess = self.get_or_create(username_full)
        async with sess.lock:
            sess.touch()
            try:
                return await coro_func(sess)
            finally:
                sess.touch()

    async def run_for_sys(self, username_full: str, coro_func):
        # รันงานระบบ (เช่น sink) โดยแชร์ lock/user เดียวกัน เพื่อ serialize กับงานหลัก
        sess = self.get_or_create(username_full)
        async with sess.lock:
            try:
                return await coro_func(sess)
            finally:
                pass

    async def janitor_loop(self):
        # ลูป background:
        # - หาก session ใด idle นานกว่า sink_idle_sec แต่ยังไม่ถึง idle_timeout_sec -> trigger random sink
        # - หาก idle เกิน idle_timeout_sec -> ปิด session
        try:
            sink_idle_sec = int(os.getenv("SINK_IDLE_SEC", "1500"))
            while not self._stop_evt.is_set():
                now = time.monotonic()
                to_close = []
                for uname_full, sess in list(self.sessions.items()):
                    if sess.closed:
                        to_close.append(uname_full)
                        continue

                    idle = now - sess.last_used

                    # Trigger sink ระหว่าง idle (serialize ผ่าน run_for_user)
                    if (idle >= sink_idle_sec
                        and idle < self.idle_timeout_sec
                        and sess.fb is not None):

                        async def _run_sink_for_user(s=sess, m=self, uname=uname_full):
                            token_user = CURRENT_USER_SAFE.set(s.username_safe)
                            token_task = CURRENT_TASK_ID.set(make_task_id())
                            try:
                                async def _sink_job(sess_in: UserSession):
                                    await randomtask(sess_in.fb)  # ภายในยังล็อกด้วย fb._sink_lock
                                await m.run_for_sys(uname, _sink_job)  # คิวร่วมกับงาน scrape-posts
                            except Exception as e:
                                err(f"[Janitor->sink] {s.username_safe}: {e}")
                            finally:
                                CURRENT_USER_SAFE.reset(token_user)
                                CURRENT_TASK_ID.reset(token_task)

                        # เพื่อหลีกเลี่ยงซ้ำซ้อนมากไป ใช้สถานะ _sink_running ของ fb เป็น guard เบื้องต้น
                        if sess.fb is not None and not getattr(sess.fb, "_sink_running", False):
                            asyncio.create_task(_run_sink_for_user())

                    # ปิด session ถ้า idle เกิน timeout และไม่มีงาน (lock) อยู่
                    if idle > self.idle_timeout_sec and not sess.lock.locked():
                        to_close.append(uname_full)

                # ปิด session ที่ถูกเลือก
                for uname_full in to_close:
                    sess = self.sessions.get(uname_full)
                    if sess and not sess.closed and not sess.lock.locked():
                        log(f"[Janitor] Close idle session: {sess.username_safe} (idle {round(now - sess.last_used, 2)}s)")
                        await sess.close()
                        self.sessions.pop(uname_full, None)

                await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            pass

    def start(self):
        # เริ่ม janitor task หนึ่งตัว
        if self._janitor_task is None:
            self._janitor_task = asyncio.create_task(self.janitor_loop())
            log("Session janitor started.")

    async def stop(self):
        # สั่งหยุด janitor และปิด session ทั้งหมด
        self._stop_evt.set()
        if self._janitor_task:
            self._janitor_task.cancel()
            try:
                await self._janitor_task
            except asyncio.CancelledError:
                pass

        for sess in list(self.sessions.values()):
            try:
                await sess.close()
            except Exception:
                pass

        self.sessions.clear()
        log("Session janitor stopped. All sessions closed.")

# ============================================================
# FastAPI Models
# ============================================================
# สร้าง Pydantic models สำหรับ validate request/response ของแต่ละ endpoint

# --- Request Models ---
class GroupItem(BaseModel):
    url: str = Field(..., description="ลิงก์ URL ของกลุ่ม Facebook (เช่น https://www.facebook.com/groups/123)")
    name: Optional[str] = Field(None, description="ชื่อกลุ่มเพื่อช่วยระบุอ่านง่าย หากไม่ทราบหรือไม่จำเป็นสามารถเว้นว่างได้")
    chat_id : Optional[str] = Field(None, description="ไอดีของกลุ่มTgที่ต้องการให้แจ้งเตือน")

class ScrapePostsRequest(BaseModel):
    username: str = Field(..., description="ชื่อผู้ใช้ที่ใช้ผูกกับโปรไฟล์/เซสชัน")
    num_post: int = Field(3, ge=1, le=50, description="จำนวนโพสต์สูงสุดต่อกลุ่มที่จะดึง (ช่วงที่รองรับ 1–50)")
    group: List[GroupItem] = Field(..., description="รายการกลุ่มที่จะดึงข้อมูล โดยระบุ URL (และ name ถ้ามี)")

class CreateProfileRequest(BaseModel):
    username: str = Field(..., description="ชื่อผู้ใช้สำหรับ Facebook ที่จะใช้สร้าง/อ้างิงโปรไฟล์ในระบบ")
    password: str = Field(..., description="รหัสผ่านของบัญชี Facebook")

class RandomScrapeRequest(BaseModel):
    username: str = Field(..., description="ชื่อผู้ใช้ที่ใช้ผูกกับโปรไฟล์/เซสชัน")
    region: str = Field("ประเทศไทย", description="ภูมิภาค/ประเทศบริบทของการคัดเลือกคอนเทนต์แบบสุ่ม")
    num_items: Optional[int] = Field(None, ge=1, le=10, description="จำนวนรายการคอนเทนต์ที่ต้องการ")

class DeleteProfileRequest(BaseModel):
    username: str = Field(..., description="ชื่อผู้ใช้ที่ใช้ผูกกับโปรไฟล์/เซสชัน")
    force: bool = Field(False, description="ตั้งค่า True เพื่อลบแม้มีงานกำลังทำงาน (จะพยายามปิดเซสชันก่อน)")
    all: bool = Field(False, description="ตั้งค่า True เพื่อลบไฟล์ทั้งหมดที่เกี่ยวข้อง (profile + logs + outputs)")

class ScrapePostsWebhookRequest(BaseModel):
    username: str = Field(..., description="ชื่อผู้ใช้ที่ใช้ผูกกับโปรไฟล์/เซสชัน")
    num_post: int = Field(3, ge=1, le=50, description="จำนวนโพสต์สูงสุดต่อกลุ่มที่จะดึง")
    group: List[GroupItem] = Field(..., description="รายการกลุ่มที่จะดึงข้อมูล โดยระบุ URL (และ name ถ้ามี)")
    webhook_url: str = Field(..., description="URL ปลายทางที่ระบบจะ POST JSON ผลลัพธ์ไปเมื่อทำงานเสร็จ")

class SnapshotRequest(BaseModel):
    username: str = Field(..., description="ชื่อผู้ใช้ที่ต้องการดูภาพหน้าจอ")

# --- Response Models ---
class ErrorResponse(BaseModel):
    detail: str = Field(..., description="ข้อความอธิบายข้อผิดพลาด")

class GroupScrapeSummary(BaseModel):
    group_url: Optional[str] = Field(None, description="ลิงก์ URL ของกลุ่มที่ดึงข้อมูล")
    group_name: Optional[str] = Field(None, description="ชื่อกลุ่มที่ดึงข้อมูล")
    posts_count: int = Field(..., description="จำนวนโพสต์ที่ดึงได้จากกลุ่มนี้")
    posts: List[Any] = Field(..., description="รายการข้อมูลโพสต์ที่ดึงได้")

class CreateProfileResponse(BaseModel):
    ok: bool = Field(..., description="สถานะรวมของคำสั่ง")
    task_id: str = Field(..., description="รหัสเฉพาะสำหรับงานนี้")
    username_safe: str = Field(..., description="ชื่อผู้ใช้หลังจากการทำ Sanitize (ใช้เป็นชื่อโฟลเดอร์)")
    profile_existed: bool = Field(..., description="สถานะว่าโปรไฟล์นี้เคยมีอยู่ก่อนหรือไม่")
    message: str = Field(..., description="ข้อความสรุปผลการทำงาน")

class ScrapePostsResponse(BaseModel):
    ok: bool = Field(..., description="สถานะรวมของการดึงโพสต์")
    task_id: str = Field(..., description="รหัสเฉพาะสำหรับงานนี้")
    username_safe: str = Field(..., description="ชื่อผู้ใช้หลังจากการทำ Sanitize")
    output_file: str = Field(..., description="พาธของไฟล์ผลลัพธ์ที่ถูกบันทึกไว้บนเซิร์ฟเวอร์")
    result_summary: List[GroupScrapeSummary] = Field(..., description="สรุปผลลัพธ์การดึงข้อมูลแยกตามกลุ่ม")

class RandomScrapeResponse(BaseModel):
    username_safe: str = Field(..., description="ชื่อผู้ใช้ (Sanitized) ที่เชื่อมโยงกับงานนี้")
    ok: bool = Field(..., description="สถานะความสำเร็จของงาน")
    reason: Optional[str] = Field(None, description="เหตุผลในกรณีที่ `ok` เป็น `false` (เช่น 'cooldown')")
    next_in_sec: Optional[float] = Field(None, description="ระยะเวลา (วินาที) ที่ต้องรอก่อนจะรันงานประเภทนี้ได้อีกครั้ง")
    task_id: Optional[str] = Field(None, description="รหัสเฉพาะของงาน (ถ้าเริ่มสำเร็จ)")
    output_file: Optional[str] = Field(None, description="พาธไฟล์ผลลัพธ์ (ถ้าเริ่มสำเร็จ)")
    output: Optional[Any] = Field(None, description="ผลลัพธ์ที่ได้จาก Agent (ถ้าเริ่มสำเร็จ)")
    error: Optional[str] = Field(None, description="ข้อความข้อผิดพลาด (ถ้าเกิด exception)")

class SessionInfo(BaseModel):
    username_full: str = Field(..., description="ชื่อผู้ใช้เต็ม")
    username_safe: str = Field(..., description="ชื่อผู้ใช้ที่ผ่านการ Sanitize")
    browser_data_dir: str = Field(..., description="พาธของโฟลเดอร์โปรไฟล์")
    locked: bool = Field(..., description="สถานะว่ามีงานกำลังรันในเซสชันนี้หรือไม่")
    last_used_sec_ago: float = Field(..., description="เวลาที่ไม่ได้ใช้งานล่าสุด (วินาที)")
    closed: bool = Field(..., description="สถานะว่าเซสชันถูกปิดไปแล้วหรือไม่")

class SessionsListResponse(BaseModel):
    sessions: List[SessionInfo] = Field(..., description="รายการเซสชันทั้งหมดที่กำลังทำงานอยู่")

class CloseSessionResponse(BaseModel):
    ok: bool = Field(..., description="สถานะความสำเร็จของการปิดเซสชัน")
    message: str = Field(..., description="ข้อความสรุปผล")

class ProfileSessionState(BaseModel):
    active: bool = Field(..., description="โปรไฟล์นี้มีเซสชันที่ทำงานอยู่ในขณะนี้หรือไม่")
    locked: bool = Field(..., description="เซสชันของโปรไฟล์นี้กำลังมีงานรันอยู่หรือไม่")

class ProfileInfo(BaseModel):
    username_safe: str = Field(..., description="ชื่อโปรไฟล์ (username ที่ผ่านการ Sanitize)")
    profile_dir: str = Field(..., description="พาธเต็มของโฟลเดอร์โปรไฟล์")
    exists: bool = Field(..., description="สถานะการมีอยู่ของโฟลเดอร์")
    nonempty: bool = Field(..., description="สถานะว่าโฟลเดอร์มีข้อมูลหรือไม่")
    mtime: Optional[str] = Field(None, description="เวลาแก้ไขล่าสุด (ISO 8601 UTC)")
    ctime: Optional[str] = Field(None, description="เวลาสร้าง (ISO 8601 UTC)")
    size_bytes: Optional[int] = Field(None, description="ขนาดโดยประมาณของโฟลเดอร์ (ไบต์)")
    session: ProfileSessionState = Field(..., description="สถานะเซสชันที่เกี่ยวข้องกับโปรไฟล์นี้")

class ProfilesListResponse(BaseModel):
    ok: bool = Field(..., description="สถานะความสำเร็จของการดึงข้อมูล")
    profiles_dir: str = Field(..., description="พาธหลักที่เก็บโปรไฟล์ทั้งหมด")
    count: int = Field(..., description="จำนวนโปรไฟล์ทั้งหมดที่พบ")
    profiles: List[ProfileInfo] = Field(..., description="รายการข้อมูลของแต่ละโปรไฟล์")

class WebhookQueuedResponse(BaseModel):
    ok: bool = Field(..., description="สถานะการรับงานเข้าคิวสำเร็จ (จะเป็น `True` เสมอหาก request ผ่าน validation)")
    task_id: str = Field(..., description="รหัสเฉพาะสำหรับงานที่ถูกสร้างขึ้นนี้")
    username_safe: str = Field(..., description="ชื่อผู้ใช้ (Sanitized) ที่เชื่อมโยงกับงานนี้")
    status: Literal["queued", "running"] = Field(..., description="สถานะปัจจุบันของงาน: 'running' (เริ่มทันที) หรือ 'queued' (เข้าคิวรอ)")
    queue_position: int = Field(..., description="ลำดับในคิว (1 หมายถึงกำลังทำงาน)")
    message: str = Field(..., description="ข้อความสรุปสถานะการรับงาน")
    webhook_url: str = Field(..., description="URL ที่ระบบจะส่งผลลัพธ์กลับไปเมื่อทำงานเสร็จ")

class WebhookResultOk(BaseModel):
    ok: bool = Field(True, description="สถานะสำเร็จ (จะเป็น `True` เสมอ)")
    task_id: str = Field(..., description="รหัสงานที่ทำเสร็จ")
    username_safe: str = Field(..., description="ชื่อผู้ใช้ของงาน")
    completed_at: str = Field(..., description="เวลาที่ทำงานเสร็จสิ้น (YYYY-MM-DD HH:MM:SS)")
    output_file: str = Field(..., description="พาธไฟล์ผลลัพธ์บนเซิร์ฟเวอร์")
    result_summary: List[GroupScrapeSummary] = Field(..., description="สรุปผลการดึงข้อมูล")

class WebhookResultError(BaseModel):
    ok: bool = Field(False, description="สถานะล้มเหลว (จะเป็น `False` เสมอ)")
    task_id: str = Field(..., description="รหัสงานที่ล้มเหลว")
    username_safe: str = Field(..., description="ชื่อผู้ใช้ของงาน")
    error: str = Field(..., description="ข้อความอธิบายข้อผิดพลาด")

# ============================================================
# FastAPI App (Metadata / Tags / Lifespan)
# ============================================================
# เตรียมโฟลเดอร์หลัก + logger กลาง + metadata ของ API + lifecycle (startup/shutdown)
ensure_dirs()
GLOBAL_LOGGER = get_global_logger()

TAGS_METADATA = [
    {"name": "Profiles", "description": "จัดการโปรไฟล์ผู้ใช้ เริ่มต้นด้วยการสร้างโปรไฟล์และล็อกอิน"},
    {"name": "Scraping", "description": "Endpoints สำหรับการดึงข้อมูลโพสต์จากกลุ่ม Facebook ในโหมดต่างๆ"},
    {"name": "Sessions", "description": "ตรวจสอบและจัดการเซสชันที่กำลังทำงานอยู่"},
    {"name": "Maintenance", "description": "เครื่องมือดูแลรักษาระบบ เช่น การลบโปรไฟล์และข้อมูลที่เกี่ยวข้อง"},
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    # เริ่ม SessionManager และ janitor เมื่อแอปเริ่ม และหยุดเมื่อแอปปิด
    app.state.fb_manager = SessionManager(idle_timeout_sec=API_IDLE_TIMEOUT_SEC)
    app.state.fb_manager.start()
    try:
        yield
    finally:
        await app.state.fb_manager.stop()

# ================== EXPORT FUNCTIONS FOR MAIN.PY ==================
async def fb_manager_start(app):
    """Start Facebook session manager - called from main.py lifespan"""
    app.state.fb_manager = SessionManager(idle_timeout_sec=API_IDLE_TIMEOUT_SEC)
    app.state.fb_manager.start()
    GLOBAL_LOGGER.info("Facebook session manager started")

async def fb_manager_stop(app):
    """Stop Facebook session manager - called from main.py lifespan"""
    if hasattr(app.state, 'fb_manager'):
        await app.state.fb_manager.stop()
        GLOBAL_LOGGER.info("Facebook session manager stopped")

# ================== API ROUTER ==================
fb_router = APIRouter()

# --- Webhook Callback Documentation ---
# สร้าง router สำหรับเอกสาร Callback (OpenAPI callbacks) เพื่อให้ schema แสดงรูปแบบ payload ที่จะเรียกกลับ
callback_router = APIRouter()

@callback_router.post(
    "{$request.body#/webhook_url}",
    name="Webhook Receiver Example",
    summary="Callback: ระบบจะ POST ผลลัพธ์ไปยัง webhook_url ที่ระบุ",
    description="Payload ที่ส่งกลับมาจะเป็นโครงสร้างตาม Model ด้านล่างนี้ (สำเร็จหรือล้มเหลว)",
    include_in_schema=False # ซ่อนจาก Sidebar หลัก แต่ยังใช้ใน callback ได้
)
async def webhook_receiver(payload: Union[WebhookResultOk, WebhookResultError]):
    """Endpoint ตัวอย่างสำหรับเอกสาร Callback (ไม่มีการเรียกใช้งานจริงจาก Client)"""
    return {"received": True}

# ============================================================
# Endpoints
# ============================================================
@fb_router.post(
    "/create-profile",
    response_model=CreateProfileResponse,
    summary="สร้างโปรไฟล์และล็อกอิน Facebook",
    description="สร้างโปรไฟล์เบราว์เซอร์สำหรับ `username` และทำการล็อกอินเข้าสู่ Facebook หากมีโปรไฟล์อยู่แล้วจะพยายามใช้เซสชันเดิม",
    tags=["FB Profiles"],
    responses={
        400: {"model": ErrorResponse, "description": "คำขอไม่ถูกต้อง เช่น username ว่างเปล่า"},
        401: {"model": ErrorResponse, "description": "การล็อกอินล้มเหลว"},
        500: {"model": ErrorResponse, "description": "เกิดข้อผิดพลาดภายในเซิร์ฟเวอร์"},
    }
)
async def create_profile(req: CreateProfileRequest, request: Request):
    """
    สร้าง/ใช้โปรไฟล์เบราว์เซอร์และเข้าสู่ระบบ Facebook
    - ถ้ามีโปรไฟล์ของ `username` อยู่แล้ว: จะไม่สร้างใหม่ และแจ้งว่ามีอยู่แล้ว พร้อมตรวจ login ให้
    - ถ้ายังไม่มี: จะสร้างโปรไฟล์ใหม่ และพยายามเข้าสู่ระบบ
    """
    manager: SessionManager = request.app.state.fb_manager

    # ตรวจความถูกต้องของพารามิเตอร์
    username_full = req.username.strip()
    if not username_full:
        raise HTTPException(status_code=400, detail="username ต้องไม่เป็นค่าว่าง")

    username_safe = to_safe_name(username_full)
    udirs = user_dirs(username_safe)
    profile_dir = udirs["profile_dir"]
    existed_before = profile_has_data(profile_dir)

    task_id = make_task_id()

    async def _job(sess: UserSession):
        # รันภายใต้ session lock ของผู้ใช้
        token_user = CURRENT_USER_SAFE.set(sess.username_safe)
        token_task = CURRENT_TASK_ID.set(task_id)
        try:
            fb = await sess.ensure_fb()
            scraper = PostScrape(
                fb=fb,
                browser_data_dir=sess.browser_data_dir,
                username_full=username_full,
                password=req.password
            )
            # พยายาม ensure login (อาจใช้ session เดิม หรือกรอก username/password)
            await scraper.ensure_login()
            if scraper.login_state:
                msg = "มีโปรไฟล์อยู่แล้ว ตรวจสอบการเข้าสู่ระบบสำเร็จ" if existed_before else "สร้างโปรไฟล์ใหม่และเข้าสู่ระบบสำเร็จ"
                log(f"[{sess.username_safe}] {msg}")
                return {
                    "ok": True,
                    "task_id": task_id,
                    "username_safe": sess.username_safe,
                    "profile_existed": existed_before,
                    "message": msg,
                }
            else:
                msg = "มีโปรไฟล์อยู่แล้ว แต่การเข้าสู่ระบบล้มเหลว" if existed_before else "สร้างโปรไฟล์ใหม่แล้ว แต่การเข้าสู่ระบบล้มเหลว"
                warn(f"[{sess.username_safe}] {msg}")
                raise HTTPException(status_code=401, detail=msg)
        finally:
            # คืนค่า contextvars
            CURRENT_USER_SAFE.reset(token_user)
            CURRENT_TASK_ID.reset(token_task)

    try:
        return await manager.run_for_user(username_full, _job)
    except HTTPException:
        raise
    except Exception as e:
        # บันทึกความผิดพลาดของ endpoint
        GLOBAL_LOGGER.error(f"[{username_safe}] endpoint create-profile error: {e}")
        record_error_json(username_safe, "endpoint.create_profile", e, {"payload": req.model_dump(), "task_id": task_id})
        raise HTTPException(status_code=500, detail=str(e))

@fb_router.post(
    "/scrape-posts",
    response_model=ScrapePostsResponse,
    summary="ดึงโพสต์จากกลุ่ม (โหมดปกติ)",
    description="ดึงโพสต์จากกลุ่มที่ระบุ **(ต้องเรียก /create-profile เพื่อล็อกอินก่อน)**",
    tags=["FB Scraping"],
    responses={
        400: {"model": ErrorResponse, "description": "ไม่พบโปรไฟล์ของผู้ใช้, กรุณา /create-profile ก่อน"},
        401: {"model": ErrorResponse, "description": "เซสชันหมดอายุ, กรุณา /create-profile เพื่อล็อกอินใหม่"},
        500: {"model": ErrorResponse, "description": "เกิดข้อผิดพลาดภายในเซิร์ฟเวอร์"},
    }
)
async def scrape_posts(req: ScrapePostsRequest, request: Request):
    """
    ดึงโพสต์จากกลุ่ม
    - ต้องมีโปรไฟล์อยู่ก่อน (จะไม่สร้างใหม่ใน endpoint นี้)
    - ถ้า session FB หลุดระหว่างทำ จะหยุดงานทันที (fallout) และแจ้งกลับ ไม่ auto login ใหม่
    """
    manager: SessionManager = request.app.state.fb_manager
    username_full = req.username.strip()
    if not username_full:
        raise HTTPException(status_code=400, detail="username ต้องไม่เป็นค่าว่าง")

    username_safe = to_safe_name(username_full)
    udirs = user_dirs(username_safe)
    profile_dir = udirs["profile_dir"]

    # ต้องมีโปรไฟล์มาก่อน
    if not profile_has_data(profile_dir):
        raise HTTPException(status_code=400, detail="ไม่พบโปรไฟล์ของผู้ใช้นี้ กรุณาเรียก /create-profile เพื่อลงชื่อเข้าใช้ก่อน")

    task_id = make_task_id()
    output_path = str(Path(udirs["out_dir"]).joinpath(f"{task_id}.json"))

    async def _job(sess: UserSession):
        # รันงานภายใต้ session lock
        token_user = CURRENT_USER_SAFE.set(sess.username_safe)
        token_task = CURRENT_TASK_ID.set(task_id)
        try:
            # ตรวจ policy รีสตาร์ทเบราว์เซอร์ (ลด state สะสม)
            try:
                cnt = int(getattr(sess, "_scrape_job_counter", 0)) + 1
                setattr(sess, "_scrape_job_counter", cnt)
                need_restart = False
                if RECHROME_POLICY == "before_each": need_restart = True
                elif RECHROME_POLICY == "every_n" and RECHROME_EVERY_N > 0 and (cnt % RECHROME_EVERY_N == 0): need_restart = True
                if need_restart:
                    log(f"[{sess.username_safe}] RE-CHROME: policy={RECHROME_POLICY} cnt={cnt} -> รีสตาร์ทเบราว์เซอร์ก่อนเริ่มงาน")
                    await sess.restart_browser()
            except Exception as e:
                warn(f"[{sess.username_safe}] RE-CHROME: ข้ามการรีสตาร์ท (สาเหตุ: {e})")

            fb = await sess.ensure_fb()
            scraper = PostScrape(fb=fb, browser_data_dir=sess.browser_data_dir, username_full=username_full, password=None)

            # ต้อง login อยู่
            if not await scraper.is_logged_in():
                raise HTTPException(status_code=401, detail="ยังไม่ได้เข้าสู่ระบบหรือ session หมดอายุ กรุณาเรียก /create-profile เพื่อเข้าสู่ระบบก่อน")

            try:
                group = [g.model_dump() for g in req.group]
                random.shuffle(group)
                result = await scraper.run_task(num_post=req.num_post, group=group, last_post=str(Path(udirs["out_dir"])))
            except Exception as e:
                if "FB_SESSION_LOST" in str(e):
                    raise HTTPException(status_code=401, detail="session หลุดระหว่างทำงาน กรุณาเรียก /create-profile เพื่อเข้าสู่ระบบใหม่") from e
                raise

            # บันทึกผลลัพธ์ลงไฟล์
            payload = {
                "task_id": task_id,
                "username_full": username_full,
                "username_safe": sess.username_safe,
                "requested_at": now_ts(),
                "num_post": req.num_post,
                "groups": [g.model_dump() for g in req.group],
                "result": result
            }
            save_json(output_path, payload)
            log(f"[{sess.username_safe}] เซฟผลลัพธ์ -> {output_path}")

            # Trigger งาน sink แบบสุ่มตามจำนวนครั้งที่เรียก (รักษา session ให้ "อุ่น")
            try:
                hits = int(getattr(sess, "_sink_hits", 0)) + 1
                setattr(sess, "_sink_hits", hits)
                threshold = int(os.getenv("SINK_HITS_BEFORE_TRIGGER", "8"))
                if hits >= threshold and sess.fb is not None:
                    setattr(sess, "_sink_hits", 0)
                    async def _run_sink_bg(s=sess, m=manager, uname=username_full):
                        token_user2 = CURRENT_USER_SAFE.set(s.username_safe)
                        token_task2 = CURRENT_TASK_ID.set(make_task_id())
                        try:
                            async def _sink_job(sess_in: UserSession): await randomtask(sess_in.fb)
                            await m.run_for_sys(uname, _sink_job)
                        except Exception as e: err(f"[Endpoint->sink] {s.username_safe}: {e}")
                        finally: CURRENT_USER_SAFE.reset(token_user2); CURRENT_TASK_ID.reset(token_task2)
                    asyncio.create_task(_run_sink_bg())
            except Exception:
                pass

            # แปลงผลลัพธ์ให้เป็น summary ที่สั้นลงสำหรับ response
            def _summary_row(r):
                url, posts, name = (r[0] if len(r) > 0 else None), (r[1] if len(r) > 1 else []), (r[2] if len(r) > 2 else None)
                return {
                    "group_url": url,
                    "group_name": name,
                    "posts_count": len(posts) if isinstance(posts, list) else 0,
                    "posts": posts
                }

            return {
                "ok": True,
                "task_id": task_id,
                "username_safe": sess.username_safe,
                "output_file": output_path,
                "result_summary": [_summary_row(r) for r in result]
            }
        finally:
            CURRENT_USER_SAFE.reset(token_user)
            CURRENT_TASK_ID.reset(token_task)

    try:
        return await manager.run_for_user(username_full, _job)
    except HTTPException:
        raise
    except Exception as e:
        GLOBAL_LOGGER.error(f"[{username_safe}] endpoint scrape-posts error: {e}")
        record_error_json(username_safe, "endpoint.scrape_posts", e, {"payload": req.model_dump(), "task_id": task_id})
        raise HTTPException(status_code=500, detail=str(e))

@fb_router.get(
    "/sessions",
    response_model=SessionsListResponse,
    summary="ดูรายการเซสชันที่กำลังทำงาน",
    tags=["FB Sessions"]
)
async def list_sessions(request: Request):
    # แสดงสถานะ session ทั้งหมดในระบบ ณ ตอนนี้
    manager: SessionManager = request.app.state.fb_manager
    out = []
    now = time.monotonic()
    for uname_full, sess in manager.sessions.items():
        out.append({
            "username_full": uname_full,
            "username_safe": sess.username_safe,
            "browser_data_dir": sess.browser_data_dir,
            "locked": sess.lock.locked(),
            "last_used_sec_ago": round(now - sess.last_used, 2),
            "closed": sess.closed
        })
    return {"sessions": out}

@fb_router.delete(
    "/sessions/{username_full}",
    response_model=CloseSessionResponse,
    summary="ปิดเซสชันของผู้ใช้",
    description="ปิดเบราว์เซอร์และนำเซสชันของผู้ใช้ออกจากหน่วยความจำ (หากไม่มีงานกำลังรันอยู่)",
    tags=["FB Sessions"],
    responses={400: {"model": ErrorResponse, "description": "ไม่สามารถปิดได้เนื่องจากมีงานกำลังทำอยู่"}}
)
async def close_session(username_full: str = PathParam(..., description="ชื่อผู้ใช้เต็มของเซสชันที่ต้องการปิด"), request: Request = None):
    # ปิด session ของผู้ใช้ตาม username_full หากไม่มีงานค้างอยู่
    manager: SessionManager = request.app.state.fb_manager
    sess = manager.sessions.get(username_full)
    safe = to_safe_name(username_full)
    if not sess:
        return {"ok": True, "message": f"session ของ {safe} ไม่พบ (อาจถูกปิดไปแล้ว)"}
    if sess.lock.locked():
        raise HTTPException(status_code=400, detail="ไม่สามารถปิดได้: มีงานกำลังทำอยู่")

    token_user = CURRENT_USER_SAFE.set(sess.username_safe)
    token_task = CURRENT_TASK_ID.set(make_task_id())
    try:
        await sess.close()
        manager.sessions.pop(username_full, None)
        return {"ok": True, "message": f"ปิด session ของ {safe} แล้ว"}
    finally:
        CURRENT_USER_SAFE.reset(token_user)
        CURRENT_TASK_ID.reset(token_task)

def profiles_base_dir() -> Path:
    # คืนพาธหลักของ profiles (ใช้ FDATA/profiles)
    root = Path(os.environ.get("FDATA", "./FDATA")).resolve()
    return root.joinpath("profiles")

def iso_utc(ts: float) -> str:
    # แปลง timestamp -> ISO8601 (UTC)
    try: return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception: return None

def dir_exists_and_nonempty(p: Path) -> bool:
    # โฟลเดอร์มีอยู่และไม่ว่างหรือไม่
    try: return p.exists() and any(p.iterdir())
    except Exception: return False

def dir_size_bytes(p: Path, limit_files: int = 50000) -> int:
    # คำนวณขนาดโฟลเดอร์โดยรวม (จำกัดจำนวนไฟล์เพื่อไม่ให้ช้าเกิน)
    total, count = 0, 0
    try:
        for root, _, files in os.walk(p):
            for fname in files:
                try: total += Path(root).joinpath(fname).stat().st_size
                except Exception: pass
                count += 1
                if count >= limit_files: return total
    except Exception: pass
    return total

@fb_router.get(
    "/profiles",
    response_model=ProfilesListResponse,
    summary="ดูรายการโปรไฟล์ทั้งหมดที่บันทึกไว้",
    tags=["FB Maintenance"]
)
async def list_profiles(request: Request, include_size: bool = Query(False, description="คำนวณขนาดของโฟลเดอร์โปรไฟล์ (อาจใช้เวลาถ้าโปรไฟล์มีขนาดใหญ่)")):
    """
    แสดงรายชื่อโปรไฟล์ทั้งหมดใน FDATA/profiles
    - คืนค่าชื่อโฟลเดอร์ (username_safe), path, เวลาแก้ไขล่าสุด, มีข้อมูลหรือไม่
    - สถานะ active (เปิดอยู่ใน SessionManager) และ closed
    - สามารถเลือกคำนวณขนาดโฟลเดอร์ได้ด้วย include_size=true (อาจใช้เวลานาน)
    """
    base = profiles_base_dir()
    try: base.mkdir(parents=True, exist_ok=True)
    except Exception: pass

    manager: SessionManager = request.app.state.fb_manager

    # สร้าง map ของสถานะ session ต่อ username_safe
    active_map = {}
    try:
        for uname_full, sess in list(manager.sessions.items()):
            active_map[sess.username_safe] = {
                "active": (not sess.closed),
                "locked": sess.lock.locked(),
                "last_used_monotonic": sess.last_used
            }
    except Exception: pass

    profiles: List[Dict[str, Any]] = []
    try:
        # ไลน์โปรไฟล์ทั้งหมดจากโฟลเดอร์
        for child in sorted(base.iterdir()):
            if not child.is_dir(): continue
            username_safe, exists_nonempty, stat = child.name, dir_exists_and_nonempty(child), None
            try: stat = child.stat()
            except Exception: pass

            item = {
                "username_safe": username_safe,
                "profile_dir": str(child),
                "exists": child.exists(),
                "nonempty": exists_nonempty,
                "mtime": iso_utc(stat.st_mtime) if stat else None,
                "ctime": iso_utc(stat.st_ctime) if stat else None
            }

            if username_safe in active_map:
                s = active_map[username_safe]
                item["session"] = { "active": s.get("active", False), "locked": s.get("locked", False) }
            else:
                item["session"] = { "active": False, "locked": False }

            if include_size and child.exists():
                item["size_bytes"] = dir_size_bytes(child)

            profiles.append(item)

    except Exception as e:
        GLOBAL_LOGGER.error(f"[profiles] list error: {e}")
        record_error_json("global", "endpoint.list_profiles", e, {"base": str(base)})
        raise HTTPException(status_code=500, detail=f"ไม่สามารถอ่านรายชื่อโปรไฟล์ได้: {e}")

    return { "ok": True, "profiles_dir": str(base), "count": len(profiles), "profiles": profiles }

def _today_iso() -> str:
    # คืนวันที่ปัจจุบันใน local time (YYYY-MM-DD)
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")

def _pick_random_prompt(today: str, region: str, num_items: int) -> str:
    # สุ่มสคริปต์งาน/คำสั่ง (prompt) สำหรับ randomtask
    candidates = [
        f"ค้นหาข่าวเด่นที่น่าสนใจในประเทศไทยประจำวันที่ {today} จำนวน {num_items} เรื่อง พร้อมลิงก์แหล่งข่าว และสรุปย่อ",
        f"สุ่มหัวข้อที่กำลังเป็นกระแสวันนี้ใน {region} ({today}) จำนวน {num_items} เรื่อง จากแหล่งข่าว/โซเชียลทั่วไป พร้อมสรุปและลิงก์อ้างอิง",
        f"หาบทความหรือโพสต์ที่มีการพูดถึงมากวันนี้ ({today}) ใน {region} อย่างน้อย {num_items} รายการ พร้อมแหล่งที่มาและคำอธิบายสั้น"
    ]
    return random.choice(candidates)

def build_random_daily_task(region: str = "ประเทศไทย", num_items: Optional[int] = None) -> str:
    # ประกอบข้อความ task สำหรับ randomtask โดยเพิ่มข้อกำหนดให้เน้น Facebook และใช้ภาษาไทย
    if num_items is None: num_items = random.randint(1, 2)
    today, objective = _today_iso(), _pick_random_prompt(_today_iso(), region, num_items)
    return objective + f" โดยเน้นหาจาก Facebook \nผลลัพธ์ต้องเป็นรายการอย่างน้อย {num_items} รายการ\n(ใช้ภาษาไทยในการทำงาน เช่น แสดงผล หรือ ค้นหา)"

@fb_router.post(
    "/random-scrape",
    response_model=RandomScrapeResponse,
    summary="สุ่มงานดึงข้อมูล (Random Task)",
    description="สั่งให้เบราว์เซอร์ทำงานสุ่ม (เช่น ค้นหาข่าว) โดยใช้โปรไฟล์ที่ล็อกอินอยู่ เพื่อจำลองพฤติกรรมมนุษย์และรักษาเซสชันให้อุ่น",
    tags=["FB Scraping"],
    responses={
        400: {"model": ErrorResponse, "description": "ไม่พบโปรไฟล์ของผู้ใช้"},
        500: {"model": ErrorResponse, "description": "เกิดข้อผิดพลาดภายในเซิร์ฟเวอร์"},
    }
)
async def random_scrape(req: RandomScrapeRequest, request: Request):
    """
    ทำ 'random work' โดยเรียกใช้ฟังก์ชัน randomtask โดยตรง
    - ใช้ session/profile ของผู้ใช้เพื่อนำทางเบราว์เซอร์
    - ผลลัพธ์และไฟล์จะถูกบันทึกไว้ใน FDATA/outputs/_sink/<task_id>.json
    - มีระบบกันซ้ำ/คูลดาวน์ตามที่ randomtask กำหนด (SINK_COOLDOWN_SEC)
    - รองรับการกำหนด region และ num_items จาก payload
    """
    manager: SessionManager = request.app.state.fb_manager
    username_full = (req.username or "").strip()
    if not username_full: raise HTTPException(status_code=400, detail="username ต้องไม่เป็นค่าว่าง")

    username_safe = to_safe_name(username_full)
    if not profile_has_data(user_dirs(username_safe)["profile_dir"]):
        raise HTTPException(status_code=400, detail="ไม่พบโปรไฟล์ของผู้ใช้นี้ กรุณาเรียก /create-profile เพื่อลงชื่อเข้าใช้ก่อน")

    async def _job(sess: UserSession):
        token_user, token_task = CURRENT_USER_SAFE.set(sess.username_safe), CURRENT_TASK_ID.set(make_task_id())
        try:
            fb = await sess.ensure_fb()
            result = await randomtask(fb, region=req.region, num_items=req.num_items)
            return { "username_safe": sess.username_safe, **result }
        finally:
            CURRENT_USER_SAFE.reset(token_user)
            CURRENT_TASK_ID.reset(token_task)

    try: return await manager.run_for_user(username_full, _job)
    except HTTPException: raise
    except Exception as e:
        GLOBAL_LOGGER.error(f"[{username_safe}] endpoint random-scrape error: {e}")
        record_error_json(username_safe, "endpoint.random_scrape", e, { "payload": req.model_dump() })
        raise HTTPException(status_code=500, detail=str(e))

def _rm_tree(path: Path) -> tuple[bool, str]:
    # ลบโฟลเดอร์ทั้งหมด (recursive) แบบรายงานผลลัพธ์ (ok,msg)
    try:
        if not path.exists(): return True, "not_exists"
        if not path.is_dir(): return False, "not_directory"
        shutil.rmtree(path, ignore_errors=False)
        return True, "deleted"
    except Exception as e: return False, f"error:{e.__class__.__name__}:{e}"

def _unique_existing_paths(paths: list[Path]) -> list[Path]:
    # กรอง path ซ้ำซ้อน (ตาม resolved path)
    out, seen = [], set()
    for p in paths:
        try: rp = p.resolve()
        except Exception: rp = p
        if str(rp) in seen: continue
        seen.add(str(rp)); out.append(p)
    return out

@fb_router.post(
    "/profiles/delete",
    response_class=PlainTextResponse,
    summary="ลบโปรไฟล์และข้อมูลที่เกี่ยวข้อง",
    description="ลบโฟลเดอร์โปรไฟล์เบราว์เซอร์ และสามารถเลือกลบไฟล์ log และ output ทั้งหมดที่เกี่ยวข้องได้",
    tags=["FB Maintenance"],
    responses={
        200: {"description": "ผลการทำงานในรูปแบบ Plain Text", "content": {"text/plain": {"example": "ok: True\nusername_safe: john.doe\nsessions_closed: 1\n..."}}},
        400: {"description": "username ว่างเปล่า หรือเซสชันกำลังทำงาน (กรณีไม่ใช้ force=true)"},
    }
)
async def delete_profile(req: DeleteProfileRequest, request: Request):
    """
    รับ JSON: { "username": "...", "force": true, "all": true }
    ส่งออกเป็นข้อความธรรมดา (plain text)
    - จะปิด session ที่กำลังทำงานถ้า force=true หรือไม่มีงานค้างอยู่
    - ลบโฟลเดอร์ profile (และ logs/outputs หาก all=true)
    """
    manager: SessionManager = request.app.state.fb_manager
    username_full = (req.username or "").strip()
    if not username_full: return PlainTextResponse(status_code=400, content="error: username is empty")

    username_safe, force, delete_all = to_safe_name(username_full), bool(req.force), bool(req.all)

    # หา session ที่เกี่ยวข้องกับ username_safe
    target_sessions = [(uname, sess) for uname, sess in list(manager.sessions.items()) if sess.username_safe == username_safe]

    # ถ้า session ใด locked และไม่ได้ force -> ปฏิเสธ
    if [uname for uname, sess in target_sessions if sess.lock.locked()] and not force:
        return PlainTextResponse(status_code=400, content="error: session_is_busy — โปรดลองใหม่ภายหลัง หรือส่ง force=true")

    # ปิด session ทั้งหมดที่พบ
    closed_count = 0
    for uname_full, sess in target_sessions:
        try:
            await sess.close(); manager.sessions.pop(uname_full, None); closed_count += 1
        except Exception as e: record_error_json(username_safe, "endpoint.delete_profile.close_session", e)

    # เตรียมรายการ path ที่จะลบ
    targets = [Path(BASE_PROFILE_DIR).joinpath(username_safe), profiles_base_dir().joinpath(username_safe)]
    if delete_all: targets.extend([Path(BASE_LOG_DIR).joinpath(username_safe), Path(BASE_OUTPUT_DIR).joinpath(username_safe)])

    # ลบและสรุปผลเป็นข้อความ
    results = [ (str(p), *_rm_tree(p)) for p in _unique_existing_paths(targets) ]
    lines = [
        f"ok: True",
        f"username_safe: {username_safe}",
        f"sessions_closed: {closed_count}",
        f"force: {force}",
        f"delete_all: {delete_all}",
        "paths:"
    ]
    lines.extend([f"- {p} => {'ok' if ok else 'fail'} ({msg})" for p, ok, msg in results])
    return PlainTextResponse(content="\n".join(lines), media_type="text/plain; charset=utf-8")

async def _post_webhook(url: str, payload: Dict[str, Any], timeout_sec: int = 15):
    # ฟังก์ชันช่วยยิง HTTP POST ไปยัง webhook_url พร้อม payload
    if not url: return
    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.post(url, json=payload)
            log(f"Webhook POST => {url} status={resp.status_code}")
    except Exception as e:
        warn(f"ส่ง Webhook ไม่สำเร็จ: {e}")

@fb_router.post(
    "/scrape-posts-webhook",
    response_model=WebhookQueuedResponse,
    summary="ดึงโพสต์จากกลุ่ม (โหมด Webhook)",
    description="รับงานดึงโพสต์เข้าคิวและทำงานเบื้องหลัง เมื่อเสร็จสิ้นจะส่งผลลัพธ์ไปที่ `webhook_url` ที่ระบุ",
    tags=["FB Scraping"],
    callbacks=callback_router.routes,
    responses={
        400: {"model": ErrorResponse, "description": "ไม่พบโปรไฟล์, username/webhook_url ว่างเปล่า"},
        500: {"model": ErrorResponse, "description": "เกิดข้อผิดพลาดภายในเซิร์ฟเวอร์"},
    }
)
async def scrape_posts_webhook(req: ScrapePostsWebhookRequest, request: Request):
    """
    ดึงโพสต์จากกลุ่ม (โหมดไวสำหรับ Webhook)
    - ตอบกลับทันทีว่าเริ่มงานแล้ว หรือเข้าคิว
    - งานจริงรันแบบ background และ serialize ต่อโปรไฟล์
    - เมื่อเสร็จจะ POST JSON กลับไปที่ webhook_url
    """
    manager: SessionManager = request.app.state.fb_manager
    username_full = (req.username or "").strip()
    if not username_full: raise HTTPException(status_code=400, detail="username ต้องไม่เป็นค่าว่าง")

    username_safe, udirs = to_safe_name(username_full), user_dirs(to_safe_name(username_full))
    if not profile_has_data(udirs["profile_dir"]): raise HTTPException(status_code=400, detail="ไม่พบโปรไฟล์ของผู้ใช้นี้ กรุณาเรียก /create-profile เพื่อลงชื่อเข้าใช้ก่อน")

    webhook_url = (req.webhook_url or "").strip()
    if not webhook_url: raise HTTPException(status_code=400, detail="ต้องระบุ webhook_url")

    # เตรียม task_id และ output_path
    task_id, output_path = make_task_id(), str(Path(udirs["out_dir"]).joinpath(f"{make_task_id()}.json"))

    # สถานะคิว: position = (#ที่กำลังรันอยู่? 1:0) + (#ค้างอยู่) + 1
    sess, running = manager.get_or_create(username_full), manager.get_or_create(username_full).lock.locked()
    queue_len, position = int(getattr(sess, "_webhook_queue_len", 0)), int(getattr(sess, "_webhook_queue_len", 0)) + (1 if running else 0) + 1
    setattr(sess, "_webhook_queue_len", queue_len + 1)

    async def _bg_job(s: UserSession):
        # งานเบื้องหลังจริงที่ไปเปิดเบราว์เซอร์ดึงโพสต์ แล้ว POST กลับ webhooks
        token_user, token_task = CURRENT_USER_SAFE.set(s.username_safe), CURRENT_TASK_ID.set(task_id)
        try:
            try:
                # รีสตาร์ทตาม policy (แบบเดียวกับ /scrape-posts)
                cnt = int(getattr(s, "_scrape_job_counter", 0)) + 1
                setattr(s, "_scrape_job_counter", cnt)
                need_restart = False
                if RECHROME_POLICY == "before_each": need_restart = True
                elif RECHROME_POLICY == "every_n" and RECHROME_EVERY_N > 0 and (cnt % RECHROME_EVERY_N == 0): need_restart = True
                if need_restart: log(f"[{s.username_safe}] RE-CHROME: policy={RECHROME_POLICY} cnt={cnt} -> รีสตาร์ทเบราว์เซอร์ (webhook)"); await s.restart_browser()
            except Exception as e: warn(f"[{s.username_safe}] RE-CHROME: ข้ามการรีสตาร์ท (webhook) เนื่องจาก: {e}")

            fb = await s.ensure_fb()
            scraper = PostScrape(fb=fb, browser_data_dir=s.browser_data_dir, username_full=username_full, password=None)

            if not await scraper.is_logged_in():
                await _post_webhook(webhook_url, { "ok": False, "task_id": task_id, "username_safe": s.username_safe, "error": "ยังไม่ได้เข้าสู่ระบบหรือ session หมดอายุ กรุณาเรียก /create-profile" })
                return

            try:
                group = [g.model_dump() for g in req.group]
                random.shuffle(group)
                result = await scraper.run_task(num_post=req.num_post, group=group, last_post=str(Path(udirs["out_dir"])))
            except Exception as e:
                if "FB_SESSION_LOST" in str(e):
                    await _post_webhook(webhook_url, { "ok": False, "task_id": task_id, "username_safe": s.username_safe, "error": "session หลุดระหว่างทำงาน กรุณาเรียก /create-profile" })
                else:
                    await _post_webhook(webhook_url, { "ok": False, "task_id": task_id, "username_safe": s.username_safe, "error": str(e) })
                return

            payload = {
                "task_id": task_id,
                "username_full": username_full,
                "username_safe": s.username_safe,
                "requested_at": now_ts(),
                "num_post": req.num_post,
                "groups": [g.model_dump() for g in req.group],
                "result": result
            }
            save_json(output_path, payload)
            log(f"[{s.username_safe}] เซฟผลลัพธ์ -> {output_path}")

            def _summary_row(r):
                logging.info(r)
                url, posts, name,chat_id = (r[0] if len(r) > 0 else None), (r[1] if len(r) > 1 else []), (r[2] if len(r) > 2 else None),(r[3] if len(r) > 3 else "0")
                return { "group_url": url, "group_name": name, "posts_count": len(posts) if isinstance(posts, list) else 0, "posts": posts ,"chat_id":chat_id}

            await _post_webhook(webhook_url, {
                "ok": True,
                "task_id": task_id,
                "username_safe": s.username_safe,
                "completed_at": now_ts(),
                "output_file": output_path,
                "result_summary": [_summary_row(r) for r in result]
            })

            # trigger sink แบบเดียวกับ endpoint ปกติ
            try:
                hits = int(getattr(s, "_sink_hits", 0)) + 1
                setattr(s, "_sink_hits", hits)
                threshold = int(os.getenv("SINK_HITS_BEFORE_TRIGGER", "8"))
                if hits >= threshold and s.fb is not None:
                    setattr(s, "_sink_hits", 0)
                    async def _run_sink_bg(s2=s, m=manager, uname=username_full):
                        token_user2, token_task2 = CURRENT_USER_SAFE.set(s2.username_safe), CURRENT_TASK_ID.set(make_task_id())
                        try:
                            async def _sink_job(sess_in: UserSession): await randomtask(sess_in.fb)
                            await m.run_for_sys(uname, _sink_job)
                        except Exception as e: err(f"[Endpoint->sink] {s2.username_safe}: {e}")
                        finally: CURRENT_USER_SAFE.reset(token_user2); CURRENT_TASK_ID.reset(token_task2)
                    asyncio.create_task(_run_sink_bg())
            except Exception: pass

        except Exception as e:
            # บันทึกและแจ้ง webhook หากมีข้อผิดพลาดภายใน job
            GLOBAL_LOGGER.error(f"[{username_safe}] bg scrape (webhook) error: {e}")
            record_error_json(username_safe, "endpoint.scrape_posts_webhook.bg", e, { "payload": req.model_dump(), "task_id": task_id })
            try: await _post_webhook(webhook_url, { "ok": False, "task_id": task_id, "username_safe": username_safe, "error": str(e) })
            except Exception: pass
        finally:
            CURRENT_USER_SAFE.reset(token_user)
            CURRENT_TASK_ID.reset(token_task)

    async def _schedule():
        # สร้าง task เข้าคิวผ่าน manager.run_for_user เพื่อ serialize
        try: await manager.run_for_user(username_full, _bg_job)
        finally:
            # ลดตัวนับคิว webhook ลง
            try: setattr(sess, "_webhook_queue_len", max(0, int(getattr(sess, "_webhook_queue_len", 1)) - 1))
            except Exception: pass

    asyncio.create_task(_schedule())

    return {
        "ok": True,
        "task_id": task_id,
        "username_safe": username_safe,
        "status": "queued" if position > 1 else "running",
        "queue_position": position,
        "message": (f"งานถูกเข้าคิว เนื่องจากมีงานกำลังทำอยู่ ลำดับคิวของงานนี้คือ #{position}" if position > 1 else "เริ่มงานดึงโพสต์แบบ Background แล้ว จะส่งผลลัพธ์ไปยัง webhook_url เมื่อเสร็จ"),
        "webhook_url": webhook_url
    }

@fb_router.post(
    "/status/snapshot",
    summary="ดูภาพหน้าจอการทำงานปัจจุบัน (Snapshot)",
    description="จับภาพหน้าจอของเบราว์เซอร์ที่กำลังทำงานอยู่สำหรับผู้ใช้ที่ระบุ (ย่อขนาดเป็น 720x480)",
    tags=["FB Snapshot"],
    responses={
        200: {"content": {"image/png": {}}, "description": "รูปภาพ PNG ของหน้าจอ"},
        400: {"model": ErrorResponse, "description": "คำขอไม่ถูกต้อง (เช่น username ว่าง)"},
        404: {"model": ErrorResponse, "description": "ไม่พบเซสชันของผู้ใช้นี้"},
        409: {"model": ErrorResponse, "description": "เซสชันถูกปิด หรือเบราว์เซอร์ไม่พร้อมใช้งาน (Warning)"},
        500: {"model": ErrorResponse, "description": "เกิดข้อผิดพลาดในการจับภาพ"},
    }
)
async def get_snapshot(req: SnapshotRequest, request: Request):
    """
    จับภาพหน้าจอของ User Session ที่ระบุ
    - ต้องมี Session ที่กำลังทำงานอยู่ (Active)
    - ส่งคืนเป็นไฟล์รูปภาพ PNG (Binary)
    """
    manager: SessionManager = request.app.state.fb_manager
    username_full = (req.username or "").strip()
    if not username_full:
        raise HTTPException(status_code=400, detail="username ต้องไม่เป็นค่าว่าง")

    sess = manager.sessions.get(username_full)
    if not sess:
        # ไม่พบ session -> 404 Not Found
        raise HTTPException(status_code=404, detail="ไม่พบเซสชันของผู้ใช้นี้ (อาจยังไม่ได้ล็อกอินหรือปิดไปแล้ว)")
    
    if sess.closed:
        # Session ปิดแล้ว -> 409 Conflict (Warning state)
        raise HTTPException(status_code=409, detail="เซสชันถูกปิดไปแล้ว")

    if not sess.fb or not getattr(sess.fb, "_page", None):
        # Browser ไม่พร้อม -> 409 Conflict (Warning state)
        raise HTTPException(status_code=409, detail="เบราว์เซอร์ยังไม่ได้ถูกเปิดใช้งานในเซสชันนี้")

    try:
        # ตรวจสอบว่า page ยังไม่ปิด
        if sess.fb._page.is_closed():
             # Page ปิด -> 409 Conflict (Warning state)
             raise HTTPException(status_code=409, detail="หน้าเพจถูกปิดไปแล้ว")

        # จับภาพ (bytes)
        png_bytes = await sess.fb._page.screenshot()
        
        # ย่อภาพด้วย PIL
        with Image.open(io.BytesIO(png_bytes)) as img:
            # Resize เป็น 720x480
            img_resized = img.resize((720, 480))
            
            output = io.BytesIO()
            img_resized.save(output, format="PNG")
            resized_bytes = output.getvalue()

        return Response(content=resized_bytes, media_type="image/png")

    except Exception as e:
        GLOBAL_LOGGER.error(f"[{sess.username_safe}] snapshot error: {e}")
        raise HTTPException(status_code=500, detail=f"ไม่สามารถจับภาพหน้าจอได้: {str(e)}")


# Standalone run (commented - use main.py instead)
# if __name__ == "__main__":
#     uvicorn.run(app, host="0.0.0.0", port=33002)
