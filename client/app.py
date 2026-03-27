#!/usr/bin/env python3
"""
Jingxi Client - Windows客户端
"""

# ==================== 配置国内镜像源（多备用源，国内网络使用） ====================
import os
import sys
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# 1. Python包镜像源配置（多个备用源）
PYTHON_MIRRORS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple",  # 清华源（首选）
    "https://mirrors.aliyun.com/pypi/simple/",  # 阿里云源（备用1）
    "https://pypi.mirrors.ustc.edu.cn/simple/",  # 中科大源（备用2）
    "https://mirrors.cloud.tencent.com/pypi/simple",  # 腾讯云源（备用3）
    "https://mirrors.huaweicloud.com/repository/pypi/simple",  # 华为云源（备用4）
]

# 设置首选镜像源
os.environ["PIP_INDEX_URL"] = PYTHON_MIRRORS[0]
os.environ["PIP_TRUSTED_HOST"] = (
    "pypi.tuna.tsinghua.edu.cn,mirrors.aliyun.com,pypi.mirrors.ustc.edu.cn,mirrors.cloud.tencent.com,mirrors.huaweicloud.com"
)

# 2. ChromeDriver镜像源配置（多个备用源）
CHROMEDRIVER_MIRRORS = [
    "https://registry.npmmirror.com/-/binary/chromedriver/",  # npm淘宝镜像（首选）
    "https://chromedriver.storage.googleapis.com/",  # Google官方（备用1）
    "https://edgedl.me.gvt1.com/edgedl/chrome/chrome-for-testing/",  # Google国内CDN（备用2）
    "https://npm.taobao.org/mirrors/chromedriver/",  # 淘宝旧镜像（备用3）
]

os.environ["WDM_DRIVER_URL"] = CHROMEDRIVER_MIRRORS[0]
os.environ["WDM_SSL_VERIFY"] = "0"  # 禁用SSL验证

# 3. Playwright浏览器镜像源配置（多个备用源）
PLAYWRIGHT_MIRRORS = [
    "https://npmmirror.com/mirrors/playwright/",  # npm淘宝镜像（首选）
    "https://playwright.azureedge.net/",  # Azure官方（备用1）
    "https://github.com/microsoft/playwright/releases/download/",  # GitHub（备用2）
    "https://mirrors.aliyun.com/npm-playwright/",  # 阿里云镜像（备用3）
]

os.environ["PLAYWRIGHT_DOWNLOAD_HOST"] = PLAYWRIGHT_MIRRORS[0]
os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "0"

# 4. 通用网络配置（优化国内网络）
os.environ["REQUESTS_CA_BUNDLE"] = ""
os.environ["CURL_CA_BUNDLE"] = ""
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["ALL_PROXY"] = ""
os.environ["NO_PROXY"] = "localhost,127.0.0.1,.local"

print(f"[配置] Python镜像源: {PYTHON_MIRRORS[0]}")
print(f"[配置] ChromeDriver镜像源: {CHROMEDRIVER_MIRRORS[0]}")
print(f"[配置] Playwright镜像源: {PLAYWRIGHT_MIRRORS[0]}")

# ==================== 导入其他模块 ====================
import json
import time
import hashlib
import threading
import webbrowser
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# 目录设置
BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
DOWNLOADS_DIR = BASE_DIR / "downloads"
STATIC_DIR = BASE_DIR / "static"

for d in [CACHE_DIR, DOWNLOADS_DIR, STATIC_DIR]:
    d.mkdir(exist_ok=True)

# 配置
HUB_URL = "https://testmumu.ftir.fun"
CLIENT_ID_FILE = CACHE_DIR / "client_id.txt"

# 获取客户端ID和MAC地址
CLIENT_MAC = None
if os.path.exists(CLIENT_ID_FILE):
    with open(CLIENT_ID_FILE, "r") as f:
        CLIENT_ID = f.read().strip()
else:
    import socket
    import uuid
    import platform

    machine_info = f"{socket.gethostname()}{platform.node()}{platform.machine()}"
    try:
        mac = ":".join(
            [
                "{:02x}".format((uuid.getnode() >> elements) & 0xFF)
                for elements in range(0, 8 * 6, 8)
            ][::-1]
        )
        machine_info += mac
        CLIENT_MAC = mac
    except Exception as e:
        print(f"[警告] 获取机器特征失败: {e}")
        pass

    CLIENT_ID = hashlib.md5(machine_info.encode()).hexdigest()[:12]
    CLIENT_ID_FILE.write_text(CLIENT_ID)
    print(f"生成客户端ID: {CLIENT_ID} (基于机器特征)")

if CLIENT_MAC is None:
    import uuid

    try:
        CLIENT_MAC = ":".join(
            [
                "{:02x}".format((uuid.getnode() >> elements) & 0xFF)
                for elements in range(0, 8 * 6, 8)
            ][::-1]
        )
    except Exception as e:
        print(f"[警告] 获取MAC地址失败: {e}")
        CLIENT_MAC = "unknown"

print(f"客户端ID: {CLIENT_ID}, MAC: {CLIENT_MAC}")

# Flask应用
app = Flask(__name__, static_folder=str(STATIC_DIR))
CORS(app)

# ============ 配置解密管理 ============
import base64

