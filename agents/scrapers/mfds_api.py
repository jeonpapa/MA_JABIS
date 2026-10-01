"""공공데이터포털(data.go.kr) MFDS API 공통 호출 — '답' 과 '실패' 를 구분한다.

kr_mfds_permit / kr_mfds_patent 가 같은 게이트웨이를 같은 방식으로 호출한다. 여기서 정한 규칙:

- 반환 리스트는 **API 가 실제로 답한 경우에만** 나온다.
  빈 리스트 = '해당 조건의 데이터 없음' — resultCode 00 에 items 가 없거나, 포털 표준 코드 03(NODATA_ERROR).
- 그 외는 전부 MfdsApiError 다: 전송 실패(DNS·타임아웃·HTTP), 본문이 JSON 이 아님(게이트웨이 XML 오류 봉투·HTML 오류 페이지),
  header.resultCode 부재, resultCode 가 00/03 이 아님. 호출자는 이것을 '품목 없음' 으로 취급하거나 캐시에 적으면 안 된다.
  (2026-10-01: 실패를 [] 로 돌려 30/90일 부정 sentinel 을 쓰던 결함의 재발 방지 — agents/rules/mfds_api_integration_rules.md)
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request

logger = logging.getLogger(__name__)

# 포털 표준 resultCode 중 '정상 응답이지만 데이터 없음' 으로 정의된 코드. 서비스 장애가 아니다.
NODATA_RESULT_CODES = frozenset({"03"})

_GATEWAY_CODE = re.compile(r"<returnReasonCode>\s*(\d+)\s*</returnReasonCode>")
_GATEWAY_MSG = re.compile(r"<returnAuthMsg>\s*([^<]+?)\s*</returnAuthMsg>")
_GATEWAY_ERR = re.compile(r"<errMsg>\s*([^<]+?)\s*</errMsg>")


class MfdsApiError(RuntimeError):
    """MFDS API 가 답을 주지 못했다 — '품목 없음' 과 구분되는 명시적 실패 신호.

    kind: 'transport'   전송 실패 (DNS·연결·타임아웃·HTTP 오류)
          'parse'       본문이 JSON 이 아니거나 포털 표준 형태(header.resultCode) 가 아님
          'gateway'     포털 게이트웨이 XML 오류 봉투 (쿼터 초과 22, 키 미등록 30, IP 미등록 32 등) — code 에 사유 코드
          'result_code' 서비스가 00/03 이외 resultCode 로 답함 — code 에 그 값
    RuntimeError 하위라 기존 `except Exception` / `except RuntimeError` 호출자와 호환된다.
    """

    def __init__(self, kind: str, detail: str, code: str | None = None):
        self.kind = kind
        self.detail = detail
        self.code = code
        super().__init__(f"{kind}{f'[{code}]' if code else ''}: {detail}")


def request_items(url: str, *, timeout: int, label: str) -> list[dict]:
    """URL 호출 → JSON → header.resultCode 검사 → body.items.

    Returns: items 리스트. 빈 리스트는 'API 가 답했고 0건' 만을 뜻한다.
    Raises: MfdsApiError — 답이 아닌 모든 경우. 호출자가 캐시에 쓰기 전에 반드시 구분해야 한다.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.debug("MFDS %s API 전송 실패: %s", label, e)
        raise MfdsApiError("transport", f"{type(e).__name__}: {e}") from e

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        m = _GATEWAY_CODE.search(body)
        if m:
            msg = _GATEWAY_MSG.search(body) or _GATEWAY_ERR.search(body)
            detail = msg.group(1) if msg else body[:200]
            logger.debug("MFDS %s 게이트웨이 오류 %s: %s", label, m.group(1), detail)
            raise MfdsApiError("gateway", detail, code=m.group(1)) from e
        logger.debug("MFDS %s 응답 JSON 파싱 실패: %s", label, body[:200])
        raise MfdsApiError("parse", f"JSON 아님: {body[:120]!r}") from e

    if not isinstance(data, dict):
        raise MfdsApiError("parse", f"예상 밖 응답 형태: {type(data).__name__}")
    header = data.get("header")
    code = header.get("resultCode") if isinstance(header, dict) else None
    if code is None or str(code).strip() == "":
        raise MfdsApiError("parse", "header.resultCode 없음 — 포털 표준 응답이 아니다")
    code = str(code).strip()
    if code in NODATA_RESULT_CODES:
        logger.debug("MFDS %s 응답 %s(%s) — 데이터 없음(정상)", label, code, header.get("resultMsg"))
        return []
    if code != "00":
        logger.debug("MFDS %s 응답 비정상 %s: %s", label, code, header.get("resultMsg"))
        raise MfdsApiError("result_code", str(header.get("resultMsg") or ""), code=code)

    body_obj = data.get("body")
    items = body_obj.get("items") if isinstance(body_obj, dict) else None
    items = items or []
    if isinstance(items, dict):  # 단일 결과는 dict 로 옴
        items = [items]
    return [it for it in items if isinstance(it, dict)]
