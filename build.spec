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
        "servicemanager",
        "redis",
        "redis.asyncio",
    ] + collect_submodules("redis"),
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="PCInspectClient",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,           # 서비스 디버그 시 콘솔 출력 보이게
    icon=None,
)
