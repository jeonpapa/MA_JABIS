"""SQLite 약가 데이터베이스 패키지 — 중앙 저장소.

모든 약가·허가·적응증·로그 데이터의 중앙 저장소.
적용일별 스냅샷 누적 / 보험코드·제품명·성분명 FTS5 인덱스.

구성:
    schema.py       — DB_SCHEMA + COL_CANDIDATES (엑셀 컬럼 매핑)
    base.py         — _DbBase: 연결 + 초기화 + 마이그레이션
    prices.py       — _PricesMixin: 국내 약가 CRUD + 검색
    logs.py         — _LogsMixin: 다운로드/검색 로그 + 데이터 신선도
    enrichment.py   — _EnrichmentMixin: RSA/용법/허가일 캐시
    foreign.py      — _ForeignMixin: 해외 약가
    indications.py  — _IndicationsMixin: 적응증 마스터 + agency variant

공용 API:
    from agents.db import DrugPriceDB
"""
from .base import _DbBase
from .enrichment import _EnrichmentMixin
from .foreign import _ForeignMixin
from .indications import _IndicationsMixin
from .logs import _LogsMixin
from .prices import _PricesMixin
from .schema import COL_CANDIDATES, DB_SCHEMA


class DrugPriceDB(
    _PricesMixin,
    _LogsMixin,
    _EnrichmentMixin,
    _ForeignMixin,
    _IndicationsMixin,
    _DbBase,
):
    """단일 SQLite 파일에 모든 도메인 테이블을 모아둔 통합 DB 핸들."""


__all__ = ["DrugPriceDB", "COL_CANDIDATES", "DB_SCHEMA"]
