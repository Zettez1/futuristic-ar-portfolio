"""VERTEX portfolio backend.

Serves static frontend + JSON APIs:
  POST /api/lead        - lead collection (chat agent + contact form)
  POST /api/chat        - simple rule-based AI agent reply
  GET  /api/calc/beam   - live steel beam check (ДСТУ 8239 I-beams)

Live Python engineering calc for the interactive tools section.
"""
from __future__ import annotations

import json
import os
import threading
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

app = FastAPI(title="VERTEX Portfolio", version="1.0.0")

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
    return {"status": "ok", "service": "vertex-portfolio", "time": datetime.now(timezone.utc).isoformat()}


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
    if any(k in text for k in ("ангар", "каркас", "навіс", "будівля", "склад")):
        reply = ("Для металоконструкцій строк КМ/КМД — від 10 робочих днів, "
                 "розрахунок кошторису — 48 годин. Який об'єкт і розміри?")
        price_hint = "від 15 000 грн за документацію"
    elif any(k in text for k in ("ar", "3d", "візуалізац", "модел", "usdz", "glb")):
        reply = "3D/WebAR-пакет: модель + інтерактив — від 6 000 грн. Формати: .glb для Android, .usdz для iOS."
        price_hint = "6 000 – 25 000 грн"
    elif any(k in text for k in ("бот", "ai", "агент", "чат", "автоматиз", "парсер")):
        reply = "AI-агенти від 20 000 грн: Telegram-боти, парсери, генератори лідів, інтеграції з CRM."
        price_hint = "20 000 – 120 000 грн"
    elif any(k in text for k in ("ціна", "вартість", "бюджет", "кільки кошту", "прайс")):
        reply = "Орієнтовні чеки: металоконструкції — від 15 000 грн, 3D/WebAR — 6 000–25 000 грн, AI-агенти — від 20 000 грн."
        price_hint = "уточніть ТЗ для точної цифри"
    elif any(k in text for k in ("привіт", "добрий", "hello", "хай", "вітаю")):
        reply = "Вітаю! Розкажіть про задачу — порахую орієнтовну вартість і передам інженеру."
        price_hint = None
    else:
        reply = ("Записала повідомлення: інженер підготує розрахунок у КП. "
                 "Поки що — який формат вас цікавить: металоконструкції, 3D/AR чи AI-агент?")
        price_hint = None
    _save_lead({"message": text, "source": "api-chat"})
    return {"ok": True, "reply": reply, "price_hint": price_hint}


# ------------------------------------------------------ beam calculator ----
# ДСТУ/ГОСТ 8239 I-beams: (h_mm, b_mm, s_mm, t_mm, Ix_см4, Wx_см3, mass_kg/m)
I_BEAMS = {
    10: (100, 55, 4.5, 7.2, 198, 39.7, 9.46),
    12: (120, 64, 4.8, 7.3, 350, 58.4, 11.50),
    14: (140, 73, 4.9, 7.5, 572, 81.7, 13.70),
    16: (160, 81, 5.0, 7.8, 873, 109.0, 15.90),
    18: (180, 90, 5.1, 8.1, 1290, 143.0, 18.40),
    20: (200, 100, 5.2, 8.4, 1840, 184.0, 21.00),
    22: (220, 110, 5.4, 8.7, 2550, 232.0, 24.00),
    24: (240, 115, 5.6, 9.5, 3460, 289.0, 27.30),
    27: (270, 125, 6.0, 9.8, 5010, 371.0, 31.50),
    30: (300, 135, 6.5, 10.2, 7080, 472.0, 36.50),
    36: (360, 145, 7.5, 12.3, 13380, 743.0, 48.60),
    40: (400, 155, 8.3, 13.0, 19062, 953.0, 57.00),
}


@app.get("/api/calc/beam")
def calc_beam(profile: int = 20, length: float = 6.0, load: float = 25.0):
    """Simply supported steel I-beam under UDL.

    M = q*L^2/8; sigma = M/W <= R_y(240 MPa); deflection f = 5qL^4/(384EI) <= L/250.
    """
    if profile not in I_BEAMS:
        raise HTTPException(400, f"Unknown profile I{profile}, use one of {sorted(I_BEAMS)}")
    if not (2.0 <= length <= 15.0):
        raise HTTPException(400, "length must be 2..15 m")
    if not (1.0 <= load <= 200.0):
        raise HTTPException(400, "load must be 1..200 kN/m")

    h, b, s, t, ix_cm4, wx_cm3, mass = I_BEAMS[profile]
    ix = ix_cm4 * 1e-8          # m4
    wx = wx_cm3 * 1e-6          # m3
    e = 206e9                   # Pa, steel
    ry = 240e6                  # Pa design yield

    moment = load * length**2 / 8.0          # kN*m
    stress_pa = moment * 1e3 / wx            # Pa
    stress = stress_pa / 1e6                 # MPa
    utilization = stress_pa / ry
    passed = utilization <= 1.0
    margin = round((1 - utilization) * -100) if not passed else round((1 - utilization) * 100)

    deflection_m = 5 * load * 1e3 * length**4 / (384 * e * ix)
    limit_m = length / 250.0
    deflection_ok = deflection_m <= limit_m

    weight = mass * length
    price = round(weight * 48)               # ~48 UAH/kg rolled steel

    return {
        "ok": True,
        "profile": profile,
        "geometry": {"h": h, "b": b, "s": s, "t": t},
        "length_m": length,
        "load_kNm": load,
        "moment": round(moment, 2),
        "stress_mpa": round(stress, 2),
        "ry_mpa": ry / 1e6,
        "utilization": round(utilization * 100, 1),
        "passed": bool(passed and deflection_ok),
        "strength_ok": bool(passed),
        "deflection_ok": bool(deflection_ok),
        "deflection": round(deflection_m, 4),
        "deflection_limit": round(limit_m, 4),
        "margin": margin,
        "weight": round(weight, 1),
        "price": price,
        "params": {"E": "206 GPa", "Ry": "240 MPa", "norm": "f <= L/250, sigma <= Ry"},
    }


# ------------------------------------------------------------- frontend ----
STATIC_DIR = BASE_DIR / "static"
if (BASE_DIR / "index.html").exists():
    STATIC_DIR = BASE_DIR

app.mount(
    "/",
    StaticFiles(directory=str(STATIC_DIR), html=True),
    name="frontend",
)


def run():
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    run()