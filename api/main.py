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
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
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
    _tg_start()
    yield
    _stop_bot()
    _tg_shutdown()


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
    channel: str | None = None
    type: str | None = None
    budget: str | None = None
    message: str | None = None
    source: str = "site"
    page: str | None = None
    ts: str | None = None


CHANNEL_LABELS = {
    "telegram": "Telegram",
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "email": "Email",
    "phone": "Телефон",
}


def _channel_contact_link(channel: str | None, contact: str) -> str:
    """Build a clickable link for the owner: t.me/…, wa.me/…, mailto:…"""
    c = (contact or "").strip()
    if not c:
        return ""
    if channel == "telegram":
        u = c.replace("@", "").strip()
        return f"t.me/{u}"
    if channel == "whatsapp":
        digits = re.sub(r"[^\d]", "", c)
        return f"wa.me/{digits}" if digits else ""
    if channel == "instagram":
        u = c.replace("@", "").strip()
        return f"instagram.com/{u}"
    if channel == "facebook":
        return f"facebook.com/{c}" if not c.startswith("http") else c
    if channel == "email":
        return c
    if channel == "phone":
        digits = re.sub(r"[^\d]", "", c)
        # no calls — the number is only for search (open the chat in Telegram)
        return f"t.me/+{digits}" if digits else ""
    return ""


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
    threading.Thread(target=_tg_notify_lead, args=(payload,), daemon=True).start()


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


# ----------------------------------------------------------------- chat LLM ----
# NOVA answers live questions via Qwen (fast) with NVIDIA as fallback,
# then deterministic rules as last resort. Keys come from Railway secrets.
CHAT_QWEN_KEY = os.getenv("CHAT_QWEN_KEY", "")
CHAT_NVIDIA_KEY = os.getenv("CHAT_NVIDIA_KEY", "")

LLM_SYSTEM = (
    "Ти NOVA — AI-консультант студії FastStart Digital. Характер: професійний продажник — "
    "доброзичливий, спокійний, впевнений, без нав'язливості й тиску. Працюєш як досвідчений "
    "консультант: слухаєш, ставиш 1–2 уточнювальні питання, радиш оптимальний варіант під задачу "
    "й бюджет клієнта (не найдорожчий!), чесно кажеш, якщо щось не підходить. Ніколи не тиснеш, "
    "не перебільшуєш, не вигадуєш знижок. Наприкінці м'яко запропонуй залишити контакт (Telegram) "
    "для КП — раз, без напору. ВАЖЛИВО: відповідай мовою останнього повідомлення клієнта — "
    "написав російською, значить вся відповідь російською; написав українською — українською. "
    "МОВНЕ ПРАВИЛО: вся відповідь — виключно мовою клієнта, без жодного слова іншою мовою; "
    "назви тарифів, функцій і категорій теж перекладай мовою відповіді (E-Commerce/Каталог → "
    "E-Commerce/Catalog тощо). "
    "Зазвичай відповідай коротко — 2–3 речення; якщо просять деталі або таблицю — давай повну інформацію.\n"
    "Тарифна сітка FastStart Digital (фікс-прайс, €):\n"
    "1) Чат-боти (Telegram/Instagram/Facebook): Start Bot — візитка, меню, FAQ, збір заявок: 150–250 €. "
    "Pro Bot — автоворонка, розсилки, запис, Google Таблиці/CRM: 300–500 €. "
    "Custom Bot — оплата (LiqPay/Stripe/PayPal), кабінет, AI-інтеграції, WebApp: 600–1200 €+.\n"
    "2) Сайти: Landing Page Express — односторінковий, форма, аналітика: 250–400 €. "
    "Business Web — 5–7 сторінок + адмінка: 500–850 €. E-Commerce/Каталог — магазин, фільтри, "
    "кошик, оплата: 900–1500 €+.\n"
    "3) Комбо «Швидкий старт»: FastStart Light (лендінг + бот-візитка) — 350 €. "
    "FastStart Pro (сайт + розумний бот з автоворонкою + CRM) — 650 €.\n"
    "Умови: в тариф входить до 3 етапів правок, додаткові функції розраховуються окремо. "
    "Оплата: 50% перед початком, 50% після демо на тестовому сервері. "
    "Супровід: 30–50 €/міс (правки, перевірка роботи, хостинг). "
    "Контакт студії: Telegram t.me/faststart_digital.\n"
    "Наприкінці м'яко запропонуй залишити контакт (Telegram) для підготовки КП. "
    "Якщо питання поза послугами — чесно скажи."
)


