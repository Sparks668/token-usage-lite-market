#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZCode token 用量统计引擎(只读本地 SQLite;零依赖、零联网、零写入)。

数据来源:~/.zcode/cli/db/db.sqlite 的 model_usage(每次模型请求一行,请求完成即落库)、
turn_usage(每回合一行,回合结束落库)、session(parent_id/task_type 识别子代理会话)。

速率语义:model_usage 单请求速率 = output_tokens / duration_ms;回合速率 = Σoutput / Σduration。
上下文占用 = 最近一次请求的 input + cache_read + cache_creation(即该次请求的提示总长)。

"当前会话"解析:显式前缀 > 最近一条**非子代理**会话的请求(排除 subagent_child,
避免子代理刚跑完就被误判为当前会话)。

所有查询走只读 URI(mode=ro),本脚本对数据库与文件系统不做任何写操作。
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"
DEFAULT_WINDOW = 200000          # 上下文窗口(宿主 GLM 模型为 2e5),可用 --window / ZUSAGE_CONTEXT_WINDOW 覆盖
MAIN_AGENT = "zcode-agent"       # model_usage.agent 的主代理取值,其余视为子代理类型
SUBAGENT_TYPE = "subagent_child"  # session.task_type 的子代理标记


def connect(db=None):
    path = Path(db or os.environ.get("ZUSAGE_DB") or DEFAULT_DB)
    if not path.exists():
        raise SystemExit(f"未找到用量数据库:{path}")
    # 只读打开;WAL 模式下 ro 连接可读到已提交数据
    uri = f"file:{path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def fmt_int(n):
    return f"{n:,}" if isinstance(n, int) else "0"


def fmt_tok(n):
    n = n or 0
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def fmt_rate(tok_per_s):
    return f"{tok_per_s:.1f} tok/s" if tok_per_s is not None else "—"


def fmt_ms(ms):
    if ms is None:
        return "—"
    return f"{ms / 1000:.2f}s" if ms >= 1000 else f"{ms}ms"


def bar(pct, width=24):
    pct = max(0.0, min(1.0, pct))
    filled = round(pct * width)
    return "█" * filled + "░" * (width - filled)


_TPS_MIN_GEN_MS = 200          # 有效生成窗口下限(同 vendored token_rate.MIN_GEN_MS)
_TPS_MAX_GEN_MS = 3_600_000    # 有效生成窗口上限 1h(同 vendored token_rate.MAX_GEN_MS)


def _gen_ms_of(row):
    """生成窗口 ms(completed−first_token,经时间戳校验 duration≡completed−started):
    思考前置请求(reasoning>0,思考在首字前流出)用整墙钟,避免漏思考分母虚高;
    窗口缺失或超出 [200ms, 1h) 视为无效样本返回 None(不参与计速,对齐 vendored 口径)。"""
    dur = row["duration_ms"] or 0
    if dur <= 0:
        return None
    if (row["reasoning_tokens"] or 0) > 0:
        gen = dur
    else:
        ft = row["time_to_first_token_ms"]
        if not ft or ft <= 0 or dur <= ft:
            return None
        gen = dur - ft
    if gen < _TPS_MIN_GEN_MS or gen >= _TPS_MAX_GEN_MS:
        return None
    return gen


def rate_of(row):
    """单请求速率(与悬浮条回退口径一致):output ÷ 生成窗口。

    生成窗口 = completed − first_token(扣首字等待);思考前置的请求
    (reasoning_tokens>0,思考在首字前流出)用 completed − started,避免漏掉
    思考分母导致速率虚高。窗口无效时回退整墙钟。注意:GLM 的思考 token
    未上报(provider reasoning=0),DB 口径天然低于生成中的实时流速。
    """
    out = row["output_tokens"] or 0
    if out <= 0:
        return None
    gen_ms = _gen_ms_of(row)
    if gen_ms is None:
        return None
    return out * 1000.0 / gen_ms


