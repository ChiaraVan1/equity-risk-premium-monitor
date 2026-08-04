# ERP 策略每日监控报告

## TODO

- 加入其他信号指标
- `fetch/simple_etf_metrics.py` 今天超时（>5分钟）未能写入新的 ETF 指标文件，当日折溢价/资金流/波动率数据读取到的是旧缓存；需排查是数据源响应慢还是需要调大子进程 timeout / 调小 `MAX_WORKERS`
- HSTECH 报告里估值指标仍标注为"ERP"，应为"PSY"（仅文字标注问题，数值本身正确）
- feature/pe-band-demo 分支也在用这份代码，这次 main 分支的一系列修复/重构还没同步过去，需要评估要不要同步
- 微信没收到推送，但是log显示推送成功了，desp 参数有 32KB 硬性长度限制
- 新鲜度校验目前只做到"检测并展示"（check_metric_freshness() /build_freshness_note()，HSTECH 的 PS 校验分支已经在跑），但检测到连续 N 天未更新时，报告里不会有任何降级动作——"极度低估/极佳买点"。这类强建仓结论照常输出。需要补上熔断：连续 ffill 超过阈值时，至少给对应标的的信号打上明显降级标记，而不是让它悄悄混进正常信号里。（这次 HSTECH 的 bug 能潜伏近一个月，就是卡在这一步——检测到了，但没人看日志，报告本身也没变。）
- 新鲜度校验计划扩展到全部标的的全部数值列（不只是手填的），逐个指数、逐个字段加新鲜度检查，覆盖所有数据源，不管是自动抓取还是人工填入。
- 单标的抓取失败(如SSLEOFError)会写NaN覆盖旧值，而非保留旧值+标记stale，是新鲜度熔断缺失的延伸问题
- 缺少远程失败通知（目前仅本地通知）

## 已完成

- ERP / PSY 历史数据采集（全量 + 增量）
- 排查 SPY 数据异常：有一个本该只手动跑一次的全量重建workflow被反复触发（因为新增关注code），每次都会把每日增量脚本已经写好的真实数据冲掉
- 胜率 / 赔率 / 仓位建议框架
- ETF 执行质量模块（折溢价、换手、波动、超额收益）
- Shiller CAPE 长期回报锚（仅 SPY）
- HSTECH PS / PSY 自建数据管道
- ERP 斜率信号（近20日，五档分类，含恐慌踩踏识别）
- 三级分级止损（估值分位对趋势信号降级 + 回撤/均线三条件）
- 三级分级止盈（乖离率过热镜像止损逻辑，MA20/MA60乖离触发，高估区触发条件升级而非豁免）
- 修复"核心估值决策"区块胜率计算未使用锚定子集的问题（SPY/QQQ/EWQ/EWG/EWJ 报告内胜率口径不一致）
- 基本面暴雷预警（东方财富快讯粗筛 + AI 二次过滤，仅输出标志位，需人工确认）
- 决策仪表盘结构
- HTML 报告自动部署到 GitHub Pages
- 减仓/止损信号支持「未持仓」场景：不再因未持仓而整条隐藏，改为降级展示为观察提示（🔎），价格结构风险始终可见
- 减仓摘要新增区间（120日窗口）回撤 / 反弹统计
- 胜率·赔率展示格式统一优化（如"胜78%·赔1.85x"，赔率趋近无穷大时显示"∞"）
- 数据新鲜度校验修复：未变化计数仅在真实新交易日推进，避免节假日/非交易日造成误判
- EWG（德国）、EEM（新兴市场）补上 A股 ETF 映射（此前标记为"无对应ETF"）
- 修复 000015（上证红利）与 000922（中证红利）ETF 代码重复映射的 bug
- 修复胜率/历史分位计算未剔除空值样本导致系统性偏低的 bug（如000300一度从真实56%显示成40%）
- 止损/止盈完整分级逻辑（低估区L1/L2降级、高估区止盈L1/L2升级、QQQ单日急跌独立信号）
- 「仓位建议」文字说明区块
- 「核心估值决策」里综合评级、历史均值、PE历史统计
- 止盈信号接入仪表盘「需要处理」置顶判定
- 代码目录重组：数据获取脚本迁移至 `fetch/`，分析类模块统一收进 `analysis/`
- QQQ PE 改为脚本化自动抓取（替代 Claude Cowork/Chrome MCP 手动写入 QQQ_PE_TODAY 的方式）
- 近10月趋势 AI 解读（DashScope 生成一句话走势总结，双数据源降级：DashScope → qnaigc → 规则法兜底，规则法基于分位数五档判断）
- ETF 行情抓取健全性校验：`simple_etf_metrics.py` 中 `trade_date` 缺失标的超过10个时判定为抓取异常并 `exit(1)`，`prepare_all_data.py` 将其传播为流程终止，避免数据全空但 CI 显示成功的情况
- etf 执行质量加入 AI解读
- 【2026-08-04】修复 ETF 价格数据自 8/2 起冻结不更新的 bug，搭建本地定时抓取自动化方案作为补充
- 【2026-08-04】修复 HSTECH 估值信号失真 bug：估值计算口径从 PE-ERP 切换为 PS/PSY，删除 HS_TECH_PE_TODAY 手动填值机制

