from __future__ import annotations

import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import app
from resolver_v15 import BrowserPublisherResolver


def run_batch(
    input_path: Path,
    output_dir: Path,
    email: str,
    workers: int,
    limit: int = 0,
) -> list[app.DownloadResult]:
    text = app.read_text_auto(input_path)
    records = app.parse_references(text)
    if limit > 0:
        records = records[:limit]
    results: list[app.DownloadResult] = list(app.find_numbered_entries_without_doi(text))
    cancel_event = threading.Event()

    output_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=max(1, min(8, workers)), thread_name_prefix="paper-download") as executor:
        future_map = {
            executor.submit(
                BrowserPublisherResolver(email=email).download,
                record,
                output_dir,
                cancel_event,
            ): record
            for record in records
        }
        completed = 0
        total = len(future_map)
        for future in as_completed(future_map):
            record = future_map[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(
                    app.DownloadResult(
                        record.number,
                        record.doi,
                        "下载失败",
                        filename=app.build_pdf_filename(record.number, record.doi),
                        message=f"Unhandled exception: {type(exc).__name__}: {exc}",
                        raw_reference=record.raw_reference,
                    )
                )
            completed += 1
            latest = results[-1]
            print(f"[{completed}/{total}] {latest.number} {latest.doi} {latest.status}", flush=True)

    results.sort(key=lambda item: app.natural_sort_key(item.number))
    app.write_logs(output_dir, results)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run paper-downloader without the Tk GUI.")
    parser.add_argument("input", type=Path, help="TXT reference file containing DOIs.")
    parser.add_argument("output", type=Path, help="Directory where PDFs and logs are written.")
    parser.add_argument("--email", default="", help="Contact email used for public metadata APIs.")
    parser.add_argument("--workers", type=int, default=2, help="Parallel DOI workers, 1-8.")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N DOI records.")
    args = parser.parse_args()

    results = run_batch(args.input, args.output, args.email, args.workers, args.limit)
    for result in results:
        print(f"{result.number}\t{result.doi}\t{result.status}\t{result.source}\t{result.message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
