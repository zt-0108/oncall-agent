# 外部故障案例来源与数据边界

本目录保存用于 Fault Lab 冷启动与评测的外部案例目录。外部案例不会写入“人工确认故障记忆”数据库。

## 当前来源

### RCAEval

- Repository: https://github.com/phamquiluan/RCAEval
- Pinned commit: `4695aa69f4f1f57b9094ca04ff235908b73a8e24`
- License: MIT
- Dataset description: https://github.com/phamquiluan/RCAEval#available-datasets
- Citation: RCAEval: A Benchmark for Root Cause Analysis of Microservice Systems

RCAEval 包含 Online Boutique、Sock Shop 和 Train Ticket 等微服务系统的指标、日志与 Trace，并提供 CPU、MEM、DISK、DELAY、LOSS、SOCKET 等故障标签。

## 本项目保存了什么

- `external_cases.json` 保存故障类型、固定来源、许可证、预期证据和本地回放适配信息。
- `aiops-docs/external-benchmark-cases.md` 保存供 RAG 检索的结构化摘要。
- 不在仓库中复制 RCAEval 的数 GB 原始遥测。
- 不把基准标签自动写入人工确认故障记忆。

## 信号含义

- `data_origin=replay_external`：场景设计来自外部基准。
- `data_origin=live_local`：指标与日志由本机 Fault Lab 在回放期间实时产生。
- `confirmed_incident`：仅表示用户对一次实际诊断进行了人工复核，和外部基准完全分开。

外部基准只提供排查线索。Agent 必须通过当前 Prometheus 指标和日志重新验证根因。
