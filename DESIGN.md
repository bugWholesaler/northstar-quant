# A 股个人量化系统首版设计

设计版本：v1.0。设计定稿日期：2026-10-05，时区 Asia/Shanghai。用途：交给执行 agent 实现。状态：设计完成，交易系统尚未实现。本文件与关联规格构成首版设计包，不包含已验证的投资收益。

首版目标是一个可在本地运行的完整闭环：数据更新、ETF 与个股策略、受约束的历史回测、持续模拟盘、人工交易清单、成交导入、账本与对账、风险检查、日报和备份。用户具有专业 Python 能力，希望组件可替换并适合逐步学习。

## 1 执行入口和文档关系

执行 agent 从本文件开始，不需要读取此前聊天。先做 T00 依赖与数据试验、T01 契约，再按任务依赖实现；未得到真实账号或风险预算也应完成可独立运行的部分。

| 文件 | 作用 |
| --- | --- |
| [每日操作手册](docs/DAILY_RUNBOOK.md) | 完成后用户每天具体怎么用，自动与人工各做什么，异常怎么处理 |
| [详细实现规格](docs/IMPLEMENTATION_PLAN.md) | 数据表、时间语义、策略、回测、账本、订单、风险和扩展边界 |
| [模块与备选](docs/MODULES_AND_ALTERNATIVES.md) | 关键 Python 文件、模拟盘细节、数据库/策略/风控等替换方案 |
| [Agent 任务与验收](docs/AGENT_TASKS.md) | T00–T10 任务、依赖及逐项验收案例 |
| [调研依据](docs/RESEARCH_DECISIONS.md) | 已核实事实、源码发现和未验证事项 |
| [来源索引](docs/research/sources.json) | 原始官方文档、GitHub 与研究链接 |

规格冲突时：本文件的首版决策及每日操作手册中的日常流程优先于旧版描述；具体数据字段与技术细节参考详细规格；备选表不意味着首版全部实现。新用户指示优先，安全与工具权限仍按执行环境要求处理。选型变更写 ADR，说明原因及兼容测试，不隐式改变交易或评估语义。

## 2 已确定范围和默认选择

| 项目 | 首版决策 |
| --- | --- |
| 标的 | 境内股票 ETF、沪深主板普通 A 股；首版不新增其他板块/资产的买入支持 |
| 频率 | 日线研究；示例策略月度调仓；每日更新数据和账户状态 |
| 初期运行 | 本地持续模拟盘，使用真实新数据及虚拟资金；无凭据时先完成合成 demo |
| 真实执行 | 用户在券商软件手工下单，系统生成清单并导入券商记录；不做自动实盘 |
| 数据 | Tushare 为第一适配器；规范 CSV 为必备离线入口 |
| 存储 | 不可变 Parquet + DuckDB 查询；SQLite 事务账本，研究/paper/manual 分离 |
| 回测 | 优先 RQAlpha，经 T00 确认安装、许可及行为；适配失败有明确替代评估 |
| 策略 | 被动基准、ETF 120 日趋势前二、个股流动性过滤后 60 日低波动前五 |
| 组合 | 两个策略共享一个账户的现金与限制，统一聚合后规划订单 |
| 风控 | 数据、组合、订单、运行与账户四类检查；配置化阈值，支持持久暂停 |
| 日常入口 | 一个 ashare CLI；OS 调度器仅调用相同入口，手工和定时不得有两套逻辑 |
| 报告 | 本地 HTML + JSON/CSV，最先显示状态、数据日期和用户下一步动作 |
| Harness | 首版为确定性实验运行与记录；AI harness 保留接口，暂不选定 |
| 运行环境 | Mac 优先，Linux 可迁移；不因当前首版先准备 Windows 交易机 |

纸面账户可使用虚拟 20 万元、ETF 40%/股票 40%/现金 20% 的演示配置。它不代表个人实际风险预算。manual 模式不得自动继承这些数值。

首版策略是工程与研究基线，未证明有效；系统验收通过与策略值得投入资金分别验收。数据账号、券商格式、费率、初始账户、个人风控参数及适用报告流程，在相应阶段落实，不凭空补值。

## 3 用户每天得到什么

系统提供一个固定入口 report/latest.html，并保留每个交易日和运行版本的历史报告。用户无需逐个执行几十个脚本。