# 配置相关全局变量
_encrypted_config = None  # 加密的配置（本地保存的是乱码）
_config_key = None  # 当前解密密钥（内存中）
_decrypted_config = None  # 解密后的配置（内存中）
_config_expires = None  # 配置过期时间


def decrypt_config(encrypted_b64: str, key: str) -> dict:
    """
    解密配置

    Args:
        encrypted_b64: base64编码的加密配置
        key: 解密密钥

    Returns:
        解密后的配置字典
    """
    try:
        # Base64解码
        encrypted = base64.b64decode(encrypted_b64)

        # XOR解密
        key_bytes = key.encode("utf-8")
        decrypted = bytearray()
        for i, byte in enumerate(encrypted):
            decrypted.append(byte ^ key_bytes[i % len(key_bytes)])

        # JSON解析
        config_json = decrypted.decode("utf-8")
        return json.loads(config_json)
    except Exception as e:
        print(f"[配置解密] 失败: {e}")
        return None


def is_config_valid() -> bool:
    """检查配置是否有效"""
    global _config_expires, _decrypted_config
    if _decrypted_config is None:
        return False
    if _config_expires and time.time() > _config_expires:
        return False
    return True


def update_config_key(new_key: str):
    """更新密钥并解密配置"""
    global _config_key, _decrypted_config, _config_expires, _encrypted_config

    if not _encrypted_config or not new_key:
        return False

    # 用新密钥解密
    decrypted = decrypt_config(_encrypted_config, new_key)
    if decrypted:
        _config_key = new_key
        _decrypted_config = decrypted
        _config_expires = time.time() + 300  # 5分钟后过期
        print(
            f"[配置] 密钥已更新，配置有效期至 {datetime.fromtimestamp(_config_expires).strftime('%H:%M:%S')}"
        )
        return True
    return False


def get_config() -> dict:
    """获取当前配置（如果有效）"""
    if is_config_valid():
        return _decrypted_config
    return None


# 全局状态 - 每种任务类型独立
current_tasks = {
    "extract": {
        "status": "idle",
        "progress": 0,
        "message": "",
        "result": None,
        "cancel_requested": False,
    },
    "query": {
        "status": "idle",
        "progress": 0,
        "message": "",
        "result": None,
        "cancel_requested": False,
    },
    "download": {
        "status": "idle",
        "progress": 0,
        "message": "",
        "result": None,
        "cancel_requested": False,
    },
}
progress_data = {"percentage": 0, "message": "", "details": {}, "task_type": "extract"}

# 最后一次提取的文本内容（用于前端显示）
last_extract_text = ""

# 操作统计
client_stats = {"queries": 0, "extracts": 0, "downloads": 0}

# 查询平台列表（自动轮流）- 深圳平台放最后，因为它经常卡住
QUERY_PLATFORMS = [
    "hunan",
    "shanxi",
    "jiangxi",
    "liaocheng",
    "liuan",
    "xiamen",
    "shenzhen",  # 深圳平台响应慢，放最后
]


# ============ 与服务端通信 ============
def hub_request(method, path, data=None):
    """向服务端发送请求"""
    import urllib.request
    import ssl

    url = f"{HUB_URL}{path}"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Jingxi Client/1.0",
        }
        if method == "POST":
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode(),
                headers=headers,
                method="POST",
            )
        else:
            req = urllib.request.Request(url, headers=headers)

        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"请求失败: {e}")
        return {"success": False, "error": str(e)}


# ============ 代码管理 ============
def download_code():
    """从服务端获取加密配置和密钥"""
    global _encrypted_config, _config_key, _decrypted_config, _config_expires

    print("[配置更新] 正在从服务端获取配置...")

    resp = hub_request("POST", "/api/code/all", data={"client_id": CLIENT_ID})
    if not resp.get("success"):
        print(f"[配置更新] 失败: {resp.get('error', '未知错误')}")
        return False

    data = resp.get("data", {})

    # 检查是否是旧格式（代码模块）
    if "modules" in data:
        print("[配置更新] 服务端使用旧格式，跳过")
        return False

    # 获取加密配置和密钥
    encrypted_config = data.get("encrypted_config")
    key = data.get("key")

    if not encrypted_config or not key:
        print("[配置更新] 失败: 配置数据不完整")
        return False

    # 保存加密配置（本地是乱码）
    _encrypted_config = encrypted_config
    config_file = CACHE_DIR / "config.enc"
    config_file.write_text(encrypted_config, encoding="utf-8")

    # 解密配置
    decrypted = decrypt_config(encrypted_config, key)
    if not decrypted:
        print("[配置更新] 失败: 配置解密失败")
        return False

    # 保存到内存
    _config_key = key
    _decrypted_config = decrypted
    _config_expires = time.time() + 300  # 5分钟后过期

    print(
        f"[配置更新] 成功，配置有效期至 {datetime.fromtimestamp(_config_expires).strftime('%H:%M:%S')}"
    )
    return True


def load_cached_config():
    """加载本地缓存的加密配置"""
    global _encrypted_config
    config_file = CACHE_DIR / "config.enc"
    if config_file.exists():
        _encrypted_config = config_file.read_text(encoding="utf-8")
        print("[配置] 已加载本地加密配置")
        return True
    return False


# 为了兼容旧代码，保留这些函数但修改实现
def load_code():
    """加载配置（兼容旧接口）"""
    config = get_config()
    if not config:
        raise ImportError("配置无效或已过期，请重启客户端")
    return config


