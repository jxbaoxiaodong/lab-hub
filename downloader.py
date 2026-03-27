"""
标准下载服务 - 整合自 lab_new 的 standard_auto_downloader.py
支持实际 PDF 下载，不只是返回搜索链接
"""

import asyncio
import re
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
from urllib.parse import quote
import time

logger = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    """下载结果"""

    standard_number: str
    success: bool = False
    file_path: str = None
    download_url: str = None
    source: str = None
    message: str = ""
    file_size: int = 0


class StandardAutoDownloader:
    """标准自动下载器 - 使用 Playwright 进行浏览器自动化"""

    def __init__(self, download_dir: str = None, progress_callback=None):
        """
        初始化下载器

        Args:
            download_dir: 下载目录
            progress_callback: 进度回调函数
        """
        self.download_dir = (
            Path(download_dir) if download_dir else Path.home() / "standard_downloads"
        )
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.progress_callback = progress_callback

        self._browser = None
        self._context = None
        self._playwright = None

    async def close(self):
        """关闭浏览器"""
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def _init_browser(self, headless: bool = True):
        """初始化 Playwright 浏览器"""
        if self._browser is not None and self._context is not None:
            return

        from playwright.async_api import async_playwright

        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=headless,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-web-security",
                    "--disable-features=IsolateOrigins,site-per-process",
                ],
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
        except Exception as e:
            logger.error(f"初始化浏览器失败: {e}")
            raise

    def _report(self, current: int, total: int, message: str, details: dict = None):
        """报告进度"""
        if self.progress_callback:
            self.progress_callback(current, total, message, details)

    def parse_standard_number(self, standard_number: str) -> Dict:
        """解析标准号"""
        result = {
            "original": standard_number,
            "prefix": None,
            "type": None,
            "number": None,
            "year": None,
            "normalized": None,
        }

        patterns = [
            (r"(GB/T|GB|GBT)\s*(\d+(?:\.\d+)?)\s*[-—:]\s*(\d{4})", "GB"),
            (r"(JJG|JJF)\s*(\d+(?:\.\d+)?)\s*[-—:]\s*(\d{4})", "JJG"),
            (
                r"(HG|T|JB/T|JB|SJ/T|SJ|YB/T|YB|TB/T|TB|DL/T|DL|JGJ|JG/T|JG|QB/T|QB|NY/T|NY|SC/T|SC|YY/T|YY|JC/T|JC|MT/T|MT|SL/T|SL|CJ/T|CJ|GA/T|GA|LY/T|LY|HY/T|HY|HS/T|HS)\s*(\d+(?:\.\d+)?)\s*[-—:]\s*(\d{4})",
                "INDUSTRY",
            ),
            (r"(DB\d+/T|DB/T|DB)\s*(\d+(?:\.\d+)?)\s*[-—:]\s*(\d{4})", "DB"),
            (
                r"(ISO|IEC|ASTM|EN|BS|DIN|JIS|ANSI|IEEE|API|ASME)\s*(\d+(?:\.\d+)?)\s*[-—:]\s*(\d{4})",
                "INTERNATIONAL",
            ),
        ]

        for pattern, std_type in patterns:
            match = re.search(pattern, standard_number, re.IGNORECASE)
            if match:
                result["prefix"] = match.group(1).upper().replace(" ", "")
                result["type"] = std_type
                result["number"] = match.group(2)
                if len(match.groups()) > 2 and match.group(3):
                    result["year"] = match.group(3)
                break

        if not result["type"]:
            simple_match = re.search(r"([A-Za-z]+)[/T]?\s*(\d+)", standard_number)
            if simple_match:
                result["prefix"] = simple_match.group(1).upper()
                result["type"] = simple_match.group(1).upper()
                result["number"] = simple_match.group(2)

        if result["prefix"] and result["number"]:
            if result["year"]:
                result["normalized"] = (
                    f"{result['prefix']} {result['number']}-{result['year']}"
                )
            else:
                result["normalized"] = f"{result['prefix']} {result['number']}"

        return result

    async def download_from_foodmate(self, standard_number: str) -> DownloadResult:
        """从食品伙伴网下载"""
        await self._init_browser(headless=True)
        page = await self._context.new_page()

        try:
            parsed = self.parse_standard_number(standard_number)
            search_term = parsed["normalized"] or standard_number
            search_term = search_term.replace(" ", "+").replace("/", "%2F")

            search_url = (
                f"https://down.foodmate.net/standard/search.php?kw={search_term}"
            )
            logger.info(f"[食品伙伴网] 正在搜索: {search_url}")
            self._report(
                10,
                100,
                f"正在搜索 {standard_number}",
                {"source": "食品伙伴网", "standard": standard_number},
            )

            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            # 尝试多种选择器来查找结果
            # 注意：必须匹配类似 /standard/sort/3/94315.html 的详情页链接，而不是 /standard/sort/1/ 这样的分类链接
            result_link = None
            used_selector = None
            selectors = [
                '.list.flck a[href*="/standard/sort/"][href$=".html"]',
                'a[href*="/standard/sort/"][href$=".html"]',
            ]
            for selector in selectors:
                links = await page.query_selector_all(selector)
                for link in links:
                    href = await link.get_attribute("href")
                    # 确保是详情页链接（包含数字ID）而不是分类链接
                    if href and re.search(r"/standard/sort/\d+/\d+\.html$", href):
                        result_link = link
                        used_selector = selector
                        logger.info(
                            f"[食品伙伴网] 使用选择器: {selector}, 链接: {href}"
                        )
                        break
                if result_link:
                    break
            if not result_link:
                return DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source="食品伙伴网",
                    message="未找到相关文档",
                )

            href = await result_link.get_attribute("href")
            logger.info(f"[食品伙伴网] 找到详情页: {href}")
            self._report(30, 100, "进入详情页", {"standard": standard_number})

            await page.goto(href, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(1)

            # 从详情页提取实际标准号
            actual_standard = standard_number  # 默认使用传入的标准号
            try:
                # 尝试从页面标题或内容中提取标准号
                page_content = await page.content()

                # 查找类似 GB/T 19001-2016 或 GB 6040-2002 的格式
                std_patterns = [
                    r"GB/T?\s*\d+[\.\-]\d+[\.\-]?\d*",  # GB/T 19001-2016
                    r"GB\s*\d+[\.\-]\d+[\.\-]?\d*",  # GB 6040-2002
                    r"标准号[:：]\s*([A-Z0-9/\.\-\s]+)",  # 标准号: GB/T 19001-2016
                    r"标准编号[:：]\s*([A-Z0-9/\.\-\s]+)",  # 标准编号: GB/T 19001-2016
                ]

                for pattern in std_patterns:
                    matches = re.findall(pattern, page_content, re.IGNORECASE)
                    if matches:
                        actual_standard = matches[0].strip()
                        logger.info(f"[食品伙伴网] 提取到实际标准号: {actual_standard}")
                        break
            except Exception as e:
                logger.warning(f"[食品伙伴网] 提取标准号失败: {e}")

            download_link = await page.query_selector('a[href*="down.php"]')
            if not download_link:
                return DownloadResult(
                    standard_number=actual_standard,
                    success=False,
                    source="食品伙伴网",
                    message="未找到下载链接",
                )

            logger.info("[食品伙伴网] 点击下载...")
            self._report(60, 100, "正在下载", {"standard": standard_number})

            async with page.expect_download(timeout=60000) as download_info:
                await download_link.click()

            download = await download_info.value
            safe_name = actual_standard.replace("/", "_").replace(" ", "_")
            pdf_path = self.download_dir / f"{safe_name}.pdf"
            await download.save_as(pdf_path)

            file_size = pdf_path.stat().st_size if pdf_path.exists() else 0
            logger.info(f"[食品伙伴网] 下载完成: {pdf_path}, 大小: {file_size}")

            self._report(
                100,
                100,
                "下载完成",
                {"standard": actual_standard, "file_size": file_size},
            )

            return DownloadResult(
                standard_number=actual_standard,
                success=True,
                file_path=str(pdf_path),
                download_url=f"/api/standards/library/{pdf_path.name}",
                source="食品伙伴网",
                message=f"下载成功，文件大小: {file_size / 1024:.1f}KB",
                file_size=file_size,
            )

        except Exception as e:
            logger.error(f"食品伙伴网下载失败: {e}")
            return DownloadResult(
                standard_number=standard_number,
                success=False,
                source="食品伙伴网",
                message=f"下载出错: {str(e)}",
            )
        finally:
            await page.close()

    async def download_from_gb_openstd(self, standard_number: str) -> DownloadResult:
        """从国家标准全文公开系统获取在线查看链接"""
        await self._init_browser(headless=True)
        page = await self._context.new_page()

        try:
            parsed = self.parse_standard_number(standard_number)
            search_term = parsed["normalized"] or standard_number
            search_term = search_term.replace(" ", "+").replace("/", "%2F")

            search_url = f"https://openstd.samr.gov.cn/bzgk/gb/std_list?keyword={search_term}&page=1"
            logger.info(f"[国家标准] 正在搜索: {search_url}")
            self._report(
                10,
                100,
                f"正在搜索 {standard_number}",
                {"source": "国家标准全文公开系统", "standard": standard_number},
            )

            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            html = await page.content()

            hcnos = re.findall(r"showInfo\('([A-Z0-9]+)'\)", html)
            hcnos = list(dict.fromkeys(hcnos))

            if not hcnos:
                return DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source="国家标准全文公开系统",
                    message="未找到相关标准",
                )

            hcno = hcnos[0]
            view_url = f"http://c.gb688.cn/bzgk/gb/showGb?type=online&hcno={hcno}"

            # 从搜索结果页面提取实际标准号
            actual_standard = standard_number
            try:
                # 优先从页面标题中提取
                page_title = await page.title()
                title_match = re.search(
                    r"(GB/T?\s*\d+[\.\-]?\d*[\.\-]?\d*)", page_title, re.IGNORECASE
                )
                if title_match:
                    actual_standard = title_match.group(1).strip()
                    logger.info(f"[国家标准] 从标题提取到实际标准号: {actual_standard}")
                else:
                    # 从搜索结果表格中提取 - 查找TD中的标准号格式
                    std_patterns = [
                        r">\s*(GB/T\s*\d+[\.\-]?\d*[\.\-]?\d*)\s*<",  # GB/T 19001-2016
                        r">\s*(GB\s+\d+[\.\-]?\d*[\.\-]?\d*)\s*<",  # GB 6040-2002
                    ]

                    for pattern in std_patterns:
                        matches = re.findall(pattern, html, re.IGNORECASE)
                        if matches:
                            actual_standard = matches[0].strip()
                            logger.info(
                                f"[国家标准] 从表格提取到实际标准号: {actual_standard}"
                            )
                            break
            except Exception as e:
                logger.warning(f"[国家标准] 提取标准号失败: {e}")

            self._report(100, 100, "获取链接成功", {"standard": actual_standard})

            return DownloadResult(
                standard_number=actual_standard,
                success=True,
                download_url=view_url,
                source="国家标准全文公开系统",
                message=f"在线预览链接",
            )

        except Exception as e:
            logger.error(f"国家标准获取链接失败: {e}")
            return DownloadResult(
                standard_number=standard_number,
                success=False,
                source="国家标准全文公开系统",
                message=f"获取链接出错: {str(e)}",
            )
        finally:
            await page.close()

    async def download_from_gbt(self, standard_number: str) -> DownloadResult:
        """从GBT标准网获取在线观看链接（夸克网盘）"""
        await self._init_browser(headless=True)
        page = await self._context.new_page()

        try:
            parsed = self.parse_standard_number(standard_number)
            search_term = parsed["normalized"] or standard_number
            search_term = search_term.replace(" ", "+").replace("/", "%2F")

            search_url = f"https://gbt.org.cn/search.php?q={search_term}"
            logger.info(f"[GBT标准网] 正在搜索: {search_url}")
            self._report(
                10,
                100,
                f"正在搜索 {standard_number}",
                {"source": "GBT标准网", "standard": standard_number},
            )

            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            # 从搜索结果页面提取实际标准号
            actual_standard = standard_number
            try:
                # 优先从页面标题中提取
                page_title = await page.title()
                title_match = re.search(
                    r"(GB/T?\s*\d+[\.\-]?\d*[\.\-]?\d*)", page_title, re.IGNORECASE
                )
                if title_match:
                    actual_standard = title_match.group(1).strip()
                    logger.info(
                        f"[GBT标准网] 从标题提取到实际标准号: {actual_standard}"
                    )
                else:
                    # 从搜索结果中提取标准号
                    page_content = await page.content()

                    # 查找类似 GB/T 19001-2016 或 GB 6040-2002 的格式
                    std_patterns = [
                        r"GB/T?\s*\d+[\.\-]\d+[\.\-]?\d*",  # GB/T 19001-2016
                        r"GB\s*\d+[\.\-]\d+[\.\-]?\d*",  # GB 6040-2002
                        r"标准号[:：]\s*([A-Z0-9/\.\-\s]+)",  # 标准号: GB/T 19001-2016
                    ]

                    for pattern in std_patterns:
                        matches = re.findall(pattern, page_content, re.IGNORECASE)
                        if matches:
                            actual_standard = matches[0].strip()
                            logger.info(
                                f"[GBT标准网] 提取到实际标准号: {actual_standard}"
                            )
                            break
            except Exception as e:
                logger.warning(f"[GBT标准网] 提取标准号失败: {e}")

            # 查找所有搜索结果
            result_links = await page.query_selector_all(
                '.applist .box h3 a[href$=".html"]'
            )
            if not result_links:
                # 备用选择器
                result_links = await page.query_selector_all(
                    '.applist .box a[href$=".html"]'
                )
            if not result_links:
                return DownloadResult(
                    standard_number=actual_standard,
                    success=False,
                    source="GBT标准网",
                    message="未找到相关标准",
                )

            # 先收集所有链接信息（避免页面导航后handle失效）
            links_info = []
            for link in result_links:
                href = await link.get_attribute("href")
                link_text = await link.inner_text()
                if link_text and link_text.strip() and href:
                    links_info.append((href, link_text.strip()))

            logger.info(f"[GBT标准网] 找到 {len(links_info)} 个有效结果")

            # 遍历所有搜索结果，查找有夸克网盘链接的
            for idx, (href, link_text) in enumerate(links_info):
                logger.info(f"[GBT标准网] 检查第{idx + 1}个结果: {link_text} -> {href}")
                self._report(
                    20 + idx * 10,
                    100,
                    f"检查: {link_text[:30]}",
                    {"standard": actual_standard},
                )

                # 访问详情页
                try:
                    await page.goto(href, wait_until="networkidle", timeout=20000)
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.warning(f"[GBT标准网] 访问详情页失败: {e}")
                    continue

                # 查找夸克网盘链接
                quark_link = await page.query_selector('a[href*="pan.quark.cn"]')
                if quark_link:
                    quark_url = await quark_link.get_attribute("href")
                    logger.info(f"[GBT标准网] 找到夸克网盘链接: {quark_url}")
                    self._report(
                        50, 100, "获取在线观看链接", {"standard": actual_standard}
                    )

                    # 访问夸克链接获取在线观看地址
                    share_id = quark_url.split("/")[-1].split("#")[0]
                    try:
                        await page.goto(
                            quark_url, wait_until="networkidle", timeout=20000
                        )
                        await asyncio.sleep(3)
                    except Exception as e:
                        logger.warning(f"[GBT标准网] 访问夸克链接失败: {e}")
                        # 即使无法访问夸克链接，也返回网盘链接
                        return DownloadResult(
                            standard_number=actual_standard,
                            success=True,
                            download_url=quark_url,
                            source="GBT标准网",
                            message=f"夸克网盘链接: {quark_url}",
                        )

                    file_item = await page.query_selector("[data-row-key]")
                    if file_item:
                        file_id = await file_item.get_attribute("data-row-key")
                        if file_id and len(file_id) == 32:
                            view_url = f"https://pan.quark.cn/s/{share_id}#/share/docpdf/{file_id}"
                            logger.info(f"[GBT标准网] 在线观看链接: {view_url}")
                            self._report(
                                100, 100, "获取链接成功", {"standard": actual_standard}
                            )

                            return DownloadResult(
                                standard_number=actual_standard,
                                success=True,
                                download_url=view_url,
                                source="GBT标准网",
                                message=f"在线观看链接: {view_url}",
                            )

                    # 有夸克链接但无法获取文件ID，返回网盘链接
                    return DownloadResult(
                        standard_number=actual_standard,
                        success=True,
                        download_url=quark_url,
                        source="GBT标准网",
                        message=f"夸克网盘链接: {quark_url}",
                    )

                # 查找百度网盘链接
                baidu_link = await page.query_selector('a[href*="pan.baidu.com"]')
                if baidu_link:
                    baidu_url = await baidu_link.get_attribute("href")
                    logger.info(f"[GBT标准网] 找到百度网盘链接: {baidu_url}")
                    self._report(
                        50, 100, "获取百度网盘链接", {"standard": actual_standard}
                    )
                    return DownloadResult(
                        standard_number=actual_standard,
                        success=True,
                        download_url=baidu_url,
                        source="GBT标准网",
                        message=f"百度网盘链接: {baidu_url}",
                    )

                # 检查是否链接到国家标准网站
                openstd_link = await page.query_selector(
                    'a[href*="openstd.samr.gov.cn"]'
                )
                if openstd_link:
                    logger.info(
                        f"[GBT标准网] 第{idx + 1}个结果链接到国家标准网站，获取在线阅读链接"
                    )
                    # 获取hcno并构造在线阅读链接
                    openstd_url = await openstd_link.get_attribute("href")
                    if openstd_url:
                        # 从URL中提取hcno参数
                        hcno_match = re.search(r"hcno=([A-Z0-9]+)", openstd_url)
                        if hcno_match:
                            hcno = hcno_match.group(1)
                            view_url = f"http://c.gb688.cn/bzgk/gb/showGb?type=online&hcno={hcno}"
                            logger.info(f"[GBT标准网] 获取到在线阅读链接: {view_url}")
                            self._report(
                                100,
                                100,
                                "获取在线阅读链接成功",
                                {"standard": actual_standard},
                            )
                            return DownloadResult(
                                standard_number=actual_standard,
                                success=True,
                                download_url=view_url,
                                source="GBT标准网",
                                message=f"在线阅读链接: {view_url}",
                            )
                        logger.warning(f"[GBT标准网] 无法从URL提取hcno: {openstd_url}")

                # 没有网盘链接也没有国家标准链接
                logger.info(f"[GBT标准网] 第{idx + 1}个结果无下载链接，继续查找")

            # 遍历完所有结果都没有找到
            return DownloadResult(
                standard_number=actual_standard,
                success=False,
                source="GBT标准网",
                message="搜索结果中未找到可下载的标准",
            )

        except Exception as e:
            logger.error(f"GBT标准网获取链接失败: {e}")
            return DownloadResult(
                standard_number=actual_standard,
                success=False,
                source="GBT标准网",
                message=f"获取链接出错: {str(e)}",
            )
        finally:
            await page.close()

    async def download(
        self, standard_number: str, sources: List[str] = None, headless: bool = True
    ) -> List[DownloadResult]:
        """
        自动下载标准文档

        Args:
            standard_number: 标准号
            sources: 指定下载源列表，None则自动选择
            headless: 是否无头模式

        Returns:
            下载结果列表
        """
        results = []

        # 解析标准号
        parsed = self.parse_standard_number(standard_number)
        logger.info(f"解析标准号: {parsed}")

        # 确定下载源顺序 - 食品伙伴网优先，GBT标准网作为备选
        if sources:
            source_list = sources
        else:
            source_list = ["foodmate", "gbt", "gb_openstd"]

        logger.info(f"准备从以下源下载 {standard_number}: {source_list}")

        source_methods = {
            "foodmate": self.download_from_foodmate,
            "gbt": self.download_from_gbt,
            "gb_openstd": self.download_from_gb_openstd,
        }

        for source_id in source_list:
            try:
                method = source_methods.get(source_id)

                if method:
                    result = await method(standard_number)
                else:
                    # 未知源
                    result = DownloadResult(
                        standard_number=standard_number,
                        success=False,
                        source=source_id,
                        message="暂不支持该下载源",
                    )

                results.append(result)

                # 如果下载成功，不需要尝试其他源
                if result.success:
                    logger.info(f"下载成功: {result.source}")
                    break

            except Exception as e:
                logger.error(f"下载源 {source_id} 异常: {e}")
                results.append(
                    DownloadResult(
                        standard_number=standard_number,
                        success=False,
                        source=source_id,
                        message=f"异常: {str(e)}",
                    )
                )

        return results


