import tempfile
import threading
import unittest
from pathlib import Path

import app
from robust_resolver import ResolutionContext, ResolvedCandidate
from resolver_v13 import MAX_CANDIDATES_PER_DOI, ResponsiveOpenAccessResolver


class ResolverV13Tests(unittest.TestCase):
    def test_candidate_count_and_per_host_are_bounded(self):
        candidates = [
            ResolvedCandidate(
                f"https://host{i // 3}.example.org/path/{i}",
                "test",
                oa_verified=True,
                direct_hint=True,
            )
            for i in range(30)
        ]
        selected = ResponsiveOpenAccessResolver._select_candidates(candidates)
        self.assertLessEqual(len(selected), MAX_CANDIDATES_PER_DOI)
        host_counts = {}
        for item in selected:
            host = item.url.split("/", 3)[2]
            host_counts[host] = host_counts.get(host, 0) + 1
        self.assertTrue(all(count <= 2 for count in host_counts.values()))

    def test_non_oa_record_stops_before_publisher_download(self):
        resolver = ResponsiveOpenAccessResolver.__new__(ResponsiveOpenAccessResolver)
        resolver.resolve = lambda doi: ResolutionContext(doi=doi, is_oa=False)
        resolver._failure_message = lambda context, errors: "未找到开放获取全文"
        record = app.ReferenceRecord("1", "10.1000/closed", "ref")
        with tempfile.TemporaryDirectory() as tmp:
            result = resolver.download(record, Path(tmp), threading.Event())
        self.assertEqual(result.status, "下载失败")
        self.assertIn("开放获取", result.message)


if __name__ == "__main__":
    unittest.main()
