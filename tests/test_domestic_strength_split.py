"""국내 약가 — 함량별 분리 + 주사제 용법 미확정 (2026-11-03 개선 요청 6).

임핀지주 0.12g/2.4mL 과 0.5g/10mL 은 정규화 키('임핀지주')가 같아 한 행으로 합쳐졌고, 120mg 이름에 500mg 가격·이력이
붙었다. 총량/용량 suffix 가 서로 다른 함량이 2개 이상이면 나누고, suffix 없는 옛 코드는 가격이 가까운 쪽에 붙인다.
정제의 '0.1289g/1정' 같은 표기는 나누지 않는다. 주사제는 용법 미확정이면 약가를 일치료비로 쓰지 않는다.
"""
from api.server import _strength_split_keys, _is_injection_product


def _rp(code, name, sfx, price):
    return {"insurance_code": code, "product_name": name, "dosage_form": sfx, "current_price": price}


def test_injection_strengths_are_split_and_legacy_code_attaches_by_price():
    rows = [_rp("650701071", "임핀지주(더발루맙)_(0.12g/2.4mL)", "0.12g/2.4mL", 731803),
            _rp("650701081", "임핀지주(더발루맙)_(0.5g/10mL)", "0.5g/10mL", 3049531),
            _rp("650700010", "임핀지주(더발루맙)", "", 3050975)]
    keys = _strength_split_keys(rows)
    assert keys["650701071"] == "임핀지주|0.12g/2.4mL"
    assert keys["650701081"] == "임핀지주|0.5g/10mL"
    assert keys["650700010"] == "임핀지주|0.5g/10mL"


def test_single_strength_and_tablet_suffixes_are_not_split():
    rows = [_rp("1", "키트루다주(펨브롤리주맙)_(0.1g/4mL)", "0.1g/4mL", 2000000),
            _rp("2", "키트루다주(펨브롤리주맙)", "", 2100000),
            _rp("3", "자누비아정100밀리그램(시타글립틴인산염수화물)_(0.1289g/1정)", "0.1289g/1정", 500),
            _rp("4", "자누비아정100밀리그램(인산시타글립틴일수화물)_(128.9mg/1정)", "128.9mg/1정", 500)]
    assert _strength_split_keys(rows) == {}


def test_same_total_written_differently_is_one_strength():
    rows = [_rp("1", "가나주(성분)_(0.5g/10mL)", "0.5g/10mL", 100), _rp("2", "가나주(성분)_(500mg/10mL)", "500mg/10mL", 100)]
    assert _strength_split_keys(rows) == {}


def test_injection_detection():
    assert _is_injection_product({"product_name": "임핀지주(더발루맙)_(0.12g/2.4mL)", "dosage_form": "0.12g/2.4mL"})
    assert _is_injection_product({"product_name": "트루리시티0.75mg/0.5mL펜주(둘라글루티드)", "dosage_form": ""})
    assert not _is_injection_product({"product_name": "자누비아정100밀리그램(시타글립틴)_(0.1289g/1정)", "dosage_form": "0.1289g/1정"})
