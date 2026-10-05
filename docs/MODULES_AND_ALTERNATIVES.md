# 模拟盘模块组成与备选方案

更新日期：2026-10-04。本文细化 [实现计划](IMPLEMENTATION_PLAN.md)，回答模拟盘如何运行、各模块由哪些 Python 文件组成，以及如何替换关键组件来学习。本轮仍然只修改设计资料，没有创建或运行交易系统。

建议保留一套明确的首版默认组合，同时为不同组件定义替换接口。下列目录、脚本名和配置片段是待实现规格；不要把它们误认为当前已存在的程序。没有必要在首版把所有备选都安装一遍。

## 1 模拟盘可以怎样做

模拟盘使用虚拟资金，没有真实证券买卖。与历史回测的差别在于：历史回测可以一次计算很多年；前瞻模拟盘随现实日期推进，每天只使用那时已经得到的数据。

| 方式 | 如何运行 | 能验证什么 | 不能据此证明什么 |
| --- | --- | --- | --- |
| 本地日线模拟盘，首版建议 | 每日接新行情，前一日已确定的订单按登记规则在下一交易日模拟结算 | 数据更新、策略信号、账户连续性、交易限制、风控、重启恢复 | 日内精确成交、真实排队、券商接口质量 |
| 本地实时行情仿真 | 接实时行情，由本地模拟器处理虚拟订单 | 盘中状态、订单生命周期、部分成交/撤单流程 | 真实券商接受该订单或实盘必然同价成交 |
| 券商模拟账户 | 接入券商提供的测试环境，用虚拟资金报撤单 | 具体 SDK、账户、回报和查询接入 | 模拟环境与实盘的流动性、费用和权限完全一致 |
| 人工操作演练 | 对本地模拟盘的计划手工填写虚拟委托/成交 CSV | 用户核对清单、导入、纠错和对账的完整操作 | 证券交易已发生 |

