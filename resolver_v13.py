from __future__ import annotations

import threading
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import app as legacy
from robust_resolver import ResolutionContext, _candidate_priority
from resolver_v12 import EnhancedOpenAccessResolver

MAX_CANDIDATES_PER_DOI = 10
MAX_SECONDS_PER_DOI = 95
BIOMEDICAL_PREFIXES = (
    "10.1001/",
    "10.1056/",
    "10.1101/",
    "10.1186/",
    "10.1371/",
)


class ResponsiveOpenAccessResolver(EnhancedOpenAccessResolver):
    """保留开放获取多源检索，同时限制查询层级、重试和单篇耗时。"""

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

    @staticmethod
    def _has_verified_direct(context: ResolutionContext) -> bool:
        return any(item.oa_verified and item.direct_hint for item in context.candidates)

    def resolve(self, doi: str) -> ResolutionContext:
        """分层检索，避免每篇 DOI 无条件请求全部平台。"""
        context = ResolutionContext(doi=doi)

        # 第一层：开放获取判断覆盖率最高的两个来源。
        self._resolve_unpaywall(context)
        self._resolve_openalex(context)
        direct = self._has_verified_direct(context)

        # 第二层：没有直链时再检查 Semantic Scholar 的仓储副本。
        if not direct:
            self._resolve_semantic_scholar(context)
            direct = self._has_verified_direct(context)

        # 两个主来源和 Semantic Scholar 均判定非开放时停止扩展检索。
        # 这类文献继续请求 DOAJ、CORE、Europe PMC 与出版社页面既不会
        # 合法取得全文，又是旧版每篇耗时约 100 秒的主要原因。
        if not direct and context.is_oa is not False:
            if doi.lower().startswith(BIOMEDICAL_PREFIXES):
                self._resolve_europe_pmc(context)
                direct = self._has_verified_direct(context)

            if not direct:
                self._resolve_doaj(context)
                direct = self._has_verified_direct(context)

            if self.core_api_key or (context.is_oa is True and not direct):
                self._resolve_core(context)
                direct = self._has_verified_direct(context)

        # Crossref 主要用于开放许可、题名、出版社落地页和路径补全。
        # 已有经过验证的直链且 OpenAlex 给出了题名时无需再请求。
        if context.is_oa is not False and (not direct or not context.title):
            self._resolve_crossref(context)

        self._expected_title = context.title

        if doi.startswith("10.1101/"):
            context.add_candidate(
                f"https://www.biorxiv.org/content/{doi}.full.pdf",
                "bioRxiv/medRxiv",
                landing_url=f"https://doi.org/{doi}",
                oa_verified=True,
                direct_hint=True,
            )
            context.is_oa = True

        if context.is_oa is not False:
            context.add_candidate(
                f"https://doi.org/{quote(doi, safe='/():;._-')}",
                "DOI 页面",
                oa_verified=bool(context.is_oa),
            )
            self._add_publisher_routes(context)
            self._add_context_specific_routes(context)

        self._resolve_elsevier_api(context)
        context.candidates.sort(key=_candidate_priority)
        context.candidates = self._select_candidates(context.candidates)
        return context

    @staticmethod
    def _select_candidates(candidates):
        selected = []
        seen_hosts: dict[str, int] = {}
        for candidate in candidates:
            host = urlsplit(candidate.url).netloc.lower()
            host_count = seen_hosts.get(host, 0)
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
