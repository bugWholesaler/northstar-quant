# v0.1 运行与恢复手册

原 DAILY_RUNBOOK 是完整设计规格，本文件描述已经实现的行为。先运行 README 的 demo，再查看 IMPLEMENTATION_STATUS 中尚未完成的门禁；不要把工程演练当成真实前瞻模拟。

## 平时使用

1. CSV 目录持续放入规范新数据，并在 configs/paper.yaml 配置 csv_directory。真实观测时间必须保留；许可及数据能力验收独立完成。
2. 当天交易日 21:30 上海时间后运行 `uv run ashare daily run --account paper --date YYYY-MM-DD`。账户锁串行写入，先结算冻结旧单，核对引擎/账本，再生成未来目标，最后报告和备份。
3. 打开 runtime/paper/report/latest.html。分别看 job_status、data_status、reconciliation、risk_state、计划有效日和待办委托。昨日成功不能证明今天正常；`ashare status --account paper` 会生成当前日期状态及 TASK_MISSING 页面。
4. WAITING_DATA 补数据后重试；PENDING/MISMATCH 导入完整订单、成交、现金和同口径账户快照再核对。没有新单的普通日仍运行。

没有 csv_directory 时可传 `--snapshot ID`；初次没有输入返回 WAITING_DATA，不推断日历或切换供应商。已有快照数据不覆盖今天则同样等待。休市依据快照日历，返回 NOOP_MARKET_CLOSED，不创建成交。

## 部分成交与未知委托

使用 `ashare import preview --kind ... --file ... --mapping ... --account ...`，检查输出；再 `ashare import apply --batch ... --account ...`。两次部分成交分别导入；合并导出文件再次导入仍去重。最终订单回报的 filled_quantity 与成交累计量一致，再导入 account_snapshot 并 reconcile。

导出只产生 EXPORTED。手动提交后用 order register 记录 broker_order_id；不知道是否提交则 `--state UNKNOWN`。先在券商终端核查委托及成交，再导入证据；空 CSV、重启、没有成交均不能证明没报单。撤单请求登记 CANCEL_PENDING，撤单成功有回报后才可记 CANCELLED；期间成交照常入账。旧清单被账户变化作废，不可继续使用纸质旧版本。

## 暂停与恢复

```sh
uv run ashare risk halt --account paper --reason '需要核查的实际原因'
uv run ashare status --account paper
# 继续补数据、导入和核对，不会自动撤掉券商订单。
uv run ashare reconcile --account manual --date YYYY-MM-DD --operator NAME
uv run ashare risk resume --account manual --operator NAME --reason '修复和核对证据'
```

HALTED/REVIEW 不随重启或午夜恢复。resume 必须账户 MATCHED、无 UNKNOWN，并记录人和原因；作废旧计划。实际计划随后仍要重新做数据、账户和当前行情预检。暂停期间允许导入；没有任何“全部自动撤单”或自动清仓功能。

## 备份恢复演练

```sh
uv run ashare backup create --account paper
uv run ashare backup verify --backup /absolute/runtime/backups/paper/BACKUP_ID
uv run ashare backup restore --backup /absolute/BACKUP_DIRECTORY --root /absolute/fresh-runtime
```

备份用 SQLite Backup API，包含账本和引用的原始导入、快照、配置、计划及逐日模拟记录。verify 检查每个文件 hash、SQLite integrity_check、事件数及重放投影。restore 必须进入全新目录，不覆盖已有账户。切换配置 runtime 后重新运行 status；保存的已应用 import 批次重复 apply 为零新增。

本版本采用完整本地副本，真实大数据分区共享/增量备份未优化；离机副本与加密存储尚未配置。不得把正在写入的 SQLite 主文件直接复制当完整备份。人工账户遗失记录须从券商原件恢复，不能猜成交。

## 中断和修订

每日 job 与输入 hash 持久保存；计算失败显示 FAILED。事件、投影、新计划、冻结下一日订单和成功 checkpoint 事务提交；重试验证已有事件前缀。过往快照修订使持续模拟暂停；旧事件不覆盖。错过交易日不追溯成交，需要复核，历史补做用 research。配置/代码改变导致已冻结历史重放不等价时，也不会悄悄重新定义过去。

没有 token 时运行 data probe 保存 NOT_TESTED。真实公司行动、退市或未支持接口使实验 INVALID，失败 manifest/report 仍留存；不能从失败结果中取收益曲线。

## OS 调度

`tools/daily.sh` 是 launchd/cron 可以调用的同一 CLI 入口，需要配置实际 repo、config 和 uv 路径。提供 `configs/launchd.example.plist` 作为模板，未安装到用户的 LaunchAgents；本机 sleep/漏跑仍通过 status 和交易日连续性检查暴露。调度模板本身不表示真实连续观察已验收。
