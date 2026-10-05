# Northstar Quant

本地 A 股量化研究、模拟与人工执行系统。按 [设计 v1](DESIGN.md) 实现，目前交付 **v0.1 离线工程版本**。可以运行合成数据闭环；完整真实数据研究、真实行情连续模拟、公司行动的引擎一致性和实际券商验收尚未完成。具体状态见 [实施与验收](docs/IMPLEMENTATION_STATUS.md)。

覆盖境内股票 ETF 与沪深主板普通股票，两条策略共享账户资金。没有自动实盘接口。manual 配置的费率、个人风险、券商映射及策略批准未确认时，无法导出可执行清单。

## 最短启动

在仓库根目录运行；需要 `uv`，Python 3.11 由 uv 管理。Mac ARM 已实测，Linux CI 使用同一锁文件。

```sh
uv sync --frozen
uv run ashare demo --scenario complete
```

打开 `runtime/demo/report/latest.html`，研究结果在 `runtime/demo/runs/`，完整闭环证据在 `runtime/demo/demo-evidence.json`。合成价格、日历和证券名都是工程样本，不能证明投资效果。demo 包括：

- CSV → 质量检查 → 内容哈希 Parquet 快照 → 两条策略 → 聚合规划 → RQAlpha 回测与逐日独立账本比较。
- 跨日模拟、重复日运行、两次部分成交、跨文件重复导入、账户核对、SQLite Backup API 备份及恢复重放。
- 本地 HTML/JSON/CSV 报告、净资产 PNG/SVG 图、明确的日期与阻断状态。

完整自动检查：

```sh
uv run python tools/verify_delivery.py
```

固定压力情景与基准：从 demo 输出复制真实 `snapshot_id`，以下参数是示例，不做参数优化。

```sh
uv run ashare research suite --config configs/demo.yaml \
  --snapshot SNAPSHOT_ID --start 2025-01-30 --end 2025-05-06
```

套件包括成本 2/3 倍、延后一天、10/30 万本金、10 bp 不利价格，以及 ETF、股票单策略和被动/等权基准。报告区分情景费率、日频成交代理与实际成交。

## 接入自己的数据

[数据字典](docs/DATA_DICTIONARY.md) 说明 `instruments.csv`、`calendar.csv`、`bars.csv`、`actions.csv` 四张规范表。`fixtures/synthetic/` 提供可运行样例；每个交易日必须有明确状态，停牌不能靠缺行猜测。日历包含休市日，两家交易所分别记录。规范数据以元、股/份、带时区时间表示。

```sh
uv run ashare data sync --config configs/research.yaml --directory /absolute/canonical-csv
uv run ashare data validate --batch BATCH_ID --root runtime/research
uv run ashare snapshot create --batch BATCH_ID --root runtime/research
uv run ashare backtest --config configs/research.yaml --snapshot SNAPSHOT_ID \
  --start YYYY-MM-DD --end YYYY-MM-DD --strategy combined --split development
```

真实数据投资用途的引擎许可适用性确认后，才可开启配置 `personal_investment_engine_license_confirmed`。此标志不代替数据来源、策略或券商验收。

Tushare 有权限探测、显式原始接口拉取、分页缓存/重试和单位标准化；**自动拼接完整历史证券状态及公司行动尚未实现**，`provider: tushare` 的自动 sync 明确返回 Unsupported。没有 token 的探测为 NOT_TESTED，不会自动改用另一供应商。

```sh
uv sync --frozen --extra tushare
# 在本地环境配置 TUSHARE_TOKEN，不写进仓库或命令参数文件。
uv run ashare data probe --provider tushare
uv run ashare data fetch --dataset daily --parameters /absolute/request.json
# --refresh 建立新的原始批次；默认重试复用已经保存的分页。
```

`request.json` 仅包含诸如 `{"ts_code":"600000.SH","start_date":"20250101","end_date":"20250131"}` 的查询条件。探测小样本不代表权限、分页和历史全覆盖已验收。当前 `historical_full` 发布被阻断，不能拿抽样数据宣称全市场研究。

## 每天的入口

在 `configs/paper.yaml` 设置真实规范 CSV 的 `csv_directory`、真实首次观测时间与许可确认。daily 自动调用同一 CSV 导入/校验代码；也可以显式指定已冻结的 `--snapshot`。新数据不足时输出 WAITING_DATA。

```sh
uv run ashare daily run --account paper --date YYYY-MM-DD
uv run ashare status --account paper
```

`paper step` 共用 daily 编排。paper/manual 只允许当天的前瞻日常任务，交易日盘后 21:30（上海时区）开始；历史回放用 research/demo。每日结算与核对先于未来目标。过去输入被修订、漏掉交易日或重放事件前缀不一致会阻断；已发布的事件不会覆盖。持久暂停与对账、数据状态分开显示。

原 [每日操作规格](docs/DAILY_RUNBOOK.md) 保留不改动；本版本实际命令及恢复流程见 [运行手册](docs/OPERATIONS.md)。

## 人工账户流程

先填写 `configs/manual.yaml` 的实际费率、风险预算及批准字段，并按 [导入字典](docs/DATA_DICTIONARY.md) 准备真实映射。真实券商格式还没有样例，不声称已支持某家券商。通用 CSV 需显式写 `account_id=manual`、`mode=manual`。

```sh
uv run ashare account init --account manual --cash ACTUAL_CASH \
  --asof YYYY-MM-DDTHH:MM:SS+08:00 --positions /absolute/initial-lots.json
uv run ashare import preview --account manual --kind account_snapshot \
  --mapping configs/brokers/manual_csv.yaml --file /absolute/account.csv
uv run ashare import apply --account manual --batch BATCH_ID
uv run ashare reconcile --account manual --date YYYY-MM-DD
uv run ashare daily run --account manual --date YYYY-MM-DD --snapshot SNAPSHOT_ID
uv run ashare plan preflight --account manual --plan PLAN_ID --confirmation /absolute/live-check.json
uv run ashare plan export --account manual --plan PLAN_ID
uv run ashare order register --account manual --intent INTENT_ID --broker-order-id BROKER_ID
```

预检确认格式见 `configs/live-check.example.json`，必须核对券商最新价格、涨跌停、状态、现金及可卖数量，时间在执行窗口内。清单输出 JSON/CSV/HTML。导出为 EXPORTED，**不代表已报单**；不确定是否提交时登记 `--state UNKNOWN`，先核查，不能重发。导入支持 orders、fills、cash_movements、account_snapshot；每批 preview 后 apply，关键冲突整批回滚。

## 实现与边界

代码位于 `src/ashare/`：供应商/策略/引擎/执行适配，稳定契约，快照，订单规划，事件账本，风控，实验登记及日常运维。外部 JSON Schema 在 `schemas/`，SQLite 初始迁移在 `migrations/0001.sql`。

`runtime/`、账户原件、token 和 `.env` 不进入 Git；不同模式使用独立数据库。持仓批次、冻结资金、费用、分红应收/到账和送转解锁有手算测试；但公司行动、持有期红利税及退市的 RQAlpha 端到端适配仍未通过，相关回测为 INVALID，业绩不发布。PIT 财务、封存集的 OS 隔离、复杂冲正和历史代码变更亦未完成。

[引擎 ADR](docs/adr/0001-engine-and-data.md) · [实施状态](docs/IMPLEMENTATION_STATUS.md) · [运行手册](docs/OPERATIONS.md) · [数据字典](docs/DATA_DICTIONARY.md) · [原始设计包说明](docs/design-package-README.md)
