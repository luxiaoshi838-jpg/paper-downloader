from __future__ import annotations

import atexit
import contextlib
import os
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from publisher_adapters import publisher_route_candidates
from resolver_v12 import validate_downloaded_pdf


BROWSER_TIMEOUT_MS = 35_000
REQUEST_TIMEOUT_MS = 22_000
MAX_BROWSER_CANDIDATES = 28
PROXY_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


@dataclass
class BrowserEngineResult:
    success: bool
    final_url: str = ""
    message: str = ""
    source: str = "Edge 浏览器出版社解析"


def _is_dead_local_proxy(value: str) -> bool:
    lowered = value.lower()
    return "127.0.0.1:9" in lowered or "localhost:9" in lowered


def _browser_env_without_dead_proxies() -> dict[str, str]:
    env = dict(os.environ)
    for name in PROXY_ENV_NAMES:
        if _is_dead_local_proxy(env.get(name, "")):
            env.pop(name, None)
    return env


@contextlib.contextmanager
def _temporarily_clear_dead_proxy_env():
    saved = {name: os.environ.get(name) for name in PROXY_ENV_NAMES}
    try:
        for name, value in saved.items():
            if value and _is_dead_local_proxy(value):
                os.environ.pop(name, None)
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class EdgePublisherEngine:
    """Render publisher pages in the installed Edge/Chrome browser.

    The engine intentionally uses the user's current network and ordinary
    publisher pages. It does not import browser profiles, solve CAPTCHAs or
    bypass authentication. Browser work is serialized because the surrounding
    downloader may run several DOI workers in parallel.
    """

    _singleton: "EdgePublisherEngine | None" = None
    _singleton_lock = threading.Lock()

    @classmethod
    def shared(cls) -> "EdgePublisherEngine":
        with cls._singleton_lock:
            if cls._singleton is None:
                cls._singleton = cls()
            return cls._singleton

    def __init__(self) -> None:
        self._operation_lock = threading.Lock()
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._browser_name = ""
        self._startup_error = ""
        atexit.register(self.close)

    def _ensure_started(self) -> bool:
        if self._context is not None:
            return True
        if self._startup_error:
            return False
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            self._startup_error = f"Playwright 不可用：{type(exc).__name__}: {exc}"
            return False

        try:
            with _temporarily_clear_dead_proxy_env():
                self._playwright = sync_playwright().start()
            chromium = self._playwright.chromium
            last_error = ""
            for channel in ("msedge", "chrome"):
                try:
                    self._browser = chromium.launch(
                        channel=channel,
                        headless=True,
                        args=["--disable-extensions", "--no-first-run"],
                        env=_browser_env_without_dead_proxies(),
                    )
                    self._browser_name = channel
                    break
                except Exception as exc:
                    last_error = f"{channel}: {type(exc).__name__}: {exc}"

            if self._browser is None:
                common_paths = (
                    os.path.expandvars(r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe"),
                    os.path.expandvars(r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe"),
                    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
                    os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
                    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
                )
                for executable in common_paths:
                    if not executable or not Path(executable).is_file():
                        continue
                    try:
                        self._browser = chromium.launch(
                            executable_path=executable,
                            headless=True,
                            args=["--disable-extensions", "--no-first-run"],
                            env=_browser_env_without_dead_proxies(),
                        )
                        self._browser_name = Path(executable).name
                        break
                    except Exception as exc:
                        last_error = f"{executable}: {type(exc).__name__}: {exc}"

            if self._browser is None:
                raise RuntimeError(last_error or "未找到可启动的 Microsoft Edge 或 Google Chrome")

            self._context = self._browser.new_context(
                accept_downloads=True,
                locale="zh-CN",
                viewport={"width": 1440, "height": 1000},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
            )
            self._context.set_default_timeout(12_000)
            self._context.set_default_navigation_timeout(BROWSER_TIMEOUT_MS)
            return True
        except Exception as exc:
            self._startup_error = f"浏览器启动失败：{type(exc).__name__}: {str(exc)[:240]}"
            self.close()
            return False

    def close(self) -> None:
        for value in (self._context, self._browser):
            if value is not None:
                try:
                    value.close()
                except Exception:
                    pass
        self._context = None
        self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._playwright = None

    def download(
        self,
        doi: str,
        target: Path,
        cancel_event: threading.Event,
        *,
        expected_title: str = "",
    ) -> BrowserEngineResult:
        with self._operation_lock:
            if cancel_event.is_set():
                return BrowserEngineResult(False, message="任务已取消")
            if not self._ensure_started():
                return BrowserEngineResult(False, message=self._startup_error)

            page = None
            observed_pdf_urls: list[str] = []
            try:
                page = self._context.new_page()

                def observe(response: Any) -> None:
                    try:
                        content_type = str(response.headers.get("content-type") or "").lower()
                        if "application/pdf" in content_type or _looks_pdf_url(response.url):
                            observed_pdf_urls.append(response.url)
                    except Exception:
                        pass

                page.on("response", observe)
                doi_url = f"https://doi.org/{urllib.parse.quote(doi, safe='/():;._-')}"
                response = page.goto(doi_url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)
                page.wait_for_timeout(1_500)
                final_url = page.url or (response.url if response else doi_url)

                if cancel_event.is_set():
                    return BrowserEngineResult(False, final_url=final_url, message="任务已取消")

                body_text = ""
                title = ""
                try:
                    title = page.title()
                    body_text = page.locator("body").inner_text(timeout=5_000)[:8_000]
                except Exception:
                    pass
                blocked = _detect_blocked_page(title + "\n" + body_text)

                # ScienceDirect exposes one of its strongest routes only after
                # the PDF control is opened. This normal click mirrors ordinary
                # user interaction; no CAPTCHA or login action is attempted.
                for selector in ("#pdfLink", "button:has-text('Download PDF')"):
                    try:
                        locator = page.locator(selector).first
                        if locator.count() and locator.is_visible():
                            locator.click(timeout=4_000)
                            page.wait_for_timeout(600)
                    except Exception:
                        continue

                page_data = self._collect_page_data(page)
                candidates = publisher_route_candidates(
                    final_url,
                    doi,
                    canonical_url=page_data.get("canonical", ""),
                    meta_pdf_urls=page_data.get("meta", []),
                    dom_urls=[*observed_pdf_urls, *page_data.get("links", [])],
                    json_scripts=page_data.get("jsonScripts", []),
                )

                errors: list[str] = []
                for candidate in candidates[:MAX_BROWSER_CANDIDATES]:
                    if cancel_event.is_set():
                        return BrowserEngineResult(False, final_url=final_url, message="任务已取消")
                    ok, saved_url, error = self._request_pdf(
                        candidate,
                        final_url,
                        target,
                        doi=doi,
                        expected_title=expected_title,
                    )
                    if ok:
                        return BrowserEngineResult(True, final_url=saved_url)
                    if error:
                        errors.append(error)

                # Some sites trigger an actual browser download instead of
                # exposing a stable href. Try only explicit PDF controls.
                click_result = self._click_download_controls(
                    page,
                    target,
                    doi=doi,
                    expected_title=expected_title,
                    cancel_event=cancel_event,
                )
                if click_result.success:
                    return click_result

                detail = "；".join(dict.fromkeys(errors))[:500]
                if blocked:
                    message = blocked
                elif not candidates:
                    message = "真实浏览器已打开出版社页面，但页面没有暴露 PDF 下载入口"
                else:
                    message = "浏览器提取到 PDF 路径，但出版社未返回有效 PDF"
                if detail:
                    message += f"；{detail}"
                return BrowserEngineResult(False, final_url=final_url, message=message)
            except Exception as exc:
                # A crashed browser is restarted for the next DOI.
                message = f"浏览器解析异常：{type(exc).__name__}: {str(exc)[:260]}"
                if "Target page, context or browser has been closed" in str(exc):
                    self.close()
                    self._startup_error = ""
                return BrowserEngineResult(False, final_url=getattr(page, "url", "") or "", message=message)
            finally:
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass

    @staticmethod
    def _collect_page_data(page: Any) -> dict[str, Any]:
        script = r"""
        () => {
          const one = (selector, attr) => {
            const node = document.querySelector(selector);
            return node ? (node.getAttribute(attr) || '') : '';
          };
          const values = (selectors, attrs) => {
            const out = [];
            for (const selector of selectors) {
              for (const node of document.querySelectorAll(selector)) {
                for (const attr of attrs) {
                  const value = node.getAttribute(attr);
                  if (value) out.push(value);
                }
              }
            }
            return out;
          };
          const links = values([
            'a.article-pdfLink', 'a.intent_pdf_link', 'a#pdf-link',
            'a.pdf-download-btn-link', '.PdfDropDownMenu a',
            'a[href*="/pdf/"]', 'a[href*="/pdfdirect/"]',
            'a[href*="pdfft"]', 'a[href$=".pdf"]',
            'object[data]', 'embed[src]', 'iframe[src]'
          ], ['href', 'data-article-url', 'data', 'src']);
          const meta = values([
            'meta[name="citation_pdf_url"]',
            'meta[property="citation_pdf_url"]',
            'link[type="application/pdf"]'
          ], ['content', 'href']);
          return {
            canonical: one('link[rel="canonical"]', 'href'),
            meta,
            links,
            jsonScripts: Array.from(document.querySelectorAll('script[type="application/json"]'))
              .map(node => node.textContent || '').filter(Boolean).slice(0, 20)
          };
        }
        """
        try:
            value = page.evaluate(script)
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _request_pdf(
        self,
        url: str,
        referer: str,
        target: Path,
        *,
        doi: str,
        expected_title: str,
    ) -> tuple[bool, str, str]:
        response = None
        try:
            response = self._context.request.get(
                url,
                headers={
                    "Accept": "application/pdf,text/html;q=0.8,*/*;q=0.5",
                    "Referer": referer,
                },
                timeout=REQUEST_TIMEOUT_MS,
                fail_on_status_code=False,
            )
            status = response.status
            final_url = response.url
            if status in {401, 403}:
                return False, final_url, f"{urllib.parse.urlsplit(final_url).netloc} HTTP {status}"
            if status == 429:
                return False, final_url, f"{urllib.parse.urlsplit(final_url).netloc} HTTP 429"
            if status < 200 or status >= 300:
                return False, final_url, f"{urllib.parse.urlsplit(final_url).netloc} HTTP {status}"
            data = response.body()
            content_type = str(response.headers.get("content-type") or "").lower()
            if b"%PDF-" not in data[:2048] and "application/pdf" not in content_type:
                return False, final_url, "返回内容仍是 HTML"
            ok, reason = _write_and_validate_pdf(
                data,
                target,
                doi=doi,
                expected_title=expected_title,
                url=final_url,
            )
            return ok, final_url, reason
        except Exception as exc:
            return False, url, f"{type(exc).__name__}: {str(exc)[:140]}"
        finally:
            if response is not None:
                try:
                    response.dispose()
                except Exception:
                    pass

    def _click_download_controls(
        self,
        page: Any,
        target: Path,
        *,
        doi: str,
        expected_title: str,
        cancel_event: threading.Event,
    ) -> BrowserEngineResult:
        selectors = (
            "a.article-pdfLink",
            "a.intent_pdf_link",
            "a#pdf-link",
            "a:has-text('Download PDF')",
            "button:has-text('Download PDF')",
        )
        for selector in selectors:
            if cancel_event.is_set():
                return BrowserEngineResult(False, final_url=page.url, message="任务已取消")
            try:
                locator = page.locator(selector).first
                if not locator.count() or not locator.is_visible():
                    continue
                with page.expect_download(timeout=7_000) as info:
                    locator.click(timeout=5_000)
                download = info.value
                temp = target.with_suffix(target.suffix + ".browser.part")
                download.save_as(str(temp))
                ok, reason = validate_downloaded_pdf(
                    temp,
                    expected_doi=doi,
                    expected_title=expected_title,
                    source="Edge 浏览器出版社解析",
                    url=download.url,
                )
                if ok:
                    temp.replace(target)
                    return BrowserEngineResult(True, final_url=download.url)
                temp.unlink(missing_ok=True)
            except Exception:
                continue
        return BrowserEngineResult(False, final_url=page.url, message="未触发浏览器文件下载")


def _write_and_validate_pdf(
    data: bytes,
    target: Path,
    *,
    doi: str,
    expected_title: str,
    url: str,
) -> tuple[bool, str]:
    if len(data) < 1024 or b"%PDF-" not in data[:2048]:
        return False, "内容不是有效 PDF"
    temp = target.with_suffix(target.suffix + ".browser.part")
    try:
        temp.write_bytes(data)
        ok, reason = validate_downloaded_pdf(
            temp,
            expected_doi=doi,
            expected_title=expected_title,
            source="Edge 浏览器出版社解析",
            url=url,
        )
        if not ok:
            temp.unlink(missing_ok=True)
            return False, f"PDF 校验失败：{reason}"
        temp.replace(target)
        return True, ""
    except OSError as exc:
        temp.unlink(missing_ok=True)
        return False, f"文件写入失败：{exc}"


def _looks_pdf_url(url: str) -> bool:
    value = urllib.parse.unquote(url or "").lower()
    return any(
        marker in value
        for marker in (
            ".pdf",
            "/pdf/",
            "/pdfdirect/",
            "/pdfft",
            "?pdf=render",
            "/full/pdf",
        )
    )


def _detect_blocked_page(text: str) -> str:
    lowered = (text or "").lower()
    if any(marker in lowered for marker in ("verify you are human", "captcha", "cloudflare ray id")):
        return "出版社要求人工验证，程序未尝试绕过验证码"
    if any(marker in lowered for marker in ("access denied", "request blocked", "unusual traffic")):
        return "出版社在浏览器中仍拒绝自动访问"
    if any(marker in lowered for marker in ("sign in through your institution", "institutional login required")):
        return "出版社要求机构登录，当前校园网 IP 未直接授予该页面权限"
    return ""
