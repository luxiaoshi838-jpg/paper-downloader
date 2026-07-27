import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter

from resolver_v12 import _is_plausible_core_pdf, validate_downloaded_pdf


class ResolverV12Tests(unittest.TestCase):
    def _make_pdf(self, path: Path, title: str) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_metadata({"/Title": title})
        with path.open("wb") as handle:
            writer.write(handle)

    def test_core_pdf_url_filter(self):
        self.assertTrue(_is_plausible_core_pdf("https://repo.example.edu/bitstream/123/paper.pdf"))
        self.assertTrue(_is_plausible_core_pdf("https://api.core.ac.uk/v3/outputs/123/download/pdf"))
        self.assertFalse(_is_plausible_core_pdf("https://core.ac.uk/data-providers/99"))
        self.assertFalse(_is_plausible_core_pdf("https://repo.example.edu/item/123"))

    def test_wrong_doaj_flyer_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flyer.pdf"
            self._make_pdf(path, "Journal information and subscription flyer")
            ok, reason = validate_downloaded_pdf(
                path,
                expected_doi="10.4081/jlimnol.2010.s1.33",
                expected_title="Chemical characteristics and acid sensitivity of boreal headwater lakes",
                source="DOAJ",
                url="https://example.org/LIMNO_flyer.pdf",
            )
            self.assertFalse(ok)
            self.assertIn("未匹配", reason)

    def test_doaj_pdf_with_matching_title_is_accepted(self):
        title = "Chemical characteristics and acid sensitivity of boreal headwater lakes in northwest Saskatchewan"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper.pdf"
            self._make_pdf(path, title)
            ok, reason = validate_downloaded_pdf(
                path,
                expected_doi="10.4081/jlimnol.2010.s1.33",
                expected_title=title,
                source="DOAJ",
                url="https://example.org/download/pdf",
            )
            self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main()
