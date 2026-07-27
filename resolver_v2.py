from __future__ import annotations

import html
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import app as legacy

APP_VERSION = "1.1.0"
PROJECT_URL = "https://github.com/luxiaoshi838-jpg/paper-downloader"
DEFAULT_CONTACT_EMAIL = "luxiaoshi838-jpg@users.noreply.github.com"
USER_AGENT = f"paper-downloader/{APP_VERSION} ({PROJECT_URL}; mailto:{DEFAULT_CONTACT_EMAIL})"
MAX_HTML_BYTES = 2_000_000

META_PATTERNS = [
    re.compile(r'<meta[^>]+(?:name|property)=["\'](?:citation_pdf_url|pdf_url|eprints\.document_url|og:pdf)["\'][^>]+content=["\']([^"\']+)', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\'](?:citation_pdf_url|pdf_url|eprints\.document_url|og:pdf)["\']', re.I),
    re.compile(r'<link[^>]+(?:type=["\']application/pdf["\']|rel=["\'](?:alternate|enclosure)["\'])[^>]+href=["\']([^"\']+)', re.I),
    re.compile(r'<link[^>]+href=["\']([^"\']+)["\'][^>]+(?:type=["\']application/pdf["\']|rel=["\'](?:alternate|enclosure)["\'])', re.I),
    re.compile(r'["\'](?:pdfUrl|pdf_url|downloadUrl|download_url|documentUrl|fullTextPdfUrl)["\']\s*:\s*["\']([^"\']+)["\']', re.I),
]
HREF_PATTERN = re.compile(r'href=["\']([^"\']+)["\']', re.I)


@dataclass(frozen=True)
class Candidate:
    url: str
    source: str


@dataclass
class ResolveInfo:
    oa_flags: list[bool] = field(default_factory=list)
    api_errors: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @property
    def oa_status(self) -> str:
        if any(self.oa_flags):
            return "开放获取"
        if self.oa_flags and not any(self.oa_flags):
            return "非开放获取"
        return "未知"


class RateLimiter:
    def __init__(self, interval: float) -> None:
        self.interval = interval
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self) -> None:
        with self.lock:
            delay = self.interval - (time.monotonic() - self.last)
            if delay > 0:
                time.sleep(delay)
            self.last = time.monotonic()


def decode_url(value: str) -> str:
    return html.unescape(value.strip()).replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")


def looks_like_pdf(url: str) -> bool:
    value = urllib.parse.unquote(url).lower()
    return any(token in value for token in (
        ".pdf", "/pdf", "pdf?", "download", "fulltext", "full-text",
        "article/download", "viewcontent", "document", "file="
    ))


