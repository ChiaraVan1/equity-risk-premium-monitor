STATUS.md — 跨 session 运行记忆

每次 Claude 完成工作后必须更新此文件（直接更新，无需确认）。每条记录需带精确时间。 最后更新：2026-08-31 15:02:00 CST

## TODO

- 加入其他信号指标
- HSTECH 报告里估值指标仍标注为"ERP"，应为"PSY"（仅文字标注问题，数值本身正确）
- feature/pe-band-demo 分支也在用这份代码，main 分支近期的一系列修复/重构还没同步过去，需要评估要不要同步
- 新鲜度校验：已有检测+展示（PE/PSY⚠️标记、08-07新增ETF指标stale_flag/streak），但只是"检测到了，报告没反应"——连续N天未变化时不会降级"极佳买点"等强结论，单标的抓取失败也会直接写NaN覆盖旧值而非保留旧值+标记stale。需补：①降级标记接入报告结论 ②抓取失败保留旧值而非覆盖 ③检测范围扩展到全部标的全部数值列（目前仅PE/PSY手填列+ETF指标列，未覆盖全部数据源）
- 缺少远程失败通知（目前仅本地）：定时任务本身跑挂时无感知，和新鲜度校验（检测数据是否陈旧）是两回事；QQQ_PE_TODAY 当日未写入会导致 QQQ ERP 沿用旧值
- EWQ/EWG/EWJ/EEM 历史 PE 为估算值（今日 PE 与 SPY 比值 × SPY 历史序列），历史精度有限，长期看是否有更准确的数据源

## 变更日志