| 场景 | 用户动作 | 系统动作 |
| --- | --- | --- |
| 模拟盘普通日 | 晚上看日报；状态正常且无待办即可结束 | 更新数据、结算此前模拟订单、更新估值/权益、对账、报告、备份 |
| 模拟盘调仓周期 | 查看新目标及订单原因；默认规则已登记则由模拟器处理后续成交 | 月末盘后形成次日起生效的目标，按日期推进，保留部分成交和偏差 |
| 人工交易普通日 | 提供/核对账户记录，查看异常；无订单时不交易 | 核对现金、持仓、外部资金与公司行动，更新报告 |
| 人工交易执行日 | 检查最新有效清单，执行前核对，手动下单并登记委托号，盘后导入记录 | 生成差额订单、风控、导出、导入去重、三方对账 |
| 异常日 | 按报告定位问题；核对后显式恢复 | 阻止新计划发布，继续允许读数据、导入、对账与诊断 |

正常日不会每天重新挑选最优参数，不会因净值涨跌自动换策略。非月末通常没有新策略目标，但未完成计划、公司行动、外部交易和风险事件仍需处理。具体时间和操作步骤见 [每日操作手册](docs/DAILY_RUNBOOK.md)。

## 4 模块职责与输入输出

~~~text
数据供应商 / 规范 CSV
    ↓ 原始行情、证券状态、公司行动
采集 → 标准化 → 质量检查 → 冻结快照
    ↓ Parquet 保存，DuckDB 查询
时点受限的数据视图 → 策略 → 目标仓位
                                    ↓
已核对账户 → 组合额度 → 差额订单 → 风控
                                    ↓
                   历史回测 / 连续模拟 / 人工清单
                                    ↓
                       模拟或真实成交事件
                                    ↓
                       账本 → 对账 → 日报
                                    ↓
                          下一日账户状态
~~~

回测引擎按历史日期驱动这一流程。模拟和真实账户分别运行，禁止共用一份现金/持仓状态。

| 模块 | 标准输入 → 标准输出 | 关键文件位置示意 |
| --- | --- | --- |
| domain / ports | 原始载荷 → 类型、事件和接口 | models.py、events.py、interfaces.py |
| data | 数据请求 → 标准批次/快照/时点数据 | ingest.py、normalize.py、quality.py、snapshot.py、market_view.py |
| adapters/data | 标准请求 → 供应商响应 | tushare_provider.py、csv_provider.py |
| adapters/storage | 标准数据/事件 → 持久化结果 | parquet_store.py、duckdb_query.py、sqlite_store.py |
| strategies | 可见数据/参数 → TargetPortfolio | buy_hold.py、etf_trend.py、stock_lowvol.py |
| portfolio | 目标/账户/规则 → OrderIntent[] | allocator.py、planner.py、sizing.py |
| risk | 数据/目标/订单/账户 → RiskDecision | engine.py、rules.py、state.py |
| adapters/backtest | 实验规格/快照 → 标准模拟事件 | rqalpha_engine.py、rqalpha_data.py、rqalpha_bridge.py |
| execution | 计划/来源记录 → 执行事件/对账结果 | paper_runner.py、plan_service.py、order_state.py、reconcile.py |
| adapters/execution | 标准意图 → 模拟回报或人工导出 | paper.py、manual_csv.py |
| accounting | 事件 → 现金/持仓/权益/税费 | ledger.py、positions.py、fees.py、corporate_actions.py |
| research / reporting | 实验与账户 → manifest/指标/页面 | runner.py、registry.py、metrics.py、daily_report.py |
| ops / cli | 操作参数 → 幂等任务状态 | daily.py、locks.py、backup.py、main.py |

这些是目录和文件设计，尚未实现。策略只依赖标准数据接口，不直接 import 数据供应商、引擎或券商 SDK；领域定义不反向依赖具体数据库。模块不是微服务，首版一个 Python 包足够。

## 5 接口和数据规则

| 核心对象 | 必须包含 |
| --- | --- |
| Instrument | 稳定内部 ID、市场、证券类型、币种、有效期、代码映射 |
| Snapshot | ID、schema 版本、明确文件清单及 hash、范围、来源、质量结果 |
| TargetPortfolio | 策略版本、decision_at、trade_not_before、expires_at、snapshot_id、权重及原因 |
| OrderIntent | 账户、target/plan/intent ID、方向、数量、价格保护、有效日、依赖、风险结果 |
| ExecutionEvent | 稳定事件 ID、源委托/成交号、市场时间、接收时间、数量/价格/费用、来源批次 |
| AccountView | 总/可用/冻结现金、总/可卖/冻结持仓、权益与负债、时点、核对状态 |
| ExperimentSpec | 策略、代码/依赖、快照、资金、费率、市场规则、风险、成交模型、切分、seed |

