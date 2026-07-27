import unittest

from app import build_pdf_filename, extract_dois, find_numbered_entries_without_doi, parse_references


class ParserTests(unittest.TestCase):
    def test_numbered_multiline_references(self):
        text = """1. Zhang, A. (2022). Example title.
Journal 10(2), 1-10.
https://doi.org/10.1000/ABC.123

2、Li, B. (2023). Another title. doi:10.5555/xyz-789.
"""
        records = parse_references(text)
        self.assertEqual(
            [(record.number, record.doi) for record in records],
            [("1", "10.1000/abc.123"), ("2", "10.5555/xyz-789")],
        )

    def test_bracket_number(self):
        records = parse_references("[12] Test. DOI: 10.1016/j.test.2024.01.001")
        self.assertEqual(records[0].number, "12")

    def test_without_number_uses_sequence(self):
        records = parse_references("A 10.1000/a B 10.1000/b")
        self.assertEqual([record.number for record in records], ["1", "2"])

    def test_duplicate_doi_removed(self):
        records = parse_references("1. 10.1000/a\n2. 10.1000/a")
        self.assertEqual(len(records), 1)

    def test_filename(self):
        self.assertEqual(build_pdf_filename("15", "10.1000/a:b"), "15+10.1000_a_b.pdf")

    def test_trailing_punctuation(self):
        self.assertEqual(extract_dois("doi 10.1000/example)."), ["10.1000/example"])

    def test_numbered_entry_without_doi_is_reported(self):
        missing = find_numbered_entries_without_doi("1. No DOI here.\n2. Has 10.1000/a")
        self.assertEqual(
            [(item.number, item.status) for item in missing],
            [("1", "无有效DOI")],
        )


if __name__ == "__main__":
    unittest.main()
