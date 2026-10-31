---
description: 卸载悬浮用量状态条并写入豁免标记(阻止自动重注入与提示)
---
卸载悬浮 token 用量状态条。

执行:用 Bash 运行(插件根目录 = 本命令文件所在目录的上一级):

    python3 "<插件根目录>/vendor/zusage/overlay_install.py" remove

结果处理:

- 成功:告知用户状态条已卸载,已写入豁免标记(自动安装不再运行、不再提示);**完全退出 ZCode 并重开**后状态条消失。
- 失败:原样转述安装器输出;输出"待收尾/运行中替换失败"时,请用户完全退出 ZCode 后重跑本命令。
- 恢复:之后想再用,运行 `/install-overlay` 并重启 ZCode。

用户附加要求:$ARGUMENTS
