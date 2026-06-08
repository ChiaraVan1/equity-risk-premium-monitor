# STATUS.md — 跨 session 运行记忆

> 每次 Claude 完成工作后必须更新此文件。
> 最后更新：2026-06-08

---

## 当前运行状态

| 项目 | 状态 | 备注 |
|-----|------|------|
| GitHub Actions 每日自动跑 | 正常 | 最近一次 commit：`cd1f84d` Auto update ERP data |
| 数据覆盖指数 | 21 个 | A股13个 + 美股2个 + 欧日2个 + 新兴1个 + 港股科技1个 + 稀土/养殖等2个 |
| HSTECH | 改用 PSY | ERP 数据停止更新，改用自建 PS/PSY（月频） |
| ETF 执行质量模块 | 依赖 ETF_data_project Release | 从 GitHub Release `latest` tag 实时下载 `simple_etf_metrics.csv` |
| 微信推送 | 正常 | 只推仪表盘 + 完整报告链接，完整 HTML 在 gh-pages 查看 |
| HTML 报告 | 正常部署 | https://chiaravan1.github.io/equity-risk-premium-monitor/report.html |

---

## 已知问题

1. **FRED API Key 硬编码**：`fetch_bond_yield.py` 和 `fetch_bond_yield_incremental.py` 中的 `FRED_API_KEY` 直接写在代码里，应改为环境变量或 GitHub Secret。
2. **`requirements.txt` 不完整**：`simple_etf_metrics.py`（本地版）依赖 `tushare`，但 `requirements.txt` 未列入。该脚本目前不在 CI 中运行（由 ETF_data_project 负责），本地测试需手动安装。
3. **QQQ PE 需手动每日维护**：若 `QQQ_PE_TODAY` GitHub Variable 未更新，当日 QQQ ERP 不会更新（使用历史最近值）。目前无告警机制。
4. **EWQ/EWG/EWJ/EEM 历史 PE 为估算值**：用今日 PE 与 SPY 的比值乘以 SPY 历史序列，历史精度有限。
5. **`simple_etf_metrics.py` 重复存在**：同一文件分别在本项目和 ETF_data_project 中存在，若两边不同步可能导致指标口径不一致。

---

## 下一步要做的事

- [ ] 将 `FRED_API_KEY` 从代码移到 GitHub Secret，消除安全隐患。
- [ ] `QQQ_PE_TODAY` 未更新时在 Actions 日志中输出更醒目的警告（目前已有 ⚠️ 打印，可考虑 `exit 1` 或 Slack/邮件告警）。
- [ ] 确认 `simple_etf_metrics.py` 是否需要保留在本项目，若纯本地测试用应加注释说明，或从本 repo 删除避免混淆。

---

## 变更日志

| 日期 | 变更内容 |
|-----|---------|
| 2026-06-08 | 初始化 CLAUDE.md / STATUS.md / ../shared/KEYS.md，读取并记录全项目结构；同步创建 ETF_data_project 的 CLAUDE.md / STATUS.md |
| 2025 | 添加持仓分类映射（`HOLDING_CATEGORY`）到 `erp_position.py` |
| 2025 | 新增稀土产业（930598）、畜牧养殖（931946）、中美互联网（930794）等指数 |
| 2025 | HSTECH 改用 PSY 口径，`update_hstech_ps()` 修复国债覆盖范围 bug |
