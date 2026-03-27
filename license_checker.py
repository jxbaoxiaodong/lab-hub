#!/usr/bin/env python3
"""
许可证检查模块 - 自动嵌入所有功能模块
"""

import urllib.request
import json
import os

HUB_URL = os.environ.get("LAB_HUB_URL", "https://testmumu.ftir.fun")
CLIENT_ID = os.environ.get("LAB_CLIENT_ID", "unknown")


def check_license() -> dict:
    """
    检查执行许可
    返回: {"allowed": True/False, "reason": "..."}
    """
    try:
        req = urllib.request.Request(
            f"{HUB_URL}/api/license/verify",
            data=json.dumps({"client_id": CLIENT_ID}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        # 网络问题，允许执行但记录
        return {"allowed": True, "warning": f"许可证检查失败: {e}"}


def require_license(func):
    """装饰器：执行前检查许可证"""

    def wrapper(*args, **kwargs):
        result = check_license()
        if not result.get("allowed"):
            raise PermissionError(f"执行被拒绝: {result.get('reason', '未知原因')}")
        return func(*args, **kwargs)

    return wrapper
