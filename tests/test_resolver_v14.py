from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

import app
from resolver_v14 import CampusNetworkResolver


class _CampusSuccessResolver(CampusNetworkResolver):
    def resolve(self, doi: str):  # pragma: no cover - should not be called
        raise AssertionError("校园网直连成功后不应继续执行开放获取检索")

    def _download_candidate(self, candidate, target, *, doi, visited, depth):
        target.write_bytes(b"%PDF-1.4\n" + b"0" * 2048 + b"\n%%EOF")
        return True, "https://publisher.example/article.pdf", ""


class CampusNetworkResolverTests(unittest.TestCase):
    def test_default_candidate_uses_current_network(self):
        candidate = CampusNetworkResolver._campus_candidate("10.1000/example")
        self.assertEqual(candidate.source, "校园网授权访问")
        self.assertTrue(candidate.oa_verified)
        self.assertEqual(candidate.url, "https://doi.org/10.1000/example")

    def test_campus_success_precedes_open_access_fallback(self):
        resolver = _CampusSuccessResolver("")
        record = app.ReferenceRecord("1", "10.1000/example", "test reference")
        with tempfile.TemporaryDirectory() as tmp:
            result = resolver.download(record, Path(tmp), threading.Event())
            self.assertEqual(result.status, "下载成功")
            self.assertEqual(result.source, "校园网授权访问")
            self.assertTrue((Path(tmp) / result.filename).exists())


if __name__ == "__main__":
    unittest.main()
