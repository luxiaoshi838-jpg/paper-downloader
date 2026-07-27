from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import app
from robust_resolver import RobustOpenAccessResolver

DOIS = [
    "10.1016/j.margeo.2020.106154",
    "10.1016/j.chemosphere.2016.08.008",
    "10.5194/os-17-1367-2021",
    "10.1016/j.ecss.2007.09.030",
    "10.1029/2005JD006173",
    "10.1139/f80-143",
    "10.1007/s10533-017-0388-8",
    "10.1016/j.ejrh.2025.102585",
    "10.2307/1552532",
    "10.1007/s00382-019-04731-2",
    "10.1080/07055900.2015.1026872",
    "10.1021/es803138z",
    "10.1016/j.dendro.2018.05.004",
    "10.1021/acs.est.6b00365",
    "10.1002/wrcr.20117",
    "10.1016/S0022-1694(97)00073-5",
    "10.4296/cwrj2011-923",
    "10.1007/s00300-021-02989-z",
    "10.3808/jei.201100199",
    "10.1002/joc.1362",
    "10.4081/jlimnol.2010.s1.33",
    "10.1016/j.ejrh.2023.101391",
    "10.1016/j.marchem.2008.08.001",
    "10.1016/j.jhydrol.2023.129820",
    "10.1016/j.jhydrol.2020.125876",
    "10.1080/07011784.2014.985512",
    "10.3390/w16182648",
    "10.1080/07011784.2025.2509226",
]


class ChurchillV11Integration(unittest.TestCase):
    def test_real_downloads(self):
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            records = [app.ReferenceRecord(str(i), doi, doi) for i, doi in enumerate(DOIS, 1)]
            results = []
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {
                    pool.submit(RobustOpenAccessResolver("").download, record, output, cancel): record
                    for record in records
                }
                for future in as_completed(futures):
                    result = future.result()
                    results.append(result)
                    print(
                        "V11_RESULT "
                        + json.dumps(
                            {
                                "n": result.number,
                                "doi": result.doi,
                                "status": result.status,
                                "source": result.source,
                                "url": result.url,
                                "message": result.message,
                                "seconds": result.elapsed_seconds,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
            results.sort(key=lambda x: int(x.number))
            success = sum(item.status == "下载成功" for item in results)
            print(
                "V11_SUMMARY "
                + json.dumps(
                    {
                        "total": len(results),
                        "success": success,
                        "failed": len(results) - success,
                        "pdf_files": len(list(output.glob("*.pdf"))),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            self.assertEqual(len(results), 28)
            self.assertEqual(success, len(list(output.glob("*.pdf"))))


if __name__ == "__main__":
    unittest.main()
