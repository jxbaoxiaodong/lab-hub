"""
标准自动下载器 - 修复版
只保留食品伙伴网、国家标准全文公开系统，以及文章中列出的官方/公开标准来源。
"""

import asyncio
from contextlib import contextmanager
import os
import re
import logging
import subprocess
import shutil
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)
ALLOW_INSECURE_TLS = os.environ.get("LAB_ALLOW_INSECURE_TLS", "0") == "1"

# 明确不可用的下载源，遇到旧入口时直接忽略
DISABLED_DOWNLOAD_SOURCES = {"renren", "wenku_baidu", "doc88", "docin"}

# 这些源当前更适合作为“官方入口”快速返回，而不是继续在页面里深挖候选链接。
ENTRY_ONLY_DOWNLOAD_SOURCES = {"mee_hj"}


@dataclass
class DownloadResult:
    """下载结果"""
    standard_number: str
    success: bool
    file_path: Optional[str] = None
    download_url: Optional[str] = None
    source: str = ""
    message: str = ""
    file_size: int = 0


# 国内镜像配置（按优先级排序：速度快 → 兜底）
# 优先使用国内镜像，本地服务作为最后兜底
CHROME_DOWNLOAD_MIRRORS = [
    "https://registry.npmmirror.com/-/binary/chrome-for-testing/",  # 淘宝镜像（首选，国内最快）
    "https://mirrors.huaweicloud.com/chrome-for-testing/",          # 华为云镜像（备用）
    "https://mirrors.aliyun.com/chrome-for-testing/",               # 阿里云镜像（备用）
    "http://labmumu.ftir.fun:7832/chrome-binaries/",                # 本地服务（兜底）
]

# 保留兼容性
NPMMIRROR_BASE = CHROME_DOWNLOAD_MIRRORS[0]


def _build_ssl_context():
    import ssl

    ctx = ssl.create_default_context()
    if ALLOW_INSECURE_TLS:
        logger.warning("LAB_ALLOW_INSECURE_TLS 已启用，下载模块将跳过 TLS 证书校验")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _safe_extract_zip_archive(zip_file, target_dir: Path):
    root = target_dir.resolve()
    for member in zip_file.infolist():
        member_name = (member.filename or "").replace("\\", "/")
        if not member_name or member_name.endswith("/"):
            continue

        target_path = (root / member_name).resolve()
        try:
            target_path.relative_to(root)
        except Exception as exc:
            raise RuntimeError(f"压缩包包含非法路径: {member.filename}") from exc

        target_path.parent.mkdir(parents=True, exist_ok=True)
        with zip_file.open(member) as source:
            with open(target_path, "wb") as target:
                target.write(source.read())


def _get_chrome_version() -> Optional[str]:
    """获取本机Chrome版本号"""
    chrome_paths = [
        "google-chrome",
        "chrome",
        "chromium",
        "chromium-browser",
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    ]
    # Windows 用户目录安装路径（常见安装位置）
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        user_profile = os.path.expanduser("~")
        chrome_paths.extend([
            Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe" if local_app_data else None,
            Path(user_profile) / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path("C:") / "Users" / os.environ.get("USERNAME", "") / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe",
            # 通过注册表查找（如果以上都失败）
            _get_chrome_path_from_registry(),
        ])
        # 过滤 None
        chrome_paths = [p for p in chrome_paths if p is not None]

    for chrome_path in chrome_paths:
        try:
            if not Path(chrome_path).exists():
                continue
            # Windows 使用 gbk 编码避免中文乱码
            kwargs = {"encoding": "gbk", "errors": "ignore"} if sys.platform == "win32" else {"text": True}
            result = subprocess.run(
                [str(chrome_path), "--version"],
                capture_output=True,
                timeout=10,
                **kwargs
            )
            output = result.stdout if isinstance(result.stdout, str) else result.stdout.decode("utf-8", errors="ignore")
            if result.returncode == 0:
                match = re.search(r"Chrome[\s/]?(\d+)\.(\d+)\.(\d+)\.(\d+)", output)
                if match:
                    return f"{match.group(1)}.{match.group(2)}.{match.group(3)}.{match.group(4)}"
        except Exception:
            continue
    return None


def _get_chrome_path_from_registry() -> Optional[Path]:
    """从Windows注册表获取Chrome安装路径"""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        # 尝试读取注册表
        keys_to_try = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
        ]
        for hkey, key_path in keys_to_try:
            try:
                with winreg.OpenKey(hkey, key_path) as key:
                    value, _ = winreg.QueryValueEx(key, None)
                    if value and Path(value).exists():
                        return Path(value)
            except Exception:
                continue
    except Exception:
        pass
    return None


def _get_latest_matching_version(chrome_version: str) -> Optional[str]:
    """从npmmirror获取与Chrome版本匹配的最新可用ChromeDriver版本"""
    import urllib.request
    import ssl
    import json

    base_url = f"{NPMMIRROR_BASE}chrome-for-testing/"

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

        versions = []
        for item in data:
            name = item.get("name", "")
            if name.endswith("/"):
                version = name.rstrip("/")
                if re.match(r"^\d+\.\d+\.\d+\.\d+$", version):
                    versions.append(version)

        if not versions:
            return None

        def version_key(v):
            parts = v.split(".")
            return tuple(int(p) for p in parts)

        versions.sort(key=version_key, reverse=True)

        chrome_major = chrome_version.split(".")[0] if chrome_version else ""

        for v in versions:
            if v.startswith(chrome_major + "."):
                return v

        return versions[0] if versions else None

    except Exception as e:
        logger.warning(f"获取版本列表失败: {e}")
        return None


def _download_with_fallback(urls: List[str], timeout: Optional[int] = None, 
                            desc: str = "文件") -> Optional[bytes]:
    """
    多镜像源下载，自动切换
    
    Args:
        urls: 镜像URL列表（按优先级）
        timeout: 超时时间（秒），None表示使用系统默认（通常300秒）
        desc: 描述，用于日志
        
    Returns:
        下载的数据，全部失败返回None
    """
    import urllib.request
    import ssl
    import socket
    
    # 设置默认socket超时（如果未指定）
    original_timeout = socket.getdefaulttimeout()
    if timeout:
        socket.setdefaulttimeout(timeout)
    
    try:
        ssl_context = _build_ssl_context()
    except Exception:
        ssl_context = None
    
    last_error = None
    for i, url in enumerate(urls):
        mirror_name = f"镜像{i+1}/{len(urls)}"
        try:
            logger.info(f"[{desc}] 尝试从{mirror_name}下载: {url}")
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )
            
            # 使用较长的超时时间，允许大文件下载
            # 连接超时60秒，读取无限制（大文件需要时间长）
            if ssl_context:
                response = urllib.request.urlopen(req, timeout=60, context=ssl_context)
            else:
                response = urllib.request.urlopen(req, timeout=60)
            
            # 分块读取，避免内存问题，同时支持大文件
            chunk_size = 1024 * 1024  # 1MB
            data = bytearray()
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                data.extend(chunk)
            
            logger.info(f"[{desc}] 从{mirror_name}下载成功: {len(data)} bytes")
            return bytes(data)
            
        except Exception as e:
            last_error = e
            logger.warning(f"[{desc}] {mirror_name}失败: {e}")
            continue
    
    # 恢复默认超时
    socket.setdefaulttimeout(original_timeout)
    
    logger.error(f"[{desc}] 所有镜像源都失败，最后错误: {last_error}")
    return None


def _download_chrome_npmirror(version: str, base_dir: Path, timeout: Optional[int] = None) -> Optional[str]:
    """多镜像源下载Chrome浏览器（完整版），自动切换备用源"""
    import zipfile
    import io

    if sys.platform == "win32":
        platform = "win64"
        chrome_binary = "chrome-win64/chrome.exe"
    elif sys.platform == "darwin":
        platform = "mac-x64"
        chrome_binary = "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    else:
        platform = "linux64"
        chrome_binary = "chrome-linux64/chrome"

    # 构建所有镜像URL（适配不同镜像源的目录结构）
    urls = []
    for mirror_base in CHROME_DOWNLOAD_MIRRORS:
        # 跳过注释掉的 Gitee 源
        if mirror_base.strip().startswith("#"):
            continue
            
        # 处理不同镜像的URL格式
        if "npmmirror" in mirror_base:
            # 淘宝: https://registry.npmmirror.com/-/binary/chrome-for-testing/{version}/{platform}/chrome-{platform}.zip
            url = f"{mirror_base}{version}/{platform}/chrome-{platform}.zip"
        elif "huaweicloud" in mirror_base:
            # 华为云: https://mirrors.huaweicloud.com/chrome-for-testing/{version}/{platform}/chrome-{platform}.zip
            url = f"{mirror_base}{version}/{platform}/chrome-{platform}.zip"
        elif "aliyun" in mirror_base:
            # 阿里云: https://mirrors.aliyun.com/chrome-for-testing/{version}/{platform}/chrome-{platform}.zip
            url = f"{mirror_base}{version}/{platform}/chrome-{platform}.zip"
        elif "labmumu" in mirror_base:
            # 项目自有域名: https://labmumu.ftir.fun/chrome-binaries/{version}/chrome-{platform}.zip
            url = f"{mirror_base}{version}/chrome-{platform}.zip"
        else:
            # 默认格式
            url = f"{mirror_base}{version}/{platform}/chrome-{platform}.zip"
        urls.append(url)
    
    logger.info(f"准备下载Chrome {version}（完整版），共{len(urls)}个镜像源")
    
    # 下载数据
    zip_data = _download_with_fallback(urls, timeout=timeout, desc=f"Chrome-{version}")
    if not zip_data:
        return None
    
    try:
        chrome_dir = base_dir / "drivers" / "chrome"
        chrome_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            _safe_extract_zip_archive(zf, chrome_dir)

        chrome_path = chrome_dir / chrome_binary
        if chrome_path.exists():
            if sys.platform != "win32":
                chrome_path.chmod(0o755)
            logger.info(f"Chrome已解压到: {chrome_path}")
            return str(chrome_path)

        # 尝试查找解压后的 chrome 可执行文件
        for pattern in ["**/chrome.exe", "**/chrome", "**/Google Chrome*"]:
            found = list(chrome_dir.glob(pattern))
            if found:
                if sys.platform != "win32":
                    found[0].chmod(0o755)
                return str(found[0])

        return None

    except Exception as e:
        logger.error(f"解压Chrome失败: {e}")
        return None


def _download_chromedriver_with_fallback(version: str, base_dir: Path, timeout: Optional[int] = None) -> Optional[str]:
    """多镜像源下载ChromeDriver，自动切换备用源"""
    import zipfile
    import io

    if sys.platform == "win32":
        platform = "win64"
        ext = ".exe"
    elif sys.platform == "darwin":
        platform = "mac-x64"
        ext = ""
    else:
        platform = "linux64"
        ext = ""

    # 构建所有镜像URL
    urls = []
    for mirror_base in CHROME_DOWNLOAD_MIRRORS:
        if mirror_base.strip().startswith("#"):
            continue
            
        # 处理不同镜像的URL格式
        if "npmmirror" in mirror_base:
            url = f"{mirror_base}{version}/{platform}/chromedriver-{platform}.zip"
        elif "huaweicloud" in mirror_base:
            url = f"{mirror_base}{version}/{platform}/chromedriver-{platform}.zip"
        elif "aliyun" in mirror_base:
            url = f"{mirror_base}{version}/{platform}/chromedriver-{platform}.zip"
        elif "labmumu" in mirror_base:
            url = f"{mirror_base}{version}/chromedriver-{platform}.zip"
        else:
            url = f"{mirror_base}{version}/{platform}/chromedriver-{platform}.zip"
        urls.append(url)
    
    logger.info(f"准备下载ChromeDriver {version}，共{len(urls)}个镜像源")
    
    # 下载数据
    zip_data = _download_with_fallback(urls, timeout=timeout, desc=f"ChromeDriver-{version}")
    if not zip_data:
        return None
    try:
        drivers_dir = base_dir / "drivers"
        drivers_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            for member in zf.namelist():
                # 找chromedriver可执行文件（不在目录中）
                if "/chromedriver" in member and not member.endswith("/"):
                    # 去掉前缀目录
                    filename = member.split("/")[-1]
                    if ext and not filename.endswith(ext):
                        filename = filename + ext
                    target_path = drivers_dir / filename
                    with zf.open(member) as source:
                        with open(target_path, "wb") as target:
                            target.write(source.read())
                    if sys.platform != "win32":
                        target_path.chmod(0o755)
                    logger.info(f"ChromeDriver已解压到: {target_path}")
                    return str(target_path)

        return None

    except Exception as e:
        logger.error(f"解压ChromeDriver失败: {e}")
        return None


# 保留旧函数名兼容性
def _download_chromedriver_npmirror(version: str, base_dir: Path, timeout: int = 180) -> Optional[str]:
    """使用npmmirror下载ChromeDriver（兼容旧代码，实际使用多镜像源）"""
    return _download_chromedriver_with_fallback(version, base_dir, timeout=timeout)


def _make_source(
    name: str,
    url: str,
    search_url: str = None,
    source_type: str = "official",
    standard_type: Optional[List[str]] = None,
    priority: int = 100,
    notes: str = "",
    method: str = "open_page",
) -> Dict:
    return {
        "name": name,
        "url": url,
        "search_url": search_url,
        "type": source_type,
        "standard_type": standard_type or ["all"],
        "priority": priority,
        "notes": notes,
        "method": method,
    }


