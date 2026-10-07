#!/usr/bin/env python
"""AIMONITOR Desktop Launcher.

Run this script to launch:
- The Always-on-top Floating HUD Widget
- The System Tray icon
- The Claude Code auto-watcher
- The standalone desktop dashboard window on demand
"""

import os
import sys

# Ensure project root is in sys.path
root_dir = os.path.dirname(os.path.abspath(__file__))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)
if __name__ == "__main__":
    # Fix cwd before imports can read .env or resolve relative DB paths.
    os.chdir(root_dir)

from desktop.config import attach_to_default_desktop  # noqa: E402
from desktop.main import main  # noqa: E402

if __name__ == "__main__":
    attach_to_default_desktop()
    main(autostart="--autostart" in sys.argv[1:])
