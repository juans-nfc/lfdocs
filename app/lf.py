"""Minimal async client for the self-hosted Laserfiche Repository API (v1 or v2)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx


class LfError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class LfConfig:
    api_url: str
    api_version: str
    repository: str
    timeout: float = 30.0

    @property
    def repo_base(self) -> str:
        ver = (self.api_version or "v1").strip().lower()
        return f"{self.api_url.rstrip('/')}/{ver}/Repositories/{quote(self.repository, safe='')}"

    @property
    def is_v2(self) -> bool:
        return (self.api_version or "").strip().lower() == "v2"


def _entry(d: dict[str, Any]) -> dict[str, Any]:
    et = d.get("entryType") or ""
    if not et:
        t = d.get("@odata.type") or ""
        et = "Folder" if t.endswith("Folder") else "Document" if t.endswith("Document") else "Shortcut" if t.endswith("Shortcut") else ""
    return {
        "id": d.get("id"),
        "name": d.get("name") or "",
        "entryType": et,
        "isFolder": et.lower() == "folder",
        "fullPath": d.get("fullPath") or "",
        "folderPath": d.get("folderPath") or "",
        "creator": d.get("creator") or "",
        "modified": d.get("lastModifiedTime"),
        "created": d.get("creationTime"),
        "extension": d.get("extension") or "",
        "pageCount": d.get("pageCount"),
        "templateName": d.get("templateName") or "",
    }


def _error_text(resp: httpx.Response) -> str:
    try:
        j = resp.json()
        if isinstance(j, dict):
            for k in ("title", "error_description", "message", "error"):
                if j.get(k):
                    return str(j[k])
    except Exception:
        pass
    t = (resp.text or "").strip()
    return t[:300] if t else f"HTTP {resp.status_code}"


class LfClient:
    def __init__(self, cfg: LfConfig, http: httpx.AsyncClient):
        self.cfg = cfg
        self.http = http

    # ---- auth ----
    async def login(self, username: str, password: str) -> tuple[str, int]:
        """Returns (access_token, expires_in_seconds)."""
        resp = await self.http.post(
            f"{self.cfg.repo_base}/Token",
            data={"grant_type": "password", "username": username, "password": password},
            headers={"Accept": "application/json"},
        )
        if resp.status_code >= 400:
            raise LfError(resp.status_code, _error_text(resp))
        j = resp.json()
        token = j.get("access_token")
        if not token:
            raise LfError(500, "Login succeeded but no access token was returned.")
        try:
            expires = int(j.get("expires_in") or 900)
        except (TypeError, ValueError):
            expires = 900
        return token, expires

    async def _get(self, token: str, url: str) -> dict[str, Any]:
        resp = await self.http.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        if resp.status_code >= 400:
            raise LfError(resp.status_code, _error_text(resp))
        return resp.json()

    # ---- entries ----
    async def find_by_path(self, token: str, full_path: str) -> dict[str, Any] | None:
        url = f"{self.cfg.repo_base}/Entries/ByPath?fullPath={quote(full_path, safe='')}&fallbackToClosestAncestor=false"
        try:
            j = await self._get(token, url)
        except LfError as e:
            if e.status == 404:
                return None
            raise
        d = j.get("entry") if isinstance(j.get("entry"), dict) else (j if "id" in j else None)
        if not d or not d.get("id"):
            return None
        return _entry(d)

    async def children(self, token: str, folder_id: int) -> list[dict[str, Any]]:
        seg = "/Folder/Children" if self.cfg.is_v2 else "/Laserfiche.Repository.Folder/children"
        base = f"{self.cfg.repo_base}/Entries/{int(folder_id)}{seg}"
        select = "$select=id,name,entryType,fullPath,creator,creationTime,lastModifiedTime,templateName,extension,pageCount"
        url = f"{base}?$orderby=name&{select}"
        out: list[dict[str, Any]] = []
        tried_plain = False
        guard = 0
        while url and guard < 50:
            guard += 1
            try:
                j = await self._get(token, url)
            except LfError as e:
                if e.status == 400 and not tried_plain:
                    tried_plain = True
                    url = f"{base}?$orderby=name"
                    continue
                raise
            for item in j.get("value") or []:
                if isinstance(item, dict):
                    out.append(_entry(item))
            url = j.get("@odata.nextLink")
        return out

    async def simple_search(self, token: str, command: str) -> list[dict[str, Any]]:
        resp = await self.http.post(
            f"{self.cfg.repo_base}/SimpleSearches",
            json={"searchCommand": command},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        if resp.status_code >= 400:
            raise LfError(resp.status_code, "Search failed: " + _error_text(resp))
        j = resp.json()
        return [_entry(i) for i in (j.get("value") or []) if isinstance(i, dict)]

    async def find_folders_by_name(self, token: str, root_path: str, pattern: str, subfolders: bool = False) -> list[dict[str, Any]]:
        root = root_path.rstrip("\\")
        esc = lambda v: v.replace('"', '""')
        scope = "" if subfolders else ", Subfolders=0"
        cmd = f'{{LF:Name="{esc(pattern)}", Type=F}} & {{LF:LOOKIN="{esc(root)}"{scope}}}'
        hits = await self.simple_search(token, cmd)
        out = []
        for h in hits:
            if not h["isFolder"]:
                continue
            fp = (h.get("folderPath") or "").rstrip("\\")
            if not subfolders and fp and fp.lower() != root.lower():
                continue
            if not h["fullPath"]:
                h["fullPath"] = f"{fp or root}\\{h['name']}"
            # where the hit lives, relative to the root (empty for direct children)
            h["subPath"] = fp[len(root) + 1:] if fp.lower().startswith(root.lower() + "\\") else ""
            out.append(h)
        out.sort(key=lambda e: (e["subPath"].lower(), e["name"].lower()))
        return out

    async def find_by_field(self, token: str, root_path: str, field: str, value: str) -> list[dict[str, Any]]:
        root = root_path.rstrip("\\")
        esc = lambda v: v.replace('"', '""')
        cmd = f'{{[]:[{field.replace("]", "]]")}]="{esc(value)}"}} & {{LF:LOOKIN="{esc(root)}"}}'
        hits = await self.simple_search(token, cmd)
        for h in hits:
            fp = (h.get("folderPath") or "").rstrip("\\")
            if not h["fullPath"] and fp:
                h["fullPath"] = f"{fp}\\{h['name']}"
            h["subPath"] = fp[len(root) + 1:] if fp.lower().startswith(root.lower() + "\\") else ""
        hits.sort(key=lambda e: (e["subPath"].lower(), e["name"].lower()))
        return hits


BAD_CHARS = re.compile(r'[\\/:"<>|]')
WILDCARDS = re.compile(r"[*?]")
