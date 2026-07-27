from __future__ import annotations

import threading
import time
from pathlib import Path

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import app as legacy
from robust_resolver import RobustOpenAccessResolver, _candidate_priority
from resolver_v12 import EnhancedOpenAccessResolver

MAX_CANDIDATES_PER_DOI = 10
MAX_SECONDS_PER_DOI = 95


class ResponsiveOpenAccessResolver(EnhancedOpenAccessResolver):
    """v1.2.1 resolver with bounded latency for batch use.

    The earlier resolver could spend several minutes on one DOI because every
    metadata endpoint and publisher page had its own retry loop. This class
    retains the legal OA sources and PDF identity validation while bounding
    retries, candidates, and total work per DOI.
    """

    def __init__(self, email: str, timeout: int = 15, session=None) -> None:
        super().__init__(email=email, timeout=min(timeout, 18), session=session)
        retry = Retry(
            total=1,
            connect=1,
            read=1,
            status=1,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def resolve(self, doi: str):
        # Call the v1.1 base implementation directly. The v1.2 implementation
        # called Semantic Scholar a second time only to obtain external IDs,
        # which doubled the slowest/rate-limited request in every batch.
        context = RobustOpenAccessResolver.resolve(self, doi)
        self._expected_title = context.title

        has_verified_direct = any(
            item.oa_verified and item.direct_hint for item in context.candidates
        )
        # CORE is most useful when ordinary OA metadata confirms availability
        # but does not provide a direct PDF. With an API key, query it for all
        # DOI records because the authenticated quota is more reliable.
        if self.core_api_key or (context.is_oa is True and not has_verified_direct):
            self._resolve_core(context)

        if context.doi.startswith("10.1101/"):
            context.add_candidate(
                f"https://www.biorxiv.org/content/{context.doi}.full.pdf",
                "bioRxiv/medRxiv",
                landing_url=f"https://doi.org/{context.doi}",
                oa_verified=True,
                direct_hint=True,
            )
            context.is_oa = True

        self._resolve_elsevier_api(context)
        context.candidates.sort(key=_candidate_priority)
        context.candidates = self._select_candidates(context.candidates)
        return context

    @staticmethod
    def _select_candidates(candidates):
        selected = []
        seen_hosts: dict[str, int] = {}
        for candidate in candidates:
            from urllib.parse import urlsplit

            host = urlsplit(candidate.url).netloc.lower()
            host_count = seen_hosts.get(host, 0)
            # Keep up to two routes per host; a publisher landing page and its
            # explicit PDF route can both be useful, while dozens of equivalent
            # redirects only slow a batch down.
            if host_count >= 2:
                continue
            selected.append(candidate)
            seen_hosts[host] = host_count + 1
            if len(selected) >= MAX_CANDIDATES_PER_DOI:
                break
        return selected

    def download(
        self,
        record: legacy.ReferenceRecord,
        output_dir: Path,
        cancel_event: threading.Event,
    ) -> legacy.DownloadResult:
        started = time.monotonic()
        filename = legacy.build_pdf_filename(record.number, record.doi)
        target = output_dir / filename

        if cancel_event.is_set():
            return legacy.DownloadResult(
                record.number,
                record.doi,
                "已取消",
                filename=filename,
                message="任务已取消",
                raw_reference=record.raw_reference,
            )
        if target.exists() and target.stat().st_size >= 1024:
            return legacy.DownloadResult(
                record.number,
                record.doi,
                "已存在",
                filename=filename,
                message="目标文件已存在，未重复下载",
                raw_reference=record.raw_reference,
                elapsed_seconds=round(time.monotonic() - started, 2),
            )

        context = self.resolve(record.doi)
        elapsed = time.monotonic() - started

        # All legal OA metadata providers have already been checked. Do not
        # spend time probing publisher PDF routes when providers agree that no
        # open copy exists.
        verified_candidates = [item for item in context.candidates if item.oa_verified]
        if context.is_oa is False and not verified_candidates:
            return legacy.DownloadResult(
                record.number,
                record.doi,
                "下载失败",
                filename=filename,
                message=self._failure_message(context, []),
                raw_reference=record.raw_reference,
                elapsed_seconds=round(elapsed, 2),
            )

        errors: list[str] = []
        attempted_sources: list[str] = []
        last_url = ""
        for candidate in context.candidates:
            if cancel_event.is_set():
                return legacy.DownloadResult(
                    record.number,
                    record.doi,
                    "已取消",
                    filename=filename,
                    message="任务已取消",
                    raw_reference=record.raw_reference,
                    elapsed_seconds=round(time.monotonic() - started, 2),
                )
            if time.monotonic() - started >= MAX_SECONDS_PER_DOI:
                errors.append(f"总耗时达到 {MAX_SECONDS_PER_DOI} 秒，停止继续探测")
                break

            attempted_sources.append(candidate.source)
            success, final_url, message = self._download_candidate(
                candidate,
                target,
                doi=record.doi,
                visited=set(),
                depth=0,
            )
            last_url = final_url or last_url
            if success:
                return legacy.DownloadResult(
                    record.number,
                    record.doi,
                    "下载成功",
                    filename=filename,
                    source=candidate.source,
                    url=final_url,
                    raw_reference=record.raw_reference,
                    elapsed_seconds=round(time.monotonic() - started, 2),
                )
            if message:
                errors.append(f"{candidate.source}: {message}")

        source_summary = "、".join(dict.fromkeys(attempted_sources))[:240]
        message = self._failure_message(context, errors)
        if any("总耗时达到" in item for item in errors):
            message += f"；单篇处理已达到 {MAX_SECONDS_PER_DOI} 秒上限。"
        return legacy.DownloadResult(
            record.number,
            record.doi,
            "下载失败",
            filename=filename,
            source=f"检索：{source_summary}" if source_summary else "",
            url=last_url,
            message=message,
            raw_reference=record.raw_reference,
            elapsed_seconds=round(time.monotonic() - started, 2),
        )


OpenAccessResolverV13 = ResponsiveOpenAccessResolver