def resolve_session(con, prefix=None):
    if not prefix:
        return None
    row = con.execute(
        "SELECT * FROM session WHERE id LIKE ? ORDER BY time_created DESC LIMIT 1",
        (prefix + "%",)).fetchone()
    if not row:
        raise SystemExit(f"未找到会话:{prefix!r}")
    return row


def latest_main_request(con, session_prefix=None):
    """基准请求:显式会话前缀优先;否则取最近一条非子代理会话的请求。"""
    if session_prefix:
        sess = resolve_session(con, session_prefix)
        return con.execute(
            "SELECT * FROM model_usage WHERE session_id=? ORDER BY started_at DESC LIMIT 1",
            (sess["id"],)).fetchone()
    return con.execute(
        """SELECT m.* FROM model_usage m
           LEFT JOIN session s ON s.id=m.session_id
           WHERE s.id IS NULL OR s.task_type IS NULL OR s.task_type<>?
           ORDER BY m.started_at DESC LIMIT 1""", (SUBAGENT_TYPE,)).fetchone()


def current_session_row(con, session_prefix=None):
    if session_prefix:
        return resolve_session(con, session_prefix)
    latest = latest_main_request(con)
    if not latest:
        return None
    return con.execute("SELECT * FROM session WHERE id=?",
                       (latest["session_id"],)).fetchone()


def session_totals(con, session_id):
    """会话累计(仅回合已落库部分;进行中回合的请求不在内)。"""
    row = con.execute(
        """SELECT COUNT(*) turns, COALESCE(SUM(input_tokens),0) inp,
                  COALESCE(SUM(output_tokens),0) out, COALESCE(SUM(reasoning_tokens),0) reasoning,
                  COALESCE(SUM(cache_read_input_tokens),0) cache_read,
                  COALESCE(SUM(cache_creation_input_tokens),0) cache_create
           FROM turn_usage WHERE session_id=?""", (session_id,)).fetchone()
    return dict(row)


# ---------- 各 scope ----------

def report_now(con, window=DEFAULT_WINDOW, session_prefix=None):
    """最近一轮:每请求速率 + 回合聚合 + 会话累计 + 上下文进度条。"""
    latest = latest_main_request(con, session_prefix)
    if not latest:
        return {"error": "数据库中还没有任何模型请求记录"}
    sid, tid = latest["session_id"], latest["turn_id"]
    rows = con.execute(
        "SELECT * FROM model_usage WHERE session_id=? AND turn_id=? ORDER BY started_at",
        (sid, tid)).fetchall()
    if not rows:
        # 防御:turn_id 缺失/异常时退化为仅展示这条请求(当前库中 turn_id 无 NULL,纯保险)
        rows = [latest]
    turn_row = con.execute("SELECT * FROM turn_usage WHERE session_id=? AND turn_id=?",
                           (sid, tid)).fetchone()
    running = turn_row is None      # 回合结束才写 turn_usage;缺席即进行中
    state = "running" if running else (turn_row["status"] or "completed")
    sess = con.execute("SELECT * FROM session WHERE id=?", (sid,)).fetchone()
    last = rows[-1]
    reqs = [{
        "model": r["model_id"], "agent": r["agent"],
        "started_at": r["started_at"], "duration_ms": r["duration_ms"],
        "ttft_ms": r["time_to_first_token_ms"],
        "output_tokens": r["output_tokens"] or 0,
        "reasoning_tokens": r["reasoning_tokens"] or 0,
        "rate_tps": round(rate_of(r), 2) if rate_of(r) is not None else None,
    } for r in rows]
    turn_out = sum(r["output_tokens"] or 0 for r in rows)
    turn_reasoning = sum(r["reasoning_tokens"] or 0 for r in rows)
    req_ms = sum(r["duration_ms"] or 0 for r in rows)
    # 生成窗口口径(无效样本整行剔除):Σ有效 output ÷ Σ有效窗口,与悬浮条 _turn_rate 一致
    rated = [(r["output_tokens"] or 0, _gen_ms_of(r)) for r in rows]
    rated = [(o, g) for o, g in rated if g is not None and o > 0]
    rated_out = sum(o for o, _ in rated)
    rated_ms = sum(g for _, g in rated)
    # 完成回合用落库的墙钟时长(含工具执行);进行中回合用请求时长合计(近似)
    duration_ms = turn_row["duration_ms"] if turn_row and turn_row["duration_ms"] else req_ms
    first_ttft = rows[0]["time_to_first_token_ms"]
    return {
        "scope": "now",
        "session_id": sid,
        "session_title": (sess["title"] or "")[:60] if sess else "",
        "state": state,
        "model": last["model_id"],
        "requests": reqs,
        "last_request": {"rate_tps": reqs[-1]["rate_tps"],
                         "ttft_ms": last["time_to_first_token_ms"],
                         "output_tokens": last["output_tokens"] or 0},
        "turn": {"request_count": len(rows),
                 "output_tokens": turn_out, "reasoning_tokens": turn_reasoning,
                 "duration_ms": duration_ms,
                 "duration_is_wall": bool(turn_row and turn_row["duration_ms"]),
                 "avg_rate_tps": round(rated_out * 1000.0 / rated_ms, 2) if rated_ms else None,
                 "rated_requests": len(rated),
                 "first_ttft_ms": first_ttft},
        "session_totals": session_totals(con, sid),
        "context": report_context(con, window, session_prefix),
    }


