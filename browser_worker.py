from __future__ import annotations

import atexit
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from browser_publisher_engine import BrowserEngineResult, EdgePublisherEngine


@dataclass
class _BrowserJob:
    doi: str
    target: Path
    cancel_event: threading.Event
    expected_title: str = ""
    done: threading.Event = field(default_factory=threading.Event)
    result: BrowserEngineResult | None = None


class DedicatedBrowserWorker:
    """Own Playwright and all of its browser objects on one dedicated thread.

    Playwright's synchronous API is greenlet based and every object must be used
    from the same OS thread in which ``sync_playwright().start()`` ran. The main
    downloader uses a ThreadPoolExecutor, so a plain lock is not sufficient: it
    serializes calls but still lets different worker threads touch the same
    Playwright context. This queue moves every browser operation onto one owner
    thread and returns the result to the calling download thread.
    """

    def __init__(
        self,
        engine_factory: Callable[[], EdgePublisherEngine] = EdgePublisherEngine,
    ) -> None:
        self._engine_factory = engine_factory
        self._jobs: queue.Queue[_BrowserJob | None] = queue.Queue()
        self._closed = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="publisher-browser-owner",
            daemon=True,
        )
        self._thread.start()
        atexit.register(self.close)

    def _run(self) -> None:
        engine: EdgePublisherEngine | None = None
        try:
            engine = self._engine_factory()
            while True:
                job = self._jobs.get()
                if job is None:
                    break
                try:
                    if job.cancel_event.is_set():
                        job.result = BrowserEngineResult(False, message="任务已取消")
                    else:
                        job.result = engine.download(
                            job.doi,
                            job.target,
                            job.cancel_event,
                            expected_title=job.expected_title,
                        )
                except Exception as exc:
                    job.result = BrowserEngineResult(
                        False,
                        message=(
                            "浏览器专用线程异常："
                            f"{type(exc).__name__}: {str(exc)[:260]}"
                        ),
                    )
                finally:
                    job.done.set()
        finally:
            if engine is not None:
                try:
                    engine.close()
                except Exception:
                    pass
            self._closed.set()

    def download(
        self,
        doi: str,
        target: Path,
        cancel_event: threading.Event,
        *,
        expected_title: str = "",
    ) -> BrowserEngineResult:
        if self._closed.is_set() or not self._thread.is_alive():
            return BrowserEngineResult(False, message="浏览器专用线程未运行")

        job = _BrowserJob(
            doi=doi,
            target=target,
            cancel_event=cancel_event,
            expected_title=expected_title,
        )
        self._jobs.put(job)

        while not job.done.wait(0.25):
            if self._closed.is_set() or not self._thread.is_alive():
                return BrowserEngineResult(False, message="浏览器专用线程意外退出")

        return job.result or BrowserEngineResult(False, message="浏览器没有返回结果")

    def close(self) -> None:
        if self._closed.is_set():
            return
        try:
            self._jobs.put_nowait(None)
        except Exception:
            return
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=5)


class SharedEdgePublisherWorker:
    _instance: DedicatedBrowserWorker | None = None
    _lock = threading.Lock()

    @classmethod
    def shared(cls) -> DedicatedBrowserWorker:
        with cls._lock:
            if cls._instance is None or cls._instance._closed.is_set():
                cls._instance = DedicatedBrowserWorker()
            return cls._instance
