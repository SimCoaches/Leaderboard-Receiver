# -*- mode: python ; coding: utf-8 -*-


import os

# Manually add PyQt6.sip binary
sip_binary = []
try:
    import PyQt6.sip
    sip_path = PyQt6.sip.__file__
    # Add the .pyd file to binaries, placing it in PyQt6 directory
    sip_binary = [(sip_path, 'PyQt6')]
except Exception as e:
    print(f"Warning: Could not find PyQt6.sip: {e}")

a = Analysis(
    ['gui_client_qt.py'],
    pathex=[],
    binaries=sip_binary,
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
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