def get_extractor(progress_callback=None):
    """获取提取器实例（从内置代码）"""
    # 导入内置的提取器模块
    try:
        from extractor import StandardExtractor

        return StandardExtractor(progress_callback)
    except ImportError as e:
        raise ImportError(f"提取器模块不可用: {e}")


def get_query_service(progress_callback=None):
    """获取查询服务实例（从内置代码）"""
    try:
        from query_service import StandardQueryService

        # 包装回调函数，自动添加task_type
        original_callback = progress_callback

        def wrapped_callback(current, total, message, details=None, task_type=None):
            if original_callback:
                original_callback(current, total, message, details, "query")

        return StandardQueryService(wrapped_callback if progress_callback else None)
    except ImportError as e:
        raise ImportError(f"查询服务模块不可用: {e}")


def get_downloader(progress_callback=None):
    """获取下载器实例（从内置代码）"""
    try:
        from downloader import StandardDownloader

        # 包装回调函数，自动添加task_type
        original_callback = progress_callback

        def wrapped_callback(current, total, message, details=None, task_type=None):
            if original_callback:
                original_callback(current, total, message, details, "download")

        return StandardDownloader(
            str(DOWNLOADS_DIR), wrapped_callback if progress_callback else None
        )
    except ImportError as e:
        raise ImportError(f"下载器模块不可用: {e}")


# ============ 文档转换 ============
def convert_to_pdf(file_path: str):
    """将 DOCX/DOC 文件转换为 PDF，返回 PDF 路径"""
    import subprocess
    import shutil
    import tempfile

    soffice_path = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice_path:
        return None

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    soffice_path,
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    tmpdir,
                    file_path,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )

            base_name = os.path.splitext(os.path.basename(file_path))[0]
            pdf_file = os.path.join(tmpdir, f"{base_name}.pdf")

            if os.path.exists(pdf_file):
                final_pdf = os.path.join(DOWNLOADS_DIR, f"{base_name}_converted.pdf")
                shutil.copy(pdf_file, final_pdf)
                return final_pdf
    except Exception as e:
        print(f"[转换] DOCX转PDF失败: {e}")

    return None


# ============ 进度回调 ============
_progress_task_type = "extract"  # 当前任务类型


def progress_callback(current, total, message, details=None, task_type=None):
    """进度回调"""
    global progress_data, _progress_task_type
    if task_type:
        _progress_task_type = task_type
    progress_data = {
        "percentage": int((current / total) * 100) if total > 0 else 0,
        "message": message,
        "details": details or {},
        "task_type": _progress_task_type,
    }
    print(
        f"[进度] {progress_data['percentage']}% - {message} [task_type: {_progress_task_type}]"
    )


# ============ Flask路由 ============


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index_offline.html")


@app.route("/api/status")
def get_status():
    return jsonify(
        {
            "success": True,
            "data": {
                "client_id": CLIENT_ID,
                "hub_url": HUB_URL,
                "current_tasks": current_tasks,
            },
        }
    )


@app.route("/api/progress")
def get_progress():
    return jsonify({"success": True, "data": progress_data})


@app.route("/api/cancel", methods=["POST"])
def cancel_task():
    """取消当前任务"""
    data = request.json
    task_type = data.get("task_type", "query")

    if task_type in current_tasks:
        current_tasks[task_type]["cancel_requested"] = True
        return jsonify({"success": True, "message": f"已请求取消 {task_type} 任务"})

    return jsonify({"success": False, "message": "未知的任务类型"})


@app.route("/api/open-folder", methods=["POST"])
def open_folder():
    """打开文件夹"""
    data = request.json
    file_path = data.get("path", "")

    if not file_path:
        return jsonify({"success": False, "message": "路径为空"})

    try:
        import subprocess
        import platform

        path = Path(file_path)
        folder = path.parent if path.is_file() else path

        if not folder.exists():
            return jsonify({"success": False, "message": "文件夹不存在"})

        system = platform.system()
        if system == "Windows":
            subprocess.run(["explorer", str(folder)])
        elif system == "Darwin":
            subprocess.run(["open", str(folder)])
        else:
            subprocess.run(["xdg-open", str(folder)])

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/extract/text")
def get_extract_text():
    """获取提取的文本内容摘要"""
    global last_extract_text
    text_preview = last_extract_text[:100] if last_extract_text else ""
    return jsonify({"success": True, "text": text_preview})


