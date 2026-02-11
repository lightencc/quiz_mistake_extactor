#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import requests
from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

try:
    from google import genai
    from google.genai import types as genai_types
except Exception:  # pragma: no cover
    genai = None
    genai_types = None

try:
    from notion_client import Client as NotionClient
except Exception:  # pragma: no cover
    NotionClient = None

try:
    from github import Github
    from github.GithubException import GithubException, UnknownObjectException
except Exception:  # pragma: no cover
    Github = None
    GithubException = Exception
    UnknownObjectException = Exception

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

from extract_mistakes import (
    crop_by_norm_bbox,
    encode_image_as_data_url,
    sanitize_bbox,
)


BASE_DIR = Path(__file__).resolve().parent
if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "web_data"
UPLOADS_DIR = DATA_DIR / "uploads"
SESSIONS_DIR = DATA_DIR / "sessions"
EXPORTS_DIR = DATA_DIR / "exports"
CACHE_DIR = DATA_DIR / "ocr_cache"
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MARTIAN_SCRIPT = BASE_DIR / "scripts" / "martian_to_blocks.mjs"
APP_LOG_LEVEL = os.getenv("APP_LOG_LEVEL", "INFO").strip().upper()
APP_LOG_FILE = os.getenv("APP_LOG_FILE", str(DATA_DIR / "app.log")).strip()
_gemini_base_raw = (os.getenv("GEMINI_BASE_URL") or "").strip()
if _gemini_base_raw:
    _gemini_base_raw = _gemini_base_raw.rstrip("/")
    # google-genai 会自动拼接 /v1beta 或 /v1；若 base_url 已带版本前缀会导致 /v1/v1beta 404。
    _gemini_base_raw = re.sub(r"/v1(?:beta)?$", "", _gemini_base_raw)
GEMINI_BASE_URL = _gemini_base_raw
GEMINI_API_KEY = (
    os.getenv("GEMINI_API_KEY")
    or os.getenv("GOOGLE_API_KEY")
    or ""
).strip()
GEMINI_MODEL = (
    os.getenv("GEMINI_MODEL")
    or os.getenv("MOONSHOT_MODEL")
    or os.getenv("MODEL")
    or "gemini-3-flash-preview"
).strip()
GEMINI_API_VERSION = (os.getenv("GEMINI_API_VERSION") or "").strip()
GEMINI_REQUEST_TIMEOUT_SECONDS = float(
    os.getenv("GEMINI_REQUEST_TIMEOUT_SECONDS")
    or os.getenv("MOONSHOT_REQUEST_TIMEOUT_SECONDS")
    or "90"
)
NOTION_API_KEY = (
    os.getenv("NOTION_API_KEY")
    or os.getenv("NOTION_TOKEN")
    or os.getenv("NOTION_SECRET")
    or ""
).strip()
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "").strip()
NOTION_DATA_SOURCE_ID = os.getenv("NOTION_DATA_SOURCE_ID", "").strip()
NOTION_TITLE_PROPERTY = os.getenv("NOTION_TITLE_PROPERTY", "").strip()
NOTION_ID_PROPERTY = os.getenv("NOTION_ID_PROPERTY", "ID").strip()
NOTION_TITLE_PREFIX = os.getenv("NOTION_TITLE_PREFIX", "").strip()

DEFAULT_MODEL = GEMINI_MODEL

DEFAULT_MARKDOWN_PROMPT_TEMPLATE = """## 1️⃣ 原题 Results

- 题目截图/题干：

- 我的原答案：

- 正确答案：


## 2️⃣ 原因 Reason

- 我认为错因：

- 家长复核：

- 本题核心遗漏：


## 3️⃣ 针对性练习 Action

- 练习1：
  - 题目：
  - 参考答案：
  - 是否做对：
  - 用时：
  - 备注：
- 练习2：
  - 题目：
  - 参考答案：
  - 是否做对：
  - 用时：
  - 备注：
- 练习3：
  - 题目：
  - 参考答案：
  - 是否做对：
  - 用时：
  - 备注：


## 4️⃣ 复盘 Review

- 下次遇到什么信号要警觉：

- 能迁移到哪些题型：

- 是否可升阶：
"""

BAIDU_OAUTH_URL = os.getenv("BAIDU_OAUTH_URL", "https://aip.baidubce.com/oauth/2.0/token")
BAIDU_OCR_URL = os.getenv("BAIDU_OCR_URL", "https://aip.baidubce.com/rest/2.0/ocr/v1/paper_cut_edu")
BAIDU_OCR_API_KEY = os.getenv("BAIDU_OCR_API_KEY") or os.getenv("API_KEY", "")
BAIDU_OCR_SECRET_KEY = os.getenv("BAIDU_OCR_SECRET_KEY") or os.getenv("SECRET_KEY", "")
BAIDU_OCR_TIMEOUT = float(os.getenv("BAIDU_OCR_TIMEOUT", "25"))
BAIDU_PRINTED_MIN_CONF = float(os.getenv("BAIDU_OCR_PRINTED_MIN_CONF", "0.50"))

GITHUB_TOKEN = (
    os.getenv("GITHUB_TOKEN")
    or os.getenv("GH_TOKEN")
    or ""
).strip()
GITHUB_REPO = os.getenv("GITHUB_REPO", "lightencc/quiz_content").strip()
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main").strip()
GITHUB_IMAGE_DIR = os.getenv("GITHUB_IMAGE_DIR", "images").strip().strip("/")
GITHUB_RAW_BASE = os.getenv("GITHUB_RAW_BASE", "https://raw.githubusercontent.com").strip().rstrip("/")
UPLOAD_COMPRESS_MAX_SIDE = int(float(os.getenv("UPLOAD_COMPRESS_MAX_SIDE", "1800")))
UPLOAD_COMPRESS_JPEG_QUALITY = int(float(os.getenv("UPLOAD_COMPRESS_JPEG_QUALITY", "82")))
NOTION_TASK_KEEP_SECONDS = int(float(os.getenv("NOTION_TASK_KEEP_SECONDS", "86400")))
EXPORT_TASK_KEEP_SECONDS = int(float(os.getenv("EXPORT_TASK_KEEP_SECONDS", "86400")))

_baidu_token_lock = threading.Lock()
_baidu_token: dict[str, Any] = {"value": "", "expire_at": 0.0}

for p in (UPLOADS_DIR, SESSIONS_DIR, EXPORTS_DIR, CACHE_DIR):
    p.mkdir(parents=True, exist_ok=True)


def _configure_logger() -> logging.Logger:
    logger = logging.getLogger("quiz_mistake")
    if logger.handlers:
        return logger

    level = getattr(logging, APP_LOG_LEVEL, logging.INFO)
    logger.setLevel(level)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    try:
        file_handler = logging.FileHandler(APP_LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception:
        pass

    logger.propagate = False
    return logger


logger = _configure_logger()

app = Flask(__name__)

_notion_tasks_lock = threading.Lock()
_notion_tasks: dict[str, dict[str, Any]] = {}
_export_tasks_lock = threading.Lock()
_export_tasks: dict[str, dict[str, Any]] = {}


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _notion_ready() -> bool:
    return bool(NOTION_API_KEY and (NOTION_DATABASE_ID or NOTION_DATA_SOURCE_ID))


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _sanitize_markdown_name(value: str) -> str:
    name = str(value or "").strip()
    if not name or not name.lower().endswith(".md"):
        return ""
    if "/" in name or "\\" in name:
        return ""
    return name


def _cleanup_export_tasks_locked() -> None:
    if EXPORT_TASK_KEEP_SECONDS <= 0:
        return
    now_ts = time.time()
    expired: list[str] = []
    for task_id, task in _export_tasks.items():
        status = str(task.get("status", ""))
        if status not in {"completed", "completed_with_errors", "failed"}:
            continue
        finished_ts = float(task.get("finished_ts", 0.0) or 0.0)
        if not finished_ts:
            continue
        if now_ts - finished_ts > EXPORT_TASK_KEEP_SECONDS:
            expired.append(task_id)
    for task_id in expired:
        _export_tasks.pop(task_id, None)


def _snapshot_export_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": str(task.get("id", "")),
        "session_id": str(task.get("session_id", "")),
        "status": str(task.get("status", "queued")),
        "progress_percent": round(float(task.get("progress", 0.0) or 0.0) * 100, 1),
        "current": str(task.get("current", "") or ""),
        "error": str(task.get("error", "") or ""),
        "question_total": int(task.get("question_total", 0) or 0),
        "question_prepared": int(task.get("question_prepared", 0) or 0),
        "question_done": int(task.get("question_done", 0) or 0),
        "last_ai_elapsed_sec": float(task.get("last_ai_elapsed_sec", 0.0) or 0.0),
        "ai_elapsed_total_sec": float(task.get("ai_elapsed_total_sec", 0.0) or 0.0),
        "created_at": str(task.get("created_at", "") or ""),
        "started_at": str(task.get("started_at", "") or ""),
        "finished_at": str(task.get("finished_at", "") or ""),
        "result": task.get("result"),
    }


