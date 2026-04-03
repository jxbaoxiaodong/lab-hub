"""标准查询服务 - 整合自 standardcheck

修复说明:
1. 移除了所有模拟数据返回逻辑
2. 当Selenium不可用时抛出明确异常
3. 当查询失败时抛出异常而非返回假数据
4. 添加了详细日志便于调试
5. Playwright 代码保留，但当前默认停用，直接使用 Selenium；后续可通过环境变量重新启用
"""

import asyncio
from contextlib import contextmanager
import os
import re
import logging
import subprocess
import sys
import shutil
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Callable
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)
ALLOW_INSECURE_TLS = os.environ.get("LAB_ALLOW_INSECURE_TLS", "0") == "1"
PLAYWRIGHT_ENABLED = os.environ.get("LAB_ENABLE_PLAYWRIGHT", "0") == "1"
LOCK_CHROME_UPDATE = os.environ.get("LAB_LOCK_CHROME_UPDATE", "1") == "1"
PLAYWRIGHT_DISABLED_MESSAGE = "Playwright已默认停用，直接使用Selenium（设置 LAB_ENABLE_PLAYWRIGHT=1 可重新启用）"


def _decode_subprocess_output(data: bytes) -> str:
    if not data:
        return ""
    for encoding in ("utf-8", "gb18030", "gbk"):
        try:
            return data.decode(encoding)
        except Exception:
            continue
    return data.decode("utf-8", errors="ignore")


def _get_query_browser_profile_dir() -> Optional[Path]:
    configured = (os.environ.get("LAB_QUERY_BROWSER_PROFILE_DIR") or "").strip()
    if configured:
        path = Path(configured).expanduser()
    else:
        try:
            base_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        except Exception:
            base_dir = Path.cwd()
        path = base_dir / "cache" / "browser_profile_query"

    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except Exception:
        return None


