"""UK MIMS 연속 포장행 + Ontario EAP mg당 가격 회귀 (2026-10-17 Winrevair)."""
import unittest

from agents.scrapers.uk_mims import UkMimsScraper as _UK
from agents.scrapers.ca_ontario import CaOntarioScraper as _CA


class MimsContinuationTests(unittest.TestCase):
    def test_semicolon_pack_inherits_strength(self):
        html = ("<h1>Winrevair</h1><p>Price: 45mg powder and solvent for soln for inj: 1-vial kit=£5422.50; "
                "2-vial kit=£10845.00. 60mg powder and solvent for soln for inj: 1-vial kit=£5422.50; "
                "2-vial kit=£10845.00.</p><p>Legal Class: POM</p>")
        rows = _UK._parse_price_page(html, "https://example")
        ds = [r["dosage_strength"] for r in rows]
        self.assertEqual(len(rows), 4, ds)
        self.assertTrue(ds[1].startswith("45mg powder and solvent for soln for inj"), ds[1])
        self.assertIn("2-vial kit", ds[1])
        self.assertTrue(ds[3].startswith("60mg"), ds[3])
        self.assertFalse(any(d.startswith(";") for d in ds))

    def test_plain_lines_unchanged(self):
        self.assertEqual(_UK._continue_pack_line("40mg tab, 90", "x"), "40mg tab, 90")


class OntarioPerMgTests(unittest.TestCase):
    def setUp(self):
        self.s = _CA.__new__(_CA)

    def test_per_mg_converted_with_explicit_strength(self):
        price, note = self.s._price_per_listed_unit("$193.7145/ mg", "45 mg /Vial")
        self.assertAlmostEqual(price, 193.7145 * 45, places=3)
        self.assertIn("mg당", note)

    def test_per_mg_without_mg_strength_is_not_guessed(self):
        price, note = self.s._price_per_listed_unit("$12.50/mg", "1 Kit")
        self.assertIsNone(price)
        self.assertIn("보류", note)

    def test_regular_unit_price_unchanged(self):
        price, note = self.s._price_per_listed_unit("$238.7160", "240 mg")
        self.assertAlmostEqual(price, 238.716)
        self.assertIsNone(note)


if __name__ == "__main__":
    unittest.main()