@app.route("/api/extract", methods=["POST"])
def do_extract():
    """执行提取（带页码信息）"""
    global progress_data

    if current_tasks["extract"]["status"] == "running":
        return jsonify({"success": False, "message": "提取任务正在运行"})

    file = request.files.get("file")
    if not file:
        return jsonify({"success": False, "message": "未选择文件"})

    if not file.filename:
        return jsonify({"success": False, "message": "文件名无效"})

    # 允许的文件格式
    allowed_extensions = {
        ".pdf",
        ".doc",
        ".docx",
        ".txt",
        ".csv",
        ".xlsx",
        ".xls",
        ".jpeg",
        ".jpg",
        ".png",
        ".zip",
    }
    file_ext = os.path.splitext(file.filename)[1].lower()

    if file_ext not in allowed_extensions:
        return jsonify(
            {
                "success": False,
                "message": f"不支持的文件格式: {file_ext}。仅支持: PDF、Word、Excel、TXT、CSV、图片",
            }
        )

    # 处理压缩包
    if file_ext == ".zip":
        import zipfile
        import tempfile

        try:
            with zipfile.ZipFile(file, "r") as zip_ref:
                # 检查压缩包内容
                file_list = zip_ref.namelist()
                # 查找第一个支持的文件
                found_file = None
                for f in file_list:
                    ext = os.path.splitext(f)[1].lower()
                    if ext in {
                        ".pdf",
                        ".doc",
                        ".docx",
                        ".txt",
                        ".csv",
                        ".xlsx",
                        ".xls",
                        ".jpeg",
                        ".jpg",
                        ".png",
                    }:
                        found_file = f
                        break

                if found_file:
                    # 解压到临时目录
                    temp_dir = tempfile.mkdtemp()
                    zip_ref.extract(found_file, temp_dir)
                    # 重命名保存
                    extracted_path = os.path.join(
                        DOWNLOADS_DIR, os.path.basename(found_file)
                    )
                    import shutil

                    shutil.move(os.path.join(temp_dir, found_file), extracted_path)
                    file_path = extracted_path
                    print(f"[DEBUG] 从压缩包提取文件: {found_file}", flush=True)
                else:
                    return jsonify(
                        {"success": False, "message": "压缩包内未找到支持的文件"}
                    )
        except Exception as e:
            return jsonify({"success": False, "message": f"无法解压压缩包: {str(e)}"})
    else:
        file_path = os.path.join(DOWNLOADS_DIR, file.filename)
        file.save(file_path)

    # 文件上传时用 request.values 获取表单数据，默认启用 OCR
    enable_ocr = request.values.get("enable_ocr", "true") == "true"

    # 重置其他任务状态
    current_tasks["query"]["status"] = "idle"
    current_tasks["download"]["status"] = "idle"

    def task_thread():
        try:
            print(f"[DEBUG] 开始提取任务, 文件: {file_path}", flush=True)
            current_tasks["extract"].update(
                {
                    "status": "running",
                    "progress": 0,
                    "message": "开始提取...",
                    "result": None,
                }
            )
            progress_data = {
                "percentage": 0,
                "message": "初始化...",
                "details": {},
                "task_type": "extract",
            }
            _progress_task_type = "extract"

            extractor = get_extractor(progress_callback)
            print(f"[DEBUG] 提取器类型: {type(extractor)}", flush=True)
            print(f"[DEBUG] 提取器 PATTERNS: {extractor.PATTERNS[:2]}", flush=True)

            results_with_pages = []
            extracted_texts = []  # 收集提取的文本内容
            file_lower = file_path.lower()

            if file_lower.endswith(".pdf"):
                try:
                    import fitz

                    doc = fitz.open(str(file_path))
                    total_pages = len(doc)

                    # 检查是否是扫描件（先提取一页看看有没有文字）
                    is_scanned = False
                    if enable_ocr:
                        sample_text = doc[0].get_text()
                        if len(sample_text.strip()) < 50:
                            is_scanned = True
                            print(f"[DEBUG] 检测到扫描件PDF，启用OCR识别", flush=True)

                    for page_num in range(total_pages):
                        # 检查是否请求取消
                        if current_tasks["extract"].get("cancel_requested"):
                            current_tasks["extract"]["cancel_requested"] = False
                            current_tasks["extract"].update(
                                {"status": "cancelled", "message": "用户取消"}
                            )
                            return

                        page = doc[page_num]
                        text = page.get_text()

                        # 如果是扫描件或文字太少，尝试OCR
                        if enable_ocr and (is_scanned or len(text.strip()) < 50):
                            try:
                                # 将页面转换为图片
                                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                                img_data = pix.tobytes("png")

                                # 尝试使用 rapidocr
                                try:
                                    from rapidocr_onnxruntime import RapidOCR

                                    rapidocr = RapidOCR()
                                    result, _ = rapidocr(img_data)
                                    if result:
                                        ocr_text = "\n".join(
                                            [line[1] for line in result]
                                        )
                                        text = ocr_text
                                        print(
                                            f"[DEBUG] 第 {page_num + 1} 页 OCR 识别完成",
                                            flush=True,
                                        )
                                except ImportError:
                                    pass
                            except Exception as e:
                                print(f"[DEBUG] OCR 识别失败: {e}", flush=True)

                        page_progress = int((page_num + 1) / total_pages * 100)
                        progress_callback(
                            page_progress,
                            100,
                            f"正在处理第 {page_num + 1}/{total_pages} 页...",
                            {"page": page_num + 1, "total_pages": total_pages},
                        )

                        temp_results = extractor._extract_from_text(text)
                        for item in temp_results:
                            results_with_pages.append(
                                {
                                    "standard": item["standard"],
                                    "page": page_num + 1,
                                    "confidence": item.get("confidence", "high"),
                                }
                            )

                        # 收集文本内容
                        if text.strip():
                            extracted_texts.append(text.strip()[:500])

                    doc.close()
                except ImportError:
                    results = extractor.extract_from_file(str(file_path), enable_ocr)
                    results_with_pages = [
                        {
                            "standard": r.get("standard", ""),
                            "page": "-",
                            "confidence": r.get("confidence", "high"),
                        }
                        for r in results
                    ]
            elif file_lower.endswith((".docx", ".doc")):
                converted_pdf = None
                try:
                    progress_callback(5, 100, "正在转换文档为PDF...", {})
                    converted_pdf = convert_to_pdf(file_path)
                    if converted_pdf:
                        progress_callback(10, 100, "转换完成，开始提取...", {})
                        import fitz

                        doc = fitz.open(converted_pdf)
                        total_pages = len(doc)

                        # 检查是否是扫描件
                        is_scanned = False
                        if enable_ocr:
                            sample_text = doc[0].get_text()
                            if len(sample_text.strip()) < 50:
                                is_scanned = True
                                print(f"[DEBUG] 检测到扫描件，启用OCR识别", flush=True)

                        for page_num in range(total_pages):
                            # 检查是否请求取消
                            if current_tasks["extract"].get("cancel_requested"):
                                current_tasks["extract"]["cancel_requested"] = False
                                current_tasks["extract"].update(
                                    {"status": "cancelled", "message": "用户取消"}
                                )
                                return

                            page = doc[page_num]
                            text = page.get_text()

                            # 如果是扫描件或文字太少，尝试OCR
                            if enable_ocr and (is_scanned or len(text.strip()) < 50):
                                try:
                                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                                    img_data = pix.tobytes("png")
                                    try:
                                        from rapidocr_onnxruntime import RapidOCR

                                        rapidocr = RapidOCR()
                                        result, _ = rapidocr(img_data)
                                        if result:
                                            ocr_text = "\n".join(
                                                [line[1] for line in result]
                                            )
                                            text = ocr_text
                                    except ImportError:
                                        pass
                                except Exception as e:
                                    print(f"[DEBUG] OCR 识别失败: {e}", flush=True)

                            page_progress = int(10 + (page_num + 1) / total_pages * 90)
                            progress_callback(
                                page_progress,
                                100,
                                f"正在处理第 {page_num + 1}/{total_pages} 页...",
                                {"page": page_num + 1, "total_pages": total_pages},
                            )

                            temp_results = extractor._extract_from_text(text)
                            for item in temp_results:
                                results_with_pages.append(
                                    {
                                        "standard": item["standard"],
                                        "page": page_num + 1,
                                        "confidence": item.get("confidence", "high"),
                                    }
                                )

                        doc.close()
                    else:
                        raise Exception("无法转换文档为PDF")
                except Exception as e:
                    print(f"[DEBUG] DOCX转换失败: {e}，使用直接提取", flush=True)
                    results = extractor.extract_from_file(str(file_path), enable_ocr)
                    results_with_pages = [
                        {
                            "standard": r.get("standard", ""),
                            "page": "-",
                            "confidence": r.get("confidence", "high"),
                        }
                        for r in results
                    ]
                finally:
                    if converted_pdf and os.path.exists(converted_pdf):
                        try:
                            os.unlink(converted_pdf)
                        except OSError as e:
                            logger.warning(f"删除临时文件失败: {e}")
                        except Exception as e:
                            logger.debug(f"删除文件时发生错误: {e}")
            else:
                print(
                    f"[DEBUG] 处理图片文件: {file_path}, enable_ocr: {enable_ocr}",
                    flush=True,
                )
                extracted_texts = []

                # 图片文件直接进行 OCR 识别
                if file_lower.endswith((".png", ".jpg", ".jpeg")) and enable_ocr:
                    try:
                        from PIL import Image
                        import io

                        # 读取图片
                        img = Image.open(file_path)

                        # 尝试 OCR
                        try:
                            from rapidocr_onnxruntime import RapidOCR

                            rapidocr = RapidOCR()

                            # 转换为 bytes
                            img_bytes = io.BytesIO()
                            img.save(img_bytes, format="PNG")
                            img_data = img_bytes.getvalue()

                            result, _ = rapidocr(img_data)
                            if result:
                                ocr_text = "\n".join([line[1] for line in result])
                                print(
                                    f"[DEBUG] OCR 识别结果: {ocr_text[:100]}...",
                                    flush=True,
                                )
                                extracted_texts.append(ocr_text)

                                # 从 OCR 结果提取标准号
                                temp_results = extractor._extract_from_text(ocr_text)
                                results_with_pages = [
                                    {
                                        "standard": r["standard"],
                                        "page": "-",
                                        "confidence": r.get("confidence", "high"),
                                    }
                                    for r in temp_results
                                ]
                                print(
                                    f"[DEBUG] 从OCR结果提取: {len(results_with_pages)} 个标准号",
                                    flush=True,
                                )
                            else:
                                results_with_pages = []
                        except ImportError:
                            print("[DEBUG] rapidocr 未安装", flush=True)
                            results_with_page = []
                    except Exception as e:
                        print(f"[DEBUG] 图片处理失败: {e}", flush=True)
                        results_with_pages = []
                else:
                    results = extractor.extract_from_file(str(file_path), enable_ocr)
                    print(f"[DEBUG] 提取结果: {results}", flush=True)
                    results_with_pages = [
                        {
                            "standard": r.get("standard", ""),
                            "page": "-",
                            "confidence": r.get("confidence", "high"),
                        }
                        for r in results
                    ]

            # 去重 - 按标准号去重，保留所有出现的页码
            seen_standards = {}
            for item in results_with_pages:
                std = item.get("standard", "").strip().upper()
                if not std:
                    continue
                # 标准化标准号
                std_normalized = std.replace(" ", "").replace("/", "").replace("-", "")
                if std_normalized not in seen_standards:
                    seen_standards[std_normalized] = {
                        "standard": item["standard"],
                        "pages": [],
                        "confidence": item.get("confidence", "high"),
                    }
                if (
                    item.get("page")
                    and item["page"] not in seen_standards[std_normalized]["pages"]
                ):
                    seen_standards[std_normalized]["pages"].append(item["page"])

            # 转换为最终格式（带去重后的页码列表）
            final_results = []
            for std_data in seen_standards.values():
                # 按页码排序
                pages = sorted(
                    std_data["pages"], key=lambda x: int(x) if str(x).isdigit() else 0
                )
                final_results.append(
                    {
                        "standard": std_data["standard"],
                        "pages": pages,
                        "confidence": std_data["confidence"],
                    }
                )

            # 按标准号排序
            final_results.sort(key=lambda x: x["standard"])
            results_with_pages = final_results

            # 保存提取的文本内容到全局变量
            global last_extract_text
            if extracted_texts:
                last_extract_text = " ".join(extracted_texts[:10])  # 取前10页文本
            else:
                last_extract_text = ""

            current_tasks["extract"].update(
                {
                    "status": "completed",
                    "progress": 100,
                    "message": f"找到 {len(results_with_pages)} 个去重后的标准号",
                    "result": results_with_pages,
                }
            )
            progress_data = {
                "percentage": 100,
                "message": "提取完成",
                "details": {},
                "task_type": "extract",
            }
            client_stats["extracts"] += 1

        except Exception as e:
            current_tasks["extract"].update(
                {"status": "error", "message": str(e), "result": None}
            )

    threading.Thread(target=task_thread, daemon=True).start()
    return jsonify({"success": True, "message": "任务已启动"})


