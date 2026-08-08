# 数据库连接失败 Runbook

1. 核验数据库端口、凭证与连接池。
2. 查看 database_connection_refused 日志及 trace_id。
3. 恢复依赖后重试结账请求。
4. 验证 5xx 错误率告警恢复。
