# PyInstaller spec for the Windows desktop build. From the repo root:
#   pip install -r backend/requirements.txt -r desktop/requirements.txt -r build-windows/requirements.txt
#   pyinstaller build-windows/aimonitor.spec --noconfirm
# Output: dist/AIMONITOR/AIMONITOR.exe (one folder; see build-windows/entry.py for its command-line roles).
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, SPECPATH)
from collect_notices import collect_notices  # noqa: E402
from desktop.tray import create_tray_icon_image  # noqa: E402

# The exe icon is the tray icon, drawn at build time so there is no binary asset to keep in sync.
os.makedirs(workpath, exist_ok=True)
ICON = os.path.join(workpath, "aimonitor.ico")
create_tray_icon_image(256, 256).save(ICON, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[ROOT],
    datas=collect_data_files("customtkinter"),  # theme JSON and fonts
    # uvicorn picks its loop/protocol modules by name at runtime; pystray picks its backend the same way.
    hiddenimports=collect_submodules("uvicorn") + ["pystray._win32"],
    excludes=["pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AIMONITOR", console=False, icon=ICON)
coll = COLLECT(exe, a.binaries, a.datas, name="AIMONITOR")
collect_notices(ROOT, coll.name, a.pure, list(a.binaries) + list(a.datas))
