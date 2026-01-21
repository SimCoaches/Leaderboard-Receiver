# -*- mode: python ; coding: utf-8 -*-


import os
sip_path = os.path.join(os.environ['APPDATA'], 'Python', 'Python311', 'site-packages', 'PyQt6', 'sip.cp311-win_amd64.pyd')

a = Analysis(
    ['gui_client_qt.py'],
    pathex=[],
    binaries=[(sip_path, 'PyQt6')],
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
