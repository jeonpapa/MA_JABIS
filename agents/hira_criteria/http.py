"""HIRA HTTP 유틸 — 실패를 '없음' 으로 바꾸지 않는다.

- 전송 실패·HTTP 오류·0바이트·기대 형식 불일치(HTML 오류 페이지 등)는 모두 예외(FetchError).
- 저장은 임시 이름 → 검증 → 최종 이름(rename). 빈 파일이 캐시가 되는 사고(2026-10-02 KEB) 재발 방지.
"""
from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Iterable, Optional

BASE = "https://www.hira.or.kr"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

MAGIC = {
    "pdf": b"%PDF",
    "hwp": bytes.fromhex("d0cf11e0a1b11ae1"),   # OLE (HWP 5.x, XLS)
    "zip": b"PK\x03\x04",                        # HWPX, XLSX, DOCX
}


class FetchError(RuntimeError):
    pass


def _abs(url: str) -> str:
    return url if url.startswith("http") else urllib.parse.urljoin(BASE, url)


def fetch(url: str, *, data: Optional[dict] = None, timeout: int = 60, retries: int = 3,
          backoff: float = 2.0) -> tuple[bytes, Message]:
    """GET(또는 data 가 있으면 form POST). 성공 시 (body, headers)."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    last: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(_abs(url), data=body, headers={"User-Agent": UA})
            if body is not None:
                req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read()
                if r.status != 200:
                    raise FetchError(f"HTTP {r.status}: {url}")
                if not b:
                    raise FetchError(f"0 bytes: {url}")
                return b, r.headers
        except Exception as e:  # noqa: BLE001 — 재시도 후 FetchError 로 올린다
            last = e
            time.sleep(backoff * (i + 1))
    raise FetchError(f"fetch failed after {retries} tries: {url} ({last})")


def fetch_text(url: str, **kw) -> str:
    b, _ = fetch(url, **kw)
    return b.decode("utf-8", "replace")


def sniff(b: bytes) -> str:
    if b.startswith(MAGIC["pdf"]):
        return "pdf"
    if b.startswith(MAGIC["hwp"]):
        return "ole"
    if b.startswith(MAGIC["zip"]):
        return "zip"
    if b.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        return "html"
    return "unknown"


def filename_from_headers(h: Message) -> str:
    cd = h.get("Content-Disposition", "") or ""
    # HIRA 는 UTF-8 바이트를 latin-1 로 보낸다
    try:
        cd = cd.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
    return urllib.parse.unquote(m.group(1)).strip() if m else ""


def download(url: str, dest_dir: Path, *, expect: Iterable[str] = ("pdf", "ole", "zip"),
             name_hint: str = "", data: Optional[dict] = None) -> dict:
    """다운로드 → 형식 검증 → sha256 이름으로 저장. 반환: {path, sha256, kind, filename, size, url}."""
    b, h = fetch(url, data=data)
    kind = sniff(b)
    if kind not in set(expect):
        raise FetchError(f"unexpected content ({kind}, {len(b)} bytes): {url}")
    sha = hashlib.sha256(b).hexdigest()
    fname = filename_from_headers(h) or name_hint or sha[:16]
    ext = Path(fname).suffix.lower() or {"pdf": ".pdf", "ole": ".hwp", "zip": ".zip"}.get(kind, ".bin")
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / f"{sha[:16]}{ext}"
    if not final.exists():
        tmp = final.with_suffix(final.suffix + ".part")
        tmp.write_bytes(b)
        if tmp.stat().st_size != len(b):
            tmp.unlink(missing_ok=True)
            raise FetchError(f"write size mismatch: {final}")
        tmp.rename(final)
    return {"path": final, "sha256": sha, "kind": kind, "filename": fname, "size": len(b), "url": _abs(url)}


def strip_html(h: str) -> str:
    h = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", h, flags=re.I)
    h = re.sub(r"<br\s*/?>", "\n", h, flags=re.I)
    h = re.sub(r"</(p|div|li|tr|h\d)>", "\n", h, flags=re.I)
    h = re.sub(r"<[^>]+>", " ", h)
    import html as _html
    h = _html.unescape(h).replace("\xa0", " ")
    h = re.sub(r"[ \t\r\f\v]+", " ", h)
    h = re.sub(r"\n\s*\n+", "\n", h)
    return h.strip()
