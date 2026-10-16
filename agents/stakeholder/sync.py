"""소스 repo git-sync + 스냅샷 멱등 import + SHA-256 무결성 + CLI (T5).

외부 에이전트(Key/Hermes, VPS)가 `jeonpapa/kr-drug-pricing-nhi-finance-intelligence` 에 발행하는 산출물을
볼륨 root(`data/stakeholder/`, 소스 repo 와 같은 상대경로)에 **읽기 전용으로 미러**하고 `sv_*` 테이블에 적재한다.

흐름 (sync)
  1) 목록 — 원격: GitHub Git Trees API(`/git/trees/{branch}?recursive=1`) / source_dir: os.walk + git blob sha
  2) 허용목록(paths.is_sync_allowed) + MAX_BLOB_BYTES 필터 → rejected
  3) sv_sync_file 의 blob_sha(+디스크 존재·크기)와 대조 → 바뀐/없는 파일만 받는다. 받은 바이트의 git blob sha 를
     목록 sha 와 대조(불일치는 쓰지 않음). `.tmp` 에 쓰고 os.replace. 한 번에 MAX_SYNC_BYTES(200MB) 상한 — 넘치면 deferred.
     원격에서 사라진 경로는 지우지 않는다(기록 삭제 금지) → orphaned.
  4) import_snapshot — manifest/런/레지스트리/정본/중요도 적재 + approval.apply_scheduled + 메타.

소유권 (agents/rules/stakeholder_verbatim_rules.md)
- sv_person · sv_person_role_history · sv_source_domain · sv_audit_proposal(+event) 는 **대쉬보드가 기록 원본** —
  sync 는 insert-if-absent 만 한다(운영자 승인·반려·상태·효력일은 어떤 sync 에도 덮어써지지 않는다).
  예외: sv_person.importance_score/importance_json 두 컬럼(부서-인물지도 §2.2 — sync 소유).
- sv_source · sv_artifact · sv_monitoring_run · sv_audit_run · 정본(origin='canonical_md') 은 외부 산출물 미러.
- 모든 DB 쓰기는 `with store.transaction(...)` 안에서(apply_scheduled 는 제안마다 자체 트랜잭션).

프로덕션 fail-closed: source_dir 없이 토큰(STAKEHOLDER_SYNC_TOKEN → GITHUB_TOKEN)도 없으면 `/app/data` 가 있는 환경에서는
원격 호출 없이 `auth required` + error_class="config" 를 돌려준다(policy_analysis_sync 선례). 이때도 DB 가 이미 있으면
apply_scheduled 는 돈다 — 운영자 결정의 익일 반영은 GitHub 접근과 무관하다.

공개 API: sync(), sync_now(), import_snapshot(), fetch_tree_github(), fetch_blob_github(), git_blob_sha(), main().
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import logging
import os
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterator

from . import approval, canonical_parse, paths, signals, store

logger = logging.getLogger(__name__)

DEFAULT_REPO = "jeonpapa/kr-drug-pricing-nhi-finance-intelligence"
DEFAULT_BRANCH = "main"
GITHUB_API = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
HTTP_TIMEOUT_SECONDS = 30
MAX_SYNC_BYTES = 200 * 1024 * 1024            # 한 번의 sync 가 받는 총량 상한 — 넘치면 다음 실행(deferred)
SYNC_ACTOR = "system:sync"
CANONICAL_TIMELINE_GLOB = "*_주제별_인물발언_타임라인_정본.md"
CANONICAL_TIMELINE_ARTIFACT = "topic_timeline_canonical"
PEOPLE_MAP_GLOB = "*_부서-인물지도_정본.md"
COPYRIGHT_FALLBACK = "원문 저작권은 각 발행기관·언론사에 있으며, 내부 검증과 provenance 보존 목적으로 저장되었습니다."
_SKIP_WALK_DIRS = {".git", "__pycache__"}
_MANIFEST_META_SCALARS = ("project", "scope_start_kst", "scope_end_kst", "retrieved_at_kst", "updated_at_kst")
_MANIFEST_META_JSON = ("withheld_claims", "evidence_grade_vocabulary", "topic_timeline_update")
_INTEGRITY_KEYS = store.INTEGRITY_STATUSES        # verified·mismatch·missing·no_local·unchecked·excluded
EXCLUDED_REASON = "sync 허용목록 밖(설계상 미러하지 않음 — 서비스 코드·대시보드 스케치 등)"
# 변경 판정에 쓰는 counts 키(두 번째 import 가 0 이어야 하는 것들)
_CHANGE_KEYS = ("downloaded", "sources_changed", "artifacts_changed", "runs_changed", "canonical_reparsed",
                "signals_changed",
                "registry_new", "role_pending_imported", "person_links", "importance_changed", "meta_changed",
                "scheduled_applied", "scheduled_failed")

# 결과에 항상 실리는 counts 키(0 포함) — 잡 원장·CLI 가 같은 모양을 본다
_COUNT_KEYS = ("listed", "rejected", "downloaded", "unchanged", "deferred", "orphaned", "fetch_failed",
               "sources_changed", "artifacts_changed", "runs_changed", "canonical_reparsed", "signals_changed",
               "registry_new",
               "registry_existing", "registry_skipped", "role_pending_imported", "person_links", "importance_changed",
               "meta_changed", "scheduled_applied", "scheduled_failed")

TreeFetcher = Callable[[str, str, "str | None"], dict]
BlobFetcher = Callable[[str, str, str, "str | None"], bytes]


class SyncError(Exception):
    def __init__(self, message: str, error_class: str = "transient"):
        super().__init__(message)
        self.error_class = error_class


# ── 환경 ─────────────────────────────────────────────────────────────────────────

def _require_auth() -> bool:
    """프로덕션(fly 볼륨 `/app/data` 존재)에서는 원격 sync 에 토큰을 강제한다."""
    return Path("/app/data").exists()


def _env_token() -> str | None:
    return os.environ.get("STAKEHOLDER_SYNC_TOKEN") or os.environ.get("GITHUB_TOKEN") or None


def _env_repo() -> str:
    return os.environ.get("STAKEHOLDER_SYNC_REPO") or DEFAULT_REPO


# ── git blob sha · HTTP ───────────────────────────────────────────────────────────

def git_blob_sha(data: bytes) -> str:
    """git 의 blob 객체 id — sha1("blob <len>\\0" + bytes). GitHub tree 항목의 sha 와 같다."""
    h = hashlib.sha1(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _request(url: str, token: str | None, accept: str) -> urllib.request.Request:
    req = urllib.request.Request(url)
    req.add_header("Accept", accept)
    req.add_header("X-GitHub-Api-Version", GITHUB_API_VERSION)
    req.add_header("User-Agent", "ma-ai-dossier-stakeholder-sync")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return req


def fetch_tree_github(repo: str, branch: str, token: str | None) -> dict:
    """GET /repos/{repo}/git/trees/{branch}?recursive=1 → JSON(dict). HTTP 오류는 그대로 올린다(분류는 호출자)."""
    url = f"{GITHUB_API}/repos/{repo}/git/trees/{urllib.parse.quote(branch, safe='')}?recursive=1"
    with urllib.request.urlopen(_request(url, token, "application/vnd.github+json"),
                                timeout=HTTP_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_blob_github(repo: str, path: str, branch: str, token: str | None) -> bytes:
    """Contents API raw — GET /repos/{repo}/contents/{path}?ref={branch} (Accept: application/vnd.github.raw)."""
    url = (f"{GITHUB_API}/repos/{repo}/contents/{urllib.parse.quote(path, safe='/')}"
           f"?ref={urllib.parse.quote(branch, safe='')}")
    with urllib.request.urlopen(_request(url, token, "application/vnd.github.raw"),
                                timeout=HTTP_TIMEOUT_SECONDS) as resp:
        return resp.read()


def _classify(exc: BaseException) -> str:
    """401/404 와 (레이트리밋이 아닌) 403 은 토큰·repo 설정 문제 → config. 그 외 네트워크·5xx → transient."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 404):
            return "config"
        if exc.code == 403:
            remaining = (exc.headers or {}).get("X-RateLimit-Remaining") if exc.headers is not None else None
            return "transient" if remaining == "0" else "config"
    return "transient"