_RU_WORDS = ("сколько", "будет", "стоит", "стоить", "стоимость", "нужно", "нужен", "можно",
             "как", "что", "надо", "пожалуйста", "сделать", "сделаем", "какой", "какая",
             "какие", "цена", "заказ", "это", "вам", "вас", "пришлите", "скинь", "покажи",
             "напиши", "привет", "здравств", "починить", "разработ", "таблиц", "мне", "тебе")
_UK_WORDS = ("скільки", "буде", "коштує", "треба", "можна", "як", "що", "зробити", "робити",
             "зробіть", "будь-ласка", "наскільки", "напишіть", "замов", "допомож", "потрібно",
             "цікав", "докладніше", "пришліть", "кільки", "ваш", "наш", "підкажіть", "порадьте",
             "хочу", "чекаю", "дякую", "вітаю")


_LANG_SCRIPTS = (
    (("\u4e00", "\u9fff"), "zh"),
    (("\u3040", "\u30ff"), "ja"),
    (("\uac00", "\ud7af"), "ko"),
    (("\u0600", "\u06ff"), "ar"),
    (("\u0590", "\u05ff"), "he"),
    (("\u0900", "\u097f"), "hi"),
    (("\u0e00", "\u0e7f"), "th"),
)

_LANG_WORDS = {
    "en": ("the", "and", "you", "how", "much", "what", "price", "cost", "with", "for", "want",
           "need", "please", "thank", "can", "help", "send", "table", "website", "shop", "your",
           "would", "give", "get", "create", "make"),
    "es": ("el", "la", "los", "las", "que", "por", "para", "con", "cuanto", "cuesta", "precio",
           "hola", "gracias", "quiero", "necesito", "como", "puedo", "es", "un", "una", "sitio"),
    "fr": ("le", "la", "les", "est", "pour", "avec", "combien", "coûte", "cout", "prix",
           "bonjour", "merci", "je", "veux", "besoin", "site", "un", "une", "nous", "vous"),
    "de": ("der", "die", "das", "und", "ist", "für", "mit", "wie", "viel", "kostet", "preis",
           "hallo", "danke", "ich", "will", "brauche", "einen", "eine", "sie", "wir"),
    "it": ("il", "lo", "gli", "che", "è", "per", "con", "quanto", "costa", "prezzo", "ciao",
           "grazie", "voglio", "ho", "bisogno", "un", "una", "sito", "mi"),
    "pt": ("o", "a", "que", "é", "para", "com", "quanto", "custa", "preço", "olá", "obrigado",
           "quero", "preciso", "um", "uma", "não", "você", "site", "loja"),
    "pl": ("i", "w", "jest", "na", "za", "z", "ile", "cena", "dzień", "dziękuję", "witam",
           "chcę", "potrzebuję", "strona", "bot", "można", "proszę", "kosztuj"),
    "nl": ("de", "het", "en", "is", "voor", "met", "hoeveel", "prijs", "hallo", "dank", "ik",
           "wil", "nodig", "een", "website", "u", "we", "onze"),
}

_LANG_INS = {
    "uk": "\n\nНапиши відповідь повністю українською мовою.",
    "ru": "\n\nНапиши ответ полностью на русском языке.",
    "en": "\n\nReply entirely in English.",
    "es": "\n\nResponde enteramente en español.",
    "fr": "\n\nRéponds entièrement en français.",
    "de": "\n\nAntworte vollständig auf Deutsch.",
    "it": "\n\nRispondi interamente in italiano.",
    "pt": "\n\nResponda inteiramente em português.",
    "pl": "\n\nOdpowiedz w całości po polsku.",
    "nl": "\n\nAntwoord volledig in het Nederlands.",
    "zh": "\n\n请用中文完整回答。",
    "ja": "\n\nすべて日本語で回答してください。",
    "ko": "\n\n전부 한국어로 답변해 주세요.",
    "ar": "\n\nأجب بالكامل باللغة العربية.",
    "he": "\n\nענה את כל התשובה בעברית.",
    "hi": "\n\nपूरी तरह हिंदी में उत्तर दें।",
    "th": "\n\nตอบเป็นภาษาไทยทั้งหมด",
    "": "\n\nReply entirely in the same language the client used.",
}


