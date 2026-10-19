"""약평위 보고서 헤더 추출·최초 허가일 선택 — 2026-10-19 검증에서 확인한 누락 원인의 회귀 테스트.

픽스처는 실제 DREC PDF 1페이지 텍스트(pdftotext)와 실제 식약처 API 응답에서 가져왔다.
"""
from agents.analog.header_extract import (extract_header_fields, extract_session_dates, korean_ingredient_from_name,
                                          looks_extractable)
from agents.analog.permit_first import earliest_permit, lookup_first_permit

GASYBA = """INSIDabcdef_:MS_0001MS_0001
- 1 -
▢ 제형, 성분․함량 :
- 1병 중 Obinuzutumab 1g (25mg/mL)
▢ 효능 효과 :
- 동반 질환이 있으며 이전에 치료받은 적이 없는 만성림프구성백혈병에서 chlorambucil과
병용투여
▢ 약제 급여 평가 위원회 심의일
 2016년 제16차 약제급여평가위원회 : 2016년 12월 15일
- 암질환심의위원회 심의일: 2015년 6월 3일1)
약제급여평가위원회 평가 결과
Obinuzutumab 1g (25mg/mL)
(가싸이바주, 한국로슈(주))
"""

YONDELIS = """약제 요양급여의 적정성 평가결과
trabectedin 1.0mg
(욘델리스주사1.0밀리그램(트라벡테딘), ((주)메디팁)
▢ 제형, 성분․함량:
- 1병 중 trabectedin 1.0mg
▢ 효능·효과:
- 안트라사이클린 및 이포스파마이드 치료요법에 실패했거나, 이들 약제 투여가 부
▢ 약제급여평가위원회 심의일
2023년 제7차 약제급여평가위원회: 2023년 7월 6일
2023년 제12차 약제급여평가위원회: 2023년 11월 9일
- 암질환심의위원회 심의일: 2023년 5월 3일
"""

TABLE_STYLE = """자비쎄프타주(세프타지딤/아비박탐)(한국화이자제약(주))
가. 약제 정보
구 분
심의 대상 구분 결정신청
주성분 함량 Ceftazidime/Avibactam 2g/0.5g
제형 및 성상 흰색 내지 연한 노란색의 분말이 투명한 바이알에 든 주사제
"""

OLD_BLANK = """약제 급여 평가 위원회 평가 결과
▢ 제형, 성분․함량 :
▢ 효능 효과 :
▢ 약제 급여 평가 위원회 심의 일
2007년 제12차 약제급여평가위원회 : 2007년 11월 16일
바른 심사 바른 평가 건강한 국민
"""

SHORT_BIEUP = """약제 요양급여의 적정성 평가결과

▢ 제형, 성분․함량:
▢ 효능·효과:

▢ 약제급여평가위원회 심의일
2023년 제7차 약제급여평가위원회: 2023년 7월 6일

건강하고 안전한 의료문화를 열어가는 국민의료평가기관 - 1 -
"""


def test_header_ingredient_strength_manufacturer_and_exact_date():
    h = extract_header_fields(GASYBA)
    assert h["inn_en"] == "obinuzutumab" and h["strength"] == "1g (25mg/mL)"      # 용량 — 이전엔 어디서도 읽지 않았다
    assert h["manufacturer"] == "한국로슈(주)" and h["brand_in_header"] == "가싸이바주"
    assert [d["date"] for d in h["session_dates"]] == ["2016-12-15"]               # 암질환심의위(2015-06-03)는 약평위 날짜가 아니다


def test_header_unbalanced_parentheses_and_multiple_sessions():
    h = extract_header_fields(YONDELIS)
    assert h["manufacturer"] == "(주)메디팁" and h["inn_en"] == "trabectedin" and h["strength"] == "1.0mg"
    assert [(d["ordinals"], d["date"]) for d in h["session_dates"]] == [([7], "2023-07-06"), ([12], "2023-11-09")]


def test_header_table_style_after_2018():
    h = extract_header_fields(TABLE_STYLE)
    assert h["inn_en"] == "ceftazidime/avibactam" and h["strength"] == "2g/0.5g"


