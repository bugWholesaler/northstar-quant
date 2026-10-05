# v0.1 数据与导入字典

金额为 CNY 十进制字符串，数量为股/份整数，证券代码为六位代码 + `.SH` / `.SZ`，交易所为 XSHG / XSHE。时间必须带时区，内部归一为 Asia/Shanghai。未知 schema 大版本、NaN、负数量、错误代码映射和不支持板块被拒绝。外部契约见 `schemas/`。

## 市场规范 CSV

目录需包含四个文件。所有完整字段见 `fixtures/synthetic/` 表头及 domain models；空 actions 必须保留表头。schema_version 为 1.0。

| 文件 | 主键 | 核心要求 |
| --- | --- | --- |
| instruments.csv | instrument_id | symbol、exchange、asset_type stock/etf、board MAIN/EQUITY_ETF、上市/退市日期、tick、lot、CNY、T+1；v0.1 内部 ID 等于规范代码，历史代码变更未支持 |
| calendar.csv | exchange + trade_date | 两个交易所，每个自然日，包括 is_open=false 的休市日；不能以 weekday 代替真实交易日历 |
| bars.csv | instrument_id + trade_date | 未复权 OHLC、volume 股/份、amount 元、factor、suspended、st、delisting_period、limit_up/down、available_at、observed_at、availability_basis |
| actions.csv | action_id | dividend/bonus/delisting/unsupported、登记/除息/到账/可卖日期、税前每股现金及送转比例、实施标志及三种信息时间 |

OHLC 区间、量额单位量级、主键、日历、上市期覆盖、因子跳变关联公司行动都经过校验。停牌需显式状态行，volume=0；不能把缺数据当停牌。策略窗口遇停牌/缺期不新买入。snapshot 按内容哈希发布，文件哈希在 manifest 中；重拉修订要新 ID，旧快照不变。

历史回补 `availability_basis=historical_assumption`；paper 必须使用真实 `observed`，且 available_at 不早于 observed_at。本仓库 fixture 的历史及观测时间均为人工构造测试值。冻结保证可重放，不能证明供应商没有历史修订。`real_sample` 只代表真实样本；`historical_full` 当前阻断。没有历史全市场证券版本证据时不能做全市场收益声明。

## 券商规范 CSV

每行必填 account_id、mode，两者必须与选定隔离账户匹配。所有文件的 `trade_date` 为 ISO 日期；event_time 带时区并与交易日相符。UTF-8/UTF-8 BOM 与 GB18030 可读取。broker mapping 声明 schema_version、broker、business_id_scope=account_date_exchange 及源字段→规范字段的 columns。其它编号作用域须先实现适配，不能直接套用。

| kind | 必填字段（通用字段之外） |
| --- | --- |
| orders | exchange、symbol、side BUY/SELL、quantity、limit_price、fee_reserve、broker_order_id、state、filled_quantity；intent_id 可选 |
| fills | exchange、symbol、side、quantity、price、gross_amount、commission、tax、other_fees、broker_order_id、broker_trade_id；买入必填 sellable_on |
| cash_movements | movement_id、kind、amount；kind 为 Deposit/Withdrawal/FeeAdjusted/DividendTaxPaid/CashRecovery；reference 可选 |
| account_snapshot | asof、cash_total、cash_available、cash_frozen、receivables；有持仓时 symbol、exchange、quantity、sellable、frozen；无持仓用一行空 symbol |

snapshot 的每行现金口径和 asof 必须相同；仓位代码不得重复。所有费用必须明确，空值不会当 0。成交 gross 必须与价格乘数量的分币结果一致。成交去重为账户+交易日+市场+成交编号，而非文件 hash 或价格数量；同业务 ID 的冲突拒绝整批。相同价格数量、不同成交编号仍是两笔。

原件、mapping 和 preview 在 `runtime/.../imports/ACCOUNT/BATCH/` 保存，apply 前验证证据 hash。preview 不改现金和持仓；apply 使用单事务，失败整批回滚。没有成交表时，订单“已成交”不会制造成交；UNKNOWN 保留冻结。导入成交后的同订单累计量需与最终订单回报核对。费用晚到通过 FeeAdjusted，未知差异不能自动凑平。

## 初始持仓与预检

initial-lots.json 是列表，每项为 symbol、quantity、sellable_on、cost、acquired（可空）。未知买入日期保留 tax_basis_known=false，不能编造持有期。v0.1 没有自动的应税权益批次归因，实际红利税以证据导入，模拟端相关公司行动仍被阻断。

预检使用 `configs/live-check.example.json`：account_hash 从当前账户视图取得，checked_at 为实际核对时刻；每个证券需 price、limit_up/down、suspended、st、delisting_period、sellable。缺字段拒绝，5 分钟内且 14:50–14:56 执行窗口才可通过。预检和导出不连接券商，也不声称完成自动实时风控。

manual 不能仅翻转 verified/approved 开关：费率（佣金率、最低佣金、股票其它费用及日期税率）和风险（总/单券比例、预留、流动性、价格、回撤）需显式填写，并换成确认后的版本名。REQUIRED、scenario、paper 示例版本不通过。v0.1 佣金模型对股票/ETF 共用一组佣金参数，实际券商若不适用该结构，必须扩展并测试，不能勉强设为 verified。合成 snapshot 不能导出 manual 清单。