def report_context(con, window=DEFAULT_WINDOW, session_prefix=None):
    """上下文进度条:最近一次请求的提示总长(input+cache_read+cache_creation)。"""
    latest = latest_main_request(con, session_prefix)
    if not latest:
        return {"error": "暂无请求记录"}
    prompt = (latest["input_tokens"] or 0) + (latest["cache_read_input_tokens"] or 0) \
        + (latest["cache_creation_input_tokens"] or 0)
    pct = prompt / window if window else 0
    return {"prompt_tokens": prompt, "window": window, "ratio": round(pct, 4),
            "session_id": latest["session_id"],
            "level": "danger" if pct >= 0.95 else "warn" if pct >= 0.8 else "ok",
            "note": "取自最近一次已完成请求,进行中的请求不计入"}


def report_subagents(con, session_prefix=None):
    """子代理统计:parent_id 指向目标会话的 subagent_child 会话,一次查询聚合。

    TTFT 取该子代理**第一次请求**的值(与会话级"首字"口径一致);agent 取其首行。
    by_agent 覆盖全部子会话,不受展示条数限制。
    """
    if session_prefix:
        sess = resolve_session(con, session_prefix)
        parent_id = sess["id"]
    else:
        cur = current_session_row(con)
        if not cur:
            return {"error": "暂无会话"}
        parent_id = cur["id"]
    children = con.execute(
        """SELECT s.id, s.title, s.time_created FROM session s
           WHERE s.parent_id=? AND s.task_type=?
           ORDER BY s.time_created DESC""", (parent_id, SUBAGENT_TYPE)).fetchall()
    if not children:
        return {"scope": "subagents", "parent_session": parent_id, "count": 0,
                "items": [], "by_agent": []}
    rows = con.execute(
        f"""SELECT * FROM model_usage
            WHERE session_id IN ({','.join('?' * len(children))})
            ORDER BY started_at""", [c["id"] for c in children]).fetchall()
    by_sid = {}
    for r in rows:
        by_sid.setdefault(r["session_id"], []).append(r)
    items, by_agent = [], {}
    for ch in children:
        rs = by_sid.get(ch["id"])
        if not rs:
            continue
        out_total = sum(r["output_tokens"] or 0 for r in rs)
        rated_pairs = [(r["output_tokens"] or 0, _gen_ms_of(r)) for r in rs]
        rated_pairs = [(o, g) for o, g in rated_pairs if g is not None and o > 0]
        dur_total = sum(g for _, g in rated_pairs)
        agent = rs[0]["agent"] or "unknown"
        item = {
            "session_id": ch["id"], "title": (ch["title"] or "")[:48],
            "agent": agent, "model": rs[-1]["model_id"],
            "requests": len(rs), "input_tokens": sum(r["input_tokens"] or 0 for r in rs),
            "output_tokens": out_total,
            "avg_rate_tps": round(sum(o for o, _ in rated_pairs) * 1000.0 / dur_total, 2) if dur_total else None,
            "first_ttft_ms": rs[0]["time_to_first_token_ms"],
        }
        items.append(item)
        a = by_agent.setdefault(agent, {"agent": agent, "sessions": 0,
                                        "requests": 0, "output_tokens": 0})
        a["sessions"] += 1
        a["requests"] += len(rs)
        a["output_tokens"] += out_total
    return {"scope": "subagents", "parent_session": parent_id, "count": len(items),
            "items": items, "by_agent": sorted(by_agent.values(),
                                               key=lambda x: -x["output_tokens"])}


