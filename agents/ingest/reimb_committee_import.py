"""약평위·암질심 pipeline DB importer — 지난 12개월 실제 위원회 결과 적재.

데이터 출처 (FAITHFUL transcription, 날조 금지):
  - data/hira_pipeline/통과_약물_archive/2025-2026_통과약물.md     (약평위 통과)
  - data/hira_pipeline/미통과_약물_관리/약평위_미진입_큐.md         (암질심 통과·약평위 대기)
  - data/hira_pipeline/미통과_약물_관리/약평위_재심의_부결.md       (재심의/부결)
  - data/hira_pipeline/미통과_약물_관리/암질심_미설정.md            (암질심 미설정)

원칙 (CLAUDE.md):
  - md 파일에 명시된 사실만 전사. 날짜/적응증/회사 날조 금지.
  - HIRA 공식 verified vs 매체 보도 구분 → evidence_url / notes 에 보존.
  - '추정'/'미확인' → null + notes, 절대 추측 금지.
  - 멱등(idempotent): UPSERT(brand_kr+ingredient_inn), 큐 이벤트 중복 방지.
  - 기존 웰리렉/키트루다 2행 + 웰리렉 AMJILSIM 큐 이벤트 보존(삭제 금지).

실행: .venv/bin/python -m agents.ingest.reimb_committee_import
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"

# ──────────────────────────────────────────────────────────────────────────────
# 1) 누락된 2025 약평위 세션 (통과약물.md 매체 보도 section 에서 참조됨)
#    year=2025, COMMITTEE='YAKPYUNGWI', status='COMPLETED'
# ──────────────────────────────────────────────────────────────────────────────
MISSING_SESSIONS = [
    # (year, ordinal_assumed, ordinal_official, session_date, committee, note)
    (2025, 9, 9, "2025-09-04", "YAKPYUNGWI",
     "통과약물.md 매체 보도(메디포뉴스 206439) 참조 — 9차 약평위"),
    (2025, 11, 11, "2025-11-06", "YAKPYUNGWI",
     "통과약물.md 매체 보도(메디파나 401520) 참조 — 11차 약평위"),
    (2025, 12, 12, "2025-12-04", "YAKPYUNGWI",
     "통과약물.md 매체 보도(메디포뉴스 209530) 참조 — 12차 약평위"),
]

# ──────────────────────────────────────────────────────────────────────────────
# 약물 전사 데이터
#   각 dict: brand_kr(필수), brand_en, ingredient_inn, manufacturer, msd_flag,
#            tracking_priority, amjilsim_pass_date, yakpyungwi_pass_date,
#            negotiation_status, indication, listing_type, submitted_date, notes,
#            events: [ {committee, state, session_date|session_id, n_th_attempt,
#                       evidence_url, queue_entry_date} ]
#   committee: 'AMJILSIM' | 'YAKPYUNGWI'  (세션 매칭은 session_date 로)
#
#   ingredient_inn 은 md 가 한글 성분명을 줄 때 그대로(또는 INN 영문). md 에 성분
#   미명시면 None. UNIQUE(brand_kr, ingredient_inn) 멱등키.
# ──────────────────────────────────────────────────────────────────────────────

DRUGS: list[dict] = []


def D(**kw):
    DRUGS.append(kw)


# ──────────────────────────────────────────────────────────────────────────────
# 다음 차수 상정 예정 후보 (md '후보 차수' 중 가장 이른 미래 차수)
#   → 평가 로직 입력. 백엔드 get_pipeline 이 "expected == 다음 upcoming 차수" 면
#     '심의 상정예정'(scheduled) 으로 도출. (오늘 2026-06 기준; 6/4 6차 약평위는 지남)
#   session_date 는 전역 UNIQUE 라 date 만으로 세션 해석 가능.
# ──────────────────────────────────────────────────────────────────────────────
EXPECTED_NEXT_SESSION: dict[str, str] = {
    "카보메틱스":   "2026-07-02",  # 후보 6/4·7/2 → 7/2 7차 약평위
    "사이람자":     "2026-07-02",  # 후보 6/4·7/2
    "옵디보 + 여보이": "2026-07-02",  # 후보 7/2·8/6
    "엘라히어주":   "2026-07-02",  # 후보 7/2(fast-track)·8/6
    "버제니오정":   "2026-07-02",  # 후보 7/2·8/6·9/3
    "리브리반트주": "2026-07-02",  # 재상정 후보 6/4·7/2 (약평위 재심의)
    "림카토주":     "2026-07-08",  # 재상정 희망 7/8 6차 암질심
}


# ── (A) 약평위 통과 약물 — 통과약물.md ────────────────────────────────────────
# HIRA 공식 verified (brdBltNo 보유): 4차(11763), 5차(11793)
# yakpyungwi_pass_date = 해당 약평위 session date → nhis 단계로 안착.

# 4차 약평위 2026-04-02 (HIRA brdBltNo=11763) — 6종
D(brand_kr="베오바정 50mg 외 1품목", ingredient_inn=None, manufacturer="제일약품",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2026-04-02", negotiation_status="IN_PROGRESS",
  indication="과민성 방광", listing_type="신규",
  notes="4차 약평위 통과 (조건: 평가금액 이하 수용 시). HIRA 공식 verified.",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2026-04-02",
               evidence_url="HIRA brdBltNo=11763")])
D(brand_kr="메탈라제주사 25mg", ingredient_inn=None, manufacturer="한국베링거",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2026-04-02", negotiation_status="IN_PROGRESS",
  indication="급성 허혈성 뇌졸중", listing_type="신규",
  notes="4차 약평위 통과 (조건: 즉시 적정). HIRA 공식 verified.",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2026-04-02",
               evidence_url="HIRA brdBltNo=11763")])
D(brand_kr="엘루시렘주사 외 8품목", ingredient_inn=None, manufacturer="게르베코리아 등",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2026-04-02", negotiation_status="IN_PROGRESS",
  indication="MRI 조영제", listing_type="신규",
  notes="4차 약평위 통과 (조건: 평가금액 이하 수용 시). HIRA 공식 verified.",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2026-04-02",
               evidence_url="HIRA brdBltNo=11763")])
D(brand_kr="타브너스캡슐 10mg", ingredient_inn=None, manufacturer="메디팁",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2026-04-02", negotiation_status="IN_PROGRESS",
  indication="중증 GPA/MPA", listing_type="신규",
  notes="4차 약평위 통과 (조건: 즉시 적정). HIRA 공식 verified.",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2026-04-02",
               evidence_url="HIRA brdBltNo=11763")])
D(brand_kr="파드셉주 20·30mg", ingredient_inn="enfortumab vedotin",
  manufacturer="한국아스텔라스", msd_flag=0, tracking_priority="competitor_class",
  yakpyungwi_pass_date="2026-04-02", negotiation_status="IN_PROGRESS",
  indication="요로상피암 1차 (펨브로 병용)", listing_type="신규",
  notes="4차 약평위 통과 (조건: 즉시 적정). 펨브로(키트루다) 병용 적응증. HIRA 공식 verified.",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2026-04-02",
               evidence_url="HIRA brdBltNo=11763")])
D(brand_kr="블린사이토주", ingredient_inn="blinatumomab", manufacturer="암젠코리아",
  msd_flag=0, tracking_priority="competitor_class",
  yakpyungwi_pass_date="2026-04-02", negotiation_status="IN_PROGRESS",
  indication="B-ALL 범위 확대 (RSA)", listing_type="확대",
  notes="4차 약평위 통과 (조건: 확대 적정, RSA). HIRA 공식 verified.",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2026-04-02",
               evidence_url="HIRA brdBltNo=11763")])

# 5차 약평위 2026-05-07 (HIRA brdBltNo=11793) — 7종
D(brand_kr="보신티정", ingredient_inn=None, manufacturer="한국다케다",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2026-05-07", negotiation_status="IN_PROGRESS",
  indication="위궤양", listing_type="신규",
  notes="5차 약평위 통과 (결정신청 약제). HIRA 공식 verified.",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2026-05-07",
               evidence_url="HIRA brdBltNo=11793")])
D(brand_kr="보노칸정", ingredient_inn=None, manufacturer="경보제약",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2026-05-07", negotiation_status="IN_PROGRESS",
  indication="위궤양", listing_type="신규",
  notes="5차 약평위 통과 (결정신청 약제). HIRA 공식 verified.",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2026-05-07",
               evidence_url="HIRA brdBltNo=11793")])
D(brand_kr="보노엠정", ingredient_inn=None, manufacturer="마더스제약",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2026-05-07", negotiation_status="IN_PROGRESS",
  indication="위궤양", listing_type="신규",
  notes="5차 약평위 통과 (결정신청 약제). HIRA 공식 verified.",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2026-05-07",
               evidence_url="HIRA brdBltNo=11793")])
D(brand_kr="브레즈트리에어로스피어흡입제", ingredient_inn=None,
  manufacturer="한국아스트라제네카", msd_flag=0, tracking_priority="competitor_class",
  yakpyungwi_pass_date="2026-05-07", negotiation_status="IN_PROGRESS",
  indication="COPD 유지", listing_type="신규",
  notes="5차 약평위 통과 (결정신청 약제). HIRA 공식 verified.",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2026-05-07",
               evidence_url="HIRA brdBltNo=11793")])
D(brand_kr="예스카타주", ingredient_inn="axicabtagene ciloleucel",
  manufacturer="길리어드코리아", msd_flag=0, tracking_priority="competitor_class",
  yakpyungwi_pass_date="2026-05-07", negotiation_status="IN_PROGRESS",
  indication="림프종", listing_type="신규",
  notes="5차 약평위 통과 (신약 승인 트랙, CAR-T). HIRA 공식 verified.",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2026-05-07",
               evidence_url="HIRA brdBltNo=11793")])
D(brand_kr="레테브모캡슐", ingredient_inn="selpercatinib", manufacturer="한국릴리",
  msd_flag=0, tracking_priority="competitor_class",
  yakpyungwi_pass_date="2026-05-07", negotiation_status="IN_PROGRESS",
  indication="폐암·갑상선수질암", listing_type="신규",
  notes="5차 약평위 통과 (신약 승인 트랙). HIRA 공식 verified.",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2026-05-07",
               evidence_url="HIRA brdBltNo=11793")])
D(brand_kr="티쎈트릭주", ingredient_inn="atezolizumab", manufacturer="한국로슈",
  msd_flag=0, tracking_priority="competitor_class",
  yakpyungwi_pass_date="2026-05-07", negotiation_status="IN_PROGRESS",
  indication="NSCLC 급여 확대", listing_type="확대",
  notes="5차 약평위 통과 (위험분담계약 RSA 확대). HIRA 공식 verified.",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2026-05-07",
               evidence_url="HIRA brdBltNo=11793")])

# 매체 보도 추가 통과 약물 (HIRA 본문 verification 대기) — evidence_url 에 매체 표기
# 1차 약평위 2026-01-15 (매체 — 메디포뉴스 210567) — 5종
_MEDIA_1CHA = ("매체: 메디포뉴스 210567 — HIRA 본문 미verified", "2026-01-15")
D(brand_kr="다잘렉스 PV", ingredient_inn="daratumumab", manufacturer="얀센",
  msd_flag=0, tracking_priority="competitor_class",
  yakpyungwi_pass_date="2026-01-15", negotiation_status="IN_PROGRESS",
  indication="AL 아밀로이드증", listing_type="신규",
  notes="1차 약평위 통과 (매체 보도, HIRA 본문 미verified).",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2026-01-15",
               evidence_url=_MEDIA_1CHA[0])])
D(brand_kr="옴짜라", ingredient_inn=None, manufacturer="GSK",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2026-01-15", negotiation_status="IN_PROGRESS",
  indication="골수섬유증", listing_type="신규",
  notes="1차 약평위 통과 (매체 보도, HIRA 본문 미verified).",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2026-01-15",
               evidence_url=_MEDIA_1CHA[0])])
D(brand_kr="누칼라", ingredient_inn="mepolizumab", manufacturer="GSK",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2026-01-15", negotiation_status="IN_PROGRESS",
  indication="중증 호산구성 천식", listing_type="신규",
  notes="1차 약평위 통과 (매체 보도, HIRA 본문 미verified).",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2026-01-15",
               evidence_url=_MEDIA_1CHA[0])])
D(brand_kr="스핀라자", ingredient_inn="nusinersen", manufacturer="바이오젠",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2026-01-15", negotiation_status="IN_PROGRESS",
  indication="SMA 확대", listing_type="확대",
  notes="1차 약평위 통과 (매체 보도, HIRA 본문 미verified).",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2026-01-15",
               evidence_url=_MEDIA_1CHA[0])])
D(brand_kr="에브리스디", ingredient_inn="risdiplam", manufacturer="로슈",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2026-01-15", negotiation_status="IN_PROGRESS",
  indication="SMA 확대", listing_type="확대",
  notes="1차 약평위 통과 (매체 보도, HIRA 본문 미verified).",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2026-01-15",
               evidence_url=_MEDIA_1CHA[0])])

# 12차 약평위 2025-12-04 (매체 — 메디포뉴스 209530) — 6종
_MEDIA_12CHA = "매체: 메디포뉴스 209530 — HIRA 본문 미verified"
D(brand_kr="마운자로", ingredient_inn="tirzepatide", manufacturer="릴리",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2025-12-04", negotiation_status="IN_PROGRESS",
  indication="제2형 당뇨병", listing_type="신규",
  notes="12차 약평위 통과 (매체 보도, HIRA 본문 미verified).",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2025-12-04",
               evidence_url=_MEDIA_12CHA)])
D(brand_kr="복스조고", ingredient_inn="vosoritide", manufacturer="삼오제약",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2025-12-04", negotiation_status="IN_PROGRESS",
  indication="소아 연골무형성증", listing_type="신규",
  notes="12차 약평위 통과 (매체 보도, HIRA 본문 미verified).",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2025-12-04",
               evidence_url=_MEDIA_12CHA)])
D(brand_kr="엡킨리", ingredient_inn="epcoritamab", manufacturer="애브비",
  msd_flag=0, tracking_priority="competitor_class",
  yakpyungwi_pass_date="2025-12-04", negotiation_status="IN_PROGRESS",
  indication="DLBCL", listing_type="신규",
  notes="12차 약평위 통과 (매체 보도, HIRA 본문 미verified).",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2025-12-04",
               evidence_url=_MEDIA_12CHA)])
D(brand_kr="옵신비", ingredient_inn=None, manufacturer="얀센",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2025-12-04", negotiation_status="IN_PROGRESS",
  indication="폐동맥고혈압 (조건부)", listing_type="신규",
  notes="12차 약평위 통과 (조건부, 매체 보도, HIRA 본문 미verified).",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2025-12-04",
               evidence_url=_MEDIA_12CHA)])
D(brand_kr="바다넴", ingredient_inn=None, manufacturer="미쓰비시다나베",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2025-12-04", negotiation_status="IN_PROGRESS",
  indication="투석 환자 빈혈 (조건부)", listing_type="신규",
  notes="12차 약평위 통과 (조건부, 매체 보도, HIRA 본문 미verified).",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2025-12-04",
               evidence_url=_MEDIA_12CHA)])
D(brand_kr="암부트라", ingredient_inn="vutrisiran", manufacturer="메디슨파마",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2025-12-04", negotiation_status="IN_PROGRESS",
  indication="TTR 가족성 아밀로이드성 다발신경병증", listing_type="신규",
  notes="12차 약평위 통과 (매체 보도, HIRA 본문 미verified).",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2025-12-04",
               evidence_url=_MEDIA_12CHA)])

# 11차 약평위 2025-11-06 (매체 — 메디파나 401520) — 5종
_MEDIA_11CHA = "매체: 메디파나 401520 — HIRA 본문 미verified"
D(brand_kr="임핀지+젬시스", ingredient_inn="durvalumab", manufacturer="AZ",
  msd_flag=0, tracking_priority="competitor_class",
  yakpyungwi_pass_date="2025-11-06", negotiation_status="AGREED",
  indication="담도암 1차", listing_type="신규",
  notes="11차 약평위 통과 (매체 보도, HIRA 본문 미verified). 9차 재심의→11차 통과. "
        "2026-03 등재. 재심의 turnaround ~2개월.",
  events=[dict(committee="YAKPYUNGWI", state="REJECTED_REQUEUE",
               session_date="2025-09-04", n_th_attempt=1,
               evidence_url="매체: 9차 재심의 — HIRA 본문 미verified"),
          dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2025-11-06",
               n_th_attempt=2, evidence_url=_MEDIA_11CHA)])
D(brand_kr="임핀지+이뮤도", ingredient_inn="durvalumab+tremelimumab",
  manufacturer="AZ", msd_flag=0, tracking_priority="competitor_class",
  yakpyungwi_pass_date="2025-11-06", negotiation_status="AGREED",
  indication="간세포암 1차", listing_type="신규",
  notes="11차 약평위 통과 (매체 보도, HIRA 본문 미verified). 9차 재심의→11차 통과. "
        "2026-03 등재. 이뮤도(트레멜리무맙) 병용. 재심의 turnaround ~2개월.",
  events=[dict(committee="YAKPYUNGWI", state="REJECTED_REQUEUE",
               session_date="2025-09-04", n_th_attempt=1,
               evidence_url="매체: 9차 재심의 — HIRA 본문 미verified"),
          dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2025-11-06",
               n_th_attempt=2, evidence_url=_MEDIA_11CHA)])
D(brand_kr="발베사", ingredient_inn="erdafitinib", manufacturer="얀센",
  msd_flag=0, tracking_priority="competitor_class",
  yakpyungwi_pass_date="2025-11-06", negotiation_status="AGREED",
  indication="FGFR3 요로상피암", listing_type="신규",
  notes="11차 약평위 통과 (매체 보도, HIRA 본문 미verified). 2026-03 등재.",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2025-11-06",
               evidence_url=_MEDIA_11CHA)])
D(brand_kr="탁자이로 PFS", ingredient_inn=None, manufacturer="다케다",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2025-11-06", negotiation_status="AGREED",
  indication="HAE", listing_type="신규",
  notes="11차 약평위 통과 (매체 보도, HIRA 본문 미verified). 2026-03 등재.",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2025-11-06",
               evidence_url=_MEDIA_11CHA)])
D(brand_kr="엑스포비오", ingredient_inn="selinexor", manufacturer="안텐진",
  msd_flag=0, tracking_priority="competitor_class",
  yakpyungwi_pass_date="2025-11-06", negotiation_status="IN_PROGRESS",
  indication="다발골수종 (조건부)", listing_type="신규",
  notes="11차 약평위 통과 (조건부, 매체 보도, HIRA 본문 미verified).",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2025-11-06",
               evidence_url=_MEDIA_11CHA)])

# 9차 약평위 2025-09-04 (매체 — 메디포뉴스 206439) — 4종
_MEDIA_9CHA = "매체: 메디포뉴스 206439 — HIRA 본문 미verified"
D(brand_kr="페트로자", ingredient_inn=None, manufacturer="제일약품",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2025-09-04", negotiation_status="IN_PROGRESS",
  indication="복잡성 요로감염", listing_type="신규",
  notes="9차 약평위 통과 (매체 보도, HIRA 본문 미verified).",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2025-09-04",
               evidence_url=_MEDIA_9CHA)])
D(brand_kr="레주록", ingredient_inn=None, manufacturer="사노피",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2025-09-04", negotiation_status="IN_PROGRESS",
  indication="만성 이식편대숙주질환", listing_type="신규",
  notes="9차 약평위 통과 (매체 보도, HIRA 본문 미verified).",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2025-09-04",
               evidence_url=_MEDIA_9CHA)])
# 키트루다 11개 적응증 — 기존 키트루다(drug_id=2, msd_asset) 행에 약평위 통과 반영.
# brand_kr='키트루다' 그대로 매칭(UPSERT) → msd_flag=1 유지.
D(brand_kr="키트루다", ingredient_inn="pembrolizumab", manufacturer="한국MSD",
  msd_flag=1, tracking_priority="msd_asset",
  yakpyungwi_pass_date="2025-09-04", negotiation_status="AGREED",
  indication="11개 적응증", listing_type="확대",
  notes="9차 약평위 통과 — 키트루다 11개 적응증 (매체: 메디포뉴스 206439, HIRA 본문 미verified). "
        "2026-01-01 급여 시행. MSD asset.",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2025-09-04",
               evidence_url=_MEDIA_9CHA)])
D(brand_kr="듀피젠트", ingredient_inn="dupilumab", manufacturer="사노피",
  msd_flag=0, tracking_priority="generic_new_drug",
  yakpyungwi_pass_date="2025-09-04", negotiation_status="IN_PROGRESS",
  indication="천식 확대", listing_type="확대",
  notes="9차 약평위 통과 (매체 보도, HIRA 본문 미verified).",
  events=[dict(committee="YAKPYUNGWI", state="APPROVED", session_date="2025-09-04",
               evidence_url=_MEDIA_9CHA)])

# ── (B) 약평위 미진입 큐 — 암질심 통과·약평위 대기 ──────────────────────────
# amjilsim_pass_date = 암질심 통과 차수 date, negotiation_status='IN_PROGRESS'
# 자카비/포말리스트: yakpyungwi 차수 '추정'만 존재 → 날조 금지로 yakpyungwi_pass_date
# null 유지, negotiation_status='AGREED', 급여 시행일을 notes 에 기록.

D(brand_kr="자카비", ingredient_inn="ruxolitinib", manufacturer="노바티스",
  msd_flag=0, tracking_priority="competitor_class",
  amjilsim_pass_date="2026-01-21", yakpyungwi_pass_date=None,
  negotiation_status="AGREED",
  indication="진성적혈구증가증 PV 2차 치료 (HU 내성·불내성)", listing_type="확대",
  notes="암질심 1차(2026-01-21) 통과(매체 verified). 급여 시행일 2026-05-01 "
        "(kpanews 534206). 약평위 통과 차수 4차 추정 — HIRA 공식 재검증 대기, "
        "yakpyungwi_pass_date 미확인으로 null(날조 금지). 등재 완료.",
  events=[dict(committee="AMJILSIM", state="APPROVED", session_date="2026-01-21",
               evidence_url="매체 verified — kpanews 534206 (급여시행 2026-05-01)")])
D(brand_kr="포말리스트", ingredient_inn="pomalidomide", manufacturer="BMS",
  msd_flag=0, tracking_priority="competitor_class",
  amjilsim_pass_date="2026-04-15", yakpyungwi_pass_date=None,
  negotiation_status="AGREED",
  indication="다발골수종 PVd 병용 2차 치료 확대", listing_type="확대",
  notes="암질심 1차(2026-01-21 매체) + 4차(2026-04-15 HIRA brdBltNo=11770 추가) 통과. "
        "급여 시행일 2026-06-01 (kpanews 535860). 약평위 통과 차수 4·5차 추정 — "
        "HIRA 공식 재검증 대기(5차 archive 미등장), yakpyungwi_pass_date null(날조 금지). "
        "등재 임박. amjilsim_pass_date 는 HIRA verified 4차 사용.",
  events=[dict(committee="AMJILSIM", state="APPROVED", session_date="2026-04-15",
               evidence_url="HIRA brdBltNo=11770")])
D(brand_kr="카보메틱스", ingredient_inn="cabozantinib", manufacturer="입센·HK이노엔",
  msd_flag=0, tracking_priority="competitor_class",
  amjilsim_pass_date="2026-01-21", negotiation_status="IN_PROGRESS",
  indication="신장세포암 단독 확대", listing_type="확대",
  notes="암질심 1차(2026-01-21 매체) 통과. 약평위 미진입 4개월+. 후보 차수 6/4 6차·7/2 7차.",
  events=[dict(committee="AMJILSIM", state="APPROVED", session_date="2026-01-21",
               evidence_url="매체 보도 — HIRA 본문 미verified")])
D(brand_kr="사이람자", ingredient_inn="ramucirumab", manufacturer="한국릴리",
  msd_flag=0, tracking_priority="competitor_class",
  amjilsim_pass_date="2026-01-21", negotiation_status="IN_PROGRESS",
  indication="위암 단독·병용 확대", listing_type="확대",
  notes="암질심 1차(2026-01-21 매체) 통과. 약평위 미진입 4개월+. 후보 차수 6/4 6차·7/2 7차.",
  events=[dict(committee="AMJILSIM", state="APPROVED", session_date="2026-01-21",
               evidence_url="매체 보도 — HIRA 본문 미verified")])
D(brand_kr="옵디보 + 여보이", ingredient_inn="nivolumab+ipilimumab",
  manufacturer="한국오노/BMS", msd_flag=0, tracking_priority="competitor_class",
  amjilsim_pass_date="2026-04-15", negotiation_status="IN_PROGRESS",
  indication="간세포암 1차 (간세포암 2차 미설정)", listing_type="확대",
  notes="암질심 4차(2026-04-15 HIRA brdBltNo=11770) 통과. 약평위 미진입 7주. "
        "후보 차수 7/2 7차·8/6 8차. (간세포암 2차는 미설정)",
  events=[dict(committee="AMJILSIM", state="APPROVED", session_date="2026-04-15",
               evidence_url="HIRA brdBltNo=11770")])
D(brand_kr="바벤시오", ingredient_inn="avelumab", manufacturer="한국머크",
  msd_flag=0, tracking_priority="competitor_class",
  amjilsim_pass_date="2026-04-15", negotiation_status="IN_PROGRESS",
  indication="요로상피암 1L 유지 (방광암 확대)", listing_type="확대",
  notes="암질심 4차(2026-04-15) 통과 — 매체 보도만. HIRA 본문(옵디보+여보이·포말리스트만 명시)"
        "에서 바벤시오 미verified, 재확인 필요.",
  events=[dict(committee="AMJILSIM", state="APPROVED", session_date="2026-04-15",
               evidence_url="매체 보도 — HIRA 본문 미verified (재확인 필요)")])
D(brand_kr="엘라히어주", ingredient_inn="mirvetuximab soravtansine",
  manufacturer="한국애브비", msd_flag=0, tracking_priority="competitor_class",
  amjilsim_pass_date="2026-05-27", negotiation_status="IN_PROGRESS",
  indication="FRα+ 백금저항성 난소암", listing_type="신규",
  notes="암질심 5차(2026-05-27 HIRA brdBltNo=11808) 통과. 약평위 미진입 8일(정상). "
        "후보 차수 7/2 7차(fast-track 가능성↑)·8/6 8차.",
  events=[dict(committee="AMJILSIM", state="APPROVED", session_date="2026-05-27",
               evidence_url="HIRA brdBltNo=11808")])
D(brand_kr="버제니오정", ingredient_inn="abemaciclib", manufacturer="한국릴리",
  msd_flag=0, tracking_priority="competitor_class",
  amjilsim_pass_date="2026-05-27", negotiation_status="IN_PROGRESS",
  indication="HR+/HER2- 림프절+ 고위험 조기 유방암 보조", listing_type="확대",
  notes="암질심 5차(2026-05-27 HIRA brdBltNo=11808) 통과. 약평위 미진입 8일. "
        "후보 차수 7/2 7차·8/6 8차·9/3 9차.",
  events=[dict(committee="AMJILSIM", state="APPROVED", session_date="2026-05-27",
               evidence_url="HIRA brdBltNo=11808")])

# ── (C) 약평위 재심의·부결 — 재심의_부결.md ────────────────────────────────
# 리브리반트: 암질심 미설정 history → amjilsim_pass_date null. 약평위 5차 재심의.
D(brand_kr="리브리반트주", ingredient_inn="amivantamab", manufacturer="한국얀센",
  msd_flag=0, tracking_priority="competitor_class",
  amjilsim_pass_date=None, yakpyungwi_pass_date=None,
  negotiation_status=None,
  indication="비소세포폐암 (EGFR 변이 추정)", listing_type="신규",
  notes="2026-05-07 5차 약평위 첫 진입 → 재심의 결정 (HIRA brdBltNo=11793). "
        "장기 history: 2022·2023·2024 암질심 미설정, 2025-09-03 암질심 미설정(4회 누적), "
        "2025-09-22 자진취하, 2025-09-26 재신청, 2026-05-07 약평위 첫 진입→재심의. "
        "재상정 후보 6/4 6차·7/2 7차. EGFR+ NSCLC market access 핵심 사례. "
        "암질심 미설정 history 로 amjilsim_pass_date null. 적응증 EGFR 변이 '추정'.",
  events=[dict(committee="YAKPYUNGWI", state="REJECTED_REQUEUE",
               session_date="2026-05-07", n_th_attempt=1,
               evidence_url="HIRA brdBltNo=11793 (5차 약평위 재심의)")])
# 타이바소: 2025-9차 약평위 비급여 판정(매체) → evaluation, REJECTED_REQUEUE.
D(brand_kr="타이바소 흡입액", ingredient_inn="treprostinil", manufacturer="안트로젠",
  msd_flag=0, tracking_priority="generic_new_drug",
  amjilsim_pass_date=None, yakpyungwi_pass_date=None, negotiation_status=None,
  indication=None, listing_type="신규",
  notes="2025 9차 약평위 비급여 판정 (매체 보도). 적응증 미확인. "
        "HIRA 공식 미보유(보도자료 본문 fetch 필요). 재신청 동향 매체 부재.",
  events=[dict(committee="YAKPYUNGWI", state="REJECTED_REQUEUE",
               session_date="2025-09-04", n_th_attempt=1,
               evidence_url="매체 보도 — HIRA 본문 미verified (비급여 판정)")])
# 빌베이: 2025 상반기 약평위 재심의(매체). 차수/세션 불명 → session_id null,
# committee YAKPYUNGWI, QUEUE_PROCESSED(심의 완료, 재심의 판정 — 차수 미확인이라
# session 링크 없이 기록). queue_entry_date 도 미확인 → null.
D(brand_kr="빌베이", ingredient_inn="odevixibat", manufacturer=None,
  msd_flag=0, tracking_priority="generic_new_drug",
  amjilsim_pass_date=None, yakpyungwi_pass_date=None, negotiation_status=None,
  indication=None, listing_type="신규",
  notes="2025 상반기 약평위 재심의 판정 (매체 보도). 희귀의약품 HEAN 2호. "
        "회사·적응증·정확한 차수 미확인. 차수 미확인으로 session 링크 없이 기록.",
  events=[dict(committee="YAKPYUNGWI", state="REJECTED_REQUEUE", session_date=None,
               n_th_attempt=1,
               evidence_url="매체 보도 — HIRA 본문 미verified (2025 상반기 재심의)")])

# ── (D) 암질심 미설정 — 암질심_미설정.md ────────────────────────────────────
# amjilsim_pass_date null, 큐 이벤트 {AMJILSIM, REJECTED_REQUEUE, 차수} → cancer 단계.
D(brand_kr="림카토주", ingredient_inn="anbalcabtagene autoleucel",
  manufacturer="큐로셀", msd_flag=0, tracking_priority="competitor_class",
  amjilsim_pass_date=None, negotiation_status=None,
  indication="r/r DLBCL·PMBCL CAR-T (국산 1호)", listing_type="신규",
  notes="암질심 5차(2026-05-27 HIRA brdBltNo=11808) 미설정. 재상정 희망 7/8 6차 암질심. "
        "장기 OS data 부재, RSA 구조 미확정, 한국 ATMP 등재 표준 부재. "
        "5/28 큐로셀 주가 -14.76%.",
  events=[dict(committee="AMJILSIM", state="REJECTED_REQUEUE",
               session_date="2026-05-27", n_th_attempt=1,
               evidence_url="HIRA brdBltNo=11808 (5차 암질심 미설정)")])
D(brand_kr="알레센자캡슐", ingredient_inn="alectinib", manufacturer="한국로슈",
  msd_flag=0, tracking_priority="competitor_class",
  amjilsim_pass_date=None, negotiation_status=None,
  indication="ALK+ NSCLC 보조요법", listing_type="확대",
  notes="암질심 5차(2026-05-27) 미설정. 재신청 보완 단계. adjuvant 장기투여 BIA 부담, "
        "NSCLC adjuvant 신규 인디케이션 재정영향↑.",
  events=[dict(committee="AMJILSIM", state="REJECTED_REQUEUE",
               session_date="2026-05-27", n_th_attempt=1,
               evidence_url="HIRA brdBltNo=11808 (5차 암질심 미설정)")])
D(brand_kr="키스칼리정", ingredient_inn="ribociclib", manufacturer="한국노바티스",
  msd_flag=0, tracking_priority="competitor_class",
  amjilsim_pass_date=None, negotiation_status=None,
  indication="HR+/HER2- 조기 유방암 보조", listing_type="확대",
  notes="암질심 5차(2026-05-27) 미설정. 재신청 보완 단계. NATALEE vs 버제니오 monarchE "
        "7년 OS 직접 비교 열위.",
  events=[dict(committee="AMJILSIM", state="REJECTED_REQUEUE",
               session_date="2026-05-27", n_th_attempt=1,
               evidence_url="HIRA brdBltNo=11808 (5차 암질심 미설정)")])
D(brand_kr="투키사정", ingredient_inn="tucatinib", manufacturer="한국화이자",
  msd_flag=0, tracking_priority="competitor_class",
  amjilsim_pass_date=None, negotiation_status=None,
  indication="HER2+ 유방암 (추정)", listing_type="신규",
  notes="암질심 4차(2026-04-15 HIRA brdBltNo=11770) 미설정. BIA 부담. 적응증 '추정'.",
  events=[dict(committee="AMJILSIM", state="REJECTED_REQUEUE",
               session_date="2026-04-15", n_th_attempt=1,
               evidence_url="HIRA brdBltNo=11770 (4차 암질심 미설정)")])
D(brand_kr="티루캡정", ingredient_inn="capivasertib", manufacturer="한국아스트라제네카",
  msd_flag=0, tracking_priority="competitor_class",
  amjilsim_pass_date=None, negotiation_status=None,
  indication="HR+/HER2- 유방암 (추정)", listing_type="신규",
  notes="암질심 4차(2026-04-15) 미설정. ICER 미충족. 적응증 '추정'.",
  events=[dict(committee="AMJILSIM", state="REJECTED_REQUEUE",
               session_date="2026-04-15", n_th_attempt=1,
               evidence_url="HIRA brdBltNo=11770 (4차 암질심 미설정)")])
D(brand_kr="킴리아주", ingredient_inn="tisagenlecleucel", manufacturer="한국노바티스",
  msd_flag=0, tracking_priority="competitor_class",
  amjilsim_pass_date=None, negotiation_status=None,
  indication="B세포 림프종 (추정)", listing_type="확대",
  notes="암질심 4차(2026-04-15) 미설정. 단가 부담, 적응증 확대 안건 불충분 evidence. "
        "CAR-T. 적응증 '추정'.",
  events=[dict(committee="AMJILSIM", state="REJECTED_REQUEUE",
               session_date="2026-04-15", n_th_attempt=1,
               evidence_url="HIRA brdBltNo=11770 (4차 암질심 미설정)")])
# 임델트라: 미설정 다회(2026-01차 + 매체). 1차 암질심(2026-01-21) 세션 링크.
D(brand_kr="임델트라", ingredient_inn="tarlatamab", manufacturer="암젠코리아",
  msd_flag=0, tracking_priority="competitor_class",
  amjilsim_pass_date=None, negotiation_status=None,
  indication="소세포폐암", listing_type="신규",
  notes="암질심 다회 미설정 (2026-01차 + 매체 보도). 재상정 진행 동향.",
  events=[dict(committee="AMJILSIM", state="REJECTED_REQUEUE",
               session_date="2026-01-21", n_th_attempt=1,
               evidence_url="2026-01차 암질심 미설정 + 매체 보도 (다회 미설정)")])
# 보류 추적: 윈도우 밖(2024-11-13 8차 암질심) but READ FULL FILE 지시 → 포함.
# 2024-11-13 8차 암질심 세션은 DB 미존재 → session 링크 없이 기록(session_id null),
# session_date 는 evidence/notes 로 보존(2025 외 세션 INSERT 범위 아님).
D(brand_kr="테크베일리", ingredient_inn="teclistamab", manufacturer="한국얀센",
  msd_flag=0, tracking_priority="competitor_class",
  amjilsim_pass_date=None, negotiation_status=None,
  indication="다발골수종 4차 단독요법", listing_type="신규",
  notes="2024-11-13 제8차 암질심 미설정 (메디파나 333852, 12개월 윈도우 직전 reference). "
        "사유: 임상적 유용성·경제성 미충족. 12개월 내 재상정 보도 부재. "
        "세션(2024 8차) DB 미존재로 session 링크 없이 기록.",
  events=[dict(committee="AMJILSIM", state="REJECTED_REQUEUE", session_date=None,
               queue_entry_date="2024-11-13", n_th_attempt=1,
               evidence_url="메디파나 333852 (2024-11-13 8차 암질심 미설정)")])
D(brand_kr="타그리소 확대", ingredient_inn="osimertinib", manufacturer="한국AZ",
  msd_flag=0, tracking_priority="competitor_class",
  amjilsim_pass_date=None, negotiation_status=None,
  indication="EGFR+ NSCLC 급여 확대", listing_type="확대",
  notes="2024-11-13 제8차 암질심 미설정. 12개월 내 재상정 보도 부재. "
        "세션(2024 8차) DB 미존재로 session 링크 없이 기록.",
  events=[dict(committee="AMJILSIM", state="REJECTED_REQUEUE", session_date=None,
               queue_entry_date="2024-11-13", n_th_attempt=1,
               evidence_url="2024-11-13 8차 암질심 미설정 (매체 보도)")])


# ──────────────────────────────────────────────────────────────────────────────
# Importer
# ──────────────────────────────────────────────────────────────────────────────

def _ensure_sessions(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    """누락된 2025 약평위 세션 INSERT(멱등). 반환: {(committee, date): session_id}."""
    for (year, oa, oo, sdate, committee, note) in MISSING_SESSIONS:
        exists = conn.execute(
            "SELECT session_id FROM amjilsim_sessions WHERE session_date = ?",
            (sdate,)).fetchone()
        if exists:
            continue
        conn.execute(
            """INSERT INTO amjilsim_sessions
               (year, ordinal_assumed, ordinal_official, session_date,
                status, committee_type, note)
               VALUES (?,?,?,?,?,?,?)""",
            (year, oa, oo, sdate, "COMPLETED", committee, note))
    conn.commit()
    rows = conn.execute(
        "SELECT session_id, committee_type, session_date FROM amjilsim_sessions"
    ).fetchall()
    return {(r["committee_type"], r["session_date"]): r["session_id"] for r in rows}


def _upsert_drug(conn: sqlite3.Connection, d: dict) -> int:
    """UPSERT on (brand_kr, ingredient_inn). 반환 drug_id."""
    cols = ["product_slug", "brand_kr", "brand_en", "ingredient_inn", "atc",
            "manufacturer", "msd_flag", "competitor_class", "tracking_priority",
            "amjilsim_pass_date", "yakpyungwi_pass_date", "negotiation_status",
            "indication", "listing_type", "submitted_date", "notes",
            "expected_session_id"]
    vals = {c: d.get(c) for c in cols}
    vals["tracking_priority"] = d.get("tracking_priority") or "generic_new_drug"
    vals["msd_flag"] = 1 if d.get("msd_flag") else 0

    existing = conn.execute(
        "SELECT drug_id FROM amjilsim_drugs WHERE brand_kr = ? "
        "AND ingredient_inn IS ?",
        (d["brand_kr"], d.get("ingredient_inn"))).fetchone()
    if existing:
        drug_id = existing["drug_id"]
        set_cols = [c for c in cols if c != "brand_kr"]
        conn.execute(
            f"UPDATE amjilsim_drugs SET {', '.join(f'{c}=?' for c in set_cols)} "
            f"WHERE drug_id = ?",
            [vals[c] for c in set_cols] + [drug_id])
        return drug_id
    cur = conn.execute(
        f"INSERT INTO amjilsim_drugs ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)})",
        [vals[c] for c in cols])
    return cur.lastrowid


def _upsert_event(conn: sqlite3.Connection, drug_id: int, ev: dict,
                  session_map: dict) -> bool:
    """큐 이벤트 멱등 INSERT. 동일 (drug_id, committee, state, session_id,
    n_th_attempt) 존재 시 skip. 반환: 새로 INSERT 했는가."""
    committee = ev["committee"]
    state = ev["state"]
    n_th = ev.get("n_th_attempt", 1)
    sdate = ev.get("session_date")
    session_id = ev.get("session_id")
    if session_id is None and sdate:
        session_id = session_map.get((committee, sdate))
        if session_id is None:
            raise RuntimeError(
                f"세션 미발견: {committee} {sdate} (drug_id={drug_id}) — "
                "세션 INSERT 또는 매핑 누락")
    queue_entry_date = ev.get("queue_entry_date") or sdate

    # 멱등 체크: session_id NULL 도 IS 비교
    dup = conn.execute(
        """SELECT id FROM amjilsim_drug_queue_status
           WHERE drug_id = ? AND committee_type = ? AND queue_state = ?
             AND session_id IS ? AND n_th_attempt = ?""",
        (drug_id, committee, state, session_id, n_th)).fetchone()
    if dup:
        # evidence/queue_entry_date 최신화(전사 보정)만
        conn.execute(
            "UPDATE amjilsim_drug_queue_status SET evidence_url = ?, "
            "queue_entry_date = ? WHERE id = ?",
            (ev.get("evidence_url"), queue_entry_date, dup["id"]))
        return False
    conn.execute(
        """INSERT INTO amjilsim_drug_queue_status
           (drug_id, session_id, queue_state, queue_entry_date,
            n_th_attempt, evidence_url, committee_type)
           VALUES (?,?,?,?,?,?,?)""",
        (drug_id, session_id, state, queue_entry_date, n_th,
         ev.get("evidence_url"), committee))
    return True


def run() -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        # expected_session_id 컬럼 멱등 보강
        cols_now = {r[1] for r in conn.execute("PRAGMA table_info(amjilsim_drugs)")}
        if "expected_session_id" not in cols_now:
            conn.execute("ALTER TABLE amjilsim_drugs ADD COLUMN expected_session_id INTEGER")
            conn.commit()

        session_map = _ensure_sessions(conn)
        n_sessions = conn.execute(
            "SELECT COUNT(*) c FROM amjilsim_sessions").fetchone()["c"]
        # date → session_id (session_date 전역 UNIQUE) — 상정 예정 후보 해석용
        date_to_session = {r["session_date"]: r["session_id"] for r in conn.execute(
            "SELECT session_id, session_date FROM amjilsim_sessions")}

        drugs_before = conn.execute(
            "SELECT COUNT(*) c FROM amjilsim_drugs").fetchone()["c"]
        events_added = 0
        drug_ids = set()
        for d in DRUGS:
            exp_date = EXPECTED_NEXT_SESSION.get(d["brand_kr"])
            d["expected_session_id"] = date_to_session.get(exp_date) if exp_date else None
            drug_id = _upsert_drug(conn, d)
            drug_ids.add(drug_id)
            for ev in d.get("events", []):
                if _upsert_event(conn, drug_id, ev, session_map):
                    events_added += 1
        conn.commit()

        drugs_after = conn.execute(
            "SELECT COUNT(*) c FROM amjilsim_drugs").fetchone()["c"]
        events_total = conn.execute(
            "SELECT COUNT(*) c FROM amjilsim_drug_queue_status").fetchone()["c"]
        return {
            "sessions_total": n_sessions,
            "drugs_before": drugs_before,
            "drugs_after": drugs_after,
            "drugs_imported": len(DRUGS),
            "drug_ids_touched": len(drug_ids),
            "events_added": events_added,
            "events_total": events_total,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    import json
    print(json.dumps(run(), ensure_ascii=False, indent=2))
