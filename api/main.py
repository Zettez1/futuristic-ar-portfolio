"""FastStart Digital portfolio backend.

Serves static frontend + JSON APIs:
  POST /api/lead        - lead collection (chat agent + contact form)
  POST /api/chat        - simple rule-based AI agent reply
  GET  /api/calc/quote  - live IT-project quote calculator

Live Python quote engine for the interactive tools section.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import math
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import socket as _socket
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
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
    _mk_start()
    yield
    _mk_shutdown()
    _stop_bot()
    _tg_shutdown()


app = FastAPI(title="FastStart Digital Portfolio", version="1.1.0", lifespan=_lifespan)


# Outbound connects to Google API occasionally stall on IPv6 in Railway;
# force IPv4 and short connect timeouts for urllib.
_orig_create_connection = _socket.create_connection

def _ipv4_create_connection(address, timeout=_socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    try:
        host, port = address
        infos = _socket.getaddrinfo(host, port, _socket.AF_INET, _socket.SOCK_STREAM)
        family, socktype, proto, _, sockaddr = infos[0]
        sock = _socket.socket(family, socktype, proto)
        try:
            sock.settimeout(timeout if timeout is not _socket._GLOBAL_DEFAULT_TIMEOUT else 20)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except Exception:
            sock.close()
            raise
    except Exception as e:
        if not isinstance(e, (OSError,)):
            raise
        # fall back to original (covers odd address tuples)
        return _orig_create_connection(address, timeout, source_address)

_socket.create_connection = _ipv4_create_connection

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- google auth ----
# Admin auth via Google OAuth. Secrets come from Railway env:
#   GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, AUTH_SECRET, AUTH_ADMIN_EMAILS,
#   AUTH_REDIRECT_URI (optional override).
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
USERS_FILE = DATA_DIR / "users.json"
AUTH_REDIRECT_URI = os.getenv(
    "AUTH_REDIRECT_URI",
    "https://web-frontend-production-78d2.up.railway.app/api/auth/google/callback",
)
AUTH_ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("AUTH_ADMIN_EMAILS", "").split(",") if e.strip()}
TURNSTILE_SECRET = os.getenv("TURNSTILE_SECRET", "")


def _verify_turnstile(token: str, ip: str = "") -> bool:
    """Verify Cloudflare Turnstile token. Returns True if no secret configured (dev mode)."""
    if not TURNSTILE_SECRET:
        return True
    if not token or len(token) > 2048:
        return False
    data = urlencode({
        "secret": TURNSTILE_SECRET,
        "response": token,
        "remoteip": ip,
    }).encode()
    req = urllib.request.Request(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
        return result.get("success", False)
    except Exception:
        return False
AUTH_COOKIE = "fsd_session"
AUTH_STATE_COOKIE = "fsd_oauth_state"
SESSION_TTL = 7 * 24 * 3600


def _auth_key() -> str:
    # Fixed from env when possible (survives restarts); fallback: per-process random.
    s = os.getenv("AUTH_SECRET", "")
    if not s:
        s = os.environ.get("AUTH_SECRET_RUNTIME", "")
        if not s:
            s = secrets.token_hex(32)
            os.environ["AUTH_SECRET_RUNTIME"] = s
    return s


def _sign(b64: str) -> str:
    return hmac.new(_auth_key().encode(), b64.encode(), hashlib.sha256).hexdigest()


def _make_session(user: dict) -> str:
    payload = {
        "e": user["email"],
        "n": user.get("name", ""),
        "p": user.get("picture", ""),
        "exp": int(time.time()) + SESSION_TTL,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    b64 = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return b64 + "." + _sign(b64)


def _read_session(request: Request) -> dict | None:
    cookie = request.cookies.get(AUTH_COOKIE)
    if not cookie or "." not in cookie:
        return None
    b64, sig = cookie.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(b64)):
        return None
    try:
        raw = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
        p = json.loads(raw)
    except Exception:
        return None
    if int(p.get("exp", 0)) < int(time.time()):
        return None
    return {"email": p.get("e"), "name": p.get("n"), "picture": p.get("p")}


def _set_session_cookie(resp, user: dict):
    resp.set_cookie(AUTH_COOKIE, _make_session(user), max_age=SESSION_TTL,
                    httponly=True, samesite="lax", secure=True, path="/")


def _clear_session_cookie(resp):
    resp.delete_cookie(AUTH_COOKIE, path="/")


def _oauth_ready() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


@app.get("/api/auth/google")
def auth_google_start(request: Request):
    if not _oauth_ready():
        raise HTTPException(503, "Google OAuth is not configured (missing env)")
    state = secrets.token_urlsafe(24)
    url = ("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": AUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }))
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(AUTH_STATE_COOKIE, state, max_age=300,
                    httponly=True, samesite="lax", secure=True, path="/")
    nxt = request.query_params.get("next", "")
    if nxt.startswith("/"):
        resp.set_cookie("fsd_next", nxt, max_age=600,
                        httponly=True, samesite="lax", secure=True, path="/")
    return resp


@app.get("/api/auth/google/callback")
def auth_google_callback(request: Request):
    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    saved = request.cookies.get(AUTH_STATE_COOKIE, "")
    if not code or not state or not saved or not hmac.compare_digest(state, saved):
        raise HTTPException(400, "invalid OAuth state")
    if not _oauth_ready():
        raise HTTPException(503, "Google OAuth is not configured (missing env)")
    try:
        token_body = urlencode({
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": AUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        })
        tok = None
        last_err = None
        for attempt in range(3):
            try:
                tok_req = urllib.request.Request(
                    "https://oauth2.googleapis.com/token",
                    data=token_body.encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                with urllib.request.urlopen(tok_req, timeout=20) as tr:
                    tok = json.loads(tr.read().decode("utf-8"))
                break
            except Exception as e:
                last_err = e
                print(f"[auth] token exchange attempt {attempt + 1} failed: {type(e).__name__}: {e}")
                if attempt < 2:
                    time.sleep(1)
        if tok is None:
            raise HTTPException(502, f"token exchange failed: {type(last_err).__name__}: {last_err}")
        access_token = tok.get("access_token")
        if not access_token:
            raise HTTPException(502, "token exchange failed: " + str(tok)[:300])
        user = None
        last_err = None
        for attempt in range(3):
            try:
                info_req = urllib.request.Request(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": "Bearer " + access_token},
                )
                with urllib.request.urlopen(info_req, timeout=20) as ir:
                    user = json.loads(ir.read().decode("utf-8"))
                break
            except Exception as e:
                last_err = e
                print(f"[auth] userinfo attempt {attempt + 1} failed: {type(e).__name__}: {e}")
                if attempt < 2:
                    time.sleep(1)
        if user is None:
            raise HTTPException(502, f"Google userinfo upstream error: {type(last_err).__name__}: {last_err}")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "Google auth upstream error")
    email = (user.get("email") or "").lower()
    now = datetime.now(timezone.utc).isoformat()
    # register-or-login: first Google login creates a client account (users.json)
    registered = _upsert_user({
        "email": email,
        "name": user.get("name", ""),
        "picture": user.get("picture", ""),
    })
    res = RedirectResponse("/?auth=registered" if registered else "/?auth=ok", status_code=303)
    _set_session_cookie(res, {
        "email": email,
        "name": user.get("name", ""),
        "picture": user.get("picture", ""),
        "fresh": registered,
    })
    nxt = request.cookies.get("fsd_next", "")
    if nxt.startswith("/"):
        frag = ""
        path = nxt
        if "#" in nxt:
            path, frag = nxt.split("#", 1)
        sep = "&" if "?" in path else "?"
        res = RedirectResponse(
            path + sep + "auth=" + ("registered" if registered else "ok") + ("#" + frag if frag else ""),
            status_code=303,
        )
        _set_session_cookie(res, {
            "email": email,
            "name": user.get("name", ""),
            "picture": user.get("picture", ""),
            "fresh": registered,
        })
        res.delete_cookie("fsd_next", path="/")
    return res


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict:
    u = _read_session(request)
    if u:
        users = _read_users()
        db_u = next((x for x in users if x.get("email") == (u.get("email") or "").lower()), None)
        u["coupon_5"] = bool(db_u and db_u.get("coupon_5"))
    return {"ok": bool(u), "user": u}


@app.post("/api/auth/logout")
def auth_logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    _clear_session_cookie(resp)
    return resp


# --------------------------------------------------- email/password auth ----
VERIFY_TTL = 600            # code lifetime, seconds
_pending_verify: dict[str, dict] = {}   # email -> {code, exp, salt, hash}


def _read_users() -> list:
    with _lock:
        if USERS_FILE.exists():
            try:
                return json.loads(USERS_FILE.read_text("utf-8"))
            except json.JSONDecodeError:
                pass
    return []


def _write_users(users: list) -> None:
    with _lock:
        USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), "utf-8")


def _upsert_user(rec: dict) -> bool:
    """Create or update a user record (by email). Returns True if newly created."""
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        users = []
        if USERS_FILE.exists():
            try:
                users = json.loads(USERS_FILE.read_text("utf-8"))
            except json.JSONDecodeError:
                users = []
        existing = next((u for u in users if u.get("email") == rec["email"]), None)
        if existing:
            for k in ("name", "picture"):
                if rec.get(k):
                    existing[k] = rec[k]
            existing["verified"] = True
            existing["last_login"] = now
            existing["admin"] = rec["email"] in AUTH_ADMIN_EMAILS
            if rec.get("pw_hash") and not existing.get("pw_hash"):
                existing["pw_salt"] = rec["pw_salt"]
                existing["pw_hash"] = rec["pw_hash"]
            USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), "utf-8")
            return False
        rec.setdefault("verified", True)
        rec.setdefault("created", now)
        rec.setdefault("last_login", now)
        rec["admin"] = rec["email"] in AUTH_ADMIN_EMAILS
        users.append(rec)
        USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), "utf-8")
        return True


def _hash_password(pw: str) -> tuple[str, str]:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()
    return salt, h


def _check_password(pw: str, salt: str, h: str) -> bool:
    if not salt or not h:
        return False
    return hmac.compare_digest(
        hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt.encode("utf-8"), 120_000).hex(), h)


class RegPayload(BaseModel):
    email: str
    password: str
    cf_turnstile: str = ""


class VerifyPayload(BaseModel):
    email: str
    code: str


class LoginPayload(BaseModel):
    email: str
    password: str
    cf_turnstile: str = ""


class RecoverPayload(BaseModel):
    email: str
    cf_turnstile: str = ""


class RecoverConfirmPayload(BaseModel):
    email: str
    code: str
    password: str
    cf_turnstile: str = ""


@app.post("/api/auth/register")
def auth_register(payload: RegPayload, request: Request) -> dict:
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not _verify_turnstile(payload.cf_turnstile, ip):
        raise HTTPException(403, "captcha failed")
    email = (payload.email or "").strip().lower()
    pw = payload.password or ""
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(400, "invalid email")
    if len(pw) < 8:
        raise HTTPException(400, "password too short")
    with _lock:
        users = []
        if USERS_FILE.exists():
            try:
                users = json.loads(USERS_FILE.read_text("utf-8"))
            except json.JSONDecodeError:
                users = []
        if any(u.get("email") == email for u in users):
            raise HTTPException(409, "email exists")
    code = f"{secrets.randbelow(900000) + 100000}"
    salt, h = _hash_password(pw)
    _pending_verify[email] = {"code": code, "exp": time.time() + VERIFY_TTL, "salt": salt, "hash": h}
    ok, info = _mail_send(
        email,
        "FastStart Digital — код підтвердження",
        _mail_frame(
            "<p>Ваш код підтвердження для реєстрації:</p>"
            f"<div style='font-size:30px;font-weight:800;letter-spacing:8px;color:#ffffff;"
            f"text-align:center;padding:14px;border:1px dashed #334155;border-radius:10px;"
            f"background:rgba(34,211,238,0.15)'>{code}</div>"
            "<p style='color:#8b93b2;font-size:13px'>Код дійсний 10 хвилин. Якщо ви не реєструвались у "
            "FastStart Digital — проігноруйте цей лист.</p>",
        ),
    )
    if not ok:
        _pending_verify.pop(email, None)
        raise HTTPException(502, f"mail send failed: {info}")
    return {"ok": True, "email": email}


@app.post("/api/auth/verify")
def auth_verify(payload: VerifyPayload) -> dict:
    email = (payload.email or "").strip().lower()
    code = (payload.code or "").strip()
    p = _pending_verify.get(email)
    if not p:
        raise HTTPException(400, "no pending verification")
    if int(time.time()) > p["exp"]:
        _pending_verify.pop(email, None)
        raise HTTPException(400, "code expired")
    if code != p["code"]:
        raise HTTPException(400, "wrong code")
    _pending_verify.pop(email, None)
    name = email.split("@")[0]
    _upsert_user({
        "email": email,
        "name": name,
        "picture": "",
        "pw_salt": p["salt"],
        "pw_hash": p["hash"],
    })
    resp = JSONResponse({"ok": True, "user": {"email": email, "name": name, "picture": ""}})
    _set_session_cookie(resp, {"email": email, "name": name, "picture": ""})
    return resp


@app.post("/api/auth/login")
def auth_login(payload: LoginPayload, request: Request) -> dict:
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not _verify_turnstile(payload.cf_turnstile, ip):
        raise HTTPException(403, "captcha failed")
    email = (payload.email or "").strip().lower()
    pw = payload.password or ""
    users = _read_users()
    u = next((x for x in users if x.get("email") == email), None)
    if not u:
        raise HTTPException(401, "bad credentials")
    if not u.get("pw_hash"):
        raise HTTPException(401, "use google login")
    if not _check_password(pw, u.get("pw_salt", ""), u.get("pw_hash", "")):
        raise HTTPException(401, "bad credentials")
    u["last_login"] = datetime.now(timezone.utc).isoformat()
    _write_users(users)
    name = u.get("name") or email.split("@")[0]
    resp = JSONResponse({"ok": True, "user": {"email": email, "name": name, "picture": u.get("picture", "")}})
    _set_session_cookie(resp, {"email": email, "name": name, "picture": u.get("picture", "")})
    return resp


@app.post("/api/auth/recover")
def auth_recover(payload: RecoverPayload, request: Request) -> dict:
    """Step 1 of password recovery: send a 6-digit code to the user's email."""
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not _verify_turnstile(payload.cf_turnstile, ip):
        raise HTTPException(403, "captcha failed")
    email = (payload.email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(400, "invalid email")
    users = _read_users()
    u = next((x for x in users if x.get("email") == email), None)
    if not u or not u.get("pw_hash"):
        raise HTTPException(404, "email not found")
    code = f"{secrets.randbelow(900000) + 100000}"
    _pending_verify[email] = {"code": code, "exp": time.time() + VERIFY_TTL, "mode": "recover"}
    ok, info = _mail_send(
        email,
        "FastStart Digital — відновлення пароля",
        _mail_frame(
            "<p>Код для відновлення пароля:</p>"
            f"<div style='font-size:30px;font-weight:800;letter-spacing:8px;color:#ffffff;"
            f"text-align:center;padding:14px;border:1px dashed #334155;border-radius:10px;"
            f"background:rgba(34,211,238,0.15)'>{code}</div>"
            "<p style='color:#8b93b2;font-size:13px'>Код дійсний 10 хвилин. Якщо ви не запитували "
            "відновлення пароля — проігноруйте цей лист.</p>",
        ),
    )
    if not ok:
        _pending_verify.pop(email, None)
        raise HTTPException(502, f"mail send failed: {info}")
    return {"ok": True, "email": email}


@app.post("/api/auth/recover/confirm")
def auth_recover_confirm(payload: RecoverConfirmPayload, request: Request) -> dict:
    """Step 2: code + new password -> update the account password."""
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not _verify_turnstile(payload.cf_turnstile, ip):
        raise HTTPException(403, "captcha failed")
    email = (payload.email or "").strip().lower()
    code = (payload.code or "").strip()
    pw = payload.password or ""
    if len(pw) < 8:
        raise HTTPException(400, "password too short")
    p = _pending_verify.get(email)
    if not p or p.get("mode") != "recover":
        raise HTTPException(400, "no pending verification")
    if int(time.time()) > p["exp"]:
        _pending_verify.pop(email, None)
        raise HTTPException(400, "code expired")
    if code != p["code"]:
        raise HTTPException(400, "wrong code")
    _pending_verify.pop(email, None)
    salt, h = _hash_password(pw)
    users = _read_users()
    u = next((x for x in users if x.get("email") == email), None)
    if not u:
        raise HTTPException(404, "email not found")
    u["pw_salt"] = salt
    u["pw_hash"] = h
    _write_users(users)
    return {"ok": True}


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
    email: str | None = None   # client Google email (if submitted while logged in or found in contact)
    status: str = "нова"       # нова / в розробці / завершено


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
    if not payload.get("email"):
        payload["email"] = _extract_email(payload.get("contact")) or ""
    payload.setdefault("status", "нова")
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
    threading.Thread(target=_mk_thanks, args=(payload,), daemon=True).start()


def _mk_thanks(lead: dict) -> None:
    """Instant thank-you email to the client who just applied."""
    to = _extract_email(lead.get("contact"))
    if not to:
        return
    name = (lead.get("name") or "").strip()
    body = (
        f'<p><b>Дякуємо за вашу заявку{", " + name if name else ""}!</b></p>'
        f'<p>Інженер FastStart Digital підготує розрахунок і звʼяжеться з вами <b>протягом 24 годин</b>.</p>'
        f'<p>Проєкт: <b>{lead.get("type") or "—"}</b> · Бюджет: <b>{lead.get("budget") or "—"}</b></p>'
        f'<p style="font-size:12px;color:#8b93b2">Не чекайте — пропозиції вже підібрано '
        f'<a href="{SITE_URL}#contact" style="color:#22d3ee">у наступному листі</a>.</p>'
    )
    ok, _ = _mail_send(to, "FastStart Digital — дякуємо за заявку!", _mail_frame(body))
    if ok:
        print(f"[MK] thanks -> {to}")


# ------------------------------------------------------------- mail ----
# Branded email sending via Resend API (free 100 emails/day) for the AI agent
# and mailings. RESEND_API_KEY must be set in env; the mail domain is verified
# in Resend with DKIM. Logo is the site's own LOGO.png (hotlinked).
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
MAIL_FROM = os.getenv("MAIL_FROM", "FastStart Digital <hello@fast-start-digital.com>").strip()
MAIL_API_TOKEN = os.getenv("MAIL_API_TOKEN", "fsd_mail_2026").strip()
SITE_URL = os.getenv("SITE_URL", "https://web-frontend-production-78d2.up.railway.app").strip()


def _mail_send(to: str, subject: str, html: str) -> tuple[bool, str]:
    if not RESEND_API_KEY:
        return False, "RESEND_API_KEY not set"
    payload = json.dumps({
        "from": MAIL_FROM, "to": [to], "subject": subject, "html": html,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
    }).encode("utf-8")
    req = urllib.request.Request("https://api.resend.com/emails", data=payload, headers={
        "Authorization": "Bearer " + RESEND_API_KEY,
        "Content-Type": "application/json",
        "User-Agent": "FastStartDigital-Mailer/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return True, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return False, exc.read().decode("utf-8", "replace")[:300]


def _mail_frame(body_html: str) -> str:
    logo = f"{SITE_URL}/LOGO.png"
    return f"""<!DOCTYPE html><html lang="uk"><head><meta charset="utf-8"></head><body style="margin:0;background:#0b0e1a;font-family:Arial,sans-serif;color:#e5e7eb">
<div style="max-width:560px;margin:24px auto;background:#111527;border:1px solid #232a45;border-radius:14px;overflow:hidden">
  <div style="padding:24px;border-bottom:1px solid #232a45;text-align:center">
    <img src="{logo}" alt="FastStart Digital" width="52" height="52" style="border-radius:10px;object-fit:contain">
    <div style="margin-top:8px;font-weight:700;font-size:15px;letter-spacing:.5px">FASTSTART DIGITAL</div>
  </div>
  <div style="padding:24px 28px;font-size:14px;line-height:1.6">{body_html}</div>
  <div style="padding:16px 28px;border-top:1px solid #232a45;font-size:12px;color:#8b93b2;text-align:center">
    Веб-розробка · 3D/WebAR · AI-агенти<br>
    <a href="{SITE_URL}" style="color:#22d3ee">fast-start-digital.com</a> · t.me/faststart_digital
  </div>
</div></body></html>"""


class MailPayload(BaseModel):
    to: str
    subject: str = ""
    name: str | None = None
    message: str | None = None


@app.post("/api/mail/send")
def mail_send(payload: MailPayload, request: Request) -> dict:
    """AI-agent mailer: branded HTML email with the FastStart Digital logo."""
    if request.headers.get("X-Mail-Token") != MAIL_API_TOKEN:
        raise HTTPException(401, "bad mail token")
    to = (payload.to or "").strip()
    if "@" not in to:
        raise HTTPException(400, "invalid recipient")
    subject = (payload.subject or "").strip() or "FastStart Digital"
    name = (payload.name or "").strip()
    message = (payload.message or "").strip()
    body = ""
    if name:
        body += f"<p><b>Вітаємо, {name}!</b></p>"
    body += (message or "").replace("\n", "<br>") or "<p>Дякуємо, що звернулись до FastStart Digital!</p>"
    ok, info = _mail_send(to, subject, _mail_frame(body))
    if not ok:
        raise HTTPException(502, f"mail send failed: {info}")
    print(f"[MAIL] -> {to} | {subject}")
    return {"ok": True, "to": to, "subject": subject}


@app.get("/api/mail/status")
def mail_status() -> dict:
    return {
        "ok": True,
        "resend_ready": bool(RESEND_API_KEY),
        "from": MAIL_FROM,
        "token_set": bool(MAIL_API_TOKEN),
    }


# -------------------------------------------------------- marketing bot ----
# "Marketing agent": understands what the client needs from their lead and
# what we have in the arsenal, then sends ONE tailored offer email per week
# (no spam: interval configurable, opt-out honored, never more often).
MARKETING_DAYS = int(os.getenv("MARKETING_INTERVAL_DAYS", "7"))
MARKETING_STATE_FILE = DATA_DIR / "marketing_state.json"

_OFFERS = {
    "web": {"title": "Веб-розробка Full-Stack", "desc": "Лендінги, корпоративні сайти та веб-застосунки на Next.js + FastAPI, швидкість і SEO.", "price_min": 15000},
    "webar": {"title": "Web3D / WebAR-візуалізація", "desc": "Інтерактивні 3D-сцени та AR-перегляд продукту в реальному просторі зі смартфона.", "price_min": 20000},
    "ai": {"title": "AI-агент/бот продажів", "desc": "Бот-консультант, генератор лідів, авто-відповіді 24/7 у Telegram та веб-чатах.", "price_min": 20000},
    "parser": {"title": "AI-бот-парсер заявок", "desc": "Моніторинг 30+ джерел, фільтр за критеріями, заявки у ваш Telegram щогодини.", "price_min": 20000},
    "dash": {"title": "BI-дашборд (звітність)", "desc": "Живі LTV/конверсія/MRR-панелі у реальному часі для вашого бізнесу.", "price_min": 15000},
    "trading": {"title": "Трейдинг-бот", "desc": "Автономна стратегія зі стоп-лосом і трейлингом на Binance Futures, паперовий або реальний режим.", "price_min": 25000},
    "full": {"title": "Комплексний проєкт під ключ", "desc": "AI-агенти, боти, дашборди + хмарний деплой і моніторинг 24/7.", "price_min": 50000},
}

# what the client asked → which arsenal items fit best
_OFFER_MATCH = {
    "web": ["web", "ai", "dash"],
    "webar": ["webar", "web", "ai"],
    "ai": ["ai", "parser", "full"],
    "parser": ["parser", "ai", "dash"],
    "dash": ["dash", "web", "ai"],
    "trading": ["trading", "parser", "ai"],
    "full": ["full", "web", "ai"],
}
_TYPE_TO_KEY = {
    "Веб-сайт / застосунок": "web",
    "3D / WebAR-візуалізація": "webar",
    "AI-агент / автоматизація": "ai",
    "Комплексний проєкт": "full",
}
_BUDGET_RANK = {"до 15 000": 15000, "15 000 – 50 000": 50000, "50 000 – 150 000": 150000, "від 150 000": 9999999}


def _mk_state() -> dict:
    try:
        return json.loads(MARKETING_STATE_FILE.read_text("utf-8")) if MARKETING_STATE_FILE.exists() else {}
    except json.JSONDecodeError:
        return {}


def _mk_save(state: dict) -> None:
    MARKETING_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")


def _extract_email(contact: str | None) -> str | None:
    c = (contact or "").strip()
    m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", c)
    return m.group(0) if m else None


def _mk_pick(lead: dict) -> list[str]:
    """Choose 1-3 offers that fit the client's type + budget."""
    key = _TYPE_TO_KEY.get((lead.get("type") or "").strip(), "full" if "Комплексн" in (lead.get("type") or "") else "web")
    cands = _OFFER_MATCH.get(key, ["web", "ai"])
    budget = None
    for k, v in _BUDGET_RANK.items():
        if k.lower() in (lead.get("budget") or "").lower():
            budget = v
    fits = []
    for cid in cands:
        off = _OFFERS[cid]
        if budget is not None and off["price_min"] > budget * 2:
            continue
        fits.append(cid)
        if len(fits) == 3:
            break
    return fits or ["web"]


def _mk_offers_html(cids: list[str], cta: bool = True) -> str:
    parts = []
    for cid in cids:
        off = _OFFERS[cid]
        parts.append(
            f'<div style="background:#0d1020;border:1px solid #232a45;border-radius:10px;'
            f'padding:14px 16px;margin:10px 0">'
            f'<div style="font-weight:700;color:#fff">{off["title"]}</div>'
            f'<div style="margin-top:4px;font-size:13px;color:#aab3cc">{off["desc"]} '
            f'Від {off["price_min"]:,} грн.</div>'.replace(",", " "))
    if cta and parts:
        parts.append(
            f'<div style="margin:16px 0;text-align:center"><a href="{SITE_URL}#contact" '
            f'style="background:#22d3ee;color:#04050c;text-decoration:none;padding:12px 26px;'
            f'border-radius:10px;font-weight:700">Обговорити пропозицію →</a></div>'
        )
    return "".join(parts)


def _mk_digest_html(lead: dict) -> str:
    name = (lead.get("name") or "друже").strip()
    offers = _mk_offers_html(_mk_pick(lead))
    unsub = f'{SITE_URL}/api/mail/unsubscribe?email={_extract_email(lead.get("contact"))}'
    body = (
        f'<p><b>Дякуємо, що обрали FastStart Digital!</b></p>'
        f'<p>Ви звернулись до нас з питанням про <b>{lead.get("type") or "розробку"}</b>. '
        f'Ось добірка пропозицій, які найкраще підходять вам:</p>'
        f'{offers}'
        f'<p style="font-size:12px;color:#8b93b2">Цей лист — раз на тиждень, не частіше. '
        f'<a href="{unsub}" style="color:#8b93b2">Відписатися</a> можна в один клік.</p>'
    )
    return _mail_frame(body)


def _mk_send_digest(lead: dict) -> bool:
    to = _extract_email(lead.get("contact"))
    if not to:
        return False
    ok, _ = _mail_send(
        to,
        f"FastStart Digital: пропозиції під ваш проєкт ({name})".replace("({name})", "") if False else "FastStart Digital — добірка пропозицій для вас",
        _mk_digest_html(lead),
    )
    return ok


def _mk_check_weekly() -> None:
    """Send the weekly digest to every subscribed client, but never more
    often than MARKETING_DAYS, and skip those who opted out."""
    now = time.time()
    state = _mk_state()
    last = state.get("last", {})
    unsub = set(state.get("unsubscribed", []))
    with _lock:
        rows = json.loads(LEADS_FILE.read_text("utf-8")) if LEADS_FILE.exists() else []
    seen = set()
    sent_any = False
    for lead in rows:
        to = _extract_email(lead.get("contact"))
        if not to or to in unsub or to in seen:
            continue
        seen.add(to)
        if now - last.get(to, 0) < MARKETING_DAYS * 86400:
            continue
        if _mk_send_digest(lead):
            last[to] = now
            sent_any = True
            print(f"[MK] weekly digest -> {to}")
    if sent_any:
        state["last"] = last
        _mk_save(state)


def _mk_loop() -> None:
    while not _mk_stop.is_set():
        _mk_stop.wait(6 * 3600)
        if _mk_stop.is_set():
            break
        try:
            _mk_check_weekly()
        except Exception as exc:
            print(f"[MK] weekly pass failed: {exc}")


_mk_stop = threading.Event()


def _mk_start() -> None:
    threading.Thread(target=_mk_loop, daemon=True).start()
    print(f"[MK] marketing agent online (every {MARKETING_DAYS} days)")


def _mk_shutdown() -> None:
    _mk_stop.set()


@app.get("/api/mail/unsubscribe")
def mail_unsubscribe(email: str) -> dict:
    """One-click opt-out from the weekly digest."""
    state = _mk_state()
    unsub = set(state.get("unsubscribed", []))
    unsub.add(email.strip().lower())
    state["unsubscribed"] = sorted(unsub)
    _mk_save(state)
    return {"ok": True, "unsubscribed": email.strip()}


@app.get("/api/marketing/status")
def marketing_status() -> dict:
    state = _mk_state()
    return {
        "ok": True,
        "interval_days": MARKETING_DAYS,
        "resend_ready": bool(RESEND_API_KEY),
        "subscribed": len(state.get("last", {})),
        "last_sent": state.get("last", {}),
        "unsubscribed": state.get("unsubscribed", []),
    }


class MKSendRequest(BaseModel):
    to: str | None = None      # send to one email (marketer test)
    force: bool = False        # ignore weekly interval


@app.post("/api/marketing/send")
def marketing_send(req: MKSendRequest, request: Request) -> dict:
    """Marketing agent manual trigger: digests for all / one recipient."""
    if request.headers.get("X-Mail-Token") != MAIL_API_TOKEN:
        raise HTTPException(401, "bad mail token")
    with _lock:
        rows = json.loads(LEADS_FILE.read_text("utf-8")) if LEADS_FILE.exists() else []
    if req.to:
        lead = next((r for r in rows if _extract_email(r.get("contact")) == req.to.strip().lower()), None)
        if not lead:
            raise HTTPException(404, "no lead for this email")
        ok, info = _mail_send(
            req.to.strip(), "FastStart Digital — добірка пропозицій для вас", _mk_digest_html(lead),
        )
        return {"ok": ok, "to": req.to, "error": None if ok else info}
    sent, errors = [], []
    state = _mk_state()
    now = time.time()
    seen = set()
    for lead in rows:
        to = _extract_email(lead.get("contact"))
        if not to or to in seen:
            continue
        seen.add(to)
        if not req.force and now - state.get("last", {}).get(to, 0) < MARKETING_DAYS * 86400:
            continue
        if _mk_send_digest(lead):
            sent.append(to)
        else:
            errors.append(to)
    if sent:
        state = _mk_state()
        for to in sent:
            state.setdefault("last", {})[to] = now
        _mk_save(state)
    return {"ok": not errors, "sent": sent, "failed": errors}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "faststart-portfolio", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/leads")
def list_leads(request: Request, limit: int = 50) -> dict:
    """Admin-only (AUTH_ADMIN_EMAILS): the full lead/CRM table."""
    session = _read_session(request)
    if not session or (session.get("email") or "").lower() not in AUTH_ADMIN_EMAILS:
        raise HTTPException(401, "auth required")
    with _lock:
        rows = json.loads(LEADS_FILE.read_text("utf-8")) if LEADS_FILE.exists() else []
    return {"count": len(rows), "leads": rows[-limit:][::-1]}


@app.post("/api/lead")
def create_lead(request: Request, lead: Lead) -> dict:
    data = lead.model_dump(exclude_none=True)
    session = _read_session(request)
    if session and session.get("email"):
        data["email"] = session["email"]
    _save_lead(data)
    return {"ok": True, "accepted": True}


# -------------------------------------------------------------- projects ----
@app.get("/api/projects")
def list_projects(request: Request, limit: int = 100) -> dict:
    """Client projects: the signed-in user sees their own (matched by email);
    an admin (in AUTH_ADMIN_EMAILS) sees every project."""
    session = _read_session(request)
    if not session:
        raise HTTPException(401, "auth required")
    email = (session.get("email") or "").lower()
    is_admin = email in AUTH_ADMIN_EMAILS
    with _lock:
        rows = json.loads(LEADS_FILE.read_text("utf-8")) if LEADS_FILE.exists() else []
    if not is_admin:
        rows = [r for r in rows
                if (r.get("email") or _extract_email(r.get("contact")) or "").lower() == email]
    projects = rows[-limit:][::-1]
    dev_count = sum(1 for p in projects if (p.get("status") or "").strip() == "в розробці")
    users = _read_users()
    db_u = next((x for x in users if x.get("email") == email), None)
    coupon_5 = bool(db_u and db_u.get("coupon_5"))
    return {"count": len(projects), "dev_count": dev_count, "admin": is_admin, "coupon_5": coupon_5, "projects": projects}


@app.patch("/api/projects/status")
def set_project_status(request: Request, item: dict) -> dict:
    """Admin-only: set a project status. Payload: {"ts": <lead ts>, "status": "..."}."""
    session = _read_session(request)
    email = (session.get("email") or "").lower() if session else ""
    if not session or email not in AUTH_ADMIN_EMAILS:
        raise HTTPException(401, "auth required")
    ts = item.get("ts")
    status = (item.get("status") or "").strip()
    if not ts or status not in ("нова", "в розробці", "завершено"):
        raise HTTPException(400, "invalid status or missing ts")
    with _lock:
        rows = json.loads(LEADS_FILE.read_text("utf-8")) if LEADS_FILE.exists() else []
        target = next((r for r in rows if r.get("ts") == ts), None)
        if not target:
            raise HTTPException(404, "project not found")
        target["status"] = status
        LEADS_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), "utf-8")
    return {"ok": True}


# --------------------------------------------------------- coupon 5% ----
@app.get("/api/coupon/status")
def coupon_status(request: Request) -> dict:
    """Check if the current user has claimed the 5% coupon."""
    session = _read_session(request)
    if not session:
        return {"ok": True, "claimed": False}
    email = (session.get("email") or "").lower()
    users = _read_users()
    u = next((x for x in users if x.get("email") == email), None)
    return {"ok": True, "claimed": bool(u and u.get("coupon_5"))}


@app.post("/api/coupon/claim")
def coupon_claim(request: Request) -> dict:
    """Claim the one-time 5% discount coupon."""
    session = _read_session(request)
    if not session:
        raise HTTPException(401, "auth required")
    email = (session.get("email") or "").lower()
    with _lock:
        users = _read_users()
        u = next((x for x in users if x.get("email") == email), None)
        if not u:
            raise HTTPException(404, "user not found")
        if u.get("coupon_5"):
            raise HTTPException(409, "already claimed")
        u["coupon_5"] = True
        u["coupon_5_claimed"] = datetime.now(timezone.utc).isoformat()
        USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), "utf-8")
    return {"ok": True}


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
    "sk": "\n\nOdpovedaj celé po slovensky.",
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
    "sk": "Neprechádzaj do žiadneho iného jazyka.",
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


def llm_reply(text: str, lang: str = "") -> str | None:
    if not lang:
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
    lang: str = ""


@app.post("/api/chat")
def chat(message: ChatMessage) -> dict:
    """NOVA agent: Qwen -> NVIDIA -> deterministic rules."""
    text = message.message.lower().strip()
    ui_lang = message.lang.strip().lower()
    if ui_lang not in ("uk", "en", "ru", "sk", "pl", "de"):
        ui_lang = ""
    llm = llm_reply(message.message, ui_lang)
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