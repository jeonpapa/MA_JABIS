"""경쟁사 동향 카드 품질 가드 — badge 재분류 · 회사 귀속 · 중복 묶음 · 재분류 스크립트.

실데이터(prod 2026-10-17 GET /api/competitor-trends) 에서 사용자 지적 사례를 그대로 옮겼다.
순수 함수 + 임시 sqlite 만 사용 (pandas/flask 불필요). `python3 -m unittest` 로도 실행 가능.
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agents import competitor_trend_quality as q  # noqa: E402

TRACKED = [
    {"query": "옵디보", "company": "BMS Korea", "logo": "BMS", "color": "#3B82F6"},
    {"query": "티쎈트릭", "company": "Roche Korea", "logo": "RC", "color": "#EF4444"},
    {"query": "임핀지", "company": "AstraZeneca Korea", "logo": "AZ", "color": "#00E5CC"},
    {"query": "바벤시오", "company": "Merck/Pfizer Korea", "logo": "MK", "color": "#8B5CF6"},
    {"query": "테빔브라", "company": "BeiGene Korea", "logo": "BG", "color": "#06B6D4"},
    {"query": "타그리소", "company": "AstraZeneca Korea", "logo": "AZ", "color": "#00E5CC"},
    {"query": "엔허투", "company": "Daiichi Sankyo / AZ", "logo": "DS", "color": "#F59E0B"},
    {"query": "트로델비", "company": "Gilead Korea", "logo": "GL", "color": "#10B981"},
    {"query": "빌로이", "company": "Astellas Korea", "logo": "AS", "color": "#EC4899"},
    {"query": "파드셉", "company": "Astellas/Pfizer Korea", "logo": "AS", "color": "#EC4899"},
    {"query": "윈레브에어", "company": "MSD Korea", "logo": "MSD", "color": "#00857C"},
]
BM = q.build_brand_map(TRACKED)


def card(id_, date, company, badge, headline, detail="", source_type="auto_naver", tier=1):
    return {"id": id_, "date": date, "company": company, "badge": badge, "badgeColor": "",
            "headline": headline, "detail": detail, "source_type": source_type,
            "source_tier": tier, "source": "데일리팜", "url": f"https://x/{id_}",
            "logo": None, "color": None}


class BadgeGuardTest(unittest.TestCase):
    def assertBadge(self, headline, detail, badge, expected):
        got, _ = q.guard_badge(headline, detail, badge)
        self.assertEqual(got, expected, f"{headline!r}: {badge} → {got} (expected {expected})")

    def test_fda_approval_labelled_launch_becomes_foreign(self):
        self.assertBadge("티쎈트릭, dMMR 결장암 보조요법 승인",
                         "FDA가 로슈의 티쎈트릭을 dMMR 결장암의 보조요법으로 승인하였다.",
                         "신규 출시", "해외 승인")
        self.assertBadge("EU, 엔허투 1차 치료제로 승인", "엔허투가 유럽에서 승인받았다.", "신규 출시", "해외 승인")
        self.assertBadge("영국, 엔허투 급여 등재 결정", "영국 NICE가 엔허투의 급여를 등재하기로 결정했다.",
                         "급여 등재", "해외 승인")

    def test_fda_headline_under_other_badge(self):
        self.assertBadge("머크 신장암 치료제 FDA 승인", "머크의 '웰리렉'과 '렌비마' 병용요법이 미국 FDA의 승인을 받아",
                         "전략 변화", "해외 승인")

    def test_domestic_approval_stays_launch(self):
        self.assertBadge("트로델비, 삼중음성유방암 치료제로 허가",
                         "트로델비가 삼중음성유방암 치료제로 국내에서 처음으로 허가받았다.", "신규 출시", "신규 출시")

    def test_foreign_application_not_approval(self):
        self.assertBadge("타그리소 병용치료 美 허가신청", "미국 허가를 신청했다.", "전략 변화", "전략 변화")

    def test_pending_reimbursement(self):
        self.assertBadge("테빔브라, 3개 적응증 급여 확대",
                         "면역항암제 테빔브라가 추가 3개 적응증에 대해 급여 확대를 추진 중이다. 연내 심평원에 제출될 예정으로",
                         "급여 등재", "급여 추진")
        self.assertBadge("윈레브에어 신속등재 신청", "", "급여 등재", "급여 추진")
        self.assertBadge("윈레브에어, 연내 급여 등재 가능성", "", "급여 등재", "급여 추진")
        self.assertBadge("티쎈트릭, 초기 비소세포폐암 급여 적용 유력", "다음 달부터 급여 적용이 유력해졌다.",
                         "급여 등재", "급여 추진")

    def test_confirmed_listing_stays(self):
        self.assertBadge("빌로이, 건강보험 급여 적용 시작", "2026년 10월 1일부터 건강보험 급여를 적용받기 시작했다.",
                         "급여 등재", "급여 등재")
        self.assertBadge("테빔브라, 5개 적응증 급여 확대", "10월 1일부터 5개 적응증에 대해 건강보험 급여가 확대 적용된다.",
                         "급여 등재", "급여 등재")
        self.assertBadge("빌로이 급여 등재 완료", "마지막 관문을 통과했다.", "급여 등재", "급여 등재")
        self.assertBadge("파드셉, 보험 급여 등재 성공", "보험 급여 등재에 성공하며 가능성을 보여주고", "급여 등재", "급여 등재")

    def test_price_settlement(self):
        self.assertBadge("파드셉·키트루다 약가 타결", "", "급여 등재", "가격 변동")
        self.assertBadge("'키트루다·파드셉' 병용요법 급여 협상 타결", "", "급여 등재", "가격 변동")
        self.assertBadge("테빔브라 급여 상한액 인하", "", "급여 등재", "가격 변동")

    def test_clinical_headline_on_reimbursement_card(self):
        self.assertBadge("렉라자 병용요법, 타그리소 대비 생존 이점 입증", "급여 적용 필요성", "급여 등재", "임상 진행")

    def test_manual_card_untouched(self):
        g = q.guard_card(card(1, "2026-10-10", "Roche Korea", "신규 출시", "티쎈트릭 FDA 승인",
                              "FDA 승인", source_type="manual"), BM)
        self.assertEqual(g["badge"], "신규 출시")
        self.assertIsNone(g["badge_reason"])

    def test_badges_registered(self):
        for b in ("해외 승인", "급여 추진"):
            self.assertIn(b, q.ALLOWED_BADGES)
            self.assertIn(b, q.BADGE_COLOR)


class CompanyGuardTest(unittest.TestCase):
    def test_welireg_news_under_bms_repaired_to_msd(self):
        g = q.guard_company("머크 신장암 치료제 FDA 승인",
                            "머크의 '웰리렉'과 '렌비마' 병용요법이 미국 FDA의 승인을 받아 신장암 2차 치료에 진입했다. "
                            "이는 옵디보와 병용되는 카보메틱스에 압박을 가할 것으로 예상된다.",
                            "BMS Korea", BM)
        self.assertEqual(g["company"], "MSD Korea")
        self.assertEqual(g["brand"], "웰리렉")
        self.assertTrue(g["reason"].startswith("brand:웰리렉"))

    def test_combo_keeps_card_company(self):
        g = q.guard_company("키트루다-파드셉, 방광암 치료 옵션 확대", "한국MSD가 키트루다와 파드셉을 결합한",
                            "Astellas/Pfizer Korea", BM)
        self.assertEqual(g["company"], "Astellas/Pfizer Korea")
        self.assertIsNone(g["reason"])

    def test_combo_on_unrelated_company_goes_to_competitor_brand(self):
        g = q.guard_company("'키트루다·파드셉' 병용요법 급여 협상 타결",
                            "이는 바벤시오 유지요법과의 경쟁에서 중요한 변화", "Merck/Pfizer Korea", BM)
        self.assertEqual(g["company"], "Astellas/Pfizer Korea")
        self.assertEqual(g["meta"]["logo"], "AS")

    def test_alias_msd(self):
        g = q.guard_company("웰리렉, 신장암 병용 FDA 승인", "", "MSD", BM)
        self.assertEqual(g["company"], "MSD Korea")

    def test_summary_contamination_flagged_not_fixed(self):
        g = q.guard_card(card(284, "2026-10-14", "MSD Korea", "급여 등재", "윈레브에어, 연내 급여 등재 가능성",
                              "윈레브에어가 신속등재 절차를 선택하여 림카토의 약가 협상에 돌입했다. 9월 약평위를 통과한 후"), BM)
        self.assertEqual(g["company"], "MSD Korea")
        self.assertTrue(g["low_confidence"])
        self.assertTrue(any("림카토" in f for f in g["flags"]))

    def test_comparison_mention_not_flagged(self):
        g = q.guard_card(card(234, "2026-09-17", "AstraZeneca Korea", "임상 진행",
                              "렉라자 병용요법, 타그리소 대비 생존 이점 입증",
                              "렉라자와 리브리반트 병용요법이 타그리소 대비 사망 위험을 25% 낮추며"), BM)
        self.assertFalse(g["low_confidence"])

    def test_untracked_subject_not_repaired(self):
        g = q.guard_company("FDA, 지헤라 HER2 양성 위암 승인", "테빔브라와 화학요법의 병용요법", "BeiGene Korea", BM)
        self.assertEqual(g["company"], "BeiGene Korea")  # 추적 대상 아님 → 교정 X, 플래그만
        self.assertTrue(any(f.startswith("company_unverified") for f in g["flags"]))


class GroupingTest(unittest.TestCase):
    def _items(self):
        return [
            card(283, "2026-10-12", "Astellas Korea", "급여 등재", "빌로이, 건강보험 급여 등재", "빌로이 등재되었다"),
            card(279, "2026-10-09", "Astellas Korea", "급여 등재", "빌로이, 건강보험 급여 적용 시작"),
            card(277, "2026-10-09", "Astellas Korea", "급여 등재", "빌로이, 건강보험 급여 등재"),
            card(275, "2026-10-08", "Astellas Korea", "급여 등재", "빌로이 건강보험 급여 등재"),
            card(281, "2026-10-12", "Astellas/Pfizer Korea", "급여 등재", "파드셉, 보험 급여 등재 성공"),
            card(278, "2026-10-09", "Astellas/Pfizer Korea", "급여 등재", "파드셉+키트루다 급여 등재"),
            card(273, "2026-10-07", "Astellas/Pfizer Korea", "급여 등재", "파드셉 병용요법 급여 등재"),
            # 다른 사건 (40일 전) — 묶이면 안 됨
            card(100, "2026-08-28", "Astellas Korea", "급여 등재", "빌로이 급여 3주년"),
            # 같은 브랜드 다른 임상 — 유사도 낮으면 별개
            card(230, "2026-09-15", "AstraZeneca Korea", "임상 진행", "타그리소 보조요법 효과 입증"),
            card(225, "2026-09-15", "AstraZeneca Korea", "임상 진행", "타그리소 보조요법 효과 입증"),
            card(191, "2026-09-12", "AstraZeneca Korea", "임상 진행", "타그리소·사볼리티닙 병용 3상 성공"),
        ]

    def test_groups_same_event(self):
        items = q.annotate_trends(self._items(), BM)
        by = {i["id"]: i for i in items}
        self.assertEqual(by[283]["group_size"], 4)
        self.assertEqual({by[x]["group_id"] for x in (283, 279, 277, 275)}, {283})
        self.assertEqual(len(by[283]["related"]), 3)
        self.assertEqual(by[279]["related"], [])
        self.assertEqual(by[281]["group_size"], 3)
        self.assertEqual(by[100]["group_size"], 1)
        self.assertEqual(by[230]["group_id"], by[225]["group_id"])
        self.assertEqual(by[191]["group_size"], 1)

    def test_rows_preserved_and_order_kept(self):
        src = self._items()
        ids = [i["id"] for i in src]
        out = q.annotate_trends(src, BM)
        self.assertEqual([i["id"] for i in out], ids)

    def test_manual_card_preferred_as_primary(self):
        items = self._items()
        items[2]["source_type"] = "manual"  # 277
        out = {i["id"]: i for i in q.annotate_trends(items, BM)}
        self.assertEqual(out[283]["group_id"], 277)
        self.assertEqual(len(out[277]["related"]), 3)

    def test_override_records_original(self):
        out = q.annotate_trends([card(1, "2026-10-10", "Roche Korea", "신규 출시", "티쎈트릭, dMMR 결장암 보조요법 승인",
                                      "FDA가 승인하였다")], BM)
        self.assertEqual(out[0]["badge"], "해외 승인")
        self.assertEqual(out[0]["quality"]["badge_original"], "신규 출시")
        self.assertIn("sky", out[0]["badgeColor"])


_SCHEMA = """
CREATE TABLE competitor_trend (
    id INTEGER PRIMARY KEY AUTOINCREMENT, company TEXT NOT NULL, logo TEXT, color TEXT,
    badge TEXT NOT NULL, badge_color TEXT, headline TEXT NOT NULL, detail TEXT NOT NULL,
    date TEXT NOT NULL, source TEXT, url TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'manual', importance TEXT, source_tier INTEGER);
