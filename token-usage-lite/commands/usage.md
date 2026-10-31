---
description: 显示 ZCode token 用量(最近一轮速率/TTFT/上下文进度条/子代理统计/今日全量)
argument-hint: [now|context|subagents|today|session:<id前缀>]
---
查询 ZCode token 用量并把返回的报表**原样**回复给用户(报表已是中文渲染文本,不要改写数字或重排)。

取数顺序:

1. 优先调用 MCP 工具 `usage_snapshot`(本插件 token-usage-lite 提供)。
2. 若该工具不可用,退回用 Bash 直接跑 CLI(只读本地 SQLite,零联网):
   先定位脚本(缓存按版本分目录,直接找文件并取最新):
   `find ~/.zcode/cli/plugins/cache -maxdepth 7 -path "*token-usage-lite*" -name "usage_stats.py" -not -path "*vendor*" 2>/dev/null | sort | tail -1`
   然后:`python3 "<该文件>" <scope> [会话id前缀]`

参数规则($ARGUMENTS → scope):

- 为空或 "now" → scope="now"(最近一轮速率/TTFT/用量 + 会话累计 + 上下文进度条)
- "context" / "上下文" → scope="context"(仅上下文进度条)
- "subagents" / "子代理" → scope="subagents"(当前会话的子代理统计)
- "today" / "今天" → scope="today"(今日全量,含按模型/主子代理/按项目)
- "session:<会话id前缀>" → 原样传入(该会话明细 + 子代理)
- 其他输入 → 原样作为 scope 传入

多窗口并行且指明"当前这个对话"时,给 now/context/subagents 追加会话前缀参数。

用户附加要求:$ARGUMENTS
