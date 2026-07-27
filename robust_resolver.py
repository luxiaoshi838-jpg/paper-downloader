from __future__ import annotations

import html
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import app as legacy

DEFAULT_CONTACT_EMAIL = "luxiaoshi838-jpg@users.noreply.github.com"
MAX_HTML_BYTES = 4_000_000
PDF_MAGIC = b"%PDF-"

API_INTERVALS = {
    "api.unpaywall.org": 0.25,
    "api.openalex.org": 0.25,
    "api.crossref.org": 0.60,
    "api.semanticscholar.org": 1.10,
    "www.ebi.ac.uk": 0.35,
    "doaj.org": 0.50,
}

KNOWN_OA_DOI_PREFIXES = (
    "10.3390/",
    "10.5194/",
    "10.4081/",
    "10.3808/",
)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

PDF_META_PATTERNS = (
    re.compile(
        r'<meta[^>]+(?:name|property)=["\'](?:citation_pdf_url|wkhealth_pdf_url|pdf_url|eprints.document_url)["\'][^>]+content=["\']([^"\']+)',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\'](?:citation_pdf_url|wkhealth_pdf_url|pdf_url|eprints.document_url)["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<link[^>]+type=["\']application/pdf["\'][^>]+href=["\']([^"\']+)',
        re.IGNORECASE,
    ),
    re.compile(
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]+type=["\']application/pdf["\']',
        re.IGNORECASE,
    ),
    re.compile(r'<(?:iframe|embed)[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE),
)

JSON_PDF_PATTERNS = (
    re.compile(
        r'["\'](?:pdfUrl|pdf_url|pdfURL|pdfDownloadUrl|downloadPdfUrl|url_for_pdf|fullTextPdf)["\']\s*:\s*["\']([^"\']+)',
        re.IGNORECASE,
    ),
    re.compile(r'["\']downloadUrl["\']\s*:\s*["\']([^"\']+)', re.IGNORECASE),
)

ANCHOR_PATTERN = re.compile(r'<a\b[^>]+href=["\']([^"\']+)["\']', re.IGNORECASE)

PAYWALL_MARKERS = (
    "purchase this article",
    "institutional access",
    "sign in to access",
    "subscribe to read",
    "rent this article",
    "access through your institution",
    "buy article",
)


class HostThrottle:
    _lock = threading.Lock()
    _next_allowed: dict[str, float] = {}

    @classmethod
    def wait(cls, url: str) -> None:
        host = urllib.parse.urlparse(url).netloc.lower()
        interval = API_INTERVALS.get(host, 0.0)
        if interval <= 0:
            return
        now = time.monotonic()
        with cls._lock:
            target = max(now, cls._next_allowed.get(host, now))
            cls._next_allowed[host] = target + interval
        delay = target - now
        if delay > 0:
            time.sleep(delay)


@dataclass(frozen=True)
class ResolvedCandidate:
    url: str
    source: str
    landing_url: str = ""
    oa_verified: bool = False
    direct_hint: bool = False


@dataclass
class ResolutionContext:
    doi: str
    is_oa: Optional[bool] = None
    candidates: list[ResolvedCandidate] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    metadata_sources: set[str] = field(default_factory=set)
    publisher: str = ""
    title: str = ""
    journal: str = ""

    def add_candidate(
        self,
        url: Optional[str],
        source: str,
        *,
        landing_url: str = "",
        oa_verified: bool = False,
        direct_hint: bool = False,
    ) -> None:
        if not url:
            return
        value = html.unescape(str(url)).strip().replace("\\/", "/")
        if value.startswith("//"):
            value = "https:" + value
        if not value.startswith(("http://", "https://")):
            base = landing_url or f"https://doi.org/{self.doi}"
            value = urllib.parse.urljoin(base, value)
        if not value.startswith(("http://", "https://")):
            return
        normalized = _canonical_url(value)
        if any(_canonical_url(item.url) == normalized for item in self.candidates):
            return
        self.candidates.append(
            ResolvedCandidate(
                url=value,
                source=source,
                landing_url=landing_url,
                oa_verified=oa_verified,
                direct_hint=direct_hint or _looks_direct_pdf(value),
            )
        )


class RobustOpenAccessResolver:
    """Resolve and download only publicly available/open-access PDFs."""

    def __init__(
        self,
        email: str,
        timeout: int = 30,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.email = email.strip() or DEFAULT_CONTACT_EMAIL
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(BROWSER_HEADERS)
        self.session.headers["From"] = self.email
        retry = Retry(
            total=3,
            connect=2,
            read=2,
            status=3,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _request(
        self,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        stream: bool = False,
    ) -> requests.Response:
        HostThrottle.wait(url)
        merged_headers = dict(BROWSER_HEADERS)
        if headers:
            merged_headers.update(headers)
        return self.session.get(
            url,
            params=params,
            headers=merged_headers,
            timeout=self.timeout,
            allow_redirects=True,
            stream=stream,
        )

    def _json_get(
        self,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
    ) -> tuple[Optional[dict[str, Any]], str]:
        try:
            response = self._request(
                url,
                params=params,
                headers={"Accept": "application/json"},
            )
            if response.status_code == 429:
                return None, "HTTP 429（接口限流）"
            if response.status_code == 404:
                return None, "未收录"
            if not response.ok:
                return None, f"HTTP {response.status_code}"
            payload = response.json()
            if not isinstance(payload, dict):
                return None, "返回内容不是 JSON 对象"
            return payload, ""
        except (requests.RequestException, ValueError, TypeError) as exc:
            return None, f"{type(exc).__name__}: {str(exc)[:140]}"

    def resolve(self, doi: str) -> ResolutionContext:
        context = ResolutionContext(doi=doi)
        self._resolve_unpaywall(context)
        self._resolve_openalex(context)
        self._resolve_semantic_scholar(context)
        self._resolve_europe_pmc(context)
        self._resolve_doaj(context)
        self._resolve_crossref(context)
        context.add_candidate(
            f"https://doi.org/{urllib.parse.quote(doi, safe='/():;._-')}",
            "DOI 页面",
            oa_verified=bool(context.is_oa),
        )
        self._add_publisher_routes(context)
        self._add_context_specific_routes(context)
        context.candidates.sort(key=_candidate_priority)
        return context

    def _resolve_unpaywall(self, context: ResolutionContext) -> None:
        url = f"https://api.unpaywall.org/v2/{urllib.parse.quote(context.doi, safe='')}"
        payload, error = self._json_get(url, params={"email": self.email})
        if payload is None:
            context.diagnostics.append(f"Unpaywall：{error}")
            return
        context.metadata_sources.add("Unpaywall")
        is_oa = payload.get("is_oa")
        if isinstance(is_oa, bool):
            context.is_oa = is_oa if context.is_oa is None else context.is_oa or is_oa
        locations: list[dict[str, Any]] = []
        for key in ("best_oa_location", "first_oa_location"):
            item = payload.get(key)
            if isinstance(item, dict):
                locations.append(item)
        locations.extend(item for item in payload.get("oa_locations") or [] if isinstance(item, dict))
        for location in locations:
            landing = str(
                location.get("url_for_landing_page")
                or location.get("url")
                or ""
            )
            context.add_candidate(
                location.get("url_for_pdf"),
                "Unpaywall",
                landing_url=landing,
                oa_verified=True,
                direct_hint=True,
            )
            context.add_candidate(
                landing,
                "Unpaywall 落地页",
                landing_url=landing,
                oa_verified=True,
            )

    def _resolve_openalex(self, context: ResolutionContext) -> None:
        work_id = urllib.parse.quote(f"https://doi.org/{context.doi}", safe="")
        payload, error = self._json_get(
            f"https://api.openalex.org/works/{work_id}",
            params={"mailto": self.email},
        )
        if payload is None:
            context.diagnostics.append(f"OpenAlex：{error}")
            return
        context.metadata_sources.add("OpenAlex")
        open_access = payload.get("open_access") or {}
        is_oa = open_access.get("is_oa")
        if isinstance(is_oa, bool):
            context.is_oa = is_oa if context.is_oa is None else context.is_oa or is_oa
        context.title = str(payload.get("title") or context.title)
        locations: list[dict[str, Any]] = []
        for key in ("best_oa_location", "primary_location"):
            item = payload.get(key)
            if isinstance(item, dict):
                locations.append(item)
        locations.extend(item for item in payload.get("locations") or [] if isinstance(item, dict))
        for location in locations:
            landing = str(location.get("landing_page_url") or "")
            verified = bool(location.get("is_oa") or is_oa)
            context.add_candidate(
                location.get("pdf_url"),
                "OpenAlex",
                landing_url=landing,
                oa_verified=verified,
                direct_hint=True,
            )
            if verified:
                context.add_candidate(
                    landing,
                    "OpenAlex 落地页",
                    landing_url=landing,
                    oa_verified=True,
                )

    def _resolve_semantic_scholar(self, context: ResolutionContext) -> None:
        paper_id = urllib.parse.quote(f"DOI:{context.doi}", safe="")
        payload, error = self._json_get(
            f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}",
            params={"fields": "title,isOpenAccess,openAccessPdf,url"},
        )
        if payload is None:
            context.diagnostics.append(f"Semantic Scholar：{error}")
            return
        context.metadata_sources.add("Semantic Scholar")
        is_oa = payload.get("isOpenAccess")
        if isinstance(is_oa, bool):
            context.is_oa = is_oa if context.is_oa is None else context.is_oa or is_oa
        context.title = str(payload.get("title") or context.title)
        pdf = payload.get("openAccessPdf") or {}
        if isinstance(pdf, dict):
            context.add_candidate(
                pdf.get("url"),
                "Semantic Scholar",
                landing_url=str(payload.get("url") or ""),
                oa_verified=True,
                direct_hint=True,
            )

    def _resolve_europe_pmc(self, context: ResolutionContext) -> None:
        payload, error = self._json_get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={
                "query": f'DOI:"{context.doi}"',
                "format": "json",
                "pageSize": 5,
                "resultType": "core",
            },
        )
        if payload is None:
            context.diagnostics.append(f"Europe PMC：{error}")
            return
        result_list = ((payload.get("resultList") or {}).get("result") or [])
        if not isinstance(result_list, list) or not result_list:
            context.diagnostics.append("Europe PMC：未收录")
            return
        context.metadata_sources.add("Europe PMC")
        for item in result_list:
            if not isinstance(item, dict):
                continue
            pmcid = str(item.get("pmcid") or "").strip()
            if pmcid:
                context.is_oa = True
                landing = f"https://europepmc.org/articles/{pmcid}"
                context.add_candidate(
                    f"https://europepmc.org/articles/{pmcid}?pdf=render",
                    "Europe PMC",
                    landing_url=landing,
                    oa_verified=True,
                    direct_hint=True,
                )
                context.add_candidate(
                    f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf/",
                    "PubMed Central",
                    landing_url=f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/",
                    oa_verified=True,
                    direct_hint=True,
                )
            full_text_urls = ((item.get("fullTextUrlList") or {}).get("fullTextUrl") or [])
            for link in full_text_urls:
                if not isinstance(link, dict):
                    continue
                value = link.get("url")
                style = str(link.get("documentStyle") or "").lower()
                availability = str(link.get("availability") or "").lower()
                if value and ("pdf" in style or "free" in availability or "open" in availability):
                    context.add_candidate(
                        value,
                        "Europe PMC",
                        oa_verified=True,
                        direct_hint="pdf" in style,
                    )

    def _resolve_doaj(self, context: ResolutionContext) -> None:
        query = urllib.parse.quote(f"doi:{context.doi}", safe="")
        endpoints = (
            f"https://doaj.org/api/v4/search/articles/{query}",
            f"https://doaj.org/api/search/articles/{query}",
        )
        payload: Optional[dict[str, Any]] = None
        last_error = "未收录"
        for endpoint in endpoints:
            payload, last_error = self._json_get(endpoint)
            if payload is not None:
                break
        if payload is None:
            context.diagnostics.append(f"DOAJ：{last_error}")
            return
        results = payload.get("results") or []
        if not isinstance(results, list) or not results:
            context.diagnostics.append("DOAJ：未收录")
            return
        context.metadata_sources.add("DOAJ")
        for result in results:
            if not isinstance(result, dict):
                continue
            bibjson = result.get("bibjson") or {}
            identifiers = bibjson.get("identifier") or []
            has_matching_doi = any(
                str(item.get("type") or "").lower() == "doi"
                and str(item.get("id") or "").lower() == context.doi.lower()
                for item in identifiers
                if isinstance(item, dict)
            )
            if not has_matching_doi:
                continue
            context.is_oa = True
            for link in bibjson.get("link") or []:
                if not isinstance(link, dict):
                    continue
                value = link.get("url")
                link_type = str(link.get("type") or "").lower()
                context.add_candidate(
                    value,
                    "DOAJ",
                    landing_url=str(value or ""),
                    oa_verified=True,
                    direct_hint="fulltext" in link_type or "pdf" in link_type,
                )

    def _resolve_crossref(self, context: ResolutionContext) -> None:
        payload, error = self._json_get(
            f"https://api.crossref.org/works/{urllib.parse.quote(context.doi, safe='')}",
            params={"mailto": self.email},
        )
        if payload is None:
            context.diagnostics.append(f"Crossref：{error}")
            return
        message = payload.get("message") or {}
        if not isinstance(message, dict):
            return
        context.metadata_sources.add("Crossref")
        context.publisher = str(message.get("publisher") or "")
        containers = message.get("container-title") or []
        if containers:
            context.journal = str(containers[0])
        titles = message.get("title") or []
        if titles and not context.title:
            context.title = str(titles[0])
        license_urls = [
            str(item.get("URL") or "").lower()
            for item in message.get("license") or []
            if isinstance(item, dict)
        ]
        has_open_license = any(
            "creativecommons.org" in item
            or "openaccess" in item
            or "rightsstatements.org" in item
            for item in license_urls
        )
        if has_open_license:
            context.is_oa = True
        resource_url = ((message.get("resource") or {}).get("primary") or {}).get("URL")
        context.add_candidate(
            resource_url,
            "Crossref 落地页",
            landing_url=str(resource_url or ""),
            oa_verified=bool(context.is_oa),
        )
        if context.is_oa:
            for link in message.get("link") or []:
                if not isinstance(link, dict):
                    continue
                url = link.get("URL")
                content_type = str(link.get("content-type") or "").lower()
                if url and ("pdf" in content_type or _looks_direct_pdf(str(url))):
                    context.add_candidate(
                        url,
                        "Crossref（已确认开放）",
                        landing_url=str(resource_url or ""),
                        oa_verified=True,
                        direct_hint=True,
                    )

    def _add_publisher_routes(self, context: ResolutionContext) -> None:
        original = list(context.candidates)
        for candidate in original:
            for expanded in _expand_known_routes(candidate.url, context.doi):
                if not context.is_oa and not context.doi.startswith(KNOWN_OA_DOI_PREFIXES):
                    if _looks_direct_pdf(expanded):
                        continue
                context.add_candidate(
                    expanded,
                    candidate.source + " 路径补全",
                    landing_url=candidate.landing_url or candidate.url,
                    oa_verified=candidate.oa_verified or bool(context.is_oa),
                    direct_hint=_looks_direct_pdf(expanded),
                )

    def _add_context_specific_routes(self, context: ResolutionContext) -> None:
        if not context.doi.startswith("10.3390/") or not context.journal:
            return
        slug = re.sub(r"[^a-z0-9]+", "-", context.journal.lower()).strip("-")
        if not slug:
            return
        for candidate in list(context.candidates):
            parsed = urllib.parse.urlsplit(candidate.url)
            if "mdpi.com" not in parsed.netloc.lower():
                continue
            match = re.search(r"/\d{4}-\d{3,4}/(\d+)/(?:\d+)/(\d+)/?$", parsed.path)
            if not match:
                continue
            volume, article = match.groups()
            stem = f"{slug}-{int(volume):02d}-{int(article):05d}"
            landing = candidate.url
            for suffix in (f"{stem}.pdf", f"{stem}-v2.pdf", f"{stem}-v3.pdf"):
                context.add_candidate(
                    f"https://mdpi-res.com/d_attachment/{slug}/{stem}/article_deploy/{suffix}",
                    "MDPI 公开资源",
                    landing_url=landing,
                    oa_verified=True,
                    direct_hint=True,
                )

    def download(
        self,
        record: legacy.ReferenceRecord,
        output_dir: Path,
        cancel_event: threading.Event,
    ) -> legacy.DownloadResult:
        started = time.perf_counter()
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
                elapsed_seconds=round(time.perf_counter() - started, 2),
            )

        context = self.resolve(record.doi)
        errors: list[str] = []
        last_url = ""
        attempted_sources: list[str] = []
        for candidate in context.candidates:
            if cancel_event.is_set():
                return legacy.DownloadResult(
                    record.number,
                    record.doi,
                    "已取消",
                    filename=filename,
                    message="任务已取消",
                    raw_reference=record.raw_reference,
                    elapsed_seconds=round(time.perf_counter() - started, 2),
                )
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
                    elapsed_seconds=round(time.perf_counter() - started, 2),
                )
            if message:
                errors.append(f"{candidate.source}: {message}")

        message = self._failure_message(context, errors)
        source_summary = "、".join(dict.fromkeys(attempted_sources))[:240]
        return legacy.DownloadResult(
            record.number,
            record.doi,
            "下载失败",
            filename=filename,
            source=f"检索：{source_summary}" if source_summary else "",
            url=last_url,
            message=message,
            raw_reference=record.raw_reference,
            elapsed_seconds=round(time.perf_counter() - started, 2),
        )

    def _download_candidate(
        self,
        candidate: ResolvedCandidate,
        target: Path,
        *,
        doi: str,
        visited: set[str],
        depth: int,
    ) -> tuple[bool, str, str]:
        if depth > 4:
            return False, candidate.url, "网页内 PDF 跳转层级过深"
        canonical = _canonical_url(candidate.url)
        if canonical in visited:
            return False, candidate.url, "检测到重复跳转链接"
        visited.add(canonical)

        headers: dict[str, str] = {}
        if candidate.landing_url and candidate.landing_url != candidate.url:
            headers["Referer"] = candidate.landing_url

        response: Optional[requests.Response] = None
        try:
            response = self._request(candidate.url, headers=headers, stream=True)
            warmup_url = candidate.landing_url or f"https://doi.org/{doi}"
            if response.status_code in {401, 403} and warmup_url != candidate.url:
                try:
                    warm = self._request(warmup_url, headers={"Referer": f"https://doi.org/{doi}"})
                    warm.close()
                except requests.RequestException:
                    pass
                time.sleep(0.35)
                response.close()
                response = self._request(candidate.url, headers={"Referer": warmup_url}, stream=True)

            if response.status_code == 429:
                return False, response.url, "HTTP 429，服务器限流，重试后仍被拒绝"
            if response.status_code in {401, 403}:
                return False, response.url, f"HTTP {response.status_code}，服务器拒绝程序化访问"
            if response.status_code == 404:
                return False, response.url, "HTTP 404，链接已失效"
            if not response.ok:
                return False, response.url, f"HTTP {response.status_code}"

            iterator = response.iter_content(chunk_size=64 * 1024)
            first_chunk = next(iterator, b"")
            content_type = response.headers.get("Content-Type", "").lower()
            if _is_pdf_bytes(first_chunk) or "application/pdf" in content_type:
                if not _is_pdf_bytes(first_chunk):
                    probe = first_chunk.lstrip().lower()
                    if probe.startswith((b"<html", b"<!doctype", b"<script")):
                        return False, response.url, "服务器将 HTML 错误页标记成 PDF"
                temp = target.with_suffix(target.suffix + ".part")
                try:
                    with temp.open("wb") as handle:
                        handle.write(first_chunk)
                        for chunk in iterator:
                            if chunk:
                                handle.write(chunk)
                    if temp.stat().st_size < 1024:
                        temp.unlink(missing_ok=True)
                        return False, response.url, "PDF 文件过小，已拒绝保存"
                    with temp.open("rb") as handle:
                        if not _is_pdf_bytes(handle.read(2048)):
                            temp.unlink(missing_ok=True)
                            return False, response.url, "下载内容不是有效 PDF"
                    temp.replace(target)
                    return True, response.url, ""
                except OSError as exc:
                    temp.unlink(missing_ok=True)
                    return False, response.url, f"文件写入错误：{exc}"

            body = bytearray(first_chunk)
            for chunk in iterator:
                if not chunk:
                    continue
                remaining = MAX_HTML_BYTES - len(body)
                if remaining <= 0:
                    break
                body.extend(chunk[:remaining])
            text = _decode_html(bytes(body), response.encoding)
            if not text:
                return False, response.url, "链接未返回 PDF 或可解析网页"

            discovered = self._extract_pdf_links(text, response.url, doi)
            for url in discovered:
                if (
                    _looks_direct_pdf(url)
                    and not candidate.oa_verified
                    and not doi.startswith(KNOWN_OA_DOI_PREFIXES)
                ):
                    continue
                nested = ResolvedCandidate(
                    url=url,
                    source=candidate.source + " 页面",
                    landing_url=response.url,
                    oa_verified=candidate.oa_verified,
                    direct_hint=_looks_direct_pdf(url),
                )
                success, final_url, message = self._download_candidate(
                    nested,
                    target,
                    doi=doi,
                    visited=visited,
                    depth=depth + 1,
                )
                if success:
                    return success, final_url, message

            lowered = text.lower()
            if any(marker in lowered for marker in PAYWALL_MARKERS):
                return False, response.url, "页面显示需要订阅或机构权限"
            if "access denied" in lowered or "forbidden" in lowered:
                return False, response.url, "页面拒绝自动访问"
            return False, response.url, "网页中未解析出可验证的 PDF 链接"
        except requests.Timeout:
            return False, candidate.url, "网络超时"
        except requests.RequestException as exc:
            return False, candidate.url, f"网络错误：{type(exc).__name__}: {str(exc)[:180]}"
        finally:
            if response is not None:
                response.close()

    def _extract_pdf_links(self, text: str, base_url: str, doi: str) -> list[str]:
        values: list[str] = []
        for pattern in PDF_META_PATTERNS + JSON_PDF_PATTERNS:
            for match in pattern.finditer(text):
                values.append(match.group(1))
        for match in ANCHOR_PATTERN.finditer(text):
            href = match.group(1)
            if _looks_like_download_link(href):
                values.append(href)

        expanded_values: list[str] = []
        for value in values:
            decoded = html.unescape(value).replace("\\/", "/").replace("\\u002F", "/")
            try:
                decoded = bytes(decoded, "utf-8").decode("unicode_escape") if "\\u" in decoded else decoded
            except UnicodeDecodeError:
                pass
            absolute = urllib.parse.urljoin(base_url, decoded)
            expanded_values.append(absolute)
            expanded_values.extend(_expand_known_routes(absolute, doi))
        expanded_values.extend(_expand_known_routes(base_url, doi))

        output: list[str] = []
        seen: set[str] = set()
        for value in expanded_values:
            if not value.startswith(("http://", "https://")):
                continue
            key = _canonical_url(value)
            if key in seen:
                continue
            seen.add(key)
            output.append(value)
        output.sort(key=lambda item: (0 if _looks_direct_pdf(item) else 1, len(item)))
        return output[:40]

    def _failure_message(self, context: ResolutionContext, errors: Iterable[str]) -> str:
        error_list = list(errors)
        joined = "；".join(error_list)
        diagnostic = "；".join(context.diagnostics)
        if context.is_oa is False:
            return "未找到开放获取全文：Unpaywall/OpenAlex 将该 DOI 标记为非开放获取。"
        if "HTTP 403" in joined or "HTTP 401" in joined:
            if context.is_oa:
                return (
                    "已找到开放获取线索，但出版社服务器拒绝程序化访问（HTTP 401/403）。"
                    "程序已尝试浏览器请求头、落地页 Cookie 和 Referer，仍未取得 PDF；"
                    "请在日志中的最终网址用浏览器手动打开。"
                )
            return "出版社服务器拒绝程序化访问，且没有元数据源确认该文献为开放获取。"
        if "HTTP 429" in joined or "接口限流" in diagnostic:
            return "文献接口持续限流（HTTP 429）；程序已自动退避重试，建议稍后只重试失败项。"
        if context.is_oa:
            return (
                "元数据源确认文献开放获取，但只返回落地页，未能解析或验证 PDF。"
                + (f" 诊断：{diagnostic[:300]}" if diagnostic else "")
            )
        if not context.candidates:
            return "Unpaywall、OpenAlex、Semantic Scholar、Europe PMC、DOAJ 和 Crossref 均未返回可用全文链接。"
        return "未找到公开可访问的 PDF。" + (f" 诊断：{diagnostic[:300]}" if diagnostic else "")


