"""LF Docs — find Laserfiche folders/documents by number and open them in the web client.

Runs under a path prefix (BASE_PATH, e.g. /lfdocs) behind nginx.
"""
from __future__ import annotations

import base64
import json
import os
import re
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


def _load_kinds() -> list[dict[str, Any]]:
    """LOOKUPS="id|Label|\\Root|match|subfolders;..." -> tabs in the UI.

    match: prefix (default) | contains | field:<Field name>; subfolders: 1/0 (default: 1 for contains, else 0).
    Same format as the desktop utility's settings.
    """
    raw = os.getenv("LOOKUPS", r"orders|Order|\Sales\Orders|prefix|0;ap|AP Invoice|\Accounts Payable|prefix|1;employees|Employee|\Active Employees|contains|1")
    kinds = []
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        bits = [b.strip() for b in part.split("|")]
        if len(bits) < 3:
            raise RuntimeError(f"Bad LOOKUPS entry: {part!r} (expected id|Label|\\Root\\Path[|match[|subfolders]])")
        kid, label, root = bits[0], bits[1], "\\" + bits[2].strip("\\")
        match = (bits[3] if len(bits) > 3 and bits[3] else "prefix")
        is_field = match.lower().startswith("field:")
        is_contains = match.lower() == "contains"
        if len(bits) > 4 and bits[4]:
            sub = bits[4].lower() in ("1", "true", "yes", "sub", "subfolders")
        else:
            sub = is_contains
        kinds.append({
            "id": kid, "label": label, "root": root,
            "match": "field" if is_field else "contains" if is_contains else "prefix",
            "field": match[6:].strip() if is_field else "",
            "subfolders": sub,
        })
    if not kinds:
        raise RuntimeError("LOOKUPS is empty")
    return kinds


DATA_DIR = Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
LOOKUPS_FILE = DATA_DIR / "lookups.json"
ADMIN_USERS = {u.strip().lower() for u in os.getenv("ADMIN_USERS", "").split(",") if u.strip()}

LOOKUP_KEYS = ("id", "label", "root", "match", "field", "subfolders")


def _normalize_kind(k: dict[str, Any]) -> dict[str, Any] | None:
    kid = re.sub(r"[^a-z0-9]", "", str(k.get("id") or k.get("label") or "").lower())
    label = str(k.get("label") or "").strip()
    root = "\\" + str(k.get("root") or "").strip().strip("\\")
    match = str(k.get("match") or "prefix").strip().lower()
    field = str(k.get("field") or "").strip()
    if match not in ("prefix", "contains", "field"):
        match = "prefix"
    if match == "field" and not field:
        match = "prefix"
    if not kid or not label or root == "\\":
        return None
    return {"id": kid, "label": label, "root": root, "match": match, "field": field if match == "field" else "", "subfolders": bool(k.get("subfolders"))}


def _load_saved_kinds() -> list[dict[str, Any]] | None:
    try:
        if not LOOKUPS_FILE.exists():
            return None
        raw = json.loads(LOOKUPS_FILE.read_text("utf-8"))
        out, seen = [], set()
        for k in raw if isinstance(raw, list) else []:
            n = _normalize_kind(k) if isinstance(k, dict) else None
            if n and n["id"] not in seen:
                seen.add(n["id"]); out.append(n)
        return out or None
    except Exception:
        return None


def _save_kinds(kinds: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LOOKUPS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(kinds, indent=2), "utf-8")
    tmp.replace(LOOKUPS_FILE)


KINDS: list[dict[str, Any]] = _load_saved_kinds() or _load_kinds()   # shared defaults


def _set_kinds(kinds: list[dict[str, Any]]) -> None:
    global KINDS
    KINDS = kinds


def _user_file(user: str) -> Path:
    safe = re.sub(r"[^a-z0-9]+", "_", (user or "").lower()).strip("_") or "user"
    return DATA_DIR / "users" / f"{safe}.json"


def _load_user_kinds(user: str) -> list[dict[str, Any]] | None:
    """The user's own list, or None if they use the shared defaults."""
    try:
        f = _user_file(user)
        if not f.exists():
            return None
        raw = json.loads(f.read_text("utf-8"))
        out, seen = [], set()
        for k in raw if isinstance(raw, list) else []:
            n = _normalize_kind(k) if isinstance(k, dict) else None
            if n and n["id"] not in seen:
                seen.add(n["id"]); out.append(n)
        return out or None
    except Exception:
        return None