# 文章中的公开/官方来源，按分类路由到优先平台
DOWNLOAD_SOURCES = {
    # 核心源
    "gb_openstd": _make_source(
        "国家标准全文公开系统",
        "https://openstd.samr.gov.cn/",
        "https://openstd.samr.gov.cn/bzgk/gb/std_list?keyword={query}&page=1",
        source_type="official",
        standard_type=["GB", "GB/T"],
        priority=1,
        notes="国家标准核心入口，优先查询",
        method="preview_print",
    ),
    "foodmate": _make_source(
        "食品伙伴网",
        "https://down.foodmate.net/",
        "https://down.foodmate.net/standard/search.php?kw={query}",
        source_type="free",
        standard_type=["GB", "GB/T", "GBZ", "WS", "NY", "SC"],
        priority=2,
        notes="食品与通用标准优先源",
        method="search_click_download",
    ),

    # GB / GB食品安全 / 公开发布
    "std_samr": _make_source(
        "国家标准信息公共服务平台",
        "https://std.samr.gov.cn/",
        "https://std.samr.gov.cn/gb/search/gbAdvancedSearch?search={query}",
        priority=3,
        notes="国家标准信息检索入口",
    ),
    "samr_gb_news": _make_source(
        "市场监管总局国家标准相关信息",
        "https://www.samr.gov.cn/zw/sj/sjxz/qzxgjbzjcxxsj/",
        priority=4,
        notes="国家标准基础信息公开页面",
    ),
    "spc_gb": _make_source(
        "中国标准出版社",
        "https://www.spc.org.cn/app/2/index.html",
        "https://www.spc.org.cn/app/2/index.html",
        source_type="official",
        priority=5,
        notes="国家标准出版社入口",
    ),
    "gb_food_safety": _make_source(
        "食品安全国家标准数据检索",
        "http://sppt.cfsa.net.cn:8086/db",
        "http://sppt.cfsa.net.cn:8086/db",
        priority=6,
        notes="食品安全国家标准库",
    ),
    "gb_food_safety_nhc": _make_source(
        "国家卫生健康委食品安全标准",
        "https://www.nhc.gov.cn/sps/spaqgjbz/spaq.shtml",
        "https://www.nhc.gov.cn/sps/spaqgjbz/spaq.shtml",
        priority=7,
        notes="食品安全国家标准公开页面",
    ),

    # HJ / 环保
    "mee_hj": _make_source(
        "生态环境部标准规范",
        "https://www.mee.gov.cn/ywgz/fgbz/bz/",
        "https://www.mee.gov.cn/ywgz/fgbz/bz/",
        priority=10,
        notes="HJ 标准公开页面",
    ),

    # 工业和通用行业标准平台
    "hbba_std": _make_source(
        "行业标准信息服务平台",
        "https://hbba.sacinfo.org.cn/",
        "https://hbba.sacinfo.org.cn/stdList?key={query}",
        priority=20,
        notes="行业标准统一检索入口",
    ),
    "mem_bz": _make_source(
        "应急管理部标准查询",
        "https://www.mem.gov.cn/fw/flfgbz/bz/bzwb/",
        "https://www.mem.gov.cn/fw/flfgbz/bz/bzwb/",
        priority=21,
        notes="AQ / XF / YJ 标准入口",
    ),
    "miit_std": _make_source(
        "工信领域标准信息服务平台",
        "https://std.miit.gov.cn/#/index",
        "https://std.miit.gov.cn/#/index",
        priority=22,
        notes="工信类行业标准入口",
    ),
    "nifdc_std": _make_source(
        "中国食品药品检定研究院",
        "https://www.nifdc.org.cn/nifdc/",
        "https://www.nifdc.org.cn/nifdc/",
        priority=23,
        notes="BJY / CFDAB / NMPAB / YBB 相关入口",
    ),
    "nmpa_std": _make_source(
        "国家药监局数据查询",
        "https://www.nmpa.gov.cn/datasearch/home-index.html#category=qt",
        "https://www.nmpa.gov.cn/datasearch/home-index.html#category=qt",
        priority=24,
        notes="YY / 药品相关标准入口",
    ),
    "nrsis_std": _make_source(
        "自然资源标准信息系统",
        "https://www.nrsis.org.cn/portal/xxcx/std",
        "https://www.nrsis.org.cn/portal/xxcx/std",
        priority=25,
        notes="CH / DZ / HY / TD",
    ),
    "csms_std": _make_source(
        "中国地震标准服务网",
        "https://www.csms.org.cn/csms/bz/StandardSearch.aspx",
        "https://www.csms.org.cn/csms/bz/StandardSearch.aspx",
        priority=26,
        notes="地震标准入口",
    ),
    "mohurd_bzgg": _make_source(
        "住房城乡建设标准公告",
        "https://www.mohurd.gov.cn/gongkai/fdzdgknr/bzgg/index.html",
        "https://www.mohurd.gov.cn/gongkai/fdzdgknr/bzgg/index.html",
        priority=27,
        notes="CJ / CJJ / GB(50000+) / JG / JGJ",
    ),
    "saac_dabz": _make_source(
        "国家档案局行业标准",
        "https://www.saac.gov.cn/daj/hybz/dabz_list.shtml",
        "https://www.saac.gov.cn/daj/hybz/dabz_list.shtml",
        priority=28,
        notes="DA 标准入口",
    ),
    "cea_db": _make_source(
        "中国电力企业联合会标准",
        "https://www.cea.gov.cn/cea/zwgk/5739581/xxbz/index.html",
        "https://www.cea.gov.cn/cea/zwgk/5739581/xxbz/index.html",
        priority=29,
        notes="DB 标准入口",
    ),
    "cgs_std": _make_source(
        "中国地质调查标准",
        "https://std.cgs.gov.cn/",
        "https://std.cgs.gov.cn/",
        priority=30,
        notes="DD 标准入口",
    ),
    "nea_std": _make_source(
        "国家能源局标准公告",
        "https://www.nea.gov.cn/policy/gg.htm",
        "https://www.nea.gov.cn/policy/gg.htm",
        priority=31,
        notes="DL / MT / NB / NB-SH / SY",
    ),
    "nea_portal_std": _make_source(
        "能源标准信息平台",
        "http://114.251.111.103:18080/zxd/portal/std",
        "http://114.251.111.103:18080/zxd/portal/std",
        priority=32,
        notes="能源行业标准查询入口",
    ),
    "nhc_wsbz": _make_source(
        "卫生健康委标准信息",
        "https://www.nhc.gov.cn/wjw/wsbzxx/wsbz.shtml",
        "https://www.nhc.gov.cn/wjw/wsbzxx/wsbz.shtml",
        priority=33,
        notes="GBZ / WS 标准入口",
    ),
    "wsbz_nhc": _make_source(
        "卫生标准查询系统",
        "https://wsbz.nhc.gov.cn/wsbzw/",
        "https://wsbz.nhc.gov.cn/wsbzw/",
        priority=34,
        notes="卫生标准查询系统",
    ),
    "oscca_std": _make_source(
        "国家密码管理局标准规范",
        "https://www.oscca.gov.cn/sca/xxgk/bzgf.shtml",
        "https://www.oscca.gov.cn/app-zxfw/zxfw/bzgfcx.jsp",
        priority=35,
        notes="GM 标准入口",
    ),
    "ncha_std": _make_source(
        "国家文物局行业标准",
        "https://www.ncha.gov.cn/col/col2423/index.html",
        "https://www.ncha.gov.cn/col/col2423/index.html",
        priority=36,
        notes="WW 标准入口",
    ),
    "nrta_std": _make_source(
        "国家广播电视总局标准",
        "https://www.nrta.gov.cn/col/col2081/index.html",
        "https://www.nrta.gov.cn/col/col113/index.html",
        priority=37,
        notes="GY 标准入口",
    ),
    "nnsa_std": _make_source(
        "核安全标准",
        "https://nnsa.mee.gov.cn/zcwj/dz/",
        "https://nnsa.mee.gov.cn/zcwj/dz/",
        priority=38,
        notes="HAD / HAF / HAFJ",
    ),
    "customs_std": _make_source(
        "海关标准",
        "https://www.customs.gov.cn/eportal/ui?pageId=302266&columnId=302272",
        "https://www.customs.gov.cn/eportal/ui?pageId=302266&columnId=302272",
        priority=39,
        notes="HDB / HS / SN",
    ),
    "jjg_spc": _make_source(
        "计量标准服务平台",
        "https://jjg.spc.org.cn/resmea/view/index",
        "https://jjg.spc.org.cn/resmea/view/index",
        priority=40,
        notes="JJG / JJF",
    ),
    "cfstc_std": _make_source(
        "中国金融标准化门户",
        "https://www.cfstc.org/bzgk/gk",
        "https://www.cfstc.org/bzgk/gk",
        priority=41,
        notes="JR 标准入口",
    ),
    "csisc_std": _make_source(
        "中国证券业标准服务",
        "https://www.csisc.cn/zbscbzw/index.shtml",
        "https://www.csisc.cn/zbscbzw/index.shtml",
        priority=42,
        notes="JR 标准入口",
    ),
    "jtst_std": _make_source(
        "交通运输标准化平台",
        "https://jtst.mot.gov.cn/",
        "https://jtst.mot.gov.cn/",
        priority=43,
        notes="JT 标准入口",
    ),
    "moe_std": _make_source(
        "教育部标准信息",
        "https://www.moe.gov.cn/",
        "https://www.moe.gov.cn/",
        priority=44,
        notes="JY 标准入口",
    ),
    "zbzx_std": _make_source(
        "教育标准化网站",
        "https://www.zbzx.edu.cn/html/bzh/",
        "https://www.zbzx.edu.cn/html/bzh/",
        priority=45,
        notes="JY 标准入口",
    ),
    "mct_wh": _make_source(
        "文旅行业标准",
        "https://www.mct.gov.cn/whzx/zxgz/wlbzhgz/",
        "https://www.mct.gov.cn/whzx/zxgz/wlbzhgz/",
        priority=46,
        notes="LB / WH 标准入口",
    ),
    "mohrss_std": _make_source(
        "人社部标准公开",
        "https://www.mohrss.gov.cn/xxgk2020/fdzdgknr/",
        "https://www.mohrss.gov.cn/xxgk2020/fdzdgknr/",
        priority=47,
        notes="LD 标准入口",
    ),
    "lswz_std": _make_source(
        "粮食和物资储备标准",
        "https://www.lswz.gov.cn/html/ywpd/bzzl/lybz.shtml",
        "https://www.lswz.gov.cn/html/ywpd/bzzl/lybz.shtml",
        priority=48,
        notes="LS 标准入口",
    ),
    "forestry_std": _make_source(
        "国家林业和草原局标准质量",
        "https://www.forestry.gov.cn/lykj/1716/index.html",
        "https://www.forestry.gov.cn/lykj/1716/index.html",
        priority=49,
        notes="LY 标准入口",
    ),
    "caac_std": _make_source(
        "中国民航局标准质量",
        "https://www.caac.gov.cn/XXGK/XXGK/index_172.html?fl=15",
        "https://www.caac.gov.cn/XXGK/XXGK/index_172.html?fl=15",
        priority=50,
        notes="MH 标准入口",
    ),
    "mca_std": _make_source(
        "民政部政府信息公开目录",
        "https://xxgk.mca.gov.cn:8011/gdnps/pc/index.jsp?mtype=1",
        "https://xxgk.mca.gov.cn:8011/gdnps/pc/index.jsp?mtype=1",
        priority=51,
        notes="MZ 标准入口",
    ),
    "moa_public": _make_source(
        "农业农村部政府公开",
        "https://www.moa.gov.cn/govpublic/",
        "https://www.moa.gov.cn/govpublic/",
        priority=52,
        notes="NY / SC 标准入口",
    ),
    "sdtdata": _make_source(
        "全国农业食品标准公共服务平台",
        "https://www.sdtdata.com/fx/fmoa/tsLibIndex",
        "https://www.sdtdata.com/fx/fmoa/tsLibIndex",
        priority=53,
        notes="NY / SC 标准入口",
    ),
    "cma_std": _make_source(
        "中国气象局政府公开目录",
        "https://www.cma.gov.cn/root7/auto13139/",
        "https://www.cma.gov.cn/root7/auto13139/",
        priority=54,
        notes="QX 标准入口",
    ),
    "mofcom_std": _make_source(
        "商务领域行业标准制修订信息管理",
        "https://ltbzh.mofcom.gov.cn/ltbz/view/bzfk/listBzfk.jsp",
        "https://ltbzh.mofcom.gov.cn/ltbz/view/bzfk/listBzfk.jsp",
        priority=55,
        notes="SB 标准入口",
    ),
    "mwr_std": _make_source(
        "水利行业技术标准查询",
        "https://gjkj.mwr.gov.cn/jsjd1/bzcx/",
        "https://gjkj.mwr.gov.cn/jsjd1/bzcx/",
        priority=56,
        notes="SD / SL 标准入口",
    ),
    "moj_std": _make_source(
        "司法部官网",
        "https://www.moj.gov.cn/",
        "https://www.moj.gov.cn/",
        priority=57,
        notes="SF 标准入口",
    ),
    "chinatax_std": _make_source(
        "国家税务总局",
        "https://www.chinatax.gov.cn/",
        "https://www.chinatax.gov.cn/",
        priority=58,
        notes="SW 标准入口",
    ),
    "cnca_std": _make_source(
        "认证认可标准化信息服务平台",
        "https://rbtest.cnca.cn/",
        "https://rbtest.cnca.cn/",
        priority=59,
        notes="RB 标准入口",
    ),
    "nra_std": _make_source(
        "国家铁路局政府信息公开",
        "https://www.nra.gov.cn/xxgkml/xxgk/xxgkml/",
        "https://www.nra.gov.cn/xxgkml/xxgk/xxgkml/",
        priority=60,
        notes="TB 标准入口",
    ),
    "china_railway_std": _make_source(
        "国铁集团技术标准",
        "https://www.china-railway.com.cn/kjcx/jsbz/",
        "https://www.china-railway.com.cn/kjcx/jsbz/",
        priority=61,
        notes="TB 继承/转化标准入口",
    ),
    "sport_jjs": _make_source(
        "国家体育总局体育经济司",
        "https://www.sport.gov.cn/jjs/index.html",
        "https://www.sport.gov.cn/jjs/index.html",
        priority=62,
        notes="TY 标准入口",
    ),
    "sport_service": _make_source(
        "国家体育总局办事服务",
        "https://www.sport.gov.cn/n322/n384/index.html",
        "https://www.sport.gov.cn/n322/n384/index.html",
        priority=63,
        notes="TY 标准入口",
    ),
    "ndrc_std": _make_source(
        "国家发展改革委公告",
        "https://www.ndrc.gov.cn/xxgk/zcfb/gg/",
        "https://www.ndrc.gov.cn/xxgk/zcfb/gg/",
        priority=64,
        notes="WB 标准入口",
    ),
    "hbba_cy": _make_source(
        "行业标准信息服务平台-新闻出版",
        "https://hbba.sacinfo.org.cn/stdList?key=&trade=%E6%96%B0%E9%97%BB%E5%87%BA%E7%89%88",
        "https://hbba.sacinfo.org.cn/stdList?key={query}&trade=%E6%96%B0%E9%97%BB%E5%87%BA%E7%89%88",
        priority=65,
        notes="CY 标准入口",
    ),
    "hbba_ga": _make_source(
        "行业标准信息服务平台-公共安全",
        "https://hbba.sacinfo.org.cn/stdList?key=&trade=%E5%85%AC%E5%85%B1%E5%AE%89%E5%85%A8",
        "https://hbba.sacinfo.org.cn/stdList?key={query}&trade=%E5%85%AC%E5%85%B1%E5%AE%89%E5%85%A8",
        priority=66,
        notes="GA 标准入口",
    ),
    "hbba_gh": _make_source(
        "行业标准信息服务平台-供销合作",
        "https://hbba.sacinfo.org.cn/stdList?key=&trade=%E4%BE%9B%E9%94%80%E5%90%88%E4%BD%9C",
        "https://hbba.sacinfo.org.cn/stdList?key={query}&trade=%E4%BE%9B%E9%94%80%E5%90%88%E4%BD%9C",
        priority=67,
        notes="GH 标准入口",
    ),
    "hbba_wm": _make_source(
        "行业标准信息服务平台-外经贸",
        "https://hbba.sacinfo.org.cn/",
        "https://hbba.sacinfo.org.cn/stdList?key={query}",
        priority=68,
        notes="WM 标准入口",
    ),
    "hbba_zy": _make_source(
        "行业标准信息服务平台-中医药",
        "https://hbba.sacinfo.org.cn/stdList?key=&trade=%E4%B8%AD%E5%8C%BB%E8%8D%AF",
        "https://hbba.sacinfo.org.cn/stdList?key={query}&trade=%E4%B8%AD%E5%8C%BB%E8%8D%AF",
        priority=69,
        notes="ZY 标准入口",
    ),
    "hbba_default": _make_source(
        "行业标准信息服务平台",
        "https://hbba.sacinfo.org.cn/",
        "https://hbba.sacinfo.org.cn/stdList?key={query}",
        priority=70,
        notes="通用行业标准入口",
    ),
    "tobacco_std": _make_source(
        "国家烟草专卖局政策文件库",
        "https://www.tobacco.gov.cn/gjyc/zcwjk/zck.shtml?tab=zcwj",
        "https://www.tobacco.gov.cn/gjyc/zcwjk/zck.shtml?tab=zcwj",
        priority=71,
        notes="YC 标准入口",
    ),
    "spb_std": _make_source(
        "国家邮政局信息公开",
        "https://xxgk.spb.gov.cn/extranet/index.html",
        "https://xxgk.spb.gov.cn/extranet/index.html",
        priority=72,
        notes="YZ 标准入口",
    ),
    "cnipa_std": _make_source(
        "国家知识产权局标准与分类",
        "https://www.cnipa.gov.cn/col/col2148/index.html",
        "https://www.cnipa.gov.cn/col/col2148/index.html",
        priority=73,
        notes="ZC 标准入口",
    ),
}