金额用 Decimal 计算并按明确精度持久化，现金舍入与证券 tick 分开；数量以股/份计。时间戳带时区，交易日按 Asia/Shanghai 日历。接口及数据都带 schema_version。

行情标准表包括证券版本/代码映射、交易日历、未复权日线、复权因子、历史状态及涨跌停、公司行动、历史股票池和来源记录。账本包括计划、订单、成交、资金事件、公司行动事件、持仓批次、风险、导入批次、对账和作业状态。

历史快照不可覆盖；清洗新版本与源修订产生新快照。信号只能看到决策时点已允许看到的数据；冻结不等于证明历史数据没有修订。历史回补的可用时间推定与真实首次观测必须区分。

研究价格可做复权，成交与账本用未复权价格加真实公司行动，不重复计分红。缺 bar 必须区分停牌、未上市、退市、休市和源缺失；禁止无条件填充后当可成交行情。

数据供应商失败时不静默换源；新增供应商或数据库先过相同契约测试。费用、时间、状态或单位变更必须使缓存失效。

## 6 策略成交与账户时序

ETF 示例：预登记候选池；月末盘后，用当时可见的 120 个交易日收益选正收益前二，每只 paper 目标最多 20%，不足部分留现金。

个股示例：历史沪深主板可投资范围，排除当时 ST/退市整理及上市不足 252 个交易日者；过去 20 日成交额中位数前 300 中，按 60 日收益波动从低到高取五只，每只 paper 目标最多 8%。历史包括后来退市的股票；不足窗口不得补未来数据。

统一时序：

1. D 日已完成的市场与账户事实先入账并核对，再在盘后形成新目标。
2. 新目标最早 D+1 生效，不能回头成交在 D 日。
3. 日线回测和 paper 使用登记的次日收盘附近价格代理；真实人工执行示例窗口为 14:50–14:56，二者不视为精确相同。
4. 执行数量从已知参考价与账户确定，未来成交价不能反向改变此前选股。
5. 买入预算不复用当天卖出资金；次日对账后再产生剩余买单。这是首版保守执行政策。
6. 日内订单仅当日有效；目标可在最多三个交易日的 paper 示例有效期内继续尝试。每次先终结/核对旧订单，再根据真实状态生成新 intent。
7. 期满、用户错过窗口或计划被替代时，旧订单不得直接复用；保留未完成原因。

RQAlpha 的有效配置和代码行为都必须验证；1d 的 next_bar 参数不自动保证次日成交，默认股利税和退市处理不能直接当真实账户结果。具体固定版本来源和适配要求见详细实现规格。

## 7 持续模拟与人工执行

模拟盘使用独立 paper 账户及数据到达记录。优先验证引擎检查点恢复；不支持可靠续跑时，重放已冻结逐日输入和订单，逐项比对历史事件前缀，仅追加新事件。既有事件不因后来数据修订或策略升级被覆盖。

跨日顺序在事件层明确：根据交易日历解锁可卖数量、处理当日适用公司行动，再处理订单和成交，随后按时点估值。若外部信息晚到，按原事件时点重新核对相关计算，保留修订证据；有影响且未解释时阻断新计划。不能只按文件到达顺序生硬更新。

人工清单输出 plan.json、plan.csv、plan.html。导出只代表 EXPORTED，手工提交后登记 SUBMITTED；未知是否提交用 UNKNOWN。撤单请求不是撤单成功，部分成交、晚到成交和费用必须处理。

导入四类记录：委托、成交、资金流水、账户快照。先 preview 校验，按 batch 原子 apply；用符合券商编号作用域的业务键跨文件去重。没有稳定 ID 的歧义需明确处理，不能把两笔相同价格数量的成交误合并。

成交入账、事件去重、账户投影处于同一数据库事务。对账比较订单/成交、现金流水、账户快照；来源事实优先，差异不能靠伪造交易凑平。真实账户需要初始现金/持仓/批次和待处理权益；缺失时标记未知并保守预留。

## 8 风控与可操作状态

无论使用哪种策略、数据库或 harness，必须保留：资金与可卖约束、交易单位和价格合法性、过期/重复/未知订单阻断、关键数据完整性、账户核对。

日报显示彼此独立的状态，不把“数据正常”写成“可以下单”：

