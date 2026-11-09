STATUS.md — 跨 session 运行记忆

每次完成工作后更新此文件（直接更新，无需确认）。每条记录带精确时间，只写一句话结论，不写过程。最后更新：2026-11-09 CST

## TODO

- 加入其他信号指标
- HSTECH 报告里估值指标仍标注为「ERP」，应为「PSY」（仅文案，数值正确）
- feature/pe-band-demo 分支未同步 main 的近期修复，待评估
- 新鲜度校验：能检测但未影响结论；抓取失败会覆盖旧值；覆盖面仅部分列
- 930794 偶发缺 IndexClose → PE ffill → streak 误报（已重置，频繁再发再考虑换源）
- 无远程失败通知；QQQ_PE_TODAY 当日未写入会沿用旧值
- cron-job.org 每天 18:30 CST 触发 workflow_dispatch；GitHub 原 schedule 与其撞车
- EWQ/EWG/EWJ/EEM 历史 PE 为估算值，长期需更准数据源
- 迁移 AI API 后观察 1-2 天日志，确认无解读失败
- ERP 图微信推送滞后一天（推送早于 gh-pages 部署）

## 变更日志

| 日期 | 变更内容 |
|---|---|
| 2026-11-09 14:55 CST | 前复权改由云端产出：入 `QUANTDASH_API_KEY`、加 quantdash 依赖、prepare_all_data 条件调度；`data/etf_price_adj.csv` 由 9-23 补齐到 11-09；本机 `fetch_and_push.sh` 停用，云端成唯一写入方 |
| 2026-11-09 13:10 CST | 价格源改为按新鲜度选（前复权滞后 >5 交易日回退未复权并标注）；价格过期时减仓/止盈/清仓降为「信号暂停」；报告并列价格数据日期；新鲜度改按内容日期校验、error/warn 两级、新增守卫自检 |
| 2026-11-09 11:20 CST | CI 改用 python 3.11（3.10 不在 runner 缓存，下载遇 504）；修 980032 `timedelta` 报错；单标的异常写入 freshness |
| 2026-10-14 | 修 freshness `.normalize()` 异常；980032 改理杏仁源；930794 streak 误报重置；新增 ERP 条形图并嵌入微信推送 |
| 2026-09-08 | QQQ PE 抓取改由 Grok 定时任务写入 Actions Variable `QQQ_PE_TODAY` |
| 2026-09-03 14:15 CST | 前复权链路接入 QuantDash（`adjust=forward`，Key 在仓库外私有配置，本地节流 9 次/分钟） |
| 2026-09-03 10:24 CST | 价格风险模块加安全回退：无前复权缓存时用未复权价并提示信号失真风险 |
| 2026-09-02 16:17 CST | 前复权改由本地 `fund_etf_hist_em(adjust=qfq)` 产出，云端只消费缓存；删除 Actions 直连东财的回填 workflow |
| 2026-08-31 15:02 CST | 云端兜底粒度：本地完整成功只跳过 `simple_etf_metrics`，其余照跑；下行失败标的保留旧值并标 stale |
| 2026-08-31 14:44 CST | 定下「本地优先、云端兜底」：先拉干净基线再抓取，四个文件一次提交；云端延后至 18:00 并按当日提交跳过 |
| 2026-08-28 | ETF 指标请求日期统一 Asia/Shanghai |
| 2026-08-07 | `fetch_and_push.sh` 改本地优先；修并发崩溃（MAX_WORKERS=1）与 akshare 版本过低 |
| 2026-08-06 | 净值/指数改增量拉取（缓存 + 5 天缓冲）；行情受新浪接口限制仍全量 |
| 2026-08-05 | 报告精简：仓位建议加 ERP 值、表格折叠、换行修复；文案去重 |
| 2026-08-05 | 决策仪表盘估值分档下加「胜率×赔率」二级标签 |
| 2026-08-04 | 修 ETF 价格 8/2 起冻结；云端定时恢复正常 |
| 2026-08-04 | HSTECH 估值口径由 PE-ERP 改 PS/PSY，删除手动填值机制 |
| 2026-08-02 | 修微信推送不校验响应体（HTTP 200 但实际未送达） |
| 2026-08-02 | 目录重组：抓取入 `fetch/`，分析入 `analysis/` |
| 2026-07-06 | 弃用 pi-mobile；QQQ PE 改 Claude Cowork 定时；指数数 21 个口径修正 |
| 2026-07-02 | 新增有色（512400.SH）、半导体材料设备（588710.SH）；同步各模块配置并回填历史 |
| 2026-06-10 | 删无效 plist；`gh auth login` 完成；QQQ PE 改 `gh variable set` 写入 |
| 2026-06-08 | launchd 方案上线：17:00 CST 触发 `update_pe_trigger.sh`（后续已废弃） |
| 2026-06-08 | 初始化 CLAUDE.md / STATUS.md / KEYS.md，记录项目结构 |
| 2025 | 持仓分类映射、稀土/中美互联网等指数、HSTECH PSY 口径、ERP 历史采集 |
| （早期，无精确日期） | 胜率赔率仓位框架、ETF 执行质量模块、Shiller CAPE、ERP 斜率信号、三级止损/止盈、基本面暴雷预警、决策仪表盘、Pages 自动部署、AI 趋势解读 |
| （早期，无精确日期） | 修 SPY 全量重建 workflow 被反复触发冲掉增量数据；修胜率历史分位未剔空值导致偏低；修红利两只 ETF 代码重复映射 |

> 2026-06-08 及更早的 launchd/plist 记录仅作审计轨迹，当前不再使用。
