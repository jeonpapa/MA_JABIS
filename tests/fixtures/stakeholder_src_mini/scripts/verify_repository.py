#!/usr/bin/env python3
"""Validate JSON stores and SHA-256 provenance paths in source_manifest.json."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    failures: list[str] = []
    json_count = 0
    for path in ROOT.rglob("*.json"):
        if any(part in {".git", ".venv", "venv"} for part in path.parts):
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
            json_count += 1
        except Exception as exc:
            failures.append(f"invalid JSON: {path.relative_to(ROOT)}: {exc}")

    manifest = json.loads((ROOT / "source_manifest.json").read_text(encoding="utf-8"))
    hash_count = 0
    for source in manifest.get("sources", []):
        for path_key, hash_key in (("local_path", "sha256"), ("text_path", "text_sha256")):
            rel = source.get(path_key)
            expected = source.get(hash_key)
            if not rel:
                continue
            path = ROOT / rel
            if not path.is_file():
                failures.append(f"missing manifest file: {rel}")
                continue
            if expected:
                actual = sha256(path)
                hash_count += 1
                if actual != expected:
                    failures.append(f"SHA-256 mismatch: {rel}: expected {expected}, got {actual}")

    for artifact in manifest.get("artifacts", []):
        rel = artifact.get("path")
        if not rel:
            failures.append("artifact entry missing path")
            continue
        path = ROOT / rel
        if not path.is_file():
            failures.append(f"missing artifact file: {rel}")
            continue
        expected_hash = artifact.get("sha256")
        if expected_hash:
            actual_hash = sha256(path)
            hash_count += 1
            if actual_hash != expected_hash:
                failures.append(
                    f"artifact SHA-256 mismatch: {rel}: expected {expected_hash}, got {actual_hash}"
                )
        expected_bytes = artifact.get("bytes")
        if expected_bytes is not None:
            actual_bytes = path.stat().st_size
            if actual_bytes != expected_bytes:
                failures.append(
                    f"artifact byte-size mismatch: {rel}: expected {expected_bytes}, got {actual_bytes}"
                )

    if failures:
        print("VALIDATION FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(f"VALIDATION OK: {json_count} JSON files parsed; {hash_count} manifest hashes matched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