| 字段 | 允许的关键状态 |
| --- | --- |
| job_status | SUCCESS、PARTIAL、WAITING_DATA、FAILED、NOOP_MARKET_CLOSED |
| data_status | READY、STALE、INCOMPLETE |
| reconciliation | MATCHED、PENDING、MISMATCH |
| risk_state | RUNNING、REVIEW、HALTED |
| plan_state | NONE、DRAFT、RISK_PASSED、EXPORTED、SUPERSEDED、EXPIRED |

只有对应任务、数据、账户、风险与当日计划都满足要求，manual 计划才能输出“执行前检查通过”。它不是成交保证，执行前仍核对券商最新行情与账户。

暂停跨重启保留。恢复须修复原因、核对账户与订单、更新数据、作废旧计划并记录操作人及原因。允许导入、读取和核对；禁止按坏数据盲目清仓。风险减少订单也要检查数量、价格与可卖，不获得绕过账本的特权。

## 9 日常入口和失败语义

下面是待实现命令协议，当前不可运行。高层命令应调用已经定义的低层数据、计划、导入和对账功能，不产生第二套业务逻辑。

~~~sh
uv run ashare status --account paper
uv run ashare daily run --account paper --date YYYY-MM-DD
uv run ashare report daily --account paper --date YYYY-MM-DD

uv run ashare status --account manual
uv run ashare plan preflight --plan PLAN_ID
uv run ashare plan export --plan PLAN_ID
uv run ashare order register --intent INTENT_ID --broker-order-id BROKER_ID
uv run ashare import preview --account manual --mapping configs/brokers/manual_csv.yaml --file FILE
uv run ashare import apply --batch BATCH_ID
uv run ashare reconcile --account manual --date YYYY-MM-DD
uv run ashare daily run --account manual --date YYYY-MM-DD
~~~

daily run 先解析账户模式和交易日，拿锁并恢复未完成批次；获取当天输入，按事件顺序更新当前账户并核对，最后才生成未来目标和计划，再报告与备份。paper step 是同一编排在 paper 模式下的兼容入口，不能另做一套 daily 逻辑。

状态输出必须写明截至哪一交易日。昨天成功的页面不能在今天任务缺失时继续显示“今天正常”。数据或交割单未齐返回 WAITING/PENDING，报告继续生成但无新的可执行计划；重试不能重复入账。作业运行中断用持久 checkpoint/批次登记恢复，不自动重发未知订单。

休市日返回 NOOP_MARKET_CLOSED，允许核对、备份和研究。禁止把当前墙上日期当作交易所开市证据。启动有副作用的模拟/人工计划阶段前校验时间；不能把收盘后才出现的计划倒填为收盘前订单。

## 10 验收里程碑与交接证据

1. M0：T00/T01 完成，环境、许可/能力边界、契约和最小引擎案例有记录。
2. M1：无凭据的合成 demo，一条入口跑过数据、策略、风控、部分成交、账本、对账和恢复。
3. M2：真实历史数据，ETF/个股策略和基准有完整运行证据、成本/延迟压力结果。
4. M3：持续 paper，新数据逐日进入；连续运行与中途重启等价，用户按每日手册独立使用。
5. M4：人工流程可用，券商导出映射、费率、账户与个人预算落实；用户明确启用具体版本。没有条件时可以完成前四阶段，M4 明确未验收。

验收用例按 Agent 任务包执行，报告逐项写 PASS / FAIL / NOT_TESTED。自动交易、财报模型、AI harness、大规模分钟数据和复杂 UI 不属于首版。

最终代码交付应包含：README 安装和最短启动、锁文件、配置模板、脱敏 fixtures、迁移脚本、全部验收命令与结果、运行报告、已知限制、数据字典、ADR 和恢复手册。不能仅交 notebook 或收益图。

可直接发送给执行 agent：

> 请读取 DESIGN.md，按 docs/AGENT_TASKS.md 实现首版，以 docs/DAILY_RUNBOOK.md 的用户流程验收。先完成 T00/T01，再打通离线 demo 和持续 paper，保留人工清单/导入/对账。默认方案按设计实现，备选只保留接口，不要首版全部安装。需要 token 或券商记录的部分明确记录待验收，同时完成独立工作。当前任务不授权自动下真实订单、采购数据或替用户决定实际风险预算。每阶段保存代码、数据版本、命令、输出和测试证据，不能把文档、合成数据或 README 示例当作投资效果验证。