def _build_ssl_context():
    import ssl

    ctx = ssl.create_default_context()
    if ALLOW_INSECURE_TLS:
        logger.warning("LAB_ALLOW_INSECURE_TLS 已启用，查询模块将跳过 TLS 证书校验")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


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
        "selenium_input_selectors": ["#txtStd", "#txtNo"],
        "search_selector": "#ibtnSearch",
        "result_selector": ".lisyt-xq",
        "need_format": False,  # 允许模糊检索，不分大小写和空格，最好整个输入
        "type": "popup",
    },
    "shenzhen": {
        "name": "深圳市标准信息服务平台",
        "url": "http://standard.sist.org.cn/StdSearch/stdSearchHome1.aspx?OperType=2&m=%u6807%u51C6&t=%u9898%u5F55%u4FE1%u606F&v=",
        "input_selector": "#tnkey",
        "search_selector": "#stdsearchadvanced",
        "result_selector": '#stdList a[href*="stdDetail"]',
        "need_format": True,  # 需要格式化
        "type": "link",
    },
    "shanxi": {
        "name": "陕西省标准信息公共服务平台",
        "url": "http://219.144.196.30/Standard/StdSearch.aspx",
        "input_selector": "#txtNo",
        "search_selector": "#ibtnSearch",
        "result_selector": '#dstStd a',
        "need_format": False,  # 允许模糊检索，不分大小写和空格，最好整个输入
        "type": "popup",
    },
    "jiangxi": {
        "name": "江西省标准化信息服务平台",
        "url": "http://59.53.159.10:7003/STDL/STDL1.aspx",
        "input_selector": "#txtStdNO",
        "search_selector": "#ibtnSearch",
        "result_selector": ".link-xqa",
        "need_format": True,  # 不分大小写但区分空格，提取字符段对结果检索有利
        "type": "popup",
    },
    "liaocheng": {
        "name": "聊城市标准信息公共服务平台",
        "url": "http://www.lcbzpt.cn/StdSearch/stdSearchHome1.aspx?OperType=2&m=%u6807%u51C6&t=%u5168%u6587&v=",
        "input_selector": "#tnkey",
        "search_selector": "#stdsearchadvanced",
        "result_selector": '#stdList a[href*="stdDetail"]',
        "need_format": True,
        "selenium_page_load_timeout": 90,
        "selenium_result_wait_timeout": 45,
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


def check_playwright_available() -> tuple:
    """检查Playwright是否可用"""
    if not PLAYWRIGHT_ENABLED:
        return False, PLAYWRIGHT_DISABLED_MESSAGE
    try:
        from playwright.async_api import async_playwright
        return True, "Playwright可用"
    except ImportError:
        return False, "Playwright未安装"


def check_selenium_available() -> tuple:
    """检查Selenium是否可用"""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        return True, "Selenium可用"
    except ImportError:
        return False, "Selenium未安装"


class StandardQueryService:
    """标准查询服务 - 修复版"""

    # 类变量：共享的浏览器实例（避免每次查询创建新窗口）
    _shared_driver = None
    _shared_driver_lock = None
    _shared_browser = None
    _shared_context = None
    _shared_playwright = None
    _shared_browser_lock = None
    _shared_playwright_init_error = None
    _shared_selenium_init_error = None
    _chrome_update_lock_attempted = False

    # 国内镜像配置
    CHROMEDRIVER_MIRRORS = [
        ("npmmirror", "https://registry.npmmirror.com/-/binary/"),
    ]

    # 镜像源URL
    NPMMIRROR_BASE = "https://registry.npmmirror.com/-/binary/"

    @staticmethod
    def _get_chrome_version() -> Optional[str]:
        """获取本机Chrome版本号"""
        chrome_paths = StandardQueryService._chrome_binary_candidates()

        for chrome_path in chrome_paths:
            try:
                result = subprocess.run(
                    [chrome_path, "--version"],
                    capture_output=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    stdout = result.stdout if isinstance(result.stdout, bytes) else str(result.stdout or "").encode("utf-8", errors="ignore")
                    output = _decode_subprocess_output(stdout)
                    # 解析版本号，格式如 "Google Chrome 131.0.6789.120"
                    match = re.search(r"Chrome[\s/]?(\d+)\.(\d+)\.(\d+)\.(\d+)", output)
                    if match:
                        return f"{match.group(1)}.{match.group(2)}.{match.group(3)}.{match.group(4)}"
            except Exception:
                continue

        if sys.platform == "win32":
            registry_version = StandardQueryService._get_windows_chrome_version_from_registry()
            if registry_version:
                return registry_version
        return None

    def _download_chromedriver_npmirror(self, version: str, timeout: int = 180) -> Optional[str]:
        """使用npmmirror下载ChromeDriver

        Args:
            version: Chrome版本号 (如 131.0.6778.264)
            timeout: 超时时间(秒)

        Returns:
            ChromeDriver路径，失败返回None
        """
        import zipfile
        import io
        import urllib.request
        import ssl

        # 确定平台
        if sys.platform == "win32":
            platform = "win64"
            ext = ".exe"
        elif sys.platform == "darwin":
            platform = "mac-x64"
            ext = ""
        else:
            platform = "linux64"
            ext = ""

        # 构建URL: chrome-for-testing/{version}/{platform}/chromedriver-{platform}.zip
        base_url = f"{self.NPMMIRROR_BASE}chrome-for-testing/{version}/{platform}/chromedriver-{platform}.zip"
        logger.info(f"尝试从npmmirror下载ChromeDriver: {base_url}")

        try:
            ssl_context = _build_ssl_context()
        except Exception:
            ssl_context = None

        try:
            # 下载zip文件
            req = urllib.request.Request(
                base_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                },
            )

            if ssl_context:
                with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as response:
                    zip_data = response.read()
            else:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    zip_data = response.read()

            # 解压到drivers目录
            if getattr(sys, "frozen", False):
                base_dir = Path(sys.executable).parent
            else:
                base_dir = Path(__file__).parent.parent.parent

            drivers_dir = base_dir / "drivers"
            drivers_dir.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                for member in zf.namelist():
                    # 找chromedriver可执行文件（不在目录中）
                    if "/chromedriver" in member and not member.endswith("/"):
                        # 去掉前缀目录（如chromedriver-linux64/）
                        filename = member.split("/")[-1]
                        if ext and not filename.endswith(ext):
                            filename = filename + ext
                        target_path = drivers_dir / filename
                        # 提取文件
                        with zf.open(member) as source:
                            with open(target_path, "wb") as target:
                                target.write(source.read())
                        # 设置执行权限(Linux/Mac)
                        if sys.platform != "win32":
                            target_path.chmod(0o755)
                        logger.info(f"ChromeDriver已解压到: {target_path}")
                        return str(target_path)

            return None

        except Exception as e:
            logger.warning(f"npmmirror下载失败: {e}")
            return None

    @staticmethod
    def _get_latest_matching_version(chrome_version: str) -> Optional[str]:
        """从npmmirror获取与Chrome版本匹配的最新可用ChromeDriver版本

        由于ChromeDriver版本必须与Chrome完全匹配，我们需要找到精确版本或最接近的版本
        """
        import urllib.request
        import ssl
        import json

        base_url = "https://registry.npmmirror.com/-/binary/chrome-for-testing/"

        try:
            ssl_context = _build_ssl_context()
        except Exception:
            ssl_context = None

        try:
            req = urllib.request.Request(
                base_url,
                headers={"User-Agent": "Mozilla/5.0"},
            )

            if ssl_context:
                with urllib.request.urlopen(req, timeout=30, context=ssl_context) as response:
                    data = json.loads(response.read().decode())
            else:
                with urllib.request.urlopen(req, timeout=30) as response:
                    data = json.loads(response.read().decode())

            # 提取所有可用版本
            versions = []
            for item in data:
                name = item.get("name", "")
                if name.endswith("/"):
                    version = name.rstrip("/")
                    if re.match(r"^\d+\.\d+\.\d+\.\d+$", version):
                        versions.append(version)

            if not versions:
                return None

            # 按版本号排序
            def version_key(v):
                parts = v.split(".")
                return tuple(int(p) for p in parts)

            versions.sort(key=version_key, reverse=True)

            # 提取Chrome主版本号 (如 131)
            chrome_major = chrome_version.split(".")[0] if chrome_version else ""

            # 找到匹配的版本
            for v in versions:
                if v.startswith(chrome_major + "."):
                    return v

            # 如果没有精确匹配，返回最新的可用版本
            return versions[0] if versions else None

        except Exception as e:
            logger.warning(f"获取版本列表失败: {e}")
            return None

    def __init__(self, progress_callback=None, query_platforms=None, cancel_callback=None):
        # Playwright相关
        self._browser = None
        self._context = None
        self._playwright = None
        self._playwright_available = None
        
        # Selenium相关
        self._driver = None
        self._webdriver = None
        self._selenium_available = None
        
        self.progress_callback = progress_callback
        self.query_platforms = query_platforms if query_platforms else QUERY_PLATFORMS
        self.cancel_callback = cancel_callback
        self._default_implicit_wait = 10
        self._probe_implicit_wait = 0.2

    @contextmanager
    def _temporary_implicit_wait(self, seconds: float):
        if not self._driver:
            yield
            return
        try:
            self._driver.implicitly_wait(seconds)
            yield
        finally:
            try:
                self._driver.implicitly_wait(self._default_implicit_wait)
            except Exception:
                pass

    @staticmethod
    def _chrome_binary_candidates() -> List[str]:
        home = Path(os.path.expanduser("~"))
        candidates = [
            shutil.which("google-chrome"),
            shutil.which("chrome"),
            shutil.which("chrome.exe"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
        ]

        if sys.platform == "win32":
            local_app_data = os.environ.get("LOCALAPPDATA", "")
            program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
            program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
            candidates.extend(
                [
                    os.path.join(program_files, "Google", "Chrome", "Application", "chrome.exe"),
                    os.path.join(program_files_x86, "Google", "Chrome", "Application", "chrome.exe"),
                    os.path.join(local_app_data, "Google", "Chrome", "Application", "chrome.exe") if local_app_data else None,
                    os.path.join(local_app_data, "Chromium", "Application", "chrome.exe") if local_app_data else None,
                    StandardQueryService._get_windows_chrome_path_from_registry(),
                ]
            )
        else:
            candidates.extend(
                [
                    "C:/Program Files/Google/Chrome/Application/chrome.exe",
                    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
                    str(home / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe"),
                    str(home / "AppData" / "Local" / "Chromium" / "Application" / "chrome.exe"),
                ]
            )

        unique = []
        seen = set()
        for path in candidates:
            if not path:
                continue
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(str(path))
        return unique

    @staticmethod
    def _get_windows_chrome_path_from_registry() -> Optional[str]:
        if sys.platform != "win32":
            return None
        try:
            import winreg

            keys_to_try = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
            ]
            for hive, key_path in keys_to_try:
                try:
                    with winreg.OpenKey(hive, key_path) as key:
                        value, _ = winreg.QueryValueEx(key, None)
                        if value:
                            return value
                except Exception:
                    continue
        except Exception:
            return None
        return None

    @staticmethod
    def _get_windows_chrome_version_from_registry() -> Optional[str]:
        if sys.platform != "win32":
            return None
        try:
            import winreg

            keys_to_try = [
                (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon", "version"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\Chrome\BLBeacon", "version"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Google\Chrome\BLBeacon", "version"),
            ]
            for hive, key_path, value_name in keys_to_try:
                try:
                    with winreg.OpenKey(hive, key_path) as key:
                        value, _ = winreg.QueryValueEx(key, value_name)
                        if value and re.match(r"^\d+\.\d+\.\d+\.\d+$", str(value)):
                            return str(value)
                except Exception:
                    continue
        except Exception:
            return None
        return None

    @classmethod
    def _detect_chrome_binary(cls) -> Optional[str]:
        for candidate in cls._chrome_binary_candidates():
            if shutil.which(candidate) or Path(candidate).exists():
                return candidate
        return None

    @staticmethod
    def _chromedriver_candidates(base_dir: Path) -> List[Path]:
        """根据当前平台返回本地 ChromeDriver 候选路径。"""
        home = Path(os.path.expanduser("~"))
        if sys.platform == "win32":
            return [
                base_dir / "drivers" / "chromedriver.exe",
                base_dir / "drivers" / "chromedriver-win64" / "chromedriver.exe",
                Path("C:/chromedriver.exe"),
                Path("D:/chromedriver.exe"),
                home / "AppData" / "Local" / "Programs" / "chromedriver.exe",
                home / "chromedriver.exe",
            ]

        return [
            Path("/usr/local/bin/chromedriver"),
            Path("/usr/bin/chromedriver"),
            base_dir / "drivers" / "chromedriver",
            base_dir / "drivers" / "chromedriver-linux64" / "chromedriver",
        ]

    @staticmethod
    def _is_browser_bootstrap_error(error: Exception) -> bool:
        text = str(error or "").lower()
        markers = [
            "playwright浏览器",
            "chromedriver",
            "session not created",
            "cannot find chrome binary",
            "browserType.launch",
            "executable doesn't exist",
            "this version of chromedriver only supports",
            "unable to discover open pages",
            "unknown error: cannot find",
        ]
        return any(marker in text for marker in markers)

    @staticmethod
    def _is_driver_version_mismatch_error(error: Exception) -> bool:
        text = str(error or "").lower()
        markers = [
            "this version of chromedriver only supports",
            "session not created",
            "chrome version",
            "only supports chrome version",
        ]
        return any(marker in text for marker in markers)

    @classmethod
    def _apply_windows_chrome_update_lock_best_effort(cls):
        """尽力锁定 Chrome 自动更新（Windows, 无管理员权限场景优先 HKCU）。"""
        if sys.platform != "win32" or not LOCK_CHROME_UPDATE:
            return
        if cls._chrome_update_lock_attempted:
            return
        cls._chrome_update_lock_attempted = True

        try:
            import winreg
        except Exception as e:
            logger.info("Chrome 更新锁定跳过（winreg 不可用）: %s", e)
            return

        update_guid = "{8A69D345-D564-463c-AFF1-A69D9E530F96}"
        policy_values = {
            "UpdateDefault": 0,
            "AutoUpdateCheckPeriodMinutes": 0,
            "DisableAutoUpdateChecksCheckboxValue": 1,
            f"Update{update_guid}": 0,
            f"Install{update_guid}": 0,
        }

        base_key = r"Software\Policies\Google\Update"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                key = winreg.CreateKeyEx(hive, base_key, 0, winreg.KEY_SET_VALUE)
                try:
                    for name, value in policy_values.items():
                        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))
                finally:
                    winreg.CloseKey(key)
                logger.info(
                    "Chrome 自动更新策略已写入 %s\\%s",
                    "HKCU" if hive == winreg.HKEY_CURRENT_USER else "HKLM",
                    base_key,
                )
            except Exception as e:
                logger.info(
                    "Chrome 更新锁定写入失败（%s）: %s",
                    "HKCU" if hive == winreg.HKEY_CURRENT_USER else "HKLM",
                    e,
                )

    def _build_selenium_options(self, headless_arg: Optional[str] = "--headless=new"):
        from selenium.webdriver.chrome.options import Options

        options = Options()
        if headless_arg:
            options.add_argument(headless_arg)
        options.add_argument("--remote-debugging-pipe")
        # 某些 Windows/驱动组合下即便传了 headless 也可能短暂露出窗口，
        # 这里额外把窗口最小化并挪到屏幕外，尽量避免打扰用户。
        options.add_argument("--start-minimized")
        options.add_argument("--window-position=-2400,-2400")
        options.add_argument("--window-size=1280,960")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-backgrounding-occluded-windows")
        options.add_argument("--disable-renderer-backgrounding")
        options.add_argument("--disable-background-timer-throttling")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-session-crashed-bubble")
        options.add_argument("--disable-infobars")
        options.add_argument("--disable-extensions")
        # 强制直连，避免系统代理异常（如 127.0.0.1:7897）导致查询站点不可达。
        options.add_argument("--proxy-server=direct://")
        options.add_argument("--proxy-bypass-list=*")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--log-level=3")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.page_load_strategy = "eager"
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        profile_dir = _get_query_browser_profile_dir()
        if profile_dir:
            options.add_argument(f"--user-data-dir={profile_dir}")
        chrome_binary = self._detect_chrome_binary()
        if chrome_binary:
            options.binary_location = chrome_binary
        return options

    @staticmethod
    def _hide_selenium_window(driver):
        """尽量把自动化浏览器窗口藏起来，避免影响用户。"""
        if driver is None:
            return

        for action in (
            lambda: driver.minimize_window(),
            lambda: driver.set_window_position(-2400, 0),
            lambda: driver.set_window_size(1280, 960),
        ):
            try:
                action()
            except Exception:
                pass

        try:
            window_info = driver.execute_cdp_cmd("Browser.getWindowForTarget", {})
            window_id = window_info.get("windowId")
            if window_id:
                for bounds in (
                    {"windowState": "minimized"},
                    {"left": -32000, "top": 0, "width": 1280, "height": 960},
                ):
                    try:
                        driver.execute_cdp_cmd(
                            "Browser.setWindowBounds",
                            {"windowId": window_id, "bounds": bounds},
                        )
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            driver.execute_script("window.blur();")
        except Exception:
            pass

    def _create_selenium_driver(self, service):
        last_error = None
        for headless_arg in ("--headless=new", "--headless"):
            options = self._build_selenium_options(headless_arg=headless_arg)
            try:
                driver = self._webdriver.Chrome(service=service, options=options)
                self._hide_selenium_window(driver)
                time.sleep(0.05)
                self._hide_selenium_window(driver)
                try:
                    driver.execute_cdp_cmd(
                        "Page.addScriptToEvaluateOnNewDocument",
                        {
                            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                        },
                    )
                except Exception:
                    pass
                return driver
            except Exception as e:
                last_error = e
                logger.warning(
                    "ChromeDriver 启动失败 (headless=%s): %s",
                    headless_arg,
                    e,
                )
        raise last_error or RuntimeError("ChromeDriver初始化失败")

    def _report(self, current: int, total: int, message: str, details: dict = None):
        if self.progress_callback:
            self.progress_callback(current, total, message, details)

    def _is_cancelled(self) -> bool:
        try:
            return bool(self.cancel_callback and self.cancel_callback())
        except Exception:
            return False

    def _raise_if_cancelled(self):
        if self._is_cancelled():
            raise RuntimeError("查询已取消")

    def _normalize_standard_token(self, value: str) -> str:
        """将标准号归一化为便于匹配的形式。"""
        return re.sub(r"[^A-Z0-9]", "", (value or "").upper())

    def _score_result_candidate(
        self,
        candidate_text: str,
        candidate_href: str,
        standard_number: str,
        formatted_number: str = "",
    ) -> int:
        """对搜索结果候选项打分，优先匹配原始标准号。"""
        from urllib.parse import parse_qs, unquote, urlparse

        targets = [
            self._normalize_standard_token(standard_number),
            self._normalize_standard_token(formatted_number),
        ]
        candidate_text_norm = self._normalize_standard_token(candidate_text)

        href_token = ""
        try:
            parsed = urlparse(candidate_href or "")
            href_params = parse_qs(parsed.query)
            if href_params.get("AppID"):
                href_token = self._normalize_standard_token(
                    unquote(href_params["AppID"][0])
                )
            elif href_params.get("v"):
                href_token = self._normalize_standard_token(
                    unquote(href_params["v"][0])
                )
        except Exception:
            href_token = ""
        if not href_token:
            href_token = self._normalize_standard_token(candidate_href)

        best_score = 0
        for target in targets:
            if not target:
                continue
            # href 命中比纯文本命中更可靠，优先级更高
            if href_token == target:
                return 120 if candidate_text_norm == target else 115
            if href_token and target in href_token:
                best_score = max(best_score, 105)

            if candidate_text_norm == target:
                best_score = max(best_score, 100)
            elif target in candidate_text_norm:
                # 文本里出现目标号不一定代表这条结果正确，
                # 例如标题可能提到目标标准但 href 指向的是别的标准。
                best_score = max(best_score, 70)
            elif candidate_text_norm and candidate_text_norm in target:
                best_score = max(best_score, 60)
        return best_score

    async def _pick_best_playwright_candidate(
        self, page, selector: str, standard_number: str, formatted_number: str = ""
    ):
        """从 Playwright 候选元素中挑选最匹配的结果。"""
        candidates = await page.query_selector_all(selector)
        if not candidates:
            return None

        best_candidate = None
        best_score = -1
        for candidate in candidates:
            try:
                text = await candidate.inner_text()
            except Exception:
                text = ""
            try:
                href = await candidate.get_attribute("href")
            except Exception:
                href = ""

            score = self._score_result_candidate(
                text or "", href or "", standard_number, formatted_number
            )
            if score > best_score:
                best_score = score
                best_candidate = candidate

        return best_candidate if best_score > 0 else candidates[0]

    def _pick_best_selenium_candidate(
        self, elements, standard_number: str, formatted_number: str = ""
    ):
        """从 Selenium 候选元素中挑选最匹配的结果。"""
        if not elements:
            return None

        best_candidate = None
        best_score = -1
        for element in elements:
            try:
                text = element.get_attribute("innerText") or element.text or ""
            except Exception:
                text = ""
            try:
                href = element.get_attribute("href") or ""
            except Exception:
                href = ""

            score = self._score_result_candidate(
                text, href, standard_number, formatted_number
            )
            if score > best_score:
                best_score = score
                best_candidate = element

        return best_candidate if best_score > 0 else elements[0]

    def _is_expected_standard(self, result: Dict, standard_number: str) -> bool:
        """判断返回结果是否与目标标准一致。"""
        result_number = self._normalize_standard_token(result.get("standard_number", ""))
        target_number = self._normalize_standard_token(standard_number)
        if not result_number or not target_number:
            return False
        return result_number == target_number

    def _is_fuzzy_query(self, standard_number: str) -> bool:
        """判断是否为模糊或不完整输入。"""
        value = (standard_number or "").strip()
        if not value:
            return True

        normalized = self._normalize_standard_token(value)
        if len(normalized) < 8:
            return True
        if re.fullmatch(r"\d{3,6}", normalized):
            return True
        if not re.search(r"[A-Za-z]", value):
            return True
        return False

    def _relax_standard_query(self, standard_number: str) -> str:
        """去掉标准号前缀，只保留主体编号和年份。

        例如:
        - NB/SH/T 6040-2021 机器人... -> 6040-2021
        - GB/T 19001-2016 质量管理体系... -> 19001-2016
        """
        value = (standard_number or "").strip()
        if not value:
            return value

        patterns = [
            r"(?:[A-Z]{1,8}(?:/[A-Z]{1,8})*\s*)?(\d{3,6})\s*[-–—/]\s*(\d{4})",
            r"(?:[A-Z]{1,8}(?:/[A-Z]{1,8})*\s*)?(\d{3,6})",
        ]
        for pattern in patterns:
            match = re.search(pattern, value, re.IGNORECASE)
            if match:
                if len(match.groups()) >= 2 and match.group(2):
                    return f"{match.group(1)}-{match.group(2)}"
                return match.group(1)
        return value

    async def _init_playwright(self):
        """初始化当前查询使用的 Playwright 实例。

        Playwright 的 async 对象跨不同 asyncio event loop 复用并不安全。
        当前客户端每条查询都通过 asyncio.run() 单独创建 event loop，
        因此这里不能像 Selenium 一样做进程级共享，否则第二轮/下一条查询
        可能卡死在 new_page() 等异步调用上。
        """
        if self._browser is not None and self._context is not None and self._playwright is not None:
            return

        timeouts = {
            "start": 20,
            "launch": 45,
            "context": 20,
            "init_script": 10,
        }

        self._report(
            0,
            100,
            "初始化 Playwright 浏览器...",
            {"engine": "playwright"},
        )

        try:
            from playwright.async_api import async_playwright

            self._raise_if_cancelled()
            self._report(2, 100, "启动 Playwright...", {"engine": "playwright"})
            self._playwright = await asyncio.wait_for(
                async_playwright().start(), timeout=timeouts["start"]
            )

            self._raise_if_cancelled()
            self._report(6, 100, "启动 Chromium...", {"engine": "playwright"})
            self._browser = await asyncio.wait_for(
                self._playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--proxy-server=direct://",
                        "--disable-blink-features=AutomationControlled",
                    ],
                ),
                timeout=timeouts["launch"],
            )

            self._raise_if_cancelled()
            self._report(10, 100, "创建浏览器上下文...", {"engine": "playwright"})
            self._context = await asyncio.wait_for(
                self._browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080},
                ),
                timeout=timeouts["context"],
            )

            self._raise_if_cancelled()
            await asyncio.wait_for(
                self._context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                ),
                timeout=timeouts["init_script"],
            )
            logger.info("Playwright初始化成功")
        except asyncio.TimeoutError as e:
            msg = (
                "Playwright初始化超时，已自动降级到 Selenium。"
                f" (start={timeouts['start']}s, launch={timeouts['launch']}s, context={timeouts['context']}s)"
            )
            logger.error("%s: %s", msg, e)
            self._playwright_available = False
            await self.close()
            raise RuntimeError(msg)
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception as e:
            if "查询已取消" in str(e):
                await self.close()
                raise
            logger.error(f"Playwright初始化失败: {e}")
            self._playwright_available = False
            await self.close()
            raise RuntimeError(f"无法初始化Playwright浏览器: {e}")

    async def _wait_for_playwright_selector(self, page, selector: str, timeout: int = 20):
        """可取消的 Playwright 元素等待。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._raise_if_cancelled()
            try:
                elements = await page.query_selector_all(selector)
                if elements:
                    return elements
            except Exception:
                pass
            await asyncio.sleep(0.5)
        raise TimeoutError(f"等待选择器超时: {selector}")

    @staticmethod
    def _kill_residual_chrome_processes():
        """保留兼容入口。

        旧逻辑会在 Windows 上直接 `taskkill chrome.exe`，这会误杀用户正在使用的
        客户端页面浏览器。现在只依赖 driver.quit() 清理 Selenium 自身实例，
        不再主动结束系统里的 Chrome 进程。
        """
        return

    @classmethod
    def shutdown_shared_selenium(cls):
        """释放共享 Selenium 实例，避免查询结束后残留自动化窗口。"""
        if cls._shared_driver_lock is None:
            driver = cls._shared_driver
            cls._shared_driver = None
            cls._shared_selenium_init_error = None
        else:
            with cls._shared_driver_lock:
                driver = cls._shared_driver
                cls._shared_driver = None
                cls._shared_selenium_init_error = None

        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

    def _init_selenium(self):
        """初始化Selenium WebDriver"""
        import threading

        # 初始化类变量
        if StandardQueryService._shared_driver_lock is None:
            StandardQueryService._shared_driver_lock = threading.Lock()

        # 双重检查锁定：先检查是否已初始化，再决定是否加锁创建
        if StandardQueryService._shared_driver is not None:
            self._driver = StandardQueryService._shared_driver
            self._hide_selenium_window(self._driver)
            return
        if StandardQueryService._shared_selenium_init_error:
            raise RuntimeError(StandardQueryService._shared_selenium_init_error)

        # 使用锁来确保只有一个线程创建driver
        with StandardQueryService._shared_driver_lock:
            # 再次检查（可能有其他线程刚创建完）
            if StandardQueryService._shared_driver is not None:
                self._driver = StandardQueryService._shared_driver
                self._hide_selenium_window(self._driver)
                return
            if StandardQueryService._shared_selenium_init_error:
                raise RuntimeError(StandardQueryService._shared_selenium_init_error)

            # 真正初始化Selenium
            if not self._check_selenium():
                raise RuntimeError(
                    "Selenium不可用。请安装Selenium: pip install selenium\n"
                    "并确保Chrome浏览器和ChromeDriver已安装。"
                )

            from selenium.webdriver.chrome.service import Service
            import os

        self._apply_windows_chrome_update_lock_best_effort()
        self._report(5, 100, "检测Chrome浏览器...", {})

        try:
            if getattr(sys, "frozen", False):
                base_dir = Path(sys.executable).parent
            else:
                base_dir = Path(__file__).resolve().parent.parent
        except NameError:
            base_dir = Path.cwd()

        local_paths = self._chromedriver_candidates(base_dir)

        last_local_error = None
        mismatch_detected = False
        for path in local_paths:
            if path.exists():
                if sys.platform == "win32" and path.suffix.lower() != ".exe":
                    logger.warning(f"跳过非Windows ChromeDriver: {path}")
                    continue
                logger.info(f"找到本地ChromeDriver: {path}")
                try:
                    self._report(10, 100, f"使用本地ChromeDriver: {path.name}...", {})
                    self._driver = self._create_selenium_driver(
                        Service(executable_path=str(path))
                    )
                    break
                except Exception as e:
                    last_local_error = e
                    mismatch_detected = mismatch_detected or self._is_driver_version_mismatch_error(e)
                    logger.warning(f"本地ChromeDriver {path} 初始化失败: {e}")

        if self._driver is None:
            # 本地路径都失败，使用自定义下载器从国内镜像下载
            from selenium.webdriver.chrome.service import Service as ChromeService

            if mismatch_detected:
                self._report(10, 100, "检测到浏览器已更新，正在后台更新驱动...", {})
            else:
                self._report(10, 100, "正在后台准备 ChromeDriver...", {})

            # 1. 获取本机Chrome版本
            chrome_version = self._get_chrome_version()
            if not chrome_version:
                raise RuntimeError(
                    "无法获取Chrome版本。\n"
                    f"本地驱动错误: {last_local_error}\n\n"
                    "请确保:\n"
                    "1. 已安装Google Chrome浏览器\n"
                    "2. Chrome浏览器可执行（路径正确）\n"
                    "3. 或手动下载chromedriver并放到drivers/目录"
                )

            self._report(11, 100, f"检测到 Chrome 版本: {chrome_version}", {})

            # 2. 获取匹配的ChromeDriver版本
            matched_version = self._get_latest_matching_version(chrome_version)
            if not matched_version:
                raise RuntimeError(
                    f"无法找到匹配的ChromeDriver版本 (Chrome: {chrome_version})。\n"
                    f"本地驱动错误: {last_local_error}\n\n"
                    "请确保Chrome版本较新，或手动下载chromedriver并放到drivers/目录"
                )

            self._report(12, 100, f"匹配驱动版本: {matched_version}", {})

            # 3. 从npmmirror下载
            driver_path = self._download_chromedriver_npmirror(matched_version)
            if not driver_path:
                raise RuntimeError(
                    f"ChromeDriver下载失败 (版本: {matched_version})。\n"
                    f"本地驱动错误: {last_local_error}\n\n"
                    "请检查网络连接，或手动下载chromedriver并放到drivers/目录"
                )

            try:
                service = ChromeService(driver_path)
                self._driver = self._create_selenium_driver(service)
            except Exception as e:
                StandardQueryService._shared_selenium_init_error = (
                    "ChromeDriver初始化失败。\n"
                    f"本地驱动错误: {last_local_error}\n"
                    f"自动下载错误: {e}\n\n"
                    "请确保:\n"
                    "1. 已安装Google Chrome浏览器\n"
                    "2. 可用的ChromeDriver版本与浏览器兼容\n"
                    "3. 或手动下载chromedriver并放到drivers/目录"
                )
                self._selenium_available = False
                raise RuntimeError(
                    StandardQueryService._shared_selenium_init_error
                )

        self._driver.set_page_load_timeout(30)
        self._driver.implicitly_wait(self._default_implicit_wait)

        # 保存到共享变量
        with StandardQueryService._shared_driver_lock:
            StandardQueryService._shared_driver = self._driver

        logger.info("WebDriver初始化成功")

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

    def _check_playwright(self) -> bool:
        """检查Playwright是否可用"""
        if self._playwright_available is not None:
            return self._playwright_available

        if not PLAYWRIGHT_ENABLED:
            logger.info(PLAYWRIGHT_DISABLED_MESSAGE)
            self._playwright_available = False
            return False

        try:
            from playwright.async_api import async_playwright
            self._playwright_available = True
            logger.info("Playwright检查通过")
            return True
        except ImportError as e:
            logger.info(f"Playwright未安装，已自动回退到Selenium: {e}")
            self._playwright_available = False
            return False

    async def close(self):
        """关闭当前实例的 Playwright 资源。"""
        try:
            if self._context is not None:
                await asyncio.wait_for(self._context.close(), timeout=5)
        except Exception:
            pass
        try:
            if self._browser is not None:
                await asyncio.wait_for(self._browser.close(), timeout=5)
        except Exception:
            pass
        try:
            if self._playwright is not None:
                await asyncio.wait_for(self._playwright.stop(), timeout=5)
        except Exception:
            pass

        self._browser = None
        self._context = None
        self._playwright = None

        # Selenium 仍然保留共享 driver
        self._driver = None

    async def query_single(
        self, standard_number: str, platform: str = "hunan", auto_switch: bool = True
    ) -> Dict:
        """查询单个标准，支持同平台引擎回退和跨平台切换。

        规则:
        1. 优先使用 Playwright
        2. Playwright 失败后回退 Selenium
        3. 若当前平台失败且 `auto_switch=True`，继续尝试下一个平台
        """
        if platform not in self.query_platforms:
            raise ValueError(
                f"未知平台: {platform}\n可用平台: {', '.join(self.query_platforms.keys())}"
            )

        tried_platforms = []
        current_platform = platform
        current_query = standard_number
        relaxed_query_used = False
        bootstrap_errors = []

        try:
            while True:
                self._raise_if_cancelled()
                browser_bootstrap_failed = False
                # 首先尝试Playwright
                if self._check_playwright():
                    try:
                        logger.info("优先使用Playwright查询")
                        return await self._query_with_playwright(
                            current_query, current_platform
                        )
                    except Exception as e:
                        if self._is_browser_bootstrap_error(e):
                            browser_bootstrap_failed = True
                            bootstrap_errors.append(("playwright", current_platform, str(e)))
                        logger.warning(f"Playwright查询失败，尝试Selenium: {e}")

                # 回退到Selenium
                if self._check_selenium():
                    try:
                        logger.info("使用Selenium查询")
                        loop = asyncio.get_event_loop()
                        result = await loop.run_in_executor(
                            None,
                            self._query_with_selenium,
                            current_query,
                            current_platform,
                        )
                        return result
                    except Exception as e:
                        if self._is_browser_bootstrap_error(e):
                            browser_bootstrap_failed = True
                            bootstrap_errors.append(("selenium", current_platform, str(e)))
                        logger.error(f"Selenium查询失败: {e}")

                if browser_bootstrap_failed:
                    # 清理共享状态，允许下次查询重新初始化
                    StandardQueryService.shutdown_shared_selenium()

                    if auto_switch:
                        tried_platforms.append(current_platform)
                        available = [
                            p for p in self.query_platforms.keys() if p not in tried_platforms
                        ]
                        if available:
                            next_platform = available[0]
                            logger.warning(
                                "平台 %s 浏览器初始化失败，切换到 %s 继续尝试",
                                current_platform,
                                next_platform,
                            )
                            self._report(
                                0,
                                100,
                                f"源 {current_platform} 浏览器初始化失败，切换到 {next_platform}...",
                                {
                                    "platform": current_platform,
                                    "next_platform": next_platform,
                                },
                            )
                            current_platform = next_platform
                            continue

                    last_engine, last_platform, last_error = (
                        bootstrap_errors[-1]
                        if bootstrap_errors
                        else ("unknown", current_platform, "unknown")
                    )
                    raise RuntimeError(
                        "浏览器初始化失败，无法继续查询。\n"
                        f"最后失败平台: {last_platform}（{last_engine}）\n"
                        f"错误详情: {last_error}\n"
                        "请检查 Chrome、ChromeDriver 与 Playwright 浏览器是否完整。"
                    )

                if not auto_switch:
                    break

                tried_platforms.append(current_platform)

                if (not relaxed_query_used) and len(tried_platforms) >= 3:
                    relaxed_query = self._relax_standard_query(standard_number)
                    if relaxed_query and relaxed_query != current_query:
                        current_query = relaxed_query
                        relaxed_query_used = True
                        logger.warning(
                            f"前3个平台未命中，切换到去标头关键词: {standard_number} -> {relaxed_query}"
                        )
                        self._report(
                            0,
                            100,
                            f"前3个平台未命中，改用 {relaxed_query} 继续查询...",
                            {"original": standard_number, "relaxed": relaxed_query},
                        )

                available = [
                    p for p in self.query_platforms.keys() if p not in tried_platforms
                ]
                if not available:
                    break

                next_platform = available[0]
                logger.warning(
                    f"平台 {current_platform} 失败，切换到 {next_platform}"
                )
                current_platform = next_platform

            raise RuntimeError(
                f"查询标准 {standard_number} 失败：已尝试平台 {', '.join(tried_platforms or [platform])}"
            )
        finally:
            await self.close()

    async def _query_with_playwright(self, standard_number: str, platform: str) -> Dict:
        """使用Playwright查询标准"""
        # 检查平台是否有效
        if platform not in self.query_platforms:
            raise ValueError(
                f"未知平台: {platform}\n可用平台: {', '.join(self.query_platforms.keys())}"
            )

        await self._init_playwright()
        config = self.query_platforms[platform]
        platform_name = config["name"]

        if config.get("need_format", False):
            formatted_number = format_standard_code(standard_number)
            logger.info(
                f"平台 {platform} 需要格式化: {standard_number} -> {formatted_number}"
            )
            input_number = formatted_number
        else:
            input_number = standard_number

        self._report(
            12,
            100,
            "创建查询页面...",
            {"platform": platform, "standard": standard_number},
        )
        page = await asyncio.wait_for(self._context.new_page(), timeout=20)

        try:
            self._raise_if_cancelled()
            self._report(
                20,
                100,
                f"访问 {platform_name}...",
                {"platform": platform, "standard": standard_number},
            )

            await page.goto(
                config["url"], wait_until="domcontentloaded", timeout=60000
            )
            self._raise_if_cancelled()

            self._report(
                40,
                100,
                f"正在搜索 {standard_number}...",
                {"platform": platform, "standard": standard_number},
            )

            await page.fill(config["input_selector"], input_number)
            self._raise_if_cancelled()

            if config.get("type") == "link":
                try:
                    await page.click("#iscontain", timeout=3000)
                except:
                    pass

            await page.click(config["search_selector"])
            await self._wait_for_playwright_selector(page, config["result_selector"], timeout=20)
            if config.get("type") == "link":
                await page.wait_for_timeout(5000)

            self._report(
                60,
                100,
                f"找到结果，进入详情页...",
                {"platform": platform, "standard": standard_number},
            )

            best_result = await self._pick_best_playwright_candidate(
                page,
                config["result_selector"],
                standard_number,
                input_number,
            )
            if not best_result:
                raise RuntimeError("未找到结果链接")

            if config.get("type") == "link":
                href = await best_result.get_attribute("href")
                if not href:
                    raise RuntimeError("结果链接无href")
                if href.startswith("/"):
                    from urllib.parse import urlparse
                    parsed = urlparse(config["url"])
                    href = f"{parsed.scheme}://{parsed.netloc}{href}"
                detail_page = await self._context.new_page()
                await detail_page.goto(
                    href, wait_until="domcontentloaded", timeout=60000
                )
                try:
                    await detail_page.wait_for_selector("#a100", timeout=15000)
                except Exception:
                    pass
            else:
                async with self._context.expect_page() as page_info:
                    await best_result.click()
                detail_page = await page_info.value
                await detail_page.wait_for_load_state("domcontentloaded")

            self._report(
                80,
                100,
                f"提取标准信息...",
                {"platform": platform, "standard": standard_number},
            )

            if config.get("type") == "link":
                info = await self._extract_standard_info_link(
                    detail_page, platform_name
                )
            else:
                info = await self._extract_standard_info_playwright(
                    detail_page, platform_name
                )

            await detail_page.close()
            await page.close()

            self._report(
                100,
                100,
                f"查询完成",
                {"platform": platform, "standard": standard_number},
            )

            result = self._to_dict(info)
            if result.get("resource", "").endswith("(模拟)"):
                raise RuntimeError(
                    f"查询 {standard_number} 返回了模拟数据，可能是页面解析失败。"
                )
            return result

        except Exception as e:
            logger.error(
                f"Playwright查询失败 {standard_number} @ {platform}: {e}"
            )
            await page.close()
            raise

    def _selenium_locator(self, selector: str) -> tuple:
        """将选择器转换为 Selenium locator。

        绝大多数站点都可以直接使用 CSS 选择器。只有显式传入 XPath 时才切换。
        """
        from selenium.webdriver.common.by import By

        selector = (selector or "").strip()
        if selector.startswith("//") or selector.startswith("("):
            return (By.XPATH, selector)
        return (By.CSS_SELECTOR, selector)

    def _get_selenium_input_selectors(self, platform: str, config: Dict) -> List[str]:
        """获取 Selenium 输入框候选选择器，按可见性和平台特性排序。"""
        selectors: List[str] = []

        platform_specific = config.get("selenium_input_selectors", [])
        if isinstance(platform_specific, str):
            platform_specific = [platform_specific]
        selectors.extend(platform_specific)

        base_selector = config.get("input_selector")
        if base_selector:
            selectors.append(base_selector)

        if platform == "hunan":
            # 湖南平台当前可见输入框通常是 #txtStd。
            # 先尝试它，避免先等 #txtNo 超时造成 10+ 秒停顿。
            selectors = ["#txtStd", "#txtNo"] + selectors

        seen = set()
        ordered = []
        for selector in selectors:
            if selector and selector not in seen:
                seen.add(selector)
                ordered.append(selector)
        return ordered

    def _wait_for_document_ready(self, timeout: int = 20):
        """等待页面进入可交互状态。"""
        from selenium.webdriver.support.ui import WebDriverWait

        WebDriverWait(self._driver, timeout).until(
            lambda d: d.execute_script("return document.readyState")
            in ("interactive", "complete")
        )

    def _wait_for_selenium_elements(self, selector: tuple, timeout: int = 20):
        """可取消的 Selenium 元素等待。"""
        from selenium.common.exceptions import NoSuchElementException

        by, loc = selector
        deadline = time.monotonic() + timeout
        with self._temporary_implicit_wait(self._probe_implicit_wait):
            while time.monotonic() < deadline:
                self._raise_if_cancelled()
                try:
                    elements = self._driver.find_elements(by, loc)
                    if elements:
                        return elements
                except NoSuchElementException:
                    pass
                time.sleep(0.25)
        raise TimeoutError(f"等待选择器超时: {loc}")

    def _fill_selenium_input(self, element, value: str):
        """稳定地向输入框写入文本。"""
        try:
            element.clear()
        except Exception:
            pass

        try:
            element.click()
        except Exception:
            pass

        try:
            element.send_keys(value)
            return
        except Exception:
            pass

        try:
            self._driver.execute_script(
                """
                const el = arguments[0];
                const value = arguments[1];
                el.value = value;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                """,
                element,
                value,
            )
        except Exception as e:
            raise RuntimeError(f"输入框写入失败: {e}")

    def _sync_hunan_search_fields(self, value: str):
        """湖南平台提交时会校验隐藏/高级字段 txtNo，需和可见输入框保持同步。"""
        try:
            self._driver.execute_script(
                """
                const value = arguments[0];
                for (const id of ['txtNo', 'txtStd']) {
                    const el = document.getElementById(id);
                    if (!el) continue;
                    el.value = value;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }
                """,
                value,
            )
        except Exception as e:
            raise RuntimeError(f"湖南平台查询字段同步失败: {e}")

    def _query_with_selenium(self, standard_number: str, platform: str) -> Dict:
        """使用Selenium查询标准 - 基于原始脚本重写"""
        import time
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        config = self.query_platforms[platform]
        platform_name = config["name"]
        platform_type = config.get("type", "popup")
        logger.info(f"[QUERY] 平台: {platform}, 类型: {platform_type}")

        self._report(0, 100, f"初始化浏览器...", {"standard": standard_number, "platform": platform_name})
        self._init_selenium()

        url = config["url"]
        self._report(10, 100, f"打开 {platform_name}", {"standard": standard_number, "platform": platform_name})

        if config.get("need_format", False):
            formatted_number = format_standard_code(standard_number)
            logger.info(f"平台 {platform} 需要格式化: {standard_number} -> {formatted_number}")
            input_number = formatted_number
        else:
            input_number = standard_number

        logger.info(f"[QUERY] 访问URL: {url}")
        page_load_timeout = config.get(
            "selenium_page_load_timeout", 90 if platform == "liaocheng" else 45
        )
        self._driver.set_page_load_timeout(page_load_timeout)
        try:
            self._driver.get(url)
        except TimeoutException:
            logger.warning(
                f"[QUERY] 页面加载超时，已中止继续渲染: {platform_name}"
            )
            try:
                self._driver.execute_script("window.stop();")
            except Exception:
                pass
        self._hide_selenium_window(self._driver)
        try:
            self._wait_for_document_ready(timeout=20 if platform == "liaocheng" else 12)
        except TimeoutException:
            logger.warning(f"[QUERY] 页面未完全进入可交互状态，继续尝试: {platform_name}")
        self._raise_if_cancelled()
        time.sleep(0.3)

        self._report(20, 100, f"输入标准号: {input_number}", {"standard": standard_number})

        input_selector_candidates = self._get_selenium_input_selectors(
            platform, config
        )
        search_selector = config["search_selector"]
        result_selector = config["result_selector"]

        try:
            standard_number_input = None
            used_selector = None
            last_error = None
            for input_selector in input_selector_candidates:
                input_by, input_loc = self._selenium_locator(input_selector)
                logger.info(f"[QUERY] 尝试输入框选择器: {input_by} = {input_loc}")
                try:
                    standard_number_input = WebDriverWait(self._driver, 12).until(
                        EC.visibility_of_element_located((input_by, input_loc))
                    )
                    used_selector = input_selector
                    break
                except Exception as e:
                    last_error = e

            if standard_number_input is None:
                raise RuntimeError(
                    "未找到可见输入框，已尝试: "
                    f"{', '.join(input_selector_candidates) or '无候选选择器'}"
                ) from last_error

            self._fill_selenium_input(standard_number_input, input_number)
            if platform == "hunan":
                self._sync_hunan_search_fields(input_number)
            time.sleep(0.2)
            logger.info(f"[QUERY] 输入标准号: {input_number} (使用 {used_selector})")
            self._raise_if_cancelled()
        except Exception as e:
            logger.error(f"[QUERY] 输入失败: {e}")
            raise

        self._report(30, 100, f"搜索: {input_number}", {"standard": standard_number, "platform": platform_name})

        try:
            search_by, search_loc = self._selenium_locator(search_selector)
            logger.info(f"[QUERY] 搜索按钮选择器: {search_by} = {search_loc}")
            
            search_button = WebDriverWait(self._driver, 20).until(
                EC.element_to_be_clickable((search_by, search_loc))
            )
            try:
                search_button.click()
            except Exception:
                self._driver.execute_script("arguments[0].click();", search_button)
            logger.info(f"[QUERY] 点击搜索按钮成功")
        except Exception as e:
            logger.error(f"[QUERY] 点击搜索失败: {e}")
            raise

        self._report(50, 100, f"查找结果...", {"standard": standard_number, "platform": platform_name})

        # 给结果区域一个很短的缓冲，后续主要依赖显式等待而不是固定长 sleep
        time.sleep(1.2 if platform == "liaocheng" else 0.8)
        try:
            self._wait_for_document_ready(timeout=20 if platform == "liaocheng" else 12)
        except TimeoutException:
            logger.warning(f"[QUERY] 结果页未完全就绪，继续尝试: {platform_name}")
        self._raise_if_cancelled()

        # 根据平台类型处理结果
        try:
            result_by, result_loc = self._selenium_locator(result_selector)
            logger.info(f"[QUERY] 结果选择器: {result_by} = {result_loc}")

            result_wait_timeout = config.get(
                "selenium_result_wait_timeout", 45 if platform == "liaocheng" else 20
            )
            elements = self._wait_for_selenium_elements(
                (result_by, result_loc), timeout=result_wait_timeout
            )
            element = self._pick_best_selenium_candidate(
                elements, standard_number, input_number
            )
            if not element:
                raise RuntimeError("未找到结果链接")

            if platform_type == "link":
                # link类型：获取href并导航
                href_value = element.get_attribute("href")
                logger.info(f"[QUERY] 获取结果链接: {href_value}")

                # 导航到详情页
                try:
                    self._driver.get(href_value)
                except TimeoutException:
                    logger.warning(f"[QUERY] 详情页加载超时，继续提取: {platform_name}")
                    try:
                        self._driver.execute_script("window.stop();")
                    except Exception:
                        pass
                self._hide_selenium_window(self._driver)
                try:
                    self._wait_for_document_ready(
                        timeout=20 if platform == "liaocheng" else 12
                    )
                except TimeoutException:
                    logger.warning(f"[QUERY] 详情页未完全就绪，继续提取: {platform_name}")
                self._raise_if_cancelled()
                time.sleep(0.5)

                info = self._extract_standard_info_selenium_link(
                    standard_number, platform_name
                )
            else:
                # popup类型：优先直接使用详情页 href，避免 target=_blank 在不同驱动下行为不稳定。
                href_value = element.get_attribute("href")
                if href_value:
                    if href_value.startswith("/"):
                        from urllib.parse import urlparse

                        parsed = urlparse(url)
                        href_value = f"{parsed.scheme}://{parsed.netloc}{href_value}"
                    logger.info(f"[QUERY] popup结果直接访问详情页: {href_value}")
                    try:
                        self._driver.get(href_value)
                    except TimeoutException:
                        logger.warning(f"[QUERY] popup详情页加载超时，继续提取: {platform_name}")
                        try:
                            self._driver.execute_script("window.stop();")
                        except Exception:
                            pass
                    self._hide_selenium_window(self._driver)
                    try:
                        self._wait_for_document_ready(
                            timeout=20 if platform == "liaocheng" else 12
                        )
                    except TimeoutException:
                        logger.warning(f"[QUERY] popup详情页未完全就绪，继续提取: {platform_name}")
                    self._raise_if_cancelled()
                    time.sleep(0.5)
                    info = self._extract_standard_info_selenium(
                        standard_number, platform_name
                    )
                else:
                    WebDriverWait(self._driver, 20).until(
                        EC.element_to_be_clickable((result_by, result_loc))
                    )
                    self._driver.execute_script("arguments[0].click();", element)
                    logger.info(f"[QUERY] 点击结果成功")

                    # 等待新窗口打开
                    WebDriverWait(self._driver, 20).until(
                        lambda d: len(d.window_handles) > 1
                    )
                    original_window = self._driver.current_window_handle
                    new_window = [
                        window
                        for window in self._driver.window_handles
                        if window != original_window
                    ][0]
                    self._driver.switch_to.window(new_window)
                    self._hide_selenium_window(self._driver)
                    logger.info(f"[QUERY] 切换到新窗口")

                    try:
                        self._wait_for_document_ready(
                            timeout=20 if platform == "liaocheng" else 12
                        )
                    except TimeoutException:
                        logger.warning(f"[QUERY] 新窗口未完全就绪，继续提取: {platform_name}")
                    self._raise_if_cancelled()
                    time.sleep(0.5)

                    info = self._extract_standard_info_selenium(
                        standard_number, platform_name
                    )

                    # 关闭新窗口
                    self._driver.close()
                    self._driver.switch_to.window(original_window)

        except Exception as e:
            if platform_type == "link":
                logger.error(f"[QUERY] link类型查询失败: {e}")
            else:
                logger.error(f"[QUERY] popup类型查询失败: {e}")
            raise

        self._report(90, 100, f"完成: {standard_number}", {"standard": standard_number, "platform": platform_name})

        result = self._to_dict(info)
        return result

    async def _extract_standard_info_playwright(self, page, source: str) -> StandardInfo:
        """使用Playwright提取标准详情"""
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

        try:
            await page.wait_for_selector("#lblStdNo", timeout=15000)
        except Exception:
            pass

        for elem_id, field_name in field_ids:
            try:
                elem = await page.query_selector(f"#{elem_id}")
                if elem:
                    data[field_name] = await elem.inner_text()
                else:
                    data[field_name] = ""
            except Exception as e:
                data[field_name] = ""
                if field_name == "standard_status":
                    logger.warning(f"[QUERY] 标准状态提取失败: {e}")

        return StandardInfo(**data)

    async def _extract_standard_info_link(self, page, source: str) -> StandardInfo:
        """使用Playwright提取标准详情 - link类型平台"""
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

        try:
            await page.wait_for_selector("#a100", timeout=15000)
        except Exception:
            pass

        for elem_id, field_name in field_ids:
            try:
                elem = await page.query_selector(f"#{elem_id}")
                if elem:
                    data[field_name] = await elem.inner_text()
                else:
                    data[field_name] = ""
            except Exception as e:
                data[field_name] = ""
                if field_name == "standard_status":
                    logger.warning(f"[QUERY] 标准状态提取失败: {e}")

        return StandardInfo(**data)

    def _extract_standard_info_selenium(self, standard_number: str, source: str) -> StandardInfo:
        """使用Selenium提取标准详情"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        import time

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

        # 等待页面完全加载 - 特别是标准状态字段
        try:
            WebDriverWait(self._driver, 10).until(
                EC.presence_of_element_located((By.ID, "lblState"))
            )
            # 额外等待确保动态内容加载完成
            time.sleep(1)
        except Exception as e:
            logger.warning(f"[QUERY] 等待lblState元素超时: {e}")

        # 调试：查找所有包含lbl的元素
        try:
            with self._temporary_implicit_wait(self._probe_implicit_wait):
                all_labels = self._driver.find_elements(By.CSS_SELECTOR, "[id^='lbl']")
            logger.info(f"[QUERY] 找到 {len(all_labels)} 个lbl元素:")
            for lbl in all_labels[:15]:  # 只显示前15个
                elem_id = lbl.get_attribute('id')
                # 使用get_attribute('innerText')获取更完整的文本
                text = lbl.get_attribute('innerText') or ''
                logger.info(f"[QUERY]   {elem_id}: {text[:50] if text else '(empty)'}")
        except Exception as e:
            logger.warning(f"[QUERY] 查找lbl元素失败: {e}")

        for elem_id, field_name in field_ids:
            try:
                elem = self._driver.find_element(By.ID, elem_id)
                # 使用innerText获取更完整的文本，包括嵌套元素
                text = elem.get_attribute('innerText')
                if text:
                    data[field_name] = text.strip()
                else:
                    data[field_name] = elem.text.strip()

                if field_name == "standard_status":
                    logger.info(f"[QUERY] 标准状态提取: '{data[field_name]}'")
            except Exception as e:
                data[field_name] = ""
                if field_name == "standard_status":
                    logger.warning(f"[QUERY] 标准状态提取失败: {e}")

        return StandardInfo(**data)

    def _extract_standard_info_selenium_link(self, standard_number: str, source: str) -> StandardInfo:
        """使用Selenium提取标准详情 - link类型平台"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

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

        data = {"resource": source, "standard_number": standard_number}

        # 等待标准号元素加载
        try:
            WebDriverWait(self._driver, 30).until(
                EC.presence_of_element_located((By.ID, "a100"))
            )
        except Exception as e:
            logger.warning(f"[QUERY] 等待a100元素超时: {e}")

        for elem_id, field_name in field_ids:
            try:
                elem = self._driver.find_element(By.ID, elem_id)
                text = elem.get_attribute('innerText')
                if text:
                    data[field_name] = text.strip()
                else:
                    data[field_name] = elem.text.strip()

                if field_name == "standard_status":
                    logger.info(f"[QUERY] 标准状态提取: '{data[field_name]}'")
            except Exception as e:
                data[field_name] = ""
                if field_name == "standard_status":
                    logger.warning(f"[QUERY] 标准状态提取失败: {e}")

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
            for k, v in self.query_platforms.items()
        }

    async def health_check(self) -> Tuple[bool, str]:
        """健康检查

        返回:
            (is_healthy, message)
        """
        try:
            if self._check_playwright():
                await self._init_playwright()
                await self.close()
                return True, "Playwright服务正常"
            elif self._check_selenium():
                self._init_selenium()
                self.close()
                return True, "Selenium服务正常"
            else:
                return False, "Playwright和Selenium都不可用"
        except Exception as e:
            return False, f"健康检查失败: {e}"