# 保持与旧版接口兼容的类
class StandardDownloader(StandardAutoDownloader):
    """兼容旧版接口的标准下载器"""

    def download(self, standard_number: str, source: str = "auto") -> Dict:
        """
        下载标准 - 返回所有平台的查询结果列表

        Args:
            standard_number: 标准号
            source: 下载源（自动选择）

        Returns:
            字典格式，包含 results 列表，每个元素包含：
            - platform: 平台名称
            - status: success/error
            - file_path: 本地文件路径（食品伙伴网下载成功时）
            - view_url: 在线查看/下载链接
            - message: 详细信息
        """
        import asyncio

        self._report(
            0, 100, f"开始下载: {standard_number}", {"standard": standard_number}
        )

        try:
            results = asyncio.run(self._download_all_platforms(standard_number))

            success_count = sum(1 for r in results if r.get("status") == "success")
            self._report(
                100,
                100,
                f"完成: {success_count}/{len(results)} 个平台可用",
                {"standard": standard_number},
            )

            return {
                "standard": standard_number,
                "success": success_count > 0,
                "results": results,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            }

        except Exception as e:
            logger.error(f"下载失败: {e}")
            self._report(100, 100, f"下载失败: {str(e)}", {"standard": standard_number})
            return {
                "standard": standard_number,
                "success": False,
                "results": [
                    {
                        "platform": "系统",
                        "status": "error",
                        "message": f"下载出错: {str(e)}",
                    }
                ],
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        finally:
            try:
                import asyncio

                async def close_with_timeout():
                    try:
                        await asyncio.wait_for(self.close(), timeout=5.0)
                    except asyncio.TimeoutError:
                        pass

                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop and loop.is_running():
                    asyncio.create_task(close_with_timeout())
                else:
                    asyncio.run(close_with_timeout())
            except Exception:
                pass

    async def _download_all_platforms(self, standard_number: str) -> List[Dict]:
        """尝试所有平台，返回结果列表"""
        results = []

        platforms = [
            ("foodmate", "食品伙伴网", self.download_from_foodmate),
            ("gb_openstd", "国家标准全文公开系统", self.download_from_gb_openstd),
            ("gbt", "GBT标准网", self.download_from_gbt),
        ]

        for platform_id, platform_name, method in platforms:
            try:
                self._report(
                    10,
                    100,
                    f"尝试 {platform_name}...",
                    {"source": platform_name, "standard": standard_number},
                )

                result = await method(standard_number)

                if result.success:
                    results.append(
                        {
                            "platform": platform_name,
                            "status": "success",
                            "standard_number": result.standard_number,
                            "file_path": result.file_path,
                            "view_url": result.download_url,
                            "message": result.message,
                            "file_size": result.file_size,
                        }
                    )
                else:
                    results.append(
                        {
                            "platform": platform_name,
                            "status": "error",
                            "standard_number": result.standard_number,
                            "message": result.message,
                        }
                    )
            except Exception as e:
                results.append(
                    {
                        "platform": platform_name,
                        "status": "error",
                        "message": f"异常: {str(e)}",
                    }
                )

        return results


# 便捷函数
async def download_standard(
    standard_number: str, download_dir: str = None
) -> DownloadResult:
    """下载单个标准"""
    downloader = StandardAutoDownloader(download_dir)
    try:
        results = await downloader.download(standard_number)
        return (
            results[0]
            if results
            else DownloadResult(
                standard_number=standard_number, success=False, message="下载失败"
            )
        )
    finally:
        await downloader.close()


if __name__ == "__main__":

    async def test():
        result = await download_standard("GB/T 19001-2016")
        print(f"结果: {result}")

    asyncio.run(test())