def _http_msg(what: str, exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"{what}: HTTP {exc.code} {exc.reason}"
    return f"{what}: {type(exc).__name__}: {exc}"


# ── 목록 ─────────────────────────────────────────────────────────────────────────

def _list_local(source_dir: Path) -> tuple[list[dict], list[str]]:
    """source_dir 를 원격처럼 본다. 심볼릭 링크·숨김/환경 파일·허용목록 밖은 rejected. 파일명은 NFC 로 정규화."""
    entries: list[dict] = []
    rejected: list[str] = []
    for dirpath, dirnames, filenames in os.walk(source_dir):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_WALK_DIRS)
        for fn in sorted(filenames):
            full = Path(dirpath) / fn
            rel = unicodedata.normalize("NFC", full.relative_to(source_dir).as_posix())
            if full.is_symlink() or not full.is_file() or not paths.is_sync_allowed(rel):
                rejected.append(rel)
                continue
            data = full.read_bytes()
            if len(data) > paths.MAX_BLOB_BYTES:
                rejected.append(rel)
                continue
            entries.append({"path": rel, "sha": git_blob_sha(data), "size": len(data), "src": full})
    return entries, rejected


def _list_remote(tree: dict) -> tuple[list[dict], list[str], list[str]]:
    """GitHub tree → (entries, rejected, warnings). type=blob 만 본다(tree·commit 은 무시), 심볼릭 링크(120000)는 거부."""
    if not isinstance(tree, dict) or not isinstance(tree.get("tree"), list):
        raise SyncError("GitHub tree 응답 형식이 올바르지 않습니다.", "transient")
    entries: list[dict] = []
    rejected: list[str] = []
    warnings: list[str] = []
    if tree.get("truncated"):
        warnings.append("GitHub tree 응답이 잘렸습니다(truncated) — 일부 파일이 목록에서 빠졌을 수 있습니다.")
    for item in tree["tree"]:
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        rel, sha, size = item.get("path"), item.get("sha"), item.get("size")
        if (not isinstance(rel, str) or not isinstance(sha, str) or item.get("mode") == "120000"
                or not paths.is_sync_allowed(rel)):
            rejected.append(str(rel))
            continue
        if isinstance(size, int) and size > paths.MAX_BLOB_BYTES:
            rejected.append(rel)
            continue
        entries.append({"path": rel, "sha": sha, "size": size if isinstance(size, int) else None})
    entries.sort(key=lambda e: e["path"])
    return entries, rejected, warnings


# ── 잠금 · 쓰기 ───────────────────────────────────────────────────────────────────

