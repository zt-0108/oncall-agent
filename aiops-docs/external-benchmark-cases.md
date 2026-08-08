# 外部故障基准案例（仅作为排查线索）

> 数据边界：以下内容来自公开 RCA 基准的故障类型，经本项目转换为本地 Fault Lab 回放场景。它们不是当前系统证据，也不是本项目已经人工确认的历史事故。诊断时必须使用当前 Prometheus 指标和日志独立验证。

## 来源与许可

- 来源：RCAEval（Root Cause Analysis benchmark）
- 仓库：https://github.com/phamquiluan/RCAEval
- 固定版本：`4695aa69f4f1f57b9094ca04ff235908b73a8e24`
- 许可：MIT
- 基准范围：Online Boutique、Sock Shop、Train Ticket；包含 CPU、MEM、DISK、DELAY、LOSS、SOCKET 等故障类型，以及指标、日志和 Trace 数据。
- 本项目只保存结构化摘要与来源，不复制数 GB 原始遥测。

## 可回放案例

### CPU 资源饱和

- 可能现象：P95 延迟升高，HTTP 仍可能成功。
- 排查线索：查看服务 CPU 压力、请求延迟和是否存在同步计算热点。
- 本地回放根因：checkoutservice CPU 饱和导致请求处理延迟。

### 内存压力

- 可能现象：内存接近上限、对象分配失败、间歇性 HTTP 500。
- 排查线索：查看内存工作集、OOM/MemoryError 日志及错误率。
- 本地回放根因：cartservice 内存资源耗尽。

### 网络延迟

- 可能现象：依赖调用耗时增加、HTTP 504、端到端 P95 上升。
- 排查线索：比较父子调用延迟，检查依赖超时日志。
- 本地回放根因：checkoutservice 到 paymentservice 的网络延迟。

### 网络丢包

- 可能现象：调用间歇失败、HTTP 503、错误率抖动。
- 排查线索：查看错误是否集中于特定依赖，并与延迟型故障区分。
- 本地回放根因：到 recommendationservice 的网络丢包。

### Socket 耗尽

- 可能现象：大量 HTTP 503、连接池获取失败、Too many open files。
- 排查线索：检查连接池、文件描述符和连接释放逻辑。
- 本地回放根因：emailservice 客户端连接池与 Socket 耗尽。

### 磁盘 I/O 饱和

- 可能现象：订单持久化缓慢，接口高延迟但不一定返回错误。
- 排查线索：检查磁盘 I/O 压力、落盘耗时和队列积压。
- 本地回放根因：orderservice 磁盘 I/O 饱和。

## 使用约束

1. 外部基准只能用于冷启动知识和评测，权威性低于当前实时证据与人工确认的故障记忆。
2. Fault Lab 产生的是本机实时模拟指标和日志，来源标记为 `live_local`；场景设计来源标记为 `replay_external`。
3. 若诊断结论经过人工验证，可另行写入故障记忆库；不得自动把基准答案写成已确认经验。
