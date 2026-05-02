from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


MODULE_PATH = Path(__file__).resolve().parent / "scrape_hoyo_tracker.py"
SPEC = importlib.util.spec_from_file_location("scrape_hoyo_tracker", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class HoyoTrackerScraperTests(unittest.TestCase):
    def test_extract_crimson_initial_codes(self) -> None:
        html = (
            '<html><script>self.__next_f.push([1,"16:[\\"$\\",\\"$L21\\",null,{'
            '\\"initialCodes\\":[{\\"id\\":1,\\"code\\":\\"ABC123\\",\\"code_variants\\":'
            '\\"XYZ987\\",\\"added\\":\\"2026-01-01T00:00:00+00:00\\",\\"start_date\\":null,'
            '\\"expires\\":null,\\"rewards\\":[{\\"item\\":\\"Primogem\\",\\"qty\\":60}]},'
            '{\\"id\\":2,\\"code\\":\\"DEF456\\",\\"code_variants\\":null,\\"added\\":'
            '\\"2026-01-02T00:00:00+00:00\\",\\"start_date\\":null,\\"expires\\":null,'
            '\\"rewards\\":[]}],\\"slug\\":\\"Genshin_Impact\\",\\"children\\":[]}]\\n"])'
            '</script></html>'
        )

        records = MODULE.extract_crimson_initial_codes(html)
        self.assertEqual(2, len(records))
        self.assertEqual("ABC123", records[0]["code"])

    def test_split_code_variants(self) -> None:
        self.assertEqual(["A", "B", "C"], MODULE.split_code_variants("A/B, C"))
        self.assertEqual([], MODULE.split_code_variants(None))

    def test_merge_prefers_crimson_metadata(self) -> None:
        now_utc = datetime(2026, 5, 3, tzinfo=timezone.utc)
        ennead = [
            {
                "game": "genshin",
                "game_label": "Genshin Impact",
                "record_type": "code",
                "source_name": "Ennead",
                "source_url": "https://api.ennead.cc/mihoyo/genshin/codes",
                "code": "PSCA8NL4ZSPD",
                "code_variants": [],
                "redemption_url": "https://genshin.hoyoverse.com/en/gift?code=PSCA8NL4ZSPD",
                "status": "active",
                "is_redeemable_now": True,
                "has_expired": False,
                "expires_in": None,
                "start_at_utc": None,
                "end_at_utc": None,
                "start_at_output_tz": None,
                "end_at_output_tz": None,
                "added_at_utc": None,
                "added_at_output_tz": None,
                "rewards": [{"item": "Primogem", "qty": 60}],
                "raw_rewards": ["Primogem x60"],
            }
        ]
        crimson = [
            {
                "game": "genshin",
                "game_label": "Genshin Impact",
                "record_type": "code",
                "source_name": "Crimson Witch",
                "source_url": "https://www.crimsonwitch.com/codes/Genshin_Impact",
                "code": "PSCA8NL4ZSPD",
                "code_variants": ["G5HS7EMI47D0"],
                "redemption_url": "https://genshin.hoyoverse.com/en/gift?code=PSCA8NL4ZSPD",
                "status": "active",
                "is_redeemable_now": True,
                "has_expired": False,
                "expires_in": None,
                "start_at_utc": None,
                "end_at_utc": None,
                "start_at_output_tz": None,
                "end_at_output_tz": None,
                "added_at_utc": "2026-03-30T14:37:50+00:00",
                "added_at_output_tz": "2026-03-30T14:37:50+00:00",
                "rewards": [{"item": "Primogem", "qty": 60}],
                "raw_rewards": ["Primogem x60"],
            }
        ]

        merged = MODULE.merge_code_records(ennead, crimson, now_utc)
        self.assertEqual(1, len(merged))
        self.assertEqual(["G5HS7EMI47D0"], merged[0]["code_variants"])
        self.assertEqual("2026-03-30T14:37:50+00:00", merged[0]["added_at_utc"])
        self.assertEqual("Ennead + Crimson Witch", merged[0]["source_name"])

    def test_normalize_crimson_scheduled_code(self) -> None:
        now_utc = datetime(2026, 5, 3, tzinfo=timezone.utc)
        output_tz = ZoneInfo("UTC")
        row = MODULE.normalize_crimson_code(
            "genshin",
            {
                "code": "LIVESTREAM CODE 1",
                "code_variants": None,
                "added": "2026-03-25T12:47:18.864029+00:00",
                "start_date": "2026-05-08T12:00:00+00:00",
                "expires": "2026-05-11T03:59:59+00:00",
                "rewards": [{"item": "Primogem", "qty": 100}],
            },
            output_tz,
            now_utc,
        )
        self.assertEqual("scheduled", row["status"])
        self.assertFalse(row["is_redeemable_now"])
        self.assertEqual(
            "https://genshin.hoyoverse.com/en/gift?code=LIVESTREAM+CODE+1",
            row["redemption_url"],
        )


if __name__ == "__main__":
    unittest.main()
