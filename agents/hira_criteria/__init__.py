"""심평원 급여기준 DB (항암제 공고 · 일반약제 고시 · KCD-9 질환 매핑).

원칙
- '최신' 만 유지한다. 이력 테이블을 쌓지 않고, 매 실행마다 최신 원문으로 파생 테이블을 다시 만든다.
  (사람 검토 행 `criteria_disease_kcd.method='manual'` 은 보존)
- 원문은 data/hira_criteria/raw/ 에 sha256 과 함께 보관하고, 변환은 kordoc(버전 고정)으로 한다.
- 기본은 dry-run. --apply 일 때만 DB 에 쓴다. 사람 정본 `indication_reimbursement` 에는 쓰지 않는다
  (agents/rules/data_contracts.md §1).
- HIRA 는 비한국/데이터센터 IP 를 차단하므로 수집은 로컬(노트북)에서 실행한다.

모듈
- http          : HIRA 다운로드(매직바이트 검증, 0바이트·HTML 오류 페이지 거부)
- sources       : 최신 원문 탐색(항암 통합본·월별 공고·허가초과 xlsx·약제 책자·보험인정기준·상병마스터)
- kordoc_runner : kordoc CLI 래퍼(버전 고정, sha 캐시)
- schema        : 신규 테이블 DDL (additive)
- kcd           : 심평원 상병마스터(KCD-9) 적재·조회·범위 전개
- onco_*        : 항암제 공고 파싱·KCD 매핑·허가초과 요법
- drug_*        : 일반약제 세부사항 파싱·최신 검증·질환 매핑
- md_export     : 암종별/약제별/질환(KCD)별 Markdown
- pipeline      : CLI
"""
