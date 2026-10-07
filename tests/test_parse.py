"""Offline tests against saved 99acres payloads (no network, no database).

    python -m unittest discover -s tests -v
"""
import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from acres import config  # noqa: E402
from acres.http import extract_initial_data  # noqa: E402
from acres.parse import parse_listing, parse_project, split_rera_number, normalize_rera_number  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class SearchUrl(unittest.TestCase):
    def test_tn_matches_browser_panel(self):
        url = config.search_url("TN", 1)
        self.assertIn("/search/property/buy/tamilnadu?", url)
        self.assertIn("city=227", url)
        self.assertIn("availability=3", url)
        self.assertNotIn("page=", url)
        self.assertIn("&page=3", config.search_url("TN", 3))

    def test_all_states_have_ids(self):
        for code, s in config.STATES.items():
            self.assertTrue(s["city_id"].isdigit(), code)


class Listing(unittest.TestCase):
    def test_tuples(self):
        tuples, meta = parse_listing(load("search_tn_page1.json"))
        self.assertEqual(len(tuples), 15)
        self.assertEqual(meta["count"], 188)
        self.assertFalse(meta["is_last_page"])
        first = tuples[0]
        self.assertEqual(first["project_id"], "r459201")
        self.assertEqual(first["name"], "Casagrand Aquagrove")
        self.assertEqual(first["url"], "https://www.99acres.com/casagrand-aquagrove-madhavaram-chennai-north-npxid-r459201")
        self.assertEqual(first["state"], "Tamil Nadu")
        self.assertEqual(first["price_min"], 10400000)
        self.assertEqual(first["price_per_sqft"], 7700)
        self.assertTrue(first["is_rera"])
        self.assertIn("New Launch", first["possession_label"])
        self.assertEqual(len({t["project_id"] for t in tuples}), 15)


class Project(unittest.TestCase):
    def test_tamil_nadu_project_with_rera(self):
        rec = parse_project(load("project_r459201.json"),
                            "https://www.99acres.com/casagrand-aquagrove-madhavaram-chennai-north-npxid-r459201",
                            listing={"price_per_sqft": 7700, "tags": ["NO_BROKERAGE"]})
        self.assertEqual(rec["project_id"], "r459201")
        self.assertEqual(rec["name"], "Casagrand Aquagrove")
        self.assertEqual(rec["state"], "Tamil Nadu")
        self.assertEqual(rec["city"], "Chennai North")
        self.assertEqual(rec["locality"], "Madhavaram")
        self.assertEqual(rec["builder_legal_name"], "Casagrand Builder Private Limited")
        self.assertEqual(rec["construction_status_code"], "NEW_LAUNCH")
        self.assertEqual(rec["completion_date"], "Jun, 2030")
        self.assertEqual(rec["completion_on"], date(2030, 6, 30))
        self.assertEqual(rec["price_min"], 10400000)
        self.assertEqual(rec["price_max"], 21479271)
        self.assertEqual(rec["price_per_sqft"], 7700)
        self.assertTrue(rec["is_rera"])
        self.assertEqual(rec["rera_status"], "REGISTERED")
        self.assertEqual(rec["rera_number"], "TNRERA/29/BLG/0183/2026")
        self.assertEqual(rec["rera_number_key"], "TNRERA29BLG01832026")
        self.assertEqual(rec["rera_url"], "https://rera.tn.gov.in/")
        self.assertEqual(rec["rera_phases"][0]["title"], "Phase 1")
        self.assertEqual(rec["unit_count"], 544)
        self.assertEqual(rec["tower_count"], 3)
        self.assertEqual(rec["floor_count"], 28)
        self.assertEqual(rec["total_area_text"], "6.7 acres")
        self.assertEqual(rec["open_area_pct"], 85)
        self.assertTrue(rec["configurations"])
        self.assertEqual(rec["configurations"][0]["bhk"], "2 BHK")
        self.assertTrue(rec["facilities"])
        self.assertTrue(rec["nearby_places"])
        self.assertTrue(rec["faqs"])
        self.assertEqual(rec["yoy_price_change"], 16.7)
        self.assertIsNotNone(rec["rating_avg"])
        self.assertIn("Good Public Transport", rec["likes"])
        self.assertTrue(rec["brochure_url"].endswith(".pdf"))
        self.assertEqual(len(rec["content_hash"]), 64)

    def test_goa_project_without_rera(self):
        rec = parse_project(load("project_r469160_goa.json"),
                            "https://www.99acres.com/seraano-by-aarza-siolim-north-goa-npxid-r469160")
        self.assertEqual(rec["project_id"], "r469160")
        self.assertEqual(rec["state"], "Goa")
        self.assertEqual(rec["pincode"], "403517")
        self.assertFalse(rec["is_rera"])
        self.assertIsNone(rec["rera_number"])
        self.assertEqual(rec["builder_name"], "Aarza Realty")
        self.assertEqual(rec["builder_id"], "149020")
        self.assertEqual(rec["price_min"], 46500000)   # basic price min was 0 -> fell back to marketing
        self.assertEqual(rec["price_max"], 69500000)
        self.assertEqual(rec["unit_count"], 8)
        self.assertTrue(rec["no_brokerage"])
        self.assertTrue(rec["has_3d_floor_plans"])
        self.assertEqual(rec["top_facilities_cnt"], 10)
        self.assertEqual(rec["payment_plan_url"].split("/")[-1][-4:], ".pdf")

    def test_hash_changes_with_price(self):
        data = load("project_r469160_goa.json")
        url = "https://www.99acres.com/x-npxid-r469160"
        a = parse_project(data, url)
        data["projectDetailState"]["pageData"]["marketing"]["maxPrice"] = "70000000"
        data["projectDetailState"]["pageData"]["basicDetails"]["price"]["max"] = 70000000
        b = parse_project(data, url)
        self.assertNotEqual(a["content_hash"], b["content_hash"])


class ReraNumber(unittest.TestCase):
    def test_split(self):
        self.assertEqual(split_rera_number("TN/35/Building/0097/2025dated 02-04-2025"),
                         ("TN/35/Building/0097/2025", date(2025, 4, 2)))
        self.assertEqual(split_rera_number("TNRERA/2/BLG/0134/2026 dated 16.04.2026"),
                         ("TNRERA/2/BLG/0134/2026", date(2026, 4, 16)))
        self.assertEqual(split_rera_number("TN/29/Building/0531/2022 dated 28/12/2022"),
                         ("TN/29/Building/0531/2022", date(2022, 12, 28)))
        self.assertEqual(split_rera_number("PRGO01231896"), ("PRGO01231896", None))
        self.assertEqual(split_rera_number(None), (None, None))

    def test_normalize(self):
        self.assertEqual(normalize_rera_number("P 5 1 7 0 0 0 1 2 3 4 5"), "P51700012345")
        self.assertEqual(normalize_rera_number("prm/ka/rera/1251/446/pr/010101/000001"), "PRMKARERA1251446PR010101000001")


class InitialData(unittest.TestCase):
    def test_extract(self):
        html = '<html><script>window.__initialData__={"a":{"b":[1,2]},"c":"x"};</script><p>tail</p></html>'
        self.assertEqual(extract_initial_data(html), {"a": {"b": [1, 2]}, "c": "x"})
        self.assertIsNone(extract_initial_data("<html>loader</html>"))


if __name__ == "__main__":
    unittest.main()
