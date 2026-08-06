STATUS.md — 跨 session 运行记忆

每次 Claude 完成工作后必须更新此文件（直接更新，无需确认）。每条记录需带精确时间。 最后更新：2026-08-06

## TODO

- 加入其他信号指标
- 定位修复价格数据自8/2起冻结问题（Actions云端抓取失败且无告警传播）；搭建本地 launchd→osascript→fetch_and_push.sh 自动化方案，绕开 py_mini_racer 在 launchd 后台daemon下的崩溃问题；发现云端仍无法抓取价格数据、单标的失败会覆盖旧值、本地重试窗口不够用等后续风险
- fetch/simple_etf_metrics.py 曾出现超时（>5分钟）未能写入新的 ETF 指标文件，当日折溢价/资金流/波动率数据读取到的是旧缓存；需排查是数据源响应慢还是需要调大子进程 timeout / 调小 MAX_WORKERS
- HSTECH 报告里估值指标仍标注为"ERP"，应为"PSY"（仅文字标注问题，数值本身正确）
- feature/pe-band-demo 分支也在用这份代码，main 分支近期的一系列修复/重构还没同步过去，需要评估要不要同步
- 微信推送 desp 参数有 32KB 硬性长度限制，曾出现"log显示推送成功但未收到"的情况，需要确认是否是长度截断导致
- 新鲜度校验目前只做到"检测并展示"（check_metric_freshness() / build_freshness_note()，HSTECH 的 PS 校验分支已在跑），但检测到连续 N 天未更新时，报告里不会有任何降级动作——"极度低估/极佳买点"这类强建仓结论照常输出。需要补上熔断：连续 ffill 超过阈值时，至少给对应标的的信号打上明显降级标记，而不是让它悄悄混进正常信号里（HSTECH 的 bug 曾潜伏近一个月，就是卡在这一步——检测到了，但没人看日志，报告本身也没变）
- 新鲜度校验计划扩展到全部标的的全部数值列（不只是手填的），逐个指数、逐个字段加新鲜度检查，覆盖所有数据源，不管是自动抓取还是人工填入
- 单标的抓取失败（如 SSLEOFError）会写 NaN 覆盖旧值，而非保留旧值 + 标记 stale，是新鲜度熔断缺失的延伸问题
- 缺少远程失败通知（目前仅本地通知）；QQQ PE 若当日未成功写入 QQQ_PE_TODAY，QQQ ERP 不会更新（沿用最近历史值），需要确认 Claude Cowork 定时任务失败时是否有可见的失败提示
- EWQ/EWG/EWJ/EEM 历史 PE 为估算值（今日 PE 与 SPY 比值 × SPY 历史序列），历史精度有限，长期看是否有更准确的数据源

## 变更日志

| 日期 | 变更内容 |
|---|---|
| 2026-08-06 | fetch/simple_etf_metrics.py 改为增量拉取：净值(nav)和基准指数(index)接口支持按日期范围查询，此前每次固定拉满3年，现改为落盘缓存历史（新增 data/etf_nav.csv、data/index_pct.csv 两个宽表）+ 只拉取"上次缓存最后日期 - 5天缓冲"到今天的增量，与本地历史合并（重叠日期以新数据为准，覆盖可能的历史修订）后再用于3年滚动窗口计算，计算口径与全量拉取完全一致，减少请求耗时并降低被限流概率。行情(price)因新浪 ak.fund_etf_hist_sina() 接口不支持日期范围参数，仍整表拉取，不受此优化影响。已确认下游 analysis/etf_quality.py、prepare_all_data.py、erp_position.py 均不受影响（输出列结构未变，新增文件无命名冲突，.gitignore 未排除）。涉及文件：fetch/simple_etf_metrics.py |
| 2026-08-05 | 详情页精简：仓位建议补充当前ERP值展示；减仓/止盈信号表格、估值分档表格、历史股息率分布表格均改为 `<details>` 折叠，默认只留结论；ERP趋势模块标题与文案调整为"ERP趋势方向/近10月ERP趋势"；删除两句多余提示语；修复仪表盘"不参与/可参与"换行丢失问题。涉及文件：erp_position.py、analysis/risk.py、analysis/trend.py、analysis/dividend_yield_analysis.py、report/markdown.py |
| 2026-08-05 | 精简报告文案：仓位建议区块删除"建议总仓位"重复行（三仓已列出数字，无需再加总）；估值分档表从"核心估值决策"模块移至"仓位建议"模块（分档是仓位拆分的直接依据，放一起更好懂）；核心估值决策的推导过程（历叶均值/胜率/赔率/PE统计）收进 `<details>` 折叠块，默认只展示综合评级一行；删除"止盈信号与减仓/止损信号相互独立"说明句（意思在字段本身已经表达清楚）；删除减仓/止损信号里"基本面暴雷见下方模块"提示（基本面预警已独立展示，无需交叉引用）。减仓/止损盈信号里的"ERP区间"/"估值区间"展示行**予以保留**，因为对应的低估区L1/L2降级、高估区L1/L2升级规则在 risk.py 里确认仍在生效，删除会导致用户看不懂阈值为何变化。涉及文件：analysis/risk.py、analysis/valuation.py、erp_position.py |
| 2026-08-05 | 决策仪表盘新增二级分组：一级仍为估值分档（🟢极度低估/🟢显著低估/🟡合理偏低/🟠合理区间/🔴高估规避），二级在各分档内部按"胜率×赔率"综合评级细分排序（🟢极佳买点/🟡可参与/🔴不参与），只展示简化标签不带说明文字；同步把 valuation.py 里"🟢已进入极度低估区，极佳买点"的文案精简为"🟢极佳买点"。目前仅梳理了估值+胜率赔率这2层信息的极简展示，report/markdown.py 新增 _rating_short() 辅助函数 |
| 2026-08-04 | 修复 ETF 价格数据自 8/2 起冻结不更新的 bug，搭建本地定时抓取自动化方案作为补充 |
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