_LANG_STAY = {
    "en": "Do not switch to any other language at all.",
    "es": "No cambies a ningún otro idioma en absoluto.",
    "fr": "Ne passe jamais à une autre langue.",
    "de": "Wechsle niemals in eine andere Sprache.",
    "it": "Non passare mai a un'altra lingua.",
    "pt": "Nunca mude para outro idioma.",
    "pl": "Nie przechodź do żadnego innego języka.",
    "nl": "Ga nooit over naar een andere taal.",
    "zh": "不要切换到其他任何语言。",
    "ja": "他の言語に切り替えないでください。",
    "ko": "절대 다른 언어로 바꾸지 마세요.",
    "ar": "لا تنتقل إلى أي لغة أخرى على الإطلاق.",
    "he": "אל תעבור בכלל לשפה אחרת.",
    "hi": "आप किसी अन्य भाषा में बिल्कुल न जाएं।",
    "th": "ห้ามเปลี่ยนไปเป็นภาษาอื่นเด็ดขาด",
    "": "Reply in one language only: the language the client used.",
}


def client_lang(text: str) -> str:
    low = text.lower()
    if any(ch in text for ch in "іїєґІЇЄҐ"):
        return "uk"
    if any(ch in text for ch in "ыэъёЫЭЪЁ"):
        return "ru"
    for (lo, hi), code in _LANG_SCRIPTS:
        if any(lo <= ch <= hi for ch in text):
            return code
    if any("\u0400" <= ch <= "\u04ff" for ch in text):
        ru = sum(1 for w in _RU_WORDS if w in low)
        uk = sum(1 for w in _UK_WORDS if w in low)
        return "ru" if ru > uk else "uk"
    tokens = re.findall(r"[a-z\u00c0-\u024f]+", low)
    scores = {code: 0 for code in _LANG_WORDS}
    for tok in tokens:
        for code, words in _LANG_WORDS.items():
            if tok in words:
                scores[code] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "en"


def _has_drift(reply: str, lang: str) -> bool:
    if not reply or lang in ("ru", "uk"):
        return False
    for (lo, hi), code in _LANG_SCRIPTS:
        if code == lang:
            return (not any(lo <= ch <= hi for ch in reply)
                    or any("\u0400" <= ch <= "\u04ff" for ch in reply))
    return any("\u0400" <= ch <= "\u04ff" for ch in reply)


def _llm_call(url, key, model, text, timeout=25, lang=None, reinforce=None):
    prompt = text[:2000] + _LANG_INS.get(lang, _LANG_INS[""])
    if reinforce:
        prompt += "\n\n" + reinforce
    return _post_chat(url, key, model, [
        {"role": "system", "content": LLM_SYSTEM},
        {"role": "user", "content": prompt},
    ], 300, timeout)


def _post_chat(url, key, model, messages, max_tokens, timeout):
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        reply = body["choices"][0]["message"]["content"].strip()
    except Exception:
        return None
    return reply or None


def _llm_raw(url, key, model, prompt, max_tokens=5, timeout=25):
    reply = _post_chat(url, key, model, [{"role": "user", "content": prompt}], max_tokens, timeout)
    return (reply or "").strip().lower()


def _try_provider(url, key, model, text, lang, timeout=25):
    reply = _llm_call(url, key, model, text, timeout=timeout, lang=lang)
    if reply and _has_drift(reply, lang):
        reply2 = reply
        for _ in range(2):
            reply2 = _llm_call(url, key, model, text, timeout=timeout, lang=lang,
                               reinforce=_LANG_STAY.get(lang, _LANG_STAY[""]))
            if reply2 and not _has_drift(reply2, lang):
                return reply2
        return reply2 or reply
    return reply


def llm_reply(text: str) -> str | None:
    lang = client_lang(text)
    qwen_last = None
    if CHAT_QWEN_KEY:
        qwen_last = _try_provider(
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
            CHAT_QWEN_KEY, "qwen-plus", text, lang)
        if qwen_last and not _has_drift(qwen_last, lang):
            return qwen_last
    if CHAT_NVIDIA_KEY:
        reply = _try_provider(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            CHAT_NVIDIA_KEY, "meta/llama-3.3-70b-instruct", text, lang, timeout=30)
        if reply:
            return reply
    return qwen_last


