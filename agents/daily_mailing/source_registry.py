from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
import ast
import re


@dataclass(frozen=True)
class SourceInfo:
    name: str
    domains: tuple[str, ...]
    source_tier: str
    tier: str | None = None
    country: str | None = None
    language: str | None = None
    weight: float = 1.0
    volume: int | None = None
    novelty: int | None = None
    ma_depth: int | None = None
    desc: str = ""
    use_for: tuple[str, ...] = field(default_factory=tuple)


_INLINE_LIST_RE = re.compile(r"^\[(.*)\]$")


def _strip_domain(value: str) -> str:
    value = value.strip().strip('"\'')
    if "|" in value and value.startswith("<"):
        value = value.split("|", 1)[1].rstrip(">")
    value = value.replace("http://", "").replace("https://", "")
    value = value.split("/", 1)[0]
    value = value.split(" ", 1)[0]
    return value.lower().strip()


def _parse_scalar(value: str):
    value = value.strip()
    if not value:
        return ""
    m = _INLINE_LIST_RE.match(value)
    if m:
        inner = m.group(1).strip()
        if not inner:
            return []
        return [_strip_yaml_token(part) for part in inner.split(",")]
    if value[0:1] in {'"', "'"}:
        try:
            return ast.literal_eval(value)
        except Exception:
            return value.strip('"\'')
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _strip_yaml_token(token: str) -> str:
    return token.strip().strip('"\'')


def _parse_simple_yaml_sources(text: str) -> list[dict]:
    """Parse the controlled source_registry.yaml shape without PyYAML dependency."""
    sources: list[dict] = []
    current: dict | None = None
    pending_list_key: str | None = None
    in_sources = False
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 0:
            in_sources = line == "sources:"
            pending_list_key = None
            continue
        if not in_sources:
            continue
        if indent == 2 and line.startswith("- "):
            if current:
                sources.append(current)
            current = {}
            pending_list_key = None
            rest = line[2:].strip()
            if rest:
                key, _, value = rest.partition(":")
                current[key.strip()] = _parse_scalar(value)
            continue
        if current is None:
            continue
        if indent == 4 and ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value == "":
                current[key] = []
                pending_list_key = key
            else:
                current[key] = _parse_scalar(value)
                pending_list_key = None
            continue
        if indent >= 6 and line.startswith("- ") and pending_list_key:
            current.setdefault(pending_list_key, []).append(_strip_yaml_token(line[2:]))
    if current:
        sources.append(current)
    return sources


class SourceRegistry:
    def __init__(self, sources: list[SourceInfo]):
        self.sources = sources

    @classmethod
    def from_file(cls, path: str | Path) -> "SourceRegistry":
        text = Path(path).read_text(encoding="utf-8")
        rows = _parse_simple_yaml_sources(text)
        sources: list[SourceInfo] = []
        for row in rows:
            domains = tuple(_strip_domain(str(d)) for d in row.get("domains", []) if str(d).strip())
            if not domains:
                continue
            sources.append(SourceInfo(
                name=str(row.get("name", domains[0])),
                domains=domains,
                source_tier=str(row.get("source_tier", "unknown")),
                tier=str(row["tier"]) if "tier" in row else None,
                country=str(row["country"]) if "country" in row else None,
                language=str(row["language"]) if "language" in row else None,
                weight=float(row.get("weight", 1.0)),
                volume=int(row["volume"]) if "volume" in row else None,
                novelty=int(row["novelty"]) if "novelty" in row else None,
                ma_depth=int(row["ma_depth"]) if "ma_depth" in row else None,
                desc=str(row.get("desc", "")),
                use_for=tuple(row.get("use_for", []) or []),
            ))
        return cls(sources)

    def match_domain(self, domain: str) -> SourceInfo | None:
        domain = _strip_domain(domain)
        # Prefer exact/longest domain match over broad parent match.
        matches = []
        for source in self.sources:
            for candidate in source.domains:
                if domain == candidate or domain.endswith("." + candidate):
                    matches.append((len(candidate), source))
        if not matches:
            return None
        return sorted(matches, key=lambda x: x[0], reverse=True)[0][1]

    def match_url(self, url: str) -> SourceInfo | None:
        parsed = urlparse(url)
        return self.match_domain(parsed.netloc or url)