# SQL 生成窗口(与 _gen_ms_of 同口径):思考前置用整墙钟,其余扣首字
_SQL_GEN_WINDOW = ("CASE WHEN duration_ms IS NULL OR duration_ms<=0 THEN 0"
                   " WHEN reasoning_tokens>0 THEN duration_ms"
                   " WHEN time_to_first_token_ms IS NULL OR time_to_first_token_ms<=0"
                   "      OR duration_ms<=time_to_first_token_ms THEN 0"
                   " ELSE duration_ms-time_to_first_token_ms END")
_SQL_GEN_VALID = (f"({_SQL_GEN_WINDOW} >= {_TPS_MIN_GEN_MS}"
                  f" AND {_SQL_GEN_WINDOW} < {_TPS_MAX_GEN_MS})")


def _rated_sql():
    """返回 (rated_out, gen_ms) 两个聚合表达式:仅有效样本计入。"""
    return (f"SUM(CASE WHEN {_SQL_GEN_VALID} THEN output_tokens ELSE 0 END)",
            f"SUM(CASE WHEN {_SQL_GEN_VALID} THEN {_SQL_GEN_WINDOW} ELSE 0 END)")


def report_today(con, window=DEFAULT_WINDOW):
    """今日全量:回合/请求聚合 + 按模型 + 主/子代理 + 按项目。"""
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    since = int(midnight.timestamp() * 1000)
    t = con.execute(
        """SELECT COUNT(*) turns, COALESCE(SUM(model_request_count),0) requests,
                  COALESCE(SUM(input_tokens),0) inp, COALESCE(SUM(output_tokens),0) out,
                  COALESCE(SUM(reasoning_tokens),0) reasoning,
                  COALESCE(SUM(cache_read_input_tokens),0) cache_read,
                  COALESCE(SUM(cache_creation_input_tokens),0) cache_create
           FROM turn_usage WHERE started_at>=?""", (since,)).fetchone()
    rated_out_sql, gen_ms_sql = _rated_sql()
    by_model = [dict(r) for r in con.execute(
        f"""SELECT model_id, COUNT(*) requests, COALESCE(SUM(output_tokens),0) out,
                  {rated_out_sql} rated_out, {gen_ms_sql} gen_ms
           FROM model_usage WHERE started_at>=? GROUP BY model_id ORDER BY out DESC""",
        (since,))]
    for m in by_model:
        m["avg_rate_tps"] = round(m["rated_out"] * 1000.0 / m["gen_ms"], 2) if m["gen_ms"] else None
    by_agent = [dict(r) for r in con.execute(
        """SELECT m.agent, s.task_type, COUNT(DISTINCT m.session_id) sessions,
                  COUNT(*) requests, COALESCE(SUM(m.output_tokens),0) out
           FROM model_usage m LEFT JOIN session s ON s.id=m.session_id
           WHERE m.started_at>=? GROUP BY m.agent, s.task_type ORDER BY out DESC""",
        (since,))]
    by_project = [dict(r) for r in con.execute(
        """SELECT COALESCE(s.directory,'?') directory, COUNT(*) turns,
                  COALESCE(SUM(t.output_tokens),0) out
           FROM turn_usage t LEFT JOIN session s ON s.id=t.session_id
           WHERE t.started_at>=? GROUP BY s.directory ORDER BY out DESC LIMIT 8""",
        (since,))]
    return {"scope": "today", "since": since, "totals": dict(t),
            "by_model": by_model, "by_agent": by_agent, "by_project": by_project,
            "context": report_context(con, window)}