@contextmanager
def _sync_lock(root: Path) -> Iterator[bool]:
    """root/.sync/lock 에 비차단 flock — 스케줄 잡과 수동 sync(T6) 가 겹치지 않게. 얻으면 True."""
    lock_dir = Path(root) / paths.SYNC_SUBDIR
    lock_dir.mkdir(parents=True, exist_ok=True)
    fh = open(lock_dir / "lock", "a+")  # noqa: SIM115 — flock 동안 열어 둔다
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                yield False
                return
            raise
        try:
            yield True
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def _atomic_write(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()


# ── 결과 ─────────────────────────────────────────────────────────────────────────

def _result(counts: Counter, errors: list[str], warnings: list[str], *, error_class: str | None = None,
            **extra: Any) -> dict:
    c = dict.fromkeys(_COUNT_KEYS, 0)
    c.update({k: int(v) for k, v in counts.items()})
    c["changes"] = sum(c.get(k, 0) for k in _CHANGE_KEYS)
    ok = not errors
    out = {"ok": ok, "counts": c, "errors": list(errors), "warnings": list(warnings),
           "error_class": None if ok else (error_class or "transient")}
    out.update(extra)
    return out


def _early_failure(msg: str, cls: str, db_path: Path, today: date | None) -> dict:
    """목록 단계 실패(토큰 부재·GitHub 오류). DB 가 이미 있으면 apply_scheduled 만 돌린다(DB 를 새로 만들지 않는다)."""
    counts: Counter = Counter()
    warnings: list[str] = []
    if Path(db_path).exists():
        try:
            ap = approval.apply_scheduled(today=today, db_path=db_path)
            counts["scheduled_applied"] += ap["applied"]
            counts["scheduled_failed"] += ap["failed"]
        except Exception as exc:  # noqa: BLE001 — 신호 경계: 잡을 죽이지 않는다
            warnings.append(f"apply_scheduled 실패: {exc}")
    res = _result(counts, [msg], warnings, error_class=cls)
    res["error"] = msg
    return res


# ── sync ────────────────────────────────────────────────────────────────────────

def sync(root: str | Path | None = None, db_path: str | Path | None = None, *, source_dir: str | Path | None = None,
         repo: str | None = None, branch: str = DEFAULT_BRANCH, token: str | None = None,
         fetch_tree: TreeFetcher | None = None, fetch_blob: BlobFetcher | None = None,
         today: date | None = None, dry_run: bool = False) -> dict:
    """미러 + import. 반환 {"ok", "counts", "errors", "warnings", "error_class"(ok 면 None), ["error"], ["dry_run"]}.

    counts: listed · rejected · downloaded · unchanged · deferred · orphaned · fetch_failed · sources_changed ·
    artifacts_changed · runs_changed · canonical_reparsed · registry_new · registry_existing · registry_skipped ·
    role_pending_imported · person_links · importance_changed · meta_changed · scheduled_applied · scheduled_failed ·
    changes(변경 합계 — 같은 스냅샷의 두 번째 실행은 0). dry_run 이면 would_download 만 세고 아무것도 쓰지 않는다."""
    root = Path(root) if root is not None else paths.stakeholder_root()
    db_path = Path(db_path) if db_path is not None else store.DEFAULT_DB_PATH
    remote = source_dir is None
    if remote:
        token = token or _env_token()
        repo = repo or _env_repo()
        if not token and _require_auth():
            return _early_failure("auth required: STAKEHOLDER_SYNC_TOKEN missing (fail-closed in production)",
                                  "config", db_path, today)

    if dry_run:
        return _dry_run(root, db_path, source_dir=source_dir, repo=repo, branch=branch, token=token,
                        fetch_tree=fetch_tree)

    with _sync_lock(root) as got:
        if not got:
            return _result(Counter(), ["stakeholder sync already running (lock held)"], [],
                           error_class="transient", error="stakeholder sync already running (lock held)")
        return _sync_locked(root, db_path, source_dir=source_dir, repo=repo, branch=branch, token=token,
                            fetch_tree=fetch_tree, fetch_blob=fetch_blob, today=today)


def _listing(source_dir, repo, branch, token, fetch_tree) -> tuple[list[dict], list[str], list[str], str | None]:
    if source_dir is not None:
        sd = Path(source_dir)
        if not sd.is_dir():
            raise SyncError(f"source_dir 가 디렉터리가 아닙니다: {sd}", "config")
        entries, rejected = _list_local(sd)
        return entries, rejected, [], None
    try:
        tree = (fetch_tree or fetch_tree_github)(repo, branch, token)
    except SyncError:
        raise
    except Exception as exc:  # noqa: BLE001 — HTTP/URL/JSON 오류를 분류해 돌려준다
        raise SyncError(_http_msg(f"GitHub tree 조회 실패({repo}@{branch})", exc), _classify(exc)) from exc
    entries, rejected, warnings = _list_remote(tree)
    return entries, rejected, warnings, tree.get("sha") if isinstance(tree.get("sha"), str) else None


def _needs_fetch(entry: dict, index: dict, root: Path) -> bool:
    idx = index.get(entry["path"])
    if not idx or idx.get("blob_sha") != entry["sha"]:
        return True
    try:
        dest = paths.resolve_under(root, entry["path"])
    except ValueError:
        return True
    if not dest.is_file():
        return True
    if idx.get("bytes") is not None and dest.stat().st_size != idx["bytes"]:
        return True
    # 볼륨 쪽 변조·손상 자가 치유: 받은 당시 sha256 과 디스크가 다르면 다시 받는다(원격 sha 는 그대로여도)
    return bool(idx.get("sha256")) and _sha256_file(dest) != idx["sha256"]


def _dry_run(root, db_path, *, source_dir, repo, branch, token, fetch_tree) -> dict:
    counts: Counter = Counter()
    try:
        entries, rejected, warnings, tree_sha = _listing(source_dir, repo, branch, token, fetch_tree)
    except SyncError as exc:
        res = _result(counts, [str(exc)], [], error_class=exc.error_class, dry_run=True)
        res["error"] = str(exc)
        return res
    index = store.get_sync_index(db_path=db_path) if Path(db_path).exists() else {}
    counts["listed"], counts["rejected"] = len(entries), len(rejected)
    counts["would_download"] = sum(1 for e in entries if _needs_fetch(e, index, root))
    listed = {e["path"] for e in entries}
    counts["orphaned"] = sum(1 for p in index if p not in listed)
    res = _result(counts, [], warnings, dry_run=True, tree_sha=tree_sha)
    res["counts"]["changes"] = 0
    return res


def _sync_locked(root: Path, db_path: Path, *, source_dir, repo, branch, token, fetch_tree, fetch_blob,
                 today) -> dict:
    counts: Counter = Counter()
    errors: list[str] = []
    try:
        entries, rejected, warnings, tree_sha = _listing(source_dir, repo, branch, token, fetch_tree)
    except SyncError as exc:
        return _early_failure(str(exc), exc.error_class, db_path, today)
    counts["listed"], counts["rejected"] = len(entries), len(rejected)
    if rejected:
        logger.info("stakeholder sync: 허용목록 밖 %d건 제외 (예: %s)", len(rejected), rejected[:5])

    index = store.get_sync_index(db_path=db_path)
    listed = {e["path"] for e in entries}
    counts["orphaned"] = sum(1 for p in index if p not in listed)   # 원격에서 사라짐 — 볼륨·색인은 보존

    fetched: list[tuple[str, str, str, int]] = []
    total = 0
    blob = fetch_blob or fetch_blob_github
    for e in entries:
        if not _needs_fetch(e, index, root):
            counts["unchanged"] += 1
            continue
        if total + (e.get("size") or 0) > MAX_SYNC_BYTES:
            counts["deferred"] += 1
            continue
        try:
            dest = paths.resolve_under(root, e["path"])
            data = e["src"].read_bytes() if "src" in e else blob(repo, e["path"], branch, token)
        except ValueError as exc:
            counts["fetch_failed"] += 1
            errors.append(f"{e['path']}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            counts["fetch_failed"] += 1
            errors.append(_http_msg(f"{e['path']} 받기 실패", exc))
            continue
        if total + len(data) > MAX_SYNC_BYTES:                         # 목록 size 가 없거나 틀린 경우
            counts["deferred"] += 1
            continue
        got = git_blob_sha(data)
        if got != e["sha"]:
            counts["fetch_failed"] += 1
            errors.append(f"{e['path']}: blob sha 불일치(목록 {e['sha'][:12]} ≠ 받은 바이트 {got[:12]}) — 쓰지 않음")
            continue
        try:
            _atomic_write(dest, data)
        except OSError as exc:
            counts["fetch_failed"] += 1
            errors.append(f"{e['path']}: 쓰기 실패: {exc}")
            continue
        fetched.append((e["path"], e["sha"], _sha256(data), len(data)))
        total += len(data)
        counts["downloaded"] += 1

    if fetched:
        with store.transaction(db_path) as c:
            for rel, sha, sha256, size in fetched:
                store.upsert_sync_file(rel, sha, sha256=sha256, bytes=size, conn=c)
    if counts["deferred"]:
        warnings.append(f"총량 상한 {MAX_SYNC_BYTES} bytes 초과 — {counts['deferred']}건은 다음 실행에서 받습니다.")

    imp = import_snapshot(root, db_path, today=today, tree_sha=tree_sha)
    counts.update(imp["counts"])
    errors += imp["errors"]
    warnings += imp["warnings"]
    res = _result(counts, errors, warnings, error_class=imp.get("error_class") or "transient",
                  tree_sha=tree_sha, rejected_paths=rejected[:50])
    try:
        with store.transaction(db_path) as c:
            store.set_meta("last_sync_result_json", {"ok": res["ok"], "counts": res["counts"],
                                                     "errors": res["errors"][:20], "tree_sha": tree_sha,
                                                     "mode": "source_dir" if source_dir is not None else "remote"},
                           conn=c)
    except Exception as exc:  # noqa: BLE001 — 기록 실패로 sync 결과를 바꾸지 않는다
        logger.warning("last_sync_result 기록 실패: %s", exc)
    return res


# ── import_snapshot ─────────────────────────────────────────────────────────────

def _set_meta_if_changed(conn, key: str, value: Any) -> bool:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if store.get_meta(key, conn=conn) == text:
        return False
    store.set_meta(key, text, conn=conn)
    return True


def _rel_file(root: Path, rel: Any) -> tuple[Path | None, str | None]:
    """manifest 경로 → (실제 파일, 오류사유). 안전하지 않은 경로는 파일 접근 키로 쓰지 않는다."""
    if not isinstance(rel, str) or not rel:
        return None, "경로 없음"
    try:
        p = paths.resolve_under(root, rel)
    except ValueError:
        return None, f"안전하지 않은 경로: {rel!r}"
    return (p, None) if p.is_file() else (None, f"파일 없음: {rel}")


def _source_integrity(root: Path, src: dict) -> tuple[str, list[str]]:
    """verified | mismatch | missing | no_local | unchecked | excluded (+사유). local_path↔sha256,
    text_path↔text_sha256, bytes(있으면 local_path 크기). 하나라도 없으면 missing, 하나라도 다르면 mismatch(missing 이
    우선). sync 허용목록 밖 경로는 대조하지 않는다 — 대조할 경로가 하나도 남지 않으면 excluded(실패 아님)."""
    pairs = [(k, h) for k, h in (("local_path", "sha256"), ("text_path", "text_sha256")) if src.get(k)]
    if not pairs:
        return "no_local", []
    outside = [k for k, _ in pairs if not paths.is_sync_allowed(str(src.get(k)))]
    pairs = [(k, h) for k, h in pairs if k not in outside]
    if not pairs:
        return "excluded", [f"{EXCLUDED_REASON}: {src[k]}" for k in outside]
    problems: list[str] = []
    missing = mismatch = False
    compared = 0
    for path_key, hash_key in pairs:
        f, why = _rel_file(root, src.get(path_key))
        if f is None:
            missing = True
            problems.append(why or "")
            continue
        expected = src.get(hash_key)
        if expected:
            compared += 1
            if _sha256_file(f) != expected:
                mismatch = True
                problems.append(f"SHA-256 불일치: {src[path_key]}")
        if path_key == "local_path" and isinstance(src.get("bytes"), int) and f.stat().st_size != src["bytes"]:
            mismatch = True
            problems.append(f"크기 불일치: {src[path_key]}")
    if missing:
        return "missing", problems
    if mismatch:
        return "mismatch", problems
    return ("verified" if compared else "unchecked"), problems


def _artifact_integrity(root: Path, a: dict) -> tuple[str, list[str]]:
    """verified | mismatch | missing | unchecked | excluded. manifest 가 등재했지만 sync 허용목록 밖인 산출물
    (stakeholder_service.py, dashboard-sketches/…)은 볼륨에 없는 게 정상이다 — missing 이 아니라 excluded."""
    if isinstance(a.get("path"), str) and paths.is_safe_rel_path(a["path"]) and not paths.is_sync_allowed(a["path"]):
        return "excluded", [f"{EXCLUDED_REASON}: {a['path']}"]
    f, why = _rel_file(root, a.get("path"))
    if f is None:
        return "missing", [why or ""]
    problems = []
    if a.get("sha256") and _sha256_file(f) != a["sha256"]:
        problems.append(f"SHA-256 불일치: {a['path']}")
    if isinstance(a.get("bytes"), int) and f.stat().st_size != a["bytes"]:
        problems.append(f"크기 불일치: {a['path']}")
    if problems:
        return "mismatch", problems
    return ("verified" if a.get("sha256") or isinstance(a.get("bytes"), int) else "unchecked"), []


def _copyright_notice(root: Path) -> str:
    readme = root / "README.md"
    try:
        for line in readme.read_text(encoding="utf-8").splitlines():
            if "저작권" in line:
                return line.strip().lstrip("-*• ").strip()
    except (OSError, UnicodeDecodeError):
        pass
    return COPYRIGHT_FALLBACK


def _import_manifest(root: Path, db_path: Path, ts: str, counts: Counter, errors: list[str],
                     warnings: list[str]) -> dict:
    """source_manifest.json → sv_source · sv_artifact · sv_meta. 반환: 무결성 요약."""
    summary: dict[str, Any] = {"sources": dict.fromkeys(_INTEGRITY_KEYS, 0),
                               "artifacts": dict.fromkeys(_INTEGRITY_KEYS, 0), "problems": [], "excluded": [],
                               "checked_at": ts}
    mpath = root / "source_manifest.json"
    if not mpath.is_file():
        errors.append("source_manifest.json 없음 — manifest·무결성 적재를 건너뜀")
        return summary
    raw = mpath.read_bytes()
    try:
        m = json.loads(raw.decode("utf-8"))
        if not isinstance(m, dict):
            raise ValueError("최상위가 객체가 아님")
    except ValueError as exc:
        errors.append(f"source_manifest.json 파싱 실패: {exc}")
        return summary
    msha = _sha256(raw)
    sources = m.get("sources") if isinstance(m.get("sources"), list) else []
    artifacts = m.get("artifacts") if isinstance(m.get("artifacts"), list) else []
    with store.transaction(db_path) as c:
        for s in sources:
            if not isinstance(s, dict) or not s.get("source_id"):
                warnings.append(f"manifest source 항목 무시(source_id 없음): {str(s)[:80]}")
                continue
            status, problems = _source_integrity(root, s)
            summary["sources"][status] += 1
            if status == "excluded":
                summary["excluded"].append({"kind": "source", "id": s["source_id"], "reason": problems})
            elif problems:
                summary["problems"].append({"kind": "source", "id": s["source_id"], "status": status,
                                            "detail": problems})
            if store.upsert_source(s, integrity_status=status, integrity_checked_at=ts, manifest_sha256=msha,
                                   conn=c) != "unchanged":
                counts["sources_changed"] += 1
        for a in artifacts:
            if not isinstance(a, dict) or not a.get("path"):
                warnings.append(f"manifest artifact 항목 무시(path 없음): {str(a)[:80]}")
                continue
            status, problems = _artifact_integrity(root, a)
            summary["artifacts"][status] += 1
            if status == "excluded":
                summary["excluded"].append({"kind": "artifact", "id": a["path"], "reason": problems})
            elif problems:
                summary["problems"].append({"kind": "artifact", "id": a["path"], "status": status,
                                            "detail": problems})
            if store.upsert_artifact(a, integrity_status=status, integrity_checked_at=ts, conn=c) != "unchanged":
                counts["artifacts_changed"] += 1
        meta: dict[str, Any] = {"manifest_sha256": msha, "copyright_notice": _copyright_notice(root)}
        for k in _MANIFEST_META_SCALARS:
            if m.get(k) is not None:
                meta[k] = str(m[k])
        for k in _MANIFEST_META_JSON:
            if k in m:
                meta[f"{k}_json"] = m[k]
        extra = {k: v for k, v in m.items()
                 if k not in ("sources", "artifacts") + _MANIFEST_META_SCALARS + _MANIFEST_META_JSON}
        meta["manifest_extra_json"] = extra                        # 미지 최상위 키도 손실 없이
        for k, v in meta.items():
            if _set_meta_if_changed(c, k, v):
                counts["meta_changed"] += 1
    bad = summary["sources"]["mismatch"] + summary["sources"]["missing"]
    if bad:
        warnings.append(f"원문 무결성 문제 {bad}건(mismatch {summary['sources']['mismatch']}, "
                        f"missing {summary['sources']['missing']}) — integrity_summary_json 참조")
    return {**summary, "_artifacts_manifest": artifacts}


def _import_runs(root: Path, db_path: Path, counts: Counter, errors: list[str]) -> None:
    """monitoring-runs/*/daily_monitoring.json · audit-runs/*/coverage_audit.json → 런 테이블(payload sha 게이트)."""
    specs = (("monitoring-runs", "daily_monitoring", store.upsert_monitoring_run),
             ("audit-runs", "coverage_audit", store.upsert_audit_run))
    with store.transaction(db_path) as c:
        for subdir, stem, upsert in specs:
            base = root / subdir
            if not base.is_dir():
                continue
            for d in sorted(p for p in base.iterdir() if p.is_dir()):
                f = d / f"{stem}.json"
                if not f.is_file():
                    continue
                md = d / f"{stem}.md"
                raw_dir = d / "raw"
                try:
                    r = upsert(f.read_bytes(), md_path=f"{subdir}/{d.name}/{stem}.md" if md.is_file() else None,
                               raw_dir=f"{subdir}/{d.name}/raw" if raw_dir.is_dir() else None, conn=c)
                except (store.StoreError, ValueError, OSError) as exc:
                    errors.append(f"{subdir}/{d.name}/{stem}.json 적재 실패: {getattr(exc, 'message', exc)}")
                    continue
                if r != "unchanged":
                    counts["runs_changed"] += 1


def _load_registry(root: Path, rel: str, key: str, errors: list[str], warnings: list[str]) -> list[dict]:
    f = root / rel
    if not f.is_file():
        warnings.append(f"{rel} 없음 — 건너뜀")
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        errors.append(f"{rel} 파싱 실패: {exc}")
        return []
    items = data.get(key) if isinstance(data, dict) else None
    if not isinstance(items, list):
        errors.append(f"{rel}: '{key}' 목록 없음")
        return []
    return [x for x in items if isinstance(x, dict)]


def _import_pending_role(c, person_id: str, item: Any, counts: Counter, warnings: list[str]) -> None:
    """소스 pending_role_changes 항목 → sv_person_role_history(scheduled, proposal_id 결합). 결합할 제안이 DB 에
    scheduled 로 있어야만 넣는다(아니면 대기 행이 그 인물의 이후 직책 변경을 영구히 막는다). (proposal_id) 기준 멱등."""
    if not isinstance(item, dict):
        return
    pid = item.get("proposal_id")
    where = f"pending_role_changes({person_id}, {pid})"
    if (item.get("status") or "scheduled") != "scheduled":
        warnings.append(f"{where}: status={item.get('status')!r} — scheduled 만 가져옵니다")
        return
    if not pid:
        warnings.append(f"{where}: proposal_id 없음 — 건너뜀")
        return
    if c.execute("SELECT 1 FROM sv_person_role_history WHERE proposal_id=?", (pid,)).fetchone():
        return                                                     # 이미 있음(이전 import 또는 대쉬보드 승인)
    proposal = store.get_proposal(pid, conn=c)
    if proposal is None or proposal["status"] != "scheduled":
        warnings.append(f"{where}: 결합할 scheduled 제안이 DB 에 없음"
                        f"({proposal['status'] if proposal else '없음'}) — 건너뜀")
        return
    person = store.get_person(person_id, conn=c)
    if person is None or not item.get("role"):
        warnings.append(f"{where}: 인물 행 또는 직책 없음 — 건너뜀")
        return
    store.insert_role_history_row(c, {
        "person_id": person_id, "organization": item.get("organization") or person["organization"],
        "department": item.get("department") or person["department"], "role": item["role"],
        "effective_from": item.get("effective_from") or proposal.get("effective_from"), "effective_to": None,
        "status": "scheduled", "proposal_id": pid, "evidence": item.get("evidence") or [],
        "approved_at": item.get("approved_at") or proposal.get("reviewed_at"), "applied_at": None})
    counts["role_pending_imported"] += 1


def _import_registry(root: Path, db_path: Path, counts: Counter, errors: list[str], warnings: list[str]) -> None:
    """data/*.json → insert-if-absent(origin='import'). 기존 행은 어떤 필드도 갱신하지 않는다(운영자 결정 보존)."""
    domains = _load_registry(root, "data/source_registry.json", "sources", errors, warnings)
    proposals = _load_registry(root, "data/audit_proposals.json", "proposals", errors, warnings)
    people = _load_registry(root, "data/people_registry.json", "people", errors, warnings)
    with store.transaction(db_path) as c:
        for s in domains:
            try:
                new = store.insert_source_domain_if_absent(s, origin="import", conn=c)
            except store.StoreError as exc:
                errors.append(f"source_registry {s.get('source_id')}: {exc.message}")
                continue
            counts["registry_new" if new else "registry_existing"] += 1
        for p in proposals:
            try:
                new = store.insert_proposal_if_absent(p, origin="import", conn=c)
            except store.ConflictError as exc:
                counts["registry_skipped"] += 1
                warnings.append(f"audit_proposals {p.get('proposal_id')}: {exc.message} — import 건너뜀")
                continue
            except store.StoreError as exc:
                errors.append(f"audit_proposals {p.get('proposal_id')}: {exc.message}")
                continue
            if new:
                store.add_proposal_event_row(c, p["proposal_id"], actor=SYNC_ACTOR, event="imported",
                                             detail=f"origin=import status={p.get('status', 'needs_review')}")
            counts["registry_new" if new else "registry_existing"] += 1
        for p in people:
            try:
                new = store.insert_person_if_absent(p, origin="import", conn=c)
            except store.StoreError as exc:
                errors.append(f"people_registry {p.get('person_id')}: {exc.message}")
                continue
            counts["registry_new" if new else "registry_existing"] += 1
            for item in p.get("pending_role_changes") or []:
                _import_pending_role(c, p["person_id"], item, counts, warnings)


# ── 정책 시그널 ──────────────────────────────────────────────────────────────────

def _signals_status(c, state: str, error: str | None, file_sha: str | None, counts: Counter) -> None:
    if _set_meta_if_changed(c, "policy_signals_status_json", {"state": state, "error": error, "file_sha256": file_sha}):
        counts["meta_changed"] += 1


def _import_signals(root: Path, db_path: Path, counts: Counter, errors: list[str], warnings: list[str]) -> None:
    """data/policy_signals.json → sv_policy_signal·sv_signal_evidence. 외부 에이전트 소유 — 읽기 전용 미러:
    검증 통과 시 내용이 다를 때만 전체 교체(signals_changed), 매 실행 근거 locator 를 다시 잰다(출처가 바뀌면 판정도).
    검증 실패(JSON·스키마·signal_id 중복)는 errors 에 남기고 **이전 정상 행을 그대로 둔다**(대쉬보드는 마지막 정상본 표시,
    import_status.state='invalid'). 파일이 없으면(구 스냅샷) 아무 행도 만들지 않는다."""
    f = root / signals.SIGNALS_REL
    with store.transaction(db_path) as c:
        if not f.is_file():
            _signals_status(c, "absent", None, None, counts)
            return
        raw = f.read_bytes()
        sha = _sha256(raw)
        try:
            data = json.loads(raw.decode("utf-8"))
            validated = signals.validate_payload(data)
        except (ValueError, signals.SignalSchemaError) as exc:   # UnicodeDecodeError·JSONDecodeError ⊂ ValueError
            kept = c.execute("SELECT COUNT(*) FROM sv_policy_signal").fetchone()[0]
            msg = f"{exc}"[:500]
            errors.append(f"{signals.SIGNALS_REL} 검증 실패 — 이전 정상 시그널 {kept}건 유지(외부 에이전트가 파일을 고쳐 "
                          f"커밋하면 다음 sync 가 교체): {msg}")
            _signals_status(c, "invalid", msg, sha, counts)
            return
        sources = {r["source_id"]: dict(r) for r in c.execute("SELECT source_id, text_path FROM sv_source")}
        sig_rows, ev_rows, warns = signals.build_rows(data, validated, root, sources)
        warnings.extend(warns)
        counts["signals_changed"] += store.replace_policy_signals(sig_rows, ev_rows, c)
        for k, v in (("policy_signals_sha256", sha), ("policy_signals_schema_version", validated.schema_version),
                     ("policy_signals_updated_at", validated.updated_at)):
            if _set_meta_if_changed(c, k, v):
                counts["meta_changed"] += 1
        _signals_status(c, "ok", None, sha, counts)
    counts["signals"] = len(sig_rows)
    counts["signal_evidence"] = len(ev_rows)
    off = [e for e in ev_rows if e["locator_match"] != "range"]
    if off:
        warnings.append(f"정책 시그널 근거 locator 범위 불일치 {len(off)}건: "
                        + "; ".join(f"{e['signal_id']}[{e['idx']}] {e['locator_match']}" for e in off[:5]))


# ── 정본 · 결합 ───────────────────────────────────────────────────────────────────

def _person_resolver(c) -> Callable[[str | None], str | None]:
    """발언자 이름 → person_id. 이름 정확 일치가 유일하면 그 행, 없으면 별칭 정확 일치가 유일할 때만. 모호하면 None."""
    by_name: dict[str, set[str]] = {}
    by_alias: dict[str, set[str]] = {}
    for r in c.execute("SELECT person_id, name, aliases_json FROM sv_person"):
        by_name.setdefault(r["name"], set()).add(r["person_id"])
        try:
            aliases = json.loads(r["aliases_json"] or "[]")
        except ValueError:
            aliases = []
        for a in aliases if isinstance(aliases, list) else []:
            if isinstance(a, str) and a.strip():
                by_alias.setdefault(a.strip(), set()).add(r["person_id"])

    def resolve(name: str | None) -> str | None:
        if not name:
            return None
        name = name.strip()
        for table in (by_name, by_alias):
            ids = table.get(name)
            if ids:
                return next(iter(ids)) if len(ids) == 1 else None
        return None
    return resolve


def _source_resolver(c) -> dict[str, str]:
    """manifest text_path(우선)·local_path → source_id."""
    out: dict[str, str] = {}
    rows = list(c.execute("SELECT source_id, text_path, local_path FROM sv_source ORDER BY source_id"))
    for col in ("local_path", "text_path"):                         # text_path 가 나중에 덮어써 우선
        for r in rows:
            if r[col]:
                out[r[col]] = r["source_id"]
    return out


def _uniq(xs) -> list:
    out: list = []
    for x in xs:
        if x and x not in out:
            out.append(x)
    return out


def _canonical_rows(res: Any, person_of, source_of: dict[str, str]) -> tuple[list[dict], list[dict], list[dict]]:
    events, statements, quotes = [], [], []
    for ev in res.events:
        d = asdict(ev)
        d["source_ids"] = _uniq(source_of.get(p) for p in ev.source_paths)
        events.append(d)
    for st in res.statements:
        d = asdict(st)
        d["person_id"] = None if st.multi_speaker else person_of(st.speaker_name)
        d["source_ids"] = _uniq(source_of.get(loc.get("path")) for loc in st.locators)
        statements.append(d)
    for q in res.quotes:
        d = asdict(q)
        d["person_id"] = person_of(q.speaker_name)
        ref = q.locator_ref or (q.locators[0] if q.locators else None)
        d["source_id"] = source_of.get(ref.get("path")) if isinstance(ref, dict) else None
        quotes.append(d)
    return events, statements, quotes


def _find_canonical(root: Path, artifacts: list) -> Path | None:
    for a in artifacts or []:
        if isinstance(a, dict) and a.get("name") == CANONICAL_TIMELINE_ARTIFACT:
            f, _ = _rel_file(root, a.get("path"))
            if f is not None:
                return f
    cands = sorted(root.glob(CANONICAL_TIMELINE_GLOB))
    return cands[-1] if cands else None


def _import_canonical(root: Path, db_path: Path, artifacts: list, counts: Counter, errors: list[str],
                      warnings: list[str]) -> None:
    """정본 타임라인 MD — sha 가 sv_meta['canonical_timeline_sha256'] 와 다를 때만 파싱 → 한 트랜잭션에서
    origin='canonical_md' 행 교체. 파서·적재 실패는 errors 에 남기고 기존 행·sha 를 유지(다음 실행이 다시 시도)."""
    md = _find_canonical(root, artifacts)
    if md is None:
        warnings.append("정본 타임라인 MD 없음 — 정본 적재를 건너뜀")
        return
    raw = md.read_bytes()
    sha = _sha256(raw)
    if store.get_meta("canonical_timeline_sha256", db_path=db_path) == sha:
        return
    rel = md.relative_to(root).as_posix()
    try:
        res = canonical_parse.parse_timeline(md, root=root)
    except Exception as exc:  # noqa: BLE001 — 파서 예외는 오류로 집계, 기존 행 유지
        errors.append(f"정본 파싱 실패({rel}): {exc}")
        return
    try:
        with store.transaction(db_path) as c:
            events, statements, quotes = _canonical_rows(res, _person_resolver(c), _source_resolver(c))
            n = store.replace_canonical(events, statements, quotes, sha, c)
            store.set_meta("canonical_timeline_sha256", sha, conn=c)
            store.set_meta("canonical_timeline_path", rel, conn=c)
            store.set_meta("canonical_parse_warnings_json", res.warnings, conn=c)
            store.set_meta("canonical_stats_json", {
                **n, "raw_quote_blocks": res.raw_quote_blocks,
                "locator_match": dict(Counter(q.locator_match for q in res.quotes)),
                "excluded_blockquotes": len(res.excluded_blockquotes), "topics": res.meta.get("topics")}, conn=c)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"정본 적재 실패({rel}): {exc}")
        return
    counts["canonical_reparsed"] += 1
    counts["canonical_events"] = n["events"]
    counts["canonical_statements"] = n["statements"]
    counts["canonical_quotes"] = n["quotes"]
    if res.warnings:
        warnings.append(f"정본 파서 경고 {len(res.warnings)}건 — sv_meta.canonical_parse_warnings_json 참조")


def _relink_and_importance(root: Path, db_path: Path, counts: Counter, warnings: list[str]) -> None:
    """매 실행: ① 정본 행의 person_id 재결합(정본 sha 와 무관 — 나중에 등록·승인된 인물에도 붙는다, 바뀐 행만 UPDATE)
    ② 부서-인물지도 §2.2 중요도 → sv_person.importance_*(sync 소유 두 컬럼, 값이 다를 때만)."""
    table: dict[str, dict] = {}
    maps = sorted(root.glob(PEOPLE_MAP_GLOB))
    if maps:
        imp_warnings: list[str] = []
        try:
            table = canonical_parse.parse_importance_table(maps[-1], warnings=imp_warnings)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"중요도표 파싱 실패({maps[-1].name}): {exc}")
        if imp_warnings:
            warnings.append(f"중요도표 경고 {len(imp_warnings)}건: {'; '.join(imp_warnings[:3])}")
    with store.transaction(db_path) as c:
        person_of = _person_resolver(c)
        for tbl, key in (("sv_statement", "statement_id"), ("sv_quote", "quote_id")):
            multi = ", multi_speaker" if tbl == "sv_statement" else ""
            for r in list(c.execute(f"SELECT {key}, speaker_name, person_id{multi} FROM {tbl} "
                                    f"WHERE origin='canonical_md'")):
                want = None if (multi and r["multi_speaker"]) else person_of(r["speaker_name"])
                if want != r["person_id"]:
                    c.execute(f"UPDATE {tbl} SET person_id=? WHERE {key}=?", (want, r[key]))
                    counts["person_links"] += 1
        for name, entry in table.items():
            want = json.loads(json.dumps(entry, ensure_ascii=False))
            stale = False
            for r in c.execute("SELECT importance_score, importance_json FROM sv_person WHERE name=?", (name,)):
                try:
                    cur = json.loads(r["importance_json"]) if r["importance_json"] else None
                except ValueError:
                    cur = None
                stale = stale or r["importance_score"] != entry.get("importance") or cur != want
            if stale:
                counts["importance_changed"] += store.set_person_importance(name, entry.get("importance"), entry,
                                                                            conn=c)