def _create_export_task(payload: dict[str, Any]) -> str:
    session_id = str(payload.get("session_id", "")).strip()
    now_iso = _now_iso()
    now_ts = time.time()
    task_id = uuid4().hex
    task: dict[str, Any] = {
        "id": task_id,
        "session_id": session_id,
        "status": "queued",
        "progress": 0.0,
        "current": "等待开始...",
        "error": "",
        "question_total": 0,
        "question_prepared": 0,
        "question_done": 0,
        "last_ai_elapsed_sec": 0.0,
        "ai_elapsed_total_sec": 0.0,
        "created_at": now_iso,
        "created_ts": now_ts,
        "started_at": "",
        "started_ts": 0.0,
        "finished_at": "",
        "finished_ts": 0.0,
        "result": None,
        # worker 使用副本，避免请求对象后续变更。
        "payload": json.loads(json.dumps(payload, ensure_ascii=False)),
    }
    with _export_tasks_lock:
        _cleanup_export_tasks_locked()
        _export_tasks[task_id] = task
    return task_id


def _run_export_task(task_id: str) -> None:
    with _export_tasks_lock:
        task = _export_tasks.get(task_id)
        if not task:
            return
        task["status"] = "running"
        task["progress"] = 0.01
        task["current"] = "正在准备导出任务..."
        task["started_at"] = _now_iso()
        task["started_ts"] = time.time()
    logger.info("[export-task:%s] start session=%s", task_id, str(task.get("session_id", "")))

    payload = task.get("payload", {}) if isinstance(task.get("payload"), dict) else {}

    def _hook(event: dict[str, Any]) -> None:
        with _export_tasks_lock:
            t = _export_tasks.get(task_id)
            if not t:
                return
            question_total = int(event.get("question_total", t.get("question_total", 0)) or 0)
            question_prepared = int(event.get("question_prepared", t.get("question_prepared", 0)) or 0)
            question_done = int(event.get("question_done", t.get("question_done", 0)) or 0)
            last_ai_elapsed = float(event.get("last_ai_elapsed_sec", t.get("last_ai_elapsed_sec", 0.0)) or 0.0)
            ai_elapsed_total = float(event.get("ai_elapsed_total_sec", t.get("ai_elapsed_total_sec", 0.0)) or 0.0)
            current = str(event.get("current", t.get("current", "")) or "")

            if question_total > 0:
                prep_ratio = min(1.0, question_prepared / float(question_total))
                ai_ratio = min(1.0, question_done / float(question_total))
                progress = 0.25 * prep_ratio + 0.75 * ai_ratio
            else:
                progress = float(event.get("progress", t.get("progress", 0.0)) or 0.0)
            if event.get("phase") == "finalize":
                progress = max(progress, 0.98)

            t["question_total"] = question_total
            t["question_prepared"] = question_prepared
            t["question_done"] = question_done
            t["last_ai_elapsed_sec"] = last_ai_elapsed
            t["ai_elapsed_total_sec"] = ai_elapsed_total
            t["current"] = current
            t["progress"] = max(0.0, min(progress, 0.999))

    try:
        result = _run_export_pipeline(payload, progress_hook=_hook)
        with _export_tasks_lock:
            t = _export_tasks.get(task_id)
            if not t:
                return
            warnings = result.get("warnings", []) if isinstance(result, dict) else []
            t["status"] = "completed_with_errors" if warnings else "completed"
            t["progress"] = 1.0
            t["current"] = "导出完成"
            t["result"] = result
            t["finished_at"] = _now_iso()
            t["finished_ts"] = time.time()
        logger.info(
            "[export-task:%s] done session=%s question_count=%s warnings=%s",
            task_id,
            str(task.get("session_id", "")),
            int(result.get("question_count", 0) or 0),
            len(result.get("warnings", []) if isinstance(result, dict) else []),
        )
    except ApiError as exc:
        with _export_tasks_lock:
            t = _export_tasks.get(task_id)
            if not t:
                return
            t["status"] = "failed"
            t["error"] = exc.message
            t["current"] = "导出失败"
            t["finished_at"] = _now_iso()
            t["finished_ts"] = time.time()
        logger.exception("[export-task:%s] failed session=%s", task_id, str(task.get("session_id", "")))
    except Exception as exc:
        with _export_tasks_lock:
            t = _export_tasks.get(task_id)
            if not t:
                return
            t["status"] = "failed"
            t["error"] = str(exc)
            t["current"] = "导出失败"
            t["finished_at"] = _now_iso()
            t["finished_ts"] = time.time()
        logger.exception("[export-task:%s] failed session=%s", task_id, str(task.get("session_id", "")))


def _cleanup_notion_tasks_locked() -> None:
    if NOTION_TASK_KEEP_SECONDS <= 0:
        return
    now_ts = time.time()
    expired: list[str] = []
    for task_id, task in _notion_tasks.items():
        status = str(task.get("status", ""))
        if status not in {"completed", "completed_with_errors", "failed"}:
            continue
        finished_ts = float(task.get("finished_ts", 0.0) or 0.0)
        if not finished_ts:
            continue
        if now_ts - finished_ts > NOTION_TASK_KEEP_SECONDS:
            expired.append(task_id)
    for task_id in expired:
        _notion_tasks.pop(task_id, None)


def _refresh_notion_task_progress(task: dict[str, Any]) -> None:
    items = task.get("items", [])
    total = len(items)
    completed = 0
    success = 0
    failed = 0
    for row in items:
        status = str(row.get("status", ""))
        if status in {"success", "failed"}:
            completed += 1
        if status == "success":
            success += 1
        elif status == "failed":
            failed += 1
    task["total"] = total
    task["completed"] = completed
    task["success"] = success
    task["failed"] = failed
    task["progress"] = (completed / total) if total > 0 else 1.0
    task["updated_at"] = _now_iso()
    task["updated_ts"] = time.time()


def _snapshot_notion_task(task: dict[str, Any]) -> dict[str, Any]:
    items_out: list[dict[str, Any]] = []
    for row in task.get("items", []):
        items_out.append(
            {
                "index": row.get("index"),
                "title": row.get("title"),
                "markdown_name": row.get("markdown_name"),
                "status": row.get("status"),
                "page_id": row.get("page_id", ""),
                "page_url": row.get("page_url", ""),
                "final_title": row.get("final_title", ""),
                "id_value": row.get("id_value", ""),
                "error": row.get("error", ""),
                "steps": row.get("steps", []),
                "started_at": row.get("started_at", ""),
                "finished_at": row.get("finished_at", ""),
            }
        )
    return {
        "task_id": task.get("id", ""),
        "session_id": task.get("session_id", ""),
        "status": task.get("status", "queued"),
        "total": int(task.get("total", 0) or 0),
        "completed": int(task.get("completed", 0) or 0),
        "success": int(task.get("success", 0) or 0),
        "failed": int(task.get("failed", 0) or 0),
        "progress_percent": round(float(task.get("progress", 0.0) or 0.0) * 100, 1),
        "current": str(task.get("current", "") or ""),
        "error": str(task.get("error", "") or ""),
        "created_at": str(task.get("created_at", "") or ""),
        "started_at": str(task.get("started_at", "") or ""),
        "finished_at": str(task.get("finished_at", "") or ""),
        "items": items_out,
    }


def _create_notion_upload_task(session_id: str, entries: list[dict[str, str]]) -> str:
    now_iso = _now_iso()
    now_ts = time.time()
    task_id = uuid4().hex
    items: list[dict[str, Any]] = []
    for idx, row in enumerate(entries, start=1):
        items.append(
            {
                "index": idx,
                "title": str(row.get("title", f"错题 {idx}")).strip() or f"错题 {idx}",
                "markdown_name": str(row.get("markdown_name", "")).strip(),
                "status": "pending",
                "page_id": "",
                "page_url": "",
                "final_title": "",
                "id_value": "",
                "steps": [],
                "error": "",
                "started_at": "",
                "finished_at": "",
            }
        )
    task: dict[str, Any] = {
        "id": task_id,
        "session_id": session_id,
        "status": "queued",
        "error": "",
        "current": "",
        "created_at": now_iso,
        "created_ts": now_ts,
        "started_at": "",
        "started_ts": 0.0,
        "finished_at": "",
        "finished_ts": 0.0,
        "updated_at": now_iso,
        "updated_ts": now_ts,
        "total": len(items),
        "completed": 0,
        "success": 0,
        "failed": 0,
        "progress": 0.0,
        "items": items,
    }
    with _notion_tasks_lock:
        _cleanup_notion_tasks_locked()
        _notion_tasks[task_id] = task
    return task_id


