from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Iterable


def _absolute(base_url: str, value: str) -> str:
    value = (value or "").strip().replace("\\/", "/")
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    return urllib.parse.urljoin(base_url, value)


def _dedupe(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = (value or "").strip()
        if not value.startswith(("http://", "https://")):
            continue
        parsed = urllib.parse.urlsplit(value)
        key = urllib.parse.urlunsplit(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                re.sub(r"/{2,}", "/", parsed.path).rstrip("/"),
                parsed.query,
                "",
            )
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def _science_direct_json_urls(base_url: str, payload: Any) -> list[str]:
    """Extract ScienceDirect PDF routes from rendered JSON state.

    ScienceDirect commonly embeds a pdfDownload.urlMetadata object containing
    path, PII, extension and short-lived md5/pid parameters. The exact nesting
    changes, so this intentionally walks the JSON tree rather than depending on
    one page-state wrapper name.
    """
    output: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            path = value.get("path")
            extension = value.get("pdfExtension")
            pii = value.get("pii")
            params = value.get("queryParams")
            if path and extension and pii and isinstance(params, dict):
                md5 = params.get("md5")
                pid = params.get("pid")
                if md5 and pid:
                    relative = f"/{str(path).strip('/')}/{pii}{extension}"
                    query = urllib.parse.urlencode({"md5": md5, "pid": pid})
                    output.append(_absolute(base_url, relative + "?" + query))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return output


def parse_json_script_urls(base_url: str, scripts: Iterable[str]) -> list[str]:
    output: list[str] = []
    for script in scripts:
        text = (script or "").strip()
        if not text or text[0] not in "[{":
            continue
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            continue
        output.extend(_science_direct_json_urls(base_url, payload))
    return _dedupe(output)


def publisher_route_candidates(
    final_url: str,
    doi: str,
    *,
    canonical_url: str = "",
    meta_pdf_urls: Iterable[str] = (),
    dom_urls: Iterable[str] = (),
    json_scripts: Iterable[str] = (),
) -> list[str]:
    """Build official PDF candidates from a rendered publisher page.

    The routes mirror long-maintained publisher translators, but this module is
    a clean Python implementation and does not execute or copy translator code.
    """
    parsed = urllib.parse.urlsplit(final_url)
    host = parsed.netloc.lower()
    path = parsed.path
    encoded_doi = urllib.parse.quote(doi, safe="/():;._-")
    candidates: list[str] = []

    for value in meta_pdf_urls:
        candidates.append(_absolute(final_url, value))
    for value in dom_urls:
        candidates.append(_absolute(final_url, value))

    canonical = _absolute(final_url, canonical_url) if canonical_url else ""
    if canonical:
        candidates.append(canonical)

    # Elsevier / ScienceDirect. LinkingHub normally redirects to a PII page.
    pii_match = re.search(r"/(?:retrieve/)?pii/([^/?#]+)", path, re.IGNORECASE)
    if "elsevier.com" in host or "sciencedirect.com" in host:
        if pii_match:
            pii = pii_match.group(1)
            page = f"https://www.sciencedirect.com/science/article/pii/{pii}"
            candidates.extend(
                (
                    page + "/pdfft?isDTMRedir=true&download=true",
                    page + "/pdfft?download=true",
                )
            )
        if canonical and "/science/article/pii/" in canonical:
            base = canonical.rstrip("/")
            candidates.extend((base + "/pdfft?download=true", base + "/pdfft?isDTMRedir=true&download=true"))
        candidates.extend(parse_json_script_urls(final_url, json_scripts))

    # Wiley and AGU use the same Atypon route. pdfdirect avoids the HTML PDF viewer.
    if "onlinelibrary.wiley.com" in host:
        base = f"https://{parsed.netloc}"
        candidates.extend(
            (
                f"{base}/doi/pdfdirect/{encoded_doi}",
                f"{base}/doi/pdf/{encoded_doi}",
            )
        )

    # Taylor & Francis and NEJM-style Atypon pages.
    if "tandfonline.com" in host or "nejm.org" in host:
        base = f"https://{parsed.netloc}"
        candidates.append(f"{base}/doi/pdf/{encoded_doi}")

    if "ascelibrary.org" in host:
        candidates.append(f"https://{parsed.netloc}/doi/pdf/{encoded_doi}")

    if "link.springer.com" in host:
        candidates.append(f"https://link.springer.com/content/pdf/{encoded_doi}.pdf")

    if "emerald.com" in host:
        match = re.search(r"/insight/content/doi/(10\.[^?#]+?)/(?:full/)?(?:html|pdf)?$", path, re.IGNORECASE)
        emerald_doi = match.group(1) if match else doi
        candidates.append(
            f"https://www.emerald.com/insight/content/doi/{urllib.parse.quote(emerald_doi, safe='/():;._-')}/full/pdf"
        )

    if "mdpi.com" in host and not path.rstrip("/").endswith("/pdf"):
        candidates.append(urllib.parse.urlunsplit((parsed.scheme or "https", parsed.netloc, path.rstrip("/") + "/pdf", "", "")))

    # Silverchair powers OUP, GeoScienceWorld, ASM, IWA, UC Press and others.
    # Its official PDF link is normally exposed as article-pdfLink or #pdf-link,
    # so DOM links above are authoritative. These route expansions cover pages
    # where only an article/abstract URL is visible before JavaScript finishes.
    if any(
        marker in host
        for marker in (
            "academic.oup.com",
            "pubs.geoscienceworld.org",
            "journals.asm.org",
            "iwaponline.com",
            "online.ucpress.edu",
        )
    ):
        for value in list(candidates):
            value_parsed = urllib.parse.urlsplit(value)
            if "/article-abstract/" in value_parsed.path:
                candidates.append(value.replace("/article-abstract/", "/article/"))

    return _dedupe(candidates)