def is_english_digits_symbols(text: str) -> bool:
    """检查文本是否只包含英文、数字、常用符号，且最多2个中文字符"""
    # 统计中文字符数量（Unicode范围：\u4e00-\u9fff）
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", text)
    if len(chinese_chars) > 2:
        return False

    # 检查是否只包含允许的字符：英文、数字、常用符号、中文字符
    # 常用符号：空格、标点、括号、斜杠、连字符等
    allowed_chars = r'^[a-zA-Z0-9\s.,;:!?\'"()\[\]{}/\\\-_+=*&^%$#@~`|<>，。；：！？（）【】《》·\u4e00-\u9fff]+$'
    return bool(re.match(allowed_chars, text))


def extract_standard_number(text: str):
    """从文本中提取标准号（使用现有的提取模块）"""
    try:
        from extractor import StandardNumberExtractor

        extractor = StandardNumberExtractor()
        results = extractor.extract(text, use_llm=False)

        if results:
            # 返回第一个提取到的标准号（标准化格式）
            return results[0].normalized
        return None
    except Exception as e:
        print(f"[标准号提取] 失败: {e}", flush=True)
        return None


@app.route("/api/query", methods=["POST"])
def do_query():
    """执行查询（自动轮流多平台）"""
    global progress_data

    if current_tasks["query"]["status"] == "running":
        return jsonify({"success": False, "message": "查询任务正在运行"})

    data = request.json
    standards = [s.strip() for s in data.get("standards", []) if s and s.strip()]

    if not standards:
        return jsonify({"success": False, "message": "未输入标准号"})

    # 重置其他任务状态，避免前端混淆显示
    current_tasks["extract"]["status"] = "idle"
    current_tasks["download"]["status"] = "idle"

    def task_thread():
        try:
            print(f"[QUERY TASK] 任务线程启动，输入数量: {len(standards)}", flush=True)
            current_tasks["query"].update(
                {"status": "running", "progress": 0, "message": "正在启动查询..."}
            )
            progress_data = {
                "percentage": 0,
                "message": "开始查询...",
                "details": {},
                "task_type": "query",
            }
            _progress_task_type = "query"

            results = []
            total = len(standards)

            for i, std in enumerate(standards):
                # 检查是否请求取消
                if current_tasks["query"].get("cancel_requested"):
                    current_tasks["query"]["cancel_requested"] = False
                    current_tasks["query"].update(
                        {"status": "cancelled", "message": "用户取消"}
                    )
                    return

                # 提取标准号
                extracted_std = extract_standard_number(std)
                is_direct_query = False

                # 如果提取不到标准号，检查是否可以直接使用输入关键词
                if not extracted_std:
                    if is_english_digits_symbols(std):
                        # 符合条件：只包含英文、数字、常用符号，且最多2个中文字符
                        # 直接使用输入关键词作为查询条件
                        extracted_std = std
                        is_direct_query = True
                    else:
                        # 不符合条件，记录为跳过
                        results.append(
                            {
                                "input_keyword": std,
                                "extracted_standard": "",
                                "status": "skipped",
                                "platform": "",
                                "error": "未提取到标准号且不符合查询条件",
                            }
                        )
                        continue

                # 轮流使用不同平台
                platform = QUERY_PLATFORMS[i % len(QUERY_PLATFORMS)]
                platform_names = {
                    "hunan": "湖南平台",
                    "shanxi": "陕西平台",
                    "jiangxi": "江西平台",
                    "liaocheng": "聊城平台",
                    "liuan": "六安平台",
                    "xiamen": "厦门平台",
                    "shenzhen": "深圳平台",  # 深圳平台响应慢，放最后
                }

                progress = int((i / total) * 100)
                query_type = "直接查询" if is_direct_query else "标准号查询"
                progress_callback(
                    progress,
                    100,
                    f"正在查询: {std} ({query_type}) -> {extracted_std} ({platform_names.get(platform, platform)})",
                    {
                        "current": i + 1,
                        "total": total,
                        "input_keyword": std,
                        "extracted_standard": extracted_std,
                        "platform": platform_names.get(platform, platform),
                        "query_type": query_type,
                    },
                    "query",
                )

                try:
                    query_svc = get_query_service(progress_callback)

                    # 检查是否是异步方法
                    import inspect

                    query_method = (
                        query_svc.query
                        if hasattr(query_svc, "query")
                        else query_svc.query_single
                    )

                    if inspect.iscoroutinefunction(query_method):
                        import asyncio

                        # 添加超时处理，避免查询卡住
                        try:
                            result = asyncio.run(
                                asyncio.wait_for(
                                    query_method(extracted_std, platform), timeout=120
                                )
                            )
                        except asyncio.TimeoutError:
                            raise TimeoutError(
                                f"查询超时（120秒）: {extracted_std} @ {platform}"
                            )
                    else:
                        result = query_method(extracted_std, platform)

                    results.append(
                        {
                            "input_keyword": std,
                            "extracted_standard": extracted_std,
                            "status": "success",
                            "platform": platform,
                            "data": result,
                        }
                    )
                except Exception as e:
                    results.append(
                        {
                            "input_keyword": std,
                            "extracted_standard": extracted_std,
                            "status": "error",
                            "platform": platform,
                            "error": str(e),
                        }
                    )

                time.sleep(0.5)  # 避免请求过快

            success_count = len([r for r in results if r["status"] == "success"])
            skipped_count = len([r for r in results if r["status"] == "skipped"])
            error_count = len([r for r in results if r["status"] == "error"])

            progress_callback(
                100,
                100,
                f"完成 {success_count}/{total} (跳过{skipped_count}, 失败{error_count})",
                {},
                "query",
            )

            client_stats["queries"] += len(standards) - skipped_count
            print(f"[QUERY TASK] 查询完成，结果数量: {len(results)}", flush=True)
            current_tasks["query"].update(
                {
                    "status": "completed",
                    "progress": 100,
                    "message": f"查询完成",
                    "result": results,
                }
            )
            progress_data = {
                "percentage": 100,
                "message": "查询完成",
                "details": {},
                "task_type": "query",
            }
            print(f"[QUERY TASK] 状态已更新为 completed", flush=True)

            # 立即将状态重置为idle，前端会处理completed状态
            # 不需要延迟，前端会在处理完completed状态后自动重置按钮

        except Exception as e:
            current_tasks["query"].update(
                {"status": "error", "message": str(e), "result": None}
            )
            print(f"[QUERY TASK] 状态已更新为 error: {str(e)}", flush=True)

    threading.Thread(target=task_thread, daemon=True).start()
    return jsonify({"success": True, "message": "任务已启动"})


