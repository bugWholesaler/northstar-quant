# 免费真实数据回测：首次研究情景

更新：2026-10-06。已实际运行 2026-01-30 至 2026-09-30 的 162 个交易日，不使用默认 RQAlpha bundle，不连接真实账户。数据为固定当前 20 股/3 ETF 的条件样本，不是全市场或干净样本外验证。

```sh
uv sync --frozen --extra baostock
uv run --extra baostock ashare research prepare-free --config configs/free_backtest.yaml
# 从上一条输出复制 SNAPSHOT_ID
uv run ashare research trial-free --config configs/free_backtest.yaml --snapshot SNAPSHOT_ID
```

第二条获取/复用源缓存并冻结研究情景 snapshot；第三条运行 11 个预先固定的策略/基准/成本/延迟/本金/税务案例。报告默认位于 runtime/free-backtest/trial-report/report.html，PNG/SVG 图与完整 JSON 记录在旁边。各运行目录保存订单、成交、事件、逐日现金/持仓/净资产比较、指标、请求/有效配置和代码身份。

只跑一个策略：

```sh
uv run ashare backtest --config configs/free_backtest.yaml --snapshot SNAPSHOT_ID \
  --start 2026-01-30 --end 2026-09-30 --strategy combined --split development
```

策略可选 combined、etf_trend、stock_lowvol、combined_passive。股票单独使用 configs/free_stock_backtest.yaml；prepare-free 返回其专属 snapshot，不能把两种配置/快照混用。

## 本次效果

20 万元情景本金，交易成本后、但尚未完整计入 ETF 现金分红的回报代理：

| 策略/情景 | 区间回报代理 | 最大回撤 |
| --- | ---: | ---: |
| ETF+股票组合 | -0.10% | 8.83% |
| ETF 单策略 | -1.88% | 10.32% |
| 股票低波动单策略 | +3.66% | 3.95% |
| 同样本 ETF/股票等权被动基准 | -0.07% | 5.93% |
| 组合成本 2 倍 | -0.23% | 8.89% |
| 组合成本 3 倍 | -0.51% | 9.01% |
| 组合额外延迟一天 | -1.55% | 9.12% |

组合未显示清晰超额。股票单策略与 ETF 单策略各最多投入 40%，组合和被动最多 80%，不能只按收益大小断言谁更优。该样本偏向现存大盘股票，尤其银行，历史选择/幸存者偏差尚未消除。ETF 单策略触发回撤复核后不再增加风险，也影响了路径。

## 明确的模型与数据边界

- 股票来自 BaoStock 未复权 OHLC、量额、ST/停牌、主表、实施现金分红；研究信号价格使用供应商后复权序列。
- ETF 来源明确登记为腾讯行情，量由手转股/份、额由万元转元。返回可能超出查询区间，严格按已登记日期过滤；字段/覆盖/复权不齐则失败。东方财富先前的成功样本和后续网络失败保留，不进行自动换源。
- ETF 后复权价格用于信号；执行/估值仍用未复权价格。ETF 现金分红尚未完整入账，因此 ETF/组合结果称回报代理，不能当实际投资总收益。
- 涨跌停参考价/上下限按供应商参考价格与日期/资产规则推导，尚未逐日对照官方限价；腾讯 ETF 基准价按前日后复权价转换为当日价格基准。输入标为 research_scenario / SCENARIO_DERIVED，不能进入 paper/manual/preflight。
- 已支持研究情景中的实施股票现金分红：登记日数量、次一交易日除息、到账日期分别处理；分币舍入与 RQAlpha 逐日核对。股息税固定 20% 立即预扣是保守情景，不是实际持有期批次税务；零税另列敏感性，不修改真实账户费率。
- 非相邻登记/除息日、送转、配股、清算或退市仍 Unsupported/INVALID。不能用最后价假卖掩盖。
- D 信号最早 D+1 收盘代理成交；额外延迟压力保持原冻结订单，不每天推迟。原延迟调度缺陷的零成交旧运行已通过 invalidated.json 标记无效并保留，不用于结果。
- 情景佣金万三、最低 5 元、日期印花税与其它费用，滑点 5 bp；这些不是用户券商的确认费率。
- 当前用途登记为非商业研究，真实投资用途许可、个人参数和券商入口仍未确认。代码不会因该声明启用 paper/manual。

本次只评估既定策略，没有优化参数，也不能据此推断未来赚钱能力。下一步应补齐 ETF 股利与真实每日交易限制，再扩大完整历史样本并进行前瞻观察。