def import_snapshot(root: str | Path, db_path: str | Path, *, today: date | None = None,
                    tree_sha: str | None = None) -> dict:
    """볼륨 root(소스 repo 와 같은 상대경로)를 sv_* 에 멱등 적재. 반환 {"ok","counts","errors","warnings","error_class"}.
    순서: manifest·무결성 → 런 → 레지스트리(insert-if-absent) → 정책 시그널(검증·교체) → 정본(sha 게이트) → 인물 결합·중요도 →
    apply_scheduled(today) → 메타(last_sync_at·last_tree_sha·integrity_summary_json)."""
    root, db_path = Path(root), Path(db_path)
    counts: Counter = Counter()
    errors: list[str] = []
    warnings: list[str] = []
    ts = store._ts()
    store.ensure_tables(db_path)

    summary: dict[str, Any] = {}

    def manifest() -> None:
        summary.update(_import_manifest(root, db_path, ts, counts, errors, warnings))

    stages: list[tuple[str, Callable[[], Any]]] = [
              ("manifest", manifest),
              ("runs", lambda: _import_runs(root, db_path, counts, errors)),
              ("registry", lambda: _import_registry(root, db_path, counts, errors, warnings)),
              ("signals", lambda: _import_signals(root, db_path, counts, errors, warnings)),
              ("canonical", lambda: _import_canonical(root, db_path, summary.get("_artifacts_manifest") or [],
                                                      counts, errors, warnings)),
              ("relink", lambda: _relink_and_importance(root, db_path, counts, warnings))]
    for name, fn in stages:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — 한 단계 실패가 나머지(특히 apply_scheduled)를 막지 않는다
            logger.exception("stakeholder import 단계 실패: %s", name)
            errors.append(f"{name} 단계 실패: {type(exc).__name__}: {exc}")

    try:
        ap = approval.apply_scheduled(today=today, db_path=db_path)
        counts["scheduled_applied"] += ap["applied"]
        counts["scheduled_failed"] += ap["failed"]
        if ap["failed"]:
            warnings.append(f"직책·출처 반영 실패 {ap['failed']}건: {ap['failed_ids']} (application_failed)")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"apply_scheduled 실패: {exc}")

    summary.pop("_artifacts_manifest", None)
    try:
        with store.transaction(db_path) as c:
            store.set_meta("last_sync_at", ts, conn=c)
            if tree_sha:
                store.set_meta("last_tree_sha", tree_sha, conn=c)
            if summary:
                store.set_meta("integrity_summary_json", summary, conn=c)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"메타 기록 실패: {exc}")
    ok = not errors
    return {"ok": ok, "counts": dict(counts), "errors": errors, "warnings": warnings,
            "error_class": None if ok else "transient", "integrity": summary}


