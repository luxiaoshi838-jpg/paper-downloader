from __future__ import annotations

import json
import unittest

from browser_publisher_engine import _detect_blocked_page
from publisher_adapters import parse_json_script_urls, publisher_route_candidates


class PublisherAdapterTests(unittest.TestCase):
    def test_sciencedirect_pii_routes(self):
        urls = publisher_route_candidates(
            "https://linkinghub.elsevier.com/retrieve/pii/S0925857415303256",
            "10.1016/j.ecoleng.2015.12.015",
        )
        self.assertIn(
            "https://www.sciencedirect.com/science/article/pii/S0925857415303256/pdfft?download=true",
            urls,
        )

    def test_sciencedirect_rendered_json_route(self):
        payload = {
            "article": {
                "pdfDownload": {
                    "urlMetadata": {
                        "path": "science/article/pii",
                        "pdfExtension": "/pdfft",
                        "pii": "S1234567890",
                        "queryParams": {"md5": "abc", "pid": "1-s2.0-S1234567890-main.pdf"},
                    }
                }
            }
        }
        urls = parse_json_script_urls(
            "https://www.sciencedirect.com/science/article/pii/S1234567890",
            [json.dumps(payload)],
        )
        self.assertTrue(any("md5=abc" in item and "pid=" in item for item in urls))

    def test_wiley_pdfdirect_route(self):
        urls = publisher_route_candidates(
            "https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2020WR000001",
            "10.1029/2020WR000001",
        )
        self.assertIn(
            "https://agupubs.onlinelibrary.wiley.com/doi/pdfdirect/10.1029/2020WR000001",
            urls,
        )

    def test_taylor_and_francis_pdf_route(self):
        urls = publisher_route_candidates(
            "https://www.tandfonline.com/doi/full/10.1080/07011784.2014.985512",
            "10.1080/07011784.2014.985512",
        )
        self.assertIn(
            "https://www.tandfonline.com/doi/pdf/10.1080/07011784.2014.985512",
            urls,
        )

    def test_emerald_pdf_route(self):
        urls = publisher_route_candidates(
            "https://www.emerald.com/insight/content/doi/10.1108/TEST-01-2020-0001/full/html",
            "10.1108/TEST-01-2020-0001",
        )
        self.assertIn(
            "https://www.emerald.com/insight/content/doi/10.1108/TEST-01-2020-0001/full/pdf",
            urls,
        )

    def test_captcha_is_reported_not_bypassed(self):
        message = _detect_blocked_page("Verify you are human - CAPTCHA")
        self.assertIn("人工验证", message)


if __name__ == "__main__":
    unittest.main()