SUPPORTED_DOWNLOAD_SOURCE_IDS = set(DOWNLOAD_SOURCES.keys())

STANDARD_ROUTE_RULES = [
    (re.compile(r"^(GB/T|GBT|GB)\b", re.IGNORECASE), [
        "gb_openstd",
        "foodmate",
        "std_samr",
        "samr_gb_news",
        "spc_gb",
        "gb_food_safety",
        "gb_food_safety_nhc",
    ]),
    (re.compile(r"^(GBZ|WS)\b", re.IGNORECASE), [
        "nhc_wsbz",
        "wsbz_nhc",
        "foodmate",
    ]),
    (re.compile(r"^HJ\b", re.IGNORECASE), ["mee_hj"]),
    (re.compile(r"^(AQ|XF|YJ)\b", re.IGNORECASE), ["mem_bz"]),
    (re.compile(r"^(BB|CB|EJ|FZ|HBJ|HB|HG|JB|JC|QB|QC|SH|SJ|WJ|XB|YB|YD|YS)\b", re.IGNORECASE), [
        "miit_std",
        "hbba_std",
    ]),
    (re.compile(r"^(BJY|CFDAB|NMPAB|YBB|YY)\b", re.IGNORECASE), [
        "nifdc_std",
        "nmpa_std",
    ]),
    (re.compile(r"^(CH|DZ|HY|TD)\b", re.IGNORECASE), ["nrsis_std", "csms_std"]),
    (re.compile(r"^(CJJ|CJ|JGJ|JG)\b", re.IGNORECASE), ["mohurd_bzgg"]),
    (re.compile(r"^DB(\d+|)\b", re.IGNORECASE), ["cea_db"]),
    (re.compile(r"^(DD)\b", re.IGNORECASE), ["cgs_std"]),
    (re.compile(r"^(DL|MT|NB-SH|NB|SY)\b", re.IGNORECASE), ["nea_std", "nea_portal_std"]),
    (re.compile(r"^GA\b", re.IGNORECASE), ["hbba_ga"]),
    (re.compile(r"^GH\b", re.IGNORECASE), ["hbba_gh"]),
    (re.compile(r"^GM\b", re.IGNORECASE), ["oscca_std"]),
    (re.compile(r"^GY\b", re.IGNORECASE), ["nrta_std"]),
    (re.compile(r"^HAD\b|^HAFJ?\b", re.IGNORECASE), ["nnsa_std"]),
    (re.compile(r"^(HDB|HS|SN)\b", re.IGNORECASE), ["customs_std"]),
    (re.compile(r"^(JJG|JJF)\b", re.IGNORECASE), ["jjg_spc"]),
    (re.compile(r"^JR\b", re.IGNORECASE), ["cfstc_std", "csisc_std"]),
    (re.compile(r"^JT\b", re.IGNORECASE), ["jtst_std"]),
    (re.compile(r"^JY\b", re.IGNORECASE), ["moe_std", "zbzx_std"]),
    (re.compile(r"^(LB|WH)\b", re.IGNORECASE), ["mct_wh"]),
    (re.compile(r"^LD\b", re.IGNORECASE), ["mohrss_std"]),
    (re.compile(r"^LS\b", re.IGNORECASE), ["lswz_std"]),
    (re.compile(r"^LY\b", re.IGNORECASE), ["forestry_std"]),
    (re.compile(r"^MH\b", re.IGNORECASE), ["caac_std"]),
    (re.compile(r"^MZ\b", re.IGNORECASE), ["mca_std"]),
    (re.compile(r"^(NY|SC)\b", re.IGNORECASE), ["moa_public", "sdtdata"]),
    (re.compile(r"^QX\b", re.IGNORECASE), ["cma_std"]),
    (re.compile(r"^SB\b", re.IGNORECASE), ["mofcom_std"]),
    (re.compile(r"^(SD|SL)\b", re.IGNORECASE), ["mwr_std"]),
    (re.compile(r"^SF\b", re.IGNORECASE), ["moj_std"]),
    (re.compile(r"^SW\b", re.IGNORECASE), ["chinatax_std"]),
    (re.compile(r"^RB\b", re.IGNORECASE), ["cnca_std"]),
    (re.compile(r"^TB\b", re.IGNORECASE), ["nra_std", "china_railway_std"]),
    (re.compile(r"^TY\b", re.IGNORECASE), ["sport_jjs", "sport_service"]),
    (re.compile(r"^WB\b", re.IGNORECASE), ["ndrc_std"]),
    (re.compile(r"^WM\b", re.IGNORECASE), ["hbba_wm"]),
    (re.compile(r"^WW\b", re.IGNORECASE), ["ncha_std"]),
    (re.compile(r"^YC\b", re.IGNORECASE), ["tobacco_std"]),
    (re.compile(r"^YZ\b", re.IGNORECASE), ["spb_std"]),
    (re.compile(r"^ZC\b", re.IGNORECASE), ["cnipa_std"]),
    (re.compile(r"^ZY\b", re.IGNORECASE), ["hbba_zy"]),
]

# 当前设计：仅保留两个下载源。
# 1. 优先尝试食品伙伴网
# 2. 仅当食品伙伴网无结果且标准属于 GB / GB-T 时，再尝试国家标准全文公开系统
PRIMARY_DOWNLOAD_SOURCE_ID = "foodmate"
NATIONAL_STANDARD_SECONDARY_SOURCE_IDS = ["gb_openstd"]
ACTIVE_DOWNLOAD_SOURCE_IDS = [
    source_id
    for source_id in [PRIMARY_DOWNLOAD_SOURCE_ID, *NATIONAL_STANDARD_SECONDARY_SOURCE_IDS]
    if source_id in DOWNLOAD_SOURCES and source_id not in DISABLED_DOWNLOAD_SOURCES
]

DEFAULT_BROWSER_SCRIPT = {
    "search_input_selectors": [
        "input[type='search']",
        "input[placeholder*='搜索']",
        "input[placeholder*='查询']",
        "input[placeholder*='标准']",
        "input[placeholder*='keyword']",
        "input[type='text']",
    ],
    "search_button_selectors": [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('搜索')",
        "button:has-text('查询')",
        "a:has-text('搜索')",
        "a:has-text('查询')",
    ],
    "result_link_selectors": [
        "a[href]",
    ],
    "detail_link_selectors": [
        "a[href*='detail']",
        "a[href*='view']",
        "a[href*='show']",
        "a[href*='read']",
        "a[href*='info']",
        "a[href*='standard']",
        "a[href*='std']",
    ],
    "result_wait_selectors": [],
}

SOURCE_BROWSER_SCRIPTS = {
    "gb_openstd": {
        "search_input_selectors": [],
        "search_button_selectors": [],
        "result_link_selectors": ["a[href*='showInfo']", "a[href*='showGb']"],
        "detail_link_selectors": ["a[href*='showGb']", "a[href*='showInfo']"],
        "result_wait_selectors": ["a[href*='showInfo']"],
    },
    "foodmate": {
        "search_input_selectors": [],
        "search_button_selectors": [],
        "result_link_selectors": [
            "a[href*='/standard/sort/'][href$='.html']",
            ".list.flck a[href*='/standard/sort/'][href$='.html']",
        ],
        "detail_link_selectors": ["a[href*='/standard/sort/'][href$='.html']"],
        "result_wait_selectors": ["a[href*='/standard/sort/']"],
    },
    "nhc_wsbz": {
        "search_input_selectors": [
            "input[type='text']",
            "input[placeholder*='标准']",
            "input[placeholder*='查询']",
        ],
        "search_button_selectors": [
            "input[type='submit']",
            "button",
            "a:has-text('查询')",
        ],
        "result_link_selectors": ["a[href]", ".list a", ".result a"],
        "detail_link_selectors": ["a[href*='detail']", "a[href*='show']", "a[href*='wsbz']"],
    },
    "wsbz_nhc": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "mee_hj": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "hbba_std": {
        "search_input_selectors": [],
        "search_button_selectors": [],
        "result_link_selectors": [
            "a[href*='stdDetail']",
            "a[href*='stdList?']",
            "a[href]",
        ],
        "result_wait_selectors": ["a[href]"],
    },
    "mem_bz": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "miit_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "nifdc_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "nmpa_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "nrsis_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "csms_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "mohurd_bzgg": {
        "search_input_selectors": ["#searchInput"],
        "search_button_selectors": ["#toSearchBtn"],
        "result_link_selectors": ["a[href*='/art/']", "a[href$='.pdf']"],
        "detail_link_selectors": ["a[href*='/art/']", "a[href$='.pdf']"],
    },
    "saac_dabz": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "cea_db": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "cgs_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "nea_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "nea_portal_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "oscca_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "ncha_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "nrta_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "nnsa_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "customs_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "jjg_spc": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "cfstc_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "csisc_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "jtst_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "moe_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "zbzx_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "mct_wh": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "mohrss_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "lswz_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "forestry_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "caac_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "mca_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "moa_public": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "sdtdata": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "cma_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "mofcom_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "mwr_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "moj_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "chinatax_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "cnca_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "nra_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "china_railway_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "sport_jjs": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "sport_service": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "ndrc_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "hbba_cy": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "hbba_ga": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "hbba_gh": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "hbba_wm": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "hbba_zy": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "hbba_default": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "tobacco_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "spb_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
    "cnipa_std": {"result_link_selectors": ["a[href]", ".list a", ".result a"]},
}

