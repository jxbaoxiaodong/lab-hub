"""标准查询服务 - 整合自 standardcheck

修复说明:
1. 移除了所有模拟数据返回逻辑
2. 当Selenium不可用时抛出明确异常
3. 当查询失败时抛出异常而非返回假数据
4. 添加了详细日志便于调试
"""

import asyncio
import re
import logging
import sys
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Callable
from dataclasses import dataclass, asdict

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
        "input_xpath": '//*[@id="txtNo"]',
        "search_xpath": '//*[@id="ibtnSearch"]',
        "result_class": "lisyt-xq",
        "need_format": False,  # 允许模糊检索，不分大小写和空格，最好整个输入
    },
    "shenzhen": {
        "name": "深圳市标准信息服务平台",
        "url": "http://standard.sist.org.cn/StdSearch/stdSearchHome1.aspx",
        "input_xpath": '//*[@id="tnkey"]',
        "search_xpath": '//*[@id="stdsearchadvanced"]',
        "need_format": True,  # 需要格式化
    },
    "shanxi": {
        "name": "陕西省标准信息公共服务平台",
        "url": "http://219.144.196.30/Standard/StdSearch.aspx",
        "input_xpath": '//*[@id="txtNo"]',
        "search_xpath": '//*[@id="ibtnSearch"]',
        "need_format": False,  # 允许模糊检索，不分大小写和空格，最好整个输入
    },
    "jiangxi": {
        "name": "江西省标准化信息服务平台",
        "url": "http://59.53.159.10:7003/STDL/STDL1.aspx",
        "input_xpath": '//*[@id="txtStdNO"]',
        "search_xpath": '//*[@id="ibtnSearch"]',
        "need_format": True,  # 不分大小写但区分空格，提取字符段对结果检索有利
    },
    "suzhou": {
        "name": "苏州市标准信息公共服务平台",
        "url": "http://www.szbz.org/Standard/List.aspx",
        "input_xpath": '//*[@id="txtNo"]',
        "search_xpath": '//*[@id="ibtnSearch"]',
        "need_format": False,  # 允许模糊检索，不分大小写和空格，最好整个输入
    },
}


def format_standard_code(code: str) -> str:
    """
    格式化标准号 - 与原始脚本保持一致

    使用正则表达式提取字母、数字和带点的数字（如 "123.456"）
    去除单字母的部分，用空格连接

    示例:
        输入: "GB/T19001-2016"
        输出: "GB 19001 2016"
    """
    # 提取字母、数字和带点的数字
    parts = re.findall(r"[a-zA-Z]+|\d+(?:\.\d+)?", code)

    # 去除单字母的部分
    parts = [i for i in parts if len(i) > 1]

    # 用空格连接提取到的部分
    formatted_code = " ".join(parts)

    return formatted_code


