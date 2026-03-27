# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

# 获取Playwright浏览器路径
def get_playwright_browser_paths():
    """获取Playwright浏览器二进制文件路径"""
    browser_paths = []
    home = Path.home()
    playwright_cache = home / '.cache' / 'ms-playwright'
    
    if playwright_cache.exists():
        # Chromium浏览器
        chromium_dirs = list(playwright_cache.glob('chromium-*'))
        for chromium_dir in chromium_dirs:
            chrome_path = chromium_dir / 'chrome-linux' / 'chrome'
            if chrome_path.exists():
                browser_paths.append((str(chrome_path.parent), 'playwright/chromium'))
            # 添加整个chromium目录
            browser_paths.append((str(chromium_dir), f'playwright/{chromium_dir.name}'))
        
        # 添加其他必要的浏览器组件
        for subdir in playwright_cache.iterdir():
            if subdir.is_dir() and not subdir.name.startswith('.'):
                browser_paths.append((str(subdir), f'playwright/{subdir.name}'))
    
    return browser_paths

block_cipher = None

# 收集Playwright浏览器
playwright_binaries = get_playwright_browser_paths()

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=playwright_binaries,
    datas=[
        ('static', 'static'),  # 包含静态文件
    ],
    hiddenimports=[
        'flask',
        'flask_cors',
        'waitress',
        'pdfplumber',
        'pdfminer',
        'PIL',
        'playwright',
        'playwright.async_api',
        'playwright.sync_api',
        'playwright.sync_api._context_manager',
        'playwright._impl',
        'playwright._impl._browser',
        'playwright._impl._browser_context',
        'playwright._impl._page',
        'playwright._impl._connection',
        'playwright._impl._driver',
        'playwright._impl._object_factory',
        'bs4',
        'lxml',
        'lxml.etree',
        'lxml._elementpath',
        'asyncio',
        'dataclasses',
        'pathlib',
        'urllib.parse',
        'urllib.request',
        'json',
        're',
        'time',
        'logging',
        'datetime',
        'typing',
        'traceback',
        'hashlib',
        'base64',
        'tempfile',
        'shutil',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['playwright_runtime_hook.py'],
    excludes=[
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'sklearn',
        'tensorflow',
        'torch',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='LabClient',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 不显示控制台窗口
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico' if os.path.exists('icon.ico') else None,  # 如果有图标
)
