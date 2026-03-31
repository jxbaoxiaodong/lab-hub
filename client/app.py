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
os.environ["WDM_SSL_VERIFY"] = "1"

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
import hmac
import secrets
import threading
import webbrowser
import re
import platform
import socket
import uuid
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import Flask, request, jsonify, send_from_directory, make_response

# 目录设置
BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
DOWNLOADS_DIR = BASE_DIR / "downloads"
STATIC_DIR = BASE_DIR / "static"
QUESTION_BANKS_DIR = BASE_DIR / "question_banks"

for d in [CACHE_DIR, DOWNLOADS_DIR, STATIC_DIR, QUESTION_BANKS_DIR]:
    d.mkdir(exist_ok=True)

TECH_TASKS_FILE = CACHE_DIR / "tech_file_tasks.json"
TECH_TASKS_ROOT = DOWNLOADS_DIR / "tech_file_tasks"
TECH_TASKS_ROOT.mkdir(exist_ok=True)

# 配置
# 客户端必须使用公网 Hub（用于分发），禁止切换到本地地址。
HUB_URL = "https://testmumu.ftir.fun"
CLIENT_APP_VERSION = "2.2.0"
CLIENT_USER_AGENT = f"Jingxi Client/{CLIENT_APP_VERSION}"
CLIENT_ID_FILE = CACHE_DIR / "client_id.txt"
CLIENT_AUTH_TOKEN_FILE = CACHE_DIR / "auth_token.txt"
CLIENT_AUTH_HEADER_PREFIX = "X-Jingxi-"
CLIENT_AUTH_VERSION = "v1"
ALLOW_INSECURE_TLS = os.environ.get("LAB_ALLOW_INSECURE_TLS", "0") == "1"
LOCAL_API_HEADER = "X-Lab-Local-Api-Token"
LOCAL_API_BOOTSTRAP_TOKEN = secrets.token_urlsafe(32)
CONFIG_ENCRYPTION_ALGORITHM = "aes-256-gcm"
QUESTION_BANK_SYNC_ALGORITHM = "aes-256-gcm"


def _bootstrap_secret_candidates() -> list[Path]:
    candidates = [BASE_DIR / "bootstrap_secret.txt"]
    if getattr(sys, "_MEIPASS", None):
        candidates.append(Path(sys._MEIPASS) / "bootstrap_secret.txt")
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "bootstrap_secret.txt")

    unique_paths = []
    seen = set()
    for path in candidates:
        normalized = str(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_paths.append(path)
    return unique_paths


def _load_client_bootstrap_secret() -> str:
    env_secret = (os.environ.get("LAB_CLIENT_AUTH_SECRET") or "").strip()
    if env_secret:
        return env_secret

    for candidate in _bootstrap_secret_candidates():
        if not candidate.exists():
            continue
        try:
            file_secret = candidate.read_text(encoding="utf-8").strip()
            if file_secret:
                return file_secret
        except Exception:
            continue
    return ""


CLIENT_BOOTSTRAP_SECRET = _load_client_bootstrap_secret()

# 获取客户端ID和MAC地址
CLIENT_MAC = None
if os.path.exists(CLIENT_ID_FILE):
    with open(CLIENT_ID_FILE, "r") as f:
        CLIENT_ID = f.read().strip()
else:
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

CLIENT_AUTH_TOKEN = ""
if CLIENT_AUTH_TOKEN_FILE.exists():
    try:
        CLIENT_AUTH_TOKEN = CLIENT_AUTH_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except Exception as e:
        print(f"[警告] 读取客户端令牌失败: {e}")
        CLIENT_AUTH_TOKEN = ""

# Flask应用
app = Flask(__name__, static_folder=str(STATIC_DIR))

# ============ 配置解密管理 ============
import base64

# 配置相关全局变量
_encrypted_config = None  # 加密的配置（本地保存的是乱码）
_config_key = None  # 当前解密密钥（内存中）
_decrypted_config = None  # 解密后的配置（内存中）
_config_expires = None  # 配置过期时间


def _cache_encrypted_config(encrypted_config: str):
    """缓存最新密文到内存和本地。"""
    global _encrypted_config

    if not encrypted_config:
        return

    _encrypted_config = encrypted_config
    config_file = CACHE_DIR / "config.enc"
    config_file.write_text(encrypted_config, encoding="utf-8")


def _derive_transport_key(key: str) -> bytes:
    return hashlib.sha256((key or "").encode("utf-8")).digest()


def _decrypt_aead_payload(
    ciphertext: bytes, key: str, nonce_b64: str, algorithm: str = CONFIG_ENCRYPTION_ALGORITHM
) -> bytes:
    if algorithm != CONFIG_ENCRYPTION_ALGORITHM:
        raise ValueError(f"不支持的加密算法: {algorithm}")
    nonce = base64.b64decode(nonce_b64)
    return AESGCM(_derive_transport_key(key)).decrypt(nonce, ciphertext, None)


def _decrypt_legacy_xor_bytes(encrypted: bytes, key: str) -> bytes:
    key_bytes = key.encode("utf-8")
    decrypted = bytearray()
    for i, byte in enumerate(encrypted):
        decrypted.append(byte ^ key_bytes[i % len(key_bytes)])
    return bytes(decrypted)


def decrypt_config(encrypted_payload: str, key: str) -> dict:
    """
    解密配置

    Args:
        encrypted_payload: 加密配置载荷
        key: 解密密钥

    Returns:
        解密后的配置字典
    """
    try:
        parsed = json.loads(encrypted_payload)
        if isinstance(parsed, dict) and parsed.get("alg") == CONFIG_ENCRYPTION_ALGORITHM:
            ciphertext = base64.b64decode(parsed.get("ciphertext") or "")
            decrypted = _decrypt_aead_payload(
                ciphertext,
                key,
                parsed.get("nonce") or "",
                parsed.get("alg") or CONFIG_ENCRYPTION_ALGORITHM,
            )
            return json.loads(decrypted.decode("utf-8"))
    except Exception:
        pass

    try:
        encrypted = base64.b64decode(encrypted_payload)
        config_json = _decrypt_legacy_xor_bytes(encrypted, key).decode("utf-8")
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


def update_config_key(
    new_key: str,
    encrypted_config: str = None,
    expires_seconds: int = 300,
    allow_refetch: bool = True,
):
    """更新密钥并解密配置。"""
    global _config_key, _decrypted_config, _config_expires, _encrypted_config

    if encrypted_config:
        _cache_encrypted_config(encrypted_config)

    if not _encrypted_config or not new_key:
        return False

    expires_seconds = max(int(expires_seconds or 300), 30)

    # 用最新密钥解密
    decrypted = decrypt_config(_encrypted_config, new_key)
    if decrypted:
        _config_key = new_key
        _decrypted_config = decrypted
        _config_expires = time.time() + expires_seconds
        print(
            f"[配置] 密钥已更新，配置有效期至 {datetime.fromtimestamp(_config_expires).strftime('%H:%M:%S')}"
        )
        return True

    if allow_refetch:
        print("[配置] 当前密钥与本地密文不匹配，正在重新获取完整配置...")
        return download_code()

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

# Hub / 配置连接状态（用于前端展示与接口提示）
hub_state = {
    "state": "connecting",  # connecting | ready | banned | error | device_id_conflict
    "message": "未连接服务端，正在重试…",
    "last_error": "",
    "hub_url": "",
}


def _save_client_auth_token(token: str):
    global CLIENT_AUTH_TOKEN

    token = (token or "").strip()
    if not token:
        return

    CLIENT_AUTH_TOKEN = token
    CLIENT_AUTH_TOKEN_FILE.write_text(token, encoding="utf-8")


def _create_ssl_context():
    import ssl

    ctx = ssl.create_default_context()
    if ALLOW_INSECURE_TLS:
        print("[安全警告] 已启用 LAB_ALLOW_INSECURE_TLS，TLS 证书校验被关闭")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _is_loopback_host(hostname: str) -> bool:
    host = (hostname or "").strip().lower().strip("[]")
    return host in {"127.0.0.1", "localhost", "::1"}


def _is_allowed_local_origin(url_value: str) -> bool:
    if not url_value:
        return True
    try:
        from urllib.parse import urlsplit

        parsed = urlsplit(url_value)
        return parsed.scheme in {"http", "https"} and _is_loopback_host(
            parsed.hostname or parsed.netloc
        )
    except Exception:
        return False


def _is_path_within_root(path_value: Path, root_value: Path) -> bool:
    try:
        path_value.resolve(strict=False).relative_to(root_value.resolve())
        return True
    except Exception:
        return False


def _safe_upload_filename(raw_name: str) -> str:
    raw_basename = Path(str(raw_name or "")).name
    suffix = Path(raw_basename).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(raw_basename).stem).strip("._")
    if not stem:
        stem = f"upload_{int(time.time())}"
    return f"{stem}{suffix}"


