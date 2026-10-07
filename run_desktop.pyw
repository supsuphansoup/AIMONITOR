#!/usr/bin/env pythonw
"""AIMONITOR Native Windows Desktop Launcher (No Console Window).

Double-click this file or use AIMONITOR.bat to run AIMONITOR as a pure
Windows desktop application with zero command prompt window.
"""
import os
import sys

root_dir = os.path.dirname(os.path.abspath(__file__))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)
if __name__ == "__main__":
    # Fix cwd before imports can read .env or resolve relative DB paths.
    os.chdir(root_dir)

from desktop.config import attach_to_default_desktop
from desktop.main import main

if __name__ == "__main__":
    attach_to_default_desktop()
    main(autostart="--autostart" in sys.argv[1:])
