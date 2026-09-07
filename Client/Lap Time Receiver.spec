# -*- mode: python ; coding: utf-8 -*-

import PyQt6.sip
from pathlib import Path

sip_binary = PyQt6.sip.__file__

a = Analysis(
    ['gui_client_qt.py'],
    pathex=[],
    binaries=[(sip_binary, 'PyQt6')],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Qt uses the Windows ICU shim. Some build machines also put a third-party
# ICU runtime on PATH (for example Poppler); PyInstaller can collect that DLL
# and place it ahead of System32, which makes Qt6Core fail at startup.
a.binaries = [
    entry for entry in a.binaries
    if not (
        Path(entry[0]).name.lower().startswith('icu')
        and Path(entry[0]).name.lower().endswith('.dll')
    )
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Lap Time Receiver',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