@app.before_request
def guard_local_api():
    if not request.path.startswith("/api/"):
        return None

    origin = request.headers.get("Origin", "")
    referer = request.headers.get("Referer", "")
    if (origin and not _is_allowed_local_origin(origin)) or (
        referer and not _is_allowed_local_origin(referer)
    ):
        return jsonify({"success": False, "message": "禁止跨站访问本地接口"}), 403

    if request.path == "/api/bootstrap":
        return None

    provided_token = (request.headers.get(LOCAL_API_HEADER) or "").strip()
    if not hmac.compare_digest(provided_token, LOCAL_API_BOOTSTRAP_TOKEN):
        return jsonify({"success": False, "message": "本地接口鉴权失败"}), 403

    return None

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

# 其他技术文件任务（持久化）
tech_task_lock = threading.Lock()
tech_task_worker_started = False


def _load_tech_tasks() -> list:
    if not TECH_TASKS_FILE.exists():
        return []
    try:
        data = json.loads(TECH_TASKS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _save_tech_tasks(tasks: list):
    TECH_TASKS_FILE.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _task_drop_dir(task_id: str) -> Path:
    task_dir = TECH_TASKS_ROOT / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


def _public_tech_task(task: dict) -> dict:
    """返回给前端的任务字段，避免泄露本地目录等内部信息。"""
    return {
        "id": task.get("id", ""),
        "created_at": task.get("created_at", ""),
        "updated_at": task.get("updated_at", ""),
        "keyword": task.get("keyword", ""),
        "status": task.get("status", "processing"),
        "progress": task.get("progress", 0),
        "progress_text": task.get("progress_text", ""),
        "download_url": task.get("download_url", ""),
        "file_name": task.get("file_name", ""),
    }


def _scan_and_update_tech_tasks(tasks: list) -> bool:
    changed = False
    for task in tasks:
        if task.get("status") != "processing":
            continue
        task_id = task.get("id", "")
        if not task_id:
            continue
        task_dir = _task_drop_dir(task_id)
        files = [p for p in task_dir.iterdir() if p.is_file()]
        if not files:
            continue
        file_path = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[0]
        task["status"] = "completed"
        task["progress"] = 100
        task["progress_text"] = "完成"
        task["updated_at"] = datetime.now().isoformat()
        task["file_name"] = file_path.name
        task["download_url"] = f"/api/tech-files/tasks/{task_id}/download/{file_path.name}"
        changed = True
    return changed


def _start_tech_task_worker():
    global tech_task_worker_started
    if tech_task_worker_started:
        return
    tech_task_worker_started = True

    def worker():
        while True:
            try:
                with tech_task_lock:
                    tasks = _load_tech_tasks()
                    if _scan_and_update_tech_tasks(tasks):
                        _save_tech_tasks(tasks)
            except Exception as e:
                print(f"[技术文件任务] 扫描失败: {e}", flush=True)
            time.sleep(5)

    threading.Thread(target=worker, daemon=True).start()


# ============ 与服务端通信 ============
def collect_client_fingerprint() -> dict:
    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = "unknown"
    return {
        "hostname": hostname,
        "platform": platform.system(),
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
        "mac": CLIENT_MAC,
    }


CLIENT_FINGERPRINT = collect_client_fingerprint()


def _build_client_auth_headers(
    method: str, path: str, body_bytes: bytes = b"", allow_bootstrap: bool = False
) -> dict:
    import urllib.parse

    headers = {
        f"{CLIENT_AUTH_HEADER_PREFIX}Client-Id": CLIENT_ID,
        f"{CLIENT_AUTH_HEADER_PREFIX}Version": CLIENT_AUTH_VERSION,
    }

    if CLIENT_AUTH_TOKEN:
        headers[f"{CLIENT_AUTH_HEADER_PREFIX}Auth-Token"] = CLIENT_AUTH_TOKEN

    if not allow_bootstrap or CLIENT_AUTH_TOKEN:
        return headers

    if not CLIENT_BOOTSTRAP_SECRET:
        raise RuntimeError(
            "缺少客户端注册密钥，请在构建时注入 bootstrap_secret.txt 或设置 LAB_CLIENT_AUTH_SECRET"
        )

    now_ts = str(int(time.time()))
    nonce = secrets.token_hex(8)
    body_hash = hashlib.sha256(body_bytes or b"").hexdigest()
    parsed = urllib.parse.urlsplit(path)
    canonical = "\n".join(
        [
            method.upper(),
            parsed.path or "/",
            parsed.query or "",
            CLIENT_ID,
            now_ts,
            nonce,
            body_hash,
        ]
    )
    signature = hmac.new(
        CLIENT_BOOTSTRAP_SECRET.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers.update(
        {
            f"{CLIENT_AUTH_HEADER_PREFIX}Timestamp": now_ts,
            f"{CLIENT_AUTH_HEADER_PREFIX}Nonce": nonce,
            f"{CLIENT_AUTH_HEADER_PREFIX}Signature": signature,
        }
    )
    return headers


def hub_request(method, path, data=None):
    """向服务端发送请求"""
    import urllib.request

    url = f"{HUB_URL}{path}"
    ctx = _create_ssl_context()
    allow_bootstrap = path.startswith("/api/client/register")

    try:
        body_bytes = b""
        body_text = ""
        if method == "POST":
            body_text = json.dumps(data or {}, ensure_ascii=False, separators=(",", ":"))
            body_bytes = body_text.encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": CLIENT_USER_AGENT,
        }
        headers.update(
            _build_client_auth_headers(
                method,
                path,
                body_bytes,
                allow_bootstrap=allow_bootstrap,
            )
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ctx),
        )
        if method == "POST":
            req = urllib.request.Request(
                url,
                data=body_bytes,
                headers=headers,
                method="POST",
            )
        else:
            req = urllib.request.Request(url, headers=headers)

        with opener.open(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        # 某些受限环境下 Python socket 会被禁用（PermissionError: Operation not permitted）。
        # 这里兜底用 curl 调用，保持客户端可用。
        try:
            import subprocess

            cmd = [
                "curl",
                "-s",
                "-L",
                "--max-time",
                "10",
                "--fail",
                "--show-error",
                "-H",
                "Content-Type: application/json",
                "-H",
                f"User-Agent: {CLIENT_USER_AGENT}",
            ]
            auth_headers = _build_client_auth_headers(
                method,
                path,
                body_bytes if method == "POST" else b"",
                allow_bootstrap=allow_bootstrap,
            )
            for key, value in auth_headers.items():
                cmd += ["-H", f"{key}: {value}"]
            if method == "POST":
                cmd += ["-X", "POST", "-d", body_text]
            cmd.append(url)

            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0 and r.stdout:
                return json.loads(r.stdout)
        except Exception:
            pass
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
    expires_seconds = data.get("key_expires_seconds", 300)

    if not encrypted_config or not key:
        print("[配置更新] 失败: 配置数据不完整")
        return False

    # 保存加密配置（本地是乱码）
    _cache_encrypted_config(encrypted_config)

    # 解密配置
    decrypted = decrypt_config(encrypted_config, key)
    if not decrypted:
        print("[配置更新] 失败: 配置解密失败")
        return False

    # 保存到内存
    _config_key = key
    _decrypted_config = decrypted
    _config_expires = time.time() + max(int(expires_seconds or 300), 30)

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

        # 获取查询平台配置
        config = get_config()
        query_platforms = config.get("query_platforms") if config else None

        return StandardQueryService(
            wrapped_callback if progress_callback else None,
            query_platforms,
            cancel_callback=lambda: current_tasks["query"].get("cancel_requested"),
        )
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
            str(DOWNLOADS_DIR),
            wrapped_callback if progress_callback else None,
            cancel_callback=lambda: current_tasks["download"].get("cancel_requested"),
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


# ============ 查询运行时调试日志（写入 client/cache） ============
_query_debug_lock = threading.Lock()


def _query_debug_path() -> Path:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return CACHE_DIR / "query_runtime_debug.jsonl"


def _append_query_debug(event: str, payload: dict | None = None) -> None:
    row = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event": event,
        "payload": payload or {},
    }
    path = _query_debug_path()
    try:
        with _query_debug_lock:
            try:
                if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
                    rotated = path.with_suffix(".jsonl.1")
                    try:
                        if rotated.exists():
                            rotated.unlink()
                    except Exception:
                        pass
                    try:
                        path.rename(rotated)
                    except Exception:
                        pass
            except Exception:
                pass
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        return


