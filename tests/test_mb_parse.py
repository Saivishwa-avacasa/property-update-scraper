"""Offline tests against saved Magicbricks payloads (no network, no database).

    python -m unittest discover -s tests -v
"""
import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mb import config  # noqa: E402
from mb.http import extract_state  # noqa: E402
from mb.parse import (parse_card, parse_project, parse_rera_validity, strip_html,  # noqa: E402
                      date_month_year, TRACKED_FIELDS)

FIX = ROOT / "tests" / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class Cards(unittest.TestCase):
    def setUp(self):
        self.data = load("mb_cards_goa_page1.json")
        self.cards = [parse_card(c, "2669") for c in self.data["projectsCards"]]

    def test_counts(self):
        self.assertEqual(self.data["totalCount"], 71)
        self.assertEqual(len(self.cards), 5)

    def test_first_card(self):
        c = self.cards[0]
        self.assertEqual(c["psmid"], "5436337")
        self.assertEqual(c["name"], "Mohidins Mirage")
        self.assertEqual(c["city"], "Goa")
        self.assertEqual(c["city_id"], "2669")
        self.assertEqual(c["locality"], "Sancoale")
        self.assertEqual(c["pincode"], "403726")
        self.assertTrue(c["url"].startswith("https://www.magicbricks.com/mohidins-mirage-sancoale-goa-pdpid-"))
        self.assertTrue(c["is_rera"])
        self.assertEqual(c["launch_date"], date(2025, 4, 1))
        self.assertEqual(c["possession_date"], date(2028, 12, 1))
        self.assertEqual(c["status"], "Under Construction")
        self.assertEqual(c["total_units"], 97)

    def test_every_card_has_identity(self):
        for c in self.cards:
            self.assertTrue(c["psmid"] and c["url"] and c["name"], c)


class Project(unittest.TestCase):
    def setUp(self):
        card = parse_card(load("mb_cards_goa_page1.json")["projectsCards"][0], "2669")
        self.rec = parse_project(load("mb_project_5436337_goa.json"), card["url"], card=card)

    def test_rera(self):
        r = self.rec
        self.assertTrue(r["is_rera"])
        self.assertEqual(r["rera_number"], "PRGO05252471")
        self.assertEqual(r["rera_number_key"], "PRGO05252471")
        self.assertEqual(r["rera_phases"], [{"number": "PRGO05252471", "valid_till": "12/2030", "phase": "Phase1"}])
        self.assertEqual(r["rera_portal_url"], "https://rera.goa.gov.in/")

    def test_header_and_price(self):
        r = self.rec
        self.assertEqual(r["developer_name"], "Mohidin Properties And Holdings")
        self.assertEqual(r["price_text"], "6.8 Lac - 1.04 Cr")
        self.assertEqual((r["price_min"], r["price_max"]), (680214, 10498243))
        self.assertAlmostEqual(r["latitude"], 15.386429)
        self.assertEqual(r["full_address"], "Plot no. E, Survey no. 89/1, Upasnagar, Sancoale, Goa")
        self.assertEqual(r["possession_date"], date(2028, 12, 1))
        self.assertEqual(r["status"], "Under Construction")

    def test_units_amenities_locality(self):
        r = self.rec
        self.assertEqual(len(r["configurations"]), 4)
        self.assertIn("Library", r["amenities"])
        self.assertEqual(r["developer_total_projects"], 3)
        self.assertEqual(r["locality_rank"], "36/227")
        self.assertEqual(r["locality_ratings"]["safetyRating"], 4.4)
        self.assertTrue(r["faqs"] and r["faqs"][0]["q"].startswith("What is the size"))

    def test_card_fields_survive(self):
        # per-sqft price is only on the card for this project; it must not be dropped
        self.assertIn("price_per_sqft_min", self.rec)
        self.assertEqual(self.rec["city_id"], "2669")

    def test_state_code(self):
        self.assertEqual(self.rec["state"], "Goa")
        self.assertEqual(self.rec["state_code"], "GA")

    def test_card_wins_for_list_tracked(self):
        # the card value is stored for daily-tracked fields, so the page never flips it
        card = parse_card(load("mb_cards_goa_page1.json")["projectsCards"][0], "2669")
        card["price_min"] = 123
        rec = parse_project(load("mb_project_5436337_goa.json"), card["url"], card=card)
        self.assertEqual(rec["price_min"], 123)
        self.assertEqual(rec["rera_number"], "PRGO05252471")   # page-only field still filled

    def test_hash_and_tracked(self):
        self.assertEqual(len(self.rec["content_hash"]), 64)
        for f in TRACKED_FIELDS:
            self.assertIn(f, self.rec)


class Helpers(unittest.TestCase):
    def test_strip_html(self):
        self.assertEqual(strip_html("AI_DATA <p>Hi</p><ul><li>a</li><li>b &amp; c</li></ul>"), "Hi\n• a\n• b & c")

    def test_month_year(self):
        self.assertEqual(date_month_year("Dec, 2028"), date(2028, 12, 1))
        self.assertEqual(date_month_year("Jun'27"), date(2027, 6, 1))
        self.assertIsNone(date_month_year("soon"))

    def test_rera_validity_multi(self):
        self.assertEqual(len(parse_rera_validity("[A1|12/2030|Phase1],[B2|06/2031|Phase2]")), 2)
        self.assertIsNone(parse_rera_validity(None))

    def test_extract_state(self):
        html = 'x<script>window.SERVER_PRELOADED_STATE_ = {"a": {"b": 1}};</script>'
        self.assertEqual(extract_state(html), {"a": {"b": 1}})

    def test_india_feed_url(self):
        from unittest import mock
        from mb.http import Site
        site = Site()
        site._warmed = True
        with mock.patch.object(site, "_get") as get:
            get.return_value.json.return_value = {}
            site.cards(None, 3)
            self.assertIn("pageNo=3&magichome=Y", get.call_args[0][0])
            site.cards("2669", 1)
            self.assertIn("pageNo=1&city=2669", get.call_args[0][0])

    def test_config_cities_unique(self):
        ids = [cid for s in config.STATES.values() for cid in s["cities"]]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