class StandardQueryService:
    """标准查询服务 - 修复版"""

    def __init__(self, progress_callback=None):
        self._driver = None
        self._webdriver = None
        self._selenium_available = None
        self.progress_callback = progress_callback

    def _report(self, current: int, total: int, message: str, details: dict = None):
        if self.progress_callback:
            self.progress_callback(current, total, message, details)

    def _check_selenium(self) -> bool:
        """检查Selenium是否可用"""
        if self._selenium_available is not None:
            return self._selenium_available

        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options

            self._webdriver = webdriver
            self._selenium_available = True
            logger.info("Selenium检查通过")
            return True
        except ImportError as e:
            logger.error(f"Selenium未安装: {e}")
            self._selenium_available = False
            return False

    def _init_driver(self):
        """初始化WebDriver"""
        if self._driver is not None:
            return

        if not self._check_selenium():
            raise RuntimeError(
                "Selenium不可用。请安装Selenium: pip install selenium\n"
                "并确保Chrome浏览器和ChromeDriver已安装。"
            )

        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        import os

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--log-level=3")

        self._report(5, 100, "检测Chrome浏览器...", {})

        try:
            # 使用webdriver-manager自动下载和管理ChromeDriver
            from selenium.webdriver.chrome.service import Service as ChromeService
            from webdriver_manager.chrome import ChromeDriverManager
            from webdriver_manager.core.os_manager import ChromeType

            self._report(10, 100, "自动下载ChromeDriver（使用国内镜像源）...", {})

            # 配置webdriver-manager使用国内镜像源
            import os

            if not os.environ.get("WDM_DRIVER_URL"):
                os.environ["WDM_DRIVER_URL"] = (
                    "https://registry.npmmirror.com/-/binary/chromedriver/"
                )
                os.environ["WDM_SSL_VERIFY"] = "0"

            # 自动检测Chrome版本并下载匹配的ChromeDriver（使用镜像源）
            service = ChromeService(ChromeDriverManager().install())
            self._driver = self._webdriver.Chrome(service=service, options=options)

        except Exception as e:
            # 如果自动下载失败，尝试本地路径
            logger.warning(f"自动下载ChromeDriver失败: {e}，尝试本地路径...")
            self._report(10, 100, "尝试本地ChromeDriver...", {})

            # 获取项目根目录（支持打包后的路径）
            try:
                if getattr(sys, "frozen", False):
                    base_dir = Path(sys.executable).parent
                else:
                    base_dir = Path(__file__).parent.parent.parent
            except NameError:
                # __file__未定义（exec执行时），使用当前工作目录
                base_dir = Path.cwd()

            # 本地路径列表
            local_paths = [
                base_dir / "drivers" / "chromedriver.exe",  # Windows打包
                base_dir / "drivers" / "chromedriver",  # Linux/Mac打包
                "/usr/bin/chromedriver",
                "/usr/local/bin/chromedriver",
                r"C:\Program Files\Google\Chrome\Application\chromedriver.exe",
            ]

            service = None
            for path in local_paths:
                if os.path.exists(path):
                    service = Service(executable_path=str(path))
                    logger.info(f"使用本地ChromeDriver: {path}")
                    break

            if service:
                try:
                    self._driver = self._webdriver.Chrome(
                        service=service, options=options
                    )
                except Exception as e2:
                    raise RuntimeError(
                        f"ChromeDriver初始化失败。\n"
                        f"自动下载错误: {e}\n"
                        f"本地路径错误: {e2}\n\n"
                        f"请确保:\n"
                        f"1. 已安装Google Chrome浏览器\n"
                        f"2. 网络连接正常（首次使用需要下载ChromeDriver）\n"
                        f"3. 或手动下载ChromeDriver放到drivers/目录"
                    )
            else:
                raise RuntimeError(
                    f"无法自动下载ChromeDriver且未找到本地ChromeDriver。\n"
                    f"错误: {e}\n\n"
                    f"请确保:\n"
                    f"1. 网络连接正常（需要下载约10MB的ChromeDriver）\n"
                    f"2. 已安装Google Chrome浏览器\n"
                    f"3. 或手动下载chromedriver并放到以下位置之一:\n"
                    f"   - {base_dir}/drivers/chromedriver(.exe)\n"
                    f"   - /usr/bin/chromedriver\n"
                    f"   - /usr/local/bin/chromedriver"
                )

        self._driver.set_page_load_timeout(30)
        self._driver.implicitly_wait(10)
        logger.info("WebDriver初始化成功")

    def close(self):
        """关闭WebDriver"""
        if self._driver:
            try:
                self._driver.quit()
            except:
                pass
            self._driver = None

    async def query_single(self, standard_number: str, platform: str = "hunan") -> Dict:
        """查询单个标准 - 修复版

        重要变更:
        1. 当Selenium不可用时，抛出异常而不是返回模拟数据
        2. 当查询失败时，抛出异常而不是返回假数据
        3. 调用方需要自己处理异常
        """
        # 检查Selenium是否可用
        if not self._check_selenium():
            raise RuntimeError(
                "Selenium不可用，无法查询真实数据。\n"
                "请安装Selenium: pip install selenium\n"
                "并确保Chrome浏览器和ChromeDriver已安装。"
            )

        # 检查平台是否有效
        if platform not in QUERY_PLATFORMS:
            raise ValueError(
                f"未知平台: {platform}\n可用平台: {', '.join(QUERY_PLATFORMS.keys())}"
            )

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, self._sync_query, standard_number, platform
            )

            # 检查是否是模拟数据（通过resource字段判断）
            if result.get("resource", "").endswith("(模拟)"):
                logger.warning(f"查询返回了模拟数据: {standard_number}")
                raise RuntimeError(
                    f"查询 {standard_number} 返回了模拟数据，可能是页面解析失败。"
                )

            return result

        except Exception as e:
            logger.error(f"查询失败 {standard_number}: {e}")
            # 重新抛出异常，让调用方处理
            raise RuntimeError(f"查询标准 {standard_number} 失败: {e}")

    def _sync_query(self, standard_number: str, platform: str) -> Dict:
        """同步查询 - 内部方法"""
        import time

        try:
            config = QUERY_PLATFORMS[platform]
            platform_name = config["name"]

            self._report(
                0,
                100,
                f"初始化浏览器...",
                {"standard": standard_number, "platform": platform_name},
            )
            self._init_driver()

            url = config["url"]

            self._report(
                10,
                100,
                f"打开 {platform_name}",
                {"standard": standard_number, "platform": platform_name},
            )

            if config.get("need_format", False):
                formatted_number = format_standard_code(standard_number)
                logger.info(
                    f"平台 {platform} 需要格式化: {standard_number} -> {formatted_number}"
                )
                input_number = formatted_number
            else:
                input_number = standard_number

            self._driver.get(url)
            time.sleep(1)

            self._report(
                20, 100, f"输入标准号: {input_number}", {"standard": standard_number}
            )

            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC

            input_elem = WebDriverWait(self._driver, 15).until(
                EC.presence_of_element_located((By.XPATH, config["input_xpath"]))
            )
            input_elem.clear()
            input_elem.send_keys(input_number)

            self._report(
                30,
                100,
                f"搜索: {input_number}",
                {"standard": standard_number, "platform": platform_name},
            )

            search_btn = WebDriverWait(self._driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, config["search_xpath"]))
            )
            search_btn.click()

            time.sleep(2)

            self._report(
                50,
                100,
                f"查找结果...",
                {"standard": standard_number, "platform": platform_name},
            )

            try:
                result_elem = WebDriverWait(self._driver, 15).until(
                    EC.element_to_be_clickable(
                        (By.CLASS_NAME, config.get("result_class", "lisyt-xq"))
                    )
                )
                result_elem.click()
            except Exception as e:
                logger.warning(f"无法点击结果: {e}")
                raise RuntimeError(f"无法找到或点击查询结果: {e}")

            self._report(
                70,
                100,
                f"提取详情...",
                {"standard": standard_number, "platform": platform_name},
            )

            WebDriverWait(self._driver, 15).until(lambda d: len(d.window_handles) > 1)
            self._driver.switch_to.window(self._driver.window_handles[-1])

            time.sleep(2)

            info = self._extract_standard_info(standard_number, platform_name)

            self._report(
                90,
                100,
                f"完成: {standard_number}",
                {"standard": standard_number, "platform": platform_name},
            )

            self._driver.close()
            self._driver.switch_to.window(self._driver.window_handles[0])

            return self._to_dict(info)

        except Exception as e:
            logger.error(f"查询失败 {standard_number}: {e}")
            # 清理driver
            self.close()
            # 抛出异常而不是返回模拟数据
            raise RuntimeError(f"查询标准 {standard_number} 时发生错误: {e}")

    def _extract_standard_info(self, standard_number: str, source: str) -> StandardInfo:
        """提取标准详情"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

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

        data = {"resource": source, "standard_number": standard_number}

        for elem_id, field_name in field_ids:
            try:
                elem = WebDriverWait(self._driver, 10).until(
                    EC.presence_of_element_located((By.ID, elem_id))
                )
                data[field_name] = elem.text.strip()
            except:
                data[field_name] = ""

        return StandardInfo(**data)

    def _to_dict(self, info: StandardInfo) -> Dict:
        """转换为字典"""
        return asdict(info)

    async def query_batch(
        self, standards: List[str], platform: str = "hunan"
    ) -> List[Dict]:
        """批量查询标准 - 修复版

        重要变更:
        1. 单个查询失败不会导致整个批次失败
        2. 失败的查询会在结果中标记错误信息
        3. 调用方可以检查每个结果的success字段
        """
        results = []
        for std in standards:
            try:
                result = await self.query_single(std, platform)
                result["_success"] = True
                results.append(result)
            except Exception as e:
                logger.error(f"批量查询中 {std} 失败: {e}")
                # 记录失败的查询而不是让整个批次失败
                results.append(
                    {
                        "standard_number": std,
                        "_success": False,
                        "_error": str(e),
                        "chinese_name": "",
                        "standard_status": "",
                        "resource": platform,
                    }
                )
            await asyncio.sleep(0.5)
        return results

    def get_platforms(self) -> Dict:
        """获取可用的查询平台"""
        return {
            k: {"id": k, "name": v["name"], "url": v["url"]}
            for k, v in QUERY_PLATFORMS.items()
        }

    async def health_check(self) -> Tuple[bool, str]:
        """健康检查

        返回:
            (is_healthy, message)
        """
        try:
            if not self._check_selenium():
                return False, "Selenium不可用"

            # 尝试初始化driver
            self._init_driver()
            self.close()

            return True, "服务正常"
        except Exception as e:
            return False, f"健康检查失败: {e}"
