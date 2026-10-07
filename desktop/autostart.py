"""Windows per-user startup registration; the registry is the source of truth."""

import ntpath
import sys
from pathlib import Path

if sys.platform == "win32":
    import winreg
else:
    winreg = None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "AIMONITOR"
ENABLED = "enabled"
DISABLED = "disabled"
OTHER_LOCATION = "other_location"
UNSUPPORTED = "unsupported"


def command() -> str:
    """Return the quoted console-free command for this checkout."""
    executable = Path(sys.executable)
    if executable.name.lower() == "python.exe":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.is_file():
            executable = pythonw
    launcher = Path(__file__).resolve().parent.parent / "run_desktop.pyw"
    return f'"{executable}" "{launcher}" --autostart'


def get_status() -> str:
    """Read current registration. Access errors propagate to the UI."""
    if sys.platform != "win32" or winreg is None:
        return UNSUPPORTED
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, kind = winreg.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return DISABLED
    if (kind == winreg.REG_SZ and isinstance(value, str)
            and ntpath.normcase(value) == ntpath.normcase(command())):
        return ENABLED
    return OTHER_LOCATION


def enable() -> bool:
    """Register this checkout, replacing any old location. No-op outside Windows."""
    if sys.platform != "win32" or winreg is None:
        return False
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
    return True


def disable() -> bool:
    """Remove only AIMONITOR's value; missing registration is already disabled."""
    if sys.platform != "win32" or winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass
    return True
