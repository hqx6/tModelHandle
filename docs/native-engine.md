# 原生反编译引擎配置 · 2026-10-12

已配置 Ghidra 12.1.4 + Eclipse Temurin JDK 21.0.12.1 + REA 6.4.0。安装在用户目录，不修改系统 Java 或 PATH。

## 安装与连接

- 工具根目录：`C:\Users\tongtu\.local\share\rea-tools`。
- Ghidra：`ghidra\ghidra_12.1.4_PUBLIC`。
- Java：`java\jdk-21.0.12.1+1`。
- REA：`rea-runtime\node_modules\rea-agents`，使用固定 Node 启动路径，不依赖临时 npm 缓存。
- CLI 路径清单：工具根目录下的 `engine.json`。
- Codex 连接：`C:\Users\tongtu\.codex\config.toml` 的 `[mcp_servers.rea]` 与 `.env`。
- 配置备份：`config.toml.rea.backup`（配置前），`config.toml.rea.timeout.backup`（增加超时前）。
- REA 的 `tool_timeout_sec = 600`，保留启动超时 30 秒；没有改变工具审批设置。其他配置经过 TOML 解析后与原始备份语义相等。

Codex 重启后会加载新增 MCP 连接。当前聊天的工具列表没有热更新；CLI 已经可用。配置和重连方式见 [REA 安装文档](https://github.com/morluto/rea/blob/main/docs/installation.md)。工具超时参数依据 [OpenAI 官方 MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。

## 使用

在项目根目录执行：

```powershell
& .\scripts\rea.ps1 doctor --client codex --provider ghidra --format json
& .\scripts\rea.ps1 decompile 'C:\Users\tongtu\Downloads\cesiumlab4_4.0.17\EarthVisLabApps\cesiumlab\4.0.17\cesiumlab4.exe' 0x140019208 --provider ghidra --format json
```

`scripts/rea.ps1` 从工具清单读取引擎路径，仅为本次调用设置环境，结束时恢复调用方原来的 Java/Ghidra 环境。Ghidra GUI 可通过安装目录的 `ghidraRun.bat` 手动打开。

## 验证证据

- `verification/ghidra-doctor.json`：引擎、64 位 JDK、Windows 原生反编译器和路径控制全部通过。
- `verification/rea-doctor.json`：Codex 注册与 Ghidra readiness 通过。
- `verification/rea-mcp-doctor.json`：production stdio MCP 初始化、170 个工具、6 个提示和 `binary_session` 请求通过。
- `verification/ghidra-cesiumlab-entry.json`：实际 Ghidra 12.1.4 反编译 Evidence，ID `ev_cc12f61c95e07971ae3eb883b88f495f240694a5f4661caaee2a8b5faeacb77d`。

测试目标为用户提供的 `cesiumlab4.exe`，PE x86-64，入口 `0x140019208`。结果包含 `entry` 函数伪代码，观察到安全 cookie 初始化以及对 `FUN_140019094` 的调用。该结果仅验证引擎工作，尚未确认模型切片算法。目标未执行、未修改；分析后的 SHA-256 仍为 `d3e93d95d1c306c3255dad8f3927a5c932f3014d57bc43c98d5a5f7a0f012d6d`。

临时分析项目由 REA 生命周期管理。伪代码是反编译器输出，不能当作原始源码。Windows REA Ghidra P0 的适用范围为本地 NTFS 上的原生 x86/x64 PE；更具体的限制保留在 Evidence 内。

## 下载校验

安装包从发布方官方 GitHub 下载，完整 SHA-256 校验通过，下载 ZIP 保留在工具根目录的 `downloads`，分片已清理。

| 官方发布 | 文件 | SHA-256 |
| --- | --- | --- |
| [Ghidra 12.1.4](https://github.com/NationalSecurityAgency/ghidra/releases/tag/Ghidra_12.1.4_build) | `ghidra_12.1.4_PUBLIC_20260921.zip` | `ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db` |
| [Temurin JDK 21](https://github.com/adoptium/temurin21-binaries/releases/tag/jdk-21.0.12.1%2B1) | `OpenJDK21U-jdk_x64_windows_hotspot_21.0.12.1_1.zip` | `f9d6e191ab098c0d416e7d588a24420a8621cd2f4720dab2459b8b7b2d2d8b4e` |
