"""
标准查询服务 - 整合自 lab_new，使用 Playwright

修复说明:
1. 使用 Playwright 替代 Selenium，更轻量、更快、更稳定
2. 移除了所有模拟数据返回逻辑
3. 当查询失败时抛出异常而非返回假数据
4. 添加了详细日志便于调试
"""

import asyncio
import re
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
from bs4 import BeautifulSoup

# 导入标准号提取器
try:
    from extractor import StandardNumberExtractor

    EXTRACTOR_AVAILABLE = True
except ImportError:
    EXTRACTOR_AVAILABLE = False
    logger.warning("标准号提取器不可用，将使用原始输入作为标准号")

logger = logging.getLogger(__name__)


@dataclass
class StandardInfo:
    """标准信息"""

    standard_number: str
    chinese_name: str = ""
    english_name: str = ""
    standard_status: str = ""
    release_date: str = ""
    implementation_date: str = ""
    cancellation_date: str = ""
    replaced_standard: str = ""
    replacing_standard: str = ""
    adopt_standard: str = ""
    reference_standard: str = ""
    supplementary_revision: str = ""
    reference_basis: str = ""
    standard_summary: str = ""
    resource: str = ""


# 查询平台配置
QUERY_PLATFORMS = {
    "hunan": {
        "name": "湖南省标准信息公共服务平台",
        "url": "https://www.hnbzw.com/Standard/StdSearch.aspx",
        "input_selector": "#txtNo",
        "search_selector": "#ibtnSearch",
        "result_selector": ".lisyt-xq",
        "need_format": False,
        "type": "popup",
    },
    "shanxi": {
        "name": "陕西省标准信息公共服务平台",
        "url": "http://219.144.196.30/Standard/StdSearch.aspx",
        "input_selector": "#txtNo",
        "search_selector": "#ibtnSearch",
        "result_selector": "#dstStd tr:first-child th a",
        "need_format": False,
        "type": "popup",
    },
    "jiangxi": {
        "name": "江西省标准化信息服务平台",
        "url": "http://59.53.159.10:7003/STDL/STDL1.aspx",
        "input_selector": "#txtStdNO",
        "search_selector": "#ibtnSearch",
        "result_selector": ".link-xqa",
        "need_format": True,
        "type": "popup",
    },
    "liaocheng": {
        "name": "聊城市标准信息公共服务平台",
        "url": "http://www.lcbzpt.cn/StdSearch/stdSearchHome1.aspx?OperType=2&m=%u6807%u51C6&t=%u5168%u6587&v=",
        "input_selector": "#tnkey",
        "search_selector": "#stdsearchadvanced",
        "result_selector": '#stdList a[href*="stdDetail"]',
        "need_format": True,
        "type": "link",
    },
    "liuan": {
        "name": "六安市标准信息平台",
        "url": "http://www.labzh.org.cn/StdSearch/stdSearchHome1.aspx?OperType=2&m=%u6807%u51C6&t=%u5168%u6587&v=",
        "input_selector": "#tnkey",
        "search_selector": "#stdsearchadvanced",
        "result_selector": '#stdList a[href*="stdDetail"]',
        "need_format": True,
        "type": "link",
    },
    "xiamen": {
        "name": "厦门市标准信息公共服务平台",
        "url": "http://bz.xmis.org.cn/StdSearch/stdSearchHome1.aspx?OperType=2&m=%u6807%u51C6&t=%u9898%u5F55%u4FE1%u606F&v=&b=&bn=",
        "input_selector": "#tnkey",
        "search_selector": "#stdsearchadvanced",
        "result_selector": '#stdList a[href*="stdDetail"]',
        "need_format": True,
        "type": "link",
    },
    "shenzhen": {
        "name": "深圳市标准信息服务平台",
        "url": "http://standard.sist.org.cn/StdSearch/stdSearchHome1.aspx?OperType=2&m=%u6807%u51C6&t=%u9898%u5F55%u4FE1%u606F&v=",
        "input_selector": "#tnkey",
        "search_selector": "#stdsearchadvanced",
        "result_selector": '#stdList a[href*="stdDetail"]',
        "need_format": True,
        "type": "link",
    },
}


def format_standard_code(code: str) -> str:
    """
    格式化标准号 - 与原始脚本保持一致

    使用正则表达式提取字母、数字和带点的数字
    去除单字母的部分，用空格连接

    示例:
        输入: "GB/T19001-2016"
        输出: "GB 19001 2016"
    """
    parts = re.findall(r"[a-zA-Z]+|\d+(?:\.\d+)?", code)
    parts = [i for i in parts if len(i) > 1]
    formatted_code = " ".join(parts)
    return formatted_code


