import json
import sys
from pathlib import Path


def main():
    try:
        raw_input = sys.stdin.read()
        if not raw_input:
            return
        aimonitor_dir = Path.home() / ".aimonitor"
        aimonitor_dir.mkdir(parents=True, exist_ok=True)
        (aimonitor_dir / "claude_statusline_raw.json").write_text(raw_input, encoding="utf-8")
        data = json.loads(raw_input)
        rl = data.get("rate_limits")
        if rl:
            (aimonitor_dir / "claude_rate_limits.json").write_text(json.dumps(rl, indent=2), encoding="utf-8")
    except Exception:
        pass


if __name__ == "__main__":
    main()
