#!/usr/bin/env node
// MCP 启动器(跨平台):解析可用的 Python(python3→python→py -3,或 ZUSAGE_PYTHON_PATH
// 覆盖,对应清单 userConfig.pythonPath),再 spawn usage_mcp.py 并透传 stdio。
// 背景:Windows 通常没有 python3 命令,macOS 可能没有 python——MCP 命令不能写死,
// 官方插件(jupyter-notebook/typst)同款思路:命令用 node,Python 在启动器内解析。
import { spawnSync, spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const script = path.join(here, "usage_mcp.py");
const override = process.env.ZUSAGE_PYTHON_PATH || "";

const candidates = override
  ? [[override]]
  : process.platform === "win32"
    ? [["python"], ["py", "-3"], ["python3"]]
    : [["python3"], ["python"]];

function works(cmdAndArgs) {
  try {
    const r = spawnSync(cmdAndArgs[0], [...cmdAndArgs.slice(1), "--version"],
                        { timeout: 10000, windowsHide: true });
    return r.status === 0;
  } catch { return false; }
}

const found = candidates.find(works);
if (!found) {
  process.stderr.write(
    "[token-usage-lite] 未找到可用的 Python。请安装 Python 3.8+,或在\n" +
    "插件设置(pythonPath)里填写完整路径;Windows 可用 `py -3`。\n");
  process.exit(1);
}

const child = spawn(found[0], [...found.slice(1), script], {
  stdio: "inherit", windowsHide: true,
  env: { ...process.env, PYTHONUNBUFFERED: "1" },
});
for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => { try { child.kill(sig); } catch {} });
}
child.on("exit", (code, sig) => {
  process.exit(code != null ? code : sig === "SIGINT" ? 130 : 1);
});