# ── 편의 진입점 (T8 잡 · T6 수동 sync) ──────────────────────────────────────────────

def sync_now(*, root: str | Path | None = None, db_path: str | Path | None = None, today: date | None = None,
             dry_run: bool = False) -> dict:
    """원격 모드(환경의 STAKEHOLDER_SYNC_REPO/TOKEN) — 스케줄 잡과 수동 sync 엔드포인트가 같은 경로를 탄다."""
    return sync(root, db_path, today=today, dry_run=dry_run)


# ── CLI ───────────────────────────────────────────────────────────────────────────

def _fmt(res: dict, db_path: Path) -> list[str]:
    c = res.get("counts", {})
    keys = ("listed", "downloaded", "unchanged", "rejected", "deferred", "orphaned", "fetch_failed",
            "canonical_reparsed", "registry_new", "registry_existing", "registry_skipped", "role_pending_imported",
            "sources_changed", "artifacts_changed", "runs_changed", "signals_changed", "person_links",
            "importance_changed", "scheduled_applied", "scheduled_failed", "changes")
    if res.get("dry_run"):
        keys = ("listed", "would_download", "rejected", "orphaned")
    lines = [f"stakeholder sync: ok={res.get('ok')} " + " ".join(f"{k}={c.get(k, 0)}" for k in keys)]
    if res.get("dry_run") or not Path(db_path).exists():
        return lines
    try:
        integ = json.loads(store.get_meta("integrity_summary_json", db_path=db_path) or "{}")
        s, a = integ.get("sources", {}), integ.get("artifacts", {})
        lines.append("integrity: " + " ".join(f"{k}={s.get(k, 0)}" for k in ("verified", "mismatch", "missing",
                                                                             "no_local", "unchecked", "excluded"))
                     + " | artifacts " + " ".join(f"{k}={a.get(k, 0)}" for k in ("verified", "mismatch", "missing",
                                                                                   "excluded")))
        quotes = store.list_quotes(db_path=db_path)
        lm = Counter(q["locator_match"] for q in quotes)
        tc = store.counts(db_path=db_path)
        lines.append(f"canonical: events={tc['sv_event']} statements={tc['sv_statement']} quotes={tc['sv_quote']} "
                     + " ".join(f"{k}={lm.get(k, 0)}" for k in ("range", "file", "none", "unchecked")))
        sig = store.list_policy_signals(db_path=db_path)
        slm = Counter(e["locator_match"] for x in sig for e in x["evidence"])
        st = json.loads(store.get_meta("policy_signals_status_json", db_path=db_path) or "{}")
        lines.append(f"signals: state={st.get('state', '-')} signals={len(sig)} "
                     f"evidence={sum(len(x['evidence']) for x in sig)} "
                     + " ".join(f"{k}={slm.get(k, 0)}" for k in ("range", "file", "none", "unchecked")))
        lines.append("db: " + " ".join(f"{k}={v}" for k, v in tc.items()))
    except Exception as exc:  # noqa: BLE001
        lines.append(f"(요약 조회 실패: {exc})")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.stakeholder.sync",
                                 description="Stakeholder 소스 repo → 볼륨 미러 + sv_* import")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--source-dir", help="로컬 디렉터리(추출본·체크아웃)를 원격으로 간주해 1회 import")
    mode.add_argument("--now", action="store_true", help="원격(GitHub) sync — 스케줄 잡과 같은 경로")
    ap.add_argument("--dry-run", action="store_true", help="목록·변경 수만 출력(파일·DB 쓰기 없음)")
    ap.add_argument("--root", help="볼륨 root (기본: STAKEHOLDER_ROOT 또는 data/stakeholder)")
    ap.add_argument("--db", help="DB 경로 (기본: data/db/drug_prices.db)")
    ap.add_argument("--repo", help=f"원격 repo (기본: STAKEHOLDER_SYNC_REPO 또는 {DEFAULT_REPO})")
    ap.add_argument("--branch", default=DEFAULT_BRANCH)
    ap.add_argument("--today", help="apply_scheduled 기준일 YYYY-MM-DD (기본: 오늘 KST)")
    ap.add_argument("--verbose", "-v", action="store_true", help="경고 전부 출력")
    args = ap.parse_args(argv)
    today = date.fromisoformat(args.today) if args.today else None
    db_path = Path(args.db) if args.db else store.DEFAULT_DB_PATH
    res = sync(args.root, db_path, source_dir=args.source_dir, repo=args.repo, branch=args.branch, today=today,
               dry_run=args.dry_run)
    for line in _fmt(res, db_path):
        print(line)
    for e in res.get("errors", []):
        print(f"ERROR: {e}")
    warns = res.get("warnings", [])
    for w in warns if args.verbose else warns[:10]:
        print(f"warning: {w}")
    if not args.verbose and len(warns) > 10:
        print(f"... 경고 {len(warns) - 10}건 더 (--verbose)")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