class StandardAutoDownloader:
    """
    标准自动下载器 - 修复版
    
    工作流程：
    1. 解析标准号，确定标准类型
    2. 选择合适的下载源（优先官方平台）
    3. 使用Playwright打开浏览器
    4. 搜索并找到标准文档
    5. 下载或预览保存为PDF
    """

    def __init__(self, download_dir: str = None, cancel_callback=None, source_timeout: int = 45):
        self.download_dir = Path(download_dir or Path(__file__).parent.parent / "downloads")
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._browser = None
        self._context = None
        self._playwright = None
        self._driver = None
        self._webdriver = None
        self._selenium_available = None
        self._playwright_available = None
        self._playwright_init_error = None
        self._selenium_init_error = None
        self.cancel_callback = cancel_callback
        self.source_timeout = source_timeout
        self._default_implicit_wait = 10
        self._probe_implicit_wait = 0.2

    @staticmethod
    def _chrome_binary_candidates() -> List[str]:
        home = Path(os.path.expanduser("~"))
        candidates = [
            shutil.which("google-chrome"),
            shutil.which("chrome"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
            "C:/Program Files/Google/Chrome/Application/chrome.exe",
            "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
            str(home / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe"),
            str(home / "AppData" / "Local" / "Chromium" / "Application" / "chrome.exe"),
        ]
        # 添加自动下载的 Chrome 路径（如果存在）
        auto_chrome = Path.cwd() / "drivers" / "chrome" / "chrome.exe"
        if auto_chrome.exists():
            candidates.insert(0, str(auto_chrome))  # 优先使用自动下载的
        return [path for path in candidates if path]

    @classmethod
    def _detect_chrome_binary(cls) -> Optional[str]:
        for candidate in cls._chrome_binary_candidates():
            if shutil.which(candidate) or Path(candidate).exists():
                return candidate
        return None

    @staticmethod
    def _is_browser_bootstrap_error(error: Exception) -> bool:
        text = str(error or "").lower()
        markers = [
            "playwright浏览器",
            "浏览器初始化失败",
            "无法初始化",
            "chrome",
            "chromedriver",
            "session not created",
            "cannot find chrome binary",
            "browsertype.launch",
            "executable doesn't exist",
            "this version of chromedriver only supports",
            "unable to discover open pages",
            "unknown error: cannot find",
            "playwright 不可用",
        ]
        return any(marker in text for marker in markers)

    def _build_selenium_options(self, headless: bool = True, headless_arg: Optional[str] = None):
        from selenium.webdriver.chrome.options import Options

        options = Options()
        if headless:
            options.add_argument(headless_arg or "--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--log-level=3")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        options.page_load_strategy = "eager"
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        prefs = {
            "download.default_directory": str(self.download_dir.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        }
        options.add_experimental_option("prefs", prefs)

        chrome_binary = self._detect_chrome_binary()
        if chrome_binary:
            options.binary_location = chrome_binary
        return options

    def _create_selenium_driver(self, service, headless: bool = True):
        last_error = None
        for headless_arg in ("--headless=new", "--headless"):
            options = self._build_selenium_options(
                headless=headless,
                headless_arg=headless_arg,
            )
            try:
                driver = self._webdriver.Chrome(service=service, options=options)
                driver.set_page_load_timeout(30)
                driver.implicitly_wait(self._default_implicit_wait)
                return driver
            except Exception as e:
                last_error = e
                logger.warning(
                    "ChromeDriver 启动失败 (headless=%s): %s",
                    headless_arg,
                    e,
                )
        if not headless:
            options = self._build_selenium_options(headless=False)
            return self._webdriver.Chrome(service=service, options=options)
        raise last_error or RuntimeError("ChromeDriver初始化失败")

    async def _init_browser(self, headless: bool = True):
        """初始化浏览器，Playwright 失败时自动降级到 Selenium"""
        if self._browser is not None:
            return
        if self._playwright_init_error and self._selenium_init_error:
            raise RuntimeError(f"所有浏览器初始化失败: Playwright={self._playwright_init_error}, Selenium={self._selenium_init_error}")

        # 先尝试 Playwright
        if not self._playwright_init_error:
            try:
                from playwright.async_api import async_playwright

                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=headless,
                    args=[
                        '--no-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-gpu',
                        '--disable-blink-features=AutomationControlled',
                    ]
                )
                self._context = await self._browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    viewport={'width': 1920, 'height': 1080},
                )
                self._playwright_available = True
                logger.info("Playwright 浏览器初始化完成")
                return
            except Exception as e:
                try:
                    if self._playwright:
                        await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
                self._browser = None
                self._context = None
                self._playwright_available = False
                self._playwright_init_error = str(e)
                logger.warning(f"Playwright 初始化失败，将降级到 Selenium: {e}")

        # Playwright 失败，降级到 Selenium
        if not self._selenium_init_error:
            try:
                self._init_selenium(headless=headless)
                self._selenium_available = True
                logger.info("Selenium 浏览器初始化完成（Playwright 降级）")
            except Exception as e:
                self._selenium_available = False
                self._selenium_init_error = str(e)
                logger.error(f"Selenium 也初始化失败: {e}")
                raise RuntimeError(f"浏览器初始化失败: Playwright={self._playwright_init_error}, Selenium={e}")

    def _report(self, current: int, total: int, message: str, details: dict = None):
        """报告进度"""
        # 当前服务未接入统一回调，保留接口以兼容调用方
        return

    def _check_playwright(self) -> bool:
        if self._playwright_available is not None:
            return self._playwright_available
        try:
            from playwright.async_api import async_playwright  # noqa: F401
            self._playwright_available = True
            return True
        except Exception:
            self._playwright_available = False
            return False

    def _check_selenium(self) -> bool:
        if self._selenium_available is not None:
            return self._selenium_available
        try:
            from selenium import webdriver  # noqa: F401
            self._webdriver = webdriver
            self._selenium_available = True
            return True
        except Exception:
            self._selenium_available = False
            return False

    def _init_selenium(self, headless: bool = True):
        """初始化 Selenium 备用浏览器。"""
        if self._driver is not None:
            return
        if self._selenium_init_error:
            raise RuntimeError(self._selenium_init_error)
        if not self._check_selenium():
            raise RuntimeError(
                "Selenium不可用。请安装Selenium: pip install selenium\n"
                "并确保Chrome浏览器和ChromeDriver已安装。"
            )

        from selenium.webdriver.chrome.service import Service

        try:
            if getattr(sys, "frozen", False):
                base_dir = Path(sys.executable).parent
            else:
                base_dir = Path(__file__).resolve().parent.parent.parent
        except Exception:
            base_dir = Path.cwd()

        local_paths = [
            Path("/usr/local/bin/chromedriver"),
            Path("/usr/bin/chromedriver"),
            base_dir / "drivers" / "chromedriver.exe",
            base_dir / "drivers" / "chromedriver",
            Path("C:/chromedriver.exe"),
            Path("D:/chromedriver.exe"),
        ]
        if sys.platform == "win32":
            local_paths.extend([
                Path(os.path.expanduser("~")) / "AppData" / "Local" / "Programs" / "chromedriver.exe",
                Path(os.path.expanduser("~")) / "chromedriver.exe",
            ])

        last_local_error = None
        for path in local_paths:
            if path.exists():
                # Windows 跳过 Linux ELF 文件（避免 [WinError 193]）
                if sys.platform == "win32":
                    try:
                        with open(path, "rb") as f:
                            header = f.read(4)
                            if header == b'\x7fELF':  # Linux ELF 文件
                                logger.debug(f"跳过 Linux ELF 驱动: {path}")
                                continue
                    except Exception:
                        pass
                try:
                    self._driver = self._create_selenium_driver(
                        Service(executable_path=str(path)),
                        headless=headless,
                    )
                    return
                except Exception as e:
                    last_local_error = e
                    logger.warning(f"本地ChromeDriver {path} 初始化失败: {e}")

        from selenium.webdriver.chrome.service import Service as ChromeService

        self._report(10, 100, "Preparing required component ChromeDriver...", {})

        # 1. 获取本机Chrome版本
        chrome_version = _get_chrome_version()
        auto_downloaded_chrome = None
        
        if not chrome_version:
            # 未检测到Chrome，尝试自动下载最新版本
            logger.warning("未检测到Chrome，尝试自动下载...")
            self._report(10, 100, "Chrome not found, downloading...", {})
            
            # 获取最新版本号
            latest_version = _get_latest_matching_version("")
            if latest_version:
                logger.info(f"将下载Chrome版本: {latest_version}")
                
                # 下载Chrome
                chrome_path = _download_chrome_npmirror(latest_version, base_dir)
                if chrome_path:
                    auto_downloaded_chrome = chrome_path
                    chrome_version = latest_version
                    logger.info(f"Chrome已下载到: {chrome_path}")
                else:
                    logger.error("Chrome自动下载失败")
            else:
                logger.error("无法获取最新Chrome版本号")
        
        if not chrome_version:
            # 自动下载也失败了，给出错误提示
            is_windows = sys.platform == "win32"
            error_msg = "无法获取或下载Chrome。"
            if is_windows:
                error_msg += "\n\n【Windows用户】"
                error_msg += "\n自动下载Chrome失败，请手动下载："
                error_msg += "\n1. 访问 https://www.google.cn/chrome 安装Chrome"
                error_msg += "\n2. 或手动下载chromedriver.exe放到 drivers/ 目录"
            else:
                error_msg += "\n\n【Linux用户】请执行: sudo apt install google-chrome-stable"
            
            if last_local_error:
                error_msg += f"\n\n【本地驱动错误】{last_local_error}"
            
            raise RuntimeError(error_msg)

        # 显示检测到的 Chrome 路径
        chrome_binary = self._detect_chrome_binary()
        if chrome_binary:
            logger.info(f"将使用 Chrome: {chrome_binary}")
        self._report(11, 100, f"Detected Chrome version: {chrome_version}", {})

        # 2. 获取匹配的ChromeDriver版本
        matched_version = _get_latest_matching_version(chrome_version)
        if not matched_version:
            raise RuntimeError(
                f"无法找到匹配的ChromeDriver版本 (Chrome: {chrome_version})。\n"
                f"本地驱动错误: {last_local_error}\n\n"
                "请确保Chrome版本较新，或手动下载chromedriver并放到drivers/目录"
            )

        self._report(12, 100, f"ChromeDriver version: {matched_version}", {})

        # 3. 从npmmirror下载
        driver_path = _download_chromedriver_npmirror(matched_version, base_dir)
        if not driver_path:
            raise RuntimeError(
                f"ChromeDriver下载失败 (版本: {matched_version})。\n"
                f"本地驱动错误: {last_local_error}\n\n"
                "请检查网络连接，或手动下载chromedriver并放到drivers/目录"
            )

        try:
            self._driver = self._create_selenium_driver(
                ChromeService(driver_path),
                headless=headless,
            )
        except Exception as e:
            self._selenium_available = False
            self._selenium_init_error = (
                "ChromeDriver初始化失败。\n"
                f"本地驱动错误: {last_local_error}\n"
                f"自动下载错误: {e}\n\n"
                "请确保:\n"
                "1. 已安装Google Chrome浏览器\n"
                "2. 可用的ChromeDriver版本与浏览器兼容\n"
                "3. 或手动下载chromedriver并放到drivers/目录"
            )
            raise RuntimeError(
                self._selenium_init_error
            )

    def _is_cancelled(self) -> bool:
        try:
            return bool(self.cancel_callback and self.cancel_callback())
        except Exception:
            return False

    def _raise_if_cancelled(self):
        if self._is_cancelled():
            raise RuntimeError("下载已取消")

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

    def _should_use_direct_selenium_fallback(self) -> bool:
        """在 Playwright 不可用时，直接走 Selenium，避免同一源重复回退。"""
        if self._playwright_available is False:
            return True
        if self._playwright_init_error:
            return True
        return not self._check_playwright()

    async def _run_with_cancel(self, awaitable, timeout: int, label: str):
        task = asyncio.create_task(awaitable)
        start = asyncio.get_event_loop().time()
        try:
            while True:
                self._raise_if_cancelled()
                elapsed = asyncio.get_event_loop().time() - start
                if timeout and elapsed >= timeout:
                    task.cancel()
                    raise TimeoutError(f"{label}超时")

                wait_timeout = 0.5
                if timeout:
                    wait_timeout = min(wait_timeout, max(0.05, timeout - elapsed))

                done, _ = await asyncio.wait(
                    {task}, timeout=wait_timeout, return_when=asyncio.FIRST_COMPLETED
                )
                if done:
                    return await task
        finally:
            if not task.done():
                task.cancel()

    async def close(self):
        """关闭浏览器"""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._browser = None
        self._context = None
        self._playwright = None
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None

    def _selenium_page_source(self, url: str) -> str:
        self._init_selenium(headless=True)
        self._driver.get(url)
        time.sleep(2)
        return self._driver.page_source

    def _format_file_display(self, standard_number: str) -> str:
        safe = self._format_actual_standard(standard_number)
        return safe.replace("/", "_")

    def parse_standard_number(self, query: str) -> Dict:
        """解析标准号"""
        result = {
            "original": query,
            "organization": None,
            "number": None,
            "year": None,
            "normalized": None,
            "type": None,
        }

        query = query.strip().upper()

        if re.fullmatch(r"\d{4}-\d{4}", query):
            number, year = query.split("-", 1)
            result["organization"] = "GB"
            result["number"] = number
            result["year"] = year
            result["type"] = "GB"
            result["normalized"] = f"GB {number}-{year}"
            return result

        if re.fullmatch(r"\d{4}", query):
            result["organization"] = "GB"
            result["number"] = query
            result["type"] = "GB"
            result["normalized"] = f"GB {query}"
            return result

        patterns = [
            (r"(GB\s*[/]?\s*T)\s*(\d+\.?\d*)\s*[:\-]?\s*(\d{4})?", "GB/T", "GB"),
            (r"(GB)\s*(\d+\.?\d*)\s*[:\-]?\s*(\d{4})?", "GB", "GB"),
            (r"(JJG)\s*(\d+)\s*[:\-]?\s*(\d{4})?", "JJG", "JJG"),
            (r"(JJF)\s*(\d+)\s*[:\-]?\s*(\d{4})?", "JJF", "JJF"),
            (r"(HG\s*[/]?\s*T)\s*(\d+)\s*[:\-]?\s*(\d{4})?", "HG/T", "HG"),
            (r"(HG)\s*(\d+)\s*[:\-]?\s*(\d{4})?", "HG", "HG"),
            (r"(JB\s*[/]?\s*T)\s*(\d+)\s*[:\-]?\s*(\d{4})?", "JB/T", "JB"),
            (r"(JB)\s*(\d+)\s*[:\-]?\s*(\d{4})?", "JB", "JB"),
            (r"(JGJ\s*[/]?\s*T?)\s*(\d+)\s*[:\-]?\s*(\d{4})?", "JGJ", "JGJ"),
            (r"(DB\d+[/]?\s*T?)\s*(\d+)\s*[:\-]?\s*(\d{4})?", "DB", "DB"),
            (r"(ISO\s*[/]?\s*IEC)\s*(\d+[\-]?\d*)\s*[:\-]?\s*(\d{4})?", "ISO/IEC", "ISO"),
            (r"(ISO)\s*(\d+[\-]?\d*)\s*[:\-]?\s*(\d{4})?", "ISO", "ISO"),
            (r"(IEC)\s*(\d+[\-]?\d*)\s*[:\-]?\s*(\d{4})?", "IEC", "IEC"),
            (r"(ASTM)\s*([A-Z]?\d+)\s*[:\-]?\s*(\d{2,4})?", "ASTM", "ASTM"),
        ]

        for pattern, org, std_type in patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                result["organization"] = org
                result["number"] = match.group(2)
                result["type"] = std_type
                if match.lastindex >= 3 and match.group(3):
                    result["year"] = match.group(3)
                break

        if result["organization"] and result["number"]:
            if result["year"]:
                result["normalized"] = f"{result['organization']} {result['number']}-{result['year']}"
            else:
                result["normalized"] = f"{result['organization']} {result['number']}"
        else:
            result["normalized"] = query

        return result

    def _format_actual_standard(self, standard_number: str) -> str:
        """将平台命中的标准号压缩成更适合展示/文件名的格式。"""
        if not standard_number:
            return standard_number
        compact = standard_number.strip().upper()
        compact = re.sub(r"[\s/]+", "", compact)
        compact = compact.replace("：", ":").replace(":", "-")
        compact = re.sub(r"[^A-Z0-9\-]", "", compact)
        return compact

    def _extract_standard_from_title(self, title: str) -> str:
        if not title:
            return ""
        patterns = [
            r"(GB/T\s*\d+(?:\.\d+)?-\d{4})",
            r"(GB\s*\d+(?:\.\d+)?-\d{4})",
            r"(HJ\s*\d+(?:\.\d+)?-\d{4})",
            r"(GBZ\s*\d+(?:\.\d+)?-\d{4})",
            r"(JGJ\s*\d+(?:\.\d+)?-\d{4})",
            r"(JG/T\s*\d+(?:\.\d+)?-\d{4})",
            r"(JJG\s*\d+(?:\.\d+)?-\d{4})",
            r"(JJF\s*\d+(?:\.\d+)?-\d{4})",
            r"([A-Z]{2,6}/?[A-Z]?\s*\d+(?:\.\d+)?-\d{4})",
        ]
        for pattern in patterns:
            match = re.search(pattern, title, re.IGNORECASE)
            if match:
                return self._format_actual_standard(match.group(1))
        return ""

    def _parse_standard_signature(self, standard_number: str) -> Optional[Dict[str, str]]:
        """提取标准号的前缀、编号和年份，用于严格比对实际命中结果。"""
        if not standard_number:
            return None
        text = standard_number.strip().upper().replace("／", "/")
        text = text.replace("：", "-").replace(":", "-")
        text = re.sub(r"\s+", "", text)
        text = re.sub(r"[^A-Z0-9./\-]", "", text)
        match = re.search(
            r"^([A-Z]+(?:[-/][A-Z]+)*)?([0-9]+(?:\.[0-9]+)*)(?:-([0-9]{4}))?$",
            text,
        )
        if not match:
            return None
        return {
            "prefix": re.sub(r"[^A-Z0-9]", "", match.group(1) or ""),
            "number": (match.group(2) or "").replace(".", ""),
            "year": match.group(3) or "",
        }

    def _standard_identity_matches(self, requested: str, actual: str) -> bool:
        """比对请求标准号与平台实际命中标准号，避免误下相近编号。"""
        requested_sig = self._parse_standard_signature(requested)
        actual_sig = self._parse_standard_signature(actual)
        if requested_sig and actual_sig:
            if requested_sig["prefix"] != actual_sig["prefix"]:
                return False
            if requested_sig["number"] != actual_sig["number"]:
                return False
            if requested_sig["year"]:
                return requested_sig["year"] == actual_sig["year"]
            return True
        return self._format_actual_standard(requested) == self._format_actual_standard(actual)

    def _standard_similarity_score(self, requested: str, actual: str) -> int:
        """为相近标准打分，用于排序，不做强制拦截。"""
        requested_sig = self._parse_standard_signature(requested)
        actual_sig = self._parse_standard_signature(actual)
        if requested_sig and actual_sig:
            score = 0
            if requested_sig["prefix"] == actual_sig["prefix"]:
                score += 60
            elif requested_sig["prefix"] and actual_sig["prefix"]:
                score -= 20

            requested_number = requested_sig["number"]
            actual_number = actual_sig["number"]
            if requested_number == actual_number:
                score += 120
            elif requested_number and actual_number and (
                requested_number in actual_number or actual_number in requested_number
            ):
                score += 45

            if requested_sig["year"] and actual_sig["year"]:
                score += 80 if requested_sig["year"] == actual_sig["year"] else -40
            return score

        requested_token = self._format_actual_standard(requested)
        actual_token = self._format_actual_standard(actual)
        if requested_token == actual_token:
            return 160
        if requested_token and actual_token and (
            requested_token in actual_token or actual_token in requested_token
        ):
            return 80
        return 0

    def _selenium_find_best_link(self, driver, selectors: List[str], target_token: str):
        from selenium.webdriver.common.by import By

        best_link = None
        best_score = -1
        with self._temporary_implicit_wait(self._probe_implicit_wait):
            for selector in selectors:
                try:
                    links = driver.find_elements(By.CSS_SELECTOR, selector)
                except Exception:
                    continue
                for link in links:
                    try:
                        href = link.get_attribute("href") or ""
                        text = link.text or ""
                    except Exception:
                        continue
                    if not href:
                        continue
                    text_token = re.sub(r"[^A-Z0-9]", "", text.upper())
                    href_token = re.sub(r"[^A-Z0-9]", "", href.upper())
                    if target_token and (
                        target_token == text_token or target_token == href_token
                    ):
                        score = 100
                    elif target_token and (
                        target_token in text_token or target_token in href_token
                    ):
                        score = 90
                    elif target_token and (
                        text_token in target_token or href_token in target_token
                    ):
                        score = 80
                    else:
                        score = 10
                    if score > best_score:
                        best_score = score
                        best_link = link
        return best_link

    def _normalize_search_term(self, standard_number: str) -> str:
        parsed = self.parse_standard_number(standard_number)
        search_term = parsed["normalized"] or standard_number
        return search_term.replace(" ", "+").replace("/", "%2F")

    def _build_search_term(self, source_id: str, standard_number: str) -> str:
        parsed = self.parse_standard_number(standard_number)
        search_term = parsed["normalized"] or standard_number
        if source_id == "mohurd_bzgg":
            return re.sub(r"[\s/]+", "", search_term)
        return search_term.replace(" ", "+").replace("/", "%2F")

    def _detect_standard_prefix(self, standard_number: str) -> str:
        text = (standard_number or "").strip().upper()
        text = re.sub(r"\s+", "", text)

        patterns = [
            (r"^GB/T", "GB/T"),
            (r"^GBT", "GB/T"),
            (r"^GBZ", "GBZ"),
            (r"^GB", "GB"),
            (r"^HJ", "HJ"),
            (r"^(AQ|XF|YJ)", "AQ"),
            (r"^(BB|CB|EJ|FZ|HBJ|HB|HG|JB|JC|QB|QC|SH|SJ|WJ|XB|YB|YD|YS)", "MIIT"),
            (r"^(BJY|CFDAB|NMPAB|YBB|YY)", "DRUG"),
            (r"^(CH|DZ|HY|TD)", "NATURAL"),
            (r"^(CJJ|CJ|JGJ|JG)", "HOUSING"),
            (r"^DB\d*", "DB"),
            (r"^DD", "DD"),
            (r"^(DL|MT|NB-SH|NB|SY)", "ENERGY"),
            (r"^GA", "GA"),
            (r"^GH", "GH"),
            (r"^GM", "GM"),
            (r"^GY", "GY"),
            (r"^HAFJ", "HAF"),
            (r"^HAF", "HAF"),
            (r"^HAD", "HAD"),
            (r"^(HDB|HS|SN)", "CUSTOMS"),
            (r"^(JJG|JJF)", "JJG"),
            (r"^JR", "JR"),
            (r"^JT", "JT"),
            (r"^JY", "JY"),
            (r"^(LB|WH)", "LB"),
            (r"^LD", "LD"),
            (r"^LS", "LS"),
            (r"^LY", "LY"),
            (r"^MH", "MH"),
            (r"^MZ", "MZ"),
            (r"^(NY|SC)", "NY"),
            (r"^QX", "QX"),
            (r"^SB", "SB"),
            (r"^(SD|SL)", "SD"),
            (r"^SF", "SF"),
            (r"^SW", "SW"),
            (r"^RB", "RB"),
            (r"^TB", "TB"),
            (r"^TY", "TY"),
            (r"^WB", "WB"),
            (r"^WM", "WM"),
            (r"^WW", "WW"),
            (r"^YC", "YC"),
            (r"^YZ", "YZ"),
            (r"^ZC", "ZC"),
            (r"^ZY", "ZY"),
        ]
        for pattern, prefix in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return prefix
        return ""

    def _route_key_for_standard(self, standard_number: str) -> str:
        """把原始标准前缀归一化为下载路由分类键。"""
        parsed = self.parse_standard_number(standard_number)
        raw = (parsed.get("organization") or "").upper()
        compact = re.sub(r"[\s]+", "", raw)
        compact = compact.replace("／", "/")
        original = re.sub(r"[\s]+", "", (standard_number or "").upper()).replace("／", "/")

        direct_map = {
            "GB/T": "GB/T",
            "GB": "GB",
            "GBZ": "GBZ",
            "WS": "WS",
            "HJ": "HJ",
            "AQ": "AQ",
            "XF": "XF",
            "YJ": "YJ",
            "JJG": "JJG",
            "JJF": "JJG",
            "JR": "JR",
            "JT": "JT",
            "JY": "JY",
            "LD": "LD",
            "LS": "LS",
            "LY": "LY",
            "MH": "MH",
            "MZ": "MZ",
            "QX": "QX",
            "SF": "SF",
            "SW": "SW",
            "RB": "RB",
            "TB": "TB",
            "TY": "TY",
            "WB": "WB",
            "WM": "WM",
            "WW": "WW",
            "YC": "YC",
            "YZ": "YZ",
            "ZC": "ZC",
            "ZY": "ZY",
            "DB": "DB",
            "DD": "DD",
            "GA": "GA",
            "GH": "GH",
            "GM": "GM",
            "GY": "GY",
        }
        if compact in direct_map:
            return direct_map[compact]

        startswith_map = [
            (("WS/", "WST", "WS"), "WS"),
            (("HG", "JB", "JC", "QB", "QC", "SH", "SJ", "WJ", "XB", "YB", "YD", "YS", "HBJ", "HB", "BB", "CB", "EJ", "FZ"), "MIIT"),
            (("BJY", "CFDAB", "NMPAB", "YBB", "YY"), "DRUG"),
            (("CH", "DZ", "HY", "TD"), "NATURAL"),
            (("CJJ", "CJ", "JGJ", "JG"), "HOUSING"),
            (("DB",), "DB"),
            (("DL", "MT", "NB", "NB-SH", "NB/SH", "SY"), "ENERGY"),
            (("HAD",), "HAD"),
            (("HAFJ", "HAF"), "HAF"),
            (("HDB", "HS", "SN"), "CUSTOMS"),
            (("LB", "WH"), "LB"),
            (("NY", "SC"), "NY"),
            (("SB",), "SB"),
            (("SD", "SL"), "SD"),
        ]
        for prefixes, route_key in startswith_map:
            if any(compact.startswith(prefix) or original.startswith(prefix) for prefix in prefixes):
                return route_key

        fallback = self._detect_standard_prefix(standard_number)
        return fallback.upper() if fallback else "DEFAULT"

    def _preferred_sources_for_standard(self, standard_number: str, sources: List[str] = None) -> List[str]:
        if sources:
            return [
                source_id
                for source_id in sources
                if source_id in ACTIVE_DOWNLOAD_SOURCE_IDS
            ]

        ordered = []
        if (
            PRIMARY_DOWNLOAD_SOURCE_ID in DOWNLOAD_SOURCES
            and PRIMARY_DOWNLOAD_SOURCE_ID not in DISABLED_DOWNLOAD_SOURCES
        ):
            ordered.append(PRIMARY_DOWNLOAD_SOURCE_ID)
        if self._route_key_for_standard(standard_number) in {"GB", "GB/T"}:
            for source_id in NATIONAL_STANDARD_SECONDARY_SOURCE_IDS:
                if (
                    source_id in DOWNLOAD_SOURCES
                    and source_id not in DISABLED_DOWNLOAD_SOURCES
                    and source_id not in ordered
                ):
                    ordered.append(source_id)
        return ordered

    def _split_two_tier_plan(
        self, standard_number: str, sources: List[str] = None
    ) -> tuple[list[str], list[str]]:
        """将下载计划拆为固定第一层和分类第二层。"""
        if sources:
            filtered = [
                source_id
                for source_id in sources
                if source_id in SUPPORTED_DOWNLOAD_SOURCE_IDS
                and source_id not in DISABLED_DOWNLOAD_SOURCES
            ]
            if not filtered:
                return [], []
            if len(filtered) == 1:
                if filtered[0] == PRIMARY_DOWNLOAD_SOURCE_ID:
                    return filtered, []
                return [], filtered
            return filtered[:1], filtered[1:]

        ordered = self._preferred_sources_for_standard(standard_number, None)
        if not ordered:
            return [], []
        primary = [ordered[0]]
        secondary = ordered[1:]
        return primary, secondary

    def _get_browser_script(self, source_id: str) -> Dict:
        script = dict(DEFAULT_BROWSER_SCRIPT)
        script.update(SOURCE_BROWSER_SCRIPTS.get(source_id, {}))
        return script

    async def _search_and_open_detail_async(self, source_id: str, standard_number: str) -> DownloadResult:
        await self._init_browser(headless=True)
        # 如果已降级到 Selenium，直接走同步备用链路
        if not self._browser and self._driver:
            return await asyncio.to_thread(
                self._search_and_open_detail_sync,
                source_id,
                standard_number,
            )
        page = await self._context.new_page()
        source = DOWNLOAD_SOURCES.get(source_id, {})
        script = self._get_browser_script(source_id)
        source_name = source.get("name", source_id)
        search_url = source.get("search_url") or source.get("url")
        if not search_url:
            await page.close()
            return DownloadResult(
                standard_number=standard_number,
                success=False,
                source=source_name,
                message="未配置访问地址",
            )

        try:
            query = self._build_search_term(source_id, standard_number)
            target_url = search_url.format(query=query) if "{query}" in search_url else search_url
            logger.info(f"[{source_name}] 打开搜索页: {target_url}")
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(1)
            await self._fill_search_input_async(page, query, script)
            await self._trigger_search_async(page, script)
            await self._wait_for_results_async(page, script)
            detail_url = await self._open_best_detail_link_async(page, standard_number, source_id, script)
            page_title = ""
            try:
                page_title = await page.title()
            except Exception:
                page_title = ""
            final_url = detail_url or page.url
            if self._is_invalid_landing_page(final_url, page_title, source.get("notes") or ""):
                return DownloadResult(
                    standard_number=self._format_actual_standard(standard_number),
                    success=False,
                    download_url=final_url,
                    source=source_name,
                    message=page_title or "命中无效页面",
                )
            return DownloadResult(
                standard_number=self._format_actual_standard(standard_number),
                success=True,
                download_url=final_url,
                source=source_name,
                message=page_title or source.get("notes") or "已打开查询页，请在页面中查看详情",
            )
        except Exception as e:
            logger.warning(f"[{source_name}] 查询失败: {e}")
            return DownloadResult(
                standard_number=standard_number,
                success=False,
                source=source_name,
                message=f"查询失败: {str(e)}",
            )
        finally:
            await page.close()

    def _search_and_open_detail_sync(self, source_id: str, standard_number: str) -> DownloadResult:
        self._init_selenium(headless=True)
        source = DOWNLOAD_SOURCES.get(source_id, {})
        script = self._get_browser_script(source_id)
        source_name = source.get("name", source_id)
        search_url = source.get("search_url") or source.get("url")
        if not search_url:
            return DownloadResult(
                standard_number=standard_number,
                success=False,
                source=source_name,
                message="未配置访问地址",
            )

        try:
            query = self._build_search_term(source_id, standard_number)
            target_url = search_url.format(query=query) if "{query}" in search_url else search_url
            logger.info(f"[{source_name}/Selenium] 打开搜索页: {target_url}")
            self._driver.get(target_url)
            time.sleep(2)
            self._fill_search_input_sync(query, script)
            self._trigger_search_sync(script)
            self._wait_for_results_sync(script)
            detail_url = self._open_best_detail_link_sync(standard_number, source_id, script)
            final_url = detail_url or self._driver.current_url
            page_title = self._driver.title or ""
            if self._is_invalid_landing_page(final_url, page_title, source.get("notes") or ""):
                return DownloadResult(
                    standard_number=self._format_actual_standard(standard_number),
                    success=False,
                    download_url=final_url,
                    source=source_name,
                    message=page_title or "命中无效页面",
                )
            return DownloadResult(
                standard_number=self._format_actual_standard(standard_number),
                success=True,
                download_url=final_url,
                source=source_name,
                message=(page_title or source.get("notes") or "已打开查询页，请在页面中查看详情"),
            )
        except Exception as e:
            logger.warning(f"[{source_name}/Selenium] 查询失败: {e}")
            return DownloadResult(
                standard_number=standard_number,
                success=False,
                source=source_name,
                message=f"查询失败: {str(e)}",
            )

    async def _fill_search_input_async(self, page, query: str, script: Dict) -> None:
        selectors = script.get("search_input_selectors", [])
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = await locator.count()
                if count <= 0:
                    continue
                element = locator.first
                if await element.is_visible():
                    await element.fill(query)
                    await element.press("Enter")
                    await asyncio.sleep(2)
                    return
            except Exception:
                continue

    def _fill_search_input_sync(self, query: str, script: Dict) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys

        selectors = script.get("search_input_selectors", [])
        with self._temporary_implicit_wait(self._probe_implicit_wait):
            for selector in selectors:
                try:
                    elements = self._driver.find_elements(By.CSS_SELECTOR, selector)
                except Exception:
                    continue
                for element in elements:
                    try:
                        if element.is_displayed():
                            element.clear()
                            element.send_keys(query)
                            element.send_keys(Keys.ENTER)
                            time.sleep(2)
                            return
                    except Exception:
                        continue

    async def _trigger_search_async(self, page, script: Dict) -> None:
        selectors = script.get("search_button_selectors", [])
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = await locator.count()
                if count <= 0:
                    continue
                element = locator.first
                if await element.is_visible():
                    await element.click()
                    await asyncio.sleep(2)
                    return
            except Exception:
                continue

    def _trigger_search_sync(self, script: Dict) -> None:
        from selenium.webdriver.common.by import By

        selectors = script.get("search_button_selectors", [])
        with self._temporary_implicit_wait(self._probe_implicit_wait):
            for selector in selectors:
                try:
                    elements = self._driver.find_elements(By.CSS_SELECTOR, selector)
                except Exception:
                    continue
                for element in elements:
                    try:
                        if element.is_displayed():
                            element.click()
                            time.sleep(2)
                            return
                    except Exception:
                        continue

    async def _wait_for_results_async(self, page, script: Dict) -> None:
        selectors = script.get("result_wait_selectors", [])
        for selector in selectors:
            try:
                await page.wait_for_selector(selector, timeout=10000)
                return
            except Exception:
                continue

    def _wait_for_results_sync(self, script: Dict) -> None:
        from selenium.webdriver.common.by import By

        selectors = script.get("result_wait_selectors", [])
        with self._temporary_implicit_wait(self._probe_implicit_wait):
            for selector in selectors:
                try:
                    time.sleep(1)
                    elements = self._driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        return
                except Exception:
                    continue

    def _is_invalid_landing_page(self, url: str, title: str, message: str = "") -> bool:
        """识别明显的错误页或辅助页，避免误判为有效详情页。"""
        haystack = " ".join([url or "", title or "", message or ""]).lower()
        invalid_markers = [
            "error report",
            "apusic application server",
            "privacy error",
            "net::err_cert",
            "无障碍辅助浏览工具",
        ]
        return any(marker in haystack for marker in invalid_markers)

    def _score_detail_candidate(self, href: str, text: str, target_token: str, source_id: str) -> int:
        href = href or ""
        text = text or ""
        href_token = re.sub(r"[^A-Z0-9]", "", href.upper())
        text_token = re.sub(r"[^A-Z0-9]", "", text.upper())
        score = 0
        text_lower = text.lower()
        if target_token and (target_token == text_token or target_token == href_token):
            score += 100
        elif target_token and (target_token in text_token or target_token in href_token):
            score += 90
        elif target_token and (text_token in target_token or href_token in target_token):
            score += 80
        if any(k in href.lower() for k in ["detail", "view", "show", "read", "std", "article", "doc", "info"]):
            score += 20
        if any(k in text for k in ["详情", "全文", "查看", "阅读", "标准"]):
            score += 15
        if source_id == "mohurd_bzgg":
            if target_token and (target_token == text_token or target_token == href_token):
                score += 220
            elif target_token and (target_token in text_token or target_token in href_token):
                score += 160
                if "现批准" in text or "编号为" in text:
                    score += 120
            if (
                ("关于发布行业标准" in text or "关于发布国家标准" in text)
                and "公告" in text
            ):
                score += 60
            elif "公告" in text and "标准" in text and len(text) < 80:
                score += 40
            if "文件库" in text_lower or "附件下载" in text_lower:
                score -= 10
            if len(text) > 120 and "公告" not in text:
                score -= 15
        if source_id == "gb_openstd" and "showgb" in href.lower():
            score += 30
        return score

    async def _open_best_detail_link_async(self, page, standard_number: str, source_id: str, script: Dict) -> str:
        target_token = re.sub(r"[^A-Z0-9]", "", (self.parse_standard_number(standard_number)["normalized"] or standard_number).upper())
        best_href = None
        best_score = -1
        try:
            if source_id == "mohurd_bzgg":
                art_links = await page.query_selector_all("a[href*='/art/']")
                strong_candidates = []
                for link in art_links:
                    try:
                        href = await link.get_attribute("href") or ""
                        text = await link.inner_text()
                    except Exception:
                        continue
                    if not href:
                        continue
                    text_token = re.sub(r"[^A-Z0-9]", "", (text or "").upper())
                    if target_token and target_token in text_token:
                        if "现批准" in text or "编号为" in text:
                            strong_candidates.append((2, href, text))
                        elif ("关于发布行业标准" in text or "关于发布国家标准" in text) and "公告" in text:
                            strong_candidates.append((1, href, text))
                        else:
                            strong_candidates.append((0, href, text))
                if strong_candidates:
                    strong_candidates.sort(key=lambda item: (item[0], len(item[2])))
                    best_href = strong_candidates[-1][1]
                    if best_href:
                        await page.goto(best_href, wait_until="domcontentloaded", timeout=30000)
                        await asyncio.sleep(1)
                        return page.url

                for link in art_links:
                    try:
                        href = await link.get_attribute("href") or ""
                        text = await link.inner_text()
                    except Exception:
                        continue
                    if not href:
                        continue
                    score = self._score_detail_candidate(href, text, target_token, source_id)
                    if score > best_score:
                        best_score = score
                        best_href = href
                if best_href:
                    await page.goto(best_href, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(1)
                    return page.url

            selectors = script.get("result_link_selectors", ["a[href]"])
            links = []
            for selector in selectors:
                try:
                    links.extend(await page.query_selector_all(selector))
                except Exception:
                    continue
            for link in links:
                try:
                    href = await link.get_attribute("href") or ""
                    text = await link.inner_text()
                except Exception:
                    continue
                if not href or href.startswith("javascript:") or href.startswith("#"):
                    continue
                score = self._score_detail_candidate(href, text, target_token, source_id)
                if score > best_score:
                    best_score = score
                    best_href = href
            if best_href:
                await page.goto(best_href, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(1)
                return page.url
        except Exception:
            pass
        return ""

    def _open_best_detail_link_sync(self, standard_number: str, source_id: str, script: Dict) -> str:
        from selenium.webdriver.common.by import By

        target_token = re.sub(r"[^A-Z0-9]", "", (self.parse_standard_number(standard_number)["normalized"] or standard_number).upper())
        best_href = None
        best_score = -1
        try:
            if source_id == "mohurd_bzgg":
                links = self._driver.find_elements(By.CSS_SELECTOR, "a[href*='/art/']")
                strong_candidates = []
                for link in links:
                    try:
                        href = link.get_attribute("href") or ""
                        text = link.text or ""
                    except Exception:
                        continue
                    if not href:
                        continue
                    text_token = re.sub(r"[^A-Z0-9]", "", (text or "").upper())
                    if target_token and target_token in text_token:
                        if "现批准" in text or "编号为" in text:
                            strong_candidates.append((2, href, text))
                        elif ("关于发布行业标准" in text or "关于发布国家标准" in text) and "公告" in text:
                            strong_candidates.append((1, href, text))
                        else:
                            strong_candidates.append((0, href, text))
                if strong_candidates:
                    strong_candidates.sort(key=lambda item: (item[0], len(item[2])))
                    best_href = strong_candidates[-1][1]
                    if best_href:
                        self._driver.get(best_href)
                        time.sleep(2)
                        return self._driver.current_url

                for link in links:
                    try:
                        href = link.get_attribute("href") or ""
                        text = link.text or ""
                    except Exception:
                        continue
                    if not href:
                        continue
                    score = self._score_detail_candidate(href, text, target_token, source_id)
                    if score > best_score:
                        best_score = score
                        best_href = href
                if best_href:
                    self._driver.get(best_href)
                    time.sleep(2)
                    return self._driver.current_url

            selectors = script.get("result_link_selectors", ["a[href]"])
            links = []
            with self._temporary_implicit_wait(self._probe_implicit_wait):
                for selector in selectors:
                    try:
                        links.extend(self._driver.find_elements(By.CSS_SELECTOR, selector))
                    except Exception:
                        continue
            for link in links:
                try:
                    href = link.get_attribute("href") or ""
                    text = link.text or ""
                except Exception:
                    continue
                if not href or href.startswith("javascript:") or href.startswith("#"):
                    continue
                score = self._score_detail_candidate(href, text, target_token, source_id)
                if score > best_score:
                    best_score = score
                    best_href = href
            if best_href:
                self._driver.get(best_href)
                time.sleep(2)
                return self._driver.current_url
        except Exception:
            pass
        return ""

    def get_available_sources(self, standard_type: str = None) -> List[Dict]:
        """获取可用的下载源"""
        sources = []
        active_ids = ACTIVE_DOWNLOAD_SOURCE_IDS or list(DOWNLOAD_SOURCES.keys())
        for source_id in active_ids:
            source = DOWNLOAD_SOURCES[source_id]
            if standard_type:
                if "all" in source.get("standard_type", []) or standard_type in source.get("standard_type", []):
                    sources.append({
                        "id": source_id,
                        "name": source["name"],
                        "type": source["type"],
                        "priority": source.get("priority", 99),
                    })
            else:
                sources.append({
                    "id": source_id,
                    "name": source["name"],
                    "type": source["type"],
                    "priority": source.get("priority", 99),
                })

        sources.sort(key=lambda x: x["priority"])
        return sources

    async def download_from_gb_openstd(self, standard_number: str) -> DownloadResult:
        """从国家标准全文公开系统获取在线查看链接"""
        await self._init_browser(headless=True)
        # 如果已降级到 Selenium，直接走同步备用链路
        if not self._browser and self._driver:
            return await asyncio.to_thread(
                self._download_from_gb_openstd_selenium_sync,
                standard_number,
            )
        page = await self._context.new_page()

        try:
            parsed = self.parse_standard_number(standard_number)
            search_term = parsed["normalized"] or standard_number
            logger.info(f"[国家标准] 正在搜索: {search_term}")
            self._report(
                10,
                100,
                f"正在搜索 {standard_number}",
                {"source": "国家标准全文公开系统", "standard": standard_number},
            )

            await page.goto("https://openstd.samr.gov.cn/bzgk/gb/std_list", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(1)
            await page.fill("input[name='search1']", search_term)
            await page.click("button#search1")
            await asyncio.sleep(2)

            row = page.locator("tr", has_text=(parsed["normalized"] or standard_number)).first
            row_count = await page.locator("tr", has_text=(parsed["normalized"] or standard_number)).count()
            if row_count <= 0:
                return DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source="国家标准全文公开系统",
                    message="未找到相关标准",
                )

            actual_standard = ""
            try:
                row_text = await row.inner_text()
                match = re.search(r"(GB/T\s*\d+(?:\.\d+)?-\d{4}|GB\s*\d+(?:\.\d+)?-\d{4})", row_text, re.IGNORECASE)
                if match:
                    actual_standard = self._format_actual_standard(match.group(1))
            except Exception as e:
                logger.warning(f"[国家标准] 提取标准号失败: {e}")

            if not actual_standard:
                actual_standard = self._format_actual_standard(standard_number)

            button = row.locator("button:has-text('查看详细')").first
            async with page.expect_popup() as popup_info:
                await button.click()
            detail_page = await popup_info.value
            await detail_page.wait_for_load_state("domcontentloaded", timeout=30000)
            await asyncio.sleep(1)
            view_url = detail_page.url
            detail_title = await detail_page.title()
            title_standard = self._extract_standard_from_title(detail_title)
            if title_standard:
                actual_standard = title_standard

            self._report(100, 100, "获取链接成功", {"standard": actual_standard})

            return DownloadResult(
                standard_number=actual_standard,
                success=True,
                download_url=view_url,
                source="国家标准全文公开系统",
                message="在线预览链接",
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
            try:
                if 'detail_page' in locals() and detail_page:
                    await detail_page.close()
            except Exception:
                pass
            await page.close()

    async def download_from_bzfsc(self, standard_number: str) -> DownloadResult:
        """从标准下载网下载"""
        await self._init_browser(headless=True)
        page = await self._context.new_page()

        try:
            search_url = f"http://www.bzfsc.com/search/{standard_number.replace('/', '_')}"
            logger.info(f"正在搜索: {search_url}")

            await page.goto(search_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            # 查找下载链接
            download_links = await page.query_selector_all('a:has-text("下载"), a[href*="download"]')

            if not download_links:
                return DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source="标准下载网",
                    message="未找到下载链接"
                )

            # 点击下载
            await download_links[0].click()
            await asyncio.sleep(5)

            # 检查下载的文件
            pdf_files = list(self.download_dir.glob(f"{standard_number.replace('/', '_')}*.pdf"))
            if pdf_files:
                pdf_path = pdf_files[-1]
                file_size = pdf_path.stat().st_size
                return DownloadResult(
                    standard_number=standard_number,
                    success=True,
                    file_path=str(pdf_path),
                    download_url=f"/api/standards/library/{pdf_path.name}",
                    source="标准下载网",
                    message=f"下载成功，文件大小: {file_size / 1024:.1f}KB",
                    file_size=file_size
                )

            return DownloadResult(
                standard_number=standard_number,
                success=False,
                source="标准下载网",
                message="下载失败"
            )

        except Exception as e:
            logger.error(f"标准下载网下载失败: {e}")
            return DownloadResult(
                standard_number=standard_number,
                success=False,
                source="标准下载网",
                message=f"下载出错: {str(e)}"
            )
        finally:
            await page.close()

    async def download_from_csres(self, standard_number: str) -> DownloadResult:
        """从工标网下载"""
        await self._init_browser(headless=True)
        page = await self._context.new_page()

        try:
            search_url = f"http://www.csres.com/search?keyword={standard_number.replace(' ', '+')}"
            logger.info(f"正在搜索: {search_url}")

            await page.goto(search_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            # 查找搜索结果
            result_links = await page.query_selector_all('.result a, .list a, a[href*="detail"]')

            if not result_links:
                return DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source="工标网",
                    message="未找到相关标准"
                )

            # 点击第一个结果
            await result_links[0].click()
            await asyncio.sleep(3)

            # 查找下载按钮
            download_btn = await page.query_selector('a:has-text("下载"), button:has-text("下载")')

            if download_btn:
                await download_btn.click()
                await asyncio.sleep(5)

                # 检查下载的文件
                pdf_files = list(self.download_dir.glob(f"{standard_number.replace('/', '_')}*.pdf"))
                if pdf_files:
                    pdf_path = pdf_files[-1]
                    file_size = pdf_path.stat().st_size
                    return DownloadResult(
                        standard_number=standard_number,
                        success=True,
                        file_path=str(pdf_path),
                        download_url=f"/api/standards/library/{pdf_path.name}",
                        source="工标网",
                        message=f"下载成功，文件大小: {file_size / 1024:.1f}KB",
                        file_size=file_size
                    )

            return DownloadResult(
                standard_number=standard_number,
                success=False,
                source="工标网",
                message="未找到下载链接"
            )

        except Exception as e:
            logger.error(f"工标网下载失败: {e}")
            return DownloadResult(
                standard_number=standard_number,
                success=False,
                source="工标网",
                message=f"下载出错: {str(e)}"
            )
        finally:
            await page.close()

    async def download_from_foodmate(self, standard_number: str) -> DownloadResult:
        """从食品伙伴网下载"""
        await self._init_browser(headless=True)
        # 如果已降级到 Selenium，直接走同步备用链路
        if not self._browser and self._driver:
            return await asyncio.to_thread(
                self._download_from_foodmate_selenium_sync,
                standard_number,
            )
        page = await self._context.new_page()

        try:
            parsed = self.parse_standard_number(standard_number)
            search_term = parsed["normalized"] or standard_number

            search_term = search_term.replace(" ", "+").replace("/", "%2F")
            search_url = f"https://down.foodmate.net/standard/search.php?kw={search_term}"
            logger.info(f"[食品伙伴网] 正在搜索: {search_url}")
            self._report(
                10,
                100,
                f"正在搜索 {standard_number}",
                {"source": "食品伙伴网", "standard": standard_number},
            )

            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(1)

            ranked_candidates = []
            seen_hrefs = set()
            selectors = [
                '.list.flck a[href*="/standard/sort/"][href$=".html"]',
                'a[href*="/standard/sort/"][href$=".html"]',
            ]
            target_token = re.sub(
                r"[^A-Z0-9]", "", (parsed["normalized"] or standard_number).upper()
            )
            best_score = -1
            for selector in selectors:
                links = await page.query_selector_all(selector)
                for link in links:
                    href = await link.get_attribute("href")
                    if href and re.search(r"/standard/sort/\d+/\d+\.html$", href):
                        try:
                            text = await link.inner_text()
                        except Exception:
                            text = ""
                        text_token = re.sub(r"[^A-Z0-9]", "", (text or "").upper())
                        href_token = re.sub(r"[^A-Z0-9]", "", (href or "").upper())
                        score = 0
                        if target_token and (
                            target_token == text_token or target_token == href_token
                        ):
                            score = 100
                        elif target_token and (
                            target_token in text_token or target_token in href_token
                        ):
                            score = 90
                        elif target_token and (
                            text_token in target_token or href_token in target_token
                        ):
                            score = 80
                        else:
                            score = 10
                        if href not in seen_hrefs:
                            seen_hrefs.add(href)
                            ranked_candidates.append((score, href, selector))
                            best_score = max(best_score, score)
                            logger.info(
                                f"[食品伙伴网] 候选链接: {href}, score={score}, selector={selector}"
                            )

            ranked_candidates.sort(key=lambda item: item[0], reverse=True)
            if not ranked_candidates:
                return DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source="食品伙伴网",
                    message="未找到相关文档",
                )

            best_candidate = None
            for base_score, href, _ in ranked_candidates:
                logger.info(f"[食品伙伴网] 尝试详情页: {href}")
                self._report(30, 100, "进入详情页", {"standard": standard_number})

                await page.goto(href, wait_until="networkidle", timeout=30000)
                await asyncio.sleep(1)

                actual_standard = standard_number
                try:
                    page_title = await page.title()
                    title_standard = self._extract_standard_from_title(page_title)
                    if title_standard:
                        actual_standard = title_standard
                        logger.info(f"[食品伙伴网] 从标题提取到实际标准号: {actual_standard}")
                    else:
                        page_content = await page.content()
                        std_patterns = [
                            r"标准号[:：]\s*([A-Z0-9/\.\-\s]+)",
                            r"标准编号[:：]\s*([A-Z0-9/\.\-\s]+)",
                        ]
                        for pattern in std_patterns:
                            matches = re.findall(pattern, page_content, re.IGNORECASE)
                            if matches:
                                actual_standard = self._format_actual_standard(matches[0].strip())
                                logger.info(f"[食品伙伴网] 提取到实际标准号: {actual_standard}")
                                break
                except Exception as e:
                    logger.warning(f"[食品伙伴网] 提取标准号失败: {e}")

                actual_standard = self._format_actual_standard(actual_standard)
                if not actual_standard:
                    actual_standard = self._format_actual_standard(standard_number)

                download_link = await page.query_selector('a[href*="down.php"]')
                if not download_link:
                    logger.warning(f"[食品伙伴网] 候选结果未找到下载链接: {actual_standard}")
                    continue

                candidate_score = base_score + self._standard_similarity_score(
                    standard_number, actual_standard
                )
                logger.info(
                    f"[食品伙伴网] 候选详情评分: 请求={standard_number}, 命中={actual_standard}, score={candidate_score}"
                )
                if not best_candidate or candidate_score > best_candidate["score"]:
                    best_candidate = {
                        "href": href,
                        "actual_standard": actual_standard,
                        "score": candidate_score,
                    }

            if not best_candidate:
                return DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source="食品伙伴网",
                    message="未找到可下载结果",
                )

            await page.goto(best_candidate["href"], wait_until="networkidle", timeout=30000)
            await asyncio.sleep(1)
            actual_standard = best_candidate["actual_standard"]
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
                message=f"下载出错: {str(e)}"
            )
        finally:
            await page.close()

    def _find_recent_download_file(self, prefixes: List[str], timeout: int = 20):
        deadline = time.time() + timeout
        patterns = []
        for prefix in prefixes:
            safe = self._format_actual_standard(prefix).replace("/", "_").replace(" ", "_")
            patterns.extend([f"{safe}*.pdf", f"{safe}*.zip", f"{safe}*.doc", f"{safe}*.docx"])

        while time.time() < deadline:
            for pattern in patterns:
                matches = sorted(
                    self.download_dir.glob(pattern),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if matches:
                    return matches[0]
            time.sleep(1)
        return None

    def _rename_download_file(self, file_path: Path, target_standard: str) -> Path:
        """将下载文件重命名为实际标准号，避免沿用模糊查询词。"""
        if not file_path or not file_path.exists():
            return file_path

        safe_standard = self._format_actual_standard(target_standard)
        if not safe_standard:
            return file_path

        target_path = file_path.with_name(f"{safe_standard}{file_path.suffix.lower()}")
        if target_path == file_path:
            return file_path

        try:
            if target_path.exists():
                target_path.unlink()
            file_path.replace(target_path)
            return target_path
        except Exception as e:
            logger.warning(f"下载文件重命名失败 {file_path} -> {target_path}: {e}")
            return file_path

    def _download_from_gb_openstd_selenium_sync(self, standard_number: str) -> DownloadResult:
        self._init_selenium(headless=True)
        parsed = self.parse_standard_number(standard_number)
        search_term = parsed["normalized"] or standard_number
        logger.info(f"[国家标准/Selenium] 正在搜索: {search_term}")

        try:
            self._driver.get("https://openstd.samr.gov.cn/bzgk/gb/std_list")
            time.sleep(2)
            from selenium.webdriver.common.by import By
            from selenium.webdriver.common.keys import Keys

            input_el = self._driver.find_elements(By.CSS_SELECTOR, "input[name='search1']")
            if not input_el:
                return DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source="国家标准全文公开系统",
                    message="未找到相关标准",
                )

            input_el = input_el[0]
            input_el.clear()
            input_el.send_keys(search_term)
            input_el.send_keys(Keys.ENTER)
            time.sleep(2)
            search_buttons = self._driver.find_elements(By.CSS_SELECTOR, "button#search1")
            if search_buttons:
                try:
                    search_buttons[0].click()
                    time.sleep(2)
                except Exception:
                    pass

            rows = self._driver.find_elements(By.CSS_SELECTOR, "tr")
            row = None
            for cand in rows:
                try:
                    row_text = cand.text or ""
                except Exception:
                    continue
                if search_term.replace(" ", "") in row_text.replace(" ", ""):
                    row = cand
                    break
            if not row:
                return DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source="国家标准全文公开系统",
                    message="未找到相关标准",
                )

            actual_standard = self._format_actual_standard(standard_number)
            try:
                row_text = row.text or ""
                match = re.search(r"(GB/T\s*\d+(?:\.\d+)?-\d{4}|GB\s*\d+(?:\.\d+)?-\d{4})", row_text, re.IGNORECASE)
                if match:
                    actual_standard = self._format_actual_standard(match.group(1))
            except Exception as e:
                logger.warning(f"[国家标准/Selenium] 提取标准号失败: {e}")

            detail_buttons = row.find_elements(By.CSS_SELECTOR, "button")
            detail_button = None
            for btn in detail_buttons:
                try:
                    if "查看详细" in (btn.text or ""):
                        detail_button = btn
                        break
                except Exception:
                    continue
            if not detail_button:
                return DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source="国家标准全文公开系统",
                    message="未找到详情按钮",
                )

            before_handles = set(self._driver.window_handles)
            detail_button.click()
            time.sleep(2)
            after_handles = set(self._driver.window_handles)
            new_handles = list(after_handles - before_handles)
            if new_handles:
                self._driver.switch_to.window(new_handles[0])
            view_url = self._driver.current_url
            title_standard = self._extract_standard_from_title(self._driver.title or "")
            if title_standard:
                actual_standard = title_standard

            return DownloadResult(
                standard_number=actual_standard,
                success=True,
                download_url=view_url,
                source="国家标准全文公开系统",
                message="在线预览链接",
            )
        except Exception as e:
            return DownloadResult(
                standard_number=standard_number,
                success=False,
                source="国家标准全文公开系统",
                message=f"获取链接出错: {str(e)}",
            )

    def _download_from_bzfsc_selenium_sync(self, standard_number: str) -> DownloadResult:
        self._init_selenium(headless=True)
        from selenium.webdriver.common.by import By

        search_url = f"http://www.bzfsc.com/search/{standard_number.replace('/', '_')}"
        logger.info(f"[标准下载网/Selenium] 正在搜索: {search_url}")
        try:
            self._driver.get(search_url)
            time.sleep(2)

            selectors = [
                'a[href*="/standard/"]',
                'a[href*="detail"]',
                'a[href*="download"]',
                'a:contains("下载")',
            ]
            result_link = self._selenium_find_best_link(
                self._driver,
                selectors[:3],
                re.sub(r"[^A-Z0-9]", "", standard_number.upper()),
            )

            target_url = search_url
            if result_link:
                href = result_link.get_attribute("href") or ""
                if href:
                    target_url = href
                    self._driver.get(href)
                    time.sleep(2)

            html = self._driver.page_source or ""
            if "下载" not in html and "download" not in html.lower() and not result_link:
                return DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source="标准下载网",
                    message="未找到下载链接",
                )

            download_link = None
            try:
                candidates = self._driver.find_elements(
                    By.CSS_SELECTOR,
                    'a[href*="download"], a[href*="down"], a:link',
                )
                for cand in candidates:
                    text = (cand.text or "").strip()
                    href = cand.get_attribute("href") or ""
                    if "下载" in text or "download" in href.lower() or "down" in href.lower():
                        download_link = cand
                        break
            except Exception:
                download_link = None

            if download_link:
                try:
                    download_link.click()
                    file_path = self._find_recent_download_file([standard_number], timeout=15)
                    if file_path:
                        return DownloadResult(
                            standard_number=self._format_actual_standard(standard_number),
                            success=True,
                            file_path=str(file_path),
                            download_url=f"/api/standards/library/{file_path.name}",
                            source="标准下载网",
                            message=f"下载成功，文件大小: {file_path.stat().st_size / 1024:.1f}KB",
                            file_size=file_path.stat().st_size,
                        )
                except Exception as e:
                    logger.warning(f"[标准下载网/Selenium] 点击下载失败: {e}")

            if not result_link:
                return DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source="标准下载网",
                    message="未找到下载链接",
                )

            return DownloadResult(
                standard_number=self._format_actual_standard(standard_number),
                success=True,
                download_url=target_url,
                source="标准下载网",
                message="请打开链接查看下载结果",
            )
        except Exception as e:
            return DownloadResult(
                standard_number=standard_number,
                success=False,
                source="标准下载网",
                message=f"下载出错: {str(e)}",
            )

    def _download_from_csres_selenium_sync(self, standard_number: str) -> DownloadResult:
        self._init_selenium(headless=True)
        from selenium.webdriver.common.by import By

        search_url = f"http://www.csres.com/search?keyword={standard_number.replace(' ', '+')}"
        logger.info(f"[工标网/Selenium] 正在搜索: {search_url}")
        try:
            self._driver.get(search_url)
            time.sleep(2)

            result_link = self._selenium_find_best_link(
                self._driver,
                [
                    'a[href*="/standard/"]',
                    'a[href*="detail"]',
                    '.result a',
                    '.list a',
                ],
                re.sub(r"[^A-Z0-9]", "", standard_number.upper()),
            )

            target_url = search_url
            if result_link:
                href = result_link.get_attribute("href") or ""
                if href:
                    target_url = href
                    self._driver.get(href)
                    time.sleep(2)

            html = self._driver.page_source or ""
            if "标准" not in html and "result" not in html.lower() and not result_link:
                return DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source="工标网",
                    message="未找到相关标准",
                )

            download_link = None
            try:
                candidates = self._driver.find_elements(
                    By.CSS_SELECTOR,
                    'a[href*="download"], a[href*="down"], button',
                )
                for cand in candidates:
                    text = (cand.text or "").strip()
                    href = cand.get_attribute("href") or ""
                    if "下载" in text or "download" in href.lower() or "down" in href.lower():
                        download_link = cand
                        break
            except Exception:
                download_link = None

            if download_link:
                try:
                    download_link.click()
                    file_path = self._find_recent_download_file([standard_number], timeout=15)
                    if file_path:
                        return DownloadResult(
                            standard_number=self._format_actual_standard(standard_number),
                            success=True,
                            file_path=str(file_path),
                            download_url=f"/api/standards/library/{file_path.name}",
                            source="工标网",
                            message=f"下载成功，文件大小: {file_path.stat().st_size / 1024:.1f}KB",
                            file_size=file_path.stat().st_size,
                        )
                except Exception as e:
                    logger.warning(f"[工标网/Selenium] 点击下载失败: {e}")

            if not result_link:
                return DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source="工标网",
                    message="未找到相关标准",
                )

            return DownloadResult(
                standard_number=self._format_actual_standard(standard_number),
                success=True,
                download_url=target_url,
                source="工标网",
                message="请打开链接查看下载结果",
            )
        except Exception as e:
            return DownloadResult(
                standard_number=standard_number,
                success=False,
                source="工标网",
                message=f"下载出错: {str(e)}",
            )

    def _download_from_foodmate_selenium_sync(self, standard_number: str) -> DownloadResult:
        self._init_selenium(headless=True)
        from selenium.webdriver.common.by import By

        parsed = self.parse_standard_number(standard_number)
        search_term = parsed["normalized"] or standard_number
        search_term = search_term.replace(" ", "+").replace("/", "%2F")
        search_url = f"https://down.foodmate.net/standard/search.php?kw={search_term}"
        logger.info(f"[食品伙伴网/Selenium] 正在搜索: {search_url}")
        try:
            self._driver.get(search_url)
            time.sleep(2)
            selectors = [
                '.list.flck a[href*="/standard/sort/"][href$=".html"]',
                'a[href*="/standard/sort/"][href$=".html"]',
            ]
            target_token = re.sub(r"[^A-Z0-9]", "", (parsed["normalized"] or standard_number).upper())
            result_link = self._selenium_find_best_link(self._driver, selectors, target_token)
            if not result_link:
                return DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source="食品伙伴网",
                    message="未找到相关文档",
                )

            href = result_link.get_attribute("href") or ""
            if href:
                self._driver.get(href)
                time.sleep(2)

            actual_standard = standard_number
            try:
                page_title = self._driver.title or ""
                title_standard = self._extract_standard_from_title(page_title)
                if title_standard:
                    actual_standard = title_standard
                else:
                    page_content = self._driver.page_source or ""
                    std_patterns = [
                        r"标准号[:：]\s*([A-Z0-9/\.\-\s]+)",
                        r"标准编号[:：]\s*([A-Z0-9/\.\-\s]+)",
                    ]
                    for pattern in std_patterns:
                        matches = re.findall(pattern, page_content, re.IGNORECASE)
                        if matches:
                            actual_standard = self._format_actual_standard(matches[0].strip())
                            break
            except Exception:
                pass

            actual_standard = self._format_actual_standard(actual_standard) or self._format_actual_standard(standard_number)
            download_link = None
            try:
                links = self._driver.find_elements(By.CSS_SELECTOR, 'a[href*="down.php"]')
                download_link = links[0] if links else None
            except Exception:
                download_link = None

            if download_link:
                try:
                    download_link.click()
                    file_path = self._find_recent_download_file([actual_standard, standard_number], timeout=20)
                    if file_path:
                        file_path = self._rename_download_file(file_path, actual_standard)
                        return DownloadResult(
                            standard_number=actual_standard,
                            success=True,
                            file_path=str(file_path),
                            download_url=f"/api/standards/library/{file_path.name}",
                            source="食品伙伴网",
                            message=f"下载成功，文件大小: {file_path.stat().st_size / 1024:.1f}KB",
                            file_size=file_path.stat().st_size,
                        )
                except Exception as e:
                    logger.warning(f"[食品伙伴网/Selenium] 点击下载失败: {e}")

            return DownloadResult(
                standard_number=actual_standard,
                success=True,
                download_url=href or search_url,
                source="食品伙伴网",
                message="找到可下载页面，请手动打开链接继续下载",
            )
        except Exception as e:
            return DownloadResult(
                standard_number=standard_number,
                success=False,
                source="食品伙伴网",
                message=f"下载出错: {str(e)}",
            )

    def _download_generic_search_source_sync(
        self, source_id: str, standard_number: str
    ) -> DownloadResult:
        self._init_selenium(headless=True)
        source = DOWNLOAD_SOURCES.get(source_id, {})
        source_name = source.get("name", source_id)
        search_url_tmpl = source.get("search_url")
        if not search_url_tmpl:
            return DownloadResult(
                standard_number=standard_number,
                success=False,
                source=source_name,
                message="未配置搜索地址",
            )

        parsed = self.parse_standard_number(standard_number)
        search_term = parsed["normalized"] or standard_number
        search_term = search_term.replace(" ", "+").replace("/", "%2F")
        search_url = search_url_tmpl.format(query=search_term)
        logger.info(f"[{source_name}/Selenium] 正在搜索: {search_url}")

        from selenium.webdriver.common.by import By

        try:
            self._driver.get(search_url)
            time.sleep(2)

            if source_id in ENTRY_ONLY_DOWNLOAD_SOURCES:
                current_url = self._driver.current_url or search_url
                page_title = (self._driver.title or "").strip()
                return DownloadResult(
                    standard_number=self._format_actual_standard(standard_number),
                    success=True,
                    download_url=current_url,
                    source=source_name,
                    message=page_title or source.get("notes") or "请打开官方入口继续查看标准信息",
                )

            target_token = re.sub(
                r"[^A-Z0-9]", "", (parsed["normalized"] or standard_number).upper()
            )
            result_link = self._selenium_find_best_link(
                self._driver,
                [
                    'a[href*="/standard/"]',
                    'a[href*="detail"]',
                    'a[href*="view"]',
                    'a[href*="read"]',
                    'a[href*="doc"]',
                ],
                target_token,
            )

            target_url = search_url
            if result_link:
                href = result_link.get_attribute("href") or ""
                if href:
                    target_url = href
                    self._driver.get(href)
                    time.sleep(2)

            page_text = self._driver.page_source or ""
            actual_standard = self._format_actual_standard(standard_number)
            std_patterns = [
                r"GB/T?\s*\d+[\.\-]\d+[\.\-]?\d*",
                r"GB\s*\d+[\.\-]\d+[\.\-]?\d*",
                r"ISO/IEC\s*\d+[\.\-]?\d*",
                r"标准号[:：]\s*([A-Z0-9/\.\-\s]+)",
                r"标准编号[:：]\s*([A-Z0-9/\.\-\s]+)",
            ]
            for pattern in std_patterns:
                matches = re.findall(pattern, page_text, re.IGNORECASE)
                if matches:
                    actual_standard = self._format_actual_standard(matches[0].strip())
                    break

            download_link = None
            try:
                for cand in self._driver.find_elements(
                    By.CSS_SELECTOR,
                    'a[href*="download"], a[href*="down"], a[href*=".pdf"], button',
                ):
                    text = (cand.text or "").strip()
                    href = cand.get_attribute("href") or ""
                    if "下载" in text or "download" in href.lower() or "down" in href.lower() or ".pdf" in href.lower():
                        download_link = cand
                        break
            except Exception:
                download_link = None

            if download_link:
                try:
                    download_link.click()
                    file_path = self._find_recent_download_file(
                        [actual_standard, standard_number], timeout=20
                    )
                    if file_path:
                        return DownloadResult(
                            standard_number=actual_standard,
                            success=True,
                            file_path=str(file_path),
                            download_url=f"/api/standards/library/{file_path.name}",
                            source=source_name,
                            message=f"下载成功，文件大小: {file_path.stat().st_size / 1024:.1f}KB",
                            file_size=file_path.stat().st_size,
                        )
                except Exception as e:
                    logger.warning(f"[{source_name}/Selenium] 点击下载失败: {e}")

            if not result_link and not download_link:
                return DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source=source_name,
                    message="未找到相关文档",
                )

            return DownloadResult(
                standard_number=actual_standard,
                success=True,
                download_url=target_url,
                source=source_name,
                message="请打开链接查看或下载标准文档",
            )
        except Exception as e:
            return DownloadResult(
                standard_number=standard_number,
                success=False,
                source=source_name,
                message=f"下载出错: {str(e)}",
            )

    async def _download_generic_search_source_async(
        self, source_id: str, standard_number: str
    ) -> DownloadResult:
        return await asyncio.to_thread(
            self._download_generic_search_source_sync, source_id, standard_number
        )

    async def _execute_single_source(
        self,
        source_id: str,
        standard_number: str,
        source_methods: Dict[str, callable],
        selenium_fallback_methods: Dict[str, callable],
    ) -> DownloadResult:
        """执行单个下载源，内部负责 Playwright -> Selenium 回退。"""
        try:
            self._raise_if_cancelled()
            method = source_methods.get(source_id)
            fallback = selenium_fallback_methods.get(source_id)
            direct_selenium_only = bool(fallback) and self._should_use_direct_selenium_fallback()

            if source_id == "miit_std" and direct_selenium_only:
                logger.info("miit_std 依赖前端 SPA 交互，当前仅有 Selenium，跳过并转后续候选源")
                return DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source=DOWNLOAD_SOURCES.get(source_id, {}).get("name", source_id),
                    message="当前环境 Playwright 不可用，已跳过工信 SPA 源",
                )

            if direct_selenium_only and fallback:
                return await asyncio.to_thread(fallback, standard_number)

            if method:
                result = await self._run_with_cancel(
                    method(standard_number),
                    self.source_timeout,
                    f"{source_id}下载",
                )
            else:
                result = DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source=source_id,
                    message="暂不支持该下载源，请联系开发者添加支持",
                )

            if not result.success and source_id in selenium_fallback_methods:
                logger.info(f"{source_id} 的 Playwright 结果不理想，尝试 Selenium 备用路径")
                try:
                    result = await asyncio.to_thread(
                        selenium_fallback_methods[source_id], standard_number
                    )
                except Exception as fallback_error:
                    logger.warning(f"{source_id} Selenium 备用失败: {fallback_error}")
                    result = DownloadResult(
                        standard_number=standard_number,
                        success=False,
                        source=DOWNLOAD_SOURCES.get(source_id, {}).get("name", source_id),
                        message=f"Selenium 备用失败: {fallback_error}",
                    )

            return result
        except Exception as e:
            if "下载已取消" in str(e):
                raise
            if self._is_browser_bootstrap_error(e):
                logger.error(f"下载源 {source_id} 浏览器初始化失败，停止继续尝试其他下载源: {e}")
                return DownloadResult(
                    standard_number=standard_number,
                    success=False,
                    source=DOWNLOAD_SOURCES.get(source_id, {}).get("name", source_id),
                    message=f"异常: {str(e)}",
                )

            if fallback:
                logger.warning(f"下载源 {source_id} Playwright 异常，尝试 Selenium 备用: {e}")
                try:
                    return await asyncio.to_thread(fallback, standard_number)
                except Exception as fallback_error:
                    logger.error(f"下载源 {source_id} Selenium 备用异常: {fallback_error}")
                    e = fallback_error

            logger.error(f"下载源 {source_id} 异常: {e}")
            return DownloadResult(
                standard_number=standard_number,
                success=False,
                source=DOWNLOAD_SOURCES.get(source_id, {}).get("name", source_id),
                message=f"异常: {str(e)}",
            )

    async def _resolve_secondary_result(
        self,
        standard_number: str,
        candidate_ids: List[str],
        source_methods: Dict[str, callable],
        selenium_fallback_methods: Dict[str, callable],
    ) -> Optional[DownloadResult]:
        """顺序尝试分类平台，只返回一个分类结果。"""
        if not candidate_ids:
            return None

        attempted: List[DownloadResult] = []
        for source_id in candidate_ids:
            result = await self._execute_single_source(
                source_id,
                standard_number,
                source_methods,
                selenium_fallback_methods,
            )
            attempted.append(result)
            if result.success:
                return result
            if self._is_browser_bootstrap_error(result.message):
                break

        if len(attempted) == 1:
            return attempted[0]

        source_names = [r.source or DOWNLOAD_SOURCES.get(candidate_ids[idx], {}).get("name", candidate_ids[idx]) for idx, r in enumerate(attempted)]
        messages = [f"{name}: {result.message}" for name, result in zip(source_names, attempted)]
        return DownloadResult(
            standard_number=standard_number,
            success=False,
            source=" / ".join(source_names),
            message="；".join(messages),
        )

    async def download(
        self,
        standard_number: str,
        sources: List[str] = None,
        headless: bool = True
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

        primary_sources, secondary_candidates = self._split_two_tier_plan(
            standard_number, sources
        )
        logger.info(
            f"下载计划 {standard_number}: 第一层={primary_sources}, 第二层候选={secondary_candidates}"
        )

        # 下载源映射到方法
        source_methods = {
            "gb_openstd": self.download_from_gb_openstd,
            "foodmate": self.download_from_foodmate,
        }
        selenium_fallback_methods = {
            "gb_openstd": self._download_from_gb_openstd_selenium_sync,
            "foodmate": self._download_from_foodmate_selenium_sync,
        }
        for source_id in ACTIVE_DOWNLOAD_SOURCE_IDS:
            if source_id in source_methods:
                continue
            source_methods[source_id] = (
                lambda standard_number, source_id=source_id: self._search_and_open_detail_async(
                    source_id, standard_number
                )
            )
            selenium_fallback_methods[source_id] = (
                lambda standard_number, source_id=source_id: self._search_and_open_detail_sync(
                    source_id, standard_number
                )
            )

        primary_success = False
        for source_id in primary_sources:
            result = await self._execute_single_source(
                source_id,
                standard_number,
                source_methods,
                selenium_fallback_methods,
            )
            results.append(result)
            if result.success:
                primary_success = True
                break

        if (not primary_success) and secondary_candidates:
            secondary_result = await self._resolve_secondary_result(
                standard_number,
                secondary_candidates,
                source_methods,
                selenium_fallback_methods,
            )
            if secondary_result is not None:
                results.append(secondary_result)

        return results


# 便捷函数
async def download_standard(standard_number: str, download_dir: str = None) -> DownloadResult:
    """下载单个标准"""
    downloader = StandardAutoDownloader(download_dir)
    try:
        results = await downloader.download(standard_number)
        if not results:
            return DownloadResult(
                standard_number=standard_number,
                success=False,
                message="下载失败"
            )

        for result in results:
            if result.success and result.file_path:
                return result

        for result in results:
            if result.success:
                return result

        return results[0]
    finally:
        await downloader.close()


# 测试
if __name__ == "__main__":
    async def test():
        result = await download_standard("GB/T 19001-2016")
        print(f"结果: {result}")

    asyncio.run(test())