# ============ Flask路由 ============


@app.route("/")
def index():
    resp = make_response(send_from_directory(STATIC_DIR, "index_offline.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/api/bootstrap")
def bootstrap_local_api():
    return jsonify(
        {
            "success": True,
            "data": {
                "local_api_token": LOCAL_API_BOOTSTRAP_TOKEN,
                "version": CLIENT_APP_VERSION,
            },
        }
    )


@app.after_request
def add_no_cache_headers(response):
    # 前端资源频繁迭代，避免浏览器缓存导致“已修复但仍卡住”的假象
    if (
        request.path.startswith("/static/")
        or request.path == "/"
        or request.path == "/api/bootstrap"
    ):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.route("/api/status")
def get_status():
    ready, ready_msg = _require_ready()
    config_remaining_seconds = 0
    if _config_expires:
        config_remaining_seconds = max(0, int(_config_expires - time.time()))
    return jsonify(
        {
            "success": True,
            "data": {
                "client_id": CLIENT_ID,
                "version": CLIENT_APP_VERSION,
                "hub_url": HUB_URL,
                "hub_state": hub_state,
                "service_ready": ready,
                "service_message": ready_msg if not ready else "就绪",
                "config_valid": is_config_valid(),
                "config_expires_in_seconds": config_remaining_seconds,
                "current_tasks": current_tasks,
            },
        }
    )


@app.route("/api/progress")
def get_progress():
    return jsonify({"success": True, "data": progress_data})


@app.route("/api/debug/query-log", methods=["GET", "POST"])
def debug_query_log():
    path = _query_debug_path()
    if request.method == "POST":
        try:
            with _query_debug_lock:
                if path.exists():
                    path.unlink()
            return jsonify({"success": True, "message": "已清空"})
        except Exception as e:
            return jsonify({"success": False, "message": str(e)})

    tail = request.args.get("tail", "300")
    try:
        tail_n = max(10, min(2000, int(tail)))
    except Exception:
        tail_n = 300

    if not path.exists():
        return jsonify({"success": True, "data": {"path": str(path), "lines": []}})

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > tail_n:
            lines = lines[-tail_n:]
        return jsonify({"success": True, "data": {"path": str(path), "lines": lines}})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/download/help", methods=["POST"])
def request_download_help():
    """下载无结果时向后台发送求助请求（走消息中心）"""
    try:
        data = request.json or {}
        input_keyword = (data.get("input_keyword") or "").strip()
        extracted_standard = (data.get("extracted_standard") or "").strip()
        platform_results = data.get("results") or []

        std = extracted_standard or input_keyword
        if not std:
            return jsonify({"success": False, "message": "标准号为空"})

        payload = {
            "type": "download_help",
            "standard": std,
            "input_keyword": input_keyword,
            "extracted_standard": extracted_standard,
            "platform_results": platform_results,
            "client_id": CLIENT_ID,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        content = "[下载求助] " + json.dumps(payload, ensure_ascii=False)
        resp = hub_request(
            "POST",
            "/api/message/send",
            {"from": CLIENT_ID, "to": "server", "content": content},
        )

        if not resp.get("success"):
            return jsonify(
                {"success": False, "message": resp.get("error") or "发送失败"}
            )
        return jsonify({"success": True, "message": "已发送求助请求"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/tech-files/tasks", methods=["GET", "POST"])
def tech_file_tasks():
    """其他技术文件任务：客户端侧代理到Hub统一任务队列。"""
    if request.method == "GET":
        resp = hub_request("GET", f"/api/tech-tasks?client_id={CLIENT_ID}")
        if not resp.get("success"):
            err = resp.get("error") or ""
            if "404" in err:
                return jsonify(
                    {
                        "success": False,
                        "message": "服务端尚未升级技术文件任务接口（404）。请重启/更新 backend/server_hub.py 后重试。",
                    }
                )
            return jsonify({"success": False, "message": err or "加载任务失败"})
        tasks = resp.get("data") or []
        # 转成本地可访问的下载代理路径
        normalized = []
        for t in tasks:
            task_id = t.get("id", "")
            download_url = t.get("download_url", "") or ""
            filename = t.get("file_name", "") or ""
            if download_url and task_id and filename:
                download_url = f"/api/tech-files/tasks/{task_id}/download/{filename}"
            normalized.append(
                {
                    "id": task_id,
                    "created_at": t.get("created_at", ""),
                    "updated_at": t.get("updated_at", ""),
                    "keyword": t.get("keyword", ""),
                    "status": t.get("status", "processing"),
                    "progress_text": t.get("progress_text", ""),
                    "download_url": download_url,
                    "file_name": filename,
                }
            )
        return jsonify({"success": True, "data": normalized})

    data = request.json or {}
    keywords = data.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [str(s).strip() for s in keywords if str(s).strip()]
    if not keywords:
        return jsonify({"success": False, "message": "请输入关键词"})

    created = []
    suggestions = []
    non_standard = []
    for keyword in keywords:
        extracted_standard = extract_standard_number(keyword)
        if extracted_standard:
            suggestions.append(
                {
                    "keyword": keyword,
                    "extracted_standard": extracted_standard,
                    "message": "检测到标准号，建议切换到“找标准”检索。",
                }
            )
        else:
            non_standard.append(keyword)

    if non_standard:
        resp = hub_request(
            "POST",
            "/api/tech-tasks/create",
            {"client_id": CLIENT_ID, "keywords": non_standard},
        )
        if not resp.get("success"):
            err = resp.get("error") or ""
            if "404" in err:
                return jsonify(
                    {
                        "success": False,
                        "message": "服务端尚未升级技术文件任务接口（404）。请重启/更新 backend/server_hub.py 后重试。",
                    }
                )
            return jsonify({"success": False, "message": err or "创建任务失败"})
        created = (resp.get("data") or {}).get("created", [])

    return jsonify(
        {
            "success": True,
            "data": {
                "created": created,
                "suggestions": suggestions,
            },
        }
    )


@app.route("/api/tech-files/tasks/<task_id>/download/<path:filename>", methods=["GET"])
def download_tech_task_file(task_id, filename):
    """下载其他技术文件任务产物（客户端代理Hub下载）。"""
    try:
        import urllib.request
        import urllib.parse

        safe_task_id = re.sub(r"[^a-f0-9]", "", (task_id or "").lower())
        if not safe_task_id:
            return jsonify({"success": False, "message": "任务ID无效"}), 400

        safe_name = Path(filename).name
        query = urllib.parse.urlencode({"filename": safe_name})
        url = f"{HUB_URL}/api/tech-tasks/{safe_task_id}/download?{query}"
        ctx = _create_ssl_context()

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": CLIENT_USER_AGENT,
                **_build_client_auth_headers(
                    "GET", f"/api/tech-tasks/{safe_task_id}/download?{query}", b""
                ),
            },
            method="GET",
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ctx),
        )
        with opener.open(req, timeout=30) as resp:
            payload = resp.read()
            content_type = resp.headers.get("Content-Type", "application/octet-stream")
            disposition = resp.headers.get("Content-Disposition", f'attachment; filename="{safe_name}"')
            out = make_response(payload)
            out.headers["Content-Type"] = content_type
            out.headers["Content-Disposition"] = disposition
            return out
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


_legacy_qb_source = (os.environ.get("QUESTION_BANKS_SOURCE_DIR") or "").strip()
LEGACY_QUESTION_BANKS_SOURCE = Path(_legacy_qb_source) if _legacy_qb_source else None
QUESTION_BANKS_ROOT = Path(
    os.environ.get("QUESTION_BANKS_ROOT", str(QUESTION_BANKS_DIR.resolve()))
)
QUESTION_BANKS_ROOT.mkdir(parents=True, exist_ok=True)
QUESTION_BANKS_MANIFEST_FILE = CACHE_DIR / "question_banks_manifest.json"


def _copy_legacy_question_banks_if_needed():
    try:
        if LEGACY_QUESTION_BANKS_SOURCE is None:
            return
        if not LEGACY_QUESTION_BANKS_SOURCE.exists():
            return
        for src in LEGACY_QUESTION_BANKS_SOURCE.glob("*.json"):
            if src.name == "catalog.json":
                continue
            dst = QUESTION_BANKS_ROOT / src.name
            should_copy = (not dst.exists()) or (src.stat().st_mtime > dst.stat().st_mtime)
            if should_copy:
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"[题库] 已同步本地题库文件: {src.name}")
    except Exception as e:
        print(f"[题库] 同步外部题库失败: {e}")