# ----------------------------------------------------------------- chat ----
class ChatMessage(BaseModel):
    message: str


@app.post("/api/chat")
def chat(message: ChatMessage) -> dict:
    """NOVA agent: Qwen -> NVIDIA -> deterministic rules."""
    text = message.message.lower().strip()
    llm = llm_reply(message.message)
    if llm:
        # innerHTML-safe: escape tags, keep line breaks
        llm = llm.replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")[:600]
        source = "qwen" if CHAT_QWEN_KEY else "nvidia"
        _save_lead({"message": text, "source": "api-chat-llm"})
        return {"ok": True, "reply": llm, "price_hint": None, "llm": source}
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


@app.post("/api/classify")
def classify(message: ChatMessage) -> dict:
    """Is the user's funnel answer a real contact, or a question/request?
    Fast regex first, then LLM (Qwen -> NVIDIA) so NOVA never saves
    garbage like "дай мне таблицу" as a phone number."""
    text = message.message.strip()
    low = text.lower()
    if re.search(r"[@]|\d{5,}|t\.me|telegram|viber|whatsapp|wa\.me|skype|email|пошта|е-мейл", low):
        return {"kind": "contact"}
    prompt = (
        "В чате продаж клиенту задали вопрос, и он ответил. Определи тип ответа одним словом:\n"
        "'contact' — контакт: имя, ник, телефон, email, telegram, ссылка.\n"
        "'question' — вопрос или просьба: спросить про цены/тарифы/сроки, "
        "попросить таблицу, прайс, примеры, КП, спросить как связаться и т.п.\n"
        "'other' — всё остальное (мусор, опечатки, пустота).\n"
        f'Ответ клиента: "{text[:300]}"\n'
        "Ответь ТОЛЬКО одним словом: contact, question или other."
    )
    if CHAT_QWEN_KEY:
        r = _llm_raw("https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
                     CHAT_QWEN_KEY, "qwen-plus", prompt)
        if r in ("contact", "question", "other"):
            return {"kind": r}
    if CHAT_NVIDIA_KEY:
        r = _llm_raw("https://integrate.api.nvidia.com/v1/chat/completions",
                     CHAT_NVIDIA_KEY, "meta/llama-3.3-70b-instruct", prompt, timeout=30)
        if r in ("contact", "question", "other"):
            return {"kind": r}
    return {"kind": "question" if "?" in low else "other"}


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
    band = round(cost * 0.2)

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
        "range": {"low": cost - band, "high": cost + band, "band_pct": 20},
        "disclaimer": "Попередня оцінка за типовими проєктами — точний кошторис після обговорення ТЗ.",
        "params": {"rate": f"{RATE_UAH_HOUR:g} UAH/h", "norm": "робочий тиждень 38 год"},
    }


# ------------------------------------------------------------ bot demo ----
# BTrade — live trading-bot demo. A supervised child process streams its
# console lines into an in-memory ring buffer; the site renders them in a
# terminal widget. Runs in PAPER mode (simulated fills, no real orders).
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
    dotenv = os.getenv("BOT_DOTENV", ".env.mexc-paper")
    env = os.environ.copy()
    env.update({"DOTENV": dotenv, "PYTHONUNBUFFERED": "1"})
    schedule = os.getenv("BOT_SCHEDULE")
    if schedule:
        env["SCHEDULE_ENABLED"] = schedule
    paper = os.getenv("PAPER_TRADING", "1") == "1"
    mode = "PAPER" if paper else "REAL"
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
        _bot_append(f"[supervisor] BTrade запущен (pid={proc.pid}, {mode}-MODE, {dotenv})")
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
# ------------------------------------------------------------- telegram ----
# Lead-accepting bot: visitors who message the bot get their application
# saved as a lead, and every site lead is forwarded to the owner chat.
# Owner chat id is captured automatically from the first /start message.
# Delivery is webhook-based (no polling → no 409 conflicts).
# Pinning: the owner file lives in the ephemeral container fs and is wiped on
# every Railway deploy, so TELEGRAM_OWNER_CHAT env var pins the owner chat and
# _tg_start() re-seeds the file from it (getChat) after each redeploy.
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_SUPPORT = os.getenv("TELEGRAM_SUPPORT", "faststart_digital_support").strip()
TG_OWNER_FILE = DATA_DIR / "telegram_owner.json"
TG_OWNER_CHAT = os.getenv("TELEGRAM_OWNER_CHAT", "").strip()
TG_OWNER_NAME = os.getenv("TELEGRAM_OWNER_NAME", "").strip()
TG_API = "https://api.telegram.org/bot"
TG_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "fsd_tg_secret_2026")

