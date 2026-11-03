#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SessionStart hook 入口(自研 wrapper):
1) 配置守卫:确保 ~/.zcode/zcode-token-usage-statusbar/config.json 存在且联网项默认关闭
   (vendored 代码的默认值已改为关,这里再兜底补齐缺失键;已有用户配置不覆盖其显式值);
2) 委托 vendored session-start.py(来源 zcode-token-usage-statusbar, MIT, 见 NOTICE.md)
   完成悬浮状态条的同步/自动安装;stdin/stdout 按 hook 协议透传,任何异常不影响会话。
"""
import json
import subprocess
import sys
from pathlib import Path

HARDENED = {
    "lang": "zh",
    "quota_enabled": False,
    "reset_badge_enabled": False,
    "plugin_auto_update": False,
    "auto_compact_patch": False,
    "answer_rate_line": True,
    "restart_notice": True,
    "context_window": 200000,
    "remote": {"auto": False, "enabled": False},
}
FILL_KEYS = ("plugin_auto_update", "quota_enabled", "reset_badge_enabled", "auto_compact_patch")


def ensure_config():
    d = Path.home() / ".zcode" / "zcode-token-usage-statusbar"
    try:
        d.mkdir(parents=True, exist_ok=True)
        f = d / "config.json"
        if not f.exists():
            f.write_text(json.dumps(HARDENED, ensure_ascii=False, indent=2),
                         encoding="utf-8")
            return
        try:
            cfg = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(cfg, dict):
            return
        changed = False
        for k in FILL_KEYS:
            if k not in cfg:          # 只补缺失键,绝不覆盖用户显式设置
                cfg[k] = HARDENED[k]
                changed = True
        if changed:
            f.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    except Exception:
        pass


def main():
    ensure_config()
    raw = sys.stdin.buffer.read()
    script = Path(__file__).resolve().parent.parent / "vendor" / "zusage" \
        / "hooks" / "session-start.py"
    for py in (sys.executable, "python3", "python"):
        try:
            r = subprocess.run([py, str(script)], input=raw,
                               capture_output=True, timeout=90)
        except FileNotFoundError:
            continue
        except Exception:
            return
        sys.stdout.buffer.write(r.stdout)
        sys.stdout.flush()
        return
    # 所有解释器都不可用:输出合法 hook JSON,静默退出
    print(json.dumps({"continue": True}))


main()
