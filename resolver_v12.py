from __future__ import annotations

import html
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, Iterable, Optional

from pypdf import PdfReader

from robust_resolver import (
    ResolutionContext,
    ResolvedCandidate,
    RobustOpenAccessResolver,
    _candidate_priority,
)

SUSPICIOUS_MARKERS = (
    "flyer",
    "brochure",
    "leaflet",
    "cover",
    "masthead",
    "frontmatter",
    "front-matter",
    "issue-info",
    "advert",
    "promo",
)

TITLE_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "using", "under", "over",
    "between", "through", "within", "across", "this", "that", "study", "case",
    "analysis", "approach", "effects", "effect", "based", "canada", "journal",
}


class EnhancedOpenAccessResolver(RobustOpenAccessResolver):
    """v1.2 resolver: legal OA fallbacks plus PDF identity validation."""

    def __init__(self, email: str, timeout: int = 30, session=None) -> None:
        super().__init__(email=email, timeout=timeout, session=session)
        self.core_api_key = os.environ.get("CORE_API_KEY", "").strip()
        self.elsevier_api_key = os.environ.get("ELSEVIER_API_KEY", "").strip()
        if self.elsevier_api_key:
            self.session.headers["X-ELS-APIKey"] = self.elsevier_api_key
        self._expected_title = ""

    def resolve(self, doi: str) -> ResolutionContext:
        context = super().resolve(doi)
        self._expected_title = context.title
        self._resolve_core(context)
        self._resolve_preprint_ids(context)
        self._resolve_elsevier_api(context)
        context.candidates.sort(key=_candidate_priority)
        return context

    def _resolve_core(self, context: ResolutionContext) -> None:
        query = urllib.parse.quote(f'doi:"{context.doi}"')
        url = f"https://api.core.ac.uk/v3/search/works?q={query}&limit=5"
        headers = {"Accept": "application/json"}
        if self.core_api_key:
            headers["Authorization"] = f"Bearer {self.core_api_key}"
        try:
            response = self._request(url, headers=headers)
            if response.status_code == 429:
                context.diagnostics.append("CORE：HTTP 429（接口限流）")
                return
            if not response.ok:
                context.diagnostics.append(f"CORE：HTTP {response.status_code}")
                return
            payload = response.json()
        except Exception as exc:
            context.diagnostics.append(f"CORE：{type(exc).__name__}: {str(exc)[:120]}")
            return

        matched = False
        for value in _iter_urls(payload):
            if not _is_plausible_core_pdf(value):
                continue
            context.add_candidate(
                value,
                "CORE",
                oa_verified=True,
                direct_hint=True,
            )
            matched = True
        if matched:
            context.metadata_sources.add("CORE")
            context.is_oa = True
        else:
            context.diagnostics.append("CORE：未返回可用 PDF 链接")

    def _resolve_preprint_ids(self, context: ResolutionContext) -> None:
        paper_id = urllib.parse.quote(f"DOI:{context.doi}", safe="")
        payload, error = self._json_get(
            f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}",
            params={"fields": "externalIds,title,isOpenAccess,openAccessPdf,url"},
        )
        if payload is None:
            if error and "429" not in error:
                context.diagnostics.append(f"预印本标识：{error}")
            return
        external = payload.get("externalIds") or {}
        if not isinstance(external, dict):
            return
        arxiv_id = str(external.get("ArXiv") or "").strip()
        if arxiv_id:
            context.add_candidate(
                f"https://arxiv.org/pdf/{urllib.parse.quote(arxiv_id, safe='._-')}",
                "arXiv",
                landing_url=f"https://arxiv.org/abs/{urllib.parse.quote(arxiv_id, safe='._-')}",
                oa_verified=True,
                direct_hint=True,
            )
            context.is_oa = True
        if context.doi.startswith("10.1101/"):
            context.add_candidate(
                f"https://www.biorxiv.org/content/{urllib.parse.quote(context.doi, safe='/._-')}.full.pdf",
                "bioRxiv/medRxiv",
                landing_url=f"https://doi.org/{context.doi}",
                oa_verified=True,
                direct_hint=True,
            )
            context.is_oa = True

    def _resolve_elsevier_api(self, context: ResolutionContext) -> None:
        if not self.elsevier_api_key or not context.doi.lower().startswith("10.1016/"):
            return
        if context.is_oa is not True:
            return
        encoded = urllib.parse.quote(context.doi, safe="/:()._-;")
        context.add_candidate(
            f"https://api.elsevier.com/content/article/doi:{encoded}?view=FULL",
            "Elsevier API（用户密钥）",
            oa_verified=True,
            direct_hint=True,
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
        success, final_url, message = super()._download_candidate(
            candidate,
            target,
            doi=doi,
            visited=visited,
            depth=depth,
        )
        if not success:
            return success, final_url, message

        ok, reason = validate_downloaded_pdf(
            target,
            expected_doi=doi,
            expected_title=self._expected_title,
            source=candidate.source,
            url=final_url or candidate.url,
        )
        if ok:
            return True, final_url, ""
        target.unlink(missing_ok=True)
        return False, final_url, f"PDF 身份校验失败：{reason}"


OpenAccessResolverV12 = EnhancedOpenAccessResolver


def validate_downloaded_pdf(
    path: Path,
    *,
    expected_doi: str,
    expected_title: str,
    source: str,
    url: str,
) -> tuple[bool, str]:
    try:
        size = path.stat().st_size
        if size < 1000:
            return False, "文件小于 1 KB"
        with path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                return False, "缺少 PDF 文件头"
            handle.seek(max(0, size - 4096))
            if b"%%EOF" not in handle.read():
                return False, "缺少 PDF 结束标记"
    except OSError as exc:
        return False, f"无法读取文件：{exc}"

    lowered = (source + " " + url).lower()
    high_risk = source.lower().startswith("doaj") or any(
        marker in lowered for marker in SUSPICIOUS_MARKERS
    )

    try:
        reader = PdfReader(str(path), strict=False)
        page_count = len(reader.pages)
        metadata_title = str((reader.metadata or {}).get("/Title") or "")
        texts = [metadata_title]
        for page in reader.pages[: min(5, page_count)]:
            try:
                texts.append(page.extract_text() or "")
            except Exception:
                continue
        text = "\n".join(texts)
    except Exception as exc:
        if high_risk:
            return False, f"高风险来源且无法解析 PDF：{type(exc).__name__}"
        return True, ""

    if page_count <= 0:
        return False, "PDF 没有页面"
    if size < 50_000 and page_count <= 1:
        high_risk = True

    if not high_risk:
        return True, ""

    normalized_text = _normalize_identity_text(text)
    normalized_doi = _normalize_doi_for_match(expected_doi)
    doi_match = normalized_doi and normalized_doi in normalized_text
    title_match = _title_matches(expected_title, text)
    if doi_match or title_match:
        return True, ""
    return False, "高风险链接的 PDF 中未匹配到目标 DOI 或题名"


def _iter_urls(obj: Any) -> Iterable[str]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str) and ("url" in key.lower() or value.startswith(("http://", "https://"))):
                yield html.unescape(value)
            else:
                yield from _iter_urls(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_urls(item)


def _is_plausible_core_pdf(url: str) -> bool:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return False
    parsed = urllib.parse.urlsplit(url)
    host = parsed.netloc.lower()
    combined = (parsed.path + "?" + parsed.query).lower()
    if "core.ac.uk" in host and any(
        marker in combined
        for marker in ("/data-providers/", "/providers/", "/journals/", "/subjects/")
    ):
        return False
    return any(
        marker in combined
        for marker in (
            ".pdf",
            "/pdf",
            "download/pdf",
            "format=pdf",
            "type=pdf",
            "bitstream",
            "/document",
        )
    )


def _normalize_doi_for_match(value: str) -> str:
    value = urllib.parse.unquote(value).lower().strip()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value)
    return re.sub(r"\s+", "", value)


def _normalize_identity_text(value: str) -> str:
    value = urllib.parse.unquote(value).lower()
    value = value.replace("https://doi.org/", "").replace("http://doi.org/", "")
    value = value.replace("doi:", "")
    return re.sub(r"\s+", "", value)


def _title_matches(expected_title: str, actual_text: str) -> bool:
    expected_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", expected_title.lower())
        if len(token) >= 4 and token not in TITLE_STOPWORDS
    }
    if len(expected_tokens) < 4:
        return False
    actual_tokens = set(re.findall(r"[a-z0-9]+", actual_text.lower()))
    matched = expected_tokens & actual_tokens
    return len(matched) >= 4 and len(matched) / len(expected_tokens) >= 0.55