---

## 整体执行流程

```
配置层
├─ config.json
├─ industry_map.json
└─ config_loader.py → ALL_INDICES / BOND_YIELD_CONFIG / HOLDING_CATEGORY / INDICES_LIST / HSTECH_TICKERS
        ↓

数据生产层（fetch/）
├─ simple_etf_metrics.py           → data/simple_etf_metrics.csv
├─ fetch_bond_yield.py             → data/erp_*.csv（全量）
├─ fetch_bond_yield_incremental.py → data/erp_*.csv（增量）
├─ fetch_ps.py                     → data/ps_HSTECH.csv
└─ dividend_yield_fetch.py         → data/dividend_yield/dyr_*.csv
        ↓

prepare_all_data.py（聚合所有 CSV，subprocess 跑 fetch/ 脚本 + import ensure_dividend_data_fresh）
        ↓

分析层（analysis/）
├─ valuation.py
├─ risk.py
├─ trend.py
├─ sentiment.py           （调用 popularity_signal.py）
├─ etf_quality.py
├─ dividend_yield_analysis.py
└─ popularity_signal.py
        ↓

analyze_and_suggest(某个code)
  ├─ build_shiller_block()            valuation.py
  ├─ build_unified_valuation_block()  valuation.py
  ├─ build_trend_block()              trend.py
  ├─ compute_exit_signal_summary()    risk.py
  ├─ build_exit_signal_block()        risk.py
  ├─ compute_profit_signal_summary()  risk.py
  ├─ build_profit_signal_block()      risk.py
  ├─ compute_range_drawdown_rebound() risk.py
  ├─ build_sentiment_block()          sentiment.py
  ├─ build_dividend_yield_block()     dividend_yield_analysis.py
  └─ build_etf_quality_block()        etf_quality.py
        ↓ 拼成单个标的的 Markdown

报告层（report/）
└─ markdown.py → build_summary_block()（仪表盘）+ markdown_to_html() → HTML
                 + save_html_report() / send_to_wechat()
        ↓

erp_position.py（主入口，根目录）
```

**首次使用**须先跑全量脚本 `fetch_bond_yield.py` 建立历史数据，之后每日跑增量脚本即可。

---

## 标的一览