vn.py 的 [PaperAccount](https://github.com/vnpy/vnpy_paperaccount) 官方说明支持基于实盘行情进行本地仿真，可作为实时方案候选。是否覆盖本项目的股票/ETF 结算、分红、费用与恢复需求，仍要跑相同契约测试；模块名不能代替验收。

券商模拟账户需要确认实际券商是否提供、支持哪些品种、如何开通、指定终端和 SDK 环境。此前已读的 [xtquant 文档](https://dict.thinktrader.net/nativeApi/xttrader.html) 描述了报撤单、查询和回调；本轮重访超时，不能据此承诺你已有可用模拟账号。首版本地模拟不依赖这一步。

### 1.1 首版日线模拟的一天

1. 建立独立 paper 账户，例如虚拟资金 20 万元，账户名称明确显示“模拟”；不加载真实下单凭据。
2. D 日盘后拉取并验收数据，冻结数据版本。月末策略生成目标，订单计划最早 D+1 生效。
3. D+1 到来后，使用当时已获得的状态做事前检查；若状态晚到，只能在到达后采取行动，不能回写成早已知道。
4. D+1 收盘数据到齐后，模拟器结算此前已冻结的订单：按价格保护、停牌、涨跌停、数量和费用产生模拟成交或拒单。成交价模型沿用主计划的“次日收盘代理”，不能临时挑当天最优价。
5. 更新持仓、现金、股利权益和可卖数量，核对模拟器与独立账本，生成日报。记录成交所属市场时刻和本系统实际获得/入账时刻。
6. 没有调仓的日子照样更新估值、处理公司行动、检查风险。下一日继承账户，不能重置为 20 万。

收盘后的完整 bar 只能用于本次模拟结算和之后的策略决策；不能反过来修改此前订单。价格代理与真实人工操作窗口有差异，日报始终显示模拟标记与成交假设。

### 1.2 模拟器如何复用

PaperAdapter 负责连接持续运行流程与模拟引擎，**它不另写一套与回测不同的金融撮合规则**。首版优先复用已验证的 RQAlpha 适配层。实际可行的状态续跑方式在 T04 试验后确定：

- 优先方案：保存并恢复引擎状态、账户、订单、策略状态和事件游标。引擎检查点与 SQLite 事件提交通过 batch_id、状态哈希和运行登记对应；跨文件不假设存在自动原子事务。
- 备选方案：若该引擎版本不能可靠续跑，从模拟账户起始日重放已冻结的逐日数据、参数和订单。比对既有事件前缀，只把新日事件幂等写入。发现旧交易改变就停止核对，不覆盖昨天已发布的模拟成绩。
- 两种方案都需要“连续跑十天”和“第五天重启后再跑五天”的等价测试。模拟引擎与审计账本相同不等于独立证明正确，因此仍保留手算案例。

策略/费率升级不能重写过去的模拟结果。首版最容易审计的做法是：继续保留旧 paper 账户，新建独立 paper 账户比较新版本；实际持仓策略切换则显式记录生效日和交接状态。

机器错过某日任务时可以补数据，但未事先生成的订单不能追溯为当天已提交。补做的历史实验归入 research，不能混进前瞻模拟业绩。

### 1.3 模拟盘交付和通过标准

至少交付 paper.yaml、paper.sqlite、每日数据/参数清单、模拟订单/成交、连续净值、风险原因和日报。以下命令是待实现规格：

~~~sh
uv run ashare paper init --config configs/paper.yaml
uv run ashare paper step --date YYYY-MM-DD
uv run ashare paper status
uv run ashare report daily --account paper --date YYYY-MM-DD
~~~

验收包括：不需要券商账号；ETF 与个股账户连续存在；同一天重复运行没有重复成交；重启不丢状态；过期未生成计划不补成交；部分成交/未知状态可演练；模拟模块无法调用真实交易适配器。完成本地仿真不代表完成券商模拟或真实交易验证。

## 2 系统分成哪些模块

以下全部以规划中的 src/ashare/ 为根目录。表中的输入输出是模块边界，关键文件是建议的内部拆分。短小功能可先放在同一文件，避免为每一个函数造一个文件。

| 模块 | 输入 → 输出 | 重点 Python 文件 | 组成和责任 |
| --- | --- | --- | --- |
| 公共定义和接口 | 外部载荷 → 校验后的标准对象 | domain/models.py、domain/events.py、ports/interfaces.py | Instrument、TargetPortfolio、OrderIntent、ExecutionEvent、Protocol 和错误类型 |
| 数据采集与整理 | 数据请求/原始响应 → 标准批次 | data/ingest.py、data/normalize.py、data/quality.py | 拉取、分页、单位转换、缺失分类与质量阻断 |
| 数据查询与时点 | 数据快照/决策时间 → 策略可见的数据 | data/snapshot.py、data/market_view.py、data/universe.py | 冻结清单、历史股票池、可见时点过滤、覆盖检查 |
| 数据商适配 | 标准请求 → 指定供应商响应 | adapters/data/tushare_provider.py、csv_provider.py；后续 rqdata_provider.py | 供应商 SDK/字段仅留在这里 |
| 存储适配 | 标准表/事件 → 文件或数据库 | adapters/storage/parquet_store.py、duckdb_query.py、sqlite_store.py | 具体存储实现；迁移后新增 postgres_store.py |
| 策略 | 可见数据/参数 → 目标仓位 | strategies/base.py、buy_hold.py、etf_trend.py、stock_lowvol.py | 不含券商 API，不直接操作 SQL；基准也是策略 |
| 组合与订单规划 | 多策略目标/账户 → 聚合目标与差额订单 | portfolio/allocator.py、planner.py、sizing.py | 额度合并、整手取整、现金预留、未完成计划占用 |
| 风控 | 目标/订单/数据/账户 → 通过或阻断及原因 | risk/engine.py、rules.py、state.py | 必要硬检查、可插拔规则、暂停与恢复；规则多后再拆 data/portfolio/order 规则文件 |
| 回测适配 | 实验规格/快照 → 模拟事件与结果 | adapters/backtest/rqalpha_engine.py、rqalpha_data.py、rqalpha_bridge.py | 把标准数据与策略接入引擎，转换订单/成交，不把内部对象泄漏出去 |
| 运行与执行 | 订单/状态 → 回报或人工计划 | execution/paper_runner.py、plan_service.py、order_state.py；adapters/execution/paper.py、manual_csv.py | 持续模拟、计划生命周期、人工导出；以后加券商 adapter |
| 账本与核对 | 成交/费用/公司行动/源快照 → 账户与差异 | accounting/ledger.py、positions.py、corporate_actions.py；execution/reconcile.py | 事件去重、现金与批次、可卖数量、分红、三方核对 |
| 实验记录与报告 | 配置/事件/账户 → 可复现实验与页面 | research/runner.py、registry.py、evaluate.py；reporting/metrics.py、daily_report.py | 运行 manifest、完整实验索引、收益风险指标、日报模板 |
| 运维与入口 | CLI/调度参数 → 任务运行状态 | cli/main.py、ops/daily.py、ops/locks.py、ops/backup.py | 作业锁、幂等任务、备份恢复；调度器只启动入口 |

规则/费用表还需要 domain/trading_rules.py、accounting/fees.py，以及配置中的有效日期；日志、配置解析和供应商注册可置于 config.py、bootstrap.py。它们属于支撑代码，不额外变成一套业务服务。

首版用一个 Python 包和一个 CLI 即可。运行账户只有一个逻辑写者；研究进程可以读取不可变数据并各自输出结果。模块数量不等于服务器或微服务数量。

### 2.1 值得优先读懂的六个文件

1. strategies/etf_trend.py：怎样把市场数据变成目标仓位。
2. portfolio/planner.py：目标比例怎样变成真实可买卖的数量。
3. risk/rules.py：哪些情况下必须拒绝交易。
4. adapters/backtest/rqalpha_bridge.py：同一套策略如何接入成熟引擎。
5. accounting/ledger.py：成交怎样改变现金和持仓。
6. execution/reconcile.py：如何证明本地记录与来源一致。

学习时可以沿着一个虚构买单走过这六个文件，比一开始阅读整个框架更直观。随后再阅读 normalize.py，理解数据错误如何影响下游。

### 2.2 必须随代码存在的配置和产物

| 类别 | 建议文件 | 作用 |
| --- | --- | --- |
| 环境 | configs/demo.yaml、research.yaml、paper.yaml、manual.yaml | 明确数据和账户隔离；manual 不继承演示风险阈值 |
| 策略 | configs/strategies/etf_trend.yaml、stock_lowvol.yaml | 参数、调仓周期、候选范围 |
| 规则和风险 | configs/rules/*.yaml、configs/risk/*.yaml | 日期有效的市场规则、硬限制和风险预算 |
| 费用 | configs/fees/*.yaml | 账户、品种、方向、最低佣金、历史税费 |
| 人工映射 | configs/brokers/manual_csv.yaml | 券商字段、编号作用域、编码和金额口径 |
| 测试 | tests/contracts/、golden/、integration/、recovery/ | 替换接口、手算、整链路和异常恢复 |
| 结果 | runtime/runs/、runtime/plans/、runtime/state/ | 回测与模拟产物、清单和隔离账户 |

## 3 数据供应商的备选

数据供应商决定从哪里拿数据，存储方案决定拿到后如何保管。这两个选择独立。

| 候选 | 适合的用途 | 额外成本或局限 | 兼容做法 |
| --- | --- | --- | --- |
| Tushare Pro，当前主线 | 首版日线、价格类策略、历史状态调查 | 逐接口权限、分页与历史覆盖要验收 | TushareProvider 输出标准表 |
| RQData | 更完整的数据需求及后续 PIT 财务研究 | 询价、授权、字段/时间语义对比；不能默认所有问题自动解决 | RQDataProvider，同一快照规范 |
| JQData | 另一商业数据源，使用官方 jqdatasdk | 当前权限和历史 PIT 覆盖需实际确认；本轮官网访问受地域限制 | JQDataProvider，同一证券/单位/时点映射 |
| AKShare | 小样本、学习、来源交叉核查 | 接口与上游变化、使用范围逐项审查 | 独立来源标签，不在主源失败时静默拼接 |
| 规范 CSV / Parquet 导入 | 无账号开发、第三方合法样本、离线学习 | 无自动更新，覆盖与质量由导入者负责 | CsvProvider，是首版必须的离线入口 |

依据：[Tushare](https://tushare.pro/document/1)、[RQData](https://www.ricequant.com/doc/rqdata/python/stock-mod.html)、[JQData SDK](https://github.com/JoinQuant/jqdatasdk)、[AKShare](https://akshare.akfamily.xyz/introduction.html)。

换源的学习实验：选择同一只 ETF/股票同一时间段，比较标准化价格、成交量、分红和停牌，再解释差异。不能只看最终收益是否相近，也不能把差异平均掉。

## 4 数据库和存储的备选

| 组合 | 适用场景及学习价值 | 代价与边界 | 本项目安排 |
| --- | --- | --- | --- |
| Parquet + DuckDB + SQLite | 学习列式数据分析与事务账本的分工；单机日线研究 | 管理文件 manifest，账户串行写入 | 首版默认 |
| DuckDB 内部表 + SQLite | 学习 SQL 表管理，减少直接处理 Parquet 分区的工作 | 仍需数据版本和导出；共享写入仍有进程边界 | 小范围对照实验 |
| SQLite 存行情和账本 | 很小样本，学习主键、索引、事务和 SQL | 大范围分析要测性能；版本化仍要设计；研究与实际账户至少分文件 | 教学用起点，可保留为 fixtures 后端 |
| PostgreSQL 存行情和账本 | 学习服务端数据库、多个进程/客户端和事务 | 需要运行服务、用户权限、索引和备份；行情与账户按 schema/权限隔离 | 多机或并发需求出现后评估 |
| Parquet + DuckDB + PostgreSQL | 保留研究文件层，只升级账户与运行状态服务 | 三种组件的部署与迁移成本 | 最自然的后续演进之一 |
| ClickHouse + PostgreSQL | 更大规模分钟/逐笔行情分析，账户仍走事务库 | 明显增加运维；不是目前几个日线策略的必要条件 | 仅列进阶方向 |

这些是设计取舍，没有跑过同机基准，不给出虚构性能排名。DuckDB 的本地进程内并发模式、SQLite 的适用边界，以及 PostgreSQL/ClickHouse 的定位可分别核对官方资料。[DuckDB](https://duckdb.org/docs/current/connect/concurrency) · [SQLite](https://www.sqlite.org/whentouse.html) · [PostgreSQL](https://www.postgresql.org/docs/current/intro-whatis.html) · [ClickHouse](https://clickhouse.com/docs/get-started/about/intro)

兼容性要求：MarketDataStore 对外只暴露按 snapshot、证券、时间查询的接口；LedgerStore 只暴露事务性事件写入和账户读取。策略不能直接写某种数据库 SQL。迁移时先导出事件、检查数量/现金与历史 hash，再切换 writer；不能同时让两个新旧账本各自接单。

DuckDB/SQLite 的嵌入式用法都不需要独立数据库服务器；PostgreSQL 通常需要。数据库品牌变化也不会自动改善数据是否完整或是否有未来信息，这仍属于数据模块。

## 5 策略和组合方法的备选

策略回答“想持有什么”，组合方法回答“每个标的分多少钱”，风险规则回答“允许这样做吗”。它们分别替换。

| 策略方向 | 简单规则示例 | 新增数据与学习点 | 主要陷阱 |
| --- | --- | --- | --- |
| 买入持有 / 固定权重 | 持有预登记 ETF，必要时恢复固定比例 | 基础行情、分红；学习基准与费用 | 固定比例调仓和纯买入持有是不同基准 |
| ETF 趋势，首版主线 | 过去 120 日为正、排序取前二，月度检查 | 收益窗口、时点、空仓规则 | 震荡市反复换仓，固定清单的历史选择偏差 |
| ETF 均线趋势 | 收盘高于过去 N 日均线时持有，否则留现金 | 学习平滑窗口、信号滞后 | 参数很多且同一趋势信号高度相关 |
| ETF 相对强弱轮动 | 在预登记池中持有近期表现较强者 | 横向排名、换手、持有缓冲 | 最强者也可能下跌；多个 ETF 持仓重叠 |
| 个股低波动，首版主线 | 在流动性合格池中选择近期波动较小者 | 历史状态与日收益 | 集中于某些行业、低波动不等于低亏损 |
| 个股动量 | 从合格池按过去若干月表现排序 | 学习横截面信号与换手 | 反转、成本、停牌和不可成交影响 |
| 价值 / 质量因子 | 结合估值、盈利、现金流等排序 | 必须增加公告时点与历史版本财务数据 | 最新修订财报回填历史造成未来信息 |
| ML 排名模型 | 用特征预测未来收益排序，再转目标 | Qlib 等、训练/验证切分、模型制品 | 样本重叠、过拟合、数据漂移，工程复杂度高 |

以上是研究候选，不是推荐同时实盘运行。趋势与质量的研究动机可参见 [Time Series Momentum](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum)、[Quality Minus Junk](https://www.aqr.com/Insights/Research/Working-Paper/Quality-Minus-Junk)；这些论文对象与本项目简化的 A 股长仓规则不同，不把论文业绩移植为本项目预期。

组合分配也有多个学习选项：

- 固定额度/等权：每个策略或标的使用预设比例，最容易核对，首版使用。
- 波动率倒数权重：近期波动较小的标的给较大权重；需设单标的上限，不能让低估波动导致极端集中。
- 风险平价：考虑资产之间相关性，使风险贡献更接近；不等同于简单的波动率倒数。
- 最小方差或其他约束优化：依赖历史协方差估计及约束；可用 PyPortfolioOpt 或 Riskfolio-Lib。优化器输出连续权重，仍需 Planner 做整手、现金与费用处理，优化无解时进入复核或显式登记的后备方案，不能偷偷去掉约束。

库提供的是组合计算能力，不替你证明收益预测准确。[PyPortfolioOpt](https://github.com/PyPortfolio/PyPortfolioOpt) · [Riskfolio-Lib](https://github.com/dcajasn/Riskfolio-Lib)

首版只实现被动基准、ETF 趋势和个股低波动。为了学习新增一条时，只更换 Strategy/Allocator，固定数据、费率、执行政策和评估区间，不用一次实验同时改变五个组件。

## 6 风控的备选与共同底线

风控不适合简单列成互斥的“A 或 B”。现金不能为负、不可卖数量不能卖、未知委托不能盲目重发、数据过期不能交易等硬检查，任何备选方案都保留。可替换的是组合风险政策和检查器实现。

### 6.1 可选择的组合风险政策

| 政策 | 做法与学习价值 | 局限 | 首版安排 |
| --- | --- | --- | --- |
| 固定仓位上限与现金预留 | 限制总仓位、单只持仓和可用资金 | 对波动变化反应较少 | 默认，参数由 paper 示例和实际预算分开 |
| 波动率目标 | 估计近期风险，波动上升时减少目标总仓位 | 估计滞后，可能在剧烈变动后卖出；默认仍不使用杠杆 | 后续可加 overlay |
| 回撤分级处理 | 回撤进入不同区间时预警、暂停加仓或按计划减仓 | 不能保证损失上限；恢复和滞回规则需要写清 | 首版有暂停复核，自动减仓另登记 |
| 行业/相关性限制 | 限制同一行业或相关资产集中 | 需要历史行业和可靠相关性数据；ETF 穿透又需额外数据 | 数据齐备后再做硬约束 |
| 压力测试与尾部风险预算 | 用极端市场情景检验组合，或研究 CVaR 等约束 | 历史情景不覆盖所有未来事件，优化也可能不稳定 | 先做报告；复杂优化后续再加 |

CVaR 可以理解为：在设定的最差一部分情景里，平均会损失多少。它是统计估计，不是保险。相关组合优化能力见 [PyPortfolioOpt 官方文档](https://pyportfolioopt.readthedocs.io/en/latest/GeneralEfficientFrontier.html) 与 [Riskfolio-Lib 文档](https://riskfolio-lib.readthedocs.io/en/latest/)。

### 6.2 检查器实现的备选

| 实现 | 适合什么 | 本项目如何使用 |
| --- | --- | --- |
| 小型 Python 规则链 | 当前单账户、人工清单、希望规则可读 | 首版；按 RiskPolicy 接口逐条返回理由，配置化阈值 |
| RQAlpha sys_risk | 仿真引擎内部的下单合法性检查 | 复用，外部账户/数据风控仍保留，不能只在回测中有效 |
| vn.py RiskManager | 已采用 vn.py 的实时交易工作流 | 可替换订单检查实现，但需标准对象适配及版本验证 |
| PyPortfolioOpt / Riskfolio-Lib | 在生成目标权重时加入组合约束 | 属于 Allocator/风险 overlay，不能代替订单和账户检查 |

vn.py 当前风险模块列出活动订单、日内报撤单、重复订单、单笔数量及委托合法性等规则。本项目参考其可审阅规则思路，不必为了日线人工清单把整个实时框架引入。[RiskManager](https://github.com/vnpy/vnpy_riskmanager) · [RQAlpha](https://github.com/ricequant/rqalpha)

暂停后仍需允许读取和对账。人工执行模式只能阻止系统发布新清单，无法技术上禁止用户在券商软件另行下单；计划外交易必须导入并触发复核。

## 7 回测执行和 harness 的备选

| 环节 | 首版 | 其他候选 | 迁移所需验证 |
| --- | --- | --- | --- |
| 回测引擎 | RQAlpha，先过适配试验 | Backtrader、Hikyuu；vectorbt 用于快速研究；LEAN/Nautilus 作为更广多资产研究方向 | 同一订单时序、数据、费用、公司行动及 golden cases；结果不要求盲目相等，要解释差异 |
| 模拟执行 | 日线 PaperAdapter 复用已验证引擎 | vn.py PaperAccount、券商模拟环境 | 持续状态、回报映射、未成交、T+1、恢复，不能只看是否能下单 |
| 实际执行 | ManualCsvAdapter | xtquant / vn.py 证券 gateway | 真实券商能力、未知状态、报撤单幂等边界、对账 |
| 确定性研究 | CLI + ExperimentSpec + RunResult | Qlib workflow；复杂任务出现后再评估调度框架 | 同样的数据/评估器/记录，不把任务编排等同策略优势 |
| AI 研究 | 暂不选定 | RD-Agent、AgentQuant、AlphaAgent | 权限、预算、训练/封存隔离、标准候选产物、公开评估复核 |

依据：[Backtrader](https://github.com/mementum/backtrader)、[Hikyuu](https://github.com/fasiondog/hikyuu)、[vectorbt](https://github.com/polakowo/vectorbt)、[Qlib workflow](https://qlib.readthedocs.io/en/latest/component/workflow.html)、[RD-Agent](https://github.com/microsoft/RD-Agent)。之前调研的具体许可证和适配边界仍有效，参阅 [调研依据](RESEARCH_DECISIONS.md)。

## 8 怎样实现兼容而不增加首版负担

接口是兼容性的基础，配置只是选择已实现的组件。下面是待实现的配置示意，不是现在可以直接运行的文件：

~~~yaml
mode: paper
data_provider: tushare
market_store: parquet_duckdb
ledger_store: sqlite
engine: rqalpha
execution: paper
strategies:
  - name: etf_trend
    budget: 0.40
  - name: stock_lowvol
    budget: 0.40
risk:
  checks: mandatory_v1
  allocation_policy: fixed_caps
research_runner: local
~~~

这些比例仅用于 paper 演示。manual 模式需要独立实际配置。任何一个名字没有实现，启动时明确报 Unsupported，不能配置了 rqdata 就声称已有该适配。

重点边界如下：

~~~text
DataProvider.fetch(...)                 -> 原始数据批次
MarketDataStore.read(snapshot, asof, ...) -> 标准且时点受限的数据
Strategy.generate(context, data)         -> TargetPortfolio
Allocator.allocate(targets, account)     -> 聚合目标
Planner.plan(target, account, rules)     -> OrderIntent[]
RiskPolicy.evaluate(intents, context)    -> RiskDecision
ExecutionAdapter.publish(intents)        -> 已导出或已提交的状态回报
ExecutionAdapter.collect(...)            -> ExecutionEvent[]
LedgerStore.append_and_project(events)   -> AccountView
ResearchRunner.evaluate(spec)            -> RunResult
~~~

publish 的返回状态必须由 adapter 能力决定：人工只是 EXPORTED，模拟器可以产生 SIMULATED_ACK；两者都不是券商真实确认。账户 ledger 带明确命名空间，模拟事件不能导入 manual 账户。

每次替换执行同一套 contract tests：列名、单位、时区、历史可见范围、稳定 ID、事件去重、事务及恢复。不把相同函数签名当作经济语义兼容。

学习顺序建议：

1. 用 CSV 小样本 + SQLite/Parquet + 买入持有，手算一笔交易。
2. 接 Tushare 和 DuckDB，比较同一查询的输入输出。
3. 加 ETF 趋势与个股低波动，保持引擎和费用不变。
4. 做持续 paper，学习状态、延迟、部分成交和对账。
5. 替换一个组件做对照：例如 SQLite→PostgreSQL，或等权→波动率倒数。
6. 有明确研究问题时，再加入 PIT 财报、Qlib 或 AI harness。

只有第一至第四步属于当前主线。第五、六步是可选学习路线，不自动扩大首版实现范围。
