"""인물 사진 정규화·저장·삭제 (admin 업로드 전용).

규칙(agents/rules/stakeholder_verbatim_rules.md §5): JPG/PNG/WEBP ≤5MB 만 받는다. Pillow 로 형식을 실제 바이트에서 판정하고
(선언 MIME·확장자 불신), EXIF 회전 보정 → 투명은 흰 배경 합성 → 640×640 중앙(얼굴 쪽 0.42) 크롭 → **새 이미지 객체**로 옮겨
WEBP 재인코딩한다. 새 객체에는 info(EXIF·ICC·XMP·주석)가 없으므로 메타데이터가 결과 파일에 실리지 않는다.
저장 경로는 볼륨 `photos/<person_id>-<hex>.webp` 만 — 읽기·삭제도 photos/ 아래로 resolve 검증한다(경로 탈출 방지).
언론사 CDN photo_url 은 여기서 다루지 않는다(핫링크 금지 — 렌더링·다운로드 모두 안 함).
"""
from __future__ import annotations

import io
import os
import re
import secrets
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from . import paths, store

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 40_000_000              # 원본 픽셀 상한(압축 폭탄 방어) — 5MB 안에 들어오는 정상 사진은 훨씬 작다
ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
OUTPUT_SIZE = (640, 640)
CENTERING = (0.5, 0.42)              # 인물 사진: 세로로 약간 위(얼굴) 기준
WEBP_QUALITY = 88
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]+")


class PhotoError(store.StoreError):
    """라우트가 그대로 HTTP 로 옮기는 오류(413 크기 · 415 형식 · 422 입력)."""

    def __init__(self, status: int, message: str, code: str):
        super().__init__(message, code)
        self.status = status


def normalize(raw: bytes) -> bytes:
    """업로드 바이트 → 640×640 WEBP(메타데이터 없음). 형식은 Pillow 판정(JPEG/PNG/WEBP 만), 애니메이션은 첫 프레임."""
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise PhotoError(422, "사진 파일이 비어 있습니다.", "PHOTO_EMPTY")
    if len(raw) > MAX_BYTES:
        raise PhotoError(413, "사진은 5MB 이하만 업로드할 수 있습니다.", "PHOTO_TOO_LARGE")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(raw))
            fmt = probe.format
            if fmt not in ALLOWED_FORMATS:
                raise PhotoError(415, "JPG·PNG·WEBP 사진만 업로드할 수 있습니다.", "PHOTO_TYPE")
            w, h = probe.size
            if w * h > MAX_PIXELS:
                raise PhotoError(413, "사진 해상도가 너무 큽니다.", "PHOTO_TOO_LARGE")
            probe.verify()                                  # 잘린·손상 파일 거부 (verify 뒤 객체는 재사용 불가)
            img = Image.open(io.BytesIO(raw))
            img.seek(0)
            img.load()
    except PhotoError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise PhotoError(413, "사진 해상도가 너무 큽니다.", "PHOTO_TOO_LARGE") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError) as exc:
        raise PhotoError(415, "이미지로 읽을 수 없는 파일입니다. JPG·PNG·WEBP 만 가능합니다.", "PHOTO_TYPE") from exc

    img = ImageOps.exif_transpose(img)                      # 회전 보정 먼저(EXIF 는 아래에서 버려진다)
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        img = bg
    else:
        img = img.convert("RGB")
    fitted = ImageOps.fit(img, OUTPUT_SIZE, Image.Resampling.LANCZOS, centering=CENTERING)
    clean = Image.new("RGB", OUTPUT_SIZE)                   # info 가 빈 새 객체 — EXIF/ICC/XMP 승계 차단
    clean.paste(fitted)
    out = io.BytesIO()
    clean.save(out, "WEBP", quality=WEBP_QUALITY, method=6)
    return out.getvalue()


def _photos_base(root: str | Path) -> Path:
    return paths.photos_dir(root).resolve()


def save_person_photo(person_id: str, raw: bytes, root: str | Path) -> str:
    """정규화 후 `photos/<person_id>-<hex>.webp` 로 원자 저장. 반환: 볼륨 root 기준 상대경로(sv_person.photo_path)."""
    data = normalize(raw)
    stem = _SAFE_ID.sub("_", person_id or "").strip("_")[:60] or "person"
    rel = f"{paths.PHOTOS_SUBDIR}/{stem}-{secrets.token_hex(6)}.webp"
    target = paths.resolve_under(root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return rel


def photo_file(rel_path: str | None, root: str | Path) -> Path | None:
    """photo_path → 실제 파일. photos/ 아래 실존 .webp 가 아니면 None(경로 탈출·다른 볼륨 파일 서빙 방지)."""
    if not rel_path:
        return None
    try:
        p = paths.resolve_under(root, rel_path)
    except ValueError:
        return None
    base = _photos_base(root)
    if base not in p.parents or p.suffix.lower() != ".webp" or not p.is_file():
        return None
    return p


def delete_photo(rel_path: str | None, root: str | Path) -> bool:
    """photos/ 아래 파일만 삭제한다. 반환: 삭제 여부."""
    p = photo_file(rel_path, root)
    if p is None:
        return False
    try:
        p.unlink()
        return True
    except OSError:
        return False
