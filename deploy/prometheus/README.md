# Prometheus 本地监控

这套配置采集两个真实目标：Prometheus 自身，以及宿主机 `9900` 端口上的 OnCall Agent `/metrics`。
配置中没有固定为真的测试告警，也不会构造业务指标。

## 指标契约

HTTP 告警规则预期 `/metrics` 至少提供：

- `oncall_agent_http_requests_total{method,path,status}`：HTTP 请求计数器。
- oncall_agent_http_request_duration_seconds_bucket{method,path,le}：HTTP 响应耗时直方图。
- oncall_agent_aiops_diagnoses_total{mode,status}：AIOps 诊断结果计数器。

无论是否提供上述业务指标，Prometheus 都会自动生成 `up{job="oncall-agent"}`：采集成功为 `1`，失败为 `0`。
因此 `/metrics` 尚未实现或服务停止时，`OnCallAgentMetricsUnavailable` 会产生真实的可用性告警。

## 启动

```powershell
docker compose -f prometheus-compose.yml up -d
```

## 验证

- Prometheus UI: http://127.0.0.1:9090
- Target 状态: http://127.0.0.1:9090/targets
- 告警页面: http://127.0.0.1:9090/alerts
- API: http://127.0.0.1:9090/api/v1/alerts

修改配置后，Prometheus 3.13 会每 15 秒自动检查并重新加载有效配置。