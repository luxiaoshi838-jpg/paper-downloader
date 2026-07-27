from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import app
from resolver_v12 import EnhancedOpenAccessResolver

# Five representative records that failed in the first EXE:
# Elsevier OA landing pages, MDPI anti-bot page, and repository fallbacks.
DOIS = [
    "10.1016/j.margeo.2020.106154",
    "10.1016/j.ejrh.2025.102585",
    "10.1016/j.ejrh.2023.101391",
    "10.1016/j.jhydrol.2023.129820",
    "10.3390/w16182648",
]


class ChurchillV12Integration(unittest.TestCase):
    def test_representative_real_downloads(self):
        cancel = threading.Event()
        rows = []
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            records = [app.ReferenceRecord(str(i), doi, doi) for i, doi in enumerate(DOIS, 1)]
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = {
                    pool.submit(EnhancedOpenAccessResolver("", timeout=15).download, record, output, cancel): record
                    for record in records
                }
                for future in as_completed(futures):
                    record = futures[future]
                    try:
                        result = future.result()
                        row = {
                            "n": int(result.number),
                            "doi": result.doi,
                            "status": result.status,
                            "source": result.source,
                            "url": result.url,
                            "message": result.message,
                            "seconds": result.elapsed_seconds,
                        }
                    except Exception as exc:
                        row = {
                            "n": int(record.number),
                            "doi": record.doi,
                            "status": "程序异常",
                            "source": "",
                            "url": "",
                            "message": f"{type(exc).__name__}: {exc}",
                            "seconds": 0,
                        }
                    rows.append(row)
                    print("V12_RESULT " + json.dumps(row, ensure_ascii=True), flush=True)

            rows.sort(key=lambda item: item["n"])
            summary = {
                "total": len(rows),
                "success": sum(item["status"] == "下载成功" for item in rows),
                "failed": sum(item["status"] == "下载失败" for item in rows),
                "exceptions": sum(item["status"] == "程序异常" for item in rows),
                "pdf_files": len(list(output.glob("*.pdf"))),
            }
            Path("churchill_v12_report.json").write_text(
                json.dumps({"summary": summary, "results": rows}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print("V12_SUMMARY " + json.dumps(summary, ensure_ascii=True), flush=True)
            self.assertEqual(len(rows), len(DOIS))
            self.assertEqual(summary["exceptions"], 0)


if __name__ == "__main__":
    unittest.main()
