# A 股个人量化最小系统实现计划

初始调研日期：2026-10-04；首版设计于 2026-10-05 定稿。面向专业 Python 开发者，资金背景为 10–30 万元，非高频。本文件是待执行的工程规格；尚未安装交易框架、下载完整行情、运行策略或连接券商。执行入口为 [DESIGN.md](../DESIGN.md)，日常行为按 [每日操作手册](DAILY_RUNBOOK.md) 验收。

建议搭建一个本地模块化单体：**同一份策略和组合逻辑，连接版本化数据、成熟事件回测引擎、模拟账户及人工成交导入；订单、风险、账本和对账形成完整闭环。** 首版同时支持境内股票 ETF 和沪深主板普通 A 股。扩展性主要来自稳定的数据和业务契约，而不是提前部署微服务。

你已确认：ETF 和个股首版都要；先生成交易清单、人工执行并导入成交；harness 继续比较后决定。默认研究频率采用日线、月度调仓。下文仓位、窗口与风险阈值均为 paper 演示配置，不视为你已接受的投资方案。

阅读顺序：先看 1–3 节了解范围与架构，再看 9–11 节的人工执行与风险；执行 agent 按 [任务包](AGENT_TASKS.md) 开工。具体 Python 文件、持续模拟盘和可替换方案见 [模块与备选](MODULES_AND_ALTERNATIVES.md)，详细选型证据见 [补充调研](RESEARCH_DECISIONS.md)。

## 1 首版完成后的使用体验

在无数据账号、无券商账号的干净环境中，一条 demo 命令应能用明确标为合成的样本，走完：

**导入数据 → 质量检查 → 冻结快照 → ETF 与个股信号 → 合并目标 → 历史回测 → 风控 → 交易清单 → 模拟部分成交 → 导入 → 对账 → 日报 → 备份恢复。**

配置 Tushare 权限后，切换到真实历史数据，流程和策略代码不变。实际人工交易前，另完成券商费率、账户初始状态、风险预算及程序化交易报告要求的核实。没有这些信息仍可完成 demo、历史研究和持续模拟。

首版必须交付四种明确区分的运行模式：

| 模式 | 数据与执行 | 能证明什么 |
| --- | --- | --- |
| demo | 合成数据及故障场景，隔离的模拟账户 | 工程流程和已定义的边界案例可以运行 |
| research | 冻结的真实历史数据，按历史日期回测 | 历史研究可复现 |
| paper | 随现实日期取得新数据，虚拟资金与连续模拟账户 | 未来数据到来时流程能持续运行；不等于真实成交验证 |
| manual | 实际账户快照、人工报单、券商回报导入 | 真实持仓与现金可跟踪、可对账；不代表策略获利 |

验收中不能以 demo 代替真实数据验收，也不能以回测结束代替人工执行闭环。首版 paper 是本地日线模拟，不要求券商账号；按已冻结计划逐日模拟结算。状态恢复和历史事件不改写的要求见 [模拟盘设计](MODULES_AND_ALTERNATIVES.md)。

## 2 首版范围与明确延后项

| 环节 | 首版必须实现 | 后续扩展 |
| --- | --- | --- |
| 标的 | 境内股票 ETF；沪深主板普通 A 股，买入时排除 ST、退市整理及上市不足 252 个交易日者 | 创业板、科创板、北交所；跨境、债券、商品 ETF；可转债 |
| 数据 | 日线、交易日历、证券主表及代码映射、历史存续与 ST/停复牌、涨跌停价、复权因子、现金分红与送转 | PIT 财报、历史行业/指数权重、分钟及盘口 |
| 研究 | ETF 趋势、个股低波动示例、基准、时间切分、全部实验记录 | 多因子、ML、自动搜索 |
| 组合 | 单账户、两个策略额度、标的聚合、整手与费用后的现金约束 | 多账户、复杂优化、风险模型 |
| 回测 | RQAlpha 适配、显式信号延迟、真实单位、费用及公司行动、受限成交、独立审计账本 | 其他引擎、分钟撮合 |
| 执行 | 交易计划、人工下单登记、CSV 导入、未成交和部分成交、计划外交易 | MiniQMT / xtquant、vn.py gateway |
| 风控 | 数据、组合、订单、运行四层检查；暂停与恢复流程 | 更细的穿透风险、压力情景 |
| 运营 | 定时任务、单写者锁、结构化日志、异常列表、每日 HTML、备份恢复 | 通知服务、Web 控制台、多机部署 |
| harness | 配置驱动任务与标准实验产物，预留候选策略输入接口 | 比较后接 Qlib、RD-Agent 或其他 agent |

**“不允许新买入”与“可以忽略已有持仓”完全不同。** 持仓之后被 ST、停牌或退市，仍必须进入估值、风险、账本及报告，不能从历史数据里删除。未支持的复杂公司行动会使相关研究运行失效并保留诊断；不能静默跳过后给出漂亮收益。

ETF 首版只交易二级市场份额，不做申赎套利。代码前缀不能代替资产分类；范围白名单和日期有效的交易规则共同决定支持能力。

## 3 技术选型和依赖方向

建议栈：Python 3.11 起步验证、pandas / NumPy、PyArrow / Parquet、DuckDB、SQLite、Pydantic、Click、Jinja2、pytest，以及隔离在适配层的 RQAlpha。实施时锁定实际跑通的依赖版本，不按“最新版本”浮动安装；Qlib / RD-Agent 后续使用独立环境。

| 部件 | 建议 | 原因与验收边界 |
| --- | --- | --- |
| 数据商 | Tushare Pro 为首个适配器，RQData 为有预算时的替代候选 | Tushare 接口与权限可逐项核查；RQData 的 PIT 与 RQAlpha 配套值得询价。二者均未在你的账号实测 |
| 历史分析存储 | 不可变 Parquet + 每个研究进程自己的 DuckDB 查询连接 | 容易审阅和迁移；不把共享可写 DuckDB 当交易数据库 |
| 状态存储 | 本地 SQLite，WAL，单个逻辑写者，版本化 SQL migration | 账户、订单与事件必须事务提交；多机或多写者出现后再迁移 PostgreSQL |
| 回测 | 优先 RQAlpha，先做 2–3 人日集成试验 | 复用 A 股账户与撮合模块；自有数据不能只替换 OHLC 就声称完整接通 |
| 执行 | ManualCsvAdapter + PaperAdapter | 符合你已选方式，不依赖 Windows 或券商 SDK |
| 确定性工作流 | 简单 CLI 顺序执行 + SQLite 运行登记 + 文件 manifest | 首版没有 ML 训练 DAG，不必引入大型调度器；这只是系统编排胶水 |
| 报告 | 静态 HTML + CSV / JSON | 可离线阅读、可 diff、可被其他 agent 读取 |
| 开发与运行 | 当前 Mac；长期任务后续可放 Linux | 数据和运行目录放本地磁盘，避免直接在同步盘/NAS 上操作账本 |