def _current_question_bank_hashes() -> dict:
    rows = {}
    for p in sorted(QUESTION_BANKS_ROOT.glob("*.json")):
        if not p.is_file() or p.name == "catalog.json":
            continue
        try:
            payload = p.read_bytes()
            rows[p.name] = hashlib.sha256(payload).hexdigest()
        except Exception:
            continue
    return rows


def _load_question_bank_manifest() -> dict:
    if QUESTION_BANKS_MANIFEST_FILE.exists():
        try:
            return json.loads(QUESTION_BANKS_MANIFEST_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"version": "", "files": {}}


def _save_question_bank_manifest(manifest: dict):
    QUESTION_BANKS_MANIFEST_FILE.write_text(
        json.dumps(manifest or {}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _refresh_question_bank_manifest():
    current_files = _current_question_bank_hashes()
    version = hashlib.sha256(
        json.dumps(current_files, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    _save_question_bank_manifest(
        {
            "version": version,
            "files": current_files,
            "updated_at": datetime.now().isoformat(),
        }
    )


def apply_question_bank_sync(sync_payload: dict, decrypt_key: str = ""):
    if not decrypt_key:
        return
    if not isinstance(sync_payload, dict):
        return
    files = sync_payload.get("files") or []
    changed = False
    # 环境变量：保留本地题库，不允许服务端删除本地文件
    preserve_local = os.environ.get("PRESERVE_LOCAL_QUESTION_BANKS", "0").strip() == "1"
    # 保留本地手动添加的题库：只删除服务端明确标记删除的文件
    # 不在同步列表中的本地文件会被保留
    for row in files:
        name = _safe_bank_id(row.get("name", ""))
        if not name:
            continue
        target = QUESTION_BANKS_ROOT / name
        if row.get("deleted"):
            if preserve_local:
                print(f"[题库] 跳过删除 {name}（PRESERVE_LOCAL_QUESTION_BANKS 已启用）")
                continue
            if target.exists():
                target.unlink(missing_ok=True)
                changed = True
            continue
        content_b64 = row.get("content_b64") or ""
        try:
            content_bytes = base64.b64decode(content_b64)
            if row.get("encrypted"):
                algorithm = row.get("algorithm") or ""
                if algorithm == QUESTION_BANK_SYNC_ALGORITHM:
                    content_bytes = _decrypt_aead_payload(
                        content_bytes,
                        decrypt_key,
                        row.get("nonce_b64") or "",
                        algorithm,
                    )
                else:
                    content_bytes = _decrypt_legacy_xor_bytes(
                        content_bytes, decrypt_key
                    )
            content = content_bytes.decode("utf-8")
        except Exception:
            continue
        target.write_text(content, encoding="utf-8")
        changed = True

    if changed:
        # 刷新清单时会包含本地已存在的文件，因为 _current_question_bank_hashes() 会扫描目录
        manifest = {
            "version": sync_payload.get("version", ""),
            "files": _current_question_bank_hashes(),
            "updated_at": datetime.now().isoformat(),
        }
        _save_question_bank_manifest(manifest)
        if preserve_local:
            print(f"[题库] 已通过心跳同步更新，共 {len(files)} 项，本地题库保护已启用")
        else:
            print(f"[题库] 已通过心跳同步更新，共 {len(files)} 项")


def _safe_bank_id(bank_id: str) -> str:
    bank_id = (bank_id or "").strip()
    if not bank_id or "/" in bank_id or "\\" in bank_id or ".." in bank_id:
        return ""
    return bank_id


_copy_legacy_question_banks_if_needed()
_refresh_question_bank_manifest()


def _question_bank_feature_available():
    if not is_config_valid():
        return False, "服务暂不可用，请联系管理员"
    return True, ""


def _display_text(value, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        text = value.strip()
        return text if text else fallback
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        text = json.dumps(value, ensure_ascii=False)
        return text if text else fallback
    except Exception:
        return fallback


def _extract_bank_meta(path: Path):
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    title = path.stem
    description = ""
    questions = []

    if isinstance(raw, dict):
        source = raw.get("source")
        if isinstance(source, dict):
            source_title = source.get("title") or source.get("name")
        else:
            source_title = source
        title = _display_text(
            raw.get("name") or raw.get("title") or source_title, title
        )
        description = _display_text(raw.get("description"), "")
        if isinstance(raw.get("questions"), list):
            questions = raw.get("questions") or []
    elif isinstance(raw, list):
        questions = raw

    return {
        "id": path.name,
        "title": title,
        "description": description,
        "count": len(questions) if isinstance(questions, list) else 0,
    }


def _normalize_question(item: dict, idx: int):
    if not isinstance(item, dict):
        return None

    question = (
        item.get("question")
        or item.get("title")
        or item.get("stem")
        or item.get("text")
        or ""
    ).strip()
    if not question:
        return None

    options = item.get("options")
    normalized_options = []
    def _strip_option_prefix(text: str, key: str) -> str:
        value = str(text or "").strip()
        key_text = str(key or "").strip()
        if not value or not key_text:
            return value
        prefix_patterns = [
            rf"^\s*{re.escape(key_text)}\s*[\.．、:：\)\）-]\s*",
            rf"^\s*[（(]\s*{re.escape(key_text)}\s*[)）]\s*",
        ]
        for pattern in prefix_patterns:
            value = re.sub(pattern, "", value, flags=re.IGNORECASE).strip()
        return value

    def _extract_key_and_text(raw_value: str, fallback_key: str):
        raw_text = str(raw_value or "").strip()
        if not raw_text:
            return str(fallback_key), ""
        matched = re.match(r"^\s*([A-Za-z]|\d{1,2})\s*[\.．、:：\)\）-]\s*(.+)$", raw_text)
        if matched:
            detected_key = matched.group(1).upper()
            detected_text = matched.group(2).strip()
            return detected_key, detected_text
        return str(fallback_key), _strip_option_prefix(raw_text, fallback_key)

    if isinstance(options, dict):
        for k, v in options.items():
            key_text = str(k).strip()
            option_text = _strip_option_prefix(str(v), key_text)
            normalized_options.append({"key": key_text, "text": option_text})
    elif isinstance(options, list):
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for i, v in enumerate(options):
            fallback_key = letters[i] if i < len(letters) else str(i + 1)
            key, option_text = _extract_key_and_text(v, fallback_key)
            normalized_options.append({"key": str(key), "text": option_text})

    answer = item.get("answer")
    if isinstance(answer, list):
        normalized_answer = [str(a).strip() for a in answer if str(a).strip()]
    elif answer is None:
        normalized_answer = []
    else:
        normalized_answer = [str(answer).strip()] if str(answer).strip() else []

    explanation = (item.get("explanation") or item.get("analysis") or "").strip()
    qtype = (item.get("type") or "").strip()

    return {
        "id": item.get("id") or f"q{idx+1}",
        "type": qtype,
        "question": question,
        "options": normalized_options,
        "answer": normalized_answer,
        "explanation": explanation,
    }


@app.route("/api/question_banks", methods=["GET"])
def list_question_banks():
    ok, message = _question_bank_feature_available()
    if not ok:
        return jsonify({"success": False, "message": message}), 403
    if not QUESTION_BANKS_ROOT.exists():
        return jsonify({"success": False, "message": "题库目录不存在"})

    banks = []
    for p in sorted(QUESTION_BANKS_ROOT.glob("*.json")):
        if p.name == "catalog.json":
            continue
        meta = _extract_bank_meta(p)
        if meta:
            banks.append(meta)

    return jsonify({"success": True, "data": banks})


@app.route("/api/question_banks/<bank_id>", methods=["GET"])
def get_question_bank(bank_id: str):
    ok, message = _question_bank_feature_available()
    if not ok:
        return jsonify({"success": False, "message": message}), 403
    safe_id = _safe_bank_id(bank_id)
    if not safe_id:
        return jsonify({"success": False, "message": "题库ID无效"})

    path = QUESTION_BANKS_ROOT / safe_id
    if not path.exists():
        return jsonify({"success": False, "message": "题库不存在"})

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({"success": False, "message": f"读取题库失败: {e}"})

    if isinstance(raw, dict) and isinstance(raw.get("questions"), list):
        questions_raw = raw.get("questions") or []
        source = raw.get("source")
        if isinstance(source, dict):
            source_title = source.get("title") or source.get("name")
        else:
            source_title = source
        title = _display_text(raw.get("name") or raw.get("title") or source_title, path.stem)
        description = _display_text(raw.get("description"), "")
    elif isinstance(raw, list):
        questions_raw = raw
        title = path.stem
        description = ""
    else:
        return jsonify({"success": False, "message": "题库格式不支持"})

    questions = []
    for idx, item in enumerate(questions_raw):
        q = _normalize_question(item, idx)
        if q:
            questions.append(q)

    return jsonify(
        {
            "success": True,
            "data": {
                "id": path.name,
                "title": title,
                "description": description,
                "count": len(questions),
                "questions": questions,
            },
        }
    )


@app.route("/api/cancel", methods=["POST"])
def cancel_task():
    """取消当前任务"""
    data = request.json
    task_type = data.get("task_type", "query")

    if task_type in current_tasks:
        current_tasks[task_type]["cancel_requested"] = True
        current_tasks[task_type]["status"] = "cancelled"
        current_tasks[task_type]["message"] = "用户取消"
        if task_type == "query":
            current_tasks["query"]["run_id"] = None
        if task_type == "query":
            global progress_data, _progress_task_type
            _progress_task_type = "query"
            progress_data = {
                "percentage": int(current_tasks["query"].get("progress", 0) or 0),
                "message": "查询已终止",
                "details": {
                    "reference_round": False,
                    "cancelled": True,
                },
                "task_type": "query",
            }
        return jsonify({"success": True, "message": f"已终止 {task_type} 任务"})

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

        path = Path(file_path).expanduser()
        folder = (path.parent if path.suffix else path).resolve(strict=False)
        if not _is_path_within_root(folder, DOWNLOADS_DIR):
            return jsonify({"success": False, "message": "仅允许打开下载目录内的文件夹"})

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

    ok, msg = _require_ready()
    if not ok:
        return jsonify({"success": False, "message": msg})

    if current_tasks["extract"]["status"] == "running":
        return jsonify({"success": False, "message": "提取任务正在运行"})

    file = request.files.get("file")
    if not file:
        return jsonify({"success": False, "message": "未选择文件"})

    if not file.filename:
        return jsonify({"success": False, "message": "文件名无效"})

    safe_upload_name = _safe_upload_filename(file.filename)

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
    file_ext = os.path.splitext(safe_upload_name)[1].lower()

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
        import shutil

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
                    safe_extracted_name = _safe_upload_filename(found_file)
                    extracted_path = os.path.join(DOWNLOADS_DIR, safe_extracted_name)
                    with zip_ref.open(found_file) as source, open(
                        extracted_path, "wb"
                    ) as target:
                        shutil.copyfileobj(source, target)
                    file_path = extracted_path
                    print(f"[DEBUG] 从压缩包提取文件: {found_file}", flush=True)
                else:
                    return jsonify(
                        {"success": False, "message": "压缩包内未找到支持的文件"}
                    )
        except Exception as e:
            return jsonify({"success": False, "message": f"无法解压压缩包: {str(e)}"})
    else:
        file_path = os.path.join(DOWNLOADS_DIR, safe_upload_name)
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

    ok, msg = _require_ready()
    if not ok:
        return jsonify({"success": False, "message": msg})

    if current_tasks["query"]["status"] == "running":
        return jsonify({"success": False, "message": "查询任务正在运行"})

    data = request.json
    standards = [s.strip() for s in data.get("standards", []) if s and s.strip()]
    is_reference_round = bool(data.get("reference_round", False))
    query_round = int(data.get("query_round", 0) or 0)

    if not standards:
        return jsonify({"success": False, "message": "未输入标准号"})

    # 重置其他任务状态，避免前端混淆显示
    current_tasks["extract"]["status"] = "idle"
    current_tasks["download"]["status"] = "idle"

    def task_thread():
        query_run_id = secrets.token_hex(6)
        try:
            print(f"[QUERY TASK] 任务线程启动，输入数量: {len(standards)}", flush=True)
            _append_query_debug(
                "query_thread_start",
                {
                    "run_id": query_run_id,
                    "standards_count": len(standards),
                    "standards": standards,
                    "include_reference_query": bool(data.get("include_reference_query", False)),
                    "reference_round": is_reference_round,
                    "query_round": query_round,
                },
            )
            current_tasks["query"].update(
                {
                    "status": "running",
                    "progress": 0,
                    "message": "正在启动查询...",
                    "result": [],
                    "run_id": query_run_id,
                }
            )
            progress_data = {
                "percentage": 0,
                "message": "开始查询...",
                "details": {
                    "reference_round": is_reference_round,
                    "query_round": query_round,
                },
                "task_type": "query",
            }
            _progress_task_type = "query"

            results = []
            total = len(standards)
            started_at = time.time()
            query_svc = get_query_service(progress_callback)

            for i, std in enumerate(standards):
                if current_tasks["query"].get("run_id") != query_run_id:
                    _append_query_debug(
                        "query_run_id_mismatch_exit",
                        {
                            "run_id": query_run_id,
                            "current_run_id": current_tasks["query"].get("run_id"),
                            "index": i,
                        },
                    )
                    return
                # 检查是否请求取消
                if current_tasks["query"].get("cancel_requested"):
                    current_tasks["query"]["cancel_requested"] = False
                    current_tasks["query"].update(
                        {"status": "cancelled", "message": "用户取消"}
                    )
                    _append_query_debug(
                        "query_cancelled_before_item",
                        {"run_id": query_run_id, "index": i, "input": std},
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
                        completed = i + 1
                        elapsed = time.time() - started_at
                        avg = elapsed / completed if completed > 0 else 0
                        eta_seconds = int(max(0, (total - completed) * avg))
                        progress_callback(
                            int((completed / total) * 100),
                            100,
                            f"已处理 {completed}/{total}",
                            {
                                "current": completed,
                                "total": total,
                                "completed": completed,
                                "success": len([r for r in results if r.get("status") == "success"]),
                                "skipped": len([r for r in results if r.get("status") == "skipped"]),
                                "error": len([r for r in results if r.get("status") == "error"]),
                                "eta_seconds": eta_seconds,
                                "latest_standard": std,
                                "latest_status": "skipped",
                                "reference_round": is_reference_round,
                                "query_round": query_round,
                            },
                            "query",
                        )
                        current_tasks["query"].update(
                            {
                                "status": "running",
                                "progress": int((completed / total) * 100),
                                "message": f"已处理 {completed}/{total}",
                                "result": list(results),
                            }
                        )
                        _append_query_debug(
                            "query_item_skipped",
                            {
                                "run_id": query_run_id,
                                "index": i,
                                "input_keyword": std,
                                "reason": "未提取到标准号且不符合查询条件",
                            },
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
                _append_query_debug(
                    "query_item_start",
                    {
                        "run_id": query_run_id,
                        "index": i,
                        "input_keyword": std,
                        "extracted_standard": extracted_std,
                        "is_direct_query": is_direct_query,
                        "platform": platform,
                    },
                )

                try:
                    last_stage_message = {"value": ""}

                    def _query_stage_progress(inner_current, inner_total, inner_message, inner_details=None, _task_type=None):
                        success_count_live = len([r for r in results if r.get("status") == "success"])
                        skipped_count_live = len([r for r in results if r.get("status") == "skipped"])
                        error_count_live = len([r for r in results if r.get("status") == "error"])
                        elapsed = time.time() - started_at
                        completed_for_eta = max(i, 1)
                        avg = elapsed / completed_for_eta if completed_for_eta > 0 else 0
                        eta_seconds = int(max(0, (total - i) * avg))
                        stage_details = {
                            "current": i + 1,
                            "total": total,
                            "completed": i,
                            "success": success_count_live,
                            "skipped": skipped_count_live,
                            "error": error_count_live,
                            "eta_seconds": eta_seconds,
                            "latest_standard": extracted_std or std,
                            "latest_status": "running",
                            "stage_message": inner_message or "",
                            "reference_round": is_reference_round,
                            "query_round": query_round,
                        }
                        if isinstance(inner_details, dict):
                            stage_details.update(inner_details)
                        progress_callback(
                            int((i / total) * 100),
                            100,
                            f"正在查询 {i + 1}/{total}: {extracted_std}",
                            stage_details,
                            "query",
                        )
                        stage_msg = (inner_message or "").strip()
                        if stage_msg and stage_msg != last_stage_message["value"]:
                            last_stage_message["value"] = stage_msg
                            _append_query_debug(
                                "query_stage",
                                {
                                    "run_id": query_run_id,
                                    "index": i,
                                    "platform": platform,
                                    "standard": extracted_std,
                                    "stage_message": stage_msg,
                                    "details": stage_details,
                                },
                            )

                    query_svc.progress_callback = _query_stage_progress

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
                            per_standard_timeout_seconds = 120
                            async def _run_with_cancel_and_timeout():
                                kwargs = {"auto_switch": True}
                                task = asyncio.create_task(query_method(extracted_std, platform, **kwargs))
                                started_local = time.time()
                                last_wait_log = 0
                                while True:
                                    if current_tasks["query"].get("cancel_requested"):
                                        task.cancel()
                                        raise RuntimeError("查询已取消")
                                    done, _ = await asyncio.wait({task}, timeout=1)
                                    if done:
                                        return task.result()
                                    waited = int(time.time() - started_local)
                                    if waited - last_wait_log >= 10:
                                        last_wait_log = waited
                                        _append_query_debug(
                                            "query_waiting",
                                            {
                                                "run_id": query_run_id,
                                                "index": i,
                                                "platform": platform,
                                                "standard": extracted_std,
                                                "elapsed_sec": waited,
                                            },
                                        )
                                    if time.time() - started_local > per_standard_timeout_seconds:
                                        task.cancel()
                                        raise TimeoutError(
                                            f"查询超时（{per_standard_timeout_seconds}秒）: {extracted_std} @ {platform}"
                                        )

                            result = asyncio.run(_run_with_cancel_and_timeout())
                        except asyncio.TimeoutError:
                            per_standard_timeout_seconds = 120
                            raise TimeoutError(
                                f"查询超时（{per_standard_timeout_seconds}秒）: {extracted_std} @ {platform}"
                            )
                    else:
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                            try:
                                future = pool.submit(query_method, extracted_std, platform, True)
                            except TypeError:
                                future = pool.submit(query_method, extracted_std, platform)
                            try:
                                while True:
                                    if current_tasks["query"].get("cancel_requested"):
                                        raise RuntimeError("查询已取消")
                                    result = future.result(timeout=1)
                                    break
                            except concurrent.futures.TimeoutError:
                                per_standard_timeout_seconds = 120
                                # 每秒轮询取消，直到超时上限
                                started_local = time.time()
                                last_wait_log = 0
                                while True:
                                    if current_tasks["query"].get("cancel_requested"):
                                        raise RuntimeError("查询已取消")
                                    try:
                                        result = future.result(timeout=1)
                                        break
                                    except concurrent.futures.TimeoutError:
                                        waited = int(time.time() - started_local)
                                        if waited - last_wait_log >= 10:
                                            last_wait_log = waited
                                            _append_query_debug(
                                                "query_waiting",
                                                {
                                                    "run_id": query_run_id,
                                                    "index": i,
                                                    "platform": platform,
                                                    "standard": extracted_std,
                                                    "elapsed_sec": waited,
                                                },
                                            )
                                        if time.time() - started_local > per_standard_timeout_seconds:
                                            raise TimeoutError(
                                                f"查询超时（{per_standard_timeout_seconds}秒）: {extracted_std} @ {platform}"
                                            )

                    if current_tasks["query"].get("cancel_requested"):
                        current_tasks["query"]["cancel_requested"] = False
                        current_tasks["query"].update(
                            {"status": "cancelled", "message": "用户取消"}
                        )
                        _append_query_debug(
                            "query_cancelled_after_item",
                            {
                                "run_id": query_run_id,
                                "index": i,
                                "platform": platform,
                                "standard": extracted_std,
                            },
                        )
                        return
                    if current_tasks["query"].get("run_id") != query_run_id:
                        _append_query_debug(
                            "query_run_id_mismatch_after_item",
                            {
                                "run_id": query_run_id,
                                "current_run_id": current_tasks["query"].get("run_id"),
                                "index": i,
                            },
                        )
                        return

                    results.append(
                        {
                            "input_keyword": std,
                            "extracted_standard": extracted_std,
                            "status": "success",
                            "platform": platform,
                            "data": result,
                        }
                    )
                    _append_query_debug(
                        "query_item_success",
                        {
                            "run_id": query_run_id,
                            "index": i,
                            "platform": platform,
                            "standard": extracted_std,
                            "elapsed_ms": int((time.time() - started_at) * 1000),
                        },
                    )
                except Exception as e:
                    if current_tasks["query"].get("cancel_requested") or "查询已取消" in str(e):
                        current_tasks["query"]["cancel_requested"] = False
                        current_tasks["query"].update(
                            {"status": "cancelled", "message": "用户取消"}
                        )
                        _append_query_debug(
                            "query_cancelled_exception",
                            {
                                "run_id": query_run_id,
                                "index": i,
                                "platform": platform,
                                "standard": extracted_std,
                                "error": str(e),
                            },
                        )
                        return
                    results.append(
                        {
                            "input_keyword": std,
                            "extracted_standard": extracted_std,
                            "status": "error",
                            "platform": platform,
                            "error": str(e),
                        }
                    )
                    _append_query_debug(
                        "query_item_error",
                        {
                            "run_id": query_run_id,
                            "index": i,
                            "platform": platform,
                            "standard": extracted_std,
                            "error": str(e),
                        },
                    )

                completed = i + 1
                elapsed = time.time() - started_at
                avg = elapsed / completed if completed > 0 else 0
                eta_seconds = int(max(0, (total - completed) * avg))
                success_count_live = len([r for r in results if r.get("status") == "success"])
                skipped_count_live = len([r for r in results if r.get("status") == "skipped"])
                error_count_live = len([r for r in results if r.get("status") == "error"])
                latest = results[-1] if results else {}
                progress_callback(
                    int((completed / total) * 100),
                    100,
                    f"已处理 {completed}/{total}",
                    {
                        "current": completed,
                        "total": total,
                        "completed": completed,
                        "success": success_count_live,
                        "skipped": skipped_count_live,
                        "error": error_count_live,
                        "eta_seconds": eta_seconds,
                        "latest_standard": latest.get("extracted_standard")
                        or latest.get("input_keyword")
                        or std,
                        "latest_status": latest.get("status", ""),
                        "reference_round": is_reference_round,
                        "query_round": query_round,
                    },
                    "query",
                )
                current_tasks["query"].update(
                    {
                        "status": "running",
                        "progress": int((completed / total) * 100),
                        "message": f"已处理 {completed}/{total}",
                        "result": list(results),
                        "run_id": query_run_id,
                    }
                )
                time.sleep(0.5)  # 避免请求过快

            success_count = len([r for r in results if r["status"] == "success"])
            skipped_count = len([r for r in results if r["status"] == "skipped"])
            error_count = len([r for r in results if r["status"] == "error"])
            elapsed_total = time.time() - started_at

            progress_callback(
                100,
                100,
                f"完成 {success_count}/{total} (跳过{skipped_count}, 失败{error_count})",
                {
                    "current": total,
                    "total": total,
                    "completed": total,
                    "success": success_count,
                    "skipped": skipped_count,
                    "error": error_count,
                    "eta_seconds": 0,
                    "elapsed_seconds": int(elapsed_total),
                    "latest_standard": "",
                    "latest_status": "completed",
                    "reference_round": is_reference_round,
                    "query_round": query_round,
                },
                "query",
            )

            client_stats["queries"] += len(standards) - skipped_count
            print(f"[QUERY TASK] 查询完成，结果数量: {len(results)}", flush=True)
            current_tasks["query"].update(
                {
                    "status": "completed",
                    "progress": 100,
                    "message": f"查询完成：成功{success_count} 失败{error_count} 跳过{skipped_count}",
                    "result": results,
                    "run_id": query_run_id,
                }
            )
            _append_query_debug(
                "query_completed",
                {
                    "run_id": query_run_id,
                    "success": success_count,
                    "skipped": skipped_count,
                    "error": error_count,
                    "total": total,
                    "elapsed_seconds": int(elapsed_total),
                    "reference_round": is_reference_round,
                    "query_round": query_round,
                },
            )
            progress_data = {
                "percentage": 100,
                "message": "查询完成",
                "details": {
                    "current": total,
                    "total": total,
                    "completed": total,
                    "success": success_count,
                    "skipped": skipped_count,
                    "error": error_count,
                    "eta_seconds": 0,
                    "elapsed_seconds": int(elapsed_total),
                    "latest_status": "completed",
                    "reference_round": is_reference_round,
                    "query_round": query_round,
                },
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
            _append_query_debug(
                "query_fatal_error",
                {"run_id": query_run_id, "error": str(e)},
            )

    threading.Thread(target=task_thread, daemon=True).start()
    return jsonify({"success": True, "message": "任务已启动"})


@app.route("/api/download", methods=["POST"])
def do_download():
    """执行下载"""
    global progress_data

    ok, msg = _require_ready()
    if not ok:
        return jsonify({"success": False, "message": msg})

    if current_tasks["download"]["status"] == "running":
        return jsonify({"success": False, "message": "下载任务正在运行"})

    data = request.json
    standards = [s.strip() for s in data.get("standards", []) if s and s.strip()]
    use_online_document = bool(data.get("use_online_document", False))
    if use_online_document:
        return jsonify(
            {
                "success": False,
                "message": "在线文档接口（冰点.exe）已移除，请取消勾选后使用普通下载。",
            }
        )

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
                    (
                        f"正在下载: {std} ({query_type}) -> {extracted_std}"
                    ),
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
                    if current_tasks["download"].get("cancel_requested") or "下载已取消" in str(e):
                        current_tasks["download"]["cancel_requested"] = False
                        current_tasks["download"].update(
                            {"status": "cancelled", "message": "用户取消"}
                        )
                        return
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
        qb_manifest = _load_question_bank_manifest()
        resp = hub_request(
            "POST",
            "/api/client/heartbeat",
            {
                "client_id": CLIENT_ID,
                "no_messages": False,
                "question_bank_state": {
                    "version": qb_manifest.get("version", ""),
                    "files": _current_question_bank_hashes(),
                },
            },
        )
        if resp.get("success"):
            data = resp.get("data", {})

            # 接收新密钥续期
            new_key = data.get("config_key")
            if new_key and _encrypted_config:
                update_config_key(
                    new_key,
                    encrypted_config=data.get("encrypted_config"),
                    expires_seconds=data.get("key_expires_seconds", 300),
                )
            apply_question_bank_sync(
                data.get("question_bank_sync"), decrypt_key=(new_key or _config_key or "")
            )

            return jsonify(
                {
                    "success": True,
                    "data": data.get("messages", []),
                    "deleted_broadcasts": data.get("deleted_broadcasts", []),
                }
            )
        elif resp.get("duplicate_id"):
            conflict_msg = resp.get("message") or "设备ID异常"
            _set_hub_state("device_id_conflict", conflict_msg, conflict_msg)
            return jsonify({"success": False, "message": conflict_msg, "duplicate_id": True})
        elif resp.get("banned"):
            # 被封禁了
            print(f"[心跳] 已被封禁: {resp.get('reason')}")
            return jsonify(
                {"success": False, "banned": True, "reason": resp.get("reason")}
            )
        return jsonify({"success": True, "data": [], "deleted_broadcasts": []})


# ============ 初始化 ============
def _write_start_error(message: str):
    try:
        cache_dir = Path(__file__).resolve().parent / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "last_start_error.txt").write_text(message, encoding="utf-8")
    except Exception:
        pass


def _set_hub_state(state: str, message: str = "", last_error: str = ""):
    hub_state["state"] = state
    if message:
        hub_state["message"] = message
    if last_error:
        hub_state["last_error"] = last_error
    hub_state["hub_url"] = HUB_URL


def start_bootstrap():
    """启动后台引导流程：注册 -> 拉配置 -> 心跳续期。

    重要：即使服务端不可用，也不应阻止前端页面启动。
    """
    global HUB_URL

    def bootstrap_thread():
        global HUB_URL
        retry_count = 0
        backoff = 2

        while True:
            try:
                _set_hub_state("connecting", "正在连接服务端…")

                register_payload = {
                    "client_id": CLIENT_ID,
                    "hostname": CLIENT_FINGERPRINT.get("hostname", "unknown"),
                    "version": CLIENT_APP_VERSION,
                    "mac": CLIENT_MAC,
                    "fingerprint": CLIENT_FINGERPRINT,
                }

                resp = hub_request("POST", "/api/client/register", register_payload)

                if resp.get("banned"):
                    reason = resp.get("reason") or "已被封禁"
                    print(f"[注册] 失败: {reason}")
                    _write_start_error(f"[注册] 失败: {reason}")
                    _set_hub_state("banned", "已被服务端禁用", reason)
                    return
                if resp.get("duplicate_id"):
                    reason = resp.get("message") or "设备ID异常"
                    print(f"[注册] 失败: {reason}")
                    _write_start_error(f"[注册] 失败: {reason}")
                    _set_hub_state("device_id_conflict", "设备ID异常", reason)
                    return

                if not resp.get("success"):
                    err = resp.get("error") or "注册失败"
                    print(f"[注册] 失败: {err}")
                    _write_start_error(f"[注册] 失败: {err}")
                    retry_count += 1
                    _set_hub_state("error", "无法连接服务端，正在重试…", err)
                    time.sleep(min(backoff, 30))
                    backoff = min(backoff * 2, 30)
                    continue

                _save_client_auth_token((resp.get("data") or {}).get("auth_token", ""))
                print("[注册] 成功")

                if not download_code():
                    err = "无法从服务端获取配置（加密配置/密钥）"
                    print(f"[启动] 失败: {err}")
                    _write_start_error(f"[启动] 失败: {err}")
                    retry_count += 1
                    _set_hub_state("error", "配置获取失败，正在重试…", err)
                    time.sleep(min(backoff, 30))
                    backoff = min(backoff * 2, 30)
                    continue

                # 成功拿到配置后进入 ready，并启动心跳续期
                _set_hub_state("ready", "就绪")
                print("[启动] 配置已就绪，进入正常心跳续期")

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
                                    "fingerprint": CLIENT_FINGERPRINT,
                                    "question_bank_state": {
                                        "version": _load_question_bank_manifest().get("version", ""),
                                        "files": _current_question_bank_hashes(),
                                    },
                                },
                            )
                            retry_count = 0

                            if resp.get("success"):
                                data = resp.get("data", {})
                                new_key = data.get("config_key")
                                if new_key and _encrypted_config:
                                    update_config_key(
                                        new_key,
                                        encrypted_config=data.get("encrypted_config"),
                                        expires_seconds=data.get(
                                            "key_expires_seconds", 300
                                        ),
                                    )
                                apply_question_bank_sync(
                                    data.get("question_bank_sync"),
                                    decrypt_key=(new_key or _config_key or ""),
                                )
                                _set_hub_state("ready", "就绪")
                            elif resp.get("banned"):
                                reason = resp.get("reason") or "已被封禁"
                                print(f"[心跳] 已被封禁: {reason}")
                                _set_hub_state("banned", "已被服务端禁用", reason)
                                break
                            elif resp.get("duplicate_id"):
                                reason = resp.get("message") or "设备ID异常"
                                print(f"[心跳] 失败: {reason}")
                                _set_hub_state("device_id_conflict", "设备ID异常", reason)
                                break
                            else:
                                err = resp.get("error") or "心跳失败"
                                _set_hub_state("error", "服务端心跳失败，正在重试…", err)
                        except Exception as e:
                            retry_count += 1
                            err = str(e)
                            if retry_count <= max_retries:
                                print(f"[心跳] 连接失败 (第{retry_count}次重试): {e}")
                                _set_hub_state("error", "服务端心跳失败，正在重试…", err)
                                time.sleep(retry_delay * retry_count)
                            else:
                                print(f"[心跳] 连续{max_retries}次失败，等待重连")
                                _set_hub_state("error", "服务端心跳失败，等待重连…", err)
                                retry_count = max_retries

                threading.Thread(target=heartbeat, daemon=True).start()
                return

            except Exception as e:
                err = str(e)
                print(f"[启动] 异常: {err}")
                _write_start_error(f"[启动] 异常: {err}")
                _set_hub_state("error", "启动异常，正在重试…", err)
                time.sleep(min(backoff, 30))
                backoff = min(backoff * 2, 30)

    threading.Thread(target=bootstrap_thread, daemon=True).start()


def _require_ready():
    if hub_state.get("state") == "device_id_conflict":
        return False, "服务暂不可用，请联系管理员（设备ID异常）"
    if hub_state.get("state") == "banned":
        return False, "服务暂不可用，请联系管理员"
    if not is_config_valid():
        return False, "服务暂不可用，请联系管理员"
    return True, ""


def init():
    """兼容旧入口（不再阻塞启动）"""
    global HUB_URL
    print("=" * 50)
    print("Jingxi Client - 初始化")
    print("=" * 50)
    print(f"[Hub] 当前HUB_URL: {HUB_URL}")
    start_bootstrap()
    print("=" * 50)


if __name__ == "__main__":
    init()

    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    try:
        cache_dir = Path(__file__).resolve().parent / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "client_port.txt").write_text(str(port), encoding="utf-8")
    except Exception:
        pass

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