def report_session(con, prefix=None):
    sess = resolve_session(con, prefix) or current_session_row(con)
    if not sess:
        return {"error": "暂无会话"}
    sid = sess["id"]
    totals = session_totals(con, sid)
    rated_out_sql, gen_ms_sql = _rated_sql()
    by_model = [dict(r) for r in con.execute(
        f"""SELECT model_id, COUNT(*) requests, COALESCE(SUM(output_tokens),0) out,
                  {rated_out_sql} rated_out, {gen_ms_sql} gen_ms
           FROM model_usage WHERE session_id=? GROUP BY model_id ORDER BY out DESC""", (sid,))]
    for m in by_model:
        m["avg_rate_tps"] = round(m["rated_out"] * 1000.0 / m["gen_ms"], 2) if m["gen_ms"] else None
    subs = report_subagents(con, sid)
    return {"scope": "session", "session_id": sid, "title": (sess["title"] or "")[:80],
            "directory": sess["directory"], "task_type": sess["task_type"],
            "totals": totals, "by_model": by_model, "subagents": subs}


# ---------- HUD(面板实时态:日志尾部 + DB 聚合) ----------

LOG_DIR = Path.home() / ".zcode" / "cli" / "log"
LOG_EVENTS = ("model.request.started", "model.request.completed",
              "ttft.model.first_provider_event", "ttft.model.completed",
              "tool.call.started", "tool.call.completed")


def _evt_time(d):
    """事件时刻(epoch ms):优先 context.at,否则解析 ISO timestamp。"""
    at = (d.get("context") or {}).get("at")
    if isinstance(at, (int, float)) and at > 0:
        return int(at)
    ts = d.get("timestamp")
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except ValueError:
            return 0
    return 0


def tail_events(session_ids, max_bytes=262144, files=2):
    """取最近日志文件尾部、属于目标会话集合的生命周期事件(只读)。

    按时间升序返回 [(event, requestId, session_id, time_ms)];解析失败的行静默跳过。
    """
    ids = set(session_ids)
    out = []
    try:
        logs = sorted(LOG_DIR.glob("zcode-*.jsonl"))[-files:]
    except OSError:
        return out
    for path in logs:
        try:
            with open(path, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - max_bytes))
                chunk = fh.read().decode("utf-8", "replace")
        except OSError:
            continue
        for line in chunk.splitlines():
            if not line.startswith("{"):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            ev = d.get("event")
            sid = d.get("sessionId")
            if ev not in LOG_EVENTS or sid not in ids:
                continue
            # toolCallId 在顶层;model 事件无稳定 requestId,配对改在 phase_of 按计数完成
            out.append((ev, d.get("toolCallId") or "", sid, _evt_time(d)))
    out.sort(key=lambda x: x[3])
    return out


