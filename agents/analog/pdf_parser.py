"""약제 등재 아날로그 — DREC Raw PDF 파서 (Regex 기반).

DREC Raw/ 의 평가결과 PDF 651개를 파싱, 구조화된 dict 반환.
Regex 로 추출 가능한 필드 전부 처리; 복잡 구조(OS/PFS, 질환분류, 정책의도)는
enrich.py LLM 단계에서 별도 처리.

스캔 PDF (2007-2012년): 텍스트 레이어 없음 → pdf_extractable=False, 파일명 메타만.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import signal
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
DREC_DIR = BASE_DIR / "DREC Raw"

# ── 정책 신호 키워드 ──────────────────────────────────────────────────────────

_POLICY_KEYWORDS = [
    "보장성 강화", "위험분담", "단일군 임상", "단일군",
    "경제성평가 생략", "재정소요", "국내 개발 신약", "국산 신약",
    "산정특례", "사용량-약가 연동", "사후관리", "희귀질환",
    "중증질환", "항암", "소아",
]

# ── Regex 패턴 ───────────────────────────────────────────────────────────────

_RE_TRIAL = re.compile(
    r'\[([A-Z][A-Z0-9]+(?:[\s\-][A-Z0-9]+)*(?:\s+\d+[A-Za-z0-9]*)?)\]'
)
_RE_FOREIGN = re.compile(r'A([78])\s*(?:국가\s*중\s*)?(\d+)\s*개국')
# 국가명 나열형: "A7 국가 중 미국, 영국, 이탈리아에 등재" / "A7 국가 모두에 등재"
_RE_FOREIGN_NAMED = re.compile(r'A([78])\s*국가\s*(중|모두)\s*([^.\n]{0,90}?)(?:에)?\s*등재')
# 제외국 등재 현황 섹션 (PDF 추출 시 '제 외국' 처럼 공백 끼는 경우 포함)
_RE_FOREIGN_SECTION = re.compile(
    r'제\s*외국\s*등재\s*현황(.{0,300}?)(?:\n\s*○|\n\s*▢|바른심사|Reference|$)', re.S
)
# A7/A8 국가군 (이태리=이탈리아, 이태리/이탈리아 표기 변형 포함)
_FOREIGN_COUNTRIES = [
    "미국", "영국", "독일", "프랑스", "이탈리아", "이태리", "스위스", "일본",
    "대만", "캐나다", "호주", "스페인", "네덜란드", "벨기에", "오스트리아",
    "스웨덴", "노르웨이", "덴마크", "핀란드", "아일랜드",
]
_RE_RSA = re.compile(r'위험\s*분담')
_RE_PE_WAIVER = re.compile(r'경제성평가\s*자료?\s*제출\s*생략')
_RE_POSTMARKET_COND = re.compile(r'\(사후관리\s*조건부\)\s*급여의\s*적정성이\s*있음')
_RE_POSTMARKET_KW = re.compile(r'사후관리\s*조건부|사후관리\s*계획|사후관리\s*조건')
_RE_APPROVED = re.compile(r'급여의\s*적정성이\s*있음')
_RE_REJECTED = re.compile(r'급여의\s*적정성이\s*없음')
_RE_COND_APPROVED = re.compile(r'(?:조건\s*부|조건부)\s*급여의\s*적정성이\s*있음')
# 구형(2007-2019) 결정 마커: "▢ 비급여" / "○ 비급여" 헤더, "...비급여함." 결정동사
_RE_REJECTED_LEGACY = re.compile(r'[▢○]\s*비급여|비급여\s*함')
# 구형 급여 결정 마커: "▢ 급여" / "○ 급여" 헤더, "...급여함." (비급여 아님 — 음수 lookbehind)
_RE_APPROVED_LEGACY = re.compile(r'[▢○]\s*급여(?:\s|$|[(가-힣])|(?<!비)급여\s*함')

# 섹션 분리 — 가. / 나. 변형 모두 지원
_RE_SEC_GA = re.compile(
    r'가\s*[\.．]\s*평가\s*결과\s*\n?(.*?)(?=나\s*[\.．]\s*평가|$)',
    re.DOTALL,
)
_RE_SEC_NA = re.compile(
    r'나\s*[\.．]\s*평가\s*내용\s*\n?(.+)',
    re.DOTALL,
)
# 효능효과 섹션 (page 1)
_RE_EFFECT = re.compile(
    r'(?:효능효과|효\s*능\s*효\s*과)\s*[:\n]\s*(.+?)'
    r'(?=(?:▢|심의|가\s*[\.．]|용법|약제급여|나\s*[\.．]|$))',
    re.DOTALL | re.IGNORECASE,
)
# 날짜 YYYY년 MM월 DD일
_RE_DATE_KO = re.compile(r'(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일')
# 사전심의 위원회 anchor (2종):
#  ① 암질환심의위원회 (암·중증질환 — 급여기준 설정): '암질환심의위원회' /
#     '중증(암)질환심의위원회' / 축약 '암질심'.
#  ② 약제급여기준소위원회 (일반약제 — 급여기준 설정): '약제급여기준소위원회' /
#     '급여기준소위원회' / '기준소위원회' + 사용자 표기변형 '약제결정기준소위원회'.
# (PDF 추출 시 공백 끼는 경우 포함). 기존 '암[질\s]*심' 은 '암질환심의' 의
# 환 때문에 매칭 실패 + DOTALL .*? 로 먼 날짜 오매칭 → anchor+window 방식으로 교체.
# 주의: '약제급여평가위원회'(약평위 = 최종 급여적정) 은 매칭 금지 (소위원회 only).
_RE_PRECOMMITTEE_ANCHOR = re.compile(
    r'(?P<cancer>중증\s*\(?\s*암\s*\)?\s*질환\s*심의\s*위원회'
    r'|암\s*질환\s*심의\s*위원회|암\s*질\s*심)'
    r'|(?P<general>약제\s*(?:급여|결정)\s*기준\s*소\s*위원회'
    r'|급여\s*기준\s*소\s*위원회|기준\s*소\s*위원회)'
)
_COMMITTEE_CANCER = "암질환심의위원회"
_COMMITTEE_GENERAL = "약제급여기준소위원회"
# 날짜 (한글형 2020년 6월 3일 / 점형 2011.7.20 / 2011. 7. 20)
_RE_DATE_KMD = re.compile(
    r'(\d{4})\s*[년.]\s*(\d{1,2})\s*[월.]\s*(\d{1,2})'
)
# RSA 유형 힌트
_RE_RSA_HWAN = re.compile(r'환급형|환급\s*형')
_RE_RSA_CHONG = re.compile(r'총액\s*제한|총액\s*한도')
# RSA 세부 조건 유형 (복수 가능) — 위험분담 약제에서 추출
_RSA_CONDITIONS = [
    ("환급형", re.compile(r'환급형|환급\s*형|총액\s*환급')),
    ("총액제한", re.compile(r'총액\s*제한|총액\s*한도|지출\s*상한')),
    ("근거생산 조건부", re.compile(r'근거\s*생산')),
    ("환자단위 사용량", re.compile(r'환자\s*단위')),
    ("성과기반", re.compile(r'성과\s*기반|성과\s*연동')),
    ("초기치료 한정", re.compile(r'초기\s*치료')),
]
# 제조사
_RE_MFGR = re.compile(
    r'제조회사\s*(?:\([^)]*\))?\s*[:：]\s*([^\n,]{2,60})'
)
# 의료적 필요성 (제6조)
_RE_MED_NEC = re.compile(
    r'제6조\s*제\d+항.{0,80}?(해당하지\s*아니|해당)',
    re.DOTALL,
)
# 파일명 연도/차수
_RE_FNAME_NEW = re.compile(r'(\d{4})년\s*제?(\d+)차')  # 2022년 제1차
_RE_FNAME_OLD = re.compile(r'(?<=[_\s])(\d{2})-(\d+)(?=[_\s.]|$)')  # _17-4_


# ── 내부 유틸 ────────────────────────────────────────────────────────────────

def _normalize(s: str) -> str:
    """macOS NFD → NFC."""
    return unicodedata.normalize('NFC', s)


def _fmt_date(y: int, mo: int, d: int) -> str:
    return f"{y:04d}-{mo:02d}-{d:02d}"


_MAX_PAGES = 30      # 평가결과 문서는 6-15페이지; 초과 시 잘못된 파일
_PAGE_TIMEOUT = 10   # extract_text() 페이지당 최대 10초


def _timeout_handler(signum, frame):
    raise TimeoutError("pdfplumber extract_text timeout")


def _extract_pages(pdf_path: Path) -> list[str]:
    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            if len(pdf.pages) > _MAX_PAGES:
                logger.warning("[pdf_parser] 페이지 수 초과 (%d) — 스킵: %s",
                               len(pdf.pages), pdf_path.name)
                return []
            for page in pdf.pages:
                try:
                    signal.signal(signal.SIGALRM, _timeout_handler)
                    signal.alarm(_PAGE_TIMEOUT)
                    text = page.extract_text() or ""
                    signal.alarm(0)
                except TimeoutError:
                    signal.alarm(0)
                    logger.warning("[pdf_parser] 페이지 타임아웃 — 스킵: %s", pdf_path.name)
                    return []
                pages.append(text)
        return pages
    except Exception as e:
        signal.alarm(0)
        logger.warning("[pdf_parser] 추출 실패 %s: %s", pdf_path.name, e)
        return []


def _is_extractable(pages: list[str]) -> bool:
    """스캔 PDF 탐지: 텍스트 레이어 없거나 CID 문자 과다."""
    full = "".join(pages[:3])
    if len(full.strip()) < 300:
        return False
    if full.count("(cid:") > 10:
        return False
    return True


# ── 파일명 파싱 ───────────────────────────────────────────────────────────────

def _parse_filename(fname: str) -> dict:
    """파일명에서 brand_name_raw, session_year, ordinal 추출.

    신형: {약제명}_{급여/비급여}_{연도차수}_평가결과.pdf
    구형: {약제명}_{급여/비급여}_{연도차수}_{약제명반복}.pdf
    연도차수 패턴을 파일명 전체에서 regex 검색해 위치에 무관하게 추출.
    """
    stem = _normalize(fname)
    # .pdf 제거
    if stem.lower().endswith('.pdf'):
        stem = stem[:-4]
    # _평가결과 제거
    for suffix in ('_평가결과', '_평가결과'):
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
            break

    # 연도/차수 파싱 — 파일명 전체에서 첫 번째 매칭
    year = ordinal = None
    m = _RE_FNAME_NEW.search(stem)
    if m:
        year, ordinal = int(m.group(1)), int(m.group(2))
        year_ord_str = m.group(0)
    else:
        m = _RE_FNAME_OLD.search(stem)
        if m:
            year, ordinal = 2000 + int(m.group(1)), int(m.group(2))
            year_ord_str = m.group(0)
        else:
            year_ord_str = None

    # 브랜드명: 연도차수 앞부분 + 급여/비급여 제거
    if year_ord_str:
        idx = stem.find(year_ord_str)
        brand_region = stem[:idx] if idx > 0 else stem
    else:
        brand_region = stem

    # _ 로 분리, 급여/비급여 제거
    reimbursable = None
    name_parts = []
    for p in brand_region.split('_'):
        if p in ('급여', '비급여'):
            reimbursable = (p == '급여')
        elif p:
            name_parts.append(p)

    brand_raw = '_'.join(name_parts) if name_parts else stem

    return {
        'brand_name_raw': brand_raw,
        'session_year': year,
        'ordinal': ordinal,
        'reimbursable_requested': reimbursable,
    }


def _clean_brand(raw: str) -> str:
    """괄호(INN/함량), 강도 수치 제거 → 제품명만."""
    name, _dose = _split_brand_dosage(raw)
    return name


# 용량(강도) 단위 — 한글·라틴 표기 모두 포함
_DOSAGE_UNIT = (
    r'(?:밀리그램|밀리그람|마이크로그램|나노그램|밀리리터|마이크로리터|그램|'
    r'국제단위|만단위|단위|퍼센트|mg|mcg|μg|㎍|ng|kg|mL|ml|L|g|IU|I\.?U\.?|MBq|%)'
)
# 제품명 끝의 용량 토큰: "100밀리그램", "0.4%", "0.5mg", "500단위", "0.25mg-0.5ml"
_RE_DOSAGE_TAIL = re.compile(
    r'\s*('
    r'\d+(?:[.,]\d+)?\s*' + _DOSAGE_UNIT +
    r'(?:\s*[/\-]\s*\d+(?:[.,]\d+)?\s*' + _DOSAGE_UNIT + r')?'
    r')\s*$'
)
# brand_name_raw 의 함량 suffix: "_(150I.U-0.5mL)" / "_(0.25mg-0.5mL)"
_RE_UNIT_SUFFIX = re.compile(r'_\(([^)]*)\)\s*$')
# 파일명 잔여 잡음: "_2013-11_fingolimod_hydrochlo" / "_조건부비급여" / "_급여" 류
_RE_NAME_JUNK = re.compile(
    r'_\d{4}-\d{2}.*$'              # _연도-월_INN
    r'|_[A-Za-z][A-Za-z_]*$'       # _영문성분명
    r'|_(?:조건부)?(?:비)?급여.*$'   # _급여/_비급여/_조건부비급여
)


def _norm_dose(s: str | None) -> str | None:
    """용량 표기 공백 정규화 ('0.5 mg' → '0.5mg', ', ' 보정)."""
    if not s:
        return None
    s = re.sub(r'\s+', '', s).replace(',', '.')
    return s or None


def _split_brand_dosage(raw: str) -> tuple[str, str | None]:
    """brand_name_raw → (제품명, 용량). 괄호(INN)·_(...)함량·강도 토큰 분리.

    예) '가드렛정100밀리그램(아나클립틴)'              → ('가드렛정', '100밀리그램')
        '그라나텍점안액0.4%(리파스딜...)_(24.48mg-5mL)' → ('그라나텍점안액', '0.4%')
        '고나도핀...시린지(...)_(150I.U-0.5mL)'         → ('고나도핀...시린지', '150I.U-0.5mL')
        '가브스정50mg'                                  → ('가브스정', '50mg')
    """
    if not raw:
        return raw, None
    name = raw.strip()
    # ① 함량 suffix '_(...)' 분리 (fallback 용량 후보)
    unit_content = None
    sm = _RE_UNIT_SUFFIX.search(name)
    if sm:
        unit_content = sm.group(1).strip()
        name = name[:sm.start()].strip()
    # ② 괄호(INN) 제거
    name = re.sub(r'\([^)]*\)', '', name).strip()
    # ③ 파일명 잔여 잡음 + 짝 없는 괄호 제거
    name = _RE_NAME_JUNK.sub('', name).strip()
    name = name.rstrip(')）(（ 　').strip()
    # ④ 제품명 끝 용량 토큰 분리
    dose_main = None
    dm = _RE_DOSAGE_TAIL.search(name)
    if dm:
        dose_main = dm.group(1).strip()
        name = name[:dm.start()].strip()
    name = re.sub(r'[-_\s]+$', '', name).strip()
    # ⑤ 용량 결정: 본문 토큰 우선, 없으면 _(...) 함량 suffix
    dosage = dose_main
    if not dosage and unit_content and re.search(r'\d', unit_content):
        dosage = unit_content
    return (name or raw), _norm_dose(dosage)


# ── 섹션 추출 ─────────────────────────────────────────────────────────────────

def _extract_effect_text(page1: str) -> str | None:
    m = _RE_EFFECT.search(page1)
    if not m:
        return None
    text = m.group(1).strip()
    return text[:5000] if len(text) >= 20 else None


def _extract_manufacturer(page1: str) -> str | None:
    m = _RE_MFGR.search(page1)
    return m.group(1).strip() if m else None


def _extract_generic_name(page1: str) -> str | None:
    """INN (영문 성분명) 추출."""
    # 약제명(브랜드(INN)) 이중 괄호 패턴
    m = re.search(r'약제명\s*\([^(\n]+?\(([^)\n]+)\)\s*\)', page1)
    if m:
        inner = m.group(1).strip()
        if re.match(r'[a-zA-Z]', inner):
            return inner.lower()
    # 페이지 상단 라인에서 INN suffix 패턴
    for line in page1.split('\n')[:25]:
        m = re.search(
            r'\b([a-z]{5,}(?:mab|nib|zib|zumab|tinib|lib|cab|rib|cept|'
            r'fungin|vir|lukast|fenib|lanib|ciclib|palbociclib))\b',
            line, re.IGNORECASE
        )
        if m:
            return m.group(1).lower()
    return None


def _extract_session_date(page1: str, session_year: int | None) -> str | None:
    dates = []
    for m in _RE_DATE_KO.finditer(page1):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 2005 <= y <= 2030 and 1 <= mo <= 12 and 1 <= d <= 31:
            dates.append(_fmt_date(y, mo, d))
    if session_year and dates:
        matching = [dt for dt in dates if dt.startswith(str(session_year))]
        if matching:
            return matching[0]
    return dates[0] if dates else None


def _extract_review_result(ga_text: str, full_text: str) -> str:
    src = ga_text or full_text or ""
    # 신형(2020+) 표준 표현 우선
    if _RE_POSTMARKET_COND.search(src):
        return "APPROVED_WITH_POSTMARKET"
    if _RE_COND_APPROVED.search(src):
        return "CONDITIONAL_APPROVED"
    if _RE_APPROVED.search(src):
        return "APPROVED"
    if _RE_REJECTED.search(src):
        return "REJECTED"
    # 구형(2007-2019) 결정 마커 — 판정문 head(가. 평가 결과 상위 300자)로 한정.
    # 비교약제 논의의 "비급여" 언급 오탐 방지 위해 ga_text 만 사용 (full_text 제외).
    head = (ga_text or "")[:300]
    if _RE_REJECTED_LEGACY.search(head):
        return "REJECTED"
    if _RE_APPROVED_LEGACY.search(head):
        return "APPROVED"
    return "UNKNOWN"


def _extract_rsa_type_hint(text: str) -> str | None:
    if _RE_RSA_HWAN.search(text):
        return "환급형"
    if _RE_RSA_CHONG.search(text):
        return "총액제한"
    if _RE_RSA.search(text):
        return "기타"
    return None


def _extract_rsa_conditions(text: str) -> list[str]:
    """위험분담 약제의 세부 조건 유형 (복수 가능). has_rsa 인 경우만 의미."""
    if not text or not _RE_RSA.search(text):
        return []
    out = [label for label, rx in _RSA_CONDITIONS if rx.search(text)]
    return out


def _count_countries(blob: str) -> int:
    """국가명 나열 문자열에서 distinct 제외국 수 (이태리=이탈리아 정규화)."""
    return len({c.replace("이태리", "이탈리아")
                for c in _FOREIGN_COUNTRIES if c in blob})


def _infer_basis(text: str, year: int | None) -> int:
    """A8/A7 토큰 우선 → 없으면 연도로 추정 (2021~ A8, 이전 A7)."""
    if re.search(r'A8', text):
        return 8
    if re.search(r'A7', text):
        return 7
    return 8 if (year and year >= 2021) else 7


def _extract_foreign_listing(text: str, year: int | None = None) -> tuple[int | None, int | None]:
    """제외국(A7/A8) 등재국가수 추출 → (count, basis).

    ① 'A7 국가 중 3개국' 숫자형 → 직접.
    ② 'A7 국가 중 미국, 영국, 이탈리아에 등재' 국가명 나열형 → 국가 수 카운트.
    ③ 'A7 국가 모두에 등재' → basis 와 동일.
    ④ '제외국 등재 현황: 신청품은 미국, 프랑스 ... 등에 등재' (A 마커 없음)
       → 섹션 내 국가명 카운트. 한국은 급여 등재 시 제외국 급여내역 요건이 있어
       제외국 섹션이 있으면 국가 나열을 반드시 카운트한다.
    """
    if not text:
        return None, None
    m = _RE_FOREIGN.search(text)
    if m:
        return int(m.group(2)), int(m.group(1))
    nm = _RE_FOREIGN_NAMED.search(text)
    if nm:
        basis = int(nm.group(1))
        if nm.group(2) == "모두":
            return basis, basis
        cnt = _count_countries(nm.group(3) or "")
        if cnt:
            return cnt, basis
    # ④ 제외국 등재 현황 섹션 (A 마커 없이 국가만 나열)
    sec = _RE_FOREIGN_SECTION.search(text)
    if sec:
        cnt = _count_countries(sec.group(1) or "")
        if cnt:
            return cnt, _infer_basis(text, year)
    return None, None


def _extract_clinical_trials(text: str) -> list[str]:
    trials, seen = [], set()
    for m in _RE_TRIAL.finditer(text):
        name = m.group(1).strip()
        if len(name) >= 3 and re.search(r'[A-Z]', name) and name not in seen:
            # 일반 단어 제외 (AND, OR, IF 등 2자 이하 이미 제외)
            if not re.fullmatch(r'[A-Z]{1,3}', name):
                trials.append(name)
                seen.add(name)
    return trials[:20]


def _extract_policy_signals(text: str) -> list[str]:
    return [kw for kw in _POLICY_KEYWORDS if kw in text]


def _extract_amjilsim_history(text: str) -> list[dict]:
    """사전심의 위원회 심의일 추출 — 암질환심의위원회 + 약제급여기준소위원회.

    anchor 직후 ~90자 윈도우 내 날짜만 채택. 각 항목에 committee 유형 태그.
    예) '암질환심의위원회 심의일: 2020년 6월 3일, 2020년 8월 26일'
        '약제급여기준소위원회(2021.7.20)'
    먼 곳의 약평위 날짜 오매칭을 막기 위해 anchor 근접 윈도우로 한정.
    반환: [{"date": "YYYY-MM-DD", "committee": "암질환심의위원회"|"약제급여기준소위원회"}]
    """
    if not text:
        return []
    history, seen = [], set()
    for am in _RE_PRECOMMITTEE_ANCHOR.finditer(text):
        committee = _COMMITTEE_CANCER if am.group("cancer") else _COMMITTEE_GENERAL
        window = text[am.end():am.end() + 90]
        for m in _RE_DATE_KMD.finditer(window):
            try:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            except (ValueError, IndexError):
                continue
            if 2005 <= y <= 2030 and 1 <= mo <= 12 and 1 <= d <= 31:
                dt = _fmt_date(y, mo, d)
                key = (dt, committee)
                if key not in seen:
                    history.append({"date": dt, "committee": committee})
                    seen.add(key)
    return history[:10]


def _extract_postmarket_detail(ga_text: str) -> str | None:
    if not ga_text:
        return None
    sentences = [
        s.strip() for s in re.split(r'[.。\n]', ga_text)
        if '사후관리' in s and len(s.strip()) > 10
    ]
    return '. '.join(sentences[:3])[:500] if sentences else None


def _extract_medical_necessity(full_text: str) -> str | None:
    m = _RE_MED_NEC.search(full_text)
    if not m:
        return None
    return "불필수" if "해당하지" in m.group(0) else "필수"


# ── 등재 트랙 한국어화 ────────────────────────────────────────────────────────

def determine_track_ko(text: str, has_rsa: bool, pe_waiver: bool,
                       has_postmarket: bool = False) -> str:
    """평가 내용 텍스트에서 등재 트랙 한국어 레이블 결정."""
    if pe_waiver:
        base = "경제성평가 생략 (PE Waiver)"
    elif re.search(r'QALY|ICER|비용효용', text):
        base = "비용효용분석 (CUA)"
    elif re.search(r'가중평균가', text) and re.search(r'대체약제', text):
        base = "대체약제 가중평균가 (WAP)"
    elif re.search(r'A[78]', text) and re.search(r'조정평균가', text):
        base = "외국조정평균가"
    elif re.search(r'비용\s*효과(?:성|적)', text):
        # CUA(QALY/ICER) 아닌 일반 비용효과성 수용 케이스
        base = "비용효과 입증"
    else:
        base = "기타"

    suffix = []
    if has_rsa:
        suffix.append("위험분담제")
    if has_postmarket:
        suffix.append("사후관리")
    return (base + " + " + " + ".join(suffix)) if suffix else base


# ── 메인 파서 ─────────────────────────────────────────────────────────────────

def parse_drec_pdf(path: Path) -> dict:
    """DREC Raw/ 평가결과 PDF → 구조화 dict.

    pdf_extractable=False 인 경우 파일명 메타만 반환.
    """
    fname = _normalize(path.name)
    fn_info = _parse_filename(fname)
    file_hash = hashlib.sha1(path.read_bytes()).hexdigest()

    pages = _extract_pages(path)
    extractable = _is_extractable(pages)

    _brand_clean, _dosage = _split_brand_dosage(fn_info["brand_name_raw"])
    base: dict = {
        "file_name": fname,
        "file_hash": file_hash,
        "brand_name_raw": fn_info["brand_name_raw"],
        "brand_name": _brand_clean,
        "dosage": _dosage,
        "session_year": fn_info["session_year"],
        "ordinal": fn_info["ordinal"],
        "reimbursable_requested": fn_info["reimbursable_requested"],
        "pdf_extractable": extractable,
    }

    if not extractable:
        base.update({
            "generic_name_en": None,
            "manufacturer": None,
            "mfds_effect_text": None,
            "session_date": f"{fn_info['session_year']}-01-01" if fn_info.get("session_year") else None,
            "review_result": "UNKNOWN",
            "has_rsa": False,
            "pe_waiver": False,
            "has_postmarket_condition": False,
            "postmarket_condition_detail": None,
            "rsa_type_hint": None,
            "foreign_listing_count": None,
            "foreign_listing_basis": None,
            "medical_necessity": None,
            "committee_history": "[]",
            "amjilsim_history": "[]",
            "clinical_trials": "[]",
            "policy_signals": "[]",
            "decision_reason": None,
            "body_text": None,
            "reimbursement_track_ko": "기타",
        })
        return base

    full_text = "\n".join(pages)
    page1 = pages[0] if pages else ""

    # 섹션 분리
    ga_m = _RE_SEC_GA.search(full_text)
    na_m = _RE_SEC_NA.search(full_text)
    ga_text = ga_m.group(1).strip() if ga_m else ""
    body_text = na_m.group(1).strip() if na_m else ""
    if not ga_text:
        ga_text = full_text[:4000]

    session_date = _extract_session_date(page1, fn_info.get("session_year"))

    # A7/A8 국가 수 (숫자형 + 국가명 나열형 + 모두 처리)
    foreign_count, foreign_basis = _extract_foreign_listing(full_text, fn_info.get("session_year"))

    # pe_waiver 만 판정문(가. 평가 결과)으로 한정 — 나. 평가내용의 위험분담제 유형
    # 분류표("④ 경제성평가자료 제출 생략")가 모든 RSA 약제에 PE Waiver 오탐 유발
    # (Keytruda/졸겐스마 오인). has_rsa 는 full_text 유지 — 킴리아처럼 판정문 RSA
    # 문구가 영업비밀로 마스킹(공란)된 경우 body 의 "위험분담" 으로만 포착 가능.
    decision_src = ga_text or full_text
    has_rsa = bool(_RE_RSA.search(full_text))
    pe_waiver = bool(_RE_PE_WAIVER.search(decision_src))
    has_postmarket = bool(_RE_POSTMARKET_KW.search(full_text))
    review_result = _extract_review_result(ga_text, full_text)

    committee_history = []
    if session_date:
        committee_history = [{
            "type": "약평위",
            "date": session_date,
            "ordinal": fn_info.get("ordinal"),
            "result": review_result,
        }]

    full_body = (body_text or full_text)
    track_ko = determine_track_ko(
        full_text, has_rsa=has_rsa, pe_waiver=pe_waiver, has_postmarket=has_postmarket
    )

    base.update({
        "generic_name_en": _extract_generic_name(page1),
        "manufacturer": _extract_manufacturer(page1),
        "mfds_effect_text": _extract_effect_text(page1),
        "session_date": session_date,
        "review_result": review_result,
        "has_rsa": has_rsa,
        "pe_waiver": pe_waiver,
        "has_postmarket_condition": has_postmarket,
        "postmarket_condition_detail": _extract_postmarket_detail(ga_text) if has_postmarket else None,
        "rsa_type_hint": _extract_rsa_type_hint(full_text),
        "rsa_types": _extract_rsa_conditions(full_text),
        "foreign_listing_count": foreign_count,
        "foreign_listing_basis": foreign_basis,
        "medical_necessity": _extract_medical_necessity(full_text),
        "committee_history": json.dumps(committee_history, ensure_ascii=False),
        "amjilsim_history": json.dumps(_extract_amjilsim_history(full_body), ensure_ascii=False),
        "clinical_trials": json.dumps(_extract_clinical_trials(full_text), ensure_ascii=False),
        "policy_signals": json.dumps(_extract_policy_signals(full_text), ensure_ascii=False),
        "decision_reason": ga_text[:10000] if ga_text else None,
        "body_text": body_text[:30000] if body_text else full_text[:15000],
        "reimbursement_track_ko": track_ko,
    })
    return base


# ── 유틸: DREC Raw PDF 목록 ───────────────────────────────────────────────────

def find_evaluation_pdfs(drec_dir: Path = DREC_DIR) -> list[Path]:
    """DREC Raw/ 에서 평가결과 PDF 목록 (NFC 정규화).

    포함 기준: 급여/비급여 포함 + '회의자료' 제외.
    구형 파일({약제명}_급여_{연도차수}_{약제명}.pdf) 도 포함.
    """
    found = []
    if not drec_dir.exists():
        logger.error("DREC Raw/ 없음: %s", drec_dir)
        return found
    for f in drec_dir.iterdir():
        norm = _normalize(f.name)
        if not norm.lower().endswith('.pdf'):
            continue
        if '회의자료' in norm:
            continue
        # 급여 또는 비급여 포함 (평가결과 파일 공통 마커)
        if '급여' in norm or '평가결과' in norm:
            found.append(f)
    return sorted(found, key=lambda p: _normalize(p.name))


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        result = parse_drec_pdf(p)
        result.pop("body_text", None)
        result.pop("decision_reason", None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        pdfs = find_evaluation_pdfs()
        print(f"평가결과 PDF: {len(pdfs)}개")
