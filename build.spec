# build.spec
# PyInstaller로 exe 빌드 시 사용
# 실행: pyinstaller build.spec

import sys
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("config.py", "."),
    ],
    hiddenimports=[
        "win32serviceutil",
        "win32service",
        "win32event",
        "win32api",
        "win32con",
        "servicemanager",
        "pywintypes",
        "redis",
        "redis.asyncio",
    ] + collect_submodules("redis")
      + collect_submodules("win32"),
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,      # onedir 방식
    name="PCInspectClient",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PCInspectClient",
)