| 指数代码 | 指数名称 | 对应 ETF | PE 数据来源 | 国债 | 更新频率 |
|---------|---------|----------|----------|------|---------|
| 000300 | 沪深300 | 510300.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 000688 | 科创50 | 588000.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 000922 | 中证红利 | 515180.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 000015 | 上证红利 | 510880.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 399989 | 中证医疗 | 512170.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 931071 | 人工智能 | 515980.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 000069 | 消费80 | 510150.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 930781 | 中证影视 | 516620.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 399975 | 证券公司 | 512880.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 399967 | 中证军工 | 512660.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 931066 | 军工龙头 | 512710.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 930598 | 稀土产业 | 516150.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 930794 | 中美互联网 | ─（暂无对应A股ETF） | 中证指数官网（akshare） | CN10Y | 日频 |
| 000819 | 有色金属 | 512400.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 950125 | 半导体材料设备 | 588710.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| SPY | S&P 500 | 513500.SH | multpl.com（月频历史）+ worldperatio.com（今日） | US10Y | 月频历史 + 日频今日 |
| QQQ | Nasdaq 100 | 159696.SZ | GuruFocus xlsx（手动下载）+ 手动填入今日值 | US10Y | 手动维护 |
| EWQ | MSCI France | 513080.SH | worldperatio.com（今日值）× SPY比值估算历史 | FR10Y | 日频今日，历史为估算 |
| EWG | MSCI Germany | 159561.SZ | worldperatio.com（今日值）× SPY比值估算历史 | DE10Y | 日频今日，历史为估算 |
| EWJ | MSCI Japan | 513880.SH | worldperatio.com（今日值）× SPY比值估算历史 | JP10Y | 日频今日，历史为估算 |
| EEM | MSCI Emerging | 520580.SH | worldperatio.com（今日值）× SPY比值估算历史 | CN10Y | 日频今日，历史为估算 |
| HSTECH | 恒生科技 | 513180.SH | 自建：yfinance 市值 + akshare 季报营收/净利润 | CN10Y | 月频 |
| 931637 | 港股通互联网 | 513770.SH | 中证指数官网（akshare） | CN10Y | 日频 |

> EWQ/EWG/EWJ/EEM 的**历史 PE 为估算值**：以今日该指数与 SPY 的 PE 比值为固定系数，乘以 SPY 历史 PE 序列反推。今日值为 worldperatio.com 真实数据。

---

## 核心计算逻辑

### ERP（股权风险溢价）

```
ERP = 1/PE − 无风险利率（10年期国债收益率）
```

- ERP 越高：股票相对债券越便宜
- ERP 越低（乃至为负）：估值越贵

**胜率** = 当前 ERP 在历史序列中的分位数

```
胜率 = (历史ERP中 < 当前ERP 的数量) / 总样本数
```

历史分位越高 → 当前越便宜 → 胜率越高。

**赔率** = (当前ERP − P10) / (P90 − 当前ERP)（ERP 绝对值法）

```
若 P90 − 当前ERP ≤ 0（已超P90极度低估） → 赔率视为"极高"（展示为 ∞）
若 当前ERP − P10 ≤ 0（已跌破P10极度高估） → 赔率 = 0
```

**欧美日锚定区间**：EWQ/EWG/EWJ/SPY/QQQ 使用 2022年1月1日以后的数据作为分位锚。

---

### ERP 斜率信号

基于近20日 ERP（或PSY）线性回归斜率，量化当前市场情绪速度。**阈值为自适应历史分位**（不是固定百分比）：用该标的自己"历史上所有20日变化"的分布来判断当前变化是否极端——ERP=1/PE−rf 是倒数关系，固定阈值对高PE标的可能永远触发不了，改用"当前变化在自身历史分布中的分位"后同一套逻辑对所有标的自适应。历史样本（20日变化）不足60条时退回固定阈值。

| 信号 | 条件（自适应历史分位，样本充足时） | 固定阈值兜底（样本<60条时） | 含义 |
|------|----------------------------------|--------------------------|------|
| 🚨 恐慌踩踏 | ≥ 90分位 | 20日绝对变化 ≥ +2% | PE急速压缩，市场抛售，历史上往往是买点临近的前兆 |
| 🟢 估值快速改善 | 65~90分位 | +0.8% ~ +2% | 估值持续修复，买入窗口打开 |
| 🟡 横盘震荡 | 中间区间 | -0.8% ~ +0.8% | 无明显趋势，保持既有仓位 |
| 🟠 估值快速恶化 | 65~90分位（反向） | -2% ~ -0.8% | 估值向贵漂移，提高警惕 |
| ⚠️ 情绪过热 | ≥ 90分位（反向） | ≤ -2% | 市场情绪快速升温，泡沫化加速，警戒高位 |

