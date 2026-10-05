# A 股系统补充调研与选型依据

调研日期：2026-10-04。本文补充原 HANDOFF.md 和 a-share-quant-guide.html，重点是从“候选工具介绍”推进到“可以验证的工程决策”。原报告保留在 /Users/jingminhao/quant-projects。本轮没有安装框架、购买数据、执行策略或交易。

证据分四种：官方文档事实、已读的固定版本源码、原调研已核实但本轮未重读的材料、本项目的设计判断。GitHub star 只是采用和关注的信号，不证明代码正确或策略获利。

## 1 相对原调研补充了什么

| 原调研已有 | 本轮新增 | 对实现的改变 |
| --- | --- | --- |
| 建议 ETF 优先 | 用户确认 ETF 和个股首版都要 | 个股历史状态、税务批次和公司行动变为首版要求 |
| Parquet + DuckDB + SQLite | 查询与事务的并发边界、manifest 和不可变分区、恢复验证 | 多 agent 研究不能并发写同一个交易库 |
| 优先评估 RQAlpha | 核对当前日线撮合、红利税和退市默认行为 | 先做窄集成试验，再以明确 Mod 适配 |
| 支持人工执行 | 区分导出/提交/成交/未知，四类 CSV 与三方核对 | 人工执行仍有 OMS、幂等和状态机 |
| Tushare 候选 | 历史 ST、股票列表、ETF 涨跌停、分红字段和更新时间 | 按整个策略的数据需求购买与验收 |
| 未来避免过拟合 | holdout 权限、完整实验列表、候选包标准 | 以后换 harness 不重写账户与评估流程 |
| 模块化方向 | 10 个核心边界和版本化载荷 | 替换数据商/策略/引擎有具体迁移路径 |
| 通用实施步骤 | T00–T10 任务依赖、G01–G28 验收 | agent 可逐项交付和报告未验证事项 |

## 2 框架比较

快照来自各主仓 GitHub API，完整 metadata 与 commit 在 research/github-snapshot.json；这是读取仓库时的默认分支快照，不是已验证的安装版本。

| 项目 | 关注规模与快照 | 适合复用的部分 | 首版取舍 |
| --- | --- | --- | --- |
| ricequant/rqalpha | 6,807 stars；0d98ade；2026-09-24 提交 | A 股事件回测、账户、Mod、自有数据源 | 优先验证，许可和交易语义必须处理 |
| vnpy/vnpy | 45,682；f6e3035；2026-10-04 | OrderData/TradeData、OmsEngine、gateway 分层 | 借鉴边界；人工执行首版不引入 GUI 和多个常驻交易模块 |
| microsoft/qlib | 49,124；be72549；2026-09-16 | 因子与模型工作流、配置、记录器 | ML/harness 扩展首选之一，非人工交易账本核心 |
| microsoft/RD-Agent | 14,836；484776c；2026-09-23 | 自动研究和候选生成，与 Qlib 场景相连 | 后续研究环境，不承担交易执行权 |
| QuantConnect/Lean | 21,854；705b955；2026-10-02 | 模块化事件引擎、现实模型抽象 | 全球多资产扩展候选；对本次 A 股低频任务的适配与 C#/.NET 栈成本较高 |
| nautechsystems/nautilus_trader | 29,609；2c319bc；2026-10-04，develop | Rust/Python 事件与回测/实盘结构 | 值得借鉴；本轮未核实可直接覆盖国内券商及全部 A 股规则 |
| fasiondog/hikyuu | 3,543；daeb2c7；2026-09-30 | C++/Python 量化研究与回测 | RQAlpha 不适配或授权不合适时的候选，仍需同一组验收 |