def report_hud(con, window=DEFAULT_WINDOW, session_prefix=None):
    """面板专用:实时相位(日志)+ 请求级统计(DB),全部只读。"""
    sess = current_session_row(con, session_prefix)
    if not sess:
        return {"error": "暂无会话"}
    sid = sess["id"]
    children = [r["id"] for r in con.execute(
        "SELECT id FROM session WHERE parent_id=? AND task_type=?",
        (sid, SUBAGENT_TYPE))]
    evs = tail_events([sid] + children)
    now_ms = int(datetime.now().timestamp() * 1000)

    def phase_of(target_sid):
        """(phase, started_ms, ttft_ms):按会话计数配对(单会话同一时刻至多一个模型请求)。

        生成中的请求若已见首个 provider 事件,同时给出实时 TTFT。
        """
        model_open, tool_open = 0, 0
        started_ms, ttft_ms = 0, None
        for ev, rid, esid, t in evs:
            if esid != target_sid:
                continue
            if ev == "model.request.started":
                model_open += 1
                started_ms, ttft_ms = t, None
            elif ev == "ttft.model.first_provider_event":
                if model_open > 0 and ttft_ms is None and started_ms:
                    ttft_ms = t - started_ms
            elif ev == "model.request.completed":
                model_open = max(0, model_open - 1)
            elif ev == "tool.call.started":
                tool_open += 1
            elif ev == "tool.call.completed":
                tool_open = max(0, tool_open - 1)
        if model_open > 0:
            return "generating", started_ms, ttft_ms
        if tool_open > 0:
            return "tool", 0, None
        return "idle", 0, None

    phase, started_ms, live_ttft = phase_of(sid)
    sub_running = 0
    for ch in children:
        p, _, _ = phase_of(ch)
        if p != "idle":
            sub_running += 1

    latest = latest_main_request(con, session_prefix)
    rows = con.execute(
        "SELECT * FROM model_usage WHERE session_id=? AND turn_id=? ORDER BY started_at",
        (sid, latest["turn_id"])).fetchall() or [latest]
    turn_row = con.execute("SELECT * FROM turn_usage WHERE session_id=? AND turn_id=?",
                           (sid, latest["turn_id"])).fetchone()
    turn_out = sum(r["output_tokens"] or 0 for r in rows)
    rated = [(r["output_tokens"] or 0, _gen_ms_of(r)) for r in rows]
    rated = [(o, g) for o, g in rated if g is not None and o > 0]
    rated_ms = sum(g for _, g in rated)
    rated_out = sum(o for o, _ in rated)
    sub_agg = con.execute(
        """SELECT COUNT(DISTINCT m.session_id) n, COALESCE(SUM(m.output_tokens),0) out
           FROM model_usage m JOIN session s ON s.id=m.session_id
           WHERE s.parent_id=? AND s.task_type=?""", (sid, SUBAGENT_TYPE)).fetchone()
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today = con.execute(
        "SELECT COUNT(*) turns, COALESCE(SUM(output_tokens),0) out FROM turn_usage WHERE started_at>=?",
        (int(midnight.timestamp() * 1000),)).fetchone()
    last = rows[-1]
    prompt = (last["input_tokens"] or 0) + (last["cache_read_input_tokens"] or 0) \
        + (last["cache_creation_input_tokens"] or 0)
    return {
        "scope": "hud", "session_id": sid, "model": last["model_id"],
        "now_ms": now_ms,
        "phase": phase, "request_started_ms": started_ms, "live_ttft_ms": live_ttft,
        "last": {"rate_tps": round((last["output_tokens"] or 0) * 1000.0 / last["duration_ms"], 2)
                 if last["duration_ms"] else None,
                 "ttft_ms": last["time_to_first_token_ms"],
                 "output_tokens": last["output_tokens"] or 0},
        "turn": {"requests": len(rows), "output_tokens": turn_out,
                 "avg_rate_tps": round(rated_out * 1000.0 / rated_ms, 2) if rated_ms else None,
                 "running": turn_row is None},
        "session": session_totals(con, sid),
        "subagents": {"running": sub_running, "total": sub_agg["n"] if sub_agg else 0,
                      "output_tokens": sub_agg["out"] if sub_agg else 0},
        "today": {"turns": today["turns"], "output_tokens": today["out"]},
        "context": {"prompt_tokens": prompt, "window": window,
                    "ratio": round(prompt / window, 4) if window else 0},
    }




