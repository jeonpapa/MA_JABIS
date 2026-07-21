"""핵심 파생 함수 단위 테스트 — price_vs_eval·icer·comparator·RuleBasis outcome-gate.

순서형 파생(price_vs_eval/icer/comparator)은 TEXT ONLY — label(review_result) 인자를
받지 않는다(타깃 누수 차단). label 은 actual 필드와 RuleBasis outcome-gate 에만 쓰인다.
"""
import inspect

import export_calibration as ec


def compact(s):
    return ec.compact(s)


# ── 타깃 누수 차단(시그니처 수준) ──
def test_derivations_take_no_label_param():
    # 파생 함수 시그니처에 label 이 없어야 한다 — review_result 가 피처로 새어들 통로 자체 제거.
    assert "label" not in inspect.signature(ec.derive_price_vs_eval).parameters
    assert "label" not in inspect.signature(ec.derive_icer).parameters
    assert "label" not in inspect.signature(ec.derive_comparator).parameters


# ── price_vs_eval ──
def test_price_accept_pattern_소폭초과():
    dr = "제약사가 원 이하를 수용하였으므로 급여의 적정성이 있음"
    val, conf = ec.derive_price_vs_eval(compact(dr))
    assert val == "소폭초과" and conf == "high"


def test_price_highcost_대폭초과_text_only():
    # 고가 진술 → 대폭초과. 결과(통과/미통과)와 무관하게 텍스트만으로 판정(누수 제거).
    dr = "대체약제 대비 효과가 유사하나 투약비용이 고가로 비용효과성이 불분명하므로 비급여함"
    val, conf = ec.derive_price_vs_eval(compact(dr))
    assert val == "대폭초과" and conf == "high"


def test_price_highcost_approved_style_text_also_대폭초과():
    # 통과 사례의 전형 문구(고가이나 다만…적정성)도 동일하게 대폭초과 — label 의존 없음 증명.
    dr = ("현행치료 대비 임상적 유용성 개선이 인정되나 대체약제 대비 소요비용이 고가로 "
          "이에 상응하는 비용효과성이 불분명함. 다만 위험분담 적용을 조건으로 급여의 적정성이 있음")
    val, conf = ec.derive_price_vs_eval(compact(dr))
    assert val == "대폭초과" and conf == "high"


def test_price_accept_beats_highcost():
    # 수용 조항이 고가 진술보다 우선(가격 인하 수용 → 소폭초과).
    dr = "소요비용이 고가이나 제약사가 A7조정최저가 이하를 수용하였으므로 급여의 적정성이 있음"
    val, _ = ec.derive_price_vs_eval(compact(dr))
    assert val == "소폭초과"


def test_price_cost_effective_이하():
    dr = "대체약제보다 저렴하여 비용 효과적이므로 급여의 적정성이 있음"
    val, conf = ec.derive_price_vs_eval(compact(dr))
    assert val == "이하" and conf == "high"


def test_price_else_low():
    dr = "본 약제는 희귀질환 치료제로 사회적 요구도가 인정된다는 점만 기술됨 상세는 기재"
    val, conf = ec.derive_price_vs_eval(compact(dr))
    assert val == "이하" and conf == "low"


# ── icer ──
def test_icer_waiver_면제():
    dr = "약가협상생략기준금액 이하를 수용하였으므로 상한금액 협상절차를 생략함 세부내용"
    val, conf = ec.derive_icer(compact(dr), 0)
    assert val == "면제" and conf == "high"


def test_icer_pe_waiver_flag():
    dr = "임상적 유용성 개선이 인정되나 대체약제 대비 소요비용이 다소 높은 편임 상세"
    val, conf = ec.derive_icer(compact(dr), 1)
    assert val == "면제"


def test_icer_waiver_denied_not_면제():
    # 명시적 생략대상 부인(해당하지 아니하며) → pe_waiver 플래그가 1이어도 면제 아님.
    dr = ("소요비용이 고가이고 기대여명이 2년 이상으로 생존을 위협할 정도의 심각한 질환으로 "
          "보기 어려운 점 등 경제성평가 자료제출 생략가능 대상약제에 해당하지 아니하며, "
          "비용효과성이 불분명하므로 비급여함")
    val, conf = ec.derive_icer(compact(dr), 1)
    assert val == "초과" and conf == "high"


