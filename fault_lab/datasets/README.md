# GitHub 历史故障数据入口

此目录预留给后续 REPLAY 数据，不把历史样本标记成实时数据。

优先来源：

1. LogPAI LogHub: https://github.com/logpai/loghub
   - 适合 HDFS、OpenStack、BGL 等真实系统日志回放。
   - 需遵守仓库的数据使用说明并保留论文引用。
2. OpenTelemetry Astronomy Shop: https://github.com/open-telemetry/opentelemetry-demo
   - 适合带负载生成器、指标、日志、Trace 和 Feature Flag 故障的实时复现实验。

每次导入必须附带 `manifest.json`，至少包含：

```json
{
  "data_origin": "replay_github",
  "source_repo": "https://github.com/owner/repo",
  "commit_sha": "固定提交哈希",
  "license": "许可证或数据使用条款",
  "source_files": ["原始文件路径"],
  "sha256": {"本地文件": "校验和"},
  "scenario_id": "唯一场景 ID",
  "ground_truth": "来源提供的异常标签或根因"
}
```

没有来源、许可信息或校验和的数据不得加入诊断证据库。