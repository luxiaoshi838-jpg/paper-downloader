from __future__ import annotations

import threading
import time
from pathlib import Path

import app as legacy
from browser_publisher_engine import EdgePublisherEngine
from resolver_v13 import ResponsiveOpenAccessResolver
from resolver_v14 import CampusNetworkResolver


class BrowserPublisherResolver(CampusNetworkResolver):
    """Campus-IP requests, then rendered publisher browser, then OA fallback."""

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

        # Fast path: ordinary HTTP requests inherit campus/VPN IP authorization.
        campus = self._campus_candidate(record.doi)
        success, final_url, request_message = self._download_candidate(
            campus,
            target,
            doi=record.doi,
            visited=set(),
            depth=0,
        )
        if success:
            return legacy.DownloadResult(
                record.number,
                record.doi,
                "下载成功",
                filename=filename,
                source="校园网授权访问（直接请求）",
                url=final_url,
                raw_reference=record.raw_reference,
                elapsed_seconds=round(time.monotonic() - started, 2),
            )

        if cancel_event.is_set():
            return legacy.DownloadResult(
                record.number,
                record.doi,
                "已取消",
                filename=filename,
                source="校园网授权访问（直接请求）",
                url=final_url,
                message="任务已取消",
                raw_reference=record.raw_reference,
                elapsed_seconds=round(time.monotonic() - started, 2),
            )

        # Mature browser path: JavaScript-render the publisher page in the
        # installed Edge/Chrome and use official PDF controls/routes.
        browser_result = EdgePublisherEngine.shared().download(
            record.doi,
            target,
            cancel_event,
        )
        if browser_result.success:
            return legacy.DownloadResult(
                record.number,
                record.doi,
                "下载成功",
                filename=filename,
                source=browser_result.source,
                url=browser_result.final_url,
                raw_reference=record.raw_reference,
                elapsed_seconds=round(time.monotonic() - started, 2),
            )

        if cancel_event.is_set():
            return legacy.DownloadResult(
                record.number,
                record.doi,
                "已取消",
                filename=filename,
                source=browser_result.source,
                url=browser_result.final_url or final_url,
                message="任务已取消",
                raw_reference=record.raw_reference,
                elapsed_seconds=round(time.monotonic() - started, 2),
            )

        # Last path: repositories/open-access sources. Calling the grandparent
        # implementation avoids repeating the campus and browser stages.
        fallback = ResponsiveOpenAccessResolver.download(
            self,
            record,
            output_dir,
            cancel_event,
        )
        fallback.elapsed_seconds = round(time.monotonic() - started, 2)
        if fallback.status == "下载成功":
            return fallback

        direct_detail = request_message or "出版社落地页未返回 PDF"
        browser_detail = browser_result.message or "浏览器未取得 PDF"
        fallback.message = (
            f"直接访问失败：{direct_detail}；"
            f"Edge 出版社解析失败：{browser_detail}；"
            f"{fallback.message}"
        )
        stages = ["校园网直接请求", "Edge 出版社解析"]
        if fallback.source:
            stages.append(fallback.source)
        fallback.source = "、".join(stages)
        if not fallback.url:
            fallback.url = browser_result.final_url or final_url
        return fallback


OpenAccessResolverV15 = BrowserPublisherResolver
