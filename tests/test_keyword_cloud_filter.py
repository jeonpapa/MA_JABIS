"""Home 정부 기관 키워드 클라우드 노이즈 필터 — 비의료 기사/토큰 · 불용어 · 복합 정책어.

사례: prod 2026-10-17 /api/home/gov-agency-clouds 국민건강보험공단 클라우드
  ['위험','분담','국가철도공단','산림청','주변','국토부','생활권','도로','철도','피해목','공동방제사업','한국도로공사','등을','민가',...]
순수 함수만 사용 — `python3 -m unittest tests.test_keyword_cloud_filter` 로도 실행 가능.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agents import keyword_cloud_filter as f  # noqa: E402
from agents.gov_agency_clouds import build_agency_clouds, _tokenize  # noqa: E402

NOISE = ["국가철도공단", "산림청", "국토부", "철도", "도로", "한국도로공사", "피해목", "공동방제사업",
         "생활권", "에페", "등을", "민가", "주변", "방안", "기반", "중심"]


def row(agency, title, desc="", date="2026-10-15"):
    return {"brand": agency, "title": title, "description": desc, "url": "https://x/" + title,
            "naver_link": None, "source_name": "데일리팜", "source_domain": "x", "pub_date": date}


class CompoundTest(unittest.TestCase):
    def test_compounds_not_split(self):
        self.assertIn("위험분담", f.normalize_compounds("공단 위험 분담제 재계약"))
        self.assertIn("위험분담", f.normalize_compounds("위험분담계약제(RSA)"))
        self.assertIn("약가유연계약", f.normalize_compounds("약가 유연계약제 도입"))
        self.assertNotIn(" 유연계약 ", f.normalize_compounds("약가유연계약"))
        self.assertIn("신속등재", f.normalize_compounds("100일 신속 등재 시범사업"))

    def test_tokenize_keeps_compound(self):
        toks = _tokenize("공단, 위험분담제 재계약 및 약가 유연계약 논의", frozenset(), frozenset())
        self.assertIn("위험분담", toks)
        self.assertIn("약가유연계약", toks)
        self.assertNotIn("위험", toks)
        self.assertNotIn("분담", toks)

    def test_brand_not_josa_stripped(self):
        toks = _tokenize("빌로이가 급여 등재", frozenset(), frozenset())
        self.assertIn("빌로이", toks)
        self.assertNotIn("빌로", toks)


class NoiseTest(unittest.TestCase):
    def test_noise_tokens(self):
        for t in NOISE:
            self.assertTrue(f.is_noise_token(t), t)
        for t in ("약가", "급여", "위험분담", "약가유연계약", "신속등재", "건강보험", "국민건강보험공단", "제네릭"):
            self.assertFalse(f.is_noise_token(t), t)

    def test_non_health_org_suffix(self):
        self.assertTrue(f.is_noise_token("부산시설공단"))
        self.assertFalse(f.is_noise_token("건보공단"))

    def test_article_relevance(self):
        self.assertFalse(f.is_health_relevant(
            "산림청·국가철도공단, 철도 주변 소나무재선충 피해목 공동방제사업",
            "민가 주변 생활권 위험목 전수조사 및 비용 분담"))
        self.assertFalse(f.is_health_relevant("부산시설공단 도시 고속도로 재난 대응 훈련"))
        self.assertFalse(f.is_health_relevant("홈플러스 퇴직금 미지급 국감", "직원 급여 지급 문제"))
        self.assertTrue(f.is_health_relevant("건보공단, 위험분담 재계약 협상", "신약 약가"))
        self.assertTrue(f.is_health_relevant("공단 약가협상 타결", ""))

    def test_filter_keywords(self):
        kws = [{"text": "국가철도공단", "weight": 90}, {"text": "위험 분담", "weight": 80},
               {"text": "건강보험 재정", "weight": 70}, {"text": "등을", "weight": 60}]
        out = f.filter_keywords(kws)
        texts = [k["text"] for k in out]
        self.assertEqual(texts, ["위험분담", "건강보험 재정"])

    def test_keyword_in_text_compound_variant(self):
        self.assertTrue(f.keyword_in_text("위험분담", "공단 위험 분담제 재계약"))
        self.assertFalse(f.keyword_in_text("위험분담", "공단 비용 협상"))


class AgencyCloudTest(unittest.TestCase):
    def test_nhis_cloud_clean(self):
        rows = [
            row("국민건강보험공단", "산림청·국가철도공단, 철도 주변 피해목 공동방제사업",
                "민가 주변 생활권 위험목 전수조사, 한국도로공사 국토부 비용 분담 등을 논의"),
            row("국민건강보험공단", "산림청 국가철도공단 재선충 방제 협력", "철도 도로 주변 피해목"),
            row("국민건강보험공단", "건보공단, 위험분담제 재계약 약가 협상", "신약 위험 분담 계약 방안 검토"),
            row("국민건강보험공단", "건보공단 약가 유연계약 도입 검토", "약가유연계약제 기반 협상"),
        ]
        out = build_agency_clouds(rows, ["국민건강보험공단"], frozenset({"약가"}), frozenset({"RSA"}))
        self.assertEqual(len(out), 1)
        cloud = out[0]
        self.assertEqual(cloud["article_count"], 2)  # 비의료 기사 2건 drop
        texts = [k["text"] for k in cloud["keywords"]]
        for t in NOISE + ["위험", "분담"]:
            self.assertNotIn(t, texts)
        self.assertIn("위험분담", texts)
        self.assertIn("약가유연계약", texts)
        # 근거뉴스 연결 — 공백 변형 기사도 매칭
        self.assertTrue(cloud["newsByKeyword"]["위험분담"])


if __name__ == "__main__":
    unittest.main()
