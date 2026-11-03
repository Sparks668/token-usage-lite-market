#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZCode token 用量 MCP server(stdio,零依赖;注册名 usage_snapshot)。

协议仅实现 initialize / tools/list / tools/call(换行分隔 JSON-RPC 2.0)。
所有数据经 usage_stats 只读查询本地 SQLite,不联网、不写任何文件。调试信息只走 stderr。
"""
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import usage_stats  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "token-usage-lite"
SERVER_VERSION = "0.3.13"
PANEL_URI = "ui://token-usage-lite/panel.html"
PANEL_PATH = Path(__file__).parent.parent / "ui" / "panel.html"


def tool_usage_snapshot(args):
    scope = str(args.get("scope") or "now").strip()
    arg = args.get("session") or args.get("arg")
    window = int(args.get("context_window") or usage_stats.DEFAULT_WINDOW)
    con = usage_stats.connect(args.get("db"))
    try:
        data = usage_stats.make_report(con, scope, arg, window)
        if scope == "hud":
            # 面板轮询:紧凑 JSON(单行),便于 JSON.parse
            return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return usage_stats.RENDER[scope.split(":", 1)[0]](data)
    finally:
        con.close()


TOOLS = [{
    "name": "usage_snapshot",
    "description": (
        "查询 ZCode token 用量(数据只读本地 SQLite,实时近似:model_usage 每次模型请求完成即落库)。"
        "scope 可选: now(最近一轮速率/TTFT/用量+会话累计+上下文进度条,默认), "
        "context(仅上下文进度条), hud(面板实时态紧凑 JSON:相位/速率/上下文/子代理), "
        "subagents(当前会话的子代理统计,可传 arg=会话id前缀), "
        "today(今日全量+按模型+主/子代理+按项目), session:<id前缀>(指定会话明细)。"
        "context_window 可覆盖上下文窗口(默认 200000)。"
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "scope": {"type": "string",
                      "description": "now | context | hud | subagents | today | session:<id前缀>"},
            "arg": {"type": "string",
                    "description": "会话 id 前缀(now/context/subagents/session 可选,缺省=当前会话,自动排除子代理会话)"},
            "context_window": {"type": "number",
                               "description": "上下文窗口大小,默认 200000"},
        },
    },
    "_meta": {
        "ui": {"resourceUri": PANEL_URI, "surface": "hud",
               "visibility": ["model", "app"]},
    },
}, {
    "name": "open_hud",
    "description": "打开 Token 用量实时面板(侧栏 HUD:速率/TTFT/上下文/子代理,每秒刷新)。用户想看实时面板、说\"打开面板/打开 HUD\"时调用。",
    "inputSchema": {"type": "object", "properties": {}},
    "_meta": {
        "ui": {"resourceUri": PANEL_URI, "surface": "hud", "visibility": ["model"]},
        # 宿主从这个兼容键读取"自动打开侧栏"偏好(typst 同款)
        "openai/ui": {"preferredModelDisplayMode": "fullscreen"},
    },
}]

RESOURCES = [{
    "uri": PANEL_URI,
    "name": "Token HUD Panel",
    "description": "Token 用量实时面板(侧栏)",
    "mimeType": "text/html",
}]


def handle(req):
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        requested = req.get("params", {}).get("protocolVersion")
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": requested if requested == PROTOCOL_VERSION else PROTOCOL_VERSION,
            "capabilities": {
                "tools": {},
                "resources": {"subscribe": True, "listChanged": False},
            },
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}}
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"resources": RESOURCES}}
    if method in ("resources/subscribe", "resources/unsubscribe"):
        # 面板走 1s 轮询,订阅按空操作接受(不推送)以满足宿主握手
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "resources/read":
        uri = (req.get("params") or {}).get("uri")
        if uri != PANEL_URI:
            return {"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32002, "message": f"未知资源:{uri}"}}
        try:
            html = PANEL_PATH.read_text(encoding="utf-8")
        except OSError as e:
            return {"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32000, "message": f"面板文件不可读:{e}"}}
        return {"jsonrpc": "2.0", "id": rid, "result": {"contents": [{
            "uri": PANEL_URI, "mimeType": "text/html", "text": html}]}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        if name == "open_hud":
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text",
                             "text": "已请求打开 Token 用量面板。若侧栏未自动出现,请在面板/插件入口查找「Token 用量」。"}]}}
        if name != "usage_snapshot":
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": f"未知工具:{name}"}], "isError": True}}
        try:
            text = tool_usage_snapshot(params.get("arguments") or {})
        except SystemExit as e:
            text = f"⚠️ {e}"
        except Exception:
            traceback.print_exc(file=sys.stderr)
            text = "⚠️ 查询失败,详见服务端日志"
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": text}]}}
    if rid is not None:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": method}}
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            print(f"无法解析请求行:{line[:80]!r}", file=sys.stderr)
            continue
        try:
            resp = handle(req)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            resp = {"jsonrpc": "2.0", "id": req.get("id"),
                    "error": {"code": -32603, "message": "internal error"}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
