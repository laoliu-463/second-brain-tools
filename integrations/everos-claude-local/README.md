# EverOS 本地 Claude Code 接入

这是 Claude Code 通过 Hook 和 MCP 使用本地 WSL EverOS 的小型接入层。仓库包含运行代码，不包含 EverOS 本体、云端 `everos-kb` MCP、运行日志、会话记录或密钥。

## 组成

- `hooks/recall-everos.py`：提交问题时从 EverOS 检索记忆，并把命中结果注入当前对话。
- `hooks/stop-everos.py`：Claude 回复结束时把最近一轮写入 EverOS，并在后台触发节流后的提取。
- `hooks/flush-everos.py`：会话结束时把缓冲内容交给 EverOS 提取。
- `mcp/server.py`：提供 `everos_search`、`everos_add`、`everos_get` 三个 MCP 工具。
- `examples/`：Claude Code Hook 和 MCP 配置片段。

Hook 会访问 `http://127.0.0.1:8000`；MCP 进程在 WSL 内运行，也通过本机回环地址访问 EverOS。EverOS 应保持只监听本机回环地址。

## 前置条件

1. Windows 上已安装 Claude Code 和 Python 3。
2. WSL Ubuntu 中已安装 EverOS，并运行在 `127.0.0.1:8000`。
3. `EVEROS_USER_ID` 要与现有记忆归属一致；不设置时，示例代码默认使用 `local-user`。

检查 EverOS：

```bash
curl http://127.0.0.1:8000/health
```

## 配置

把仓库克隆到一个固定路径。将 `examples/hooks.settings.json` 中的三个命令合并到 `%USERPROFILE%\.claude\settings.json` 的 `hooks` 字段；将 `examples/mcp-servers.json` 中的服务器项合并到 `%USERPROFILE%\.claude.json` 的 `mcpServers` 字段。合并时保留已有配置，不要覆盖整份文件。

配置片段中的 Windows 用户名和仓库路径是占位符，需改成当前机器上的实际路径。示例按仓库位于 `D:\Repositories\second-brain-tools` 编写；MCP 对应的 WSL 路径是 `/mnt/d/Repositories/second-brain-tools/integrations/everos-claude-local/mcp/server.py`。

设置环境变量 `EVEROS_USER_ID`，使 Windows Hooks 和 WSL MCP 使用同一个记忆归属 ID。Hook 还支持：

- `EVEROS_BASE_URL`，默认 `http://127.0.0.1:8000`
- `EVEROS_APP_ID`，默认 `claude-code`
- `EVEROS_PROJECT_ID`，默认 `claude-code`
- `EVEROS_RECALL_TOP_K`，默认 `5`
- `EVEROS_RECALL_METHOD`，默认 `hybrid`
- `EVEROS_RECALL_TIMEOUT_S`，默认 `5`
- `STOP_FLUSH_THROTTLE`，默认 `90` 秒

配置完成后重新打开 Claude Code。Hook 运行时状态文件和 `flush.log` 保存在 Hook 脚本目录中，已由 `.gitignore` 排除。

## 工作方式与限制

检索 Hook 使用 EverOS 的 `/api/v2/memory/search`。写入和提取使用 `/api/v1/memory/add` 与 `/api/v1/memory/flush`；EverOS 当前将 `/api/v1` 作为 `/api/v2` 的兼容别名。新集成建议逐步切换到 `/api/v2`。

记忆提取依赖 EverOS 配置的 LLM。Hook 的 `flush` 最长等待 60 秒；若模型推理超时，EverOS 服务仍可能健康，但本轮记忆提取会失败或延迟。检查 EverOS 日志和 `hooks/flush.log`。`/health` 中的 Cascade 队列只反映索引任务，不代表 LLM 提取已经成功。

EverOS 默认不带 HTTP 身份验证。不要把服务监听地址改成公网网卡，也不要把 API 直接暴露到不可信网络。