| 日期 | 变更内容 |
|---|---|
| 2026-08-31 15:02:00 CST | 修正云端兜底粒度：本地完整成功时只跳过 `fetch/simple_etf_metrics.py`，国债/ERP、股息率、分析报告和 gh-pages 部署继续执行；本地存在任一行情失败时改用“部分失败”提交标记，云端不会跳过 AkShare，会继续查漏补缺。失败标的在中间快照中保留旧值并标记 stale，避免空值/删列污染下游。 |
| 2026-08-31 14:44:44 CST | 稳定本地优先、GitHub兜底的数据更新链路：本地 `fetch_and_push.sh` 改为先拉取干净基线再抓取，并一次提交 `simple_etf_metrics/etf_price/etf_nav/index_pct` 四个关联文件；GitHub定时兜底延后至北京时间18:00，并按北京时间当天的本地成功提交精确判断是否跳过；`simple_etf_metrics.py` 在单标的行情失败时保留 `etf_price` 旧列和上一版指标快照，同时标记 stale，避免局部接口失败把有效数据覆盖为空；忽略 `.DS_Store`。 |
| 2026-08-28 | ETF 指标请求日期统一使用 Asia/Shanghai |
| 2026-08-02（补记） | 修复微信推送 send_to_wechat()：此前不检查 requests.post() 响应体，Server酱业务失败（sckey失效/超额度/desp过长被拒等）仍返回HTTP 200，导致"log显示成功但未收到"。现改为校验HTTP状态码+响应体code字段，失败时打印具体原因 |
| 2026-08-07 | fetch_and_push.sh改为本地抓取优先，网络失败不阻塞；修复MAX_WORKERS并发崩溃(改1)及akshare版本过旧致净值报错(升1.18.83)。云端已有本地成功则跳过机制(daily_trade.yml) |
| 2026-08-06 | 净值/基准指数改增量拉取（本地缓存+近5天缓冲，口径与全量一致），减少请求耗时；行情因新浪接口限制仍全量拉取。涉及：fetch/simple_etf_metrics.py |
| 2026-08-05 | 详情页精简：仓位建议加ERP值展示；多个表格改折叠默认收起；修复换行丢失bug。涉及：erp_position.py等5个文件 |
| 2026-08-05 | 精简报告文案：删除重复仓位说明；估值分档表移至仓位建议模块；推导过程收进折叠块。涉及：risk.py/valuation.py/erp_position.py |
| 2026-08-05 | 决策仪表盘加二级分组：估值分档下按胜率×赔率细分标签（极佳买点/可参与/不参与） |
| 2026-08-04 | 修复ETF价格数据8/2起冻结问题；08-06起云端Actions定时运行恢复正常（见#226） |
| 2026-08-04 | 修复 HSTECH 估值信号失真 bug：估值计算口径从 PE-ERP 切换为 PS/PSY，删除 HS_TECH_PE_TODAY 手动填值机制 |
| 2026-08-02 | 代码目录重组：数据获取脚本迁移至 fetch/，分析类模块统一收进 analysis/ |
| 2026-07-06 | 移除已废弃的 pi-mobile host 相关记录（不再使用）；QQQ PE 抓取从本地 launchd+AppleScript 方案迁移为 Claude Cowork 自带定时任务；确认 simple_etf_metrics.py 无重复、FRED_API_KEY 硬编码非问题，从"已知问题"中移除；数据覆盖指数从23个更正为21个（000989/931139/931946 已彻底下线，此前数字把它们也算进去了） |
| 2026-07-02 | Claude Cowork 新增2只指数：有色金属（000819→512400.SH）、半导体材料设备（950125→588710.SH）。同步更新 fetch_bond_yield_incremental.py / fetch_bond_yield.py 的 INDEX_CONFIG，erp_position.py 的 indices / HOLDING_CATEGORY / 基本面预警关键词，etf_metrics.py 的 ERP_TO_ETF；已手动触发 init_history.yml 回填历史 PE |
| 2026-06-10 18:00 | 删除 com.chiaravan.update-pe.plist（update_pe.py 不存在，每日报错）；gh auth login 完成（ChiaraVan1）；update_pe_trigger.sh prompt 第二步改为 gh variable set QQQ_PE_TODAY 替代浏览器导航 GitHub settings（更稳定，不依赖 Chrome 登录状态） |
| 2026-06-08 17:10 | 修复 update_pe_trigger.sh：改用 AppleScript 内部设置剪贴板 + keystroke "v" using {command down} 粘贴完整 prompt，解决 Cmd+V 无效问题 |
| 2026-06-08 17:00 | launchd 触发时间改为 17:00（CST） |
| 2026-06-08 16:41 | 修复 plist 时间：launchd 用本地时间，Hour 改为 16，Minute 改为 30（之前错误地设成 UTC 08:30） |
| 2026-06-08 16:30 | 配置 launchd 定时任务（com.chiaravan.updatepe），每日北京时间触发 update_pe_trigger.sh，日志输出到 ~/update_pe.log |
| 2026-06-08 | 初始化 CLAUDE.md / STATUS.md / ../shared/KEYS.md，读取并记录全项目结构；同步创建 ETF_data_project 的 CLAUDE.md / STATUS.md |
| 2025 | 添加持仓分类映射（HOLDING_CATEGORY）到 erp_position.py |
| 2025 | 新增稀土产业（930598）、中美互联网（930794）等指数 |
| 2025 | HSTECH 改用 PSY 口径，update_hstech_ps() 修复国债覆盖范围 bug |
| （早期，无精确日期） | ERP / PSY 历史数据采集（全量 + 增量） |
| （早期，无精确日期） | 排查 SPY 数据异常：有一个本该只手动跑一次的全量重建 workflow 被反复触发（因为新增关注 code），每次都会把每日增量脚本已经写好的真实数据冲掉 |
| （早期，无精确日期） | 胜率 / 赔率 / 仓位建议框架 |
| （早期，无精确日期） | ETF 执行质量模块（折溢价、换手、波动、超额收益） |
| （早期，无精确日期） | Shiller CAPE 长期回报锚（仅 SPY） |
| （早期，无精确日期） | HSTECH PS / PSY 自建数据管道 |
| （早期，无精确日期） | ERP 斜率信号（近20日，五档分类，含恐慌踩踏识别） |
| （早期，无精确日期） | 三级分级止损（估值分位对趋势信号降级 + 回撤/均线三条件） |
| （早期，无精确日期） | 三级分级止盈（乖离率过热镜像止损逻辑，MA20/MA60乖离触发，高估区触发条件升级而非豁免） |
| （早期，无精确日期） | 修复"核心估值决策"区块胜率计算未使用锚定子集的问题（SPY/QQQ/EWQ/EWG/EWJ 报告内胜率口径不一致） |
| （早期，无精确日期） | 基本面暴雷预警（东方财富快讯粗筛 + AI 二次过滤，仅输出标志位，需人工确认） |
| （早期，无精确日期） | 决策仪表盘结构 |
| （早期，无精确日期） | HTML 报告自动部署到 GitHub Pages |
| （早期，无精确日期） | 减仓/止损信号支持「未持仓」场景：不再因未持仓而整条隐藏，改为降级展示为观察提示（🔎），价格结构风险始终可见 |
| （早期，无精确日期） | 减仓摘要新增区间（120日窗口）回撤 / 反弹统计 |
| （早期，无精确日期） | 胜率·赔率展示格式统一优化（如"胜78%·赔1.85x"，赔率趋近无穷大时显示"∞"） |
| （早期，无精确日期） | 数据新鲜度校验修复：未变化计数仅在真实新交易日推进，避免节假日/非交易日造成误判 |
| （早期，无精确日期） | EWG（德国）、EEM（新兴市场）补上 A股 ETF 映射（此前标记为"无对应ETF"） |
| （早期，无精确日期） | 修复 000015（上证红利）与 000922（中证红利）ETF 代码重复映射的 bug |
| （早期，无精确日期） | 修复胜率/历史分位计算未剔除空值样本导致系统性偏低的 bug（如000300一度从真实56%显示成40%） |
| （早期，无精确日期） | 止损/止盈完整分级逻辑（低估区L1/L2降级、高估区止盈L1/L2升级、QQQ单日急跌独立信号） |
| （早期，无精确日期） | 「仓位建议」文字说明区块 |
| （早期，无精确日期） | 「核心估值决策」里综合评级、历史均值、PE历史统计 |
| （早期，无精确日期） | 止盈信号接入仪表盘「需要处理」置顶判定 |
| （早期，无精确日期） | QQQ PE 写入方式从模拟网页表单操作改为 GitHub REST API 直接写入，抓取仍由 Claude Cowork + Chrome MCP 完成 |
| （早期，无精确日期） | 近10月趋势 AI 解读（DashScope 生成一句话走势总结，双数据源降级：DashScope → qnaigc → 规则法兜底，规则法基于分位数五档判断） |
| （早期，无精确日期） | ETF 行情抓取健全性校验：simple_etf_metrics.py 中 trade_date 缺失标的超过10个时判定为抓取异常并 exit(1)，prepare_all_data.py 将其传播为流程终止，避免数据全空但 CI 显示成功的情况 |
| （早期，无精确日期） | ETF 执行质量加入 AI 解读 |

> 2026-06-08 及更早关于 launchd/plist 的历史记录保留作为审计轨迹，不代表当前仍在使用（本地 launchd + AppleScript 方案已废弃，现由 Claude Cowork 自带定时任务负责 QQQ PE 抓取）。
