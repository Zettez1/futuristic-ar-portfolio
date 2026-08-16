"""FastStart Digital portfolio backend.

Serves static frontend + JSON APIs:
  POST /api/lead        - lead collection (chat agent + contact form)
  POST /api/chat        - simple rule-based AI agent reply
  GET  /api/calc/quote  - live IT-project quote calculator

Live Python quote engine for the interactive tools section.
"""
from __future__ import annotations

import io
import json
import math
import os
import subprocess
import sys
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
LEADS_FILE = DATA_DIR / "leads.json"
_lock = threading.Lock()

@asynccontextmanager
async def _lifespan(app: FastAPI):
    _start_bot()
    yield
    _stop_bot()


app = FastAPI(title="FastStart Digital Portfolio", version="1.1.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- leads ----
class Lead(BaseModel):
    name: str | None = None
    contact: str | None = None
    type: str | None = None
    budget: str | None = None
    message: str | None = None
    source: str = "site"
    page: str | None = None
    ts: str | None = None


def _save_lead(payload: dict) -> None:
    payload = {k: (v.strip() if isinstance(v, str) else v) for k, v in payload.items() if v}
    payload["ts"] = payload.get("ts") or datetime.now(timezone.utc).isoformat()
    with _lock:
        rows = []
        if LEADS_FILE.exists():
            try:
                rows = json.loads(LEADS_FILE.read_text("utf-8"))
            except json.JSONDecodeError:
                rows = []
        rows.append(payload)
        LEADS_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), "utf-8")
    print(f"[LEAD] {payload.get('type','?')} | {payload.get('name','?')} | {payload.get('contact','?')} | {payload.get('budget','?')}")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "faststart-portfolio", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/leads")
def list_leads(limit: int = 50) -> dict:
    """All leads in plain sight — for demo/CRM review. Protect in production."""
    with _lock:
        rows = json.loads(LEADS_FILE.read_text("utf-8")) if LEADS_FILE.exists() else []
    return {"count": len(rows), "leads": rows[-limit:][::-1]}


@app.post("/api/lead")
def create_lead(lead: Lead) -> dict:
    _save_lead(lead.model_dump(exclude_none=True))
    return {"ok": True, "accepted": True}


# ----------------------------------------------------------------- chat ----
class ChatMessage(BaseModel):
    message: str


@app.post("/api/chat")
def chat(message: ChatMessage) -> dict:
    """Tiny deterministic agent — replies to the most common asks."""
    text = message.message.lower().strip()
    if any(k in text for k in ("сайт", "лендінг", "магазин", "застосунок", "веб", "платформ", "crm")):
        reply = ("Для веб-розробки: ТЗ і прототип — від 5 днів, MVP — від 4 тижнів. "
                 "Опишіть продукт і функціонал — порахую обсяг робіт.")
        price_hint = "від 12 000 грн за лендінг/full-stack по ТЗ"
    elif any(k in text for k in ("ar", "3d", "візуалізац", "модел", "usdz", "glb")):
        reply = "3D/WebAR-пакет: модель + інтерактив — від 6 000 грн. Формати: .glb для Android, .usdz для iOS."
        price_hint = "6 000 – 25 000 грн"
    elif any(k in text for k in ("бот", "ai", "агент", "чат", "автоматиз", "парсер", "трейдинг", "торгов", "крипто", "crypto", "trade")):
        reply = ("Розробка ботів від 20 000 грн: Telegram-боти, парсери, трейдинг-алгоритми (на сторінці — "
                 "живий трейдинг-бот у терміналі), AI-агенти та генератори лідів. Деплой на хмару безкоштовно.")
        price_hint = "20 000 – 120 000 грн"
    elif any(k in text for k in ("ціна", "вартість", "бюджет", "кільки кошту", "прайс")):
        reply = "Орієнтовні чеки: лендінг — від 12 000 грн, 3D/WebAR — 6 000–25 000 грн, AI-агенти — від 20 000 грн."
        price_hint = "уточніть ТЗ для точної цифри"
    elif any(k in text for k in ("привіт", "добрий", "hello", "хай", "вітаю")):
        reply = "Вітаю! Розкажіть про задачу — порахую орієнтовну вартість і передам продакт-менеджеру."
        price_hint = None
    else:
        reply = ("Записала повідомлення: менеджер підготує розрахунок у КП. "
                 "Поки що — який формат вас цікавить: веб-сайт, 3D/AR чи AI-агент?")
        price_hint = None
    _save_lead({"message": text, "source": "api-chat"})
    return {"ok": True, "reply": reply, "price_hint": price_hint}


# ------------------------------------------------------ quote calculator ---
# Typed base effort (developer-hours) per product type; complexity and team
# size affect delivery time. Rate: 850 UAH/hour.
QUOTE_MODEL = {
    "landing": {"label": "Лендінг",        "hours": 16,  "weeks_min": 1},
    "site":    {"label": "Сайт",           "hours": 90,  "weeks_min": 2},
    "app":     {"label": "Веб-застосунок", "hours": 260, "weeks_min": 5},
    "webar":   {"label": "Web3D/AR",       "hours": 120, "weeks_min": 3},
    "ai":      {"label": "AI-агент",       "hours": 180, "weeks_min": 4},
}
COMPLEXITY_FACTOR = {1: 1.0, 2: 1.55, 3: 2.4}
COMPLEXITY_LABEL = {1: "базова", 2: "середня", 3: "складна"}
RATE_UAH_HOUR = 850.0
HOURS_PER_WEEK = 38.0


