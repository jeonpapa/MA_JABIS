"""일반약제 급여기준 파서·최신화·KCD 검증 (네트워크·실DB 미사용).

python3 -m unittest tests.test_hira_criteria_drug
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.hira_criteria import drug_booklet as dbk  # noqa: E402
from agents.hira_criteria import drug_disease as dd  # noqa: E402
from agents.hira_criteria import drug_latest as dl  # noqa: E402
from agents.hira_criteria.schema import ensure_schema  # noqa: E402

PAGE1 = """<table>
<tr><th>구 분</th><th>세부인정기준 및 방법</th></tr>
<tr><td>[421]<br>Ruxolitinib phosphate 경구제<br>(품명: 자카비정<br>5밀리그램 등)</td><td>허가사항 범위 내에서 아래와 같은 기준으로 투여 시 요양급여를 인정함.<br>가. 항암요법에 투여 시 「암환자에게 처방·투여하는 약제에 대한 요양급여의 적용기준 및 방법에 관한 세부사항」범위 내에서 사용하는 경우<br>나. 스테로이드불응 이식편대숙주질환</td></tr>
</table>"""
# 다음 페이지(청크): 왼쪽 빈 행 = 이어짐, 파이프 표 + 헤더 글자 누락('부인정기준')
PAGE2 = """| 구 분 | 부인정기준 및 방법 |
| --- | --- |
|  | 다. 최대 3년<br>(고시 제2023-197호, ’23.11.1.)<br>(고시 제2026-192호, ’26.10.1.) |
| [612]<br>Aztreonam 주사제<br>(품명 : 호스피라)<br>[612]<br>Colistimethate 주사제<br>(품명 : 후콜리스티메테이트주 등) | 1. 허가사항 범위 내 투여<br>(고시 제2026-117호, ’26.6.1.)<br>1. 다제내성 그람음성균 감염<br>(고시 제2017-193호, ʼ17.11.1.) |
"""


class GosiTest(unittest.TestCase):
    def test_parse_variants(self):
        g = dbk.parse_gosi("(고시 제2026-69호, ’26.4.1.) 그리고 (고시 제2013-127호, ʼ13.9.1.) (고시 제2020-69호, ’20.4.1)")
        self.assertEqual(g, [("고시 제2026-69호", "2026-04-01"), ("고시 제2013-127호", "2013-09-01"),
                             ("고시 제2020-69호", "2020-04-01")])

    def test_latest_is_max_date(self):
        self.assertEqual(dbk.latest_gosi("(고시 제2026-192호, ’26.10.1.) (고시 제2023-197호, ’23.11.1.)"),
                         ("고시 제2026-192호", "2026-10-01"))
        self.assertEqual(dbk.latest_gosi("없음"), (None, None))


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.items, self.stats = dbk.parse_items(PAGE1 + "\n" + PAGE2)

    def test_page_boundary_join(self):
        rux = self.items[0]
        self.assertEqual(rux.class_no, "421")
        self.assertIn("최대 3년", rux.criteria_text)           # 다음 페이지 이어붙임
        self.assertEqual(dbk.latest_gosi(rux.criteria_text)[0], "고시 제2026-192호")
        self.assertEqual(rux.ingredient, "Ruxolitinib phosphate")
        self.assertEqual(rux.dosage_form, "경구제")
        self.assertTrue(rux.brand_text.startswith("자카비정"))

    def test_merged_row_split_by_gosi(self):
        titles = [i.title for i in self.items]
        self.assertEqual(len(self.items), 3)
        az, co = self.items[1], self.items[2]
        self.assertTrue(az.title.startswith("Aztreonam"))
        self.assertEqual(dbk.latest_gosi(az.criteria_text)[0], "고시 제2026-117호")
        self.assertEqual(dbk.latest_gosi(co.criteria_text)[0], "고시 제2017-193호", titles)
        self.assertEqual(self.stats["merged_rows"], 1)

    def test_bracket_non_class_not_title(self):
        items, _ = dbk.parse_items("<table><tr><th>구 분</th><th>세부인정기준 및 방법</th></tr>"
                                   "<tr><td>[399]<br>Lactulose 경구제<br>[전문의약품]<br>(품명 : 듀파락시럽 등)</td>"
                                   "<td>본문<br>(고시 제2013-127호, ’13.9.1.)</td></tr></table>")
        self.assertEqual(len(items), 1)


class MatchTest(unittest.TestCase):
    def test_title_normalization(self):
        a = "Ruxolitinib phosphate 경구제 (품명 : 자카비정5밀리그램 등)"
        b = "Ruxolitinib phosphate 경구제(품명: 자카비정5밀리그램 등)"
        self.assertEqual(dl.match_key(a), dl.match_key(b))
        self.assertEqual(dl.match_key("[일반원칙] 당뇨병용제"), dl.match_key("당뇨병용제"))
        self.assertEqual(dl.gosi_tuple("고시 제2026-192호"), (2026, 192))

    def _items(self):
        return [{"title": "Sirolimus 경구제 (품명 : 라파뮨정 1밀리그램 등)", "class_no": "142", "gosi_no": "고시 제2025-15호",
                 "criteria_text": "old", "latest_basis": "booklet"},
                {"title": "Dupilumab 주사제 (품명 : 듀피젠트)", "class_no": "142", "gosi_no": "고시 제2026-92호",
                 "criteria_text": "old", "latest_basis": "booklet"}]

    def test_newer_gosi_replaces_and_new_form_is_added(self):
        items = self._items()
        rows = [
            {"title": "Dupilumab 주사제(품명: 듀피젠트프리필드주300밀리그램 등)", "gosi_no": "고시 제2026-159호",
             "key": ("20260801", "1", "0005"), "posted_date": "2026-08-01"},
            {"title": "Sirolimus 시럽제(품명: 라파뮨 시럽)", "gosi_no": "고시 제2026-176호",
             "key": ("20260901", "2", "0002"), "posted_date": "2026-09-01"},
            {"title": "Dupilumab 주사제 (품명 : 듀피젠트)", "gosi_no": "고시 제2026-92호",
             "key": ("x", "x", "x"), "posted_date": "2026-05-01"},
        ]
        details = {
            ("20260801", "1", "0005"): {"url": "u1", "sections": {"고시 개정 전체내용": "[229] 새 기준",
                                                                  "고시 개정 고시번호(시행일자)": "고시 제2026-159호(2026.8.1.)"}},
            ("20260901", "2", "0002"): {"url": "u2", "sections": {"고시 신설 전체내용": "[142] 시럽 기준",
                                                                  "고시 신설 고시번호(시행일자)": "고시 제2026-176호(2026.9.1.)"}},
        }
        rep = dl.apply_latest(items, gosi_rows=rows, fetch_detail=lambda *k: details[k])
        dup = next(i for i in items if i["title"].startswith("Dupilumab"))
        self.assertEqual(dup["gosi_no"], "고시 제2026-159호")
        self.assertEqual(dup["effective_date"], "2026-08-01")
        self.assertEqual(dup["latest_basis"], "gosi_newer")
        siro_tab = next(i for i in items if "라파뮨정" in i["title"])
        self.assertEqual(siro_tab["criteria_text"], "old")      # 제형 다르면 교체 금지
        self.assertEqual(len(rep["added"]), 1)
        self.assertEqual(rep["added"][0]["gosi"], "고시 제2026-176호")


class KcdTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("ATTACH DATABASE ':memory:' AS common")
        for stmt in ("CREATE TABLE common.kcd_code(code TEXT PRIMARY KEY,name_kr TEXT,name_en TEXT,is_complete INT,main_dx_ok INT,version TEXT)",
                     "CREATE TABLE common.kcd_alias(code TEXT,name_kr TEXT,name_en TEXT,PRIMARY KEY(code,name_kr))"):
            self.conn.execute(stmt)
        for code, nm in [("T860", "이식편대숙주병"), ("M05", "혈청검사양성 류마티스관절염"), ("M06", "기타 류마티스관절염"),
                         ("L40", "건선")]:
            self.conn.execute("INSERT INTO common.kcd_code VALUES (?,?,?,?,?,?)", (code, nm, "", 1, 1, "t"))
            self.conn.execute("INSERT INTO common.kcd_alias VALUES (?,?,?)", (code, nm, ""))
        self.idx = dd.KcdIndex(self.conn, "common")

    def test_candidates_only_existing_codes(self):
        c = dd.candidates("성인의 류마티스 관절염 및 이식편대숙주질환, 아토피피부염", self.idx)
        codes = {x["code"] for x in c}
        self.assertIn("M05", codes)
        self.assertIn("T860", codes)
        self.assertNotIn("L20", codes)    # 사전에는 있지만 kcd_code 에 없는 코드는 후보 금지

    def test_llm_cannot_inject_codes(self):
        class FakeLLM:
            available = True
            calls = errors = 0

            def chat_json(self, s, u):
                return {"selected": [{"code": "T860", "confidence": 0.9, "label": "이식편대숙주질환"},
                                     {"code": "Z999", "confidence": 0.99}, {"code": "L40", "confidence": 0.9}],
                        "uncovered_terms": []}
        item = {"title": "Ruxolitinib 경구제", "criteria_text": "스테로이드불응 이식편대숙주질환. 「암환자에게 처방·투여하는 약제에 대한 요양급여의 적용기준」범위 내"}
        rows, flags = dd.map_item(item, self.idx, FakeLLM())
        self.assertEqual([r["kcd_code"] for r in rows], ["T860"])   # 후보 밖(L40)·미존재(Z999) 거부
        self.assertIn("onco_delegated", flags)
        self.assertEqual(rows[0]["review_status"], "auto")

    def test_no_llm_fallback_confidence(self):
        rows, _ = dd.map_item({"title": "x", "criteria_text": "건선 환자"}, self.idx, None)
        self.assertEqual((rows[0]["kcd_code"], rows[0]["review_status"]), ("L40", "auto"))      # 큐레이션 0.8
        rows, _ = dd.map_item({"title": "x", "criteria_text": "이식편대숙주병 환자"}, self.idx, None)
        self.assertEqual((rows[0]["kcd_code"], rows[0]["review_status"]), ("T860", "needs_review"))  # 이름매칭 0.6

    def test_schema_has_drug_columns(self):
        c = sqlite3.connect(":memory:")
        ensure_schema(c)
        cols = {r[1] for r in c.execute("PRAGMA table_info(drug_criteria_item)")}
        self.assertTrue({"item_key", "gosi_no", "effective_date", "latest_basis", "latest_url"} <= cols)


if __name__ == "__main__":
    unittest.main()
