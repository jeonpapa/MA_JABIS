"""kordoc CLI 래퍼 (HWP·HWPX·PDF → Markdown).

- 버전 고정(KORDOC_VERSION). `npx -y kordoc@^4` 는 레지스트리에 아직 tarball 이 없는 최신 태그를 집어
  ETARGET 으로 실패한 적이 있다(2026-10-29) → 정확한 버전만 쓴다.
- 결과는 data/hira_criteria/cache/<sha16>.md 에 캐시(원문 sha 가 같으면 재변환하지 않음).
- 빈 결과·비정상 종료는 예외. 큰 PDF(900쪽+)는 수십 분 걸릴 수 있으므로 timeout 을 넉넉히 둔다.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

KORDOC_VERSION = os.environ.get("KORDOC_VERSION", "4.24.16")
BASE_DIR = Path(__file__).resolve().parents[2]
CACHE_DIR = BASE_DIR / "data" / "hira_criteria" / "cache"


class KordocError(RuntimeError):
    pass


def _sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def convert(src: Path, *, pages: str | None = None, timeout: int = 3600, force: bool = False) -> Path:
    """src → Markdown 파일 경로. pages 예: '1-50'."""
    src = Path(src)
    if not src.exists() or src.stat().st_size == 0:
        raise KordocError(f"missing/empty source: {src}")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tag = _sha16(src) + (f"_p{pages.replace(',', '_')}" if pages else "")
    out = CACHE_DIR / f"{tag}.md"
    if out.exists() and out.stat().st_size > 0 and not force:
        return out
    npx = shutil.which("npx")
    if not npx:
        raise KordocError("npx not found (Node 20+ 필요)")
    # kordoc 은 확장자로 형식을 판단한다 → 캐시 폴더에 올바른 확장자로 링크
    ext = src.suffix.lower() or ".pdf"
    staged = CACHE_DIR / f"{tag}_src{ext}"
    if not staged.exists():
        shutil.copyfile(src, staged)
    cmd = [npx, "-y", f"kordoc@{KORDOC_VERSION}", str(staged), "-o", str(out)]
    if pages:
        cmd[4:4] = ["-p", pages]
    tmp_out = out.with_suffix(".part.md")
    cmd[-1] = str(tmp_out)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise KordocError(f"kordoc timeout ({timeout}s): {src.name}") from e
    if r.returncode != 0 or not tmp_out.exists() or tmp_out.stat().st_size == 0:
        raise KordocError(f"kordoc failed rc={r.returncode}: {(r.stderr or r.stdout)[-600:]}")
    tmp_out.rename(out)
    # 이미지 폴더가 생겼다면 그대로 둔다(증빙). staged 사본은 지운다.
    staged.unlink(missing_ok=True)
    return out
