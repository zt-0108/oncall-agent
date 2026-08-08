# MCP Servers

项目当前提供两个本地 MCP 服务：

- `mcp_servers/cls_server.py`（8003）：Log MCP，查询 Fault Lab 实际写入的 JSONL 运行日志。
- `mcp_servers/monitor_server.py`（8004）：Monitor MCP，通过 Prometheus HTTP API 执行真实 PromQL。

## 数据真实性

| 服务 | backend | data_origin | 说明 |
|---|---|---|---|
| Log MCP | `live_local` | `live_local` | 只读取 `logs/fault-lab.jsonl` 中实际发生的请求日志 |
| Monitor MCP | `prometheus` | `live_local` | 只返回 Prometheus 中存在的时间序列；缺失数据返回 `null` |

当前 8003 端口和 `MCP_CLS_*` 环境变量名称仅为兼容旧配置。它没有连接腾讯云 CLS，也不会返回模拟 Topic 或动态生成日志。
如后续接入腾讯云，应新增明确的 `tencent_cls` backend，并在前端显示 `LIVE · TENCENT CLS`，不能与本地或回放数据混淆。

## 启动

```powershell
.\.venv\Scripts\python.exe mcp_servers\cls_server.py
.\.venv\Scripts\python.exe mcp_servers\monitor_server.py
```

## Log MCP 工具

- `list_log_sources`
- `search_logs`
- `analyze_log_patterns`

## Monitor MCP 工具

- `query_prometheus`
- `query_prometheus_range`
- `query_service_http_summary`