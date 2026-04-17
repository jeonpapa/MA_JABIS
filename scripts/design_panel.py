"""
해외약가 대쉬보드 재설계 패널 논의 스크립트
- OpenAI GPT-4o 와 Google Gemini 2.5-flash 에게 동일한 브리프를 전달
- 각 LLM 의 독립 견해를 받아 파일에 저장
"""

import json
import os
import re
import ssl
import sys
import urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))


def _load_env():
    env_path = BASE_DIR / "config" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env()

BRIEF = """당신은 한국 글로벌 제약회사 (MSD Korea) 의 Market Access (MA) 담당자를 위한
"해외약가 대쉬보드" 재설계 논의에 참여합니다. 공정하고 솔직한 견해를 주세요.

[사용자 프로필]
- 직책: MSD Korea MA 담당자 (국내 약가 등재·조정, 심평원 협상 지원)
- 목표: AI 를 활용한 업무 자동화 — 반복 조사 최소화, 신뢰 가능한 근거 자료 확보
- 언어: 한국어 중심, 제품명/성분명은 영문 사용

[프로젝트 현황]
1. 국내 약가 (HIRA) 변동 이력 조회·시각화 — 작동 양호
2. 해외약가 — JP·IT·FR·CH·UK·DE 에서 HIRA 가 신뢰하는 공개 사이트 스크레이핑
   - JP: MHLW / IT: AIFA / FR: Vidal 또는 BDPM / CH: Compendium
   - UK: MIMS / DE: Rote Liste
3. HIRA A8 조정가 자동 계산 — 공장도비율·환율 (KEB하나은행 36개월 평균) ·VAT·유통마진
4. 제외국 허가현황 (HTA) — FDA·PBAC·CADTH·NICE·SMC 조회, FDA 적응증 매트릭스로 국가별 매핑

[현재 UI (사용자가 '유용하지 않다' 고 평가)]
- 국가별 카드 요약 (flag, 국가, 제품명, 조정가)
- 수평 막대 차트 (국가별 KRW 조정가 비교)
- 상세 테이블 — 최근 11 컬럼 분해 (현지약가→환율→원화환산→공장도비율→공장도가→VAT→VAT적용→조정가)
- A8 요약 (평균/최저/최저×% 로 협상 제안가 추정)
- 제외국 허가현황 매트릭스 (FDA 적응증 기준, PBAC/CADTH/NICE/SMC)

[사용자 피드백 (원문)]
"해외약가의 경우 지금 전체적으로 유용한 대쉬보드라고 보이지 않아.
내가 궁극적으로 원하는건 '심평원이 신뢰하는 공개된 약가 사이트' 에서 정확한 약가를 가져오고
이를 '조정하는 로직을 테이블로 적용' 해서 업무의 효율화를 하고자 함이고,
동일하게 제외국의 허가사항도 명확하게 잘 정리게 되어지면 좋겠어.
지금은 내가 말은 하지만 네가 뭘하는지도 모르고 하는 상황이라서
내가 계속 피드백을 주는게 크게 의미가 없는 것 같아.
궁극적으로 내가 원하는 바를 이해하고 이에 맞는 결과인지 자체 점검하고 보여줘."

[핵심 긴장 관계]
- 사용자가 '가격 조정 로직 테이블' 을 명시적으로 언급했으나 기존에 테이블을 추가해도 '유용하지 않다' 고 평가.
- 즉 문제는 테이블 유무 가 아니라 **워크플로우 맞춤성** 에 있을 가능성이 높음.

[논의할 5가지 핵심 질문 — 각 질문에 구체 답변 부탁]

1. Job-to-be-done (핵심 과업):
   MA 담당자가 이 도구로 "가장 중요한 단 하나의 일" 이 무엇인가?
   후보:
   (a) 심평원 협상용 '제안 상한가' 근거 자료 생성 (A8 최저×% 등)
   (b) 글로벌 약가 벤치마킹 (우리 제품 + 경쟁 제품 동시 비교)
   (c) 제외국 허가현황 리서치 (적응증별 급여 여부)
   (d) 주기적 모니터링 (가격 변동·신규 허가)
   (e) 기타
   각각의 타당성 + 당신이 가장 유력하다고 보는 것 + 그 이유

2. 출력 단위 (산출물의 형태):
   - 화면 대시보드 중심?
   - 파일 다운로드 (xlsx/pdf) 중심?
   - 둘 다 필요하다면 주/종 관계?

3. 제품 매칭 문제:
   국가별 pack size/용량이 다른데 A8 조정가를 어느 단위로 계산할지 어떻게 UX 로 풀어야 하는가?
   (예: 국내 기준 100mg 1 vial 에 대응하는 각국 최소 단위? 사용자가 수동 선택? 자동 정규화?)

4. 가격 + 허가 통합 여부:
   A8 협상 자료 작성 시 "국가별 가격" 과 "국가별 급여 여부·허가 적응증" 이 같은 view 에 있어야 하는가, 별도 tab 이 나은가?

5. 제품 전체 UX 흐름:
   (A) Drug-centric: 제품 검색 → 국가별 가격·허가 통합 dossier 1장
   (B) Workflow-centric: 협상 준비 → 목표가 입력 → 대안 비교 → 자료 export
   (C) Monitoring-centric: 등록한 watch-list 주기 체크 → 변동 알림
   어떤 정신모델이 '대시보드' 의 성격에 맞는가?

[요청]
- 5개 질문에 순서대로 직접 답변
- 추가로 '당신이라면 이 제품을 완전히 다시 설계할 때 가장 먼저 결정할 단 하나의 선택' 을 제안
- 한국어로 작성, 길이는 800~1500자 (간결하면서도 근거 포함)
- 확신 없는 부분은 추측임을 명시
"""