class OpenAccessResolverV2:
    """Improved legal/open-access resolver compatible with the original GUI."""

    _api_limiter = RateLimiter(0.45)
    _page_limiter = RateLimiter(0.15)
    _local = threading.local()

    def __init__(self, email: str, timeout: int = 30) -> None:
        self.email = email.strip() or DEFAULT_CONTACT_EMAIL
        self.timeout = timeout

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            retry = Retry(
                total=3,
                connect=3,
                read=2,
                status=3,
                backoff_factor=0.9,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset({"GET", "HEAD"}),
                respect_retry_after_header=True,
                raise_on_status=False,
            )
            session = requests.Session()
            adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            session.headers.update({
                "User-Agent": USER_AGENT,
                "Accept": "application/pdf,text/html,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.8,en;q=0.7",
            })
            self._local.session = session
        return session

    def _get(self, url: str, *, params: Optional[dict[str, str]] = None,
             stream: bool = False, api: bool = False) -> requests.Response:
        (self._api_limiter if api else self._page_limiter).wait()
        response = self._session().get(
            url, params=params, timeout=self.timeout,
            allow_redirects=True, stream=stream,
        )
        response.raise_for_status()
        return response

    def _json(self, url: str, params: Optional[dict[str, str]] = None) -> dict:
        return self._get(url, params=params, api=True).json()

    def candidates(self, doi: str) -> tuple[list[Candidate], ResolveInfo]:
        result: list[Candidate] = []
        info = ResolveInfo()
        seen: set[str] = set()

        def add(url: object, source: str) -> None:
            if not url:
                return
            absolute = urllib.parse.urljoin(f"https://doi.org/{doi}", decode_url(str(url)))
            if not absolute.startswith(("http://", "https://")) or absolute in seen:
                return
            seen.add(absolute)
            result.append(Candidate(absolute, source))
            if source not in info.sources:
                info.sources.append(source)

        try:
            data = self._json(
                f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='')}",
                {"email": self.email},
            )
            info.oa_flags.append(bool(data.get("is_oa")))
            locations = [data.get("best_oa_location") or {}, *(data.get("oa_locations") or [])]
            for item in locations:
                add(item.get("url_for_pdf"), "Unpaywall PDF")
                add(item.get("url"), "Unpaywall")
                add(item.get("url_for_landing_page"), "Unpaywall 页面")
        except requests.HTTPError as exc:
            info.api_errors.append(f"Unpaywall HTTP {getattr(exc.response, 'status_code', '?')}")
        except (requests.RequestException, ValueError, TypeError) as exc:
            info.api_errors.append(f"Unpaywall {type(exc).__name__}")

        try:
            data = self._json(
                "https://api.openalex.org/works/" + urllib.parse.quote(f"https://doi.org/{doi}", safe=""),
                {"mailto": self.email},
            )
            info.oa_flags.append(bool((data.get("open_access") or {}).get("is_oa")))
            locations = [data.get("best_oa_location") or {}, data.get("primary_location") or {}]
            locations.extend(data.get("locations") or [])
            for item in locations:
                add(item.get("pdf_url"), "OpenAlex PDF")
                add(item.get("landing_page_url"), "OpenAlex 页面")
        except requests.HTTPError as exc:
            info.api_errors.append(f"OpenAlex HTTP {getattr(exc.response, 'status_code', '?')}")
        except (requests.RequestException, ValueError, TypeError) as exc:
            info.api_errors.append(f"OpenAlex {type(exc).__name__}")

        try:
            data = self._json(
                f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}",
                {"mailto": self.email},
            )
            message = data.get("message") or {}
            for item in message.get("link") or []:
                url = item.get("URL") or item.get("url")
                content_type = str(item.get("content-type") or "").lower()
                if url and ("pdf" in content_type or "unspecified" in content_type or looks_like_pdf(str(url))):
                    add(url, "Crossref")
            add(((message.get("resource") or {}).get("primary") or {}).get("URL"), "Crossref 页面")
        except requests.HTTPError as exc:
            info.api_errors.append(f"Crossref HTTP {getattr(exc.response, 'status_code', '?')}")
        except (requests.RequestException, ValueError, TypeError) as exc:
            info.api_errors.append(f"Crossref {type(exc).__name__}")

        add(f"https://unpaywall.org/{doi}", "Unpaywall 跳转")
        add(f"https://doi.org/{doi}", "DOI 页面")
        return result, info

    def _extract_links(self, base_url: str, page: str) -> list[str]:
        links: list[str] = []
        seen: set[str] = set()

        def add(raw: str, force: bool = False) -> None:
            absolute = urllib.parse.urljoin(base_url, decode_url(raw))
            if not absolute.startswith(("http://", "https://")):
                return
            if not force and not looks_like_pdf(absolute):
                return
            if absolute not in seen:
                seen.add(absolute)
                links.append(absolute)

        for pattern in META_PATTERNS:
            for match in pattern.finditer(page):
                add(match.group(1), True)
        for match in HREF_PATTERN.finditer(page):
            add(match.group(1))
        return links[:30]

    def _download_url(self, candidate: Candidate, target: Path,
                      visited: Optional[set[str]] = None, depth: int = 0) -> tuple[bool, str, str]:
        visited = visited or set()
        if candidate.url in visited:
            return False, candidate.url, "重复跳转"
        if depth > 3:
            return False, candidate.url, "PDF 跳转层级过深"
        visited.add(candidate.url)

        try:
            response = self._get(candidate.url, stream=True)
            final_url = response.url
            content_type = response.headers.get("Content-Type", "").lower()
            iterator = response.iter_content(chunk_size=128 * 1024)
            first = next(iterator, b"")
            is_pdf = b"%PDF-" in first[:8192] or (
                "application/pdf" in content_type and b"<html" not in first[:2048].lower()
            )
            if is_pdf:
                temp = target.with_suffix(target.suffix + ".part")
                try:
                    with temp.open("wb") as handle:
                        handle.write(first)
                        for chunk in iterator:
                            if chunk:
                                handle.write(chunk)
                    if temp.stat().st_size < 1024:
                        temp.unlink(missing_ok=True)
                        return False, final_url, "PDF 文件过小"
                    with temp.open("rb") as handle:
                        if b"%PDF-" not in handle.read(8192):
                            temp.unlink(missing_ok=True)
                            return False, final_url, "返回内容不是有效 PDF"
                    temp.replace(target)
                    return True, final_url, ""
                finally:
                    response.close()

            content = bytearray(first)
            for chunk in iterator:
                if chunk:
                    content.extend(chunk)
                if len(content) >= MAX_HTML_BYTES:
                    break
            response.close()
            encoding = response.encoding or "utf-8"
            page = bytes(content[:MAX_HTML_BYTES]).decode(encoding, errors="replace")
            links = self._extract_links(final_url, page)
            for url in links:
                ok, final, message = self._download_url(
                    Candidate(url, candidate.source + " 页面"), target, visited, depth + 1
                )
                if ok:
                    return True, final, ""
            return False, final_url, f"网页未发现可用 PDF（疑似链接 {len(links)} 个）"
        except requests.HTTPError as exc:
            return False, candidate.url, f"HTTP {getattr(exc.response, 'status_code', '?')}"
        except requests.RequestException as exc:
            return False, candidate.url, f"网络错误：{type(exc).__name__}"
        except OSError as exc:
            return False, candidate.url, f"文件写入错误：{exc}"

    @staticmethod
    def _failure(info: ResolveInfo, messages: list[str]) -> tuple[str, str]:
        combined = "；".join([*info.api_errors, *messages])
        if info.oa_status == "非开放获取":
            return "未开放获取", "Unpaywall 和 OpenAlex 均未发现合法开放全文"
        if "429" in combined:
            return "接口限流", "接口限流；已自动退避重试但仍失败"
        if info.oa_status == "开放获取":
            return "开放链接失效", "文献被标记为开放获取，但页面未返回可用 PDF"
        if "HTTP 401" in combined or "HTTP 403" in combined:
            return "需要登录或授权", "页面要求登录、机构授权或拒绝自动访问"
        return "未找到公开PDF", "未找到可直接下载的公开 PDF"

    def download(self, record: legacy.ReferenceRecord, output_dir: Path,
                 cancel_event: threading.Event) -> legacy.DownloadResult:
        started = time.perf_counter()
        filename = legacy.build_pdf_filename(record.number, record.doi)
        target = output_dir / filename
        if cancel_event.is_set():
            return legacy.DownloadResult(record.number, record.doi, "已取消", filename=filename,
                                         message="任务已取消", raw_reference=record.raw_reference)
        if target.exists() and target.stat().st_size >= 1024:
            return legacy.DownloadResult(
                record.number, record.doi, "已存在", filename=filename,
                message="目标文件已存在，未重复下载", raw_reference=record.raw_reference,
                elapsed_seconds=round(time.perf_counter() - started, 2),
            )

        candidates, info = self.candidates(record.doi)
        messages: list[str] = []
        last_url = ""
        for candidate in candidates:
            if cancel_event.is_set():
                return legacy.DownloadResult(record.number, record.doi, "已取消", filename=filename,
                                             message="任务已取消", raw_reference=record.raw_reference)
            ok, final_url, message = self._download_url(candidate, target)
            last_url = final_url
            if message:
                messages.append(f"{candidate.source}: {message}")
            if ok:
                return legacy.DownloadResult(
                    record.number, record.doi, "下载成功", filename=filename,
                    source=candidate.source, url=final_url, raw_reference=record.raw_reference,
                    elapsed_seconds=round(time.perf_counter() - started, 2),
                )

        category, summary = self._failure(info, messages)
        detail = [f"失败分类：{category}", f"开放获取状态：{info.oa_status}", summary]
        if info.api_errors:
            detail.append("接口：" + "；".join(info.api_errors))
        if messages:
            detail.append("最后结果：" + messages[-1])
        return legacy.DownloadResult(
            record.number, record.doi, "下载失败", filename=filename, url=last_url,
            message="；".join(detail), raw_reference=record.raw_reference,
            elapsed_seconds=round(time.perf_counter() - started, 2),
        )
