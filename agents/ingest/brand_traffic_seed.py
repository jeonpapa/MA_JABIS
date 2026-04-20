"""브랜드 트래픽 초기 시드 — 목업 10건을 DB 로 마이그레이션.

한 번만 실행: `python3 agents/ingest/brand_traffic_seed.py`.
이후에는 /admin/brand-traffic 에서 편집.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from pathlib import Path

from agents.db import DrugPriceDB
from agents.db.schema import DB_SCHEMA

BASE_DIR = Path(__file__).resolve().parents[2]
db = DrugPriceDB(BASE_DIR / "data" / "db" / "drug_prices.db")


SEED = [
    {
        "rank": 1, "brand": "Keytruda", "company": "한국MSD", "category": "면역항암제",
        "color": "#00E5CC", "traffic_index": 9840, "change_pct": 12.4,
        "sparkline": [6200, 7100, 6800, 7900, 8400, 8750, 9840],
        "news": [
            {"title": "Keytruda, 대장암 1차 치료 급여 확대 신청 접수", "source": "메디칼타임즈", "date": "2025-04-14", "tag": "급여", "url": "https://www.medicaltimes.com"},
            {"title": "키트루다 폐암 보조요법 급여 적용 논의 본격화", "source": "약업신문", "date": "2025-04-12", "tag": "급여", "url": "https://www.yakup.com"},
            {"title": "MSD, Keytruda 병용요법 KEYNOTE-789 결과 발표", "source": "청년의사", "date": "2025-04-10", "tag": "임상", "url": "https://www.docdocdoc.co.kr"},
            {"title": "심평원, 키트루다 위암 적응증 급여 재평가 착수", "source": "메디게이트뉴스", "date": "2025-04-08", "tag": "약가", "url": "https://www.medigatenews.com"},
            {"title": "Keytruda 글로벌 매출 1분기 역대 최고 경신", "source": "한국경제", "date": "2025-04-05", "tag": "매출", "url": "https://www.hankyung.com"},
        ],
    },
    {
        "rank": 2, "brand": "Opdivo", "company": "한국BMS", "category": "면역항암제",
        "color": "#7C3AED", "traffic_index": 7210, "change_pct": 5.8,
        "sparkline": [6400, 6100, 6700, 6500, 6900, 6820, 7210],
        "news": [
            {"title": "Opdivo+Yervoy 병용, 간세포암 급여 등재 신청", "source": "메디칼타임즈", "date": "2025-04-13", "tag": "급여", "url": "https://www.medicaltimes.com"},
            {"title": "옵디보 약가 재평가 결과 -9.0% 인하 확정", "source": "심평원 고시", "date": "2025-04-01", "tag": "약가", "url": "https://www.hira.or.kr"},
            {"title": "BMS, CheckMate-816 국내 허가 추가 신청", "source": "청년의사", "date": "2025-03-28", "tag": "허가", "url": "https://www.docdocdoc.co.kr"},
            {"title": "Opdivo 식도암 1차 치료 급여 적용 검토", "source": "약업신문", "date": "2025-03-25", "tag": "급여", "url": "https://www.yakup.com"},
        ],
    },
    {
        "rank": 3, "brand": "Tagrisso", "company": "한국아스트라제네카", "category": "표적항암제",
        "color": "#F59E0B", "traffic_index": 6580, "change_pct": 18.2,
        "sparkline": [3900, 4200, 4800, 5100, 5600, 5900, 6580],
        "news": [
            {"title": "Tagrisso 2세대 제형 국내 허가 신청 접수", "source": "MFDS 공시", "date": "2025-04-15", "tag": "허가", "url": "https://www.mfds.go.kr"},
            {"title": "타그리소 보조요법 급여 확대 논의 재개", "source": "메디게이트뉴스", "date": "2025-04-11", "tag": "급여", "url": "https://www.medigatenews.com"},
            {"title": "AZ, LAURA 임상 결과 국내 학회 발표", "source": "청년의사", "date": "2025-04-07", "tag": "임상", "url": "https://www.docdocdoc.co.kr"},
            {"title": "Tagrisso 약가 재평가 -4.0% 인하 적용", "source": "심평원 고시", "date": "2025-04-01", "tag": "약가", "url": "https://www.hira.or.kr"},
            {"title": "타그리소 3세대 EGFR 억제제 시장 점유율 확대", "source": "한국경제", "date": "2025-03-30", "tag": "시장", "url": "https://www.hankyung.com"},
        ],
    },
    {
        "rank": 4, "brand": "Enhertu", "company": "한국다이이찌산쿄", "category": "ADC 항암제",
        "color": "#EF4444", "traffic_index": 5920, "change_pct": 31.6,
        "sparkline": [2800, 3100, 3600, 4000, 4700, 5200, 5920],
        "news": [
            {"title": "Enhertu 유방암 2차 치료 급여 등재 확정", "source": "보건복지부 고시", "date": "2025-04-14", "tag": "급여", "url": "https://www.mohw.go.kr"},
            {"title": "엔허투 폐암 적응증 허가 신청 접수", "source": "MFDS 공시", "date": "2025-04-09", "tag": "허가", "url": "https://www.mfds.go.kr"},
            {"title": "DESTINY-Breast06 결과 국내 발표, 급여 확대 기대", "source": "메디칼타임즈", "date": "2025-04-03", "tag": "임상", "url": "https://www.medicaltimes.com"},
            {"title": "Enhertu 약가 인상 3.98% 적용, 신규 적응증 반영", "source": "약업신문", "date": "2025-02-15", "tag": "약가", "url": "https://www.yakup.com"},
        ],
    },
    {
        "rank": 5, "brand": "Imfinzi", "company": "한국아스트라제네카", "category": "면역항암제",
        "color": "#10B981", "traffic_index": 4870, "change_pct": -2.1,
        "sparkline": [5200, 5100, 5300, 4900, 5050, 4980, 4870],
        "news": [
            {"title": "Imfinzi 소세포폐암 1차 치료 급여 등재 신청", "source": "메디칼타임즈", "date": "2025-04-12", "tag": "급여", "url": "https://www.medicaltimes.com"},
            {"title": "임핀지 약가 재평가 -7.0% 인하 확정", "source": "심평원 고시", "date": "2025-04-01", "tag": "약가", "url": "https://www.hira.or.kr"},
            {"title": "PACIFIC 임상 5년 추적 결과 국내 발표", "source": "청년의사", "date": "2025-03-22", "tag": "임상", "url": "https://www.docdocdoc.co.kr"},
        ],
    },
    {
        "rank": 6, "brand": "Lynparza", "company": "한국MSD/AZ", "category": "PARP 억제제",
        "color": "#F97316", "traffic_index": 4210, "change_pct": 8.9,
        "sparkline": [3500, 3600, 3750, 3820, 3900, 4050, 4210],
        "news": [
            {"title": "Lynparza 전립선암 급여 적용 심평원 검토 중", "source": "메디게이트뉴스", "date": "2025-04-10", "tag": "급여", "url": "https://www.medigatenews.com"},
            {"title": "린파자 유방암 보조요법 급여 확대 신청", "source": "약업신문", "date": "2025-04-06", "tag": "급여", "url": "https://www.yakup.com"},
            {"title": "PARP 억제제 시장 경쟁 심화, 린파자 전략 변화", "source": "한국경제", "date": "2025-03-28", "tag": "시장", "url": "https://www.hankyung.com"},
        ],
    },
    {
        "rank": 7, "brand": "Tecentriq", "company": "한국로슈", "category": "면역항암제",
        "color": "#EC4899", "traffic_index": 3640, "change_pct": -5.3,
        "sparkline": [4100, 4000, 3900, 3850, 3780, 3700, 3640],
        "news": [
            {"title": "Tecentriq 소세포폐암 1차 치료 급여 등재 확정", "source": "보건복지부 고시", "date": "2025-04-01", "tag": "급여", "url": "https://www.mohw.go.kr"},
            {"title": "티쎈트릭 약가 재평가 -8.0% 인하 적용", "source": "심평원 고시", "date": "2025-04-01", "tag": "약가", "url": "https://www.hira.or.kr"},
            {"title": "Roche, Tecentriq 간암 적응증 국내 허가 신청", "source": "MFDS 공시", "date": "2025-03-20", "tag": "허가", "url": "https://www.mfds.go.kr"},
        ],
    },
    {
        "rank": 8, "brand": "Calquence", "company": "한국아스트라제네카", "category": "BTK 억제제",
        "color": "#8B5CF6", "traffic_index": 2980, "change_pct": 22.4,
        "sparkline": [1800, 1950, 2100, 2300, 2500, 2720, 2980],
        "news": [
            {"title": "Calquence CLL 1차 치료 급여 등재 신청", "source": "메디칼타임즈", "date": "2025-04-13", "tag": "급여", "url": "https://www.medicaltimes.com"},
            {"title": "칼퀀스 MCL 적응증 허가 신청 접수", "source": "MFDS 공시", "date": "2025-04-08", "tag": "허가", "url": "https://www.mfds.go.kr"},
            {"title": "BTK 억제제 시장 경쟁 본격화, 칼퀀스 전략 발표", "source": "약업신문", "date": "2025-04-02", "tag": "시장", "url": "https://www.yakup.com"},
        ],
    },
    {
        "rank": 9, "brand": "Januvia", "company": "한국MSD", "category": "DPP-4 억제제",
        "color": "#06B6D4", "traffic_index": 2540, "change_pct": -8.7,
        "sparkline": [3100, 2980, 2850, 2780, 2700, 2620, 2540],
        "news": [
            {"title": "Januvia 제네릭 출시 확대로 시장 점유율 하락", "source": "약업신문", "date": "2025-04-11", "tag": "시장", "url": "https://www.yakup.com"},
            {"title": "자누비아 약가 재평가 -4.2% 인하 적용", "source": "심평원 고시", "date": "2025-04-01", "tag": "약가", "url": "https://www.hira.or.kr"},
            {"title": "DPP-4 억제제 시장 경쟁 심화, MSD 대응 전략", "source": "메디게이트뉴스", "date": "2025-03-25", "tag": "시장", "url": "https://www.medigatenews.com"},
        ],
    },
    {
        "rank": 10, "brand": "Libtayo", "company": "사노피", "category": "면역항암제",
        "color": "#84CC16", "traffic_index": 2180, "change_pct": 14.6,
        "sparkline": [1500, 1620, 1700, 1820, 1900, 2050, 2180],
        "news": [
            {"title": "Libtayo 피부암 급여 적용 범위 확대 신청", "source": "메디칼타임즈", "date": "2025-04-09", "tag": "급여", "url": "https://www.medicaltimes.com"},
            {"title": "립타요 약가 재평가 -5.0% 인하 확정", "source": "심평원 고시", "date": "2025-04-01", "tag": "약가", "url": "https://www.hira.or.kr"},
            {"title": "Sanofi, Libtayo 폐암 적응증 국내 허가 추진", "source": "MFDS 공시", "date": "2025-03-18", "tag": "허가", "url": "https://www.mfds.go.kr"},
        ],
    },
]


def seed():
    with db._connect() as conn:
        conn.executescript(DB_SCHEMA)
        existing = conn.execute("SELECT COUNT(*) FROM brand_traffic").fetchone()[0]
        if existing > 0:
            print(f"brand_traffic: 이미 {existing}건 존재 — 스킵")
            return
        now = datetime.now(timezone.utc).isoformat()
        for item in SEED:
            conn.execute(
                "INSERT INTO brand_traffic (rank, brand, company, category, color, traffic_index, change_pct, sparkline_json, news_json, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item["rank"], item["brand"], item["company"], item["category"], item["color"],
                    item["traffic_index"], item["change_pct"],
                    json.dumps(item["sparkline"], ensure_ascii=False),
                    json.dumps(item["news"], ensure_ascii=False),
                    now, now,
                ),
            )
        conn.commit()
    print(f"brand_traffic: {len(SEED)}건 시드 완료")


if __name__ == "__main__":
    seed()
