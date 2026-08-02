# STATUS.md — 跨 session 运行记忆

> 每次 Claude 完成工作后必须更新此文件（直接更新，无需确认）。每条记录需带精确时间。
> 最后更新：2026-07-06

---

## 当前运行状态

| 项目 | 状态 | 备注 |
|-----|------|------|
| 数据覆盖指数 | 21 个 | A股/港股15个（含证券公司399975、中证军工399967、军工龙头931066、中美互联网930794、稀土产业930598、有色金属000819、半导体材料设备950125等主题指数） + 美股2个（SPY/QQQ） + 欧日3个（EWQ/EWG/EWJ） + 新兴市场1个（EEM） |
| ETF 执行质量模块 | 依赖 ETF_data_project Release | 从 GitHub Release `latest` tag 实时下载 `simple_etf_metrics.csv`；数据源已切换为纯 AKShare，不再需要 TUSHARE_TOKEN |
| 微信推送 | 正常 | 只推仪表盘 + 完整报告链接，完整 HTML 在 gh-pages 查看 |
| HTML 报告 | 正常部署 | https://chiaravan1.github.io/equity-risk-premium-monitor/report.html |
| QQQ PE 抓取 | Claude Cowork 自带定时任务 | 不再依赖本地 launchd/AppleScript，见下方说明 |

---

## QQQ PE 每日更新

**当前机制**：由 Claude Cowork 自带的定时/计划任务负责，每日自动查询 QQQ 今日 PE 并写入 GitHub Actions Variable `QQQ_PE_TODAY`。

**不再使用**：本地 macOS launchd LaunchAgent（`com.chiaravan.updatepe.plist`）+ `update_pe_trigger.sh` + AppleScript 模拟键入激活 Claude 桌面应用的方案已废弃，相关 plist、触发脚本、`~/update_pe.log` 均可视为历史遗留，不再维护。

> 具体的 Cowork 计划任务配置（触发时间、失败重试等）不在本机 launchd 里，无法像以前一样通过 `launchctl list` 查看状态——如果需要排查，应在 Claude Cowork 自身的任务管理界面里查。

---

## 已知问题

1. **QQQ PE 需按日更新**：若当日 QQQ PE 未成功写入 `QQQ_PE_TODAY`，当日 QQQ ERP 不会更新（沿用最近历史值）。目前无告警机制，需要确认 Cowork 定时任务失败时是否有可见的失败提示。
2. **EWQ/EWG/EWJ/EEM 历史 PE 为估算值**：用今日 PE 与 SPY 的比值乘以 SPY 历史序列，历史精度有限。

---

## 下一步要做的事

- [ ] 确认 Cowork 定时任务失败时的告警方式（目前唯一的"失败信号"是当日ERP不更新，没有主动通知）。

---

## 变更日志

| 日期 | 变更内容 |
|-----|---------|
| 2026-07-06 | 移除已废弃的 pi-mobile host 相关记录（不再使用）；QQQ PE 抓取从本地 launchd+AppleScript 方案迁移为 Claude Cowork 自带定时任务；确认 `simple_etf_metrics.py` 无重复、`FRED_API_KEY` 硬编码非问题，从"已知问题"中移除；数据覆盖指数从23个更正为21个（`000989`/`931139`/`931946` 已彻底下线，此前数字把它们也算进去了） |
| 2026-07-02 | Claude Cowork 新增2只指数：有色金属（000819→512400.SH）、半导体材料设备（950125→588710.SH）。同步更新 fetch_bond_yield_incremental.py / fetch_bond_yield.py 的 INDEX_CONFIG，erp_position.py 的 indices / HOLDING_CATEGORY / 基本面预警关键词，etf_metrics.py 的 ERP_TO_ETF；已手动触发 init_history.yml 回填历史 PE |
| 2026-06-10 18:00 | 删除 `com.chiaravan.update-pe.plist`（`update_pe.py` 不存在，每日报错）；`gh auth login` 完成（ChiaraVan1）；`update_pe_trigger.sh` prompt 第二步改为 `gh variable set QQQ_PE_TODAY` 替代浏览器导航 GitHub settings（更稳定，不依赖 Chrome 登录状态） |
| 2026-06-08 17:10 | 修复 update_pe_trigger.sh：改用 AppleScript 内部设置剪贴板 + `keystroke "v" using {command down}` 粘贴完整 prompt，解决 Cmd+V 无效问题 |
| 2026-06-08 17:00 | launchd 触发时间改为 17:00（CST） |
| 2026-06-08 16:41 | 修复 plist 时间：launchd 用本地时间，Hour 改为 16，Minute 改为 30（之前错误地设成 UTC 08:30） |
| 2026-06-08 16:30 | 配置 launchd 定时任务（com.chiaravan.updatepe），每日北京时间触发 update_pe_trigger.sh，日志输出到 ~/update_pe.log |
| 2026-06-08 | 初始化 CLAUDE.md / STATUS.md / ../shared/KEYS.md，读取并记录全项目结构；同步创建 ETF_data_project 的 CLAUDE.md / STATUS.md |
| 2025 | 添加持仓分类映射（`HOLDING_CATEGORY`）到 `erp_position.py` |
| 2025 | 新增稀土产业（930598）、中美互联网（930794）等指数 |
| 2025 | HSTECH 改用 PSY 口径，`update_hstech_ps()` 修复国债覆盖范围 bug |

> 2026-06-08 及更早关于 launchd/plist 的历史记录保留作为审计轨迹，不代表当前仍在使用（见上方"QQQ PE 每日更新"当前机制说明）。