def test_icer_conditional_waiver_offer_not_면제():
    # 부결문 상투구: "생략기준금액 이하를 수용할 경우 …생략함" = 미성립 조건부 제안 → 면제 아님.
    # 지배 신호는 비용효과성 불분명 → 초과.
    dr = ("대체약제 대비 효과가 열등하다고 보기 어려우나 투약비용이 고가로 이에 상응하는 "
          "비용효과성이 불분명하여 비급여함. 단, 제약사가 약가협상생략기준금액(20mg: 원/캡슐, "
          "30mg: 원/캡슐) 이하를 수용할 경우 상한금액 협상절차를 생략함")
    val, _ = ec.derive_icer(compact(dr), 0)
    assert val == "초과"


def test_icer_applied_waiver_면제():
    # 적용형: "생략기준금액 이하로 상한금액 협상절차를 생략함" → 면제.
    dr = "투약비용이 저렴하여 비용효과적이므로 급여의 적정성이 있으며, 약가협상생략기준금액(원/캡슐) 이하로 상한금액 협상절차를 생략함"
    val, conf = ec.derive_icer(compact(dr), 0)
    assert val == "면제" and conf == "high"


def test_icer_unclear_초과_text_only():
    # 비용효과성 불분명 → 초과. 결과(통과/미통과)와 무관 — label 인자 자체가 없다.
    dr = "대체약제 대비 효과가 유사하나 투약비용이 고가로 비용효과성이 불분명하므로 비급여"
    val, conf = ec.derive_icer(compact(dr), 0)
    assert val == "초과" and conf == "high"


def test_icer_unclear_approved_style_text_also_초과():
    # 통과 사례 전형 문구(불분명하나 수용으로 적정성)도 동일하게 초과 — 텍스트 전용 판정.
    dr = "일부 지표에서 비용효과성이 불분명하나 제약사 수용으로 급여의 적정성이 있음 상세기술"
    val, _ = ec.derive_icer(compact(dr), 0)
    assert val == "초과"


def test_icer_not_cost_effective_초과():
    dr = "소요비용이 고가로 경제성평가 결과 비용효과적이라고 보기는 어려움. 다만 중증질환임"
    val, conf = ec.derive_icer(compact(dr), 0)
    assert val == "초과" and conf == "high"


def test_icer_low_이하():
    dr = "투약비용이 대체약제보다 저렴하여 비용효과적이므로 급여의 적정성이 있음 상세 기술"
    val, conf = ec.derive_icer(compact(dr), 0)
    assert val == "이하" and conf == "high"


def test_icer_else_근처_low():
    dr = "본 약제는 희귀질환 치료제로 사회적 요구도가 인정된다는 점만 기술됨 상세는 기재"
    val, conf = ec.derive_icer(compact(dr), 0)
    assert val == "근처" and conf == "low"


# ── comparator ──
def test_comparator_equal():
    dr = "대체약제 대비 효과가 유사하고 안전성 프로파일도 비슷한 것으로 평가되었음 상세"
    val, conf = ec.derive_comparator(compact(dr))
    assert val == "동등" and conf == "high"


def test_comparator_superior():
    dr = "기존 치료법 대비 임상적 유용성 개선이 인정되어 우월한 효과를 보였음 상세 기술내용"
    val, conf = ec.derive_comparator(compact(dr))
    assert val == "우위" and conf == "med"


def test_comparator_improvement_beats_동등오탐():
    # 실데이터(658/684/686) 유형: '임상적 유용성 개선 인정' + '치료적 위치가 동등한 제품이
    # 없고'(동등 오탐 유발 문구) → 우위로 판정되어야 함.
    dr = ("무진행생존기간(PFS), 전체생존기간(OS) 등이 개선되었으므로 임상적 유용성 개선이 "
          "인정되나 소요비용이 고가임. 다만 치료적 위치가 동등한 제품(치료법)이 없고 생존을 "
          "위협할 정도의 심각한 질환에 사용되는 약제임")
    val, conf = ec.derive_comparator(compact(dr))
    assert val == "우위" and conf == "med"


def test_comparator_improvement_negated_not_우위():
    dr = "대체요법 대비 임상적 유용성 개선이 인정되지 않으며 추가 근거가 필요함 상세 기술"
    val, _ = ec.derive_comparator(compact(dr))
    assert val != "우위"


def test_comparator_inferior():
    dr = "동일차수 비교약제 대비 전체생존 효과가 열등하여 임상적 유용성에 의문이 있음 상세"
    val, conf = ec.derive_comparator(compact(dr))
    assert val == "열위" and conf == "high"


