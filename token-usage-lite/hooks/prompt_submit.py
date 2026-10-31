#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UserPromptSubmit hook 入口:配置守卫后委托 vendored prompt-submit.py
(记录提问时刻以驱动悬浮条上一轮速率行;见 NOTICE.md)。协议透传,绝不抛出。"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_start import ensure_config  # noqa: E402


def main():
    ensure_config()
    raw = sys.stdin.buffer.read()
    script = Path(__file__).resolve().parent.parent / "vendor" / "zusage" \
        / "hooks" / "prompt-submit.py"
    for py in (sys.executable, "python3", "python"):
        try:
            r = subprocess.run([py, str(script)], input=raw,
                               capture_output=True, timeout=8)
        except FileNotFoundError:
            continue
        except Exception:
            return
        sys.stdout.buffer.write(r.stdout)
        sys.stdout.flush()
        return
    print('{"continue": true}')


main()
