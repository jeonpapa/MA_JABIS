# MA AI Dossier — 오류 및 발견사항 로그

> 이 파일은 개발 과정에서 발생한 오류, 비작동 기능, 주요 발견사항을 기록합니다.
> 동일한 실수가 반복되지 않도록 모든 항목에 **원인**과 **해결책**을 함께 기록합니다.

---

## [2026-04-15] 국내 약가 검색 속도 극단적 지연 (8~34초)

**증상**: `/api/domestic/search?q=키트루다` 응답 8~34초, 실질적으로 사용 불가 수준

**원인 (2가지)**:
1. `drug_prices` 테이블 3,785,284행에 대해 `LIKE '%키트루다%'` 수행
   - Leading `%` wildcard → B-tree 인덱스 무효화 → 풀테이블 스캔 (~8초 DB 쿼리)
   - Flask 오버헤드 + 결과 직렬화로 추가 ~20초
2. `price_changes` API는 `search_drug(limit=200)` 후 코드별 `get_price_history()` N+1 호출

**해결책**:
- `drug_latest` 테이블 신설: 보험코드별 최신 날짜 레코드만 (~22K행)
- `fts_drug_names` FTS5 가상 테이블: unicode61 토크나이저, prefix 매칭
- `search_drug()` 재작성: FTS5 우선 → drug_latest LIKE 폴백 (둘 다 22K 행)
- 초기화: 서버 시작 시 `_migrate_search_tables()` 1회 실행 (30~60초, 이후 스킵)
- `upsert_prices()` 후 drug_latest + FTS 증분 갱신

**예상 개선**: 8~34초 → <100ms (FTS5) / <200ms (LIKE 폴백)

---

## [2026-04-15] FR/DE 스크레이퍼 잘못된 대체 소스 사용

**증상**: 독일·프랑스 약가가 `None`으로 반환, 인정받는 소스 아님

**원인**: 초기 구현 시 Playwright 로그인 장벽 우회를 위해 공개 대체 DB 사용
- 독일: Gelbe Liste (공개) → **인정 소스: Rote Liste (DocCheck 계정 필요)**
- 프랑스: BDPM (공개) → **인정 소스: Vidal.fr (Professional 구독 필요)**

**해결책**: 두 스크레이퍼를 `requests` 기반으로 완전 재작성
- `de_rote_liste.py`: DocCheck POST 로그인 (`https://login.doccheck.com/`)
  - 필수 hidden 필드: `login_id=2000000012529`, `dc_client_id=2000000012529`,
    `redirect_uri=https://www.rote-liste.de/login`, `intDcLanguageId=148`,
    `intLoginVersion=3`, `strDesignVersion=fullscreen_dc`
  - 환경변수: `ROTE_LISTE_DE_USERNAME`, `ROTE_LISTE_DE_PASSWORD`
- `fr_vidal.py`: Vidal POST 로그인
  - URL: `https://www.vidal.fr/login/?client_id=vidal_2017&redirect=...`
  - 필드: `login[email]`, `login[password]`, `login[remember]=1`, `login[submit]=Je me connecte`
  - 약제 URL: `data-cbo` 속성의 base64 디코딩 필요
  - 환경변수: `VIDAL_FR_USERNAME`, `VIDAL_FR_PASSWORD`

**남은 작업**: 실제 계정 자격증명 `.env` 설정 필요 (계정 없으면 `local_price=None`)

---

## [2026-04-15] market_intelligence_agent.py SyntaxError (f-string 개행)

**증상**: 서버 import 시 `SyntaxError: EOL while scanning string literal` (line 660)

**원인**: Python 3.9에서는 f-string 내 리터럴 개행 불가
```python
# 잘못된 코드 (Python 3.9 오류)
f"다음 약제의 약가 변동 사유를 분석해주세요.
"
```

**해결책**: `\n` 이스케이프 시퀀스 사용
```python
# 올바른 코드
f"다음 약제의 약가 변동 사유를 분석해주세요.\n\n"
```

**규칙**: Python 3.9 환경에서 f-string 내 실제 개행 문자 사용 금지. 항상 `\n` 사용.

---

## [2026-04-15] 해외 약가 탭 UX — "캐시 조회" 개념 제거

**증상/요청**: "캐시 조회" 버튼이 사용자 관점에서 의미 불명확

**해결책**: 검색 히스토리 사이드바 패턴으로 전환
- 왼쪽 사이드바: 과거 검색 약제 목록 (`/api/foreign/drugs` 엔드포인트)
- 약제 클릭 → 최신 캐시 결과 표시
- 상단 검색창: 신규 약제 실시간 검색
- 제형 필터: `dosage_strength`가 복수인 경우 자동 활성화

**신규 API**: `GET /api/foreign/drugs` → `db.get_foreign_drug_list()` 집계 쿼리

---

## 개발 원칙 (이 파일 업데이트 규칙)

- 새 오류/발견사항 발생 시 이 파일 상단(최신순)에 추가
- **원인 불명인 채로 해결책만 기록 금지** — 원인 파악이 우선
- Playwright 의존 스크레이퍼는 requests 대안을 먼저 검토할 것
- Python 3.9 호환성: f-string 개행, walrus 연산자 등 버전 제약 항상 확인
