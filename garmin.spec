# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all, collect_submodules

# Collect garminconnect and all its submodules
hiddenimports = [
    "garminconnect",
    "garminconnect.api",
    "garminconnect.model",
    "garth",
    "garth.exc",
    "garth.http",
    "garth.auth",
    "garth.utils",
]

# Also collect data files from garminconnect
datas = []
binaries = []

a = Analysis(
    ["app/main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    bootloader_ignore_signals=[],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="garmin",
    debug=False,
    bootloader_ignore_signals=[],
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="garmin",
)