来源：[RQAlpha](https://github.com/ricequant/rqalpha)、[vn.py](https://github.com/vnpy/vnpy)、[Qlib](https://github.com/microsoft/qlib)、[RD-Agent](https://github.com/microsoft/RD-Agent)、[LEAN](https://github.com/QuantConnect/Lean)、[NautilusTrader](https://github.com/nautechsystems/nautilus_trader)、[Hikyuu](https://github.com/fasiondog/hikyuu)。

原报告里的 vectorbt、Backtrader、backtesting.py 继续可作研究候选。本轮不重复列长榜单：向量化探索不能替代 A 股交易限制，通用引擎需要证明其市场适配，换引擎本身也解决不了历史数据和人工对账问题。这是针对当前需求的取舍，不是项目质量排名。

为何不直接选一个全包框架：你的数据源、harness 和策略都会迭代，而人工成交需要从券商事实重建账户。把这些核心状态绑在某个框架的 context 中，会使一次框架迁移变成重写整套系统。因此使用成熟引擎计算仿真，同时维护少量业务契约与实际账户账本。

## 3 读源码后需要写入验收的事项

### 3.1 RQAlpha 的日频撮合

在已读取 commit 中，sys_simulation/mod.py 的 60–63 行把 1d 下的 NEXT_BAR_OPEN 改为 CURRENT_BAR_CLOSE，并发出警告。API 参数名字不足以保证执行时间顺序。必须存储前一日目标、下一日才加载执行，并断言实际成交日。[固定版本源码](https://github.com/ricequant/rqalpha/blob/0d98adefa87956e26f3e7ca5b26b5f3fc7ca834f/rqalpha/mod/rqalpha_mod_sys_simulation/mod.py#L60)

### 3.2 账户默认项不等于个人实盘口径

sys_accounts 的配置默认 dividend_tax_enabled=False、cash_return_by_stock_delisted=True。源码中已有股利税与账户处理可复用，但不能把默认回测当作已扣个人税、真实退市回收的收益。计划要求逐项验收，而非自行重写全部账户。[配置源码](https://github.com/ricequant/rqalpha/blob/0d98adefa87956e26f3e7ca5b26b5f3fc7ca834f/rqalpha/mod/rqalpha_mod_sys_accounts/__init__.py) · [账户源码](https://github.com/ricequant/rqalpha/blob/0d98adefa87956e26f3e7ca5b26b5f3fc7ca834f/rqalpha/mod/rqalpha_mod_sys_accounts/position_model.py)

### 3.3 接入 Tushare 不只是一段 K 线代码

官方扩展文档提供 DataSource/Mod 的入口，并有旧版 get_k_data 的例子。本项目还要映射证券定义、日历、状态、分红、送转和日期边界；示例不是当前 Tushare Pro 的完整市场数据适配器。[扩展数据源](https://rqalpha.readthedocs.io/zh-cn/latest/development/data_source.html)

### 3.4 许可证信息要读完整文本

该 commit 的 pyproject metadata 与 LICENSE 的使用边界不能混为一谈；LICENSE 对商业用途另有限制。本文不判断个人真实投资必然符合非商业用途。实施 agent 需记录确认结果或替代方案，不能复用未知授权代码。[LICENSE](https://github.com/ricequant/rqalpha/blob/0d98adefa87956e26f3e7ca5b26b5f3fc7ca834f/LICENSE)

### 3.5 vn.py 结构可借鉴但不自动等于持久账本

已读 OmsEngine、BaseGateway、OrderData、TradeData。它们支持本项目对事件、查询、订单与成交分离的设计。是否跨重启完整持久化、是否满足券商 CSV 对账，要由具体应用层实现并验证，不能从“有 OMS”推导出“账就不会错”。[engine.py](https://github.com/vnpy/vnpy/blob/f6e303547b2de75a3a5239c9ec9b9c739656ec8d/vnpy/trader/engine.py)

以上源文件保存在 research/source-cache，仅供审阅。缓存不是本项目运行依赖；不直接把源文件复制进产品冒充自研。

## 4 数据调研的新结论

Tushare 对首版价格类策略值得优先试验，但“API 存在”不能替代历史全覆盖验收。该选择基于公开接口、预算和你较强的 Python 能力；若整理历史状态耗时很高，付费使用更完整数据可能更划算。

值得特别注意的官方字段：

- daily 的 pre_close 标注为除权参考昨收，不能直接等同上一行未复权 close；vol 是手、amount 是千元；当前文档还提供盘后成交量/额字段。应确认总量与盘后量的包含关系，不能重复相加。[股票日线](https://tushare.pro/document/2?doc_id=27)
- dividend 同时提供 cash_div（税后）与 cash_div_tax（税前），并区分 ex_date、pay_date、div_listdate。采用税前值再按账户计算税时，不得误用税后值重复扣税；每日更新时段写为 20–21 点，日常任务应等待相关数据齐备。[分红送股](https://tushare.pro/document/2?doc_id=103)
- bak_basic 文档起点为 2016 年；其中包含的财务衍生字段不能因为与历史日期同表就自动视为完整 PIT 财报。[股票历史列表](https://tushare.pro/document/2?doc_id=262)
- stock_st 按日期提供历史列表，但提醒过早历史无法补齐，并给出约 09:20 的当日更新时间。历史查询能力与真正保留全部公告修订仍是两回事。[ST 列表](https://tushare.pro/document/2?doc_id=397)
- etf_limit 已有独立接口，不必机械把股票涨跌停接口套到所有 ETF；历史起点和终止上市 ETF 覆盖仍需 probe。[ETF 限价](https://tushare.pro/document/2?doc_id=491)

RQData 的 get_pit_financials_ex 带 date 与 statements，可用于按查询时点和报表版本取值；这为后续质量/价值因子提供更合适的调查入口。具体数据覆盖、授权与报价尚未验证。[RQData](https://www.ricequant.com/doc/rqdata/python/stock-mod.html)

AKShare 继续适合抽样核对或学习；本轮不建议它成为关键实盘数据唯一来源。免费源可以有用，关键是失败/修订/单位/许可有明确边界，而不是把“免费”当稳定性承诺。

个人数据预算建议拆成三个情景而非先花满：已有机器 + 日线试验约按官方 500–1,000 元年费档探测；PIT/更完整服务询价后再决定；分钟数据仅在执行误差妨碍判断时购买所需范围。首版人工执行无需先买 Windows 机器。20 万本金上每年 3,000 元固定费相当于 1.5%，应单独显示。

## 5 为什么是 Parquet DuckDB SQLite

本项目选型的依据是访问模式：行情和因子是批量扫描、可重建，账户是小量事务、必须可靠。前者使用列式文件，后者使用事务库。不需要把所有东西塞进同一种数据库。

DuckDB 的本地进程内模式有单进程写入边界；官方也介绍新的多进程方案，但个人日线首版没有引入其复杂度的收益。SQLite WAL 允许读写协作但一次仅一个写者，且要求同机共享内存。本设计因此将研究并行与账户写入分离。[DuckDB](https://duckdb.org/docs/current/connect/concurrency) · [SQLite](https://www.sqlite.org/wal.html)

| 未来变化 | 怎么迁移 | 不需要改的部分 |
| --- | --- | --- |
| Tushare → RQData | 新 provider，标准表对齐、重叠样本差异报告、新 snapshot | 策略、账本、风险与标准报告 |
| 本地 Parquet → 对象存储 | Snapshot resolver 改 URI 与缓存；保持文件哈希和 schema | 策略与实验 spec |
| SQLite → PostgreSQL | LedgerStore 新实现、迁移事件并校验投影，改变并发模型 | 订单/成交事件语义 |
| RQAlpha → 其他引擎 | BacktestEngine adapter，复跑 golden suite 和同源对照 | 策略/Planner/风险契约；历史 run 保留旧环境 |
| 规则策略 → Qlib 模型 | 固定训练制品，转标准目标/信号，进入同一评估器 | 实际账户与人工执行 |
| 手工 CSV → xtquant | 增加券商查询和回调适配器及 UNKNOWN 恢复 | OrderIntent、事件账本和对账规则 |
| CLI → 新 harness | 新 CandidateArtifact/ExperimentSpec 生产者 | 公共评估入口和上线门槛 |

迁移前后收益差异需要解释，不以“接口能调用”作为兼容成功；例如公司行动、收盘时间和费率口径的变化都可能改变结果。

## 6 Harness 如何进一步比较

Qlib 的 qrun 已提供数据处理、模型训练和评估的配置式工作流及产物跟踪，适合未来 ML。官方示例资金为 1 亿元、组合约 50 只，不能直接作为你的 10–30 万执行配置。[官方 workflow](https://qlib.readthedocs.io/en/latest/component/workflow.html)

RD-Agent 的当前 README 说明 Linux 支持要求，多数场景需 Docker；量化路径明确与 Qlib 相关。因此 Mac 上的首版交易系统不应强制依赖它。先在单独环境研究，再将候选送回公共评估入口。[主仓](https://github.com/microsoft/RD-Agent) · [场景](https://rdagent.readthedocs.io/en/latest/scens/quant_agent_fin.html)

AgentQuant 有失败记忆、研究运行记录与显式切分的工程思路，但其 README 也明确部分优化结果基于 mock fitness。可借鉴机制，不能当作已通过实盘检验的量化基础设施。[主仓](https://github.com/OnePunchMonk/AgentQuant)

AlphaAgent 当前主仓、论文版本与数据层授权需要一起审查。原调研已阅读部分 Tushare 财务展开代码，本轮未将该代码跑过，也未因论文表现给出高可信背书。QuantHarness 则偏高频价格分析，与当前月度调仓需求不匹配。[AlphaAgent](https://github.com/RndmVariableQ/AlphaAgent) · [QuantHarness](https://github.com/Y-Research-SBU/QuantHarness)

后续试验设计建议：

| 维度 | 统一测试方法 |
| --- | --- |
| 数据接入 | 同一 snapshot、同一可见字段，严禁各工具默认下载不同源 |
| 决策质量 | 10 个预登记研究任务，包括“应发现无效”的负对照 |
| 评估一致 | 相同本金、费用、延迟、候选池与风险；公共引擎复核 |
| 复现 | 相同候选代码/数据/配置重复运行，比较目标、成交、指标 |
| 搜索污染 | 提案只看开发/验证反馈，封存集单独解锁 |
| 成本 | API 费用、总耗时、人工修改次数、失败后能否续跑 |
| 权限 | 无账户 token、无法改真实账本或发布交易计划 |
| 可退出 | 产物能导出为规范策略/因子包，不被工具私有数据库锁住 |

不必为了比较先搭四套完整系统。先实现统一评估入口，再挑 Qlib/RD-Agent 一条路径和另一候选做窄试验即可。

## 7 研究报告怎样为策略提供依据

原报告已核实的海通 ETF 风格轮动研报可以继续作为“将观点翻译成规则”的案例；本轮重访 PDF 未成功，没有复核其原报收益。它使用宏观和中观信息，若照搬会引入发布时间、修订与数据成本，因此没有作为第一条必须复现的策略。[海通原始材料](https://www.htsec.com/jfimg/colimg/upload/20240430/1714455637218041379.pdf)

本轮读取了 AQR 的趋势与低 beta 研究介绍、回测过拟合论文，以及 RD-Agent-Quant 摘要。它们分别支持研究动机、实验纪律和自动研究范式，不支持本项目的参数或收益承诺。[趋势](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum) · [低 beta](https://www.aqr.com/Insights/Research/Journal-Article/Betting-Against-Beta) · [回测过拟合](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) · [RD-Agent-Quant](https://arxiv.org/abs/2505.15155)

每次借鉴研报，执行 agent 应输出六项：经济假设、可观测数据、信息可用时刻、机械规则、交易成本/可成交假设、发布后的检验区间。无法把这六项写清楚，就先作为研究线索，不直接进入策略库。

## 8 交易接入的后续路线

当前用户选择人工执行，所以首版只研究自动接入边界，不开通或连接账户。xtquant 官方文档提供报撤单、资产/持仓/订单/成交查询及回调，这与标准 ExecutionAdapter 的未来实现相符。[迅投官方文档](https://dict.thinktrader.net/nativeApi/xttrader.html)

SDK 的异步请求序号、券商委托编号和本系统 intent_id 是不同对象；网络超时无法天然保证 exactly-once。未来自动接入必须保留本地意图→提交→查询确认→未知恢复流程，不能调用异常就再次下单。通过 vn.py 接 gateway 也一样。

是否支持 Mac/Windows、账户资产门槛、模拟环境、ETF 委托类型和当前 SDK 版本要向实际券商确认。不要因为网上存在 Python 示例就把这些条件视为满足。

## 9 尚待实际执行核验

| 未决项 | 谁在何时解决 | 对当前工作的影响 |
| --- | --- | --- |
| RQAlpha 安装/版本/真实投资授权 | T00，实施 agent 与用户按需确认 | 影响引擎最终选择，不影响业务契约 |
| Tushare 实际权限与历史覆盖 | T00/T02，用户账号下 probe | 无 token 仍可实现 CSV 与合成闭环，真实数据验收不可通过 |
| 历史公司行动、退市、ETF 元信息缺漏 | T02/T04 | 缺失区间或事件不能发布正式业绩 |
| 用户券商导出格式 | T06，取得脱敏样例 | 先实现通用 CSV；真实账户导入尚未完成 |
| 个人仓位/回撤预算 | M4 前由用户确定 | paper 参数不能自动进入 manual |
| 手工操作时间是否匹配 14:50 窗口 | M4 前确定 | 若改变执行政策，必须重跑压力和误差评价 |
| 数据授权是否允许上传给第三方模型 | AI harness 试验前 | 默认只在本地使用，不能把付费行情当公共数据发出 |
| 策略有没有可实施优势 | M2–M4 研究与前瞻 | 本次没有实证答案，不影响工程系统单独验收 |

与原报告相比，本轮完成了更多接口和源码层核查，但仍然是设计与调研完成，不能写成投资系统已部署。