OpenAccessResolverV2 = RobustOpenAccessResolver


def _candidate_priority(candidate: ResolvedCandidate) -> tuple[int, int, int]:
    source = candidate.source.lower()
    repository = any(
        marker in candidate.url.lower()
        for marker in (
            "pmc.ncbi.nlm.nih.gov",
            "europepmc.org",
            "repository",
            "handle.net",
            "bitstream",
            "eprints",
            "archive",
            "mspace.lib",
        )
    )
    if candidate.oa_verified and repository:
        group = 0
    elif candidate.oa_verified and candidate.direct_hint:
        group = 1
    elif candidate.direct_hint:
        group = 2
    elif candidate.oa_verified:
        group = 3
    elif "doi 页面" in source:
        group = 6
    else:
        group = 5
    return group, len(candidate.url), len(candidate.source)


def _canonical_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    host = parts.netloc.lower()
    path = re.sub(r"/{2,}", "/", parts.path)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = sorted((key, value) for key, value in query if not key.lower().startswith("utm_"))
    return urllib.parse.urlunsplit((scheme, host, path.rstrip("/"), urllib.parse.urlencode(query), ""))


def _is_pdf_bytes(data: bytes) -> bool:
    return PDF_MAGIC in data[:2048]


def _looks_direct_pdf(url: str) -> bool:
    lowered = urllib.parse.unquote(url).lower()
    return any(
        marker in lowered
        for marker in (
            ".pdf",
            "/pdf/",
            "/pdf?",
            "/pdfdirect/",
            "/pdfft",
            "?pdf=render",
            "/article/download/",
            "/download/article/",
            "/bitstreams/",
            "/bitstream/",
            "/content/pdf/",
        )
    )


