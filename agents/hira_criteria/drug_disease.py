"""일반약제 항목 → 급여 대상 질환 KCD 매핑.

단계
1) 후보 만들기(결정적): 큐레이션 동의어 사전 + 상병마스터 표제어/동의어의 본문 등장(문자열 매칭).
2) LLM(선택): 후보 목록을 주고 '급여 대상 질환'인 것만 고르게 한다(후보 밖 코드 금지). 후보로 덮이지 않는
   대상 질환은 용어만 돌려받아 상병마스터 이름 검색으로 후보를 만든 뒤 한 번 더 고르게 한다.
3) 저장 전 검증: kcd_code 에 존재하지 않는 코드는 버린다. confidence<0.8 → needs_review.
- 항암 공고 위임 문장('「암환자에게 처방·투여하는 약제…」 범위 내')은 질환 매핑 대신 항목 플래그 onco_delegated.
- LLM 키는 config/.env OPENAI_API_KEY (출력 금지). 키가 없거나 호출 실패면 1) 결과만(name_match) 저장.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parents[2]
MODEL = os.environ.get("HIRA_CRITERIA_LLM_MODEL", "gpt-4o-mini")

ONCO_DELEGATE_RE = re.compile(r"암환자에게\s*처방\s*[·ㆍ･․]?\s*투여하는\s*약제에\s*대한[\s\S]{0,60}?범위")

# 급여기준에 자주 나오는 표현 → KCD(3단 위주). 코드는 저장 전 kcd_code 존재 검증을 다시 거친다.
CURATED: dict[str, list[str]] = {
    "류마티스 관절염": ["M05", "M06"], "류마티스관절염": ["M05", "M06"],
    "소아 특발성 관절염": ["M08"], "건선성 관절염": ["L405", "M07"], "건선": ["L40"], "판상건선": ["L400"],
    "강직성 척추염": ["M45"], "강직성척추염": ["M45"], "크론병": ["K50"], "궤양성 대장염": ["K51"], "궤양성대장염": ["K51"],
    "베체트": ["M352"], "아토피피부염": ["L20"], "아토피 피부염": ["L20"], "화농성 한선염": ["L732"],
    "천식": ["J45"], "만성폐쇄성폐질환": ["J44"], "만성 폐쇄성 폐질환": ["J44"], "특발성 폐섬유증": ["J841"],
    "만성 B형간염": ["B181"], "B형간염": ["B16", "B181"], "만성 C형간염": ["B182"], "C형간염": ["B171", "B182"],
    "HIV": ["B20", "B21", "B22", "B23", "B24"], "후천성면역결핍증": ["B20", "B21", "B22", "B23", "B24"],
    "제2형 당뇨병": ["E11"], "당뇨병": ["E10", "E11"], "제1형 당뇨병": ["E10"], "고혈압": ["I10"], "본태성 고혈압": ["I10"],
    "고지혈증": ["E78"], "이상지질혈증": ["E78"], "폐동맥고혈압": ["I270"], "심부전": ["I50"], "심방세동": ["I48"],
    "골다공증": ["M80", "M81"], "다발성 경화증": ["G35"], "다발성경화증": ["G35"], "시신경척수염": ["G360"],
    "척수성 근위축증": ["G120", "G121"], "알츠하이머": ["G30", "F00"], "치매": ["F00", "F01", "F02", "F03"],
    "파킨슨": ["G20"], "뇌전증": ["G40"], "편두통": ["G43"], "기면증": ["G474"], "주의력결핍 과잉행동장애": ["F900"],
    "조현병": ["F20"], "정신분열병": ["F20"], "우울증": ["F32", "F33"], "양극성": ["F31"],
    "신경병증성 통증": ["G629", "M792"], "섬유근육통": ["M797"], "대상포진": ["B02"],
    "습성 연령관련 황반변성": ["H3532"], "황반변성": ["H353"], "당뇨병성 황반부종": ["H360", "E113"],
    "망막정맥폐쇄": ["H348"], "건성안": ["H041"], "안구건조증": ["H041"],
    "만성 신부전": ["N18"], "만성신장병": ["N18"], "신성빈혈": ["D631"], "이차성 부갑상선기능항진증": ["N258"],
    "혈우병": ["D66", "D67"], "혈우병 A": ["D66"], "혈우병 B": ["D67"], "면역성 혈소판감소증": ["D693"],
    "특발성 혈소판감소성 자반증": ["D693"], "발작성 야간 혈색소뇨증": ["D595"], "비정형 용혈성 요독 증후군": ["D593"],
    "이식편대숙주질환": ["T860"], "장기이식": ["Z94"], "신장이식": ["Z940"], "간이식": ["Z944"],
    "성장호르몬 결핍": ["E230"], "터너증후군": ["Q96"], "말단비대증": ["E220"], "쿠싱병": ["E240"],
    "고셔병": ["E752"], "파브리병": ["E752"], "폼페병": ["E740"], "뮤코다당증": ["E76"], "저인산증": ["E833"],
    "아밀로이드증": ["E85"], "트랜스티레틴": ["E851", "E858"], "결핵": ["A15", "A16", "A17", "A18", "A19"],
    "비결핵항산균": ["A31"], "헬리코박터": ["B980"], "인플루엔자": ["J09", "J10", "J11"], "코로나바이러스감염증-19": ["U071"],
    "COVID-19": ["U071"], "거대세포바이러스": ["B25"], "침습성 아스페르길루스증": ["B44"], "칸디다": ["B37"],
    "패혈증": ["A41"], "폐렴": ["J18"], "요로감염": ["N390"], "위식도역류질환": ["K21"], "역류성 식도염": ["K210"],
    "소화성궤양": ["K27"], "위궤양": ["K25"], "십이지장궤양": ["K26"], "과민성 장증후군": ["K58"],
    "간성혼수": ["K729"], "간성뇌증": ["K729"], "과활동성 방광": ["N328"], "양성 전립선비대증": ["N40"],
    "전립선비대증": ["N40"], "불임": ["N97"], "난임": ["N97"], "자궁내막증": ["N80"], "폐경": ["N951"],
    "중증 근무력증": ["G700"], "길랑바레": ["G610"], "만성 염증성 탈수초성 다발신경병증": ["G618"],
    "유전성 혈관부종": ["D841"], "만성 특발성 두드러기": ["L501"], "두드러기": ["L50"], "알레르기 비염": ["J303", "J304"],
    "알레르기성 비염": ["J303", "J304"], "호산구성": ["J82"], "비용종": ["J33"], "골관절염": ["M15", "M16", "M17"],
    "통풍": ["M10"], "전신홍반루푸스": ["M321"], "루푸스": ["M32"], "쇼그렌": ["M350"], "전신경화증": ["M34"],
    "거대세포 동맥염": ["M315"], "류마티스 다발근통": ["M353"], "고칼륨혈증": ["E875"], "고인산혈증": ["E833"],
    "철결핍성 빈혈": ["D50"], "빈혈": ["D64"], "호중구감소": ["D70"], "혈전색전증": ["I26", "I80", "I82"],
    "심부정맥혈전증": ["I802"], "폐색전증": ["I26"], "뇌졸중": ["I63", "I64"], "뇌경색": ["I63"], "급성 심근경색": ["I21"],
    "협심증": ["I20"], "말초동맥질환": ["I739"], "레이노": ["I730"], "녹내장": ["H40"], "백내장": ["H25", "H26"],
    "포도막염": ["H20", "H30"], "신경섬유종증": ["Q850"], "결절성 경화증": ["Q851"], "듀센": ["G710"],
    "근위축성 측삭경화증": ["G122"], "헌팅턴": ["G10"], "레녹스-가스토": ["G404"], "드라베": ["G404"],
}

STOP_ALIASES = {"기타", "통증", "상세불명", "질환", "증후군", "감염", "염증", "출혈", "부종", "발열", "피로", "기침",
                "구토", "설사", "변비", "두통", "환자", "투여", "합병증", "임신", "수술", "이식", "신생물", "악성 신생물"}


# ── 후보 ──────────────────────────────────────────────────────────────────────

class KcdIndex:
    """상병마스터 이름 → 코드 (3·4단). 본문 문자열 매칭용."""

    def __init__(self, conn: sqlite3.Connection, schema: str = "common"):
        self.conn, self.schema = conn, schema
        rows = conn.execute(f"SELECT a.code, a.name_kr FROM {schema}.kcd_alias a WHERE length(a.code) BETWEEN 3 AND 4").fetchall()
        self.names: dict[str, set[str]] = {}
        for code, nm in rows:
            nm = (nm or "").strip()
            if len(nm) < 4 or nm in STOP_ALIASES or nm.startswith(("상세불명", "기타")):
                continue
            self.names.setdefault(nm, set()).add(code)
        self.code_name = dict(conn.execute(f"SELECT code, name_kr FROM {schema}.kcd_code").fetchall())

    def exists(self, code: str) -> bool:
        return code in self.code_name

    def name(self, code: str) -> str:
        return self.code_name.get(code, "")

    def search(self, term: str, limit: int = 12) -> list[tuple[str, str]]:
        q = f"%{term}%"
        rows = self.conn.execute(
            f"SELECT DISTINCT a.code FROM {self.schema}.kcd_alias a WHERE a.name_kr LIKE ? "
            f"ORDER BY length(a.code), a.code LIMIT ?", (q, limit)).fetchall()
        return [(r[0], self.name(r[0])) for r in rows]


def candidates(text: str, idx: KcdIndex) -> list[dict]:
    """본문 → [{code, name, label, source}] (중복 제거, 큐레이션 우선)."""
    out: dict[str, dict] = {}
    flat = re.sub(r"\s+", " ", text or "")
    nospace = flat.replace(" ", "")
    for term, codes in CURATED.items():
        if term.replace(" ", "") in nospace:
            for c in codes:
                if idx.exists(c) and c not in out:
                    out[c] = {"code": c, "name": idx.name(c), "label": term, "source": "curated"}
    for nm, codes in idx.names.items():
        if nm in flat:
            for c in codes:
                if c not in out:
                    out[c] = {"code": c, "name": idx.name(c), "label": nm, "source": "kcd_name"}
    return list(out.values())


def is_onco_delegated(text: str) -> bool:
    return bool(ONCO_DELEGATE_RE.search(text or ""))


# ── LLM ───────────────────────────────────────────────────────────────────────

def _api_key() -> Optional[str]:
    k = os.environ.get("OPENAI_API_KEY")
    if k:
        return k
    env = BASE_DIR / "config" / ".env"
    if env.exists():
        for ln in env.read_text().splitlines():
            if ln.startswith("OPENAI_API_KEY="):
                return ln.split("=", 1)[1].strip().strip("'\"") or None
    return None


class LLM:
    def __init__(self):
        self.key = _api_key()
        self.calls = 0
        self.errors = 0
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(self.key)

    def chat_json(self, system: str, user: str) -> Optional[dict]:
        body = json.dumps({"model": MODEL, "temperature": 0, "response_format": {"type": "json_object"},
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
        for attempt in range(3):
            try:
                req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=body,
                                             headers={"Authorization": f"Bearer {self.key}",
                                                      "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    data = json.loads(r.read())
                with self._lock:
                    self.calls += 1
                return json.loads(data["choices"][0]["message"]["content"])
            except Exception:  # noqa: BLE001
                time.sleep(2 * (attempt + 1))
        with self._lock:
            self.errors += 1
        return None


SYS = ("너는 한국 건강보험 약제 급여기준을 읽고 '급여(요양급여 인정) 대상 질환'을 KCD 코드로 고르는 전문가다. "
       "반드시 주어진 후보 코드 목록 안에서만 고른다. 제외 대상·병용금지·참고 문구에만 등장하는 질환은 고르지 않는다. "
       "JSON 으로만 답한다.")


def _llm_select(llm: LLM, title: str, text: str, cands: list[dict]) -> Optional[dict]:
    lst = "\n".join(f"- {c['code']}: {c['name']} (본문표현: {c['label']})" for c in cands[:60]) or "(없음)"
    user = (f"[약제] {title}\n[급여기준 원문]\n{text[:10000]}\n\n[후보 KCD]\n{lst}\n\n"
            '출력: {"selected":[{"code":"후보코드","label":"원문 질환표현","confidence":0.0~1.0}],'
            '"uncovered_terms":["후보로 덮이지 않는 급여 대상 질환 한국어 표현"]}. '
            "급여 대상 질환이 없는 행정 기준(용량·기간·처방방법 등)이면 둘 다 빈 배열.")
    return llm.chat_json(SYS, user)


def map_item(item: dict, idx: KcdIndex, llm: Optional[LLM]) -> tuple[list[dict], list[str]]:
    """반환: (매핑 행들, 항목 플래그)."""
    text = item.get("criteria_text") or ""
    title = item.get("title") or ""
    flags = []
    if is_onco_delegated(text):
        flags.append("onco_delegated")
    cands = candidates(title + "\n" + text, idx)
    rows: list[dict] = []
    if llm and llm.available:
        res = _llm_select(llm, title, text, cands)
        if res is not None:
            allowed = {c["code"] for c in cands}
            for s in res.get("selected") or []:
                code = str(s.get("code", "")).replace(".", "").strip()
                if code in allowed and idx.exists(code):
                    rows.append({"kcd_code": code, "disease_label": s.get("label") or "", "method": "llm",
                                 "confidence": float(s.get("confidence") or 0.5)})
            terms = [t for t in (res.get("uncovered_terms") or []) if isinstance(t, str) and t.strip()][:6]
            if terms:
                extra = []
                for t in terms:
                    for code, nm in idx.search(t.strip()):
                        if code not in allowed:
                            extra.append({"code": code, "name": nm, "label": t, "source": "search"})
                            allowed.add(code)
                    # 짧게 줄인 검색(예: '중등도-중증 판상건선' → '판상건선')
                    short = t.strip().split()[-1]
                    if short != t.strip():
                        for code, nm in idx.search(short, 6):
                            if code not in allowed:
                                extra.append({"code": code, "name": nm, "label": t, "source": "search"})
                                allowed.add(code)
                if extra:
                    res2 = _llm_select(llm, title, text, extra)
                    ok2 = {c["code"] for c in extra}
                    for s in (res2 or {}).get("selected") or []:
                        code = str(s.get("code", "")).replace(".", "").strip()
                        if code in ok2 and idx.exists(code) and all(r["kcd_code"] != code for r in rows):
                            rows.append({"kcd_code": code, "disease_label": s.get("label") or "", "method": "llm",
                                         "confidence": min(float(s.get("confidence") or 0.5), 0.79)})
            return _finish(rows), flags
        flags.append("llm_failed")
    # 결정적 대안: 큐레이션 0.8, 이름 매칭 0.6
    for c in cands:
        rows.append({"kcd_code": c["code"], "disease_label": c["label"], "method": "name_match",
                     "confidence": 0.8 if c["source"] == "curated" else 0.6})
    return _finish(rows), flags


def _finish(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        if r["kcd_code"] in seen:
            continue
        seen.add(r["kcd_code"])
        r["confidence"] = max(0.0, min(1.0, r["confidence"]))
        r["review_status"] = "auto" if r["confidence"] >= 0.8 else "needs_review"
        out.append(r)
    return out
