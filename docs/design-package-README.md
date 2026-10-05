# A 股个人量化系统设计

本仓库当前包含调研和实施计划，尚未实现交易系统。

设计 v1.0 定稿于 2026-10-05。执行 agent 从 [DESIGN.md](DESIGN.md) 开始；用户日常使用看 [每日操作手册](docs/DAILY_RUNBOOK.md)。完整交接文件包位于 deliverables/，不需要附带聊天记录。

用户已确认：首版覆盖 ETF 与个股；生成交易清单后人工执行、导入成交；harness 进一步比较后再决定。

- [可读版 HTML](a-share-minimal-system-plan.html)：首版设计、每日操作、详细规格、备选、任务与调研，离线可读。
- [实现计划](docs/IMPLEMENTATION_PLAN.md)：范围、架构、数据表、接口、回测、执行、风控、账本、运维和验收。
- [执行任务包](docs/AGENT_TASKS.md)：可直接转交的提示词、T00–T10 任务依赖和 G01–G39 验收案例。
- [模拟盘模块与备选](docs/MODULES_AND_ALTERNATIVES.md)：连续模拟盘、模块和关键 Python 文件、数据库/策略/风控等备选，以及学习替换路线。
- [补充调研](docs/RESEARCH_DECISIONS.md)：开源选型、源码发现、数据/harness 比较与未决事项。
- [来源索引](docs/research/sources.json)：可追溯的官方文档和项目链接。
- [GitHub 快照](docs/research/github-snapshot.json)：2026-10-04 的主仓元数据；不代表安装测试。
- 原资料：[交接文档](/Users/jingminhao/quant-projects/HANDOFF.md)、[原调研 HTML](/Users/jingminhao/quant-projects/a-share-quant-guide.html)。

从 T00 和 T01 开始实施。计划内的 ashare CLI 命令是未来验收规格，目前尚不存在。

报告生成脚本在 tools/render_plan.mjs，使用 bundled Node.js 的 marked 包。所有展示检查仅验证文档，不验证策略收益或交易功能。

交接包不包含原始商业研报、付费行情、账户凭据或外部项目源码缓存；研究证据以原始来源链接和元数据保留。源码缓存仅在原工作区供审阅。