def _looks_like_download_link(value: str) -> bool:
    lowered = html.unescape(value).lower()
    if lowered.startswith(("javascript:", "mailto:", "#")):
        return False
    return any(
        marker in lowered
        for marker in (
            ".pdf",
            "/pdf",
            "pdf?",
            "download",
            "fulltext",
            "bitstream",
            "document",
            "pdfft",
        )
    )


def _expand_known_routes(url: str, doi: str) -> list[str]:
    output: list[str] = []
    parsed = urllib.parse.urlsplit(url)
    host = parsed.netloc.lower()
    path = parsed.path

    pii_match = re.search(r"/retrieve/pii/([^/?#]+)", path, re.IGNORECASE)
    if pii_match:
        pii = pii_match.group(1)
        page = f"https://www.sciencedirect.com/science/article/pii/{pii}"
        output.extend((page, page + "/pdfft?isDTMRedir=true&download=true"))
    pii_page = re.search(r"/science/article/pii/([^/?#]+)", path, re.IGNORECASE)
    if pii_page and "/pdfft" not in path.lower():
        page = f"https://www.sciencedirect.com/science/article/pii/{pii_page.group(1)}"
        output.append(page + "/pdfft?isDTMRedir=true&download=true")

    if "link.springer.com" in host and "/article/" in path:
        output.append(f"https://link.springer.com/content/pdf/{urllib.parse.quote(doi, safe='/():;._-')}.pdf")

    if "tandfonline.com" in host:
        for marker in ("/doi/full/", "/doi/abs/", "/doi/epdf/"):
            if marker in path:
                output.append(urllib.parse.urlunsplit((parsed.scheme or "https", parsed.netloc, path.replace(marker, "/doi/pdf/"), parsed.query, "")))

    if "onlinelibrary.wiley.com" in host:
        for marker in ("/doi/full/", "/doi/abs/"):
            if marker in path:
                output.append(urllib.parse.urlunsplit((parsed.scheme or "https", parsed.netloc, path.replace(marker, "/doi/pdfdirect/"), parsed.query, "")))
                output.append(urllib.parse.urlunsplit((parsed.scheme or "https", parsed.netloc, path.replace(marker, "/doi/pdf/"), parsed.query, "")))

    if "pubs.acs.org" in host and "/doi/" in path and "/doi/pdf" not in path:
        output.append(urllib.parse.urlunsplit((parsed.scheme or "https", parsed.netloc, path.replace("/doi/", "/doi/pdf/", 1), parsed.query, "")))

    if "mdpi.com" in host and re.search(r"/\d{4}-\d{3,4}/\d+/\d+/\d+/?$", path):
        output.append(urllib.parse.urlunsplit((parsed.scheme or "https", parsed.netloc, path.rstrip("/") + "/pdf", "", "")))

    if "europepmc.org/articles/" in url.lower() and "pdf=render" not in url.lower():
        separator = "&" if parsed.query else "?"
        output.append(url + separator + "pdf=render")

    deduped: list[str] = []
    seen: set[str] = set()
    for value in output:
        key = _canonical_url(value)
        if key not in seen:
            seen.add(key)
            deduped.append(value)
    return deduped


def _decode_html(data: bytes, declared_encoding: Optional[str]) -> str:
    encodings = [declared_encoding, "utf-8", "gb18030", "latin-1"]
    for encoding in encodings:
        if not encoding:
            continue
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")
