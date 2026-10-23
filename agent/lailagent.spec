# PyInstaller spec for the Laila Group IT Agent.
# Build on a Windows PC:  pyinstaller lailagent.spec
# Produces dist\lailagent.exe (a single file, no console window).
block_cipher = None

a = Analysis(
    ['lailagent.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['pystray._win32', 'PIL._tkinter_finder'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter'],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='lailagent',
    debug=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,          # no console window; it lives in the tray
    icon=None,
    version=None,
)

# build: publish exe as release asset
