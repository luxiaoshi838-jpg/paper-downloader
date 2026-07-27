from __future__ import annotations

import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from browser_publisher_engine import BrowserEngineResult
from browser_worker import DedicatedBrowserWorker


class _FakeThreadBoundEngine:
    def __init__(self) -> None:
        self.owner_thread = threading.get_ident()
        self.call_threads: list[int] = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()
        self.closed_thread: int | None = None

    def download(self, doi, target, cancel_event, *, expected_title=""):
        current = threading.get_ident()
        if current != self.owner_thread:
            raise RuntimeError("thread-bound engine used from another thread")
        with self.lock:
            self.call_threads.append(current)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.02)
        with self.lock:
            self.active -= 1
        return BrowserEngineResult(True, final_url=f"https://example.test/{doi}.pdf")

    def close(self) -> None:
        self.closed_thread = threading.get_ident()


class DedicatedBrowserWorkerTests(unittest.TestCase):
    def test_parallel_callers_are_executed_on_one_owner_thread(self) -> None:
        created: list[_FakeThreadBoundEngine] = []

        def factory():
            engine = _FakeThreadBoundEngine()
            created.append(engine)
            return engine

        worker = DedicatedBrowserWorker(engine_factory=factory)
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(
                        worker.download,
                        f"10.1234/test-{index}",
                        base / f"{index}.pdf",
                        cancel,
                    )
                    for index in range(8)
                ]
                results = [future.result(timeout=5) for future in futures]

        worker.close()
        self.assertEqual(len(created), 1)
        engine = created[0]
        self.assertTrue(all(result.success for result in results))
        self.assertEqual(len(set(engine.call_threads)), 1)
        self.assertEqual(engine.call_threads[0], engine.owner_thread)
        self.assertEqual(engine.max_active, 1)
        self.assertEqual(engine.closed_thread, engine.owner_thread)


if __name__ == "__main__":
    unittest.main()
