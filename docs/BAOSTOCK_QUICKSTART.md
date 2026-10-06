# BaoStock 免费数据第一次试用

在仓库根目录运行，无需 Tushare token、真实资金或券商凭据：

```sh
uv sync --frozen --extra baostock
uv run --extra baostock ashare data probe --provider baostock --root runtime/baostock
uv run --extra baostock ashare data sync --config configs/baostock.yaml
```

sync 输出 report、signals、bundle_id。打开输出的 report.html，查看两个策略的月末信号和数据假设。最新位置也记录在 `runtime/baostock/learning-latest.json`。

首次数据范围在 configs/baostock.yaml 明确登记：股票 2025 年起、ETF 2026 年起，截止 2026-09-30。股票采用供应商后复权研究序列，ETF 明确采用未复权价格代理；算法标签为 etf_trend_price_proxy。这里的样本是流程演练，不是选股建议。

本次取得 23 个标的、9,023 条未复权日线。首次网络取数需要逐项请求；再次相同命令复用缓存。需要重新向供应商取数时设置 baostock.refresh=true，每次刷新建立新的 raw/bundle，旧内容保留。

**RESEARCH_PREVIEW_READY 表示真实数据和信号预览就绪，完整交易流程尚未就绪。** 所有输出均为 executable=false。不要把 bundle_id 传给 backtest 或 plan；它不是交易 snapshot_id。

缺失的每日涨跌停、ETF 公司行动完整性、历史退市整理期和引擎公司行动适配仍需补齐。数据空记录不代表“没有事件”；供应商忽略复权参数时，默认验证模式会拒绝。具体发现见 [ADR](adr/0002-baostock-free-learning.md)。

单独获取某个接口的原始响应：

```sh
uv run --extra baostock ashare data fetch --provider baostock \
  --dataset daily --parameters /absolute/request.json --root runtime/baostock
```

request.json 示例：

```json
{"symbol":"600000.SH","asset_type":"stock","start":"2026-09-21","end":"2026-09-30","adjustflag":"3"}
```

支持 daily、calendar、basic、adjust_factor、dividend。输入必须使用规范 symbol（.SH/.SZ），不接收 token/password/API key 字段。请求失败会保存 failure.json；超时退出，已完成缓存可继续使用，无自动换源。

旧版开发/模拟账本不能因安装了数据适配器就自动重解释；原演练仍可使用其原版本，新的离线验证运行 `uv run python tools/verify_delivery.py`，使用代码和锁版本隔离目录。