"""


class ReclassifyScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        conn = sqlite3.connect(self.db)
        conn.executescript(_SCHEMA)
        rows = [
            ("BMS Korea", "전략 변화", "머크 신장암 치료제 FDA 승인",
             "머크의 '웰리렉'과 '렌비마' 병용요법이 미국 FDA의 승인을 받아", "auto_naver"),
            ("Roche Korea", "신규 출시", "티쎈트릭, dMMR 결장암 보조요법 승인", "FDA가 승인하였다", "manual"),
            ("BeiGene Korea", "급여 등재", "테빔브라, 3개 적응증 급여 확대", "급여 확대를 추진 중이다.", "promoted"),
            ("Astellas Korea", "급여 등재", "빌로이, 건강보험 급여 적용 시작", "적용받기 시작했다", "auto_naver"),
        ]
        for c, b, h, d, st in rows:
            conn.execute("INSERT INTO competitor_trend (company, badge, headline, detail, date, created_at, "
                         "updated_at, source_type) VALUES (?,?,?,?,?,?,?,?)",
                         (c, b, h, d, "2026-10-01", "x", "x", st))
        conn.commit()
        conn.close()
        sys.path.insert(0, str(REPO / "scripts"))
        import reclassify_competitor_trends as rc
        self.rc = rc

    def tearDown(self):
        self.tmp.cleanup()

    def _rows(self):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute("SELECT id, company, badge, source_type FROM competitor_trend ORDER BY id").fetchall()
        finally:
            conn.close()

    def test_dry_run_default_no_write(self):
        before = self._rows()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.rc.main(["--db", str(self.db), "--json"])
        self.assertEqual(rc, 0)
        report = json.loads(buf.getvalue())
        self.assertEqual(report["summary"]["mode"], "dry-run")
        self.assertEqual(report["summary"]["changes"], 2)  # id1 (badge+company), id3 (badge); manual id2 skip
        self.assertEqual(self._rows(), before)

    def test_apply_updates_auto_only(self):
        with redirect_stdout(io.StringIO()):
            self.rc.main(["--db", str(self.db), "--apply"])
        rows = {r[0]: r for r in self._rows()}
        self.assertEqual(rows[1][1:3], ("MSD Korea", "해외 승인"))
        self.assertEqual(rows[2][2], "신규 출시")          # manual 보존
        self.assertEqual(rows[3][2], "급여 추진")
        self.assertEqual(rows[4][2], "급여 등재")
        # 멱등 — 두 번째 실행은 변경 0
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.rc.main(["--db", str(self.db), "--json"])
        self.assertEqual(json.loads(buf.getvalue())["summary"]["changes"], 0)


if __name__ == "__main__":
    unittest.main()
