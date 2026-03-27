"""
PyInstaller运行时钩子 - 配置Playwright浏览器路径
此文件会在可执行程序启动时自动执行
"""

import os
import sys


def setup_playwright_browsers():
    """设置Playwright浏览器路径指向打包的浏览器"""
    # 检查是否在PyInstaller环境中运行
    if getattr(sys, "frozen", False):
        # PyInstaller环境 - 获取解压后的临时目录
        if hasattr(sys, "_MEIPASS"):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.dirname(sys.executable)

        # 设置Playwright浏览器路径
        playwright_path = os.path.join(base_path, "playwright")
        if os.path.exists(playwright_path):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = playwright_path
            print(f"[Playwright] 浏览器路径已设置: {playwright_path}")
        else:
            # 尝试使用系统缓存路径
            home = os.path.expanduser("~")
            cache_path = os.path.join(home, ".cache", "ms-playwright")
            if os.path.exists(cache_path):
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = cache_path
                print(f"[Playwright] 使用系统缓存路径: {cache_path}")


# 立即执行设置
setup_playwright_browsers()
