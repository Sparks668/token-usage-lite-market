# token-usage-lite-market

ZCode 插件市场:token 用量速览(MCP 查询 + 侧栏 HUD 面板 + 底部悬浮速率条)。

## 安装(三步)

1. ZCode → 插件市场 → 添加 → 粘贴本仓库地址:
   `https://github.com/<你的用户名>/token-usage-lite-market`
2. 个人市场 → **Token 用量速览** → 安装
3. **完全退出 ZCode(⌘Q / 菜单「退出」)再重开**。首次会自动安装底部悬浮条
   (会修改 ZCode 的 `app.asar`,再完全重启一次后出现);不需要悬浮条可随时
   `/uninstall-overlay` 卸载,查询与侧栏面板不受影响。

## 功能

| 组件 | 说明 |
|---|---|
| 底部悬浮条 | 输入框上方,生成中实时速率(含思考的流式口径,常见 100+ t/s);默认仅显示速率,⚙ 面板可开缓存命中/上下文/子代理等 |
| `/usage` | 最近一轮速率/TTFT/用量 + 会话累计 + 上下文进度条;`/usage today`、`/usage subagents` 等 |
| 侧栏 HUD 面板 | 会话里说「打开 token 面板」(`open_hud` 工具),每秒刷新 |
| MCP 工具 | `usage_snapshot`(now/context/hud/subagents/today/session:<id前缀>) |

## 数据与隐私

- **只读本地**:`~/.zcode/cli/db/db.sqlite` 与本地日志;不联网、不上传任何数据。
- 分发版默认**关闭**套餐额度查询与插件自动更新(上游默认开启,本版已硬化;
  如需启用见 `~/.zcode/zcode-token-usage-statusbar/config.json`)。
- 悬浮条基于 [ByteRax/zcode-token-usage-statusbar](https://github.com/ByteRax/zcode-token-usage-statusbar)
  v1.5.7(MIT)vendored,相对上游的修改见插件内 `NOTICE.md`。

## 速率口径说明

- 生成中实时读数:全部流式字符(含思考)÷ 活跃流式时长(渲染器桥数据,常见 100+ t/s)。
- 完成态/查询口径:output ÷ (completed − first_token),窗口 ∈ [200ms, 1h),无效样本剔除;
  GLM 思考 token 不入库,故完成态读数系统性地低于实时读数,属数据边界。

## 卸载

- 悬浮条:会话内 `/uninstall-overlay`(完全退出重开后消失,app.asar 恢复)。
- 整个插件:插件市场 → 卸载。

## 兼容性

- macOS / Windows;Python 3.8+(标准库,零依赖)。
- ZCode 大版本升级后悬浮条若失联:更新本插件即可(上游指纹随版本适配);
  或临时 `/install-overlay` 重装。

## License

MIT(本仓库代码);vendored 部分见 `token-usage-lite/vendor/zusage/LICENSE`。