def _effective_kinds(session: dict[str, Any] | None) -> list[dict[str, Any]]:
    if session and session.get("user"):
        mine = _load_user_kinds(session["user"])
        if mine:
            return mine
    return KINDS


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), "utf-8")
    tmp.replace(path)


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
        "kinds": _effective_kinds(s),
        "signedIn": bool(s),
        "username": (s or {}).get("user") or _suggested_username(request),
        "isAdmin": _is_admin(request, s),
        "hasCustom": bool(s and s.get("user") and _load_user_kinds(s["user"])),
    }


def _is_admin(request: Request, session: dict[str, Any] | None) -> bool:
    if not ADMIN_USERS:
        return False
    names = set()
    if session and session.get("user"):
        names.add(session["user"].lower())
    for h in ("x-auth-request-email", "x-auth-request-user"):
        v = request.headers.get(h)
        if v:
            names.add(v.lower()); names.add(v.split("@")[0].lower())
    # allow matching without the DOMAIN\ prefix too
    names |= {n.split("\\")[-1] for n in list(names)}
    return bool(names & ADMIN_USERS)


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
    k = next((x for x in _effective_kinds(session) if x["id"] == kind), None)
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
        if k["match"] == "field":
            hits = await lf().find_by_field(token, root, k["field"], q)
            if not hits:
                return {"found": False, "message": f"Nothing under {root} has {k['field']} = {q}"}
            if len(hits) == 1 and hits[0]["isFolder"]:
                children = await lf().children(token, hits[0]["id"])
                return {"found": True, "folder": _decorate(hits)[0], "matchedFrom": q, "children": _decorate(children)}
            return {"found": True, "multiple": True, "pattern": f"{k['field']} = {q}", "root": root, "matches": _decorate(hits)}

        folder = await lf().find_by_path(token, f"{root}\\{q}") if exact else None
        matched_from = None
        if folder is None:
            pattern = q if not exact else (f"*{q}*" if k["match"] == "contains" else f"{q}*")
            matches = await lf().find_folders_by_name(token, root, pattern, subfolders=k["subfolders"])
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


# ---------------------------------------------------------------- settings (document types)

@app.get("/api/lookups")
async def api_lookups_get(request: Request, session: dict = Depends(require_session)):
    mine = _load_user_kinds(session["user"])
    return {
        "mine": mine or KINDS,
        "hasCustom": bool(mine),
        "shared": KINDS,
        "isAdmin": _is_admin(request, session),
    }


class LookupsBody(BaseModel):
    lookups: list[dict[str, Any]]
    scope: str = "mine"   # "mine" (this user) | "shared" (defaults, admins only)


def _clean_list(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out, seen = [], set()
    for k in items:
        n = _normalize_kind(k)
        if not n:
            continue
        base, i = n["id"], 2
        while n["id"] in seen:
            n["id"] = f"{base}{i}"; i += 1
        seen.add(n["id"]); out.append(n)
    return out


@app.put("/api/lookups")
async def api_lookups_put(body: LookupsBody, request: Request, session: dict = Depends(require_session)):
    out = _clean_list(body.lookups)
    if not out:
        raise HTTPException(400, "Add at least one document type with a name and a root folder")
    try:
        if body.scope == "shared":
            if not _is_admin(request, session):
                raise HTTPException(403, "Only administrators listed in ADMIN_USERS can change the shared defaults")
            _write_json(LOOKUPS_FILE, out)
            _set_kinds(out)
        else:
            _write_json(_user_file(session["user"]), out)
    except OSError as e:
        raise HTTPException(500, f"Could not save document types: {e}")
    return {"kinds": _effective_kinds(session), "hasCustom": bool(_load_user_kinds(session["user"]))}


@app.delete("/api/lookups")
async def api_lookups_reset(session: dict = Depends(require_session)):
    """Drop this user's own list and go back to the shared defaults."""
    try:
        f = _user_file(session["user"])
        if f.exists():
            f.unlink()
    except OSError as e:
        raise HTTPException(500, f"Could not reset: {e}")
    return {"kinds": KINDS, "hasCustom": False}


@app.get("/api/subfolders/{folder_id}")
async def api_subfolders(folder_id: int, session: dict = Depends(require_session)):
    """Folder picker: immediate subfolders of an entry (1 = repository root)."""
    try:
        children = await lf().children(session["tok"], folder_id)
    except LfError as e:
        return _lf_error(e)
    except httpx.HTTPError as e:
        return JSONResponse({"error": f"Laserfiche is not reachable: {e}"}, status_code=502)
    folders = [{"id": c["id"], "name": c["name"], "fullPath": c["fullPath"]} for c in children if c["isFolder"]]
    folders.sort(key=lambda f: f["name"].lower())
    return {"folders": folders}


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
