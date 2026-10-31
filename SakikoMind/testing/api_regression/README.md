# 售后接口回归

本套件从 2026-10-27 独立售后验收套件整理而来，保留 AS-01 至 AS-22 的业务断言；默认测试当前仓库的 `api/aftersales.py`，不再默认测试源码快照。新增工作是归入主仓库、分层选择、接入 CI；既有用例不能计为本次新发现的缺陷或新增业务覆盖。

## 范围与结构

- `cases/test_aftersales.py`：业务主链、参数、人工确认、审批边界、幂等和独立 SQL 核对。
- `cases/test_security_extended.py`：所有接口的认证矩阵、用户隔离、自审批拒绝、确认凭证边界；拒绝后核对业务表未变。
- `cases/test_recovery_extended.py`：提交前回滚、提交后丢响应、订单变化、并发重放。
- `cases/support.py`：HTTP 请求、SQL 断言和受控诊断脱敏；`conftest.py` 管理每用例临时数据库、合成身份与源码散列。
- `tools/verify_failure_report.py`：单独运行 4 个有意失败的诊断探针，验证报告和清理，排除在业务通过率之外。

层级是 **进程内 HTTP + 真实临时 SQLite**。请求经过 FastAPI 路由、鉴权、参数校验及业务存储；planner 按用例替换为固定结果。不经过网络网关，不调用真实模型、支付、Redis、Chroma，也不代表生产容量或真实支付验收。

测试依据是项目自定义的 [模拟售后规则](../../docs/aftersales.md)，无需真实密钥。显式注入 store/tokens，避免加载用户 `.env`。每个用例拥有独立数据库目录，关闭客户端及数据库连接后清理；故障注入仅作用于测试实例，没有新增公共接口。

## 本地运行

在本目录使用 Python 3.12 创建独立虚拟环境并安装 `requirements.txt`（`requirements.lock` 约束已验证的传递依赖）；后续命令使用该环境的 Python。从本目录运行：

```sh
python -m pip install -r requirements.txt
python -X utf8 -m pytest -m smoke --junitxml=reports/smoke.xml
python -X utf8 -m pytest --junitxml=reports/api.xml
python -X utf8 tools/verify_failure_report.py
```

`smoke` 覆盖鉴权拒绝、需审批的完整业务链、重复确认与执行，共 4 个参数展开后的用例；完整回归包含 smoke，不应将两轮执行相加为独立覆盖数。

按风险选择：

```sh
python -X utf8 -m pytest -m security
python -X utf8 -m pytest -m recovery
```

从 Python 服务根目录运行也可显式选择配置：

```sh
python -X utf8 -m pytest -c testing/api_regression/pytest.ini testing/api_regression/cases --junitxml=testing/api_regression/reports/api.xml
```

仅比较其他源码时使用 `--sakiko-source=绝对服务路径`（也支持 `SAKIKOMIND_SOURCE`）。默认路径始终指向当前仓库；报告记录实际源码 SHA256，不把外部快照结果套用到当前版本。不要在同一 pytest 进程混测多个源码版本。

## 持续集成与质量结论

仓库 `.github/workflows/api-tests.yml` 中的 `api-regression` job 会安装轻量依赖、运行 smoke 和完整回归、检查失败诊断并归档 XML。提交、PR 中相关源码、测试或依赖变更会触发，也支持手动运行；任何业务失败或诊断校验失败均使 job 失败。它与后端 offline 测试、前端 browser 测试分别报告。

该 job 还在 Python 服务根目录显式执行 `tests/test_aftersales_write_admission.py` 与 `tests/test_sqlite_write_comparison.py`，检查写入准入、异常清理及性能报告的业务状态判定；不会自动收集其它主应用测试。随后用 4 并发、每组 12 个测量请求、1 轮运行本地 HTTP 对照 runner，输出到 runner 临时目录并归档。这一步只验证 runner 可运行、数据校验和恢复流程，不用于比较容量、声称性能提升或复现历史错误率。详细协议见 [SQLite 对照说明](../../performance/SQLITE_WRITE_COMPARISON.md)。

JUnit XML 包含 Python、SQLite、依赖版本、被测源码及测试文件 SHA256。失败信息包含受控请求/响应、预期、实际及 trace ID；直接业务异常保留脱敏调用栈。脱敏仅保证经过本套件 helper 的受控合成测试输入，不能将真实令牌或个人数据放入测试。

`failure-probe-v2.xml` 中的 4 个失败是工具验证，不能作为业务失败或产品缺陷计数。正常接口报告为 `api.xml`，冒烟报告为 `smoke.xml`，写入准入和性能判定测试报告为 `write-admission.xml`。报告保留 14 天，长期复核请按运行 ID 单独保存。

此配置本身不代表 GitHub 已运行，也不配置分支保护。只有取得对应提交的远端 Actions 结果，才能宣称远端通过；将 `After-sales API regression` 设为合并必需检查，还需仓库规则设置。未经真实执行不得宣称合并门禁已经启用。
