from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


MODULE_PATH = Path(__file__).with_name("generate_sec_filings.py")
SPEC = importlib.util.spec_from_file_location("generate_sec_filings", MODULE_PATH)
assert SPEC and SPEC.loader
sec = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sec
SPEC.loader.exec_module(sec)


class SecFilingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.company = sec.SecCompany(
            company="SoFi",
            display_name="SoFi Technologies",
            ticker="SOFI",
            cik="1818874",
            category="Fintech Blogs",
            forms=["10-K", "10-Q", "8-K", "424B"],
        )

    def test_form_matching_includes_amendments_and_424b_variants(self) -> None:
        allowed = {"10-K", "10-Q", "8-K", "424B"}
        self.assertTrue(sec.form_allowed("10-K/A", allowed))
        self.assertTrue(sec.form_allowed("424B4", allowed))
        self.assertFalse(sec.form_allowed("4", allowed))

    def test_sec_payload_builds_direct_edgar_links(self) -> None:
        payload = {
            "filings": {
                "recent": {
                    "form": ["10-Q", "4"],
                    "accessionNumber": ["0001818874-26-000001", "0001818874-26-000002"],
                    "filingDate": ["2026-05-07", "2026-05-08"],
                    "reportDate": ["2026-03-31", "2026-05-08"],
                    "acceptanceDateTime": ["2026-05-07T16:00:00.000Z", "2026-05-08T16:00:00.000Z"],
                    "primaryDocument": ["sofi-20260331.htm", "ownership.xml"],
                }
            }
        }
        filings = sec.parse_company_filings(self.company, payload)
        self.assertEqual(1, len(filings))
        self.assertEqual("SEC EDGAR", filings[0].dataSource)
        self.assertIn("000181887426000001", filings[0].filingUrl)

    def test_nasdaq_reference_is_stable(self) -> None:
        url = "https://app.quotemedia.com/data/downloadFiling?ref=320023651&type=HTML&symbol=SOFI"
        self.assertEqual("320023651", sec.nasdaq_reference(url))

    def test_read_through_wrapper_extracts_json(self) -> None:
        body = 'Title:\n\nURL Source: https://data.sec.gov/example.json\n\nMarkdown Content:\n{"filings":{"recent":{}}}'
        payload = sec.decode_wrapped_json(body)
        self.assertEqual({"recent": {}}, payload["filings"])

    def test_efts_hit_builds_direct_sec_filing(self) -> None:
        hit = {
            "_id": "0001818874-26-000043:sofi-20260617.htm",
            "_source": {
                "ciks": ["0001818874"],
                "form": "8-K",
                "adsh": "0001818874-26-000043",
                "file_date": "2026-06-18",
                "period_ending": "2026-06-17",
            },
        }
        filing = sec.parse_efts_hit(self.company, hit)
        self.assertIsNotNone(filing)
        assert filing
        self.assertEqual("SEC EDGAR via read-through", filing.dataSource)
        self.assertTrue(filing.documentUrl.endswith("/sofi-20260617.htm"))


if __name__ == "__main__":
    unittest.main()
