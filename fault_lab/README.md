# Fault Lab

Fault Lab 是独立于 OnCall Agent 的受控实验服务，监听 `127.0.0.1:9910`。
它只制造可恢复的应用级故障，不会主动占满 CPU、内存、磁盘，也不会修改外部数据。

## 本地完整链路

1. 在 AIOps 页面选择场景并启用。
2. 点击“产生 12 次真实请求”。
3. Fault Lab 实际执行请求、记录指标并写入 `logs/fault-lab.jsonl`。
4. Prometheus 采集 9910 `/metrics` 并按规则产生告警。
5. Log MCP 查询 JSONL 证据，Monitor MCP 查询 PromQL 证据。
6. AIOps Agent 综合告警、日志、指标和 Runbook 输出报告。
7. 点击“恢复正常”关闭故障场景。

## 数据来源

- `live_local`：当前电脑上真实执行的请求、指标和日志。
- `replay_github`：未来从 GitHub 公共数据集导入的历史样本，必须带仓库、提交、许可证和校验和。
- `tencent_cls`：未来真正调用腾讯云 CLS API 时使用；未配置云 API 前不得显示此标识。

场景标准答案位于 `scenarios/catalog.json`，仅用于人工查看和后续评估，不会自动注入 Agent 工具结果。