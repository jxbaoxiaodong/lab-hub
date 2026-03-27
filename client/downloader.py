"""标准下载服务 - 整合自 standarddownload"""

import re
import json
import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class StandardDownloadInfo:
    """标准下载信息"""

    standard_number: str
    title: str
    year: str
    status: str
    organization: str
    download_links: List[Dict]
    preview_links: List[str]
    match_score: float


# 搜索源配置
SEARCH_SOURCES = {
    "iso_official": {
        "name": "ISO 官方",
        "base_url": "https://www.iso.org/standard/",
        "search_url": "https://www.iso.org/search.html?q={query}&sort=rel&type=standard",
        "preview_url": "https://standards.iso.org/ittf/PubliclyAvailableStandards/",
        "format": "direct",
        "free": True,
    },
    "gb_official": {
        "name": "中国国家标准",
        "search_url": "http://openstd.samr.gov.cn/bzgk/gb/index?t=gb&f=2026&q={query}",
        "preview_url": "http://openstd.samr.gov.cn/bzgk/gb/",
        "format": "preview",
        "free": True,
    },
    "researchgate": {
        "name": "ResearchGate",
        "search_url": "https://www.researchgate.net/search/publication?q={query}",
        "format": "preview",
        "free": True,
        "requires_login": True,
    },
    "pdfdrive": {
        "name": "PDFdrive",
        "search_url": "https://www.pdfdrive.to/search?q={query}",
        "format": "direct",
        "free": True,
    },
    "scribd": {
        "name": "Scribd",
        "search_url": "https://www.scribd.com/search?query={query}",
        "format": "preview",
        "free": False,
        "trial": True,
    },
    "academia": {
        "name": "Academia.edu",
        "search_url": "https://www.academia.edu/search?q={query}",
        "format": "preview",
        "free": True,
        "requires_login": True,
    },
    "astm": {
        "name": "ASTM",
        "search_url": "https://www.astm.org/search-results.html?q={query}",
        "format": "preview",
        "free": False,
    },
    "ansi": {
        "name": "ANSI",
        "search_url": "https://webstore.ansi.org/standards/ansistandard?searchterm={query}",
        "format": "preview",
        "free": False,
    },
    "iec": {
        "name": "IEC",
        "search_url": "https://webstore.iec.ch/search/public?q={query}",
        "format": "preview",
        "free": False,
    },
    "google": {
        "name": "Google 镜像搜索",
        "search_url": "https://www.google.com/search?q={query}+filetype:pdf+download",
        "format": "search",
        "free": True,
    },
}

# 常见ISO标准直接链接
ISO_DIRECT_LINKS = {
    "9001": "https://standards.iso.org/ittf/PubliclyAvailableStandards/ISO_IEC%209001_2015%20ed.5%20-%20id.69711%20Publication%20PDF%20(en).zip",
    "14001": "https://standards.iso.org/ittf/PubliclyAvailableStandards/ISO%2014001_2015%20ed.2%20-%20id.65382%20Publication%20PDF%20(en).zip",
    "27001": "https://standards.iso.org/ittf/PubliclyAvailableStandards/ISO_IEC%2027001_2022%20ed.3%20-%20id.73119%20Publication%20PDF%20(en).zip",
    "45001": "https://standards.iso.org/ittf/PubliclyAvailableStandards/ISO%2045001_2018%20ed.1%20-%20id.221217%20Publication%20PDF%20(en).zip",
    "32000-2": "https://standards.iso.org/ittf/PubliclyAvailableStandards/ISO%2032000-2_2020%20ed.1%20-%20id.73786%20Publication%20PDF%20(en).zip",
}


