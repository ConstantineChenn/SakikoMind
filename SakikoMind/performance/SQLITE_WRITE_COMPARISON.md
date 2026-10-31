# SQLite 写入排队与同条件复测

本专项测量真实 `api.aftersales.create_app` → `AfterSalesStore.confirm` → SQLite
确认写入链路。使用隔离的合成订单、身份、确认凭据，不调用模型或支付渠道。
它是新的可复现对照，不能替代或宣称修复了历史“200 并发、14.83% 错误率”。

## 变更与适用范围

`AfterSalesStore` 默认在创建写连接之前取得实例级线程锁；完成提交、回滚与关闭后释放。
读取不经过该锁。原有 `BEGIN IMMEDIATE` 和 SQLite 15 秒超时保留。
排队超过 `write_queue_timeout_s`（默认15秒）返回明确的 HTTP 503 / `write_queue_timeout`，
未取得锁的请求不会开始事务。排队预算与 SQLite 等待外部写锁的预算独立。
调用方可查询业务状态，并按原有幂等键重试。

同一实例内的准入减少线程在 SQLite 内部争抢写锁；它不保证 FIFO，也不协调其他实例或进程。
事务业务边界没有改变，因此本次工作不称为“缩小事务范围”。
多进程仍依赖数据库事务保证原子性；本地对照不能证明生产容量或多实例收益。

## 可复现实验

在 Python 服务根目录，使用 Python 3.12：

```sh
python -m pip install -r performance/requirements.txt
python -X utf8 performance/sqlite_write_comparison.py --output-dir /absolute/path/to/new-results --concurrency 50 100 200 --requests 400 --repeats 3
```

Windows 可将输出路径写为 `C:\\path\\to\\new-results`。目录必须不存在，以避免覆盖旧证据。
脚本只绑定随机 loopback 端口，逐轮启动及关闭独立服务。运行前暂停其他主动测试负载。

设计固定如下：

- 两组使用同一冻结生产源码，唯一处理差异为 `serialize_writes=False`（raw）或 `True`（queued）。
- 数据库来自同一模板的字节副本，每次请求使用独立订单、quote及幂等键；服务器共享一个 SQLite 文件。
- 每组每档400个确认请求；50/100/200客户端并发；服务器固定单进程、200个线程池令牌。
- 每轮先10次串行预热，测量后10次恢复请求，两者不计入正式吞吐与延迟。
- 请求无自动重试、无速率节流；客户端超时35秒，SQLite超时15秒，queued另有15秒排队上限。
- 三轮交替 raw→queued、queued→raw、raw→queued。两组各3600次，合计7200次正式请求。
- 每次确认只创建 `ready` 申请，不执行退款；订单保持 `paid`。成功必须同时存在唯一申请、幂等记录及确认审计。

API suite 为进程内 HTTP；此脚本使用真实 loopback HTTP socket。这两种测试的延迟不能直接比较。
本专项不经过生产 Nginx，不包含模型、Redis、ChromaDB或外部支付。

## 指标与证据

HTTP延迟从取得客户端并发槽开始计时，包含服务器线程调度、写入排队和数据库事务；
客户端提交等待另记 `client_queue_ms`，总完成等待见 `end_to_end_ms`。
SQLite `entry_ms` 包含准入、建立连接、PRAGMA及BEGIN，并非精确的数据库锁等待计数。
同一台机器运行客户端和服务端，因此结果仅适用于声明环境。

输出保留：

- `plan.json`：参数、依赖版本、源码和runner散列、运行顺序与边界。
- `source/` 和冻结runner：实际被测源码；`fixture.json`记录各组数据库初始散列。
- `r*-c*-*/requests.jsonl`、`connections.jsonl`：逐请求与连接计时，不记录认证头和确认token。
- `oracle.json`、隔离SQLite：订单、申请、幂等、确认审计、外键及完整性校验。
- `server.json`、`shutdown.json`和服务日志：进程绑定与回收证据。
- `runs.json`、`comparison.json`、`README.md`：分轮结果和汇总。

汇总的 P95 为“各轮 P95 的中位数”，不是将多轮请求混合后计算的分位数。
应同时读取分轮数据，不从有限次数推导统计显著性、最大容量或生产 SLA。

## CI 的范围

独立 `After-sales API regression` 工作流运行接口冒烟、完整接口回归、
写入准入与状态校验测试、失败诊断验证，并执行4并发/12请求/1轮的小型runner检查。
该小负载只检查脚本、进程管理与状态校验能运行，不设延迟或吞吐容量门槛。
性能比较应在环境明确、没有其他主动负载时单独执行。
