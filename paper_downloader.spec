# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")

analysis = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=playwright_binaries,
    datas=playwright_datas,
    hiddenimports=[
        "robust_resolver",
        "resolver_v12",
        "resolver_v13",
        "resolver_v14",
        "resolver_v15",
        "publisher_adapters",
        "browser_publisher_engine",
        "enhanced_app",
        "pypdf",
    ] + playwright_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="DOI文献批量下载器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
