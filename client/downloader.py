"""
客户端下载器包装层。

分发版客户端不能依赖 backend/ 目录，因此这里固定走客户端内置的
`standard_auto_downloader_core.py`，避免退回旧的外标搜索逻辑。
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

logger = logging.getLogger(__name__)


def _load_core() -> Tuple[Any, Any]:
    """加载客户端下载核心，开发环境下兼容后端路径。"""
    try:
        import standard_auto_downloader_core as core_module

        return core_module, core_module.StandardAutoDownloader
    except ImportError:
        from backend.services import standard_auto_downloader as core_module

        return core_module, core_module.StandardAutoDownloader


class StandardDownloader:
    """与 `client/app.py` 保持兼容的下载器接口。"""

    def __init__(
        self,
        download_dir: str = "downloads",
        progress_callback=None,
        cancel_callback=None,
        source_timeout: int = 45,
    ):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.progress_callback = progress_callback
        self.cancel_callback = cancel_callback
        self.source_timeout = source_timeout

    def _report(self, current: int, total: int, message: str, details: dict = None):
        if self.progress_callback:
            self.progress_callback(current, total, message, details)

    @staticmethod
    def _result_to_dict(result: Any) -> Dict[str, Any]:
        if result is None:
            return {}
        if is_dataclass(result):
            return asdict(result)
        if isinstance(result, dict):
            return dict(result)
        data = {}
        for key in (
            "standard_number",
            "success",
            "file_path",
            "download_url",
            "source",
            "message",
            "file_size",
        ):
            if hasattr(result, key):
                data[key] = getattr(result, key)
        return data

    @classmethod
    def _normalize_platform_results(
        cls, raw_results: Iterable[Any], fallback_source: str = "auto"
    ) -> Tuple[list[Dict[str, Any]], bool, str | None, str]:
        platform_results = []
        success = False
        first_file_path = None
        first_success_message = None
        first_error_message = None

        for raw in raw_results or []:
            data = cls._result_to_dict(raw)
            item_success = bool(data.get("success"))
            item = {
                "platform": data.get("source") or data.get("platform") or fallback_source,
                "status": "success" if item_success else "error",
                "standard_number": data.get("standard_number"),
                "file_path": data.get("file_path"),
                "download_url": data.get("download_url"),
                "view_url": (
                    data.get("download_url") if data.get("download_url") and not data.get("file_path") else None
                ),
                "message": data.get("message") or ("可用" if item_success else "无结果"),
                "file_size": data.get("file_size", 0),
            }
            platform_results.append(item)

            if item_success:
                success = True
                if first_success_message is None:
                    first_success_message = item["message"]
                if first_file_path is None and item.get("file_path"):
                    first_file_path = item["file_path"]
            elif first_error_message is None:
                first_error_message = item["message"]

        if success:
            message = first_success_message or "下载完成"
        else:
            message = first_error_message or "未找到可下载结果"

        return platform_results, success, first_file_path, message

    async def download(self, standard_number: str, source: str = "auto") -> Dict[str, Any]:
        """下载标准并返回前端结果结构。"""
        self._report(0, 100, f"开始下载: {standard_number}", {"standard": standard_number})

        _, core_cls = _load_core()
        downloader = core_cls(
            str(self.download_dir),
            cancel_callback=self.cancel_callback,
            source_timeout=self.source_timeout,
        )

        try:
            sources = None if source in (None, "", "auto") else [source]
            raw_results = await downloader.download(standard_number, sources=sources)
            platform_results, success, file_path, message = self._normalize_platform_results(
                raw_results,
                fallback_source=source or "auto",
            )
            self._report(
                100,
                100,
                "下载完成" if success else "未找到可下载结果",
                {"standard": standard_number, "success": success},
            )
            return {
                "standard": standard_number,
                "success": success,
                "message": message,
                "results": platform_results,
                "file_path": file_path,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        except Exception as e:
            logger.error("下载失败: %s", e)
            self._report(100, 100, f"下载失败: {e}", {"standard": standard_number})
            return {
                "standard": standard_number,
                "success": False,
                "message": f"下载出错: {e}",
                "results": [
                    {
                        "platform": "系统",
                        "status": "error",
                        "standard_number": standard_number,
                        "file_path": None,
                        "download_url": None,
                        "view_url": None,
                        "message": f"下载出错: {e}",
                        "file_size": 0,
                    }
                ],
                "file_path": None,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        finally:
            try:
                await downloader.close()
            except Exception as close_error:
                logger.debug("关闭下载器失败: %s", close_error)

    def get_sources(self) -> Dict[str, Dict[str, Any]]:
        """暴露当前客户端下载核心的下载源定义，便于调试。"""
        core_module, _ = _load_core()
        source_ids = getattr(
            core_module,
            "ACTIVE_DOWNLOAD_SOURCE_IDS",
            getattr(core_module, "SUPPORTED_DOWNLOAD_SOURCE_IDS", []),
        )
        source_map = getattr(core_module, "DOWNLOAD_SOURCES", {})
        disabled = getattr(core_module, "DISABLED_DOWNLOAD_SOURCES", set())

        result = {}
        for source_id in source_ids:
            if source_id in disabled:
                continue
            config = source_map.get(source_id, {})
            result[source_id] = {
                "id": source_id,
                "name": config.get("name", source_id),
                "type": config.get("type", ""),
                "url": config.get("search_url") or config.get("url") or "",
            }
        return result