def call_openai(brief: str) -> dict:
    try:
        from openai import OpenAI
    except ImportError:
        return {"source": "openai", "error": "openai 패키지 미설치"}
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        return {"source": "openai", "error": "OPENAI_API_KEY 없음"}
    try:
        client = OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model="gpt-5",
            messages=[
                {"role": "system", "content": "당신은 프로덕트 디자이너이자 제약 산업 전문가입니다. 실용적이고 구체적으로 답하세요."},
                {"role": "user", "content": brief},
            ],
            max_completion_tokens=4000,
        )
        return {"source": "openai (gpt-5)", "content": resp.choices[0].message.content}
    except Exception as e:
        return {"source": "openai", "error": str(e)}


def call_gemini(brief: str) -> dict:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return {"source": "gemini", "error": "GEMINI_API_KEY 없음"}
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-pro:generateContent?key={key}"
    )
    body = {
        "systemInstruction": {
            "role": "system",
            "parts": [{"text": "당신은 프로덕트 디자이너이자 제약 산업 전문가입니다. 실용적이고 구체적으로 답하세요."}],
        },
        "contents": [{"role": "user", "parts": [{"text": brief}]}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 6000,
        },
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
            raw = resp.read().decode("utf-8")
        payload = json.loads(raw)
        text = (
            payload.get("candidates", [{}])[0]
            .get("content", {}).get("parts", [{}])[0]
            .get("text", "")
        ).strip()
        return {"source": "gemini (2.5-pro)", "content": text}
    except Exception as e:
        return {"source": "gemini", "error": str(e)}


def main():
    print("=== 해외약가 대쉬보드 재설계 패널 논의 ===\n")
    print("GPT-5 와 Gemini 2.5-pro 에게 동시 브리프 전송 중...\n")
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_gpt = ex.submit(call_openai, BRIEF)
        fut_gem = ex.submit(call_gemini, BRIEF)
        results = [fut_gpt.result(), fut_gem.result()]

    out_dir = BASE_DIR / "data" / "design_panel"
    out_dir.mkdir(parents=True, exist_ok=True)
    for r in results:
        src = r["source"].split(" ")[0]
        if "error" in r:
            print(f"[{src}] ❌ 오류: {r['error']}\n")
            continue
        path = out_dir / f"panel_{src}.md"
        path.write_text(f"# {r['source']}\n\n{r['content']}\n", encoding="utf-8")
        print(f"[{src}] ✅ 저장: {path.name} ({len(r['content'])}자)\n")

    # 컴팩트 미리보기
    print("\n=== 요약 미리보기 ===\n")
    for r in results:
        if "error" in r:
            continue
        preview = r["content"][:600].replace("\n", " ")
        print(f"\n### {r['source']}\n{preview}...\n")


if __name__ == "__main__":
    main()
