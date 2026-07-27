import threading
import unittest
from pathlib import Path

import app
from tests.temp_utils import ProjectTempDir
from robust_resolver import (
    DEFAULT_CONTACT_EMAIL,
    ResolutionContext,
    ResolvedCandidate,
    RobustOpenAccessResolver,
    _expand_known_routes,
)


class FakeResponse:
    def __init__(self, *, status=200, url="https://example.org/x", body=b"", headers=None, payload=None):
        self.status_code = status
        self.url = url
        self._body = body
        self.headers = headers or {}
        self._payload = payload
        self.encoding = "utf-8"

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def iter_content(self, chunk_size=65536):
        for index in range(0, len(self._body), chunk_size):
            yield self._body[index:index + chunk_size]

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def close(self):
        pass


class FakeSession:
    def __init__(self, responses=None):
        self.headers = {}
        self.responses = list(responses or [])
        self.calls = []

    def mount(self, *args, **kwargs):
        pass

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("no response for " + url)
        response = self.responses.pop(0)
        if not response.url:
            response.url = url
        return response


class ResolverTests(unittest.TestCase):
    def test_empty_email_uses_default(self):
        resolver = RobustOpenAccessResolver("", session=FakeSession())
        self.assertEqual(resolver.email, DEFAULT_CONTACT_EMAIL)

    def test_extracts_meta_json_and_non_pdf_download_links(self):
        resolver = RobustOpenAccessResolver("", session=FakeSession())
        page = '''<meta name="citation_pdf_url" content="/paper/file">
        <script>{"pdfDownloadUrl":"https:\\/\\/cdn.example.org\\/x\\/paper.pdf"}</script>
        <a href="/article/download/33">Download</a>'''
        urls = resolver._extract_pdf_links(
            page,
            "https://journal.example.org/article/33",
            "10.1234/abc",
        )
        self.assertIn("https://journal.example.org/paper/file", urls)
        self.assertIn("https://cdn.example.org/x/paper.pdf", urls)
        self.assertIn("https://journal.example.org/article/download/33", urls)

    def test_elsevier_route_expansion(self):
        urls = _expand_known_routes(
            "https://linkinghub.elsevier.com/retrieve/pii/S0025322720300426",
            "10.1016/x",
        )
        self.assertTrue(any("/science/article/pii/S0025322720300426" in item for item in urls))
        self.assertTrue(any("/pdfft?" in item for item in urls))

    def test_direct_pdf_is_saved_and_verified(self):
        body = b"%PDF-1.7\n" + b"x" * 2048
        session = FakeSession([
            FakeResponse(
                url="https://repo.example/paper",
                body=body,
                headers={"Content-Type": "application/octet-stream"},
            )
        ])
        resolver = RobustOpenAccessResolver("", session=session)
        with ProjectTempDir() as tmp:
            target = Path(tmp) / "x.pdf"
            ok, _, message = resolver._download_candidate(
                ResolvedCandidate("https://repo.example/paper", "test", oa_verified=True),
                target,
                doi="10.1/x",
                visited=set(),
                depth=0,
            )
            self.assertTrue(ok)
            self.assertTrue(target.exists())
            self.assertEqual(message, "")

    def test_non_oa_failure_is_classified(self):
        resolver = RobustOpenAccessResolver("", session=FakeSession())
        resolver.resolve = lambda doi: ResolutionContext(doi=doi, is_oa=False)
        record = app.ReferenceRecord("1", "10.1000/closed", "ref")
        with ProjectTempDir() as tmp:
            result = resolver.download(record, Path(tmp), threading.Event())
        self.assertEqual(result.status, "下载失败")
        self.assertIn("非开放获取", result.message)

    def test_403_warms_landing_page_and_retries(self):
        pdf = b"%PDF-1.4\n" + b"z" * 1500
        session = FakeSession([
            FakeResponse(status=403, url="https://publisher.example/p.pdf"),
            FakeResponse(
                status=200,
                url="https://publisher.example/article",
                body=b"<html></html>",
                headers={"Content-Type": "text/html"},
            ),
            FakeResponse(
                status=200,
                url="https://publisher.example/p.pdf",
                body=pdf,
                headers={"Content-Type": "application/pdf"},
            ),
        ])
        resolver = RobustOpenAccessResolver("", session=session)
        with ProjectTempDir() as tmp:
            target = Path(tmp) / "p.pdf"
            ok, _, _ = resolver._download_candidate(
                ResolvedCandidate(
                    "https://publisher.example/p.pdf",
                    "test",
                    landing_url="https://publisher.example/article",
                    oa_verified=True,
                ),
                target,
                doi="10.1/x",
                visited=set(),
                depth=0,
            )
            self.assertTrue(ok)
            self.assertEqual(len(session.calls), 3)


if __name__ == "__main__":
    unittest.main()
