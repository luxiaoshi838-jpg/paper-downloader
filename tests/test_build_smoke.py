import unittest

from app import build_pdf_filename, normalize_doi


class BuildSmokeTest(unittest.TestCase):
    def test_windows_filename(self):
        doi = normalize_doi("https://doi.org/10.1016/j.foreco.2021.119318")
        self.assertEqual(
            build_pdf_filename("0269", doi),
            "0269+10.1016_j.foreco.2021.119318.pdf",
        )


if __name__ == "__main__":
    unittest.main()
