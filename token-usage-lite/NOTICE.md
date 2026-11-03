# NOTICE — 第三方代码声明

本插件包含来自 [zcode-token-usage-statusbar](https://github.com/ByteRax/zcode-token-usage-statusbar)
(v1.5.10,作者 xhwxt/ByteRax)的代码,按 **MIT 许可证** vendored 引入,
许可证全文见 [vendor/zusage/LICENSE](vendor/zusage/LICENSE),版权归原作者所有。

## 引入范围

`vendor/zusage/` 下的悬浮用量状态条运行时:overlay.js(状态条 UI)、inject-main.cjs
(主进程加载器)、patch_install.py(app.asar 补丁)、overlay_install.py / install_monitor.py /
restart_notice.py(安装与自愈)、zusage.py / usage_stats.py / token_rate.py / turn-stats.js /
renderer_bridge.py(统计引擎与流式桥)、plugin-updater.cjs / plugin-release.cjs /
plugin-storage.cjs / quota-client.cjs / model-puller.cjs / release_unpack.py / runtime_lock.py、
hooks/session-start.py 与 hooks/prompt-submit.py。

## 相对上游的修改

1. **路径迁移**:运行时文件移至 `vendor/zusage/`(含上游 `.zcode-plugin/plugin.json` 副本,
   仅供其自检读取版本),插件根目录引用相应调整;运行时安装目录仍为
   `~/.zcode/zcode-token-usage-statusbar/`(与上游一致)。
2. **默认值硬化(3 处)**:
   - `plugin_auto_update.py`:`plugin_auto_update` 缺省 `True → False`(不自动拉更新)
   - `inject-main.cjs`:`quotaEnabled()` 由 `!== false` 改为 `=== true`(缺省不查套餐额度)
   - `plugin-updater.cjs`:自动更新由"键缺失即开"改为"仅显式 true 才开"
   - 上游缺省开启的套餐额度查询与插件自动更新在本分发版中**缺省关闭**;如需启用,
     在 `~/.zcode/zcode-token-usage-statusbar/config.json` 中显式设置。
3. **配置守卫**:本插件 `hooks/session_start.py` / `hooks/prompt_submit.py` 为自研 wrapper,
   在委托上游 hook 前确保上述硬化配置存在(只补缺失键,不覆盖用户显式值)。
4. **改名分发适配(2 处)**:上游运行时同步验收硬编码了自己的插件 id
   (`zcode-token-usage-statusbar@zusage-marketplace`),vendored 改名后必然失配。
   在 `overlay_install.py` 与 `plugin-updater.cjs` 中把"记录不存在"的拒绝条件从
   `hasPending || inventory` 收窄为 `hasPending`(保留待同步意图保护,其余验收不变),
   使无上游安装记录的分发版可正常自动安装。
5. **显示精简(v0.3.1/v0.3.2,overlay.js 补丁)**:用户要求悬浮条只显示速率(常显格)。
   默认显示项全部关闭(含 v0.3.1 新增的缓存命中格,命中率 = 缓存读取 ÷（未命中输入 +
   缓存读取 + 缓存写入）,会话口径)、新增 `cache` 项(⚙ 面板可开回其他格)、
   localStorage 键两度升级(`zusage3.show → zusage4.show → zusage5.show`,丢弃旧存储使
   新默认生效)、精简模式摘要加缓存行。
6. **命令适配**:`/install-overlay`、`/uninstall-overlay` 指向 vendored 路径;
   `/usage` 使用本插件的统计引擎与 MCP 工具。
7. **本插件统计引擎(0.3.3/0.3.4)**:速率口径对齐 vendored `_row_gen_ms`——生成窗口 =
   completed − first_token(思考前置请求用整墙钟),窗口有效域 [200ms, 1h),无效样本
   整行剔除(Σ有效 output ÷ Σ有效窗口);经独立 SQL 交叉验证一致。GLM 思考 token
   未上报(reasoning 恒 0),DB 口径完成态速率低于悬浮条生成中的实时读数,属数据边界。

## 上游同步

上游持续活跃(版本指纹随 ZCode 官方更新而更新)。ZCode 大版本升级后若状态条失联,
优先考虑同步上游新版本并重放上述 3 处默认值补丁。

8. **引擎补丁缺省关(v0.3.5)**:上游 v1.5.10 新增 `engine-patch.cjs`(给 glm/zcode.cjs
   打自动压缩阈值补丁,上游缺省开)。本分发版按最小修改原则改为**缺省关**
   (`auto_compact_patch === true` 才启用),配置守卫同步补该键。
9. **上游基线**:v1.5.10(2026-11-03 同步,支持 ZCode 3.17.2 指纹;上游缺省开/关的
   三个联网项与改名验收补丁、显示精简补丁如上,每次上游同步后重放)。
10. **速度卡精简(v0.3.6)**:上游 v1.5.10 的速率悬停详情卡(speedCardHTML:大数字 +
    十档色带图例 + 口径注脚)体积过大;三处调用点统一传 `legend: false` 并清空
    `foot`,仅保留数字读数与一行统计口径。