---

### 减仓 / 清仓信号

**设计原则：ERP框架（逆向估值）与均线/回撤（趋势跟踪）冲突时，用估值分位对趋势信号做"降级"而非简单屏蔽。**

三级回撤止损：

| 级别 | 触发条件 | 高估区（ERP<P50）动作 | 低估区（ERP≥P50）动作 |
|-----|---------|---------------------|---------------------|
| L1 | 回撤≥10% 且跌破MA20 | ⚠️ 减持1/3仓位 | 🛡️ 降级为观察提示，不减仓 |
| L2 | 回撤≥15% 且跌破MA60 | 🔴 减至底仓（保留泡沫仓30%） | ⚠️ 降级：同样减至底仓，但展示为L1级别 |
| L3 | 回撤≥20% | 🚨 强制全清，止损优先 | 🚨 同样全清，**低估区无豁免**（20%是硬止损线） |

**未持仓标的**：以上判断照常计算是否触线，但文案从"建议操作"降级为"🔎 观察提示"——没有仓位可减，只是提醒你该标的价格结构已经很差，避免信号因未持仓而整条消失。

**摘要新增**：120日窗口内的区间回撤/反弹统计——先定位窗口内峰值，再在峰值**之后**的区间找谷值（谷值必须晚于峰值，否则衡量的是涨幅而非回撤，方向会反），回撤=（谷值-峰值）/峰值，反弹=（现价-谷值）/谷值，帮助判断当前价格处于回撤后的哪个阶段。若价格仍在创新低（尚未探底），谷值等于现价，反弹会显示为0%，属正常现象。

**QQQ 单日急跌独立信号**（不计入上述三级，并行展示）：单日跌幅≥5%时触发——若当前处于高估区，提示"估值偏贵，建议减仓1/3"；若处于低估区，提示"可能是加仓机会"。

价格数据来源：`etf_price.csv`（由 `simple_etf_metrics.py` 生成）。

> ⚠️ 基本面暴雷属于独立预警，见下方模块。价格信号触发后应同步核查基本面再决策。

---

### 基本面暴雷预警

数据源：东方财富全球财经快讯（`ak.stock_info_global_em`，免key，30分钟内复用缓存）。

```
本地关键词粗筛（命中才继续，未命中直接判定"未知"，不用AI脑补）
   → AI 相关性过滤（剔除关键词撞车但实际不相关的新闻，如"军工"误中无关快讯）
   → AI 最终判断：疑似暴雷/关注/正常 + 置信度 + 摘要 + 逐条新闻正负面分类
```

| 预警等级 | 含义 | 处理方式 |
|---------|------|---------|
| ✅ 正常 | 近期无重大基本面异常 | 无需操作 |
| ⚠️ 关注 | 存在值得注意的信息 | 持续关注，暂不操作 |
| 🚨 疑似暴雷 | 检测到重大负面事件 | **需人工确认后才可触发减仓/清仓** |

宽基/国别指数（`000300` `000688` `SPY` `QQQ` `EWQ` `EWG` `EWJ` `EEM`）成分股高度分散，本模块不适用，直接跳过。其余标的（含红利、医疗、军工、稀土、半导体、有色金属、互联网等主题指数）各有专属关键词表，用于本地粗筛。

**重要设计约束：本模块仅输出置信度预警标志位，不接任何自动交易执行链**，"疑似暴雷"必须人工核实。

调用 AI 接口带限流感知重试（429 时按 5s→10s→20s 退避，最多重试3次；连续两次调用间隔至少2秒）。

---

### HSTECH 专属：PS / PSY

恒生科技成分股多为早期高增长亏损公司，PE 因净利润为负而失真，改用营收口径：

```
PS  = 月末总市值 / TTM总营收
PSY = 1/PS − CN10Y
```