@app.route("/api/download", methods=["POST"])
def do_download():
    """执行下载"""
    global progress_data

    if current_tasks["download"]["status"] == "running":
        return jsonify({"success": False, "message": "下载任务正在运行"})

    data = request.json
    standards = [s.strip() for s in data.get("standards", []) if s and s.strip()]

    if not standards:
        return jsonify({"success": False, "message": "未输入标准号"})

    # 重置其他任务状态
    current_tasks["extract"]["status"] = "idle"
    current_tasks["query"]["status"] = "idle"

    def task_thread():
        import sys

        print("[DEBUG] task_thread started", flush=True)
        try:
            print("[DEBUG] About to update status", flush=True)
            current_tasks["download"].update(
                {
                    "status": "running",
                    "progress": 0,
                    "message": "开始下载...",
                    "result": None,
                }
            )
            progress_data = {
                "percentage": 0,
                "message": "开始下载...",
                "details": {},
                "task_type": "download",
            }
            _progress_task_type = "download"

            results = []
            total = len(standards)

            for i, std in enumerate(standards):
                # 检查是否请求取消
                if current_tasks["download"].get("cancel_requested"):
                    current_tasks["download"]["cancel_requested"] = False
                    current_tasks["download"].update(
                        {"status": "cancelled", "message": "用户取消"}
                    )
                    return

                # 提取标准号
                extracted_std = extract_standard_number(std)
                is_direct_query = False

                # 如果提取不到标准号，检查是否可以直接使用输入关键词
                if not extracted_std:
                    if is_english_digits_symbols(std):
                        extracted_std = std
                        is_direct_query = True
                    else:
                        results.append(
                            {
                                "input_keyword": std,
                                "extracted_standard": "",
                                "status": "skipped",
                                "results": [],
                                "error": "未提取到标准号且不符合查询条件",
                            }
                        )
                        continue

                progress = int((i / total) * 100)
                query_type = "直接查询" if is_direct_query else "标准号查询"
                progress_callback(
                    progress,
                    100,
                    f"正在下载: {std} ({query_type}) -> {extracted_std}",
                    {"current": i + 1, "total": total, "standard": extracted_std},
                    "download",
                )

                try:
                    downloader = get_downloader(progress_callback)

                    # 检查是否是异步方法
                    import inspect

                    if inspect.iscoroutinefunction(downloader.download):
                        import asyncio

                        result = asyncio.run(downloader.download(extracted_std))
                    else:
                        result = downloader.download(extracted_std)

                    results.append(
                        {
                            "input_keyword": std,
                            "extracted_standard": extracted_std,
                            "status": "success" if result.get("success") else "error",
                            "results": result.get("results", []),
                            "message": result.get("message", ""),
                        }
                    )
                except Exception as e:
                    results.append(
                        {
                            "input_keyword": std,
                            "extracted_standard": extracted_std,
                            "status": "error",
                            "results": [],
                            "error": str(e),
                        }
                    )

                time.sleep(1)  # 避免请求过快

            success_count = len([r for r in results if r["status"] == "success"])
            print(
                f"[DEBUG] Download completed: {success_count}/{total}, results: {results}",
                flush=True,
            )
            progress_callback(100, 100, f"完成 {success_count}/{total}", {}, "download")

            client_stats["downloads"] += len(standards)
            current_tasks["download"].update(
                {
                    "status": "completed",
                    "progress": 100,
                    "message": f"下载完成",
                    "result": results,
                }
            )
            print(f"[DEBUG] Status updated to completed", flush=True)

        except Exception as e:
            print(f"[DEBUG] Download error: {e}", flush=True)
            current_tasks["download"].update(
                {"status": "error", "message": str(e), "result": None}
            )

    threading.Thread(target=task_thread, daemon=True).start()
    print("[DEBUG] Download thread started", flush=True)
    return jsonify({"success": True, "message": "任务已启动"})