def test_blank_header_returns_none_not_invented_values():
    """2007~2009 보고서는 제형·성분·함량이 원문에서 비어 있다 — 날짜만 확보하고 나머지는 None."""
    h = extract_header_fields(OLD_BLANK)
    assert h["inn_en"] is None and h["strength"] is None and h["manufacturer"] is None
    assert [d["date"] for d in h["session_dates"]] == ["2007-11-16"]


def test_invalid_calendar_date_is_dropped():
    assert extract_session_dates("2020년 제3차 약제급여평가위원회: 2020년 2월 31일") == []


def test_korean_ingredient_from_file_name():
    assert korean_ingredient_from_name("가니레버프리필드시린지주0.25mg-0.5ml(가니렐릭스아세트산염)_(0.25mg-0.5mL)") == "가니렐릭스아세트산염"
    assert korean_ingredient_from_name("가드렛정100밀리그램(아나클립틴)") == "아나클립틴"
    assert korean_ingredient_from_name("가싸이바주") is None
    assert korean_ingredient_from_name("약이름정(한국로슈(주))") is None            # 회사 괄호는 성분이 아니다


def test_short_text_report_is_not_a_scan():
    """회귀: 300자 미만이면 스캔으로 오판 → 273건이 화면에서 빠지고 심의일이 YYYY-01-01 자리표시가 됐다."""
    assert looks_extractable([SHORT_BIEUP]) is True
    assert looks_extractable(["  "]) is False                                       # 진짜 스캔(텍스트 없음)
    assert looks_extractable(["(cid:1)" * 20 + "가" * 400]) is False                 # CID 깨짐
    assert looks_extractable(["a" * 320]) is True


# ── 식약처 최초 허가일 ─────────────────────────────────────────────────────────

NESINA = [  # 실제 API 응답(2026-10-19): 최초 허가 2013-05-31 은 취하·만료, 2023-10-30 은 재허가(정상)
    {"ITEM_NAME": "네시나정12.5밀리그램(알로글립틴벤조산염)", "ITEM_PERMIT_DATE": "20130531", "CANCEL_NAME": "취하"},
    {"ITEM_NAME": "네시나정25밀리그램(알로글립틴벤조산염)", "ITEM_PERMIT_DATE": "20130531", "CANCEL_NAME": "취하"},
    {"ITEM_NAME": "네시나정6.25밀리그램(알로글립틴벤조산염)", "ITEM_PERMIT_DATE": "20130531", "CANCEL_NAME": "유효기간만료"},
    {"ITEM_NAME": "네시나정12.5밀리그램(알로글립틴벤조산염)", "ITEM_PERMIT_DATE": "20231030", "CANCEL_NAME": "정상"},
    {"ITEM_NAME": "네시나정25밀리그램(알로글립틴벤조산염)", "ITEM_PERMIT_DATE": "20231030", "CANCEL_NAME": "정상"},
]


def test_earliest_permit_prefers_first_permit_over_first_normal_item():
    r = earliest_permit(NESINA, "네시나정")
    assert r["permit_date"] == "2013.05.31" and r["latest_permit_date"] == "2023.10.30" and r["n_items"] == 5


def test_earliest_permit_ignores_other_products_in_the_result_set():
    items = NESINA + [{"ITEM_NAME": "네시나정유사품", "ITEM_PERMIT_DATE": "20000101", "CANCEL_NAME": "정상"},
                      {"ITEM_NAME": "타사제품정", "ITEM_PERMIT_DATE": "19900101", "CANCEL_NAME": "정상"}]
    # '네시나정유사품' 은 같은 접두라 포함되지만 '타사제품정' 은 배제된다 — 접두 일치만 같은 제품군으로 본다
    assert earliest_permit(items, "네시나정")["permit_date"] == "2000.01.01"
    assert earliest_permit([{"ITEM_NAME": "타사제품정", "ITEM_PERMIT_DATE": "19900101"}], "네시나정") is None


def test_earliest_permit_brand_with_strength_tail_and_spaces():
    items = [{"ITEM_NAME": "뉴프로패취1mg24h(로티고틴)", "ITEM_PERMIT_DATE": "20120301", "CANCEL_NAME": "정상"}]
    assert earliest_permit(items, "뉴프로 패취 1, 2, 3mg24h")["permit_date"] == "2012.03.01"


