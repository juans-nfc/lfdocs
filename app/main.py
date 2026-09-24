"""LF Docs — find Laserfiche folders/documents by number and open them in the web client.

Runs under a path prefix (BASE_PATH, e.g. /lfdocs) behind nginx.
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, Response as RawResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .lf import BAD_CHARS, WILDCARDS, LfClient, LfConfig, LfError

# ---------------------------------------------------------------- config

BASE_PATH = ("/" + os.getenv("BASE_PATH", "/lfdocs").strip("/")).rstrip("/") or ""
LF = LfConfig(
    api_url=os.getenv("LF_API_URL", "https://lf.northernfruit.com/LFRepositoryAPI"),
    api_version=os.getenv("LF_API_VERSION", "v1"),
    repository=os.getenv("LF_REPOSITORY", "NorthernFruit"),
)
LF_WEB_URL = os.getenv("LF_WEB_URL", "https://lf.northernfruit.com/laserfiche").rstrip("/")
APP_TITLE = os.getenv("APP_TITLE", "LF Docs")
COOKIE_NAME = "lfdocs_session"
VERIFY_TLS = os.getenv("LF_VERIFY_TLS", "true").lower() not in ("0", "false", "no")


def _load_kinds() -> list[dict[str, str]]:
    """LOOKUPS="orders|Order|\\Sales\\Orders;invoices|Invoice|\\AP\\Invoices" -> tabs in the UI."""
    raw = os.getenv("LOOKUPS", r"orders|Order|\Sales\Orders")
    kinds = []
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        bits = [b.strip() for b in part.split("|")]
        if len(bits) != 3:
            raise RuntimeError(f"Bad LOOKUPS entry: {part!r} (expected id|Label|\\Root\\Path)")
        kid, label, root = bits
        root = "\\" + root.strip("\\")
        kinds.append({"id": kid, "label": label, "root": root})
    if not kinds:
        raise RuntimeError("LOOKUPS is empty")
    return kinds


KINDS = _load_kinds()
KIND_BY_ID = {k["id"]: k for k in KINDS}


def _fernet() -> Fernet:
    key = os.getenv("SECRET_KEY", "").strip()
    if not key:
        raise RuntimeError("SECRET_KEY is required (python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\")")
    if len(key) != 44:  # allow any passphrase: derive a urlsafe 32-byte key from it
        import hashlib
        key = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()).decode()
    return Fernet(key)


FERNET = _fernet()
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# ---------------------------------------------------------------- app

app = FastAPI(title=APP_TITLE, root_path=BASE_PATH, docs_url=None, redoc_url=None, openapi_url=None)
http_client: httpx.AsyncClient | None = None


@app.on_event("startup")
async def _startup() -> None:
    global http_client
    http_client = httpx.AsyncClient(timeout=LF.timeout, verify=VERIFY_TLS)


@app.on_event("shutdown")
async def _shutdown() -> None:
    if http_client:
        await http_client.aclose()


def lf() -> LfClient:
    assert http_client is not None
    return LfClient(LF, http_client)


# ---------------------------------------------------------------- session cookie

def _read_session(request: Request) -> dict[str, Any] | None:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    try:
        data = json.loads(FERNET.decrypt(raw.encode(), ttl=None))
    except (InvalidToken, ValueError):
        return None
    if data.get("exp", 0) <= time.time():
        return None
    return data


def _write_session(request: Request, response: Response, data: dict[str, Any]) -> None:
    raw = FERNET.encrypt(json.dumps(data).encode()).decode()
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(
        COOKIE_NAME, raw, max_age=int(data["exp"] - time.time()), httponly=True, samesite="lax",
        secure=secure, path=(BASE_PATH or "/"),
    )


def _clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path=(BASE_PATH or "/"))


def require_session(request: Request) -> dict[str, Any]:
    s = _read_session(request)
    if not s:
        raise HTTPException(401, "Not signed in")
    return s


def _suggested_username(request: Request) -> str:
    email = request.headers.get("x-auth-request-email") or request.headers.get("x-auth-request-user") or ""
    return email.split("@")[0] if email else ""


def _web_url(entry: dict[str, Any]) -> str:
    page = "browse.aspx" if entry["isFolder"] else "docview.aspx"
    sep = "#?" if entry["isFolder"] else "&"
    return f"{LF_WEB_URL}/{page}?db={quote(LF.repository, safe='')}{sep}id={entry['id']}"


def _decorate(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for e in entries:
        e["webUrl"] = _web_url(e)
        e["lfeUrl"] = (f"api/lfe/{e['id']}?name={quote(e['name'], safe='')}"
                       + ("&folder=true" if e["isFolder"] else f"&pages={int(e.get('pageCount') or 0)}"))
    return entries


def _lf_error(e: LfError, response: Response | None = None) -> JSONResponse:
    if e.status in (401, 403):
        r = JSONResponse({"error": "Your Laserfiche session has expired or was refused. Sign in again.", "reauth": True}, status_code=401)
        _clear_session(r)
        return r
    return JSONResponse({"error": e.message}, status_code=502)


# ---------------------------------------------------------------- API

class LoginBody(BaseModel):
    username: str
    password: str


@app.get("/api/config")
async def api_config(request: Request) -> dict[str, Any]:
    s = _read_session(request)
    return {
        "title": APP_TITLE,
        "repository": LF.repository,
        "webUrl": LF_WEB_URL,
        "kinds": KINDS,
        "signedIn": bool(s),
        "username": (s or {}).get("user") or _suggested_username(request),
    }


@app.post("/api/login")
async def api_login(body: LoginBody, request: Request):
    user = body.username.strip()
    if not user or not body.password:
        raise HTTPException(400, "User name and password are required")
    try:
        token, expires = await lf().login(user, body.password)
    except LfError as e:
        if e.status in (400, 401, 403):
            return JSONResponse({"error": "Sign-in failed: " + e.message}, status_code=401)
        return JSONResponse({"error": "Laserfiche is not reachable: " + e.message}, status_code=502)
    except httpx.HTTPError as e:
        return JSONResponse({"error": f"Laserfiche is not reachable: {e}"}, status_code=502)
    exp = time.time() + max(60, expires - 30)
    resp = JSONResponse({"ok": True, "username": user, "expiresIn": int(exp - time.time())})
    _write_session(request, resp, {"tok": token, "exp": exp, "user": user})
    return resp


@app.post("/api/logout")
async def api_logout():
    resp = JSONResponse({"ok": True})
    _clear_session(resp)
    return resp


@app.get("/api/lookup/{kind}")
async def api_lookup(kind: str, q: str, session: dict = Depends(require_session)):
    k = KIND_BY_ID.get(kind)
    if not k:
        raise HTTPException(404, "Unknown lookup")
    q = q.strip()
    if not q:
        raise HTTPException(400, "Enter a number")
    if BAD_CHARS.search(q):
        raise HTTPException(400, "That contains characters not allowed in a folder name")
    token = session["tok"]
    root = k["root"]
    exact = not WILDCARDS.search(q)
    try:
        folder = await lf().find_by_path(token, f"{root}\\{q}") if exact else None
        matched_from = None
        if folder is None:
            pattern = q + "*" if exact else q
            matches = await lf().find_folders_by_name(token, root, pattern)
            if not matches:
                return {"found": False, "message": f"No {k['label'].lower()} folder matching {pattern} in {root}", "pattern": pattern}
            if len(matches) > 1:
                return {"found": True, "multiple": True, "pattern": pattern, "root": root, "matches": _decorate(matches)}
            folder = matches[0]
            matched_from = q
        elif not folder["isFolder"]:
            return {"found": False, "message": f"{root}\\{q} exists but is a {folder['entryType']}, not a folder"}
        children = await lf().children(token, folder["id"])
    except LfError as e:
        return _lf_error(e)
    except httpx.HTTPError as e:
        return JSONResponse({"error": f"Laserfiche is not reachable: {e}"}, status_code=502)
    if not folder.get("fullPath"):
        folder["fullPath"] = f"{root}\\{folder['name']}"
    return {
        "found": True,
        "folder": _decorate([folder])[0],
        "matchedFrom": matched_from,
        "children": _decorate(children),
    }


@app.get("/api/folder/{folder_id}")
async def api_folder(folder_id: int, session: dict = Depends(require_session)):
    token = session["tok"]
    try:
        children = await lf().children(token, folder_id)
    except LfError as e:
        return _lf_error(e)
    except httpx.HTTPError as e:
        return JSONResponse({"error": f"Laserfiche is not reachable: {e}"}, status_code=502)
    return {"children": _decorate(children)}


# ---------------------------------------------------------------- .lfe shortcut for the Windows client

def _xml_attr(v: str) -> str:
    return v.replace("&", "&amp;").replace("'", "&apos;").replace("<", "&lt;").replace(">", "&gt;")


@app.get("/api/lfe/{entry_id}")
async def api_lfe(entry_id: int, name: str = "", pages: int = 0, folder: bool = False, session: dict = Depends(require_session)):
    """Laserfiche Windows client shortcut (.lfe). Folders open with the tree shown; documents open in the
    document viewer (imaged pages) or the native application (electronic document without pages)."""
    if folder:
        entry = f"<entry id='{entry_id}' makeroot='n' />"
    else:
        entry = f"<entry id='{entry_id}' mode='{1 if pages > 0 else 2}' />"
    xml = (
        "<?xml version='1.0' encoding='utf-8'?>\r\n<laserfiche>\r\n"
        f"  <repository name='{_xml_attr(LF.repository)}'>\r\n    {entry}\r\n  </repository>\r\n</laserfiche>\r\n"
    )
    safe = "".join(c for c in (name or f"entry-{entry_id}") if c.isalnum() or c in " -_.()")[:80].strip() or f"entry-{entry_id}"
    return RawResponse(
        content=xml.encode("utf-8"),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe}.lfe"', "Cache-Control": "no-store"},
    )


@app.get("/api/health")
async def api_health() -> dict[str, Any]:
    return {"ok": True, "kinds": [k["id"] for k in KINDS]}


# ---------------------------------------------------------------- static / PWA

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@app.get("/sw.js")
async def sw():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": (BASE_PATH or "/")})


app.mount("/", StaticFiles(directory=str(STATIC_DIR)), name="static")