def test_comparator_inferior_negated_not_열위():
    # "열등하다고 보기 어려우나" = 부정 → 열위 아님(부정어 우선). 다른 우위 단서도 없음.
    dr = "임상적 유용성이 열등하다고 보기 어려우나 소요비용이 대체약제보다 다소 비싼 편임 상세"
    val, _ = ec.derive_comparator(compact(dr))
    assert val != "열위"


def test_comparator_strong_inferior_beats_동등어():
    # 실데이터 유형: '유용성이 열등함으로 비급여' — '유사' 동등어가 있어도 강한 열위가 우선.
    dr = "대체약제 대비 효과가 유사한 측면도 있으나 상대적 임상적 유용성이 열등함으로 비급여함 상세"
    val, conf = ec.derive_comparator(compact(dr))
    assert val == "열위" and conf == "high"


def test_comparator_비열등_not_열위():
    # '비열등'은 동등 신호 — 강한 열위 패턴이 어절경계로 오탐하면 안 됨.
    dr = "대체약제 대비 효과가 비열등하고 소요비용이 저렴하여 비용 효과적이므로 급여 적정 상세"
    val, _ = ec.derive_comparator(compact(dr))
    assert val == "동등"


def test_comparator_발열등_not_열위():
    # '발열 등의 증상' → '발열등' 어절경계 오탐 방지(before-guard).
    dr = "야간 발열 등의 증상이 있는 거대 또는 진행성 환자에서 임상적 유효성이 확인되었음 상세"
    val, _ = ec.derive_comparator(compact(dr))
    assert val != "열위"


# ── RuleBasis outcome-gate ──
def _row(**kw):
    base = dict(id=1, review_result=None, approval_driver="", has_rsa=0, pe_waiver=0,
               rsa_type_hint="", policy_tags="", consulted_societies="", manufacturer="")
    base.update(kw)
    return base


def test_basis_reject_cost_only_when_미통과():
    dr = "투약비용이 고가로 비용효과성이 불분명하므로 비급여함 상세 기술 내용을 채운다"
    ct = compact(dr)
    row = _row(approval_driver="REJECTED_COST", review_result="REJECTED")
    bases = ec.make_basis(row, ct, "미통과", "동등", False)
    assert any(b["code"] == "KR-REJECT-COST" for b in bases)
    # 오표기 차단: 같은 driver라도 label=통과면 KR-REJECT-COST 없어야
    bases_pass = ec.make_basis(row, ct, "통과", "동등", False)
    assert not any(b["code"] == "KR-REJECT-COST" for b in bases_pass)


def test_basis_requires_excerpt():
    # driver=COST_EFFECTIVE + 통과 지만 dr 에 비용효과 발췌 없음 → basis 생략.
    dr = "본 약제는 희귀질환 치료제로 사회적 요구도만 기술되어 있고 다른 근거는 없음 상세"
    ct = compact(dr)
    row = _row(approval_driver="COST_EFFECTIVE", review_result="APPROVED")
    bases = ec.make_basis(row, ct, "통과", "동등", False)
    assert not any(b["code"] == "KR-COST-EFFECTIVE" for b in bases)


def test_basis_rsa_cap_vs_refund():
    dr = "총액제한형 위험분담 계약을 조건으로 급여의 적정성이 있음 상세 기술 내용 채움"
    ct = compact(dr)
    row = _row(has_rsa=1, rsa_type_hint="총액제한", review_result="APPROVED")
    bases = ec.make_basis(row, ct, "통과", "동등", False)
    assert any(b["code"] == "KR-RSA-CAP" for b in bases)
    assert all(b["sourceExcerpt"] for b in bases)  # 발췌 필수


def test_label_mapping():
    assert ec.label_of("APPROVED") == "통과"
    assert ec.label_of("CONDITIONAL_APPROVED") == "통과"
    assert ec.label_of("REJECTED") == "미통과"
    assert ec.label_of("UNKNOWN") is None


# ── 내용 기반 해시 ──
def test_db_content_hash_deterministic_and_content_sensitive():
    rows = [
        {"id": 1, "review_result": "APPROVED", "decision_reason": "가나다"},
        {"id": 2, "review_result": "REJECTED", "decision_reason": "라마바"},
    ]
    h1 = ec.db_content_hash(rows)
    h2 = ec.db_content_hash([dict(r) for r in rows])
    assert h1 == h2 and len(h1) == 12
    rows2 = [dict(rows[0]), {"id": 2, "review_result": "REJECTED", "decision_reason": "라마사"}]
    assert ec.db_content_hash(rows2) != h1