@app.route("/api/messages", methods=["GET", "POST"])
def handle_messages():
    if request.method == "POST":
        data = request.json
        resp = hub_request(
            "POST",
            "/api/message/send",
            {
                "from": CLIENT_ID,
                "to": data.get("to", "server"),
                "content": data.get("content"),
            },
        )
        return jsonify(resp)
    else:
        # 前端轮询获取消息，明确指定获取消息
        resp = hub_request(
            "POST",
            "/api/client/heartbeat",
            {"client_id": CLIENT_ID, "no_messages": False},
        )
        if resp.get("success"):
            data = resp.get("data", {})

            # 接收新密钥续期
            new_key = data.get("config_key")
            if new_key and _encrypted_config:
                update_config_key(new_key)

            return jsonify(
                {
                    "success": True,
                    "data": data.get("messages", []),
                    "deleted_broadcasts": data.get("deleted_broadcasts", []),
                }
            )
        elif resp.get("banned"):
            # 被封禁了
            print(f"[心跳] 已被封禁: {resp.get('reason')}")
            return jsonify(
                {"success": False, "banned": True, "reason": resp.get("reason")}
            )
        return jsonify({"success": True, "data": [], "deleted_broadcasts": []})


# ============ 初始化 ============
def init():
    """初始化"""
    print("=" * 50)
    print("Jingxi Client - 初始化")
    print("=" * 50)

    print("[注册] 正在注册到服务端...")
    resp = hub_request(
        "POST",
        "/api/client/register",
        {
            "client_id": CLIENT_ID,
            "hostname": "test-client",
            "version": "1.0.0",
            "mac": CLIENT_MAC,
        },
    )
    if resp.get("banned"):
        print(f"[注册] 失败: {resp.get('reason')}")
        sys.exit(1)
    print("[注册] 成功")

    if not download_code():
        print("[启动] 失败: 无法从服务端获取脚本，请检查网络连接")
        sys.exit(1)

    def heartbeat():
        retry_count = 0
        max_retries = 5
        retry_delay = 10  # 秒

        while True:
            time.sleep(30)
            try:
                resp = hub_request(
                    "POST",
                    "/api/client/heartbeat",
                    {
                        "client_id": CLIENT_ID,
                        "stats": client_stats,
                        "no_messages": True,
                    },
                )
                retry_count = 0  # 成功时重置重试计数

                if resp.get("success"):
                    data = resp.get("data", {})
                    new_key = data.get("config_key")
                    if new_key and _encrypted_config:
                        update_config_key(new_key)
                elif resp.get("banned"):
                    print(f"[心跳] 已被封禁: {resp.get('reason')}")
                    # 被封禁后停止心跳
                    break

            except Exception as e:
                retry_count += 1
                if retry_count <= max_retries:
                    print(f"[心跳] 连接失败 (第{retry_count}次重试): {e}")
                    time.sleep(retry_delay * retry_count)  # 指数退避
                else:
                    print(f"[心跳] 连续{max_retries}次失败，停止重试")
                    # 达到最大重试次数后，继续等待下次正常心跳
                    retry_count = max_retries

    threading.Thread(target=heartbeat, daemon=True).start()
    print("[心跳] 已启动")
    print("=" * 50)


if __name__ == "__main__":
    init()

    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    print(f"\n启动本地服务: http://127.0.0.1:{port}")
    print("正在打开浏览器...\n")

    def open_browser():
        time.sleep(2)
        webbrowser.open(f"http://127.0.0.1:{port}")

    threading.Thread(target=open_browser, daemon=True).start()

    try:
        from waitress import serve

        serve(app, host="127.0.0.1", port=port)
    except ImportError:
        app.run(host="127.0.0.1", port=port, debug=False)
