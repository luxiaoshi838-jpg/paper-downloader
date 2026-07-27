from __future__ import annotations

import unittest


@unittest.skip("实时出版社下载检查改为手动诊断，避免标准构建受网络和反爬限制卡住")
class ChurchillV12Integration(unittest.TestCase):
    def test_representative_real_downloads(self):
        pass


if __name__ == "__main__":
    unittest.main()