@app.get("/api/calc/quote")
def calc_quote(ptype: str = "landing", team: int = 1, complexity: int = 1):
    """Instant IT-project quote: effort, price, timeline, support.

    price   = hours * rate
    weeks   = max(weeks_min, ceil(hours * factor / (team * hours_per_week)))
    support = ~15% of price per month during warranty.
    """
    if ptype not in QUOTE_MODEL:
        raise HTTPException(400, f"Unknown product type '{ptype}', use one of {sorted(QUOTE_MODEL)}")
    if not (1 <= team <= 12):
        raise HTTPException(400, "team must be 1..12 developers")
    if complexity not in COMPLEXITY_FACTOR:
        raise HTTPException(400, "complexity must be 1..3")

    base = QUOTE_MODEL[ptype]
    factor = COMPLEXITY_FACTOR[complexity]
    hours = round(base["hours"] * factor)
    cost = round(hours * RATE_UAH_HOUR)
    weeks = max(base["weeks_min"], math.ceil(hours / max(team, 1) / HOURS_PER_WEEK))
    support_month = round(cost * 0.15)

    return {
        "ok": True,
        "type": ptype,
        "type_label": base["label"],
        "complexity": complexity,
        "complexity_label": COMPLEXITY_LABEL[complexity],
        "team": team,
        "hours": hours,
        "cost": cost,
        "weeks": weeks,
        "support_month": support_month,
        "rate_uah_hour": RATE_UAH_HOUR,
        "hours_per_week": HOURS_PER_WEEK,
        "from_price": cost,
        "params": {"rate": f"{RATE_UAH_HOUR:g} UAH/h", "norm": "робочий тиждень 38 год"},
    }


# ------------------------------------------------------------ bot demo ----
# BTrade — live trading-bot demo. A supervised child process streams its
# console lines into an in-memory ring buffer; the site renders them in a
# terminal widget. Runs in PAPER mode (MEXC paper API, no real orders).
BOT_DIR = BASE_DIR / "BTrade"
BOT_ENABLED = os.getenv("BOT_ENABLED", "1") == "1"

_bot_lines: deque[dict] = deque(maxlen=800)  # {"n": int, "ts": float, "text": str}
_bot_lock = threading.Lock()
_bot_state: dict = {
    "running": False, "pid": None, "started_at": None, "restarts": 0,
    "last_exit": None, "last_error": None, "line_no": 0,
}
_bot_stop = threading.Event()


def _bot_append(text: str) -> None:
    with _bot_lock:
        _bot_state["line_no"] += 1
        _bot_lines.append({"n": _bot_state["line_no"], "ts": time.time(), "text": text})


def _bot_worker() -> None:
    env = os.environ.copy()
    env.update({"DOTENV": ".env.mexc-paper", "SCHEDULE_ENABLED": "0", "PYTHONUNBUFFERED": "1"})
    while not _bot_stop.is_set():
        try:
            proc = subprocess.Popen(
                [sys.executable, "main.py"],
                cwd=str(BOT_DIR),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except Exception as exc:
            with _bot_lock:
                _bot_state["running"] = False
                _bot_state["last_error"] = repr(exc)
            time.sleep(10)
            continue
        with _bot_lock:
            _bot_state.update({"running": True, "pid": proc.pid,
                               "started_at": time.time(), "last_error": None})
        _bot_append(f"[supervisor] BTrade запущен (pid={proc.pid}, PAPER-MODE)")
        stream = io.TextIOWrapper(proc.stdout, encoding="utf-8", errors="replace")
        while True:
            line = stream.readline()
            if not line:
                break
            _bot_append(line.rstrip("\r\n"))
        proc.wait()
        stream.close()
        with _bot_lock:
            _bot_state["running"] = False
            _bot_state["last_exit"] = proc.returncode
        if _bot_stop.is_set():
            break
        _bot_state["restarts"] += 1
        _bot_append(f"[supervisor] бот завершился (код {proc.returncode}), перезапуск через 5 c…")
        time.sleep(5)


def _start_bot() -> None:
    if not BOT_ENABLED or BOT_DIR.joinpath("main.py").exists() is False:
        return
    if _bot_state.get("running"):
        return
    _bot_stop.clear()
    threading.Thread(target=_bot_worker, name="btrade", daemon=True).start()


def _stop_bot() -> None:
    _bot_stop.set()


# ------------------------------------------------------------- frontend ----
@app.get("/api/bot/status")
def bot_status() -> dict:
    with _bot_lock:
        state = dict(_bot_state)
    state["enabled"] = BOT_ENABLED
    state["buffer_lines"] = len(_bot_lines)
    if state.get("started_at"):
        state["uptime_s"] = round(time.time() - state["started_at"], 1)
    return {"ok": True, "bot": state}


@app.get("/api/bot/logs")
def bot_logs(after: int = 0, limit: int = 300) -> dict:
    limit = max(1, min(limit, 500))
    with _bot_lock:
        lines = [line for line in _bot_lines if line["n"] > after][-limit:]
        last_no = _bot_state["line_no"]
        running = _bot_state["running"]
    return {"ok": True, "seq": last_no, "running": running, "lines": lines}


# ------------------------------------------------------------- frontend ----
import mimetypes

mimetypes.add_type("model/vnd.usdz+zip", ".usdz")
mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("model/gltf+json", ".gltf")

STATIC_DIR = BASE_DIR / "static"
if (BASE_DIR / "index.html").exists():
    STATIC_DIR = BASE_DIR


class ModelStaticFiles(StaticFiles):
    """Models must never be cached: stale 304 replies keep old
    'application/octet-stream' content-type and break AR Quick Look."""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        path = str(full_path).lower()
        if path.endswith((".usdz", ".glb", ".gltf")):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
            response.headers["Content-Disposition"] = "inline"
        elif path.endswith((".html", ".js", ".css")):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


app.mount(
    "/",
    ModelStaticFiles(directory=str(STATIC_DIR), html=True),
    name="frontend",
)


def run():
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    run()