class StandardDownloader:
    """标准下载器"""

    def __init__(self, download_dir: str = "downloads", progress_callback=None):
        self.download_dir = download_dir
        self.progress_callback = progress_callback
        self.results: List[StandardDownloadInfo] = []

    def _report(self, current: int, total: int, message: str, details: dict = None):
        if self.progress_callback:
            self.progress_callback(current, total, message, details)

    def parse_standard_number(self, query: str) -> Dict:
        """解析标准号"""
        result = {
            "original": query,
            "organization": None,
            "number": None,
            "year": None,
            "normalized": None,
        }

        query = query.strip().upper()

        patterns = [
            (r"(ISO)\s*(\d+)\s*[:\-]?\s*(\d{4})?", "ISO"),
            (r"(GB/T)\s*(\d+)\s*[:\-]?\s*(\d{4})?", "GB"),
            (r"(GB)\s*(\d+)\s*[:\-]?\s*(\d{4})?", "GB"),
            (r"(ASTM)\s*([A-Z]+\d+)\s*[:\-]?\s*(\d{4})?", "ASTM"),
            (r"(ANSI)[/]?(ISO)?\s*(\d+)\s*[:\-]?\s*(\d{4})?", "ANSI"),
            (r"(IEC)\s*(\d+)\s*[:\-]?\s*(\d{4})?", "IEC"),
            (r"(IEEE)\s*(\d+\.?\d*)\s*[:\-]?\s*(\d{4})?", "IEEE"),
        ]

        for pattern, org in patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                result["organization"] = org
                result["number"] = match.group(2)
                if match.lastindex >= 3 and match.group(3):
                    result["year"] = match.group(3)
                break

        if result["organization"] and result["number"]:
            if result["year"]:
                result["normalized"] = (
                    f"{result['organization']} {result['number']}:{result['year']}"
                )
            else:
                result["normalized"] = f"{result['organization']} {result['number']}"
        else:
            result["normalized"] = query

        return result

    def fuzzy_match(self, query: str, candidates: List[str]) -> List[Tuple[str, float]]:
        """模糊匹配"""

        def similarity(s1: str, s2: str) -> float:
            s1 = s1.upper().replace(" ", "").replace("-", "").replace(":", "")
            s2 = s2.upper().replace(" ", "").replace("-", "").replace(":", "")

            if s1 == s2:
                return 1.0
            if s1 in s2 or s2 in s1:
                return 0.8

            common = sum(1 for c in s1 if c in s2)
            return common / max(len(s1), len(s2)) if max(len(s1), len(s2)) > 0 else 0

        results = []
        for candidate in candidates:
            score = similarity(query, candidate)
            if score > 0.3:
                results.append((candidate, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def search_iso(self, query: str) -> List[Dict]:
        """搜索ISO标准"""
        results = []
        parsed = self.parse_standard_number(query)

        if parsed["organization"] == "ISO" and parsed["number"]:
            num = parsed["number"].replace(".", "-")
            if num in ISO_DIRECT_LINKS:
                results.append(
                    {
                        "source": "iso_official",
                        "url": ISO_DIRECT_LINKS[num],
                        "type": "direct",
                        "free": True,
                    }
                )

        return results

    def search_gb(self, query: str) -> List[Dict]:
        """搜索GB标准"""
        results = []
        parsed = self.parse_standard_number(query)

        if parsed["organization"] in ["GB", "GB/T"]:
            results.append(
                {
                    "source": "gb_official",
                    "name": "国家标准全文公开系统",
                    "url": "http://openstd.samr.gov.cn/bzgk/gb/index?t=gb",
                    "type": "preview",
                    "free": True,
                    "note": "在线预览，可打印保存",
                }
            )

        return results

    def search_multi_source(self, query: str) -> List[Dict]:
        """多源搜索"""
        all_links = []
        parsed = self.parse_standard_number(query)
        search_term = parsed["normalized"] or query
        search_term_encoded = search_term.replace(" ", "+")

        sources_list = [
            (sid, s) for sid, s in SEARCH_SOURCES.items() if "search_url" in s
        ]
        total_sources = len(sources_list)

        for idx, (source_id, source) in enumerate(sources_list):
            self._report(
                idx,
                total_sources,
                f"搜索 {source['name']}: {query}",
                {"source": source["name"], "source_id": source_id, "standard": query},
            )
            url = source["search_url"].format(query=search_term_encoded)
            all_links.append(
                {
                    "source": source_id,
                    "name": source["name"],
                    "url": url,
                    "type": source.get("format", "preview"),
                    "free": source.get("free", False),
                    "requires_login": source.get("requires_login", False),
                }
            )

        return all_links

    def generate_download_methods(self, info: StandardDownloadInfo) -> List[Dict]:
        """生成下载方法建议"""
        methods = []

        for link in info.download_links:
            if link.get("type") == "direct":
                methods.append(
                    {
                        "method": "直接下载",
                        "url": link["url"],
                        "source": link.get("name", link.get("source")),
                        "free": link.get("free", False),
                        "steps": ["点击下载链接", "保存 PDF"],
                    }
                )

        for link in info.download_links:
            if link.get("type") in ["preview", "search"]:
                methods.append(
                    {
                        "method": "预览 + 打印",
                        "url": link["url"],
                        "source": link.get("name", link.get("source")),
                        "free": link.get("free", False),
                        "steps": [
                            "打开链接",
                            "使用浏览器预览",
                            "Ctrl+P 打印",
                            '选择"另存为 PDF"',
                            "保存",
                        ],
                    }
                )

        methods.append(
            {
                "method": "浏览器扩展",
                "url": None,
                "source": "通用",
                "free": True,
                "steps": [
                    "安装 Print Friendly & PDF 扩展",
                    "打开标准预览页面",
                    "点击扩展图标",
                    "生成并下载 PDF",
                ],
            }
        )

        methods.append(
            {
                "method": "开发者工具",
                "url": None,
                "source": "通用",
                "free": True,
                "steps": [
                    "按 F12 打开开发者工具",
                    "切换到 Network 标签",
                    "刷新页面",
                    "筛选 PDF 或 document",
                    "右键复制链接",
                    "新标签页打开下载",
                ],
            }
        )

        return methods

    def search(self, query: str) -> List[Dict]:
        """搜索标准"""
        logger.info(f"搜索标准: {query}")

        parsed = self.parse_standard_number(query)
        results = []

        iso_links = self.search_iso(query)
        if iso_links:
            results.append(
                StandardDownloadInfo(
                    standard_number=parsed.get("normalized", query),
                    title=f"{parsed.get('normalized', query)} 标准",
                    year=parsed.get("year", "Latest"),
                    status="Published",
                    organization="ISO",
                    download_links=iso_links,
                    preview_links=[],
                    match_score=1.0,
                )
            )

        gb_links = self.search_gb(query)
        if gb_links:
            results.append(
                StandardDownloadInfo(
                    standard_number=parsed.get("normalized", query),
                    title=f"{parsed.get('normalized', query)} 中国国家标准",
                    year=parsed.get("year", "Latest"),
                    status="Published",
                    organization="GB",
                    download_links=gb_links,
                    preview_links=[],
                    match_score=1.0,
                )
            )

        multi_links = self.search_multi_source(query)
        if multi_links or not results:
            results.append(
                StandardDownloadInfo(
                    standard_number=parsed.get("normalized", query),
                    title=f"{parsed.get('normalized', query)} 多源搜索结果",
                    year=parsed.get("year", "Latest"),
                    status="Unknown",
                    organization=parsed.get("organization", "Unknown"),
                    download_links=multi_links,
                    preview_links=[
                        l["url"] for l in multi_links if l.get("type") == "preview"
                    ],
                    match_score=0.8,
                )
            )

        self.results = results
        return [asdict(r) for r in results]

    def get_download_links(self, standard_id: str) -> List[Dict]:
        """获取下载链接"""
        results = self.search(standard_id)
        all_links = []
        for r in results:
            all_links.extend(r.get("download_links", []))
        return all_links

    def get_sources(self) -> Dict:
        """获取可用的搜索源"""
        return {
            k: {"id": k, "name": v["name"], "free": v.get("free", False)}
            for k, v in SEARCH_SOURCES.items()
        }

    def download(self, standard_number: str, source: str = "auto") -> Dict:
        """下载标准（返回搜索链接）"""
        import time

        self._report(
            0, 100, f"解析标准号: {standard_number}", {"standard": standard_number}
        )

        parsed = self.parse_standard_number(standard_number)
        org = parsed.get("organization", "Unknown")

        self._report(
            10,
            100,
            f"识别为 {org} 标准",
            {"organization": org, "standard": standard_number},
        )

        search_results = self.search(standard_number)
        all_links = []
        for r in search_results:
            all_links.extend(r.get("download_links", []))

        self._report(
            100, 100, f"找到 {len(all_links)} 个下载源", {"links_count": len(all_links)}
        )

        return {
            "standard": standard_number,
            "success": True,
            "message": "请点击搜索链接查找并下载标准文档",
            "search_links": all_links,
            "file_path": None,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