def _run_notion_upload_task(task_id: str) -> None:
    with _notion_tasks_lock:
        task = _notion_tasks.get(task_id)
        if not task:
            return
        task["status"] = "running"
        task["started_at"] = _now_iso()
        task["started_ts"] = time.time()
        task["updated_at"] = task["started_at"]
        task["updated_ts"] = task["started_ts"]
    session_id = str(task.get("session_id", "") or "")
    logger.info("[notion-task:%s] start session=%s", task_id, session_id)

    try:
        for row in task.get("items", []):
            title = str(row.get("title", "") or "").strip()
            md_name = str(row.get("markdown_name", "") or "").strip()
            with _notion_tasks_lock:
                row["status"] = "running"
                row["started_at"] = _now_iso()
                task["current"] = title or md_name
                task["updated_at"] = _now_iso()
                task["updated_ts"] = time.time()
            logger.info("[notion-task:%s] item start markdown=%s title=%s", task_id, md_name, title)

            try:
                md_path = EXPORTS_DIR / session_id / md_name
                if not md_path.exists():
                    raise RuntimeError(f"Markdown 文件不存在：{md_name}")
                markdown_text = md_path.read_text(encoding="utf-8")
                result = upload_markdown_to_notion(markdown_text)
                with _notion_tasks_lock:
                    row["status"] = "success"
                    row["error"] = ""
                    row["page_id"] = str(result.get("page_id", "")).strip()
                    row["page_url"] = str(result.get("page_url", "")).strip()
                    row["final_title"] = str(result.get("title", "")).strip()
                    row["id_value"] = str(result.get("id_value", "")).strip()
                    row["steps"] = result.get("steps", [])
                    row["finished_at"] = _now_iso()
                    _refresh_notion_task_progress(task)
                logger.info("[notion-task:%s] item done markdown=%s page_id=%s", task_id, md_name, row.get("page_id", ""))
            except Exception as exc:
                err_text = str(exc)
                with _notion_tasks_lock:
                    row["status"] = "failed"
                    row["error"] = err_text
                    row["finished_at"] = _now_iso()
                    _refresh_notion_task_progress(task)
                logger.exception("[notion-task:%s] item failed markdown=%s", task_id, md_name)

        with _notion_tasks_lock:
            if int(task.get("failed", 0) or 0) > 0:
                task["status"] = "completed_with_errors"
            else:
                task["status"] = "completed"
            task["current"] = ""
            task["finished_at"] = _now_iso()
            task["finished_ts"] = time.time()
            task["updated_at"] = task["finished_at"]
            task["updated_ts"] = task["finished_ts"]
        logger.info(
            "[notion-task:%s] done success=%d failed=%d total=%d",
            task_id,
            int(task.get("success", 0) or 0),
            int(task.get("failed", 0) or 0),
            int(task.get("total", 0) or 0),
        )
    except Exception as exc:
        with _notion_tasks_lock:
            task["status"] = "failed"
            task["error"] = str(exc)
            task["current"] = ""
            task["finished_at"] = _now_iso()
            task["finished_ts"] = time.time()
            task["updated_at"] = task["finished_at"]
            task["updated_ts"] = task["finished_ts"]
        logger.exception("[notion-task:%s] fatal failed", task_id)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _extract_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            left, right = text.find("{"), text.rfind("}")
            if left >= 0 and right > left:
                try:
                    data = json.loads(text[left : right + 1])
                    return data if isinstance(data, dict) else {}
                except json.JSONDecodeError:
                    return {}
    return {}


def _resolve_baidu_token(force_refresh: bool = False) -> str:
    if not BAIDU_OCR_API_KEY or not BAIDU_OCR_SECRET_KEY:
        raise RuntimeError("缺少百度 OCR 鉴权配置，请设置 BAIDU_OCR_API_KEY/BAIDU_OCR_SECRET_KEY。")

    now = time.time()
    with _baidu_token_lock:
        if not force_refresh and _baidu_token["value"] and now < float(_baidu_token["expire_at"]):
            return str(_baidu_token["value"])

        resp = requests.get(
            BAIDU_OAUTH_URL,
            params={
                "grant_type": "client_credentials",
                "client_id": BAIDU_OCR_API_KEY,
                "client_secret": BAIDU_OCR_SECRET_KEY,
            },
            timeout=BAIDU_OCR_TIMEOUT,
        )
        resp.raise_for_status()
        data = _extract_json_object(resp.text)
        token = str(data.get("access_token", "")).strip()
        expires_in = int(_safe_float(data.get("expires_in"), 0))
        if not token:
            message = data.get("error_description") or data.get("error_msg") or "未返回 access_token"
            raise RuntimeError(f"获取百度 access_token 失败：{message}")

        _baidu_token["value"] = token
        _baidu_token["expire_at"] = time.time() + max(60, expires_in - 120)
        return token


def _line_looks_handwritten(item: dict[str, Any]) -> bool:
    text_type_keys = ["words_type", "text_type", "char_type", "type", "category", "recg_type"]
    for key in text_type_keys:
        raw = str(item.get(key, "")).strip().lower()
        if not raw:
            continue
        if "hand" in raw or "手写" in raw:
            return True

    word_type = str(item.get("word_type", "")).strip().lower()
    if word_type in {"handwriting", "handwrite"}:
        return True

    for key in ["source", "tag", "label"]:
        raw = str(item.get(key, "")).strip().lower()
        if "hand" in raw or "手写" in raw:
            return True

    return False


def _extract_confidence(item: dict[str, Any]) -> float:
    for key in ["confidence", "score", "prob", "probability"]:
        value = item.get(key)
        if isinstance(value, dict):
            for inner_key in ["average", "overall", "text", "score", "confidence"]:
                if inner_key in value:
                    return _safe_float(value.get(inner_key), 0.0)
        score = _safe_float(value, -1.0)
        if score >= 0:
            return score
    return -1.0


