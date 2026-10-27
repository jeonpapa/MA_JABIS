"""A8 해외약가 자료원 파서·행 생성 공용 모듈 (표준 라이브러리만 사용).

서버 스크레이퍼, 요청 업로드 검증(api/price_request_ingest.py), Aside 워커가 쓰는 행 생성 스크립트(scripts/a8_rows.py)가
같은 파서를 공유해 국가별 가격 단계가 갈라지지 않게 한다. 규칙 원문: docs/foreign_price/README.md.
"""
