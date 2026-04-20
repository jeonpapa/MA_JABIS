"""Competitor Trends 초기 시드 — 목업 6건을 DB 로 마이그레이션.

한 번만 실행: `PYTHONPATH=. python3 agents/ingest/competitor_trend_seed.py`.
이후에는 /admin/competitor-trends 에서 편집.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agents.db import DrugPriceDB
from agents.db.schema import DB_SCHEMA

BASE_DIR = Path(__file__).resolve().parents[2]
db = DrugPriceDB(BASE_DIR / "data" / "db" / "drug_prices.db")


SEED = [
    {
        "company": "AstraZeneca Korea", "logo": "AZ", "color": "#00E5CC",
        "badge": "신규 출시", "badge_color": "bg-emerald-500/20 text-emerald-400",
        "headline": "Tagrisso 2세대 제형 국내 허가 신청",
        "detail": "비소세포폐암 1차 치료제로 기존 대비 생체이용률 35% 향상된 신규 제형 허가 신청. 2025년 3분기 출시 예정.",
        "date": "2025-03-28", "source": "MFDS 공시", "url": "https://www.mfds.go.kr",
    },
    {
        "company": "Pfizer Korea", "logo": "PF", "color": "#7C3AED",
        "badge": "가격 변동", "badge_color": "bg-amber-500/20 text-amber-400",
        "headline": "Eliquis 약가 재평가 결과 -4.2% 인하",
        "detail": "건강보험심사평가원 약가 재평가 결과 반영. 2025년 4월 1일부터 적용. 경쟁 제품 대비 여전히 높은 가격 유지.",
        "date": "2025-03-25", "source": "심평원 고시", "url": "https://www.hira.or.kr",
    },
    {
        "company": "Novartis Korea", "logo": "NV", "color": "#F59E0B",
        "badge": "임상 진행", "badge_color": "bg-violet-500/20 text-violet-400",
        "headline": "Cosentyx 건선성 관절염 적응증 확대 임상 3상",
        "detail": "국내 다기관 임상 3상 진행 중. 총 240명 대상, 2026년 상반기 결과 발표 예정.",
        "date": "2025-03-20", "source": "ClinicalTrials.gov", "url": "https://clinicaltrials.gov",
    },
    {
        "company": "Roche Korea", "logo": "RC", "color": "#EF4444",
        "badge": "급여 등재", "badge_color": "bg-emerald-500/20 text-emerald-400",
        "headline": "Tecentriq 소세포폐암 1차 치료 급여 등재 확정",
        "detail": "2025년 4월부터 소세포폐암 1차 치료에 건강보험 급여 적용. 환자 접근성 대폭 향상 예상.",
        "date": "2025-03-18", "source": "보건복지부 고시", "url": "https://www.mohw.go.kr",
    },
    {
        "company": "MSD Korea", "logo": "MS", "color": "#3B82F6",
        "badge": "파이프라인", "badge_color": "bg-blue-500/20 text-blue-400",
        "headline": "Keytruda 병용요법 국내 허가 추가 신청",
        "detail": "대장암 1차 치료 병용요법 허가 신청 접수. 글로벌 임상 결과 기반, 국내 허가 시 시장 판도 변화 예상.",
        "date": "2025-03-15", "source": "MFDS 접수 현황", "url": "https://www.mfds.go.kr",
    },
    {
        "company": "Sanofi Korea", "logo": "SN", "color": "#10B981",
        "badge": "전략 변화", "badge_color": "bg-rose-500/20 text-rose-400",
        "headline": "희귀질환 포트폴리오 강화 전략 발표",
        "detail": "2025-2027 전략 계획 발표. 희귀질환 분야 R&D 투자 40% 확대, 국내 희귀의약품 시장 공략 강화 예고.",
        "date": "2025-03-10", "source": "기업 IR 자료", "url": None,
    },
]


KEYWORD_SEED = [
    ("약가 재평가", 98, "#00E5CC"),
    ("건강보험", 92, "#00E5CC"),
    ("급여 등재", 88, "#00C9B1"),
    ("심평원", 85, "#00E5CC"),
    ("보건복지부", 82, "#00C9B1"),
    ("건강보험공단", 78, "#00E5CC"),
    ("항암제", 74, "#F59E0B"),
    ("바이오시밀러", 70, "#F59E0B"),
    ("위험분담제", 68, "#EF4444"),
    ("RSA", 65, "#EF4444"),
    ("약제급여평가위원회", 62, "#8B9BB4"),
    ("희귀의약품", 60, "#8B9BB4"),
    ("제네릭", 58, "#8B9BB4"),
    ("허가-특허 연계", 55, "#8B9BB4"),
    ("비급여", 52, "#8B9BB4"),
    ("임상시험", 50, "#6B7280"),
    ("의약품 안전", 48, "#6B7280"),
    ("처방 패턴", 45, "#6B7280"),
    ("약가 협상", 43, "#6B7280"),
    ("환자 접근성", 40, "#6B7280"),
]


def seed() -> None:
    with db._connect() as conn:
        conn.executescript(DB_SCHEMA)
        ct_existing = conn.execute("SELECT COUNT(*) FROM competitor_trend").fetchone()[0]
        if ct_existing > 0:
            print(f"competitor_trend: 이미 {ct_existing}건 존재 — 스킵")
        else:
            now = datetime.now(timezone.utc).isoformat()
            for item in SEED:
                conn.execute(
                    "INSERT INTO competitor_trend (company, logo, color, badge, badge_color, headline, detail, date, source, url, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        item["company"], item["logo"], item["color"], item["badge"], item["badge_color"],
                        item["headline"], item["detail"], item["date"], item["source"], item.get("url"),
                        now, now,
                    ),
                )
            conn.commit()
            print(f"competitor_trend: {len(SEED)}건 시드 완료")

        kw_existing = conn.execute("SELECT COUNT(*) FROM keyword_cloud").fetchone()[0]
        if kw_existing > 0:
            print(f"keyword_cloud: 이미 {kw_existing}건 존재 — 스킵")
            return
        now = datetime.now(timezone.utc).isoformat()
        for text, weight, color in KEYWORD_SEED:
            conn.execute(
                "INSERT INTO keyword_cloud (text, weight, color, created_at, updated_at) VALUES (?,?,?,?,?)",
                (text, weight, color, now, now),
            )
        conn.commit()
        print(f"keyword_cloud: {len(KEYWORD_SEED)}건 시드 완료")


if __name__ == "__main__":
    seed()
