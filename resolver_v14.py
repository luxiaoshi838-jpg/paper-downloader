from __future__ import annotations

import threading
import time
import urllib.parse
from pathlib import Path

import app as legacy
from robust_resolver import ResolvedCandidate
from resolver_v13 import ResponsiveOpenAccessResolver


class CampusNetworkResolver(ResponsiveOpenAccessResolver):
    """优先使用当前网络的机构订阅权限，再回退到开放获取来源。

    不读取浏览器 Cookie，不配置额外代理，也不绕过登录或访问控制。
    当电脑已连接校园网、学校 VPN 或学校认可的机构网络时，普通 DOI
    请求会自然继承该网络的 IP 授权。
    """

    @staticmethod
    def _campus_candidate(doi: str) -> ResolvedCandidate:
        url = f"https://doi.org/{urllib.parse.quote(doi, safe='/():;._-')}"
        # oa_verified 在底层仅表示允许继续跟随网页中发现的 PDF 链接。
        # 此处的实际授权依据是当前校园网络，而不是开放获取许可。
        return ResolvedCandidate(
            url=url,
            source="校园网授权访问",
            landing_url=url,
            oa_verified=True,
            direct_hint=False,
        )

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

        campus = self._campus_candidate(record.doi)
        success, final_url, campus_message = self._download_candidate(
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
                source="校园网授权访问",
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
                source="校园网授权访问",
                url=final_url,
                message="任务已取消",
                raw_reference=record.raw_reference,
                elapsed_seconds=round(time.monotonic() - started, 2),
            )

        # 校园网直连未取得 PDF 后，再执行原有开放获取多源回退。
        fallback = super().download(record, output_dir, cancel_event)
        fallback.elapsed_seconds = round(time.monotonic() - started, 2)
        if fallback.status == "下载成功":
            return fallback

        detail = campus_message or "出版社页面未返回可下载 PDF"
        fallback.message = f"校园网直接访问未取得全文：{detail}；{fallback.message}"
        fallback.source = (
            f"校园网授权访问、{fallback.source}" if fallback.source else "校园网授权访问"
        )
        if not fallback.url:
            fallback.url = final_url
        return fallback


OpenAccessResolverV14 = CampusNetworkResolver
