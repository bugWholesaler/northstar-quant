# ADR 0001：固定 RQAlpha 窄适配，真实能力分开验收

日期：2026-10-05。状态：接受用于非商业工程试验；真实投资使用的许可适用性未确认。

选择 Python 3.11、RQAlpha 6.1.5，并由 uv.lock 锁定依赖。本次 Mac ARM 安装和股票/ETF 自有 datasource 窄试验通过。对应官方标签为 release/6.1.5，Git 对象 14760d7d31eea3e3091da92da508f2cf256b9764。选择一个实际安装并验证的固定版本，不宣称它是当前最新版。没有改写完整撮合引擎，没有使用默认 bundle 或网络数据兜底。

源码适配引用公共 API，项目没有复制第三方核心源码。RQAlpha LICENSE 对商业用途有额外限制，不把 Apache 元数据当完整许可；本次限于工程/学习合成试验。真实数据投资用途受显式配置门禁约束，未替用户做法律适用性判断。原始许可见 [固定标签 LICENSE](https://github.com/ricequant/rqalpha/blob/release/6.1.5/LICENSE)。

实际命令：

```sh
uv sync --frozen
uv run python tools/spike_engine.py
uv run pytest -q tests/integration/test_engine.py
```

自有两资产试验：2025-04-30 决策，2025-05-02 各买 200 股/份，股票与 ETF 当日卖出被拒；2025-05-05 各卖 100。四笔真实由引擎生成的合成成交，日终现金分别为 10000、5160.18、7564.45；完整逐日记录见 `docs/evidence/engine-spike.json`。价格和日历都为合成，5 月 1 日只是此 fixture 人工登记的休市日。

实测日线 next_bar 被引擎改为 CURRENT_BAR_CLOSE。正式适配请求 current_bar，并将 D 日订单冻结到 D+1 回调提交。改变 D+1 行情不改变 D 信号或首批数量。请求与有效配置分别留档，禁止静默提前成交。ETF 显式设置 market_tplus=1；引擎默认基金可能为 0，不能沿用默认。

自定义 transaction-cost decider 负责分币舍入、按订单累计最低佣金、股票印花税日期分档与情景过户费。撮合前校验不利价格不能越过 bar、限价或涨跌停；成交容量仍由 RQAlpha 处理。每日现金、持仓、净资产与独立 Decimal 账本比较，差异超出浮点换算容差即失败。

能力缺口：公司行动、红利税、送转可卖日期和退市未完成引擎端到端一致性。账本原语与税率函数的单元测试不算引擎验收。当前任何带公司行动的 snapshot、区间内退市均使 run INVALID，保留失败 manifest，不发布业绩。不存在“最后价假卖”兜底。未知接口为 Unsupported。

Tushare token 本次不存在；15 个接口权限、早晚覆盖、分页和实际样本均 NOT_TESTED。实现了显式 raw fetch、分页缓存/重试、刷新批次与 Bar 单位规范化；完整多数据集历史拼接尚未实现。规范 CSV 是当前可运行入口，`historical_full` 必须等待历史股票池完整性审计，不能由 CSV 行齐全自动宣称。

暂不因这些缺口切到另一引擎；它们仍属于适配工作。授权或关键能力确认失败时，再对 Hikyuu 做同场景试验并另写 ADR，不能用自研理想撮合代替验收。