def test_lookup_first_permit_uses_injected_fetch_and_handles_miss():
    calls = []

    def fetch(item_name=None, num_of_rows=None, **_):
        calls.append((item_name, num_of_rows))
        return NESINA if item_name == "네시나정" else []
    assert lookup_first_permit("네시나정", fetch=fetch)["permit_date"] == "2013.05.31"
    assert lookup_first_permit("없는약정", fetch=fetch) is None
    assert calls[0] == ("네시나정", 50)                                              # 50건까지 — 다품목 브랜드가 10건을 넘을 수 있다


# ── 표본 대조(2026-10-19)에서 틀렸던 사례 — 틀린 값을 넣느니 비워 둔다 ─────────────────────────────

ISTURISA = """약제 요양급여의 적정성 평가결과
osilodrostat phosphate(as osilodrostat 1, 5㎎)
(이스투리사필름코팅정1,5밀리그램(오실로드로스타트인산염),
(주)레코르다티코리아)
▢ 제형, 성분․함량:
- 1정 중 osilodrostat phosphate 1.431mg, 7.155mg
(osilodrostat로서 1mg, 5mg)
▢ 효능·효과
"""

TRIMBOW = """약제 요양급여의 적정성 평가결과
beclomethasone dipropionate 12mg(0.1mg/회),
glycopyrronium bromide (as glycopyrronium 1.2mg(10μg/회))
(트림보우흡입제100/6/12.5(베클로메타손디프로피오네이트,
포르모테롤푸마르산염수화물, 글리코피로니움브롬화물), 코오롱제약㈜)
▢ 제형, 성분․함량:
- 무색 또는 노란색을 띤 액이 일정량씩 분무되는 밸브가 장착된 알루미늄캔에
"""

PRELECTIN = """약제 요양급여의 적정성 평가 결과
doxylamine succinate 10mg, pyridoxine hydrochloride 10mg
(프리렉틴장용정(독실아민숙신산염, 피리독신염산염), 한화제약㈜)
▢ 제형, 성분․함량:
- 1정당 독실아민숙신산염 10mg, 피리독신염산염 10mg
"""

OTEZLA = """약제 요양급여의 적정성 평가결과
apremilast 30mg, 10mg/20mg/30mg
(오테리아정(아프레밀라스트), 동아에스티㈜)
▢ 제형, 성분․함량:
- 10mg : 분홍색의 마름모형 필름코팅정제
- 1정 중 apremilast 30mg
"""


def test_wrapped_brand_line_gives_company_not_a_strength():
    """회귀: '(osilodrostat로서 1mg, 5mg)' 줄이 브랜드 줄로 오인돼 제조사가 '5mg' 으로 들어갔다."""
    h = extract_header_fields(ISTURISA)
    assert h["manufacturer"] == "(주)레코르다티코리아"
    assert h["inn_en"] == "osilodrostat phosphate" and h["strength"] == "1.431mg, 7.155mg"


def test_multiline_combination_brand_line_company():
    h = extract_header_fields(TRIMBOW)
    assert h["manufacturer"] == "코오롱제약㈜"
    assert h["strength"] is None and h["inn_en"] is None            # 복합제·제형 서술 — 용량을 지어내지 않는다


def test_combination_product_is_left_blank():
    h = extract_header_fields(PRELECTIN)
    assert h["manufacturer"] == "한화제약㈜" and h["inn_en"] is None and h["strength"] is None


def test_ambiguous_multi_strength_keeps_inn_only():
    h = extract_header_fields(OTEZLA)
    assert h["manufacturer"] == "동아에스티㈜" and h["inn_en"] == "apremilast" and h["strength"] is None


def test_strength_values_with_unit_noise_are_rejected():
    from agents.analog.header_extract import _split_inn_strength
    assert _split_inn_strength("1병 중 Obinuzutumab 1g (25mg/mL)") == ("obinuzutumab", "1g (25mg/mL)")
    assert _split_inn_strength("4:1 w/w mixed hydrogel of sodium hyaluronate gel 2.04g")[1] is None
    assert _split_inn_strength("1정 중 vedolizumab 300mg") == ("vedolizumab", "300mg")
