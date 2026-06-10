# STATUS.md — 跨 session 运行记忆

> 每次 Claude 完成工作后必须更新此文件（直接更新，无需确认）。每条记录需带精确时间。
> 最后更新：2026-06-10

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

## 本地定时任务（macOS launchd）

### pi-mobile host 开机自启（常驻服务）

| 项目 | 路径/值 |
|-----|--------|
| plist 文件 | `~/Library/LaunchAgents/com.chiaravan.pi-mobile-host.plist` |
| 启动命令 | `pnpm --filter host dev`（WorkingDirectory: `~/workspace/dev/pi-mobile`） |
| 触发时机 | 登录即启动（`RunAtLoad=true`），崩溃自动重启（`KeepAlive=true`） |
| 环境变量 | `PI_MOBILE_HOST_BIND=0.0.0.0` |
| 文件句柄限制 | `SoftResourceLimits.NumberOfFiles=65536`（等效 `ulimit -n 65536`） |
| 日志文件 | `~/pi-mobile-host.log`（stdout + stderr 合并） |
| launchd Label | `com.chiaravan.pi-mobile-host` |

**管理命令**：
```bash
# 查看状态
launchctl list | grep pi-mobile

# 停止
launchctl unload ~/Library/LaunchAgents/com.chiaravan.pi-mobile-host.plist

# 重新加载（修改 plist 后执行）
launchctl unload ~/Library/LaunchAgents/com.chiaravan.pi-mobile-host.plist
launchctl load   ~/Library/LaunchAgents/com.chiaravan.pi-mobile-host.plist

# 查看实时日志
tail -f ~/pi-mobile-host.log
```

---

### QQQ PE 每日触发

QQQ PE 的本地自动化通过 launchd LaunchAgent 实现，每日北京时间 16:30（UTC 08:30）触发。

| 项目 | 路径/值 |
|-----|--------|
| plist 文件 | `~/Library/LaunchAgents/com.chiaravan.updatepe.plist` |
| 触发脚本 | `~/update_pe_trigger.sh` |
| 触发时间 | 周一至周五，北京时间 17:00（launchd 本地时间 Hour=17 Minute=0） |
| 日志文件 | `~/update_pe.log` |
| launchd Label | `com.chiaravan.updatepe` |

**脚本职责**：激活 Claude 桌面应用，自动输入 `updatePE` 并回车，触发 PE 数据查询。日志格式：
```
[2026-06-08 16:30:00] === update_pe_trigger.sh 开始 ===
[2026-06-08 16:30:15] === 结束，exit code: 0 ===
```

**管理命令**：
```bash
# 查看状态
launchctl list | grep chiaravan

# 重新加载（修改 plist 后执行）
launchctl unload ~/Library/LaunchAgents/com.chiaravan.updatepe.plist
launchctl load   ~/Library/LaunchAgents/com.chiaravan.updatepe.plist

# 手动触发测试
bash ~/update_pe_trigger.sh
```

> ~~`com.chiaravan.update-pe.plist`~~ 已删除（`update_pe.py` 不存在，每日 18:00 报错，2026-06-10 清理）。

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
| 2026-06-10 18:00 | 删除 `com.chiaravan.update-pe.plist`（`update_pe.py` 不存在，每日报错）；`gh auth login` 完成（ChiaraVan1）；`update_pe_trigger.sh` prompt 第二步改为 `gh variable set QQQ_PE_TODAY` 替代浏览器导航 GitHub settings（更稳定，不依赖 Chrome 登录状态） |
| 2026-06-08 17:10 | 修复 update_pe_trigger.sh：改用 AppleScript 内部设置剪贴板 + `keystroke "v" using {command down}` 粘贴完整 prompt，解决 Cmd+V 无效问题 |
| 2026-06-08 17:00 | launchd 触发时间改为 17:00（CST） |
| 2026-06-08 16:41 | 修复 plist 时间：launchd 用本地时间，Hour 改为 16，Minute 改为 30（之前错误地设成 UTC 08:30） |
| 2026-06-08 16:30 | 配置 launchd 定时任务（com.chiaravan.updatepe），每日北京时间触发 update_pe_trigger.sh，日志输出到 ~/update_pe.log |
| 2026-06-08 | 初始化 CLAUDE.md / STATUS.md / ../shared/KEYS.md，读取并记录全项目结构；同步创建 ETF_data_project 的 CLAUDE.md / STATUS.md |
| 2025 | 添加持仓分类映射（`HOLDING_CATEGORY`）到 `erp_position.py` |
| 2025 | 新增稀土产业（930598）、畜牧养殖（931946）、中美互联网（930794）等指数 |
| 2025 | HSTECH 改用 PSY 口径，`update_hstech_ps()` 修复国债覆盖范围 bug |