推荐的调用链：

~~~text
Tushare / RQData / CSV
         ↓ DataProvider
Raw → 标准化与质量门禁 → Snapshot + Parquet
                               ↓ MarketDataView
                       Strategy → TargetPortfolio
                                      ↓
                          Allocator → Risk → OrderIntent
                                      ↓
                 ┌────────────────────┼────────────────────┐
                 ↓                    ↓                    ↓
           RQAlphaAdapter         PaperAdapter        ManualCsvAdapter
                 └────────────────────┼────────────────────┘
                                      ↓
                           ExecutionEvent → Ledger
                                      ↓
                          Reconcile → Report → Backup

Harness → ExperimentSpec / CandidateArtifact → 上述评估流程
~~~

策略不能 import Tushare、RQAlpha、xtquant 或 sqlite3，也不能直接读写交易账本。引擎 API 仅出现在引擎适配器中。策略读数据、输出目标权重；风险和执行决定目标能否实现。

上述模块边界借鉴 RQAlpha 的可替换数据源及 Mod、vn.py 的订单/成交与 gateway 分离、Qlib 的配置和实验产物管理。具体的单机存储组合与人工工作流是本项目设计，并不是某个开源项目提供的现成一键方案。[RQAlpha 扩展接口](https://rqalpha.readthedocs.io/zh-cn/latest/development/data_source.html) · [vn.py](https://github.com/vnpy/vnpy) · [Qlib workflow](https://qlib.readthedocs.io/en/latest/component/workflow.html)

## 4 模块边界和稳定契约

建议用 Python Protocol 描述边界，用 Pydantic 校验跨模块载荷，用 JSON Schema 导出外部契约。内部可用 DataFrame/Arrow，但约定主键、类型、时区、单位和排序；不得只传无规格的任意 DataFrame。

| 契约 | 最小输入和输出 | 必须保证 |
| --- | --- | --- |
| DataProvider | fetch(dataset, instruments, start, end, cursor) → RawBatch | 保留源字段、分页依据、请求参数、抓取时间、内容哈希；无静默换源 |
| MarketDataView | bars / instruments / status / actions，绑定 snapshot 与 decision_at | 信号侧只返回当时允许见到的数据；禁止绕过时点过滤 |
| Strategy | generate(context, data_view) → TargetPortfolio | 同输入同输出；输出目标而非订单，不持有券商状态 |
| Allocator | 各策略目标 + 配额 + 已核对账户 → 聚合目标 | 同标的先合并再下单，账户总额度不因两个策略而重复使用 |
| Planner | 聚合目标 + PositionView + RuleSet + FeeSchedule → OrderIntent 列表 | 只做差额、考虑冻结和未完成计划、整手、费用和价格上限 |
| RiskPolicy | evaluate(candidate, context) → PASS / BLOCK / REVIEW + reasons | 不通过时不可导出可执行单；保留修改前后值及原因 |
| BacktestEngine | ExperimentSpec + snapshot + 策略制品 → 标准运行产物 | 引擎名称、版本和成交假设进入 run manifest |
| ExecutionAdapter | publish / import_events / reconcile_source | publish 的语义由能力声明决定；人工 export 不等于已报单 |
| LedgerStore | append_events + 原子更新投影 | 重复事件不重复入账，可从事件重建账户 |
| ResearchHarness | propose / evaluate → CandidateArtifact / RunResult | 使用公共评估器，不能改费率、风控、数据快照或偷看封存集 |

关键业务对象必须包含：

- Instrument：内部稳定 instrument_id、交易所 MIC、证券类型、币种；代码通过带有效期的映射表关联 vendor symbol 和 broker symbol，不仅保存六位字符串。
- Snapshot：snapshot_id、schema_version、每个表的文件清单与哈希、数据区间、供应商、转换版本、质量报告、时间语义。
- TargetPortfolio：strategy_id/version、decision_at、trade_not_before、expires_at、snapshot_id、权重和原因、建议现金比例；非调仓日返回 NO_REBALANCE，不能把空列表误作清仓。
- OrderIntent：intent_id、plan_id、账户、标的、方向、数量、限价保护、有效日、依赖条件、费用估计、risk_result_id。
- ExecutionEvent：event_id、账户、券商委托号/成交号、事件时间与接收时间、事件类型、数量、价格、费用、来源批次、关联 intent；未知关联不得自动猜测。
- AccountView：现金总额、可用现金、冻结现金、可取现金（如源提供）、总持仓、可卖数量、冻结数量、应收股利、数据截止时间与核对状态。
- ExperimentSpec：策略、数据快照、时间切分、本金、基准、规则与费率版本、撮合/滑点配置、seed、资源预算。

金额计算使用 Decimal；持久化采用明确缩放的整数或十进制字符串，禁止依靠 SQLite REAL 做账户金额。内部计算可保留至百万分之一元，现金记账按券商分币规则舍入；价格另按股票/ETF tick 校验。数量是股/份的整数；行情成交量保留源精度后验证转换误差。

所有业务时间用带时区的 timestamp；交易日用 Asia/Shanghai 日历，数据库可存 UTC 并保留 exchange_date。不能把周一至周五直接当交易日。

配置和事件都带 schema_version。兼容升级只新增可选字段；删字段或变单位必须升大版本并提供迁移。读取未知大版本直接报错，不能猜。

## 5 数据库与数据来源

### 5.1 三层数据与目录

~~~text
runtime/                         # 示意名；实际路径通过配置指定，与源码分开，不提交 Git
  raw/{provider}/{dataset}/{batch_id}/
    response.*  request.json     # 原始响应与脱敏请求
  lake/{dataset}/year=YYYY/part-{content_hash}.parquet
  snapshots/{snapshot_id}/
    manifest.json  quality.json
  state/research.sqlite
  state/paper.sqlite
  state/manual.sqlite
  runs/{run_id}/
    manifest.json targets.parquet orders.parquet fills.parquet
    ledger.parquet equity.parquet metrics.json report.html logs.jsonl
  plans/{account}/{plan_id}/
    plan.json plan.csv plan.html
  imports/{account}/{batch_id}/
    original.csv mapping.json validation.json
  backups/{date}/
~~~

raw 只追加。清洗产生新文件；修订数据产生新快照，历史 run 固定引用旧快照。manifest 是文件清单，不得用 lake 下所有 parquet 的通配符查询，否则新旧版本会重复进入回测。先写临时文件、校验、原子发布 manifest，读者只能看到完整快照。

快照只引用变化后的分区，不复制整个历史库。日线按数据集和年份分区，必要时再按月份或稳定桶拆分；不为每只股票每天建小文件。十年全市场日线是千万级行量估算，首版用单机列式存储合理；真实容量与耗时在 T02 基准测试记录。

DuckDB 以进程内分析为主。每个研究进程直接查询同一组只读 Parquet，拥有自己的内存/临时 catalog；不让 ingestion 和多个 agent 并发写一个 .duckdb 文件。SQLite 由一个服务/持锁 CLI 串行提交账户变更。WAL 不能放在网络文件系统上。[DuckDB 并发说明](https://duckdb.org/docs/current/connect/concurrency) · [SQLite WAL](https://www.sqlite.org/wal.html)

### 5.2 标准数据集

| 表 | 主键或唯一约束 | 核心字段与语义 |
| --- | --- | --- |
| instrument_versions | instrument_id, valid_from, revision_id | 名称、资产类别、市场/板块、上市/退市日、tick、lot、结算类型、有效区间及来源 |
| symbol_map | provider, symbol, valid_from | 内部 ID 与外部代码映射；代码变更不能新建一份重复持仓 |
| calendar | exchange, trade_date | is_open、前后交易日、session、临时变更 |
| bars_daily | instrument_id, trade_date，在快照内唯一 | 未复权 OHLC、前收参考值、股/份成交量、人民币成交额、状态 |
| adjustment_factors | instrument_id, trade_date | 股票和基金各自复权因子、算法/供应商版本；仅供研究价格构造 |
| trading_status | instrument_id, effective_date, revision_id | listed、ST、suspended、delisting_period、涨跌停价、状态的已知时间 |
| corporate_actions | action_id, revision_id | 公告/实施/登记/除权/到账/新增股份可卖日期、每股现金与送转比例、方案状态 |
| universe_membership | universe_id, instrument_id, valid_from, revision_id | 历史可投资范围及选入原因；每个决策日可重建 |
| provenance | dataset, batch_id | 源接口、权限检查、抓取时间、内容哈希、转换版本、覆盖与失败记录 |

财务扩展表预留为 financial_observations：instrument_id、report_period、field、value、announced_at、revision_id、available_at。首版不下载财报、不计算质量/价值因子，避免把字段预留误做完整功能。

SQLite 至少包括 schema_migrations、accounts、runs、plans、intents、order_events、fills、cash_events、corporate_action_events、position_lots、positions、cash_balances、risk_decisions、reconciliation_runs、reconciliation_items、import_batches、job_runs、artifacts。事件和相关投影须在同一事务内更新；所有账户表都带 account_id，研究和实际账户另有物理数据库隔离。

### 5.3 Tushare 首版采购和拉取清单

| 数据 | 接口候选 | 已核实的文档事实及需验收点 |
| --- | --- | --- |
| 日历、证券主表 | trade_cal、stock_basic、fund_basic / etf_basic | 股票需获取上市、退市及暂停上市等记录；ETF 分类与终止上市覆盖需样本确认 |
| 股票行情 | daily、adj_factor | 分页、停牌无 bar、历史覆盖；不把缺 bar 自动填成交易日 |
| ETF 行情 | fund_daily、fund_adj | fund_daily 文档要求至少 5,000 积分，vol 为手、amount 为千元；fund_adj 是独立基金因子 |
| 历史股票与 ST | bak_basic、stock_st，必要时 namechange 核对 | bak_basic 文档从 2016 年起；stock_st 支持按日期的历史列表，但早期不齐，且当日约 09:20 更新 |
| 交易限制 | suspend_d、stk_limit、etf_limit | 每日证券限制；etf_limit 文档要求 2,000 积分，历史起点未保证，必须探测 |
| 公司行动 | dividend、fund_div | 只采用实施方案，去重修订；税前现金值映射 cash_div_tax，不与税后字段混淆；拆分除息/到账/股份可卖日期 |

依据：[股票日线](https://tushare.pro/document/2?doc_id=27)、[ETF 日线](https://tushare.pro/document/2?doc_id=127)、[基金因子](https://tushare.pro/document/2?doc_id=199)、[历史股票列表](https://tushare.pro/document/2?doc_id=262)、[ST 列表](https://tushare.pro/document/2?doc_id=397)、[ETF 涨跌停](https://tushare.pro/document/2?doc_id=491)、[股票分红](https://tushare.pro/document/2?doc_id=103)、[基金分红](https://tushare.pro/document/2?doc_id=120)。

这些接口名是实施调查清单，不是“已买到完整数据”的承诺。公共权限表列出 5,000 积分档约 500 元/年，且有独立付费接口；以账号实际权限和样本为准。首版不因预算有几千元就直接买全量分钟数据。[权限表](https://tushare.pro/document/2?doc_id=290)

先测 2 只 ETF + 20 只股票 + 一只历史退市股票 + 有分红送转的区间，覆盖两家交易所。再补齐所选研究期的历史主板全体证券，才能运行“从全市场筛选”的股票策略；不能拿抽样股票的结果冒充全市场回测。股票演示正式研究期建议从 2017 年开始，使用 2016 年作预热，并以状态/公司行动数据完整度决定最终起点。

换源时建立 dataset-level 路由配置。必须保留供应商身份，做重叠区间对比并生成新 snapshot。禁止 API 失败时悄悄用 AKShare 补几行原始价格而继续沿用旧因子。RQData 可降低部分历史状态与财务工作的成本，但报价和授权需实际询问；其 PIT API 的 date 和 statements 参数仍要按实验时点使用。[RQData PIT](https://www.ricequant.com/doc/rqdata/python/stock-mod.html)

### 5.4 时间语义与质量门禁

区分 event_time（发生）、effective_time（生效）、published_at（公告）、observed_at（本系统首次取得）、available_at（评估器允许策略看见）。保留 available_at 的依据：源时间戳、保守规则或真实首次观测。

历史回补的数据在 2026 年抓取，不代表 2017 年的策略都不可见，也不代表供应商能证明当时的原始版本。研究模式可以声明“按公开日推定可用”的历史假设；严格 paper 模式以实际到达时间控制。**冻结快照保证可复现，不能单独证明不存在历史修订或未来信息。** 财务、历史分类等缺少旧版本时，要降低证据等级。

日线策略只读 D 日完成并验收后的数据；若没有明确发布时刻，盘后统一延迟到配置的决策时刻，如 21:30，且检测实际齐备情况。Tushare 分红接口文档写明 20–21 点更新，任务不能仅因日线已到齐就宣布全部数据可用。当日 ST 列表 09:20 更新不能在回测里假定 08:30 已知；盘前的 draft 和开盘后的最终检查必须区别。完整 bar 最早供下一交易日使用。

发布快照前检查：

1. 分页完整、请求区间完整、主键无冲突、有效证券与日历覆盖有统计；接口返回满页不能视为全部下载完毕。
2. OHLC 非负且区间合法；单位转换和成交额/成交量均价量级合理；字段改名、异常 NaN 直接失败。
3. 缺失分成休市、未上市、停牌、退市、源缺漏；只有估值允许带标记沿用旧价，信号窗口和撮合不得默默前向填充。
4. 因子跳变应能关联公司行动；涨跌停参考价不能无条件等于昨日未复权收盘价。
5. 关键状态缺失时不输出“可执行”计划；数据不齐只能产生带阻断原因的报告。
6. 同源重拉差异、跨源小样本差异都保留；source 优先级不可由策略自由改变。

## 6 策略和组合的首版规格

这两条是验证系统的研究假设，不是已经证实的赚钱策略。窗口和权重在第一次实验前登记；不要让执行 agent 自动搜索“最高 Sharpe”再把参数当默认值。

### 6.1 ETF 趋势示例

预先登记 3–5 只境内宽基股票 ETF 的白名单、类别与选定日期。只在其上市后且至少有 121 个有效收盘观察时使用。白名单按今天选出会有选择偏差，因此历史结果标为“固定清单的条件实验”，不声称复现当时全 ETF 市场选基效果。

每月最后交易日盘后，用截至 D 的复权研究价格计算 120 个交易日总回报代理。正回报者按回报排序，取前 2 名；每只最多分配账户 20%，不足两只时余款留现金，全部不满足则该策略目标为现金。并列按内部 ID 稳定排序。

复权序列的收益定义和供应商算法要在 manifest 中注明；这只是趋势信号，真实账户仍用未复权价格和公司行动记账。趋势研究背景见 [Time Series Momentum](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum)，其研究对象并不直接验证本方案的 A 股 ETF 参数。

### 6.2 个股低波动示例

每月最后交易日盘后，从当日已上市至少 252 个交易日、非 ST、非退市整理的沪深主板普通 A 股中选候选，保留后来退市的证券历史。使用过去 20 个交易日人民币成交额中位数筛前 300 只，再以过去 60 个有效日收益标准差从低到高选 5 只，账户权重每只最多 8%，合计最多 40%。

日收益用经过验证的复权研究序列；60 个收益至少需要 61 个价格。首版要求对应窗口覆盖所有预期交易日；停牌/缺数据导致不足者不新买入。并列按 ID 排序。不使用当前总市值、当前行业或当下指数成分去筛历史。20/60/252/300/5 均为待检验规格。

低风险效应仅提供研究动机，例如 [Betting Against Beta](https://www.aqr.com/Insights/Research/Journal-Article/Betting-Against-Beta)；低 beta、低波动和本项目长仓五股组合不是同一个策略。五只股票的集中风险会明确显示在报告里。

### 6.3 基准和账户合并

至少输出全现金、同一 ETF 清单固定权重、个股相同流动性股票池的等权研究组合，以及有可用数据的宽基收益参照。指数价格指数与含股息策略的口径不一致须注明；不要用一个忽略分红的指数画出“超额”。

ETF/股票各 40% 是 paper 示例，预留 20% 现金。两者在同一账户聚合、统一风控与下单；分别记录策略归因，但实际账户账本只有一套。首版不做行业硬约束，避免当前行业污染历史；输出可解释的行业集中度需先有历史分类。ETF 与个股可能重叠，未做持仓穿透时报告“穿透暴露未知”，不能宣称充分分散。

Planner 计算目标数量时使用决策时刻已知参考价，向下取整到买入单位，再按限价保护和费用重新检查现金。买不起一手就记录跳过与目标偏差，不能假设可以买碎股。可卖零股按证券规则处理。

非调仓日继续更新估值、检查风控、导入及对账。目标没有变化不等于系统当天可以不运行。

## 7 回测设计和必须验证的引擎行为

### 7.1 首版明确使用延迟到下一交易日的收盘代理

为配合日线数据和人工执行，首版研究主模型采用：D 日盘后形成不可变目标和数量草案，最早在 D+1 的日终事件提交预先生成的订单，以 D+1 收盘附近的受限成交代理撮合。人工示例执行窗口为 D+1 14:50–14:56，使用限价；这需要你届时有空，窗口可在上线前改为已登记的执行政策。

**日收盘价不等于 14:50 的可成交价。** 报告必须显示“日频成交代理”标签，并给出延后一个交易日、成本 2 倍/3 倍、价格不利偏移等压力结果。人工实盘记录真实执行时刻与价格，累计偏差后校准；若优势依赖几 bp 或窗口内精确成交，升级所选标的/调仓日分钟数据后再评价，不把日频结果当精确模拟。

不使用 D+1 的收盘价重新挑股、排名或增加数量。D+1 的已知状态可以阻止交易；执行日 bar 的价格和成交量仅供模拟市场判断能否成交，不能反向送入 D 日决策。策略数据视图与撮合数据视图物理隔离。

RQAlpha 当前源码会在 1d 模式把 next_bar 变为 current_bar。本设计通过“D 的计划存储，D+1 的执行回调加载”实现交易日延迟，不能只设置 next_bar。增加突变价格 fixture，断言最早成交日期严格晚于 decision_date。引擎若提前泄露 D+1 bar，验收失败。[已核对源码](https://github.com/ricequant/rqalpha/blob/0d98adefa87956e26f3e7ca5b26b5f3fc7ca834f/rqalpha/mod/rqalpha_mod_sys_simulation/mod.py#L60)

### 7.2 现金和换仓采取保守的首版政策

买入预算只用执行日前已核对的可用现金，扣掉冻结、费用和现金预留。首版不复用当天卖出产生的现金；卖出完成并在日终对账后，最早下一交易日生成剩余买入单。因此大幅换仓可能分两天完成，回测和人工计划必须使用相同政策。

这是降低人工操作和日线内成交顺序歧义的工程限制，不是把卖出款当日不可用于买入的监管规则。后续支持“卖出成交回报后解锁买单”时，只扩展执行状态机，并用分钟或真实回报验证。

未完成目标保留固定 target_id；下一日根据实际账户重算剩余差额。最多尝试 3 个交易日是 paper 示例，超时目标失效、生成偏差报告，不能无限追价。月度新目标到来时，要先处理旧计划状态并显式 supersede。

### 7.3 复用引擎但统一事实

实现 CanonicalRQAlphaDataSource，把证券属性、日历、bar、停牌/ST、分红送转、价格限制和可用区间全部映射到同一 snapshot。默认 bundle 可用作安装烟测，不能混入正式 run 的公司行动或日历。未实现接口显式 Unsupported，不能静默回退网络。

引擎产生订单和成交，统一转为标准事件，进入独立 Ledger。RQAlpha 的账户供仿真与下单约束使用，独立账本负责审计；逐日比较两者的经济资产、现金、持仓。发现差异必须修正 adapter/成本模型，不得仅在最终图表修一条收益曲线。

当前源码中股息税默认关闭、存在退市现金返还开关，并已有红利税和送转处理。这些是需要审阅与验证的复用点，不能推断默认即满足真实个人账户。[账户实现](https://github.com/ricequant/rqalpha/blob/0d98adefa87956e26f3e7ca5b26b5f3fc7ca834f/rqalpha/mod/rqalpha_mod_sys_accounts/position_model.py)

最低要求包括：

- 个股红利税启用并验证持有期及分批卖出；是否对 ETF 应用由资产类别决定。
- 禁用“退市按最后市值自动拿回现金”的假设。退市持仓转不可交易资产，基准估值按零的保守政策、回收时另记现金事件；与引擎的差异需显式适配。
- 现金分红不默认自动再投资。拆股/送转后的新增股份按真实可卖日解锁。
- 市价类、限价类、撮合价、tick、费用与涨跌停都做契约测试。保留修改过的 Mod，尽量不 fork 核心。
- RQAlpha 授权包含商业用途限制，不能以 pyproject 里的 Apache 标签代替 LICENSE。个人投资用途如何适用，实施前确认；有障碍时按 T00 评估 Hikyuu 等替代引擎，业务契约不变。[许可证](https://github.com/ricequant/rqalpha/blob/0d98adefa87956e26f3e7ca5b26b5f3fc7ca834f/LICENSE)

### 7.4 成交限制和费用

日频不模拟排队优先级；在不利侧触及涨跌停时采取保守不成交政策，停牌/零量不成交。滑点后的价格不能突破订单限价、交易所价格限制或 bar 合法范围；无法满足就减少/拒绝成交，不制造 bar 外的理想成交。每日成交量参与上限与过去 20 日流动性检查都要有；前者是事后市场容量模拟，后者才是事前计划约束。不能用全天成交量证明收盘窗口存在同样流动性。

费用表按日期、账户、资产类别和方向配置。包含佣金率、最低佣金、印花税、适用过户费/规费、费用包含关系、红利税及舍入规则。最低佣金按券商实际订单收费单位汇总，不能每次部分成交重复收一份；在最终交割单到达后做费用调整事件。

股票印花税在 2023-08-28 有政策变化；红利税与持有期及卖出有关，不能在所有分红日直接统一扣 20%。ETF 的交易税费与股票分别建表。[印花税公告](https://jiangsu.chinatax.gov.cn/art/2023/8/28/art_8349_432258.html) · [股息红利税通知](https://www.chinatax.gov.cn/n810341/n810755/c1797427/content.html)

回测须输出“交易成本后净值”和“再计数据/运行固定支出的经济结果”。后者单独展示，避免固定订阅费用改变策略信号。没有实际券商费率时仅能标注情景费率，不得包装为真实账户净收益。

## 8 账本和公司行动

首版采用可重放的事件账本和明确的现金/证券投影，不必引入复杂会计平台。Event append、去重、现金/持仓更新必须原子提交；历史更正通过冲正/调整事件处理，不直接修改旧成交。

统一事件包括：Deposit、Withdrawal、OrderRegistered、Fill、FeeAdjusted、DividendEntitled、DividendPaid、DividendTaxPaid、SplitOrBonus、SharesUnlocked、AssetWrittenDown、CashRecovery、ExternalTrade、Correction。每个事件有来源和证据。

净资产计算为现金总额 + 可交易证券市值 + 不可交易资产估值 + 应收股利 − 已确认负债；冻结现金已经在现金总额中，不再额外相加。可用现金用于下单，不等于净资产。入金不能算策略收益，展示时间加权收益；资金使用者的实际回报可另报 XIRR。

分红至少记录登记日持仓权益、除息日应收、到账日现金转换。用未复权交易价格记账；如果同时使用复权收益再把现金分红记一遍，会重复计收益。送转要调整数量和批次成本，配股、合并换股、清算等没有适配时阻断发布相关回测结果，实际账户进入人工处理状态。

手算验收例子，均为合成数据与明确情景，不是市场报价：

| 情景 | 预期 |
| --- | --- |
| 初始 10,000 元；买 200 股 × 10 元，佣金 5 元 | 现金 7,995；持仓 200；本日可卖新增数量 0 |
| 下一交易日卖 100 股 × 11 元，佣金 5 元、示例卖出税 0.55 元，其他费用假设为 0 | 现金 9,089.45；持仓 100；以 11 元估值净资产 10,189.45 |
| 同一成交 CSV 导入两遍 | 第二次新增事件 0；现金持仓完全不变 |
| 100 股由 10 元除息到 9.8 元，每股现金 0.2 元，其他因素忽略 | 证券价值 980 + 应收 20；到账仅应收转现金，资产不平白增加 |
| 同一订单分成三笔成交 | 同一订单费用按规则累积，不能机械收取三次最低佣金 |
| 停牌中出现风险阈值 | 保留持仓和估值标记，产生待处置风险；不能虚构卖出 |

真实持仓初始化要导入基准日完整现金与持仓，并确认尚未结算的成交、分红权益和持仓批次。拿不到历史买入日期时，红利税预测标为未知并采用保守预留；实际交割单是最终账务依据。不能为了启动方便假设已有股票都是当天买的。

## 9 人工执行也是完整执行系统

### 9.1 每个交易日的流程

1. 导入前一日结算后的交割单和账户快照；检查未知订单与差异。未核对通过，只能生成研究信号。
2. 拉取 D 日数据；质量门禁通过后冻结 snapshot，月末生成次日起可执行的目标。
3. 按已核对账户生成 draft 计划。每行显示证券名/代码、当前/可卖/目标数量、买卖数量、限价保护、费用、原因、有效日和 plan_id。
4. 执行日核对最新账户、ST/停牌/涨跌停以及价格。没有实时 API 时，人工在券商终端确认并登记检查时刻、参考价与可卖/现金；因此不能声称系统完成自动实时风控。
5. 运行 preflight 后导出可执行清单，人工提交限价委托，登记券商委托号。报价超过保护区间、资金不足、无法确认状态时不提交该行。
6. 导入委托状态、成交、费用、资金流水及账户快照。只有成交表无法证明“未成交委托已撤销”；相关计划必须保持待核对。
7. 收盘后做三方核对：计划/订单、成交/资金流水、券商账户快照。生成日报、阻断项及下次需要处理的事项。

建议首版真正支持一个通用规范 CSV 和用户券商的一种导出格式；未知券商时用样例映射模板。不要一次承诺支持所有券商。

### 9.2 计划和订单状态机

~~~text
Plan: DRAFT → RISK_PASSED → EXPORTED → RECONCILED
                       ↘ BLOCKED / EXPIRED / SUPERSEDED

Order: INTENT → EXPORTED → SUBMITTED → ACKNOWLEDGED
                                      ↓          ↓
                              PARTIALLY_FILLED → FILLED
                                      ↓
                                CANCEL_PENDING → CANCELLED
其他结果: REJECTED / EXPIRED / UNKNOWN
~~~

EXPORTED 只代表文件生成。人工已点击但没有拿到委托号，标 UNKNOWN；程序失败、空 CSV 或未看到成交不能把它改为“没下单”。UNKNOWN 阻断同一账户标的方向的替代订单，先查券商终端/当日委托与成交。

取消请求不等于已取消。允许取消期间出现新成交；最终以累计成交和实际撤销剩余量计算。终态订单收到晚到成交也要幂等入账并重新核对，不能因状态已结束就丢弃。

intent_id 由账户、target_id、有效交易日、证券、方向、执行批次确定。plan_id 绑定 snapshot、策略版本、账户状态哈希、风险版本及目标；相同输入重复生成返回同一计划。账户变更产生新版本，先显式替代旧版本。旧纸质/HTML 清单不能物理召回，所以新清单突出作废编号；操作记录是必需的。

### 9.3 CSV 契约和导入事务

至少支持四类载荷：orders、fills、cash_movements、account_snapshot。成交字段包括 account_id、trade_date、trade_time、exchange、symbol、side、quantity、price、gross_amount、commission、tax、other_fees、broker_order_id、broker_trade_id。计划号可缺，但缺时进入待匹配列表。

每批先校验编码（UTF-8/常见 GBK）、日期、时区、小数、买卖枚举、单位及金额一致性，再生成 dry-run 差异。批准本地导入后单事务提交；一批有关键冲突则整批不提交并输出行号，不半入账。实盘原件只保留本地且脱敏。

去重主键优先采用券商规范定义的账户 + 交易日 + 市场 + 成交编号，先确认该券商编号作用域。只有文件哈希不能防止两次导出同一成交。没有稳定编号时不能盲目按价格/数量去重，因为可能是两笔相同成交；需源行标识、委托号、时间与累计量验证并处理歧义。

计划外交易也必须记账并标 EXTERNAL，不得丢弃；无成交仅有仓位变化时记录 unexplained 差异，不能伪造一笔交易来凑平。费用晚到以 FeeAdjusted 补记。

### 9.4 对账的通过标准

持仓数量、可卖数量、冻结数量、委托累计成交精确一致；现金差异须按源的舍入和费用待结算分类。示例每笔 0.01 元的舍入容差只接受已解释差异，不能把“低于 10 元”设成可忽略。比较统一时刻和统一口径，券商市值与本地价格时间不同形成的差异单独展示。

报告保存来源文件、导入批次、核对时刻、差异类别、处置记录和关闭人。未解释资金/持仓差异阻断新买入。下一日计划必须从已确认真实状态重算，不能沿用理想目标持仓。

## 10 风控和暂停恢复

阈值首先是配置示例，未得到你的风险预算前 manual 模式不能使用 paper 默认值悄悄开通。下表中的硬约束是系统行为要求；百分比只是演示参数。

| 层次 | 首版检查 | 失败动作 |
| --- | --- | --- |
| 数据 | 快照完整、预期交易日、历史状态/因子一致、执行前行情/人工确认时间 | 阻断受影响的新交易，生成明确原因 |
| 组合 | paper 总目标仓位 ≤80%，ETF 单只 ≤20%，股票单只 ≤8%；无负权重、无借款 | 对非法目标拒绝；被动涨价造成超限进入复核，不虚构强平 |
| 流动性 | 示例单次计划金额 ≤过去 20 日成交额中位数的 0.5%；极端价格和整手后偏离 | 缩量需重新生成签名计划；不能静默改变策略目标 |
| 订单 | 当前现金/可卖、未结委托、tick/lot、涨跌停、价格偏离、有效期、重复计划 | 拒绝具体订单并记录；未知状态先核对 |
| 账户 | 账本差异、未授权资产、计划外交易、红利税/公司行动待处理 | 阻断新买入，保留读数据和对账能力 |
| 运行 | 锁冲突、磁盘空间、数据库损坏、备份失败、任务缺失、时间异常 | 暂停发单/导出，暴露错误，恢复前检查 |
| 策略风险 | 示例净值回撤 10% 进入复核；异常单日损益检查估值与现金流 | 不增加风险；是否减仓由预登记政策和可成交条件决定 |

阈值越过不能保证亏损止于该水平。日频检查看不到完整日内风险，人工执行的实时限制还依赖操作者。

状态采用 RUNNING、REVIEW、HALTED。暂停时阻止新计划发布，列出全部已导出/已报/未知订单，提示人工核查和必要撤单；程序没有券商控制权，不能在界面显示“已全部撤单”。撤单成功必须有证据。

恢复需要修复原因 → 获取最新账户与订单状态 → 对账通过 → 检查数据和价格 → 作废旧计划 → 记录操作人及原因 → 重新生成。重启进程或跨过午夜不自动恢复。紧急减少风险仍要走独立记录的减仓计划，并检查可卖与行情，不能用损坏数据盲目清仓。

## 11 交易规则和实际使用前置条件

沪深交易规则均发布了 2026 修订版本，通知写明 2026-07-06 生效。上交所页面状态标签与正文曾有不一致，应结合正文、附件和暂缓条文记录版本。历史回测按历史日期应用规则，不把今天的涨跌幅和费用覆盖全历史。[上交所通知](https://www.sse.com.cn/lawandrules/sselawsrules2025/fund/trading/c/c_20260424_10817739.shtml) · [深交所通知](https://investor.szse.cn/lawrules/rule/trade/t20260424_620190.html)

RuleSet 的选择键至少包括 date、exchange、board、asset_type、特殊状态。沪深主板普通股票与首版股票 ETF 的买入单位、价格最小变动单位、T+1 可卖和涨跌停分别验证。不要用“所有 A 股/ETF 都是 10% 或都可 T+0”的常量。即使首版不新买 ST，也要能处理持仓转 ST 后的真实限制。

程序自动生成指令也在程序化交易定义范围内，人工点击不能自动推定免报告。manual 模式实际使用前，向券商说明“系统生成买卖数量和清单，个人手动提交”，落实该账户适用的报告/核查流程，保存确认记录。[证监会发布说明](https://www.csrc.gov.cn/csrc/c100028/c7480577/content.shtml) · [上交所实施细则](https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20250612_10781696.shtml) · [深交所实施细则通知](https://www.szse.cn/lawrules/rule/trade/t20250403_612770.html)

这些是实际投入资金的入口条件，不阻碍现在完成代码、数据样本和模拟账户。用户尚未提供券商、费率、账户初始状态和个人风险预算，应在配置中标为 required，不猜测。

## 12 Harness 比较和可替换方式

这里分成两件事：确定性 harness 管理一次实验如何执行和记录；AI harness 提出假设、写候选代码、根据开发集反馈迭代。首版必须拥有前者，后者先比较再决定，与你的回答一致。

| 候选 | 适合的需求 | 接入成本与局限 | 本项目结论 |
| --- | --- | --- | --- |
| 轻量 CLI + 标准 RunResult | 两个规则策略、快照、配置、固定验收 | 自写少量编排与登记；不提供自动研究 | 首版直接实现，作为公共评估入口 |
| Qlib workflow / qrun | 因子面板、ML 训练、时间切分与实验记录 | 数据需导出/接 provider；评估资金与费用需改成真实约束 | 最优先的 ML 扩展候选，不替代账户账本 |
| Microsoft RD-Agent(Q) | 自动因子/模型迭代、研报研究 | 当前文档要求 Linux，多数场景依赖 Docker；默认量化路径与 Qlib 结合 | 后续独立环境试验，候选回到本系统验收 |
| OnePunchMonk/AgentQuant | 失败记忆、搜索预算、可审阅研究过程 | A 股与数据适配仍需工作，部分优化 benchmark 使用 mock | 借鉴流程，不把 README 结果当可用策略 |
| RndmVariableQ/AlphaAgent | A 股因子 DSL / 自动挖掘探索 | 需要核历史时点、当前代码与论文版本、代码授权 | 保留研究候选，暂不成为首版依赖 |

依据：[Qlib](https://qlib.readthedocs.io/en/latest/component/workflow.html)、[RD-Agent](https://github.com/microsoft/RD-Agent)、[Quant 场景](https://rdagent.readthedocs.io/en/latest/scens/quant_agent_fin.html)、[AgentQuant](https://github.com/OnePunchMonk/AgentQuant)、[AlphaAgent](https://github.com/RndmVariableQ/AlphaAgent)。

建议以后用相同数据快照和 10 个固定候选任务做对比，而不是各跑各的排行榜。评价维度：可复现率、有效候选比例、时间/费用、失败可解释性、对公共评估器的遵从、数据适配成本、Mac/Linux 迁移和授权。

AI 候选产物限定为策略包/因子包 + manifest，写明所需字段、lookback、训练截止日、数据来源、代码 hash 与参数。只能在无账户凭据、只读研究快照的隔离工作进程运行。交易计划发布者仅接受已批准的具体代码版本；harness 无权重写费用、风险阈值、真实账本或自动扩大仓位。这是保护评估完整性和账户边界的设计。

可替换不等于所有模型零成本转换。Qlib 的因子、模型可包装成 Strategy；RD-Agent 产生的 Qlib 工作流仍需转出标准信号或策略制品，再用相同 RQAlpha 评估器复核。若不能表达该候选，报告 Unsupported，不能另用一个更理想的回测结果补位。

## 13 实验纪律和报告规格

每个 run 固定：Git commit 与未提交 diff hash、Python/依赖锁 hash、策略包/参数、数据快照、费率/规则/风险版本、撮合政策、时间切分、初始账户、seed、执行命令、开始/结束时间、状态及异常。缓存键覆盖上述输入；费用或单位变了也必须失效。

建议股票从数据完整可用的早期年份开发，2023–2024 作验证、2025 至最近完整交易日作一次性留出测试只是候选切分；实际区间须在初次策略运行前登记。如果此前已针对这段历史选择策略或看过大量结果，它只是回看验证，不再叫干净 holdout。之后积累的 paper 结果作为真正前瞻记录。

开发/验证集可做少量预登记邻域与成本压力；失败实验也保存。封存集单独目录/权限与解锁动作，候选生成进程不能读；如果反复向 agent 返回“封存表现好/坏”，同样会泄露。定量研究的多重尝试问题见 [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)。

日报和回测报告至少包括：

- 数据截止时间、账户核对状态、当前总/可用现金、持仓与目标偏差、待办订单、阻断原因。
- 净值、年化收益、最大回撤、波动、Sharpe（明确无风险利率及频率）、收益样本区间；短样本不强调年化。
- 基准与超额的定义、逐年/分段表现、ETF/个股/现金归因，资金流和分红影响。
- 成交数量、每笔金额、双边换手 = 买卖成交额绝对值之和 / 平均净资产、费用、滑点、未成交率、现金拖累。
- 不利成交/成本 2 倍和 3 倍/延后一日/小本金 10 万及 30 万的结果；不需要无目的地穷举搜索。
- assumptions 列表：历史可用时间推定、ETF 清单选择偏差、日频成交代理、缺乏穿透、未支持公司行动等。
- 数据许可范围、完整输入引用、全部实验索引和运行失败原因。

回测成功只证明计算完成。策略是否进入人工实盘另有书面候选评审：结果能够复现、样本外解释合理、成本/延迟后仍满足你预设目标、风险和资金匹配。首版可验收为“工程可用但两条策略均不值得投入”。

## 14 运维和恢复

首版日常任务：交易日盘后 ingestion → validate → snapshot → strategy/plan → report → backup；执行日另有 preflight 和 import/reconcile。使用 OS 调度器加幂等 CLI，Mac 可采用 launchd；机器休眠/错过时间时补数据和报告，但过期计划不得自动补执行。

每个 job 用 exchange_date + job_type + config_hash 唯一登记，运行前拿锁，写 heartbeat 和结束状态。单进程崩溃后下一次启动先检查未结束任务和导入事务；相同 batch 不重复扣款。研究并行跑时各自使用独立运行数据库，只汇总产物索引。

结构化日志包含 run_id、snapshot_id、plan_id、intent_id、event_id 和错误类别，不记录 token 或完整证券账号。首版终端错误码 + 本地异常报告必做；无人值守通知插件可后加。暂停后必须能继续导入与核对。

账户事件持久化成功后才返回成功。每日使用 SQLite Backup API/一致快照备份账本，同时保存被引用的 manifest、配置和原始成交导入文件；不能只复制一个正在写入的 SQLite 主文件。每月做一次恢复演练，目标是在 30 分钟内恢复最近完整备份并重放已保存的源文件；这是待验证目标，不是本次已测结论。[SQLite 备份](https://www.sqlite.org/backup.html)

有外部磁盘或私人备份存储时保留第二份加密副本。磁盘丢失时仅本机备份不构成灾备；未配置异地/离机副本前在运行报告中标明该限制。恢复可能缺失的交易以券商记录重新导入，不猜测。

## 15 实施顺序与完成标准

| 阶段 | 工作与交付 | 必须通过才能进入下一阶段 |
| --- | --- | --- |
| M0 依赖与数据可行性 | 契约 v1、RQAlpha 安装/数据适配试验、权限探测、许可与环境记录 | 股票/ETF 样例能走引擎；时间顺序与关键公司行动无未解释错误 |
| M1 离线闭环 | 合成数据、存储、账本、风险、计划、重复导入与故障恢复 | 一条 demo 跑通，手算与异常用例通过；明确标合成 |
| M2 真实研究 | 完整样本期数据、ETF/个股示例、基准、报告与实验记录 | 数据质量/来源完整；无未来 bar 泄漏；结果可复现 |
| M3 持续 paper | 真实新数据逐日运行、人工 CSV 工作流演练 | 跨重启、部分成交、费用晚到、未知订单与对账均可恢复 |
| M4 人工实际使用 | 账户初始化、券商格式、个人风控、费率/报告流程落实 | 工程和个人入口条件满足，用户明确决定使用具体版本 |

工作量估算：专业 Python 开发者串行投入约 20–35 个有效人日；主要不确定性在历史数据、引擎边界、公司行动和券商导出格式。agent 可以减少编码时间，但不能省略数据授权和市场观察。纸面运行至少覆盖 2–3 次真实调仓可检验操作，月度策略意味着数月；离线回放可先查技术流程，不能代替前瞻观察。

完整验收要求：

1. 干净环境按 README 可运行，不需要付费 token 完成合成 demo。
2. ETF 和个股均运行真实数据的完整策略，不仅写接口占位。
3. 新数据源或新策略通过公共 contract tests 后可替换，旧 run 仍可重放。
4. 至少覆盖未来信息、T+1、整手/零股、涨跌停、停牌、分红送转、费用分拆、部分成交、重复导入、UNKNOWN、断电恢复和退市。
5. 风控真实阻断执行清单；账本、账户差异可追溯，不通过就不发布新买单。
6. 自动交易仍未启用；系统清楚标识 demo、paper、manual，不会因配置默认值把研究输出变成实盘动作。

## 16 从哪里开工

执行 agent 首先阅读本计划与 [任务包](AGENT_TASKS.md)，建立契约和两个有确定答案的合成案例，然后做 RQAlpha 与数据源的窄范围适配试验。不要先做 UI、抓全市场分钟数据或让 agent 开始因子搜索。

建议仓库布局如下；这是待实现目标，当前仓库只交付调研与计划：

~~~text
src/ashare/
  domain/        # 类型、事件、规则和错误语义
  ports/         # Provider / Strategy / Engine / Execution / Ledger
  data/          # ingest、normalize、quality、snapshot
  strategies/    # ETF 趋势、个股低波动、被动基准
  portfolio/     # 聚合、配额、差额订单和现金计划
  risk/          # 门禁与暂停恢复
  accounting/    # 事件账本、批次、费用与公司行动
  execution/     # 计划生命周期与对账
  adapters/
    data/        # tushare、规范 CSV，后续 rqdata
    backtest/    # rqalpha
    execution/   # paper、manual_csv，后续 xtquant
    storage/     # parquet、duckdb、sqlite
  research/      # ExperimentSpec、产物与候选验收
  reporting/
  cli/
  ops/           # 日常任务、锁、备份恢复
configs/
tests/{contracts,golden,integration,recovery}/
fixtures/synthetic/
docs/adr/
~~~

拟定命令体验如下，**这些命令是实现验收规格，现在尚不存在**：

~~~sh
uv sync --frozen
uv run ashare demo --scenario complete
uv run ashare data probe --provider tushare
uv run ashare data sync --config configs/research.yaml
uv run ashare data validate --batch BATCH_ID
uv run ashare snapshot create --batch BATCH_ID
uv run ashare backtest --config configs/etf_trend.yaml --snapshot SNAPSHOT_ID
uv run ashare backtest --config configs/stock_lowvol.yaml --snapshot SNAPSHOT_ID
uv run ashare paper step --date YYYY-MM-DD
uv run ashare plan build --account paper --asof YYYY-MM-DD
uv run ashare plan preflight --plan PLAN_ID
uv run ashare plan export --plan PLAN_ID
uv run ashare import preview --account paper --mapping configs/broker_csv.yaml --file FILE
uv run ashare import apply --batch BATCH_ID
uv run ashare reconcile --account paper --date YYYY-MM-DD
uv run ashare report daily --account paper --date YYYY-MM-DD
uv run ashare risk halt --account paper --reason REASON
uv run ashare backup create --account paper
uv run ashare backup verify --backup BACKUP_ID
~~~

离线 demo、真实历史研究、持续 paper 和人工账户各有完成证据。执行 agent 汇报时要逐项说明哪些已运行、哪些只检查代码、哪些仍需账号或真实市场验证。