def check_playwright_available() -> tuple:
    """检查Playwright是否可用"""
    try:
        from playwright.async_api import async_playwright

        return True, "Playwright可用"
    except ImportError:
        return (
            False,
            "Playwright未安装，请运行: pip install playwright && playwright install chromium",
        )


class StandardQueryService:
    """标准查询服务 - Playwright实现"""

    def __init__(self, progress_callback=None):
        self._browser = None
        self._context = None
        self._playwright = None
        self.progress_callback = progress_callback

    def _report(self, current: int, total: int, message: str, details: dict = None):
        if self.progress_callback:
            self.progress_callback(current, total, message, details)

    async def _init_browser(self):
        """初始化浏览器"""
        if self._browser is not None and self._context is not None:
            return

        from playwright.async_api import async_playwright

        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--proxy-server=direct://",
                ],
            )
            self._context = await self._browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            logger.info("浏览器初始化成功")
        except Exception as e:
            logger.error(f"浏览器初始化失败: {e}")
            raise RuntimeError(f"无法初始化Playwright浏览器: {e}")

    async def close(self):
        """关闭浏览器"""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._browser = None
        self._context = None
        self._playwright = None

    async def query_single(
        self, standard_number: str, platform: str = "hunan", auto_switch: bool = True
    ) -> Dict:
        """查询单个标准，支持源切换"""
        tried_platforms = []
        current_platform = platform

        while True:
            try:
                await self._init_browser()

                if current_platform not in QUERY_PLATFORMS:
                    raise ValueError(
                        f"未知平台: {current_platform}\n"
                        f"可用平台: {', '.join(QUERY_PLATFORMS.keys())}"
                    )

                config = QUERY_PLATFORMS[current_platform]

                if config.get("need_format", False):
                    formatted_number = format_standard_code(standard_number)
                    logger.info(
                        f"平台 {current_platform} 需要格式化: {standard_number} -> {formatted_number}"
                    )
                    input_number = formatted_number
                else:
                    input_number = standard_number

                page = await self._context.new_page()

                try:
                    self._report(
                        20,
                        100,
                        f"访问 {config['name']}...",
                        {"platform": current_platform, "standard": standard_number},
                    )

                    await page.goto(
                        config["url"], wait_until="networkidle", timeout=30000
                    )

                    self._report(
                        40,
                        100,
                        f"正在搜索 {standard_number}...",
                        {"platform": current_platform, "standard": standard_number},
                    )

                    await page.fill(config["input_selector"], input_number)

                    if config.get("type") == "link":
                        try:
                            await page.click("#iscontain", timeout=3000)
                        except:
                            pass

                    await page.click(config["search_selector"])

                    await page.wait_for_selector(
                        config["result_selector"], timeout=15000
                    )

                    self._report(
                        60,
                        100,
                        f"找到结果，进入详情页...",
                        {"platform": current_platform, "standard": standard_number},
                    )

                    if config.get("type") == "link":
                        link_element = await page.query_selector(
                            config["result_selector"]
                        )
                        if not link_element:
                            raise RuntimeError("未找到结果链接")
                        href = await link_element.get_attribute("href")
                        if not href:
                            raise RuntimeError("结果链接无href")
                        if href.startswith("/"):
                            from urllib.parse import urlparse

                            parsed = urlparse(config["url"])
                            href = f"{parsed.scheme}://{parsed.netloc}{href}"
                        detail_page = await self._context.new_page()
                        await detail_page.goto(
                            href, wait_until="networkidle", timeout=30000
                        )
                    else:
                        async with self._context.expect_page() as page_info:
                            await page.click(config["result_selector"])
                        detail_page = await page_info.value
                        await detail_page.wait_for_load_state("networkidle")

                    self._report(
                        80,
                        100,
                        f"提取标准信息...",
                        {"platform": current_platform, "standard": standard_number},
                    )

                    if config.get("type") == "link":
                        info = await self._extract_standard_info_link(
                            detail_page, config["name"]
                        )
                    else:
                        info = await self._extract_standard_info(
                            detail_page, config["name"]
                        )

                    await detail_page.close()
                    await page.close()

                    self._report(
                        100,
                        100,
                        f"查询完成",
                        {"platform": current_platform, "standard": standard_number},
                    )

                    return self._to_dict(info)

                except Exception as e:
                    logger.error(
                        f"查询失败 {standard_number} @ {current_platform}: {e}"
                    )
                    await page.close()

                    if auto_switch:
                        tried_platforms.append(current_platform)
                        available = [
                            p
                            for p in QUERY_PLATFORMS.keys()
                            if p not in tried_platforms
                        ]
                        if available:
                            logger.warning(
                                f"源 {current_platform} 失败，切换到 {available[0]}"
                            )
                            self._report(
                                0,
                                100,
                                f"源 {config['name']} 连接失败，切换到 {QUERY_PLATFORMS[available[0]]['name']}...",
                                {
                                    "platform": current_platform,
                                    "next_platform": available[0],
                                    "standard": standard_number,
                                },
                            )
                            current_platform = available[0]
                            continue
                    raise RuntimeError(f"查询标准 {standard_number} 失败: {e}")

            except Exception as e:
                if "未知平台" in str(e) or not auto_switch:
                    raise
                tried_platforms.append(current_platform)
                available = [
                    p for p in QUERY_PLATFORMS.keys() if p not in tried_platforms
                ]
                if not available:
                    logger.error(f"所有源都无法连接: {standard_number}")
                    raise RuntimeError(
                        f"所有查询源都无法连接，查询 {standard_number} 失败"
                    )
                current_platform = available[0]
                continue

    async def _extract_standard_info(self, page, source: str) -> StandardInfo:
        """提取标准详情"""
        field_ids = [
            ("lblStdNo", "standard_number"),
            ("lblCnName", "chinese_name"),
            ("lblEnName", "english_name"),
            ("lblState", "standard_status"),
            ("lblPubDate", "release_date"),
            ("lblActDate", "implementation_date"),
            ("lblEndDate", "cancellation_date"),
            ("lblRep", "replaced_standard"),
            ("lblRepNo", "replacing_standard"),
            ("lblStd", "adopt_standard"),
            ("lblExp", "reference_standard"),
            ("lblStdOrigin", "reference_basis"),
        ]

        data = {"resource": source}

        for elem_id, field_name in field_ids:
            try:
                elem = await page.query_selector(f"#{elem_id}")
                if elem:
                    data[field_name] = await elem.inner_text()
                else:
                    data[field_name] = ""
            except:
                data[field_name] = ""

        return StandardInfo(**data)

    async def _extract_standard_info_link(self, page, source: str) -> StandardInfo:
        """提取标准详情 - link类型平台"""
        field_ids = [
            ("a100", "standard_number"),
            ("a298", "chinese_name"),
            ("a302", "english_name"),
            ("a200", "standard_status"),
            ("a101", "release_date"),
            ("a205", "implementation_date"),
            ("a206", "cancellation_date"),
            ("a462List", "replaced_standard"),
            ("a462List1", "replacing_standard"),
            ("a800List1", "adopt_standard"),
            ("a502List", "reference_standard"),
            ("a823List", "supplementary_revision"),
            ("a842List", "reference_basis"),
            ("remark", "standard_summary"),
        ]

        data = {"resource": source}

        for elem_id, field_name in field_ids:
            try:
                elem = await page.query_selector(f"#{elem_id}")
                if elem:
                    data[field_name] = await elem.inner_text()
                else:
                    data[field_name] = ""
            except:
                data[field_name] = ""

        return StandardInfo(**data)

    def _to_dict(self, info: StandardInfo) -> Dict:
        """转换为字典"""
        return asdict(info)

    async def query_batch(
        self, standards: List[str], platform: str = "hunan"
    ) -> List[Dict]:
        """批量查询标准"""
        results = []
        total = len(standards)

        for i, std in enumerate(standards):
            self._report(
                int((i / total) * 100),
                100,
                f"正在查询 {std} ({i + 1}/{total})",
                {"current": std, "index": i + 1, "total": total},
            )

            try:
                result = await self.query_single(std, platform)
                results.append(result)
            except Exception as e:
                logger.error(f"批量查询中 {std} 失败: {e}")
                results.append(
                    {
                        "standard_number": std,
                        "error": str(e),
                        "resource": f"{platform} (查询失败)",
                    }
                )
            await asyncio.sleep(0.5)

        self._report(100, 100, f"批量查询完成", {"total": total})
        return results

    def get_platforms(self) -> Dict:
        """获取可用的查询平台"""
        return {
            k: {"id": k, "name": v["name"], "url": v["url"]}
            for k, v in QUERY_PLATFORMS.items()
        }

    async def fuzzy_search(
        self, keyword: str, platform: str = "hunan", limit: int = 20
    ) -> List[Dict]:
        """模糊搜索标准"""
        import requests

        results = []

        search_urls = [
            f"https://www.hnbzw.com/Standard/StdSearch.aspx?keyword={keyword}",
            f"http://bz.foodmate.net/search?keyword={keyword}&type=std",
            f"http://openstd.samr.gov.cn/bzgk/gb/search?keyword={keyword}",
        ]

        for url in search_urls:
            try:
                response = requests.get(
                    url,
                    timeout=15,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    },
                )

                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, "html.parser")

                    for link in soup.find_all("a", href=True):
                        href = link.get("href", "")
                        text = link.get_text(strip=True)

                        if re.search(
                            r"(GB|T|JJ|GJ|DB|Q|HB|SN|T\/|ISO|IEC|ASTM)\s*[\d\/\-\.]+",
                            text,
                            re.I,
                        ):
                            std_num = re.search(
                                r"((?:GB|T|JJ|GJ|DB|Q|HB|SN|T\/|ISO|IEC|ASTM)[\s\-]?[\d\/\-\.]+)",
                                text,
                                re.I,
                            )
                            if std_num:
                                std_num = std_num.group(1).strip()
                                if not any(
                                    r.get("standard_number") == std_num for r in results
                                ):
                                    results.append(
                                        {
                                            "standard_number": std_num,
                                            "chinese_name": text,
                                            "standard_status": "未知",
                                            "resource": "模糊搜索",
                                            "match_type": "keyword",
                                        }
                                    )
                                    if len(results) >= limit:
                                        break

                    for tr in soup.find_all("tr"):
                        cells = tr.find_all(["td", "th"])
                        if len(cells) >= 2:
                            for cell in cells:
                                text = cell.get_text(strip=True)
                                std_match = re.search(
                                    r"((?:GB|T|JJ|GJ|DB|Q|HB|SN|T\/|ISO|IEC|ASTM)[\s\-]?[\d\/\-\.]+)",
                                    text,
                                    re.I,
                                )
                                if std_match:
                                    std_num = std_match.group(1).strip()
                                    if not any(
                                        r.get("standard_number") == std_num
                                        for r in results
                                    ):
                                        name = ""
                                        for c in cells:
                                            ctext = c.get_text(strip=True)
                                            if (
                                                ctext
                                                and ctext != std_num
                                                and len(ctext) > 2
                                            ):
                                                name = ctext
                                                break
                                        results.append(
                                            {
                                                "standard_number": std_num,
                                                "chinese_name": name,
                                                "standard_status": "未知",
                                                "resource": "模糊搜索",
                                                "match_type": "keyword",
                                            }
                                        )
                                        if len(results) >= limit:
                                            break

                    if len(results) >= limit:
                        break

            except Exception as e:
                logger.warning(f"模糊搜索 {url} 失败: {e}")
                continue

        valid_results = []
        for r in results:
            std_num = r.get("standard_number", "")
            if re.match(
                r"^(GB|T|JJ|GJ|DB|Q|HB|SN|CNCA|RB|JJF|HG|JB)\s*[/]?\s*\d+[\.\d]*[-\s]\d{4}",
                std_num,
                re.I,
            ):
                valid_results.append(r)
        results = valid_results

        return results[:limit]


def query_standard(
    standard_number: str, platform: str = "hunan", progress_callback=None
) -> Dict:
    """
    查询单个标准（便捷函数）

    Args:
        standard_number: 标准号
        platform: 查询平台
        progress_callback: 进度回调函数

    Returns:
        标准信息字典
    """
    service = StandardQueryService(progress_callback=progress_callback)
    try:
        import asyncio

        return asyncio.run(service.query_single(standard_number, platform))
    finally:
        import asyncio

        asyncio.run(service.close())


def query_standards(
    standards: List[str], platform: str = "hunan", progress_callback=None
) -> List[Dict]:
    """
    批量查询标准（便捷函数）

    Args:
        standards: 标准号列表
        platform: 查询平台
        progress_callback: 进度回调函数

    Returns:
        标准信息字典列表
    """
    service = StandardQueryService(progress_callback=progress_callback)
    try:
        import asyncio

        return asyncio.run(service.query_batch(standards, platform))
    finally:
        import asyncio

        asyncio.run(service.close())


if __name__ == "__main__":
    import asyncio

    async def test():
        service = StandardQueryService()
        try:
            result = await service.query_single("GB/T 19001-2016", "hunan")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            await service.close()

    asyncio.run(test())