def _collect_text_lines(node: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(node, dict):
        word = node.get("word")
        words = node.get("words")
        text = node.get("text")
        if isinstance(word, str) and word.strip():
            out.append({"text": word.strip(), **node})
        elif isinstance(words, str) and words.strip():
            out.append({"text": words.strip(), **node})
        elif isinstance(text, str) and text.strip():
            out.append({"text": text.strip(), **node})

        for value in node.values():
            _collect_text_lines(value, out)
        return

    if isinstance(node, list):
        for item in node:
            _collect_text_lines(item, out)


def _extract_from_qus_result(raw_ocr: dict[str, Any], min_conf: float) -> str:
    q_items = raw_ocr.get("qus_result")
    if not isinstance(q_items, list):
        return ""

    lines: list[str] = []
    seen: set[str] = set()
    for q in q_items:
        if not isinstance(q, dict):
            continue
        elems = q.get("qus_element")
        if not isinstance(elems, list):
            continue
        for elem in elems:
            if not isinstance(elem, dict):
                continue
            words = elem.get("elem_word")
            if not isinstance(words, list):
                continue
            for w in words:
                if not isinstance(w, dict):
                    continue
                text = str(w.get("word", "")).strip()
                if not text or text in seen:
                    continue

                word_type = str(w.get("word_type", "")).strip().lower()
                if word_type and word_type not in {"print", "printed"}:
                    continue

                if _line_looks_handwritten(w):
                    continue
                confidence = _extract_confidence(w)
                if confidence >= 0 and confidence < min_conf:
                    continue
                if re.fullmatch(r"[×xX✓√✗]+", text):
                    continue

                seen.add(text)
                lines.append(text)

    return "\n".join(lines).strip()


def _filter_printed_text(raw_ocr: dict[str, Any], min_conf: float) -> str:
    structured = _extract_from_qus_result(raw_ocr, min_conf)
    if structured:
        return structured

    lines: list[dict[str, Any]] = []
    _collect_text_lines(raw_ocr, lines)

    kept: list[str] = []
    seen: set[str] = set()
    for row in lines:
        text = str(row.get("text", "")).strip()
        if not text:
            continue
        if text in seen:
            continue
        if _line_looks_handwritten(row):
            continue

        confidence = _extract_confidence(row)
        if confidence >= 0 and confidence < min_conf:
            continue
        if re.fullmatch(r"[×xX✓√✗]+", text):
            continue
        seen.add(text)
        kept.append(text)

    if kept:
        return "\n".join(kept).strip()
    return ""


def _call_baidu_ocr(image_path: Path) -> dict[str, Any]:
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")

    def _once(force_refresh_token: bool) -> dict[str, Any]:
        token = _resolve_baidu_token(force_refresh=force_refresh_token)
        resp = requests.post(
            BAIDU_OCR_URL,
            params={"access_token": token},
            data={"image": encoded},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=BAIDU_OCR_TIMEOUT,
        )
        resp.raise_for_status()
        return _extract_json_object(resp.text)

    data = _once(False)
    if int(_safe_float(data.get("error_code"), 0)) in {110, 111}:  # token invalid/expired
        data = _once(True)
    return data


def _require_github_config() -> None:
    if Github is None:
        raise RuntimeError("缺少 PyGithub 依赖，请先安装 requirements.txt。")
    missing: list[str] = []
    if not GITHUB_TOKEN:
        missing.append("GITHUB_TOKEN")
    if not GITHUB_REPO:
        missing.append("GITHUB_REPO")
    if not GITHUB_BRANCH:
        missing.append("GITHUB_BRANCH")
    if missing:
        raise RuntimeError("缺少 GitHub 配置：" + ", ".join(missing))


def _build_github_raw_url(repo_path: str) -> str:
    clean_path = quote(repo_path.strip().lstrip("/"), safe="/")
    return f"{GITHUB_RAW_BASE}/{GITHUB_REPO}/refs/heads/{GITHUB_BRANCH}/{clean_path}"


def _upload_file_to_github(local_path: Path, repo_path: str, commit_message: str) -> str:
    _require_github_config()
    begin = time.time()
    logger.info("[github] upload start path=%s file=%s", repo_path, local_path.name)
    gh = Github(GITHUB_TOKEN)
    repo = gh.get_repo(GITHUB_REPO)
    content = local_path.read_bytes()
    try:
        existing = repo.get_contents(repo_path, ref=GITHUB_BRANCH)
        repo.update_file(
            path=repo_path,
            message=commit_message,
            content=content,
            sha=existing.sha,
            branch=GITHUB_BRANCH,
        )
        action = "updated"
    except UnknownObjectException:
        repo.create_file(
            path=repo_path,
            message=commit_message,
            content=content,
            branch=GITHUB_BRANCH,
        )
        action = "created"
    except GithubException as exc:
        raise RuntimeError(f"GitHub 上传失败(status={getattr(exc, 'status', '?')}): {exc}") from exc

    raw_url = _build_github_raw_url(repo_path)
    logger.info("[github] upload done path=%s action=%s elapsed=%.2fs", repo_path, action, time.time() - begin)
    return raw_url


def _compress_image_for_upload(source_path: Path, target_path: Path) -> Path:
    if Image is None:
        raise RuntimeError("缺少 Pillow 依赖，无法进行图片压缩上传。")
    with Image.open(source_path) as img:
        image = img.convert("RGB")
        max_side = max(1, int(UPLOAD_COMPRESS_MAX_SIDE))
        w, h = image.size
        cur_max = max(w, h)
        if cur_max > max_side:
            scale = max_side / float(cur_max)
            nw = max(1, int(round(w * scale)))
            nh = max(1, int(round(h * scale)))
            image = image.resize((nw, nh), Image.Resampling.LANCZOS)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        quality = max(35, min(95, int(UPLOAD_COMPRESS_JPEG_QUALITY)))
        image.save(target_path, format="JPEG", quality=quality, optimize=True, progressive=True)

    try:
        src_size = source_path.stat().st_size
        dst_size = target_path.stat().st_size
        ratio = (dst_size / src_size) if src_size > 0 else 0.0
        logger.info(
            "[upload] compress %s -> %s src=%d dst=%d ratio=%.3f",
            source_path.name,
            target_path.name,
            src_size,
            dst_size,
            ratio,
        )
    except Exception:
        pass
    return target_path


def _require_notion_config() -> None:
    if NotionClient is None:
        raise RuntimeError("缺少 notion-client 依赖，请先安装 requirements.txt。")
    if not NOTION_API_KEY:
        raise RuntimeError("缺少 NOTION_API_KEY（或 NOTION_TOKEN）。")
    if not NOTION_DATABASE_ID and not NOTION_DATA_SOURCE_ID:
        raise RuntimeError("缺少 NOTION_DATABASE_ID 或 NOTION_DATA_SOURCE_ID。")


def _create_notion_client() -> NotionClient:
    _require_notion_config()
    return NotionClient(auth=NOTION_API_KEY)


def _normalize_notion_uuid(value: str) -> str:
    return value.strip().replace("-", "")


def _resolve_notion_parent_and_schema(notion: NotionClient) -> tuple[dict[str, str], dict[str, Any], str]:
    if NOTION_DATA_SOURCE_ID:
        if not hasattr(notion, "data_sources"):
            raise RuntimeError("notion-client 版本过低，不支持 data_sources。")
        ds_id = _normalize_notion_uuid(NOTION_DATA_SOURCE_ID)
        ds = notion.data_sources.retrieve(data_source_id=ds_id)
        return {"data_source_id": ds_id}, ds, "data_source"

    db_id = _normalize_notion_uuid(NOTION_DATABASE_ID)
    db = notion.databases.retrieve(database_id=db_id)
    props = db.get("properties")
    if isinstance(props, dict) and props:
        return {"database_id": db_id}, db, "database"

    ds_items = db.get("data_sources")
    if isinstance(ds_items, list) and ds_items:
        if not hasattr(notion, "data_sources"):
            raise RuntimeError("数据库为 data_sources 模式，请升级 notion-client。")
        first = ds_items[0]
        if isinstance(first, dict):
            ds_id = _normalize_notion_uuid(str(first.get("id", "")))
            if ds_id:
                ds = notion.data_sources.retrieve(data_source_id=ds_id)
                return {"data_source_id": ds_id}, ds, "data_source"

    raise RuntimeError("未找到可用的数据源，请设置 NOTION_DATA_SOURCE_ID。")


def _detect_notion_properties(database_info: dict[str, Any]) -> tuple[str, str]:
    props = database_info.get("properties", {})
    if not isinstance(props, dict):
        raise RuntimeError("Notion 数据库结构异常：properties 缺失。")

    title_prop = NOTION_TITLE_PROPERTY
    if not title_prop:
        for name, schema in props.items():
            if isinstance(schema, dict) and schema.get("type") == "title":
                title_prop = name
                break
    if not title_prop:
        raise RuntimeError("未找到 Notion 标题属性，请设置 NOTION_TITLE_PROPERTY。")

    id_prop = NOTION_ID_PROPERTY
    if id_prop and id_prop in props:
        return title_prop, id_prop

    for name, schema in props.items():
        if isinstance(schema, dict) and schema.get("type") == "unique_id":
            return title_prop, name
    return title_prop, id_prop


def _extract_property_plain_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            text_obj = item.get("text")
            if isinstance(text_obj, dict):
                content = str(text_obj.get("content", "")).strip()
                if content:
                    parts.append(content)
                    continue
            plain = str(item.get("plain_text", "")).strip()
            if plain:
                parts.append(plain)
        return "".join(parts).strip()
    return ""


def _extract_notion_id_value(prop: dict[str, Any]) -> str:
    if not isinstance(prop, dict):
        return ""
    p_type = str(prop.get("type", "")).strip()
    if p_type == "unique_id":
        uid = prop.get("unique_id")
        if isinstance(uid, dict):
            raw_prefix = uid.get("prefix")
            prefix = str(raw_prefix).strip() if raw_prefix else ""
            number = uid.get("number")
            num_text = str(number).strip() if number is not None else ""
            return f"{prefix}{num_text}" if (prefix or num_text) else ""
    if p_type == "rich_text":
        return _extract_property_plain_text(prop.get("rich_text"))
    if p_type == "title":
        return _extract_property_plain_text(prop.get("title"))
    if p_type == "number":
        num = prop.get("number")
        return str(num).strip() if num is not None else ""
    if p_type == "formula":
        formula = prop.get("formula")
        if isinstance(formula, dict):
            f_type = str(formula.get("type", "")).strip()
            if f_type == "string":
                return str(formula.get("string", "")).strip()
            if f_type == "number":
                num = formula.get("number")
                return str(num).strip() if num is not None else ""
    return ""


def _markdown_to_notion_blocks(markdown_text: str) -> list[dict[str, Any]]:
    if not MARTIAN_SCRIPT.exists():
        raise RuntimeError(f"缺少 Martian 脚本：{MARTIAN_SCRIPT}")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", encoding="utf-8", delete=False) as tmp:
        tmp.write(markdown_text or "")
        tmp_path = Path(tmp.name)

    try:
        proc = subprocess.run(
            ["node", str(MARTIAN_SCRIPT), str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
        )
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(msg or "Martian 转换失败。")
        data = _extract_json_object(proc.stdout)
        blocks = data.get("blocks")
        if not isinstance(blocks, list):
            raise RuntimeError("Martian 输出格式错误：缺少 blocks 数组。")
        return [b for b in blocks if isinstance(b, dict)]
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _chunk_list(values: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    if size <= 0:
        return [values]
    return [values[i : i + size] for i in range(0, len(values), size)]


def _extract_markdown_image_entries(markdown_text: str) -> list[tuple[str, str]]:
    text = markdown_text or ""
    pattern = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>https?://[^)\s]+)\)")
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in pattern.finditer(text):
        alt = str(match.group("alt") or "").strip()
        url = str(match.group("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        entries.append((alt, url))
    return entries


def _collect_image_urls_from_blocks(blocks: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            b_type = str(node.get("type", "")).strip()
            if b_type == "image":
                image_obj = node.get("image", {})
                if isinstance(image_obj, dict):
                    external = image_obj.get("external")
                    if isinstance(external, dict):
                        url = str(external.get("url", "")).strip()
                        if url:
                            found.add(url)
            for value in node.values():
                _walk(value)
            return
        if isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(blocks)
    return found


def _make_paragraph_block(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {"content": text[:1900]},
                }
            ]
        },
    }


def _make_external_image_block(url: str, caption: str) -> dict[str, Any]:
    image_block: dict[str, Any] = {
        "object": "block",
        "type": "image",
        "image": {
            "type": "external",
            "external": {"url": url},
        },
    }
    if caption:
        image_block["image"]["caption"] = [{"type": "text", "text": {"content": caption[:120]}}]
    return image_block


def _ensure_markdown_images_in_blocks(markdown_text: str, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = _extract_markdown_image_entries(markdown_text)
    if not entries:
        return blocks

    existed = _collect_image_urls_from_blocks(blocks)
    missing = [(alt, url) for alt, url in entries if url not in existed]
    if not missing:
        return blocks

    inject_blocks: list[dict[str, Any]] = [_make_paragraph_block("题目截图（自动补充）")]
    for idx, (alt, url) in enumerate(missing, start=1):
        caption = alt or f"题目截图 {idx}"
        inject_blocks.append(_make_external_image_block(url, caption))

    if blocks and str(blocks[0].get("type", "")).startswith("heading_"):
        return [blocks[0], *inject_blocks, *blocks[1:]]
    return [*inject_blocks, *blocks]


def upload_markdown_to_notion(markdown_text: str) -> dict[str, Any]:
    notion = _create_notion_client()
    parent_obj, schema_obj, target_type = _resolve_notion_parent_and_schema(notion)
    title_prop, id_prop = _detect_notion_properties(schema_obj)

    blocks = _markdown_to_notion_blocks(markdown_text)
    blocks = _ensure_markdown_images_in_blocks(markdown_text, blocks)

    steps: list[str] = []
    temp_title = "待命名"
    created = notion.pages.create(
        parent=parent_obj,
        properties={
            title_prop: {
                "title": [
                    {
                        "type": "text",
                        "text": {"content": temp_title},
                    }
                ]
            }
        },
    )
    page_id = str(created.get("id", "")).strip()
    page_url = str(created.get("url", "")).strip()
    if not page_id:
        raise RuntimeError("Notion 创建页面失败：未返回 page_id。")
    steps.append(f"已创建页面（{target_type}）")

    fetched = notion.pages.retrieve(page_id=page_id)
    props = fetched.get("properties", {})
    id_value = ""
    if isinstance(props, dict) and id_prop in props and isinstance(props[id_prop], dict):
        id_value = _extract_notion_id_value(props[id_prop])
    if not id_value:
        id_value = "NOID"
    steps.append(f"已检索 ID：{id_value}")

    date_part = datetime.now().strftime("%Y-%m%d")
    prefix = f"{NOTION_TITLE_PREFIX}-" if NOTION_TITLE_PREFIX else ""
    final_title = f"{date_part}-{prefix}{id_value}"
    notion.pages.update(
        page_id=page_id,
        properties={
            title_prop: {
                "title": [
                    {
                        "type": "text",
                        "text": {"content": final_title},
                    }
                ]
            }
        },
    )
    steps.append(f"已更新标题：{final_title}")

    if blocks:
        for chunk in _chunk_list(blocks, 100):
            notion.blocks.children.append(block_id=page_id, children=chunk)
        steps.append(f"已写入内容：{len(blocks)} blocks")
    else:
        steps.append("Markdown 内容为空，未写入 blocks")

    return {
        "page_id": page_id,
        "page_url": page_url,
        "title": final_title,
        "id_value": id_value,
        "steps": steps,
    }


def json_error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def session_path(session_id: str) -> Path:
    return SESSIONS_DIR / f"{session_id}.json"


def save_session(session_id: str, payload: dict[str, Any]) -> None:
    session_file = session_path(session_id)
    session_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_session(session_id: str) -> dict[str, Any] | None:
    p = session_path(session_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def is_allowed_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_SUFFIXES


def normalize_questions(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in raw_rows:
        figure_bboxes = [sanitize_bbox(b) for b in row.get("figure_bboxes", [])]
        out.append(
            {
                "question_no": str(row.get("question_no", "")).strip(),
                "question_bbox": sanitize_bbox(row.get("question_bbox", [0, 0, 0, 0])),
                "figure_bboxes": figure_bboxes,
                "has_figure": bool(figure_bboxes),
                "ocr_text": str(row.get("ocr_text", "")).strip(),
            }
        )
    return out


def strip_markdown_fence(text: str) -> str:
    value = (text or "").strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if len(lines) >= 2:
            value = "\n".join(lines[1:])
        if value.endswith("```"):
            value = value[:-3]
    return value.strip()


def render_question_template_markdown(
    *,
    question_image_url: str,
    ocr_text: str,
    figure_urls: list[str],
) -> str:
    lines: list[str] = []
    lines.append("## 1️⃣ 原题 Results")
    lines.append("")
    lines.append("- 题目截图/题干：")
    lines.append(f"  - 题目截图：![]({question_image_url})")
    if figure_urls:
        for idx, fig_url in enumerate(figure_urls, start=1):
            lines.append(f"  - 图形补充{idx}：![]({fig_url})")
    lines.append(f"  - 题干：{ocr_text or '（待补充）'}")
    lines.append("")
    lines.append("- 我的原答案：")
    lines.append("")
    lines.append("- 正确答案：")
    lines.append("")
    lines.append("")
    lines.append("## 2️⃣ 原因 Reason")
    lines.append("")
    lines.append("- 我认为错因：")
    lines.append("")
    lines.append("- 家长复核：")
    lines.append("")
    lines.append("- 本题核心遗漏：")
    lines.append("")
    lines.append("")
    lines.append("## 3️⃣ 针对性练习 Action")
    lines.append("")
    lines.append("- 练习1：")
    lines.append("  - 题目：")
    lines.append("  - 参考答案：")
    lines.append("  - 是否做对：")
    lines.append("  - 用时：")
    lines.append("  - 备注：")
    lines.append("- 练习2：")
    lines.append("  - 题目：")
    lines.append("  - 参考答案：")
    lines.append("  - 是否做对：")
    lines.append("  - 用时：")
    lines.append("  - 备注：")
    lines.append("- 练习3：")
    lines.append("  - 题目：")
    lines.append("  - 参考答案：")
    lines.append("  - 是否做对：")
    lines.append("  - 用时：")
    lines.append("  - 备注：")
    lines.append("")
    lines.append("")
    lines.append("## 4️⃣ 复盘 Review")
    lines.append("")
    lines.append("- 下次遇到什么信号要警觉：")
    lines.append("")
    lines.append("- 能迁移到哪些题型：")
    lines.append("")
    lines.append("- 是否可升阶：")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def _create_gemini_client() -> Any:
    if genai is None:
        raise RuntimeError("缺少 google-genai SDK，请先安装 requirements.txt。")
    if not GEMINI_API_KEY:
        raise RuntimeError("缺少 API Key，请设置 GEMINI_API_KEY 或 GOOGLE_API_KEY。")

    kwargs: dict[str, Any] = {"api_key": GEMINI_API_KEY}
    if GEMINI_BASE_URL and genai_types is not None and hasattr(genai_types, "HttpOptions"):
        try:
            opts_kwargs: dict[str, Any] = {"base_url": GEMINI_BASE_URL}
            if GEMINI_API_VERSION:
                opts_kwargs["api_version"] = GEMINI_API_VERSION
            kwargs["http_options"] = genai_types.HttpOptions(**opts_kwargs)
        except Exception:
            # 某些 SDK 版本可能不支持自定义 base_url / api_version
            pass

    logger.info(
        "[gemini] init client base_url=%s model=%s timeout=%.1fs",
        GEMINI_BASE_URL or "(default)",
        GEMINI_MODEL,
        GEMINI_REQUEST_TIMEOUT_SECONDS,
    )
    return genai.Client(**kwargs)


def _extract_gemini_text(resp: Any) -> str:
    text_attr = getattr(resp, "text", None)
    if isinstance(text_attr, str) and text_attr.strip():
        return text_attr.strip()

    candidates = getattr(resp, "candidates", None)
    if isinstance(candidates, list):
        parts: list[str] = []
        for cand in candidates:
            content = getattr(cand, "content", None)
            if not content:
                continue
            c_parts = getattr(content, "parts", None)
            if not isinstance(c_parts, list):
                continue
            for p in c_parts:
                text = getattr(p, "text", None)
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        if parts:
            return "\n".join(parts).strip()
    return ""


def call_gemini_fill_template(
    *,
    client: Any,
    model: str,
    question_index: int,
    question_image_path: Path,
    question_image_url: str,
    ocr_text: str,
    template_text: str,
    figure_urls: list[str],
) -> str:
    q_begin = time.time()
    logger.info("[gemini] q%s start", question_index)
    info_lines = [
        f"题号: {question_index}",
        f"题目图片URL: {question_image_url}",
        f"题目文本(用户编辑): {ocr_text or '（空）'}",
    ]
    if figure_urls:
        info_lines.append("图形URL:")
        info_lines.extend([f"- {u}" for u in figure_urls])
    info_text = "\n".join(info_lines).strip()
    effective_template = (template_text or DEFAULT_MARKDOWN_PROMPT_TEMPLATE).strip()

    user_payload = (
        "你是小学数学错题整理助手，擅长按模板输出结构化复盘 Markdown。\n"
        "请根据以下材料，严格按照模板输出最终 Markdown。\n\n"
        f"【模板】\n{effective_template}\n\n"
        f"【题目信息文本】\n{info_text}\n\n"
        "【图片说明】\n"
        "请结合附带图片识别题目内容；忽略手写批注、打勾打叉、红笔分数等非题干信息。\n\n"
        "要求：\n"
        "1) 必须保持模板四个章节与条目结构，不要删除条目。\n"
        "2) 填写“题目截图/题干、我的原答案、正确答案、我认为错因、家长复核、本题核心遗漏”。\n"
        "3) 将“解题思路解析”写入“家长复核”或“本题核心遗漏”中。\n"
        "4) 在“题目截图”行使用提供的题目图片URL。\n"
        "5) 信息不确定时写“（待家长补充）”。\n"
        "6) “针对性练习 Action”必须由你生成 3 道举一反三的类似题，围绕本题核心知识点，不能与原题完全相同。\n"
        "7) 三道练习题需体现难度递进（基础 -> 变式 -> 提升），并覆盖同一知识点的不同问法。\n"
        "8) 每道练习请填写“题目”和“参考答案”；“是否做对/用时/备注”保留给孩子练习记录，可填写“（待练习后填写）”。\n"
        "9) 只输出 Markdown 正文，不要代码围栏。\n"
    )
    gen_begin = time.time()
    if Image is None:
        raise RuntimeError("缺少 Pillow 依赖，无法加载题目图片给 Gemini。")
    with Image.open(question_image_path) as pil_img:
        kwargs: dict[str, Any] = {
            "model": model,
            "contents": [user_payload, pil_img.copy()],
        }
        if genai_types is not None and hasattr(genai_types, "GenerateContentConfig"):
            kwargs["config"] = genai_types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=2048,
            )
        resp = client.models.generate_content(**kwargs)
    logger.info("[gemini] q%s generate done elapsed=%.2fs", question_index, time.time() - gen_begin)

    content = _extract_gemini_text(resp)
    result = strip_markdown_fence(content)
    if not result:
        raise RuntimeError("Gemini 未返回有效 Markdown 内容。")
    logger.info("[gemini] q%s done total_elapsed=%.2fs output_chars=%d", question_index, time.time() - q_begin, len(result))
    return result


@app.route("/")
def index():
    return render_template("index.html")


@app.get("/api/ai-health")
def api_ai_health():
    begin = time.time()
    if genai is None:
        return jsonify(
            {
                "ok": False,
                "error": "缺少 google-genai SDK",
                "model": GEMINI_MODEL,
                "base_url": GEMINI_BASE_URL or "(default)",
                "latency_ms": int((time.time() - begin) * 1000),
            }
        )
    if not GEMINI_API_KEY:
        return jsonify(
            {
                "ok": False,
                "error": "缺少 API Key",
                "model": GEMINI_MODEL,
                "base_url": GEMINI_BASE_URL or "(default)",
                "latency_ms": int((time.time() - begin) * 1000),
            }
        )

    try:
        client = _create_gemini_client()
        kwargs: dict[str, Any] = {
            "model": GEMINI_MODEL,
            "contents": "ping",
        }
        if genai_types is not None and hasattr(genai_types, "GenerateContentConfig"):
            kwargs["config"] = genai_types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=1,
            )
        client.models.generate_content(**kwargs)
        latency_ms = int((time.time() - begin) * 1000)
        logger.info("[ai-health] ok model=%s latency_ms=%s", GEMINI_MODEL, latency_ms)
        return jsonify(
            {
                "ok": True,
                "model": GEMINI_MODEL,
                "base_url": GEMINI_BASE_URL or "(default)",
                "latency_ms": latency_ms,
            }
        )
    except Exception as exc:
        latency_ms = int((time.time() - begin) * 1000)
        logger.exception("[ai-health] failed latency_ms=%s", latency_ms)
        err_text = str(exc)
        if "url.not_found" in err_text or "/v1/v1beta/" in err_text:
            err_text = f"{err_text}；请检查 GEMINI_BASE_URL，建议留空使用默认 Google 端点。"
        if "API keys are not supported" in err_text or "UNAUTHENTICATED" in err_text:
            err_text = (
                f"{err_text}；请确认 GOOGLE_API_KEY/GEMINI_API_KEY 为 Google AI Studio Key"
                "（通常以 AIza 开头），而不是第三方网关 Key。"
            )
        return jsonify(
            {
                "ok": False,
                "error": err_text,
                "model": GEMINI_MODEL,
                "base_url": GEMINI_BASE_URL or "(default)",
                "latency_ms": latency_ms,
            }
        )


@app.route("/uploads/<path:filename>")
def get_upload(filename: str):
    return send_from_directory(UPLOADS_DIR, filename)


@app.route("/exports/<session_id>/<path:filename>")
def get_export(session_id: str, filename: str):
    return send_from_directory(EXPORTS_DIR / session_id, filename)


@app.post("/api/upload")
def api_upload():
    if Image is None:
        return json_error("缺少 Pillow 依赖，请先安装 requirements.txt。", 500)

    files = request.files.getlist("images")
    if not files:
        single = request.files.get("image")
        if single:
            files = [single]

    if not files:
        return json_error("请至少选择一张图片。")

    session_id = uuid4().hex
    images: list[dict[str, Any]] = []

    for f in files:
        if not f or not f.filename:
            continue
        if not is_allowed_image(f.filename):
            return json_error(f"文件不支持：{f.filename}")

        suffix = Path(secure_filename(f.filename)).suffix.lower() or ".jpg"
        image_id = uuid4().hex[:12]
        filename = f"{session_id}_{image_id}{suffix}"
        upload_path = UPLOADS_DIR / filename
        f.save(upload_path)

        try:
            with Image.open(upload_path) as img:
                image_width, image_height = img.size
        except Exception:
            return json_error(f"无法读取图片：{f.filename}")

        images.append(
            {
                "image_id": image_id,
                "image_name": f.filename,
                "stored_image": filename,
                "image_width": image_width,
                "image_height": image_height,
            }
        )

    if not images:
        return json_error("未检测到有效图片。")

    payload = {
        "session_id": session_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "images": images,
        "prompt_template": DEFAULT_MARKDOWN_PROMPT_TEMPLATE,
    }
    save_session(session_id, payload)

    return jsonify(
        {
            "ok": True,
            "session_id": session_id,
            "images": [
                {
                    **img,
                    "image_url": f"/uploads/{img['stored_image']}",
                }
                for img in images
            ],
            "default_prompt_template": DEFAULT_MARKDOWN_PROMPT_TEMPLATE,
            "default_model": DEFAULT_MODEL,
            "notion_enabled": _notion_ready(),
        }
    )


@app.post("/api/recognize-question")
def api_recognize_question():
    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get("session_id", "")).strip()
    image_id = str(payload.get("image_id", "")).strip()
    bbox = sanitize_bbox(payload.get("question_bbox", [0, 0, 0, 0]))

    if not session_id:
        return json_error("缺少 session_id。")
    if not image_id:
        return json_error("缺少 image_id。")

    session = load_session(session_id)
    if not session:
        return json_error("会话不存在，请重新上传图片。", 404)

    session_images: dict[str, dict[str, Any]] = {img["image_id"]: img for img in session.get("images", [])}
    image_info = session_images.get(image_id)
    if not image_info:
        return json_error("图片不存在。", 404)

    image_path = UPLOADS_DIR / image_info["stored_image"]
    if not image_path.exists():
        return json_error("源图片不存在。", 404)

    cache_dir = CACHE_DIR / session_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    crop_path = cache_dir / f"{image_id}_{uuid4().hex[:10]}.png"

    if not crop_by_norm_bbox(image_path, bbox, crop_path):
        return json_error("裁剪题目区域失败，请检查框选范围。")

    try:
        raw = _call_baidu_ocr(crop_path)
    except Exception as exc:
        return json_error(f"OCR 调用失败：{exc}")

    if int(_safe_float(raw.get("error_code"), 0)) > 0:
        error_msg = raw.get("error_msg") or "未知错误"
        return json_error(f"OCR 返回错误：{error_msg}")

    ocr_text = _filter_printed_text(raw, BAIDU_PRINTED_MIN_CONF)
    return jsonify(
        {
            "ok": True,
            "ocr_text": ocr_text,
            "crop_data_url": encode_image_as_data_url(crop_path),
        }
    )


def _run_export_pipeline(payload: dict[str, Any], progress_hook: Any | None = None) -> dict[str, Any]:
    req_begin = time.time()
    session_id = str(payload.get("session_id", "")).strip()
    if not session_id:
        raise ApiError("缺少 session_id。")

    session = load_session(session_id)
    if not session:
        raise ApiError("会话不存在，请重新上传图片。", 404)

    images_payload = payload.get("images")
    if not isinstance(images_payload, list):
        raise ApiError("images 格式错误。")
    logger.info("[export:%s] start images=%d", session_id, len(images_payload))

    prompt_template = str(payload.get("prompt_template") or session.get("prompt_template") or "").strip()
    if not prompt_template:
        prompt_template = DEFAULT_MARKDOWN_PROMPT_TEMPLATE

    question_total_hint = 0
    for image_input in images_payload:
        if not isinstance(image_input, dict):
            continue
        raw_questions = image_input.get("questions")
        if isinstance(raw_questions, list):
            question_total_hint += len(raw_questions)

    if progress_hook:
        progress_hook(
            {
                "phase": "prepare",
                "current": "正在准备题目裁剪与图片上传...",
                "question_total": question_total_hint,
                "question_prepared": 0,
                "question_done": 0,
                "ai_elapsed_total_sec": 0.0,
                "last_ai_elapsed_sec": 0.0,
            }
        )

    session_images: dict[str, dict[str, Any]] = {img["image_id"]: img for img in session.get("images", [])}
    out_dir = EXPORTS_DIR / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    global_index = 0
    question_records: list[dict[str, Any]] = []
    prepared_count = 0

    for img_idx, image_input in enumerate(images_payload, start=1):
        image_id = str(image_input.get("image_id", "")).strip()
        session_img = session_images.get(image_id)
        if not session_img:
            logger.warning("[export:%s] skip image_id=%s reason=not_in_session", session_id, image_id)
            continue

        image_path = UPLOADS_DIR / session_img["stored_image"]
        if not image_path.exists():
            logger.warning("[export:%s] skip image_id=%s reason=missing_file", session_id, image_id)
            continue

        raw_questions = image_input.get("questions")
        if not isinstance(raw_questions, list):
            logger.warning("[export:%s] skip image_id=%s reason=invalid_questions_payload", session_id, image_id)
            continue

        questions = normalize_questions(raw_questions)
        if not questions:
            logger.info("[export:%s] image_id=%s no_questions", session_id, image_id)
            continue

        for q_idx, question in enumerate(questions, start=1):
            logger.info("[export:%s] crop start img=%s q=%s", session_id, img_idx, q_idx)
            q_name = f"img{img_idx}_q{q_idx}_question.png"
            q_path = out_dir / q_name
            if not crop_by_norm_bbox(image_path, question["question_bbox"], q_path):
                logger.warning("[export:%s] crop failed img=%s q=%s", session_id, img_idx, q_idx)
                continue
            q_upload_name = f"img{img_idx}_q{q_idx}_question_upload.jpg"
            q_upload_path = out_dir / q_upload_name
            try:
                _compress_image_for_upload(q_path, q_upload_path)
            except Exception as exc:
                logger.exception("[export:%s] compress failed file=%s", session_id, q_name)
                raise ApiError(f"题目图片压缩失败（{q_name}）：{exc}")

            figure_files: list[str] = []
            figure_paths: list[Path] = []
            figure_urls: list[str] = []
            for fig_idx, bbox in enumerate(question["figure_bboxes"], start=1):
                fig_name = f"img{img_idx}_q{q_idx}_fig{fig_idx}.png"
                fig_path = out_dir / fig_name
                if crop_by_norm_bbox(image_path, bbox, fig_path):
                    figure_files.append(fig_name)
                    figure_paths.append(fig_path)

            github_base = GITHUB_IMAGE_DIR or "images"
            q_repo_path = f"{github_base}/{session_id}/{q_upload_name}"
            try:
                q_url = _upload_file_to_github(
                    q_upload_path,
                    q_repo_path,
                    commit_message=f"upload question image {session_id}/{q_upload_name}",
                )
            except Exception as exc:
                logger.exception("[export:%s] github upload failed file=%s", session_id, q_name)
                raise ApiError(f"GitHub 上传失败（{q_name}）：{exc}")

            for fig_name, fig_path in zip(figure_files, figure_paths):
                fig_upload_name = f"{Path(fig_name).stem}_upload.jpg"
                fig_upload_path = out_dir / fig_upload_name
                try:
                    _compress_image_for_upload(fig_path, fig_upload_path)
                except Exception as exc:
                    logger.exception("[export:%s] compress failed file=%s", session_id, fig_name)
                    raise ApiError(f"图形图片压缩失败（{fig_name}）：{exc}")
                fig_repo_path = f"{github_base}/{session_id}/{fig_upload_name}"
                try:
                    fig_url = _upload_file_to_github(
                        fig_upload_path,
                        fig_repo_path,
                        commit_message=f"upload figure image {session_id}/{fig_upload_name}",
                    )
                except Exception as exc:
                    logger.exception("[export:%s] github upload failed file=%s", session_id, fig_name)
                    raise ApiError(f"GitHub 上传失败（{fig_name}）：{exc}")
                figure_urls.append(fig_url)

            global_index += 1
            question_records.append(
                {
                    "index": global_index,
                    "image_name": session_img["image_name"],
                    "question_no": question.get("question_no", ""),
                    "ocr_text": question.get("ocr_text", ""),
                    "q_name": q_name,
                    "q_path": q_path,
                    "q_url": q_url,
                    "figure_files": figure_files,
                    "figure_paths": figure_paths,
                    "figure_urls": figure_urls,
                    "md_name": f"q{global_index}.md",
                }
            )
            prepared_count += 1
            if progress_hook:
                progress_hook(
                    {
                        "phase": "prepare",
                        "current": f"已完成题目准备 {prepared_count}/{max(global_index, question_total_hint or 1)}",
                        "question_total": max(global_index, question_total_hint),
                        "question_prepared": prepared_count,
                        "question_done": 0,
                        "ai_elapsed_total_sec": 0.0,
                        "last_ai_elapsed_sec": 0.0,
                    }
                )
            logger.info("[export:%s] prepared q_index=%s image=%s", session_id, global_index, session_img["image_name"])

    markdown_urls: list[dict[str, str]] = []
    warnings: list[str] = []
    index_lines: list[str] = []
    index_lines.append("# 错题 Markdown 索引")
    index_lines.append("")
    index_lines.append(f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    index_lines.append(f"- 题目数量: {global_index}")
    index_lines.append("")

    client: Any = None
    if question_records:
        try:
            client = _create_gemini_client()
        except Exception as exc:
            logger.exception("[export:%s] gemini init failed", session_id)
            raise ApiError(f"导出失败：{exc}")

    ai_done = 0
    ai_elapsed_total = 0.0
    if progress_hook:
        progress_hook(
            {
                "phase": "ai",
                "current": f"准备开始 AI 生成（共 {global_index} 题）",
                "question_total": global_index,
                "question_prepared": prepared_count,
                "question_done": ai_done,
                "ai_elapsed_total_sec": ai_elapsed_total,
                "last_ai_elapsed_sec": 0.0,
            }
        )

    for rec in question_records:
        ocr_text = str(rec.get("ocr_text", "")).strip()
        if progress_hook:
            progress_hook(
                {
                    "phase": "ai",
                    "current": f"AI 生成中 {rec['index']}/{global_index}",
                    "question_total": global_index,
                    "question_prepared": prepared_count,
                    "question_done": ai_done,
                    "ai_elapsed_total_sec": ai_elapsed_total,
                    "last_ai_elapsed_sec": 0.0,
                }
            )
        ai_begin = time.time()
        try:
            content = call_gemini_fill_template(
                client=client,
                model=GEMINI_MODEL,
                question_index=rec["index"],
                question_image_path=rec["q_path"],
                question_image_url=rec["q_url"],
                ocr_text=ocr_text,
                template_text=prompt_template,
                figure_urls=rec["figure_urls"],
            )
        except Exception as exc:
            logger.exception("[export:%s] q%s ai_generate_failed", session_id, rec["index"])
            warnings.append(f"错题 {rec['index']} AI 生成失败，已使用模板占位：{exc}")
            content = render_question_template_markdown(
                question_image_url=rec["q_url"],
                ocr_text=ocr_text,
                figure_urls=rec["figure_urls"],
            )
        ai_elapsed = time.time() - ai_begin
        ai_done += 1
        ai_elapsed_total += ai_elapsed
        if progress_hook:
            progress_hook(
                {
                    "phase": "ai",
                    "current": f"AI 完成 {ai_done}/{global_index}，本题耗时 {ai_elapsed:.1f}s",
                    "question_total": global_index,
                    "question_prepared": prepared_count,
                    "question_done": ai_done,
                    "ai_elapsed_total_sec": ai_elapsed_total,
                    "last_ai_elapsed_sec": ai_elapsed,
                }
            )

        q_md_path = out_dir / rec["md_name"]
        q_md_path.write_text(content, encoding="utf-8")
        q_md_url = f"/exports/{session_id}/{rec['md_name']}"
        markdown_urls.append({"title": f"错题 {rec['index']}", "url": q_md_url})
        index_lines.append(f"- [错题 {rec['index']}（{rec['image_name']}）]({rec['md_name']})")

    if global_index == 0:
        index_lines.append("当前没有可导出的手动标注错题。")

    if progress_hook:
        progress_hook(
            {
                "phase": "finalize",
                "current": "正在写入 Markdown 文件...",
                "question_total": global_index,
                "question_prepared": prepared_count,
                "question_done": ai_done,
                "ai_elapsed_total_sec": ai_elapsed_total,
                "last_ai_elapsed_sec": 0.0,
            }
        )

    markdown = "\n".join(index_lines).strip() + "\n"
    md_path = out_dir / "mistakes.md"
    md_path.write_text(markdown, encoding="utf-8")

    session["prompt_template"] = prompt_template
    session["last_export_at"] = datetime.now().isoformat(timespec="seconds")
    save_session(session_id, session)
    logger.info(
        "[export:%s] done question_count=%d warnings=%d elapsed=%.2fs",
        session_id,
        global_index,
        len(warnings),
        time.time() - req_begin,
    )

    return {
        "ok": True,
        "markdown_url": f"/exports/{session_id}/mistakes.md",
        "markdown_urls": markdown_urls,
        "export_dir": str(out_dir),
        "question_count": global_index,
        "warnings": warnings,
    }


@app.post("/api/export")
def api_export():
    payload = request.get_json(silent=True) or {}
    try:
        result = _run_export_pipeline(payload)
    except ApiError as exc:
        return json_error(exc.message, exc.status)
    except Exception as exc:
        logger.exception("[export:-] fatal failed")
        return json_error(f"导出失败：{exc}")
    return jsonify(result)


@app.post("/api/export/tasks")
def api_export_task_start():
    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get("session_id", "")).strip()
    if not session_id:
        return json_error("缺少 session_id。")
    session = load_session(session_id)
    if not session:
        return json_error("会话不存在，请重新上传图片。", 404)
    images_payload = payload.get("images")
    if not isinstance(images_payload, list):
        return json_error("images 格式错误。")

    task_id = _create_export_task(payload)
    thread = threading.Thread(target=_run_export_task, args=(task_id,), daemon=True)
    thread.start()
    logger.info("[export-task:%s] created session=%s", task_id, session_id)
    with _export_tasks_lock:
        task = _export_tasks.get(task_id, {})
        snapshot = _snapshot_export_task(task) if task else {}
    return jsonify({"ok": True, "task_id": task_id, "task": snapshot})


@app.get("/api/export/tasks/<task_id>")
def api_export_task_status(task_id: str):
    with _export_tasks_lock:
        _cleanup_export_tasks_locked()
        task = _export_tasks.get(task_id)
        if not task:
            return json_error("任务不存在或已过期。", 404)
        snapshot = _snapshot_export_task(task)
    return jsonify({"ok": True, "task": snapshot})


@app.post("/api/notion-upload/tasks")
def api_notion_upload_task_start():
    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get("session_id", "")).strip()
    raw_items = payload.get("items")
    if not _notion_ready():
        return json_error("Notion 未配置（缺少 NOTION_API_KEY/NOTION_DATABASE_ID 或 NOTION_DATA_SOURCE_ID）。")
    if not session_id:
        return json_error("缺少 session_id。")
    if not isinstance(raw_items, list) or not raw_items:
        return json_error("缺少 items。")

    entries: list[dict[str, str]] = []
    invalid: list[str] = []
    for idx, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            invalid.append(f"第 {idx} 项格式错误")
            continue
        md_name = _sanitize_markdown_name(item.get("markdown_name", ""))
        title = str(item.get("title", f"错题 {idx}")).strip() or f"错题 {idx}"
        if not md_name:
            invalid.append(f"{title}：markdown_name 不合法")
            continue
        md_path = EXPORTS_DIR / session_id / md_name
        if not md_path.exists():
            invalid.append(f"{title}：Markdown 不存在（{md_name}）")
            continue
        entries.append({"markdown_name": md_name, "title": title})
    if not entries:
        detail = "；".join(invalid) if invalid else "未找到可上传的 Markdown。"
        return json_error(detail)

    task_id = _create_notion_upload_task(session_id, entries)
    thread = threading.Thread(target=_run_notion_upload_task, args=(task_id,), daemon=True)
    thread.start()
    logger.info(
        "[notion-task:%s] created session=%s total=%d invalid=%d",
        task_id,
        session_id,
        len(entries),
        len(invalid),
    )
    with _notion_tasks_lock:
        task = _notion_tasks.get(task_id, {})
        snapshot = _snapshot_notion_task(task) if task else {}
    return jsonify(
        {
            "ok": True,
            "task_id": task_id,
            "task": snapshot,
            "invalid_items": invalid,
        }
    )


@app.get("/api/notion-upload/tasks/<task_id>")
def api_notion_upload_task_status(task_id: str):
    with _notion_tasks_lock:
        _cleanup_notion_tasks_locked()
        task = _notion_tasks.get(task_id)
        if not task:
            return json_error("任务不存在或已过期。", 404)
        snapshot = _snapshot_notion_task(task)
    return jsonify({"ok": True, "task": snapshot})


@app.post("/api/notion-upload")
def api_notion_upload():
    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get("session_id", "")).strip()
    markdown_name = str(payload.get("markdown_name", "")).strip()
    logger.info("[notion:%s] upload start markdown=%s", session_id or "-", markdown_name or "-")

    if not session_id:
        return json_error("缺少 session_id。")
    if not markdown_name:
        return json_error("缺少 markdown_name。")
    if not markdown_name.lower().endswith(".md"):
        return json_error("markdown_name 必须是 .md 文件。")
    if "/" in markdown_name or "\\" in markdown_name:
        return json_error("markdown_name 不合法。")

    md_path = EXPORTS_DIR / session_id / markdown_name
    if not md_path.exists():
        return json_error("Markdown 文件不存在。", 404)

    try:
        result = upload_markdown_to_notion(md_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.exception("[notion:%s] upload failed markdown=%s", session_id, markdown_name)
        return json_error(f"上传 Notion 失败：{exc}")
    logger.info("[notion:%s] upload done markdown=%s page_id=%s", session_id, markdown_name, result.get("page_id"))

    return jsonify(
        {
            "ok": True,
            "markdown_name": markdown_name,
            "page_id": result["page_id"],
            "page_url": result["page_url"],
            "title": result["title"],
            "id_value": result["id_value"],
            "steps": result["steps"],
        }
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=7860, debug=True)
