"""
webapp/api.py — fastapi бэкенд для sentinel music webapp
"""
import os
import sys
import time
import hashlib
import secrets
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import database as db
import downloader
from config import OWNER_ID, TOKEN

app = FastAPI(title="Sentinel Music API")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── auth ──────────────────────────────────────────────────────────

_sessions: dict[str, tuple[int, float]] = {}
SESSION_TTL = 86400 * 7  # 7 дней


def _make_session(user_id: int) -> str:
    token = secrets.token_hex(32)
    _sessions[token] = (user_id, time.time() + SESSION_TTL)
    return token


def _check_session(token: str) -> int | None:
    entry = _sessions.get(token)
    if not entry:
        return None
    user_id, expires = entry
    if time.time() > expires:
        del _sessions[token]
        return None
    return user_id


def _verify_tg_data(init_data: str) -> dict | None:
    """проверяем Telegram WebApp initData"""
    import urllib.parse
    import hmac
    try:
        params = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        check_hash = params.pop("hash", "")
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, check_hash):
            return None
        import json
        user = json.loads(params.get("user", "{}"))
        return user
    except Exception:
        return None


@app.post("/api/auth/code")
async def auth_by_code(request: Request):
    """вход по коду из /code команды бота"""
    body = await request.json()
    code = str(body.get("code", "")).strip()
    if not code:
        raise HTTPException(400, "code required")

    user_id = await db.pop_login_code(code)
    if not user_id:
        raise HTTPException(401, "неверный или истёкший код")
    token = _make_session(user_id)
    return {"ok": True, "token": token}


@app.post("/api/auth")
async def auth(request: Request):
    body = await request.json()
    init_data = body.get("initData", "")
    user = _verify_tg_data(init_data)
    if not user:
        # dev режим — owner может войти напрямую
        if body.get("dev_token") == hashlib.sha256((TOKEN[:16] + str(OWNER_ID)).encode()).hexdigest()[:12].upper():
            user = {"id": OWNER_ID, "first_name": "eterytyy"}
        else:
            raise HTTPException(401, "unauthorized")
    uid = user["id"]
    await db.get_or_create_user(uid, user.get("username", ""), user.get("first_name", ""))
    token = _make_session(uid)
    return {"ok": True, "token": token, "user": user}


def _get_uid(request: Request) -> int:
    token = request.headers.get("X-Session-Token", "")
    uid = _check_session(token)
    if not uid:
        raise HTTPException(401, "unauthorized")
    return uid


# ── search ────────────────────────────────────────────────────────

@app.get("/api/search")
async def search(q: str = Query(...), platform: str = Query("sc"), request: Request = None):
    uid = _get_uid(request)
    results = await downloader.search(q, platform=platform, limit=12)
    return {"ok": True, "results": results}


# ── track info ────────────────────────────────────────────────────

@app.get("/api/track/{track_id:path}")
async def get_track(track_id: str, request: Request):
    _get_uid(request)
    track = await db.get_track(track_id)
    if not track:
        raise HTTPException(404, "track not found")
    return {"ok": True, "track": track}


# ── stream ────────────────────────────────────────────────────────

@app.get("/api/stream/{track_id:path}")
async def stream_track(track_id: str, request: Request, tok: str = None):
    # принимаем токен из query param (нужно для <audio> тега)
    if tok:
        uid = _check_session(tok)
        if not uid:
            raise HTTPException(401, "unauthorized")
    else:
        _get_uid(request)
    track = await db.get_track(track_id)
    file_path = track.get("file_path") if track else None

    if not file_path or not os.path.exists(file_path):
        # качаем если нет
        try:
            file_path = await downloader.download(track_id)
        except Exception as e:
            raise HTTPException(500, str(e))

    return FileResponse(
        file_path,
        media_type="audio/mpeg",
        headers={"Accept-Ranges": "bytes"}
    )


# ── download trigger ──────────────────────────────────────────────

@app.post("/api/download")
async def trigger_download(request: Request):
    uid = _get_uid(request)
    body = await request.json()
    track_id = body.get("track_id")
    if not track_id:
        raise HTTPException(400, "track_id required")

    track = await db.get_track(track_id)
    if not track:
        raise HTTPException(404, "track not found")

    # если уже скачан
    if track.get("file_path") and os.path.exists(track["file_path"]):
        return {"ok": True, "cached": True}

    # качаем в фоне
    asyncio.create_task(downloader.download(track_id))
    return {"ok": True, "cached": False, "status": "downloading"}


# ── likes ─────────────────────────────────────────────────────────

@app.post("/api/like")
async def like_track(request: Request):
    uid = _get_uid(request)
    body = await request.json()
    track_id = body.get("track_id")
    if not track_id:
        raise HTTPException(400, "track_id required")
    liked = await db.toggle_like(uid, track_id)
    return {"ok": True, "liked": liked}


@app.get("/api/likes")
async def get_likes(request: Request):
    uid = _get_uid(request)
    likes = await db.get_likes(uid)
    return {"ok": True, "tracks": likes}


# ── history ───────────────────────────────────────────────────────

@app.get("/api/history")
async def get_history(request: Request):
    uid = _get_uid(request)
    history = await db.get_history(uid)
    return {"ok": True, "tracks": history}


@app.post("/api/history")
async def add_history(request: Request):
    uid = _get_uid(request)
    body = await request.json()
    track_id = body.get("track_id")
    if track_id:
        await db.add_history(uid, track_id)
    return {"ok": True}


# ── thumbnail proxy ───────────────────────────────────────────────

@app.get("/api/thumb/{track_id:path}")
async def get_thumb(track_id: str, request: Request):
    _get_uid(request)
    track = await db.get_track(track_id)
    thumb_url = track.get("thumbnail") if track else None
    if not thumb_url:
        raise HTTPException(404, "no thumbnail")
    path = await downloader.get_thumbnail(thumb_url, track_id)
    if not path:
        raise HTTPException(404, "thumbnail download failed")
    return FileResponse(path, media_type="image/jpeg")


# ── health ────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"ok": True, "ts": int(time.time())}