_tg_lock = threading.Lock()
_tg_state = {
    "running": False, "me": None, "owner": None, "last_error": None,
    "updates": 0, "sent": 0, "last_update": None,
}


def _tg_call(method: str, payload: dict | None = None, timeout: int = 40) -> dict | None:
    if not TG_TOKEN:
        return None
    req = urllib.request.Request(
        f"{TG_API}{TG_TOKEN}/{method}",
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        with _tg_lock:
            _tg_state["last_error"] = f"{method}: {exc}"
        return None


def _tg_send(chat_id, text: str) -> None:
    res = _tg_call("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })
    if res and res.get("ok"):
        with _tg_lock:
            _tg_state["sent"] += 1


def _tg_owner() -> dict | None:
    with _tg_lock:
        info = None
        if TG_OWNER_FILE.exists():
            try:
                info = json.loads(TG_OWNER_FILE.read_text("utf-8"))
            except json.JSONDecodeError:
                info = None
    if info:
        if not TG_OWNER_CHAT or str(info.get("chat_id")) == TG_OWNER_CHAT:
            return info
    if TG_OWNER_CHAT:
        return {
            "chat_id": int(TG_OWNER_CHAT),
            "name": TG_OWNER_NAME or "owner (env)",
            "username": "",
            "ts": "env",
        }
    return None


def _tg_set_owner(info: dict) -> None:
    with _tg_lock:
        _tg_state["owner"] = info
        TG_OWNER_FILE.write_text(json.dumps(info, ensure_ascii=False, indent=2), "utf-8")
        _tg_state["last_error"] = None


def _tg_lead_text(p: dict) -> str:
    lines = ["🆕 <b>НОВА ЗАЯВКА НА РОЗРОБКУ</b>"]
    labels = [
        ("type", "Тип"), ("budget", "Бюджет"), ("name", "Ім'я"),
        ("contact", "Контакт"), ("message", "Повідомлення"), ("page", "Сторінка"),
    ]
    channel = p.get("channel")
    contact = (p.get("contact") or "").strip()
    if not channel:
        # legacy/empty form submissions: guess the channel from the contact
        if contact.startswith("+") or (contact and contact.replace("+", "").replace(" ", "").isdigit()):
            channel = "phone"
        elif "@" in contact and "." in contact.split("@")[1]:
            channel = "email"
        else:
            channel = "telegram"
    if channel:
        lines.append(f"<b>Канал:</b> {CHANNEL_LABELS.get(channel, channel)}")
    for key, label in labels:
        if p.get(key):
            lines.append(f"<b>{label}:</b> {p[key]}")
    link = _channel_contact_link(channel, contact)
    if link:
        lines.append(f"🔗 <a href=\"https://{link}\">Знайти клієнта → {link}</a>")
    lines.append(f"<i>Джерело: {p.get('source', 'site')} · {p.get('ts', '')}</i>")
    return "\n".join(lines)


def _tg_ping_client(p: dict) -> None:
    """Reassure the client in their channel: 'we found you, expect the quote'."""
    if not TG_TOKEN:
        return
    if p.get("source") == "telegram-bot" and p.get("contact"):
        # client talked to the bot first → we already replied with confirmation
        return
    chat_id = p.get("tg_chat_id")
    if not chat_id:
        return
    _tg_send(chat_id, "👋 Ми вас знайшли!\n"
        "Ваша заявка прийнята — інженер готує розрахунок. Очікуйте повідомлення протягом 24 годин.\n"
        "Техпідтримка: t.me/" + TG_SUPPORT)


def _tg_notify_lead(p: dict) -> None:
    owner = _tg_owner()
    if not owner:
        print("[TG] owner chat not set yet — /start the bot first")
        return
    _tg_send(owner["chat_id"], _tg_lead_text(p))
    _tg_ping_client(p)


def _tg_handle_update(upd: dict) -> None:
    msg = upd.get("message") or {}
    chat = msg.get("chat") or {}
    text = (msg.get("text") or "").strip()
    with _tg_lock:
        _tg_state["updates"] += 1
        _tg_state["last_update"] = time.time()
    if not chat.get("id"):
        return

    if text == "/start":
        if TG_OWNER_CHAT and str(chat["id"]) != TG_OWNER_CHAT:
            _tg_send(chat["id"], "ℹ️ Бот вже приймає заявки на розробку.\nТехпідтримка: t.me/" + TG_SUPPORT)
            return
        info = {
            "chat_id": chat["id"],
            "name": chat.get("first_name") or chat.get("title") or "owner",
            "username": chat.get("username") or "",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        existed = _tg_owner() is not None
        first = info["username"] and info["username"].lower() not in ("", "bot")
        _tg_set_owner(info)
        if existed:
            reply = "✅ Новий власник!\nСюди будуть приходити заявки на розробку.\nТехпідтримка: t.me/" + TG_SUPPORT
        else:
            reply = "✅ Бота підключено!\nЗ цього часу всі заявки на розробку з сайту приходитимуть сюди.\nТехпідтримка: t.me/" + TG_SUPPORT
        _tg_send(chat["id"], reply)
    elif text.startswith("/"):
        _tg_send(chat["id"], "ℹ️ Команди: /start — підключити прийом заявок.\nТехпідтримка: t.me/" + TG_SUPPORT)
    else:
        uname = chat.get("username")
        contact = "@" + uname if uname else f"tg {chat['id']}"
        _save_lead({
            "name": chat.get("first_name") or chat.get("title") or "Telegram",
            "contact": contact,
            "channel": "telegram",
            "message": text,
            "source": "telegram-bot",
            "tg_chat_id": chat["id"],
        })
        _tg_send(chat["id"],
            "✅ <b>Заявку прийнято!</b>\n"
            "Інженер FastStart Digital зв'яжеться з вами протягом 24 годин і підготує прорахунок.\n"
            "Техпідтримка: t.me/" + TG_SUPPORT)


@app.post("/api/tg/webhook")
async def tg_webhook(request: Request) -> dict:
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != TG_WEBHOOK_SECRET:
        raise HTTPException(401, "bad secret token")
    upd = await request.json()
    threading.Thread(target=_tg_handle_update, args=(upd,), daemon=True).start()
    return {"ok": True}


def _tg_start() -> None:
    if not TG_TOKEN:
        return
    with _tg_lock:
        _tg_state["running"] = True
        _tg_state["last_error"] = None
    me = _tg_call("getMe")
    if me and me.get("ok"):
        with _tg_lock:
            _tg_state["me"] = me["result"]
        print(f"[TG] bot @{me['result'].get('username')} online (webhook mode)")
    owner = _tg_owner()
    if TG_OWNER_CHAT and not TG_OWNER_FILE.exists():
        try:
            chat_id = int(TG_OWNER_CHAT)
        except ValueError:
            chat_id = 0
        info = {"chat_id": chat_id, "name": TG_OWNER_NAME or "owner (env)", "username": "", "ts": "env"}
        ch = _tg_call("getChat", {"chat_id": chat_id})
        if ch and ch.get("ok"):
            info["name"] = ch["result"].get("first_name") or ch["result"].get("title") or info["name"]
            info["username"] = ch["result"].get("username") or ""
        with _tg_lock:
            _tg_state["owner"] = info
            TG_OWNER_FILE.write_text(json.dumps(info, ensure_ascii=False, indent=2), "utf-8")
        owner = info
        print(f"[TG] owner restored from env pin: chat {chat_id}")
    if owner:
        _tg_send(owner["chat_id"], "🟢 FastStart Digital: бот-приймач заявок перезапущено.")


@app.get("/api/tg/status")
def tg_status() -> dict:
    owner = _tg_owner()
    with _tg_lock:
        st = dict(_tg_state)
    return {
        "ok": True,
        "enabled": bool(TG_TOKEN),
        "me": st.get("me"),
        "owner": owner,
        "updates": st["updates"],
        "sent": st["sent"],
        "last_error": st.get("last_error"),
        "last_update": st.get("last_update"),
        "running": st["running"],
        "support": "t.me/" + TG_SUPPORT,
    }


# ------------------------------------------------------------- frontend ----
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