---
name: token-usage-lite
description: 查询 ZCode 的 token 用量与速率。当用户问"token 用量/速率/tok/s/TTFT/首字延迟/上下文还剩多少/子代理消耗"等问题时使用。数据只读本地 SQLite 与本地日志,不联网。另有侧栏实时 HUD 面板(ui://token-usage-lite/panel.html)。
---

# ZCode token 用量查询

数据源:`~/.zcode/cli/db/db.sqlite`(只读打开,绝不写入)与 `~/.zcode/cli/log/zcode-*.jsonl`(尾部只读)。核心表:

- `model_usage`:每次模型请求一行,**请求完成即落库**——轮询得到请求级速率;列含 `duration_ms`、`time_to_first_token_ms`(TTFT)、`output_tokens`、`agent`、`model_id`。
- `turn_usage`:每回合一行,回合结束落库;`session` 表的 `task_type='subagent_child'` + `parent_id` 识别子代理会话。
- 日志尾部事件(`model.request.started/completed`、`ttft.model.first_provider_event`、`tool.call.started/completed`)驱动 HUD 的实时相位(生成中/工具执行中/空闲)与生成中已耗时、实时 TTFT。

## 怎么查

优先用 MCP 工具 `usage_snapshot`(scope: now/context/hud/subagents/today/session:<id前缀>)。hud 返回紧凑 JSON(面板轮询用)。

用户想看**实时面板**(侧栏 HUD)时:调用 MCP 工具 `open_hud`(无参数)——宿主会自动打开「Token 用量」侧栏面板;面板每秒自刷新,无需后续操作。

工具不可用时跑 CLI(插件根目录用 find 定位):

```bash
# 定位脚本(缓存按版本分目录):
find ~/.zcode/cli/plugins/cache -maxdepth 7 -path "*token-usage-lite*" -name "usage_stats.py" -not -path "*vendor*" 2>/dev/null | sort | tail -1
python3 "$US" now          # 最近一轮速率/TTFT+上下文条
python3 "$US" subagents    # 子代理统计
python3 "$US" now <会话id前缀>   # 指定会话(多窗口时用)
python3 "$US" --json now   # JSON 便于二次计算
```

## 口径说明(回复用户时要讲清)

- 速率 = output ÷ 生成窗口(completed − first_token,扣首字等待;思考前置的请求用整墙钟),与悬浮条回退口径一致;`reasoning_tokens` 单列。
- **GLM 思考 token 不入库**(provider 恒报 0),DB 任何口径都低于生成中的实时流速(悬浮条桥模式把思考字符也计入,故生成中常见 100+;完成后回退 DB 口径即 40-90,两者都是"对的",粒度不同)。
- TTFT = 首个 token 延迟,回合取第一次请求的值;子代理取其第一次请求的值。
- 上下文占用 = 最近一次已完成请求的 input+cache_read+cache_creation,进行中的请求不计入。
- 上下文窗口默认 200000(宿主 GLM 模型),可用 `--window` 调整。
- "当前会话"= 最近一条**非子代理**请求所属会话(自动排除 subagent_child,子代理刚跑完不会误判);多窗口并行时可用第二个位置参数 / MCP session 参数显式指定。
- 完成回合的"历时"用 turn_usage.duration_ms(墙钟,含工具执行);进行中回合用请求时长合计(近似)。