STATE_LABEL = {"running": "🔄 进行中", "completed": "✅ 已完成",
               "cancelled": "🚫 已取消", "error": "❌ 出错"}


def render_now(d):
    if "error" in d:
        return f"⚠️ {d['error']}"
    state = STATE_LABEL.get(d["state"], d["state"])
    L = [f"⚡ 最近一轮({state} · {d['turn']['request_count']} 次请求 · {d['model']})"]
    lr = d["last_request"]
    L.append(f"  最近请求:{fmt_rate(lr['rate_tps'])} · 首字 {fmt_ms(lr['ttft_ms'])} · 输出 {fmt_int(lr['output_tokens'])} tok")
    t = d["turn"]
    L.append(f"  本轮累计:输出 {fmt_int(t['output_tokens'])} tok(含思考 {fmt_int(t['reasoning_tokens'])})"
             f" · 平均 {fmt_rate(t['avg_rate_tps'])} · 首字 {fmt_ms(t['first_ttft_ms'])} · 历时 {fmt_ms(t['duration_ms'])}")
    s = d["session_totals"]
    L.append(f"  会话累计(不含子代理):{s['turns']} 轮 · 输入 {fmt_tok(s['inp'])} · 输出 {fmt_tok(s['out'])} · 缓存读 {fmt_tok(s['cache_read'])}")
    c = d["context"]
    if "error" not in c:
        L.append(render_context(c))
    return "\n".join(L)


def render_context(d):
    if "error" in d:
        return f"⚠️ {d['error']}"
    mark = "🔴" if d["level"] == "danger" else "🟡" if d["level"] == "warn" else "🟢"
    return (f"{mark} 上下文 {bar(d['ratio'])} {d['ratio']*100:.1f}%"
            f" · {fmt_int(d['prompt_tokens'])} / {fmt_int(d['window'])} tok")


def render_subagents(d):
    if "error" in d:
        return f"⚠️ {d['error']}"
    if d["count"] == 0:
        return "🧩 本会话暂无子代理记录"
    L = [f"🧩 子代理统计(本会话 {d['count']} 个)"]
    for a in d["by_agent"]:
        L.append(f"  {a['agent']} ×{a['sessions']} 会话 · {a['requests']} 请求 · 输出 {fmt_tok(a['output_tokens'])} tok")
    for it in d["items"][:10]:
        L.append(f"  · [{it['agent']}] {it['title'] or it['session_id'][:28]}"
                 f" — {fmt_rate(it['avg_rate_tps'])} · 输出 {fmt_tok(it['output_tokens'])} · 首字 {fmt_ms(it['first_ttft_ms'])}")
    return "\n".join(L)


def render_today(d):
    t = d["totals"]
    L = [f"🧾 今日({datetime.fromtimestamp(d['since']/1000).strftime('%Y-%m-%d')})"
         f" — {t['turns']} 轮 · {t['requests']} 请求"]
    L.append(f"  输入 {fmt_tok(t['inp'])} · 输出 {fmt_tok(t['out'])}(思考 {fmt_tok(t['reasoning'])})"
             f" · 缓存读 {fmt_tok(t['cache_read'])} · 缓存写 {fmt_tok(t['cache_create'])}")
    if d["by_model"]:
        ms = " · ".join(f"{m['model_id']} {fmt_tok(m['out'])}({fmt_rate(m['avg_rate_tps'])})" for m in d["by_model"][:4])
        L.append(f"  按模型:{ms}")
    subs = [a for a in d["by_agent"] if a["task_type"] == SUBAGENT_TYPE]
    if subs:
        ss = " · ".join(f"{a['agent'] or '?'} ×{a['sessions']} {fmt_tok(a['out'])}" for a in subs[:4])
        L.append(f"  子代理:{ss}")
    for p in d["by_project"][:3]:
        name = Path(p["directory"]).name if p["directory"] != "?" else "?"
        L.append(f"  项目 {name}:{p['turns']} 轮 · 输出 {fmt_tok(p['out'])}")
    c = d["context"]
    if "error" not in c:
        L.append("  " + render_context(c))
    return "\n".join(L)


