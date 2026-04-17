# Tier-3 Pilot — Keytruda × NICE TA531 교차검증 결과

**실행일**: 2026-04-16
**질의**: NICE Technology Appraisal for pembrolizumab 1L NSCLC monotherapy, PD-L1 high
**소스**:
- **Gemini 2.5-pro (grounded)** — Google Search grounding 활성
- **Claude (WebSearch)** — 본 세션에서 nice.org.uk·ncbi.nlm.nih.gov 도메인 한정 검색

> **환경 제약**: Perplexity sonar-pro / OpenAI GPT-5 는 macOS 시스템 파이썬 (LibreSSL 2.8.3) ↔ Cloudflare TLS 핸드쉐이크 실패로 이번 회차는 차단됨. Gemini (Google 인프라) 만 프로그래매틱 호출 성공. 개념 증명 목적으로 Claude WebSearch 를 2번째 독립 소스로 활용.

---

## 필드별 2-Source 매트릭스

| 상태 | 필드 | Gemini 2.5-pro (grounded) | Claude (WebSearch) | 판정 |
|---|---|---|---|---|
| ✅ | `ta_number` | **TA531** | TA531 | 합의 |
| ✅ | `decision` | **Recommended** | Recommended (as an option) | 합의 |
| ✅ | `decision_date` | **2018-07** | 18 July 2018 | 합의 |
| ✅ | `indication_scope` | PD-L1 TPS ≥50%, EGFR/ALK 음성, 2년 중단 규칙 | PD-L1 ≥50% TPS, no EGFR/ALK, 2-year stopping rule | 합의 |
| ⚠️ | `icer_value_gbp` | **£30,244** | 공개 페이지에 수치 미노출 ("commercial in confidence") | **단일 소스** — 수치 검증 필요 |
| ✅ | `pas_applied` | **true** | "commercial access agreement" 존재 | 합의 |
| ✅ | `rationale` | KEYNOTE-024, +16개월 OS, EoL 기준 수용 | 16-month OS gain vs chemo, within EoL acceptable range | 합의 |
| ✅ | `source_url` | https://www.nice.org.uk/guidance/ta531 | https://www.nice.org.uk/guidance/ta531 | 합의 |

---

## 요약

- **8개 필드 중 7개 합의** (≥2 소스 일치)
- **1개 필드 단일 소스** (`icer_value_gbp` £30,244): Gemini 가 학습/grounding 과정에서 얻은 수치이나 NICE 공개 summary 페이지는 상세 ICER 대신 "commercial in confidence" 로 처리. 위원회 심의 문서(committee papers) 직접 접근 필요.
- **충돌 0건** — 두 소스 모두 동일한 NICE 결정·날짜·적응증 범위에 수렴

## 교차검증 개념 입증

이 파일럿이 증명한 것:
1. 독립 소스 2개가 동일한 HTA 사실(TA번호, 결정, 날짜, 적응증 조건, PAS 유무)에 수렴 → **신뢰도 높음**으로 자동 등급 가능
2. 수치 데이터(ICER) 처럼 공개 범위가 제한된 필드는 **단일 소스 플래그 → 유저 리뷰** 경로로 분기 가능
3. 필드 단위 매트릭스가 "어디를 믿고, 어디를 더 확인해야 하는지" 한눈에 제시 → 워크벤치 UX 의 HTA Matrix 시트에 그대로 사상 가능

## TLS 차단 해소 후 기대되는 보강

Perplexity sonar-pro / OpenAI GPT-5 복구 시:
- ICER £30,244 를 제3·제4 소스로 검증 → 단일 소스 → 합의/충돌로 전이
- committee papers PDF 인용 capability 추가
- 4-way 합의 강도로 신뢰도 등급 세분화 (2-source agree → 4-source agree)

## TLS 복구 옵션

| 옵션 | 영향도 | 리스크 |
|---|---|---|
| `brew install python@3.12` (Homebrew 선행 설치 필요) | 최우선 권장 — 시스템 python 그대로 두고 병행 | 낮음, 역행 가능 |
| python.org 공식 installer 수동 다운로드 | brew 없이도 가능 | 낮음 |
| VPN (오피스 밖에서 사용 중이시면) | 가능한 빠른 완화 | 자격증명 이슈 여부에 따라 다름 |

---

## 원본 출처

- [NICE TA531 Overview](https://www.nice.org.uk/guidance/ta531)
- [NICE TA531 Recommendation (Chapter 1)](https://www.nice.org.uk/guidance/ta531/chapter/1-Recommendation)
- [NICE TA531 Committee Discussion (Chapter 3)](https://www.nice.org.uk/guidance/ta531/chapter/3-Committee-discussion)
- [NICE TA531 Information about pembrolizumab (Chapter 2)](https://www.nice.org.uk/guidance/ta531/chapter/2-Information-about-pembrolizumab)
- [NCBI Bookshelf — TA531 summary](https://www.ncbi.nlm.nih.gov/books/NBK619769/)