PSY 用于替代 ERP 参与胜率/赔率计算，斜率信号和减仓信号同样适用。

---

### ETF 执行质量指标

`data/simple_etf_metrics.csv` 每日生成并随commit写回本仓库。数据源已从 Tushare 切换为纯 AKShare（不再需要 `TUSHARE_TOKEN`）：

> ⚠️ 新增新鲜度校验：对比前一日快照，折溢价率/换手分位/波动率/跟踪误差任一字段连续 3 次未变化，会在 `stale_flag`/`stale_note` 列标记预警（逻辑与 `erp_position.py` 里 PE/PSY 的新鲜度校验平行，互不覆盖）。

- ETF 行情：`ak.fund_etf_hist_sina()`（新浪）
- ETF 净值（折溢价用）：`ak.fund_etf_fund_info_em()`（东方财富）
- 基准指数：`ak.stock_zh_index_hist_csindex()`（中证官网）

`ThreadPoolExecutor(max_workers=5)` 并发处理各 ETF，遇到限流可调小 `MAX_WORKERS`。

| 指标 | 说明 |
|-----|------|
| 折溢价率 | 净值 vs 收盘价，决定买入实际成本 |
| 换手率分位 | 资金活跃度，配合价格方向判断真实性 |
| 价格/换手背离 | 量价不配合，警惕假突破 |
| 年化波动率/回撤分位 | 当前风险水位 |
| 超额收益均值 | ETF 跟踪质量，长期负值考虑换标的 |

---

### 决策仪表盘

- 触发止损/清仓的标的单独置顶「🚨 需要处理」区，不与正常估值区间条目混排
- 其余标的按估值区间分组展示（极度低估 → 显著低估 → 合理偏低 → 合理区间 → 高估/规避）
- 📌 标记自选持仓标的（`HOLDING_CATEGORY` 配置）
- 每条自动生成一句执行动作建议（结合折溢价/换手背离/波动率综合判断）
- 微信只推仪表盘摘要 + 完整报告链接，完整版在 `docs/report.html`（gh-pages）查看

---

## 仓位框架

三仓结构，各自独立触发：

| 仓位 | 触发条件 | 比例 |
|-----|---------|------|
| **泡沫底仓** | ERP ≥ P25 | 30% |
| | ERP < P25 | 5% |
| **价值主力** | ERP ≥ P75 | 40% |
| | ERP ≥ P50 | 35% |
| | ERP ≥ P25 | 10% |
| | ERP < P25 | 0% |
| **投机奇兵** | ERP ≥ P95 | 30% |
| | ERP ≥ P90 | 20% |
| | ERP ≥ P50 | 10% |
| | ERP < P50 | 5% |

> 触发减仓/清仓信号（`exit_level > 0`）时，不展示常规三仓拆分，改为提示已进入止损流程。

---

## 环境变量（GitHub Actions Secrets）

| 变量 | 用途 | 必填 |
|-----|------|------|
| `SCT_KEY` | ServerChan 微信推送 Key | 是 |
| `ANTHROPIC_API_KEY` | 七牛云 API Key（Anthropic 兼容接口 `api.qnaigc.com`），供基本面暴雷预警模块调用 | 是 |
| `SHILLER_PATH` | Shiller CAPE 数据文件路径，默认 `./data/ie_data.xls` | 否 |
| `GH_PAT` | GitHub Personal Access Token，供 Claude Cowork 通过 Chrome MCP 模拟操作（如写入 Variable） | 是 |

## GitHub Actions Variables

| 变量 | 用途 | 更新方式 |
|-----|------|---------|
| `QQQ_PE_TODAY` | QQQ 今日 PE | 由 Claude Cowork 每日自动写入 |

---

## 手动维护事项

- **Shiller CAPE**：`./data/ie_data.xls` 需从 [Robert Shiller 网站](http://www.econ.yale.edu/~shiller/data.htm) 手动下载，仅用于 SPY 长期回报锚分析，建议每月月初检查更新。