def render_session(d):
    if "error" in d:
        return f"⚠️ {d['error']}"
    L = [f"📋 会话 {d['session_id'][:32]}({d['task_type']})"]
    if d["title"]:
        L.append(f"  {d['title']}")
    t = d["totals"]
    L.append(f"  累计 {t['turns']} 轮 · 输入 {fmt_tok(t['inp'])} · 输出 {fmt_tok(t['out'])} · 缓存读 {fmt_tok(t['cache_read'])}")
    for m in d["by_model"]:
        L.append(f"  · {m['model_id']}:{m['requests']} 请求 · 输出 {fmt_tok(m['out'])} · 平均 {fmt_rate(m['avg_rate_tps'])}")
    L.append(render_subagents(d["subagents"]))
    return "\n".join(L)


def render_hud(d):
    if "error" in d:
        return f"⚠️ {d['error']}"
    ph = {"generating": "🔄 生成中", "tool": "🔧 工具执行中", "idle": "💤 空闲"}[d["phase"]]
    L = [f"⏱ {ph} · {d['model']}"
         + (f" · 已 {(d['now_ms']-d['request_started_ms'])/1000:.1f}s" if d["request_started_ms"] else "")
         + (f" · 首字 {d['live_ttft_ms']/1000:.2f}s" if d["live_ttft_ms"] else "")]
    L.append(json.dumps({k: d[k] for k in
                         ("last", "turn", "context", "subagents", "today")},
                        ensure_ascii=False))
    return "\n".join(L)


RENDER = {"now": render_now, "context": render_context, "hud": render_hud,
          "subagents": render_subagents, "today": render_today, "session": render_session}


def make_report(con, scope, arg=None, window=DEFAULT_WINDOW):
    """scope 统一入口:解析 session:<前缀> 形式,返回 dict;渲染交给 RENDER。"""
    if scope.startswith("session:"):
        scope, arg = "session", scope.split(":", 1)[1].strip() or None
    if scope == "now":
        return report_now(con, window, arg)
    if scope == "context":
        return report_context(con, window, arg)
    if scope == "hud":
        return report_hud(con, window, arg)
    if scope == "subagents":
        return report_subagents(con, arg)
    if scope == "today":
        return report_today(con, window)
    if scope == "session":
        return report_session(con, arg)
    known = "now | context | subagents | today | session:<id前缀>"
    raise SystemExit(f"未知 scope:{scope!r}。可用:{known}")


def build_report(con, scope, arg=None, window=DEFAULT_WINDOW):
    data = make_report(con, scope, arg, window)
    base = scope.split(":", 1)[0]
    return RENDER.get(base, render_now)(data)


def _env_window():
    try:
        return int(os.environ.get("ZUSAGE_CONTEXT_WINDOW", ""))
    except ValueError:
        return DEFAULT_WINDOW


def main(argv=None):
    p = argparse.ArgumentParser(description="ZCode token 用量(只读本地 SQLite)")
    p.add_argument("scope", nargs="?", default="now",
                   help="now | context | subagents | today | session:<id前缀>")
    p.add_argument("arg", nargs="?", help="会话 id 前缀(now/context/subagents/session 可选)")
    p.add_argument("--json", action="store_true", help="输出 JSON 而非渲染文本")
    p.add_argument("--window", type=int, default=_env_window(),
                   help="上下文窗口大小(默认 200000)")
    p.add_argument("--db", default=None, help="数据库路径(默认 ~/.zcode/cli/db/db.sqlite)")
    a = p.parse_args(argv)
    con = connect(a.db)
    try:
        if a.json:
            print(json.dumps(make_report(con, a.scope, a.arg, a.window),
                             ensure_ascii=False, indent=2))
        else:
            print(build_report(con, a.scope, a.arg, a.window))
    finally:
        con.close()


if __name__ == "__main__":
    main()
