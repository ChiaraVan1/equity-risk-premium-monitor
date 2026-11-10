# ERP 策略每日监控报告

## 项目状态

TODO / 已知问题 / 变更日志见 [STATUS.md](./STATUS.md)。

---

## 决策规则（唯一口径）

本报告只执行以下 5 条：

| 环节 | 规则 |
|---|---|
| 买不买 | 看 ERP 分位：>90 极便宜 / 60–90 便宜 / 25–60 中性只持有 / <25 贵不加 |
| 加多少 | 指数 3,000/档、个股 5,000/档，取 min(定额, 6% 上限剩余空间)。不重仓 |
| 何时加 | 相对上次加仓价每跌 5% 一档；同只当月最多一档；当月总额 ≤ 总资产 5% |
| 何时卖 | 只认分位 <25%。每次减一档、每月最多一档。**不设价格止损**；个股只在「公司变坏」时走 |
| 默认 | 以上都不触发 → **不动** |

不参与决策（只提示）：MA20、换手背离、Shiller（长期维度）。

---

## 整体执行流程

```
配置层
├─ config.json / industry_map.json
└─ config_loader.py → ALL_INDICES / BOND_YIELD_CONFIG / HOLDING_CATEGORY / INDICES_LIST / HSTECH_TICKERS
        ↓

数据生产层（fetch/）
├─ simple_etf_metrics.py           → etf_price.csv, etf_nav.csv, index_pct.csv, simple_etf_metrics.csv
├─ fetch_etf_adjusted_price.py     → etf_price_adj.csv（QuantDash 前复权）
├─ fetch_bond_yield.py             → erp_*.csv（全量，首次建库）
├─ fetch_bond_yield_incremental.py → erp_*.csv（增量）+ ps_HSTECH.csv
├─ fetch_ps.py                     → ps_HSTECH.csv（手动全量重算，不在每日调度）
├─ dividend_yield_fetch.py         → dividend_yield/dyr_*.csv
└─ freshness.py                    → 统一新鲜度校验
        ↓
data/freshness_report.json → prepare_all_data.py 汇总打印
        ↓

prepare_all_data.py（subprocess 跑 fetch/ 脚本 + 聚合所有 CSV）
        ↓

分析层（analysis/）
├─ valuation.py  risk.py  trend.py  sentiment.py
├─ etf_quality.py  dividend_yield_analysis.py
        ↓
analyze_and_suggest(code) → 拼成单标的 Markdown
        ↓

报告层（report/）markdown.py → 仪表盘 + HTML → docs/report.html → gh-pages
        ↓
erp_position.py（主入口）
```

首次使用先跑 `fetch_bond_yield.py` 建历史，之后每日跑增量。

> 新增任何数据源接入 `fetch/`，必须调用 `freshness.py` 并把结果汇总进 `data/freshness_report.json`。

### 每日更新链路

1. cron-job.org 每天 18:30（6:30 PM）CST 触发 `workflow_dispatch`。
2. 云端是唯一写入方：ETF 指标 → 前复权（QuantDash）→ 国债 PE → 股息率 → 报告 → gh-pages → 微信推送。

---

## 接口信心

| 接口 | 接口地址 | 本地 | 云端 | 限制 |
|---|---|---|---|---|
| QuantDash | `api.quantdash.net`（quantdash SDK） | 可接 | 可接（26/26，约 2 分钟） | 限流 9 次/分钟，需等 59s；失败回落未复权 |
| 新浪行情 | `finance.sina.com.cn/realstock/company/{symbol}/hisdata_klc2/klc_kl.js` | 可接 | 可接 | 日线当天晚间才更新，收盘后立刻跑只拿得到前一日；不支持日期范围，每次整表 |
| 中证指数 | `www.csindex.com.cn/csindex-home/perf/index-perf` | 可接 | 可接 | 偶发缺 `IndexClose`，会致 PE ffill、streak 误报 |
| 东财净值 | `api.fund.eastmoney.com/f10/lsjz` | 可接 | 未验证 | 盘后晚间才发布 |
| 东财 K 线 | `push2his.eastmoney.com/api/qt/stock/kline/get` | 可接（200 / 80ms） | **不可接**（2026-09-02 直连被断） | 风控限流，批量高频易 403 |
| 东财快讯 | `np-weblist.eastmoney.com/comm/web/getFastNewsList` | 可接 | 可接 | 30 分钟缓存 |
| 理杏仁 | `open.lixinger.com/api/cn/index/fundamental` | 可接 | 可接 | 需 token，失效即整列缺失 |
| FRED（国债收益率） | `api.stlouisfed.org/fred/series/observations` | 可接 | 可接 | 需 api_key（明文写在 `fetch_bond_yield*.py` 里，非 Secret）；免费额度 120 次/分钟；增量只拉近 60 天 |
| multpl.com | `www.multpl.com/s-p-500-pe-ratio/table/by-month` | 可接 | 未验证 | 网页结构变动即断 |
| worldperatio.com | `worldperatio.com/major-stock-index-pe-ratios` | 可接 | 未验证 | 网页结构变动即断 |
| GuruFocus / `QQQ_PE_TODAY` | `www.gurufocus.com/economic_indicators/6778/nasdaq-100-pe-ratio` | 只能人工 | 只能人工 | 断更则沿用旧值 |
| yfinance | `query1.finance.yahoo.com/v8/finance/chart/{symbol}`（SDK 内部） | 可接 | 可接 | 季报口径、市值时点易偏 |

> 本地能不能接 ≠ 云端能不能接；目前唯一已知两边不一致的是东财 K 线接口。其余源"今天没数据"与"今天没更新"无法自证，靠 `freshness_report.json` 兜。

---

## 标的一览

| 指数代码 | 指数名称 | 对应 ETF | PE 数据来源 | 国债 | 更新频率 |
|---------|---------|---------|-----------|------|---------|
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
| 930794 | 中美互联网 | ─（无对应A股ETF） | 中证指数官网（akshare） | CN10Y | 日频 |
| 000819 | 有色金属 | 512400.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 950125 | 半导体材料设备 | 588710.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 399986 | 中证银行 | 512800.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 930633 | 中证旅游 | 159766.SZ | 中证指数官网（akshare） | CN10Y | 日频 |
| 931946 | 畜牧养殖 | 159172.SZ | 中证指数官网（akshare） | CN10Y | 日频 |
| 980032 | 新能电池 | 159755.SZ | 理杏仁 | CN10Y | 日频 |
| SPY | S&P 500 | 513500.SH | multpl.com（历史）+ worldperatio.com（今日） | US10Y | 月频+日频 |
| QQQ | Nasdaq 100 | 159696.SZ | GuruFocus xlsx（手动）+ 今日值手填 | US10Y | 手动 |
| EWQ | MSCI France | 513080.SH | worldperatio.com 今日 × SPY 比值估历史 | FR10Y | 日频，历史估算 |
| EWG | MSCI Germany | 159561.SZ | 同上 | DE10Y | 日频，历史估算 |
| EWJ | MSCI Japan | 513880.SH | 同上 | JP10Y | 日频，历史估算 |
| EEM | MSCI Emerging | 520580.SH | 同上 | CN10Y | 日频，历史估算 |
| HSTECH | 恒生科技 | 513180.SH | 自建：yfinance 市值 + akshare 季报 | CN10Y | 月频 |
| 931637 | 港股通互联网 | 513770.SH | 中证指数官网（akshare） | CN10Y | 日频 |

> EWQ/EWG/EWJ/EEM 历史 PE 为估算值（今日 PE 与 SPY 比值 × SPY 历史序列），长期精度有限。
>
> 报告只展开持仓 10 只（`config.json` 中 `holding: true`），其余 16 只作为观察池。

---

## 核心计算逻辑

### ERP（股权风险溢价）

```
ERP  = 1/PE − 无风险利率（10Y 国债收益率）
胜率 = 历史 ERP 中 < 当前 ERP 的样本数 / 总样本数
赔率 = (当前ERP − P10) / (P90 − 当前ERP)
    P90 − ERP ≤ 0 → ∞（极度低估）；ERP − P10 ≤ 0 → 0（极度高估）
```

欧美日锚定区间：EWQ/EWG/EWJ/SPY/QQQ 只用 2022-01-01 以后的数据算分位。

### ERP 斜率信号

近 20 日 ERP（或 PSY）线性回归斜率，阈值取**该标的自身历史变化的分位**——不用固定百分比，否则高 PE 标的永不触发。

| 信号 | 历史分位 | 含义 |
|------|---------|------|
| 🚨 恐慌踩踏 | ≥90 | PE 急速压缩，历史少见 |
| 🟢 估值快速改善 | 65~90 | 估值修复较快 |
| 🟡 横盘震荡 | 中间区间 | 变化不明显 |
| 🟠 估值快速恶化 | 65~90（反向） | 估值变贵偏快 |
| ⚠️ 情绪过热 | ≥90（反向） | 高位仍在快速变贵 |

### 区间回撤口径

- **120 日区间回撤/反弹**：峰值之后找谷值（谷值须晚于峰值），回撤=(谷−峰)/峰，反弹=(现价−谷)/谷；仍在创新低时反弹显示 0%。
- **价格源**：优先 `etf_price_adj.csv`（前复权）；缺失或滞后超 5 交易日则回退 `etf_price.csv` 并在报告标出原因。价格过期时信号降为「⛔ 信号暂停」，不输出任何操作提示。

> 基本面暴雷是独立预警，与价格无关，只作提示。

### 基本面暴雷预警

数据源：`ak.stock_info_global_em`（东财全球快讯，免 key，30 分钟缓存）。

```
本地关键词粗筛（未命中直接判「未知」，不让 AI 脑补）
  → AI 相关性过滤（剔关键词撞车）
  → AI 定级：疑似暴雷 / 关注 / 正常 + 置信度 + 摘要
```

| 等级 | 含义 | 处理 |
|-----|------|------|
| ✅ 正常 | 无重大异常 | 无需操作 |
| ⚠️ 关注 | 有值得注意的信息 | 观察 |
| 🚨 疑似暴雷 | 重大负面事件 | **只提示，不触发任何操作**，人工核查 |

宽基/国别指数（000300/000688/SPY/QQQ/EWQ/EWG/EWJ/EEM）成分分散，直接跳过；其余主题指数各有关键词表。**只输出标志位，不接任何自动交易链。** AI 调用带 429 退避（5s→10s→20s，最多 3 次），间隔≥2s。

### HSTECH 专属：PS / PSY

成分股多为亏损期高增长公司，PE 失真，改营收口径：

```
PS  = 月末总市值 / TTM 总营收
PSY = 1/PS − CN10Y
```

PSY 替代 ERP 参与胜率/赔率与斜率计算。

### ETF 执行质量

数据源：行情 `fund_etf_hist_sina`（新浪）｜净值 `fund_etf_fund_info_em`（东财）｜基准 `stock_zh_index_hist_csindex`（中证）。`MAX_WORKERS = 1` 串行，压限流风险。折溢价/换手分位/波动/跟踪误差连续 3 次不变 → `stale_flag` 预警。

| 指标 | 说明 |
|-----|------|
| 折溢价率 | 净值 vs 收盘价，决定买入实际成本 |
| 换手率分位 | 资金活跃度（原始数据保留，不入报告） |
| 年化波动率/回撤分位 | 当前风险水位 |
| 超额收益均值 | 跟踪质量，长期负值考虑换标的 |

### 决策仪表盘

- 需要操作的标的置顶（分位 <25% 可减、或市值超 6% 上限），不与估值分组混排
- 其余按估值区间分组（极度低估 → 显著低估 → 合理偏低 → 合理 → 高估/规避）
- 📌 标自选持仓（`HOLDING_CATEGORY`）；每条给一句执行建议
- 微信只推摘要 + 报告链接，完整版在 `docs/report.html`

---

## 仓位框架

单档金额指数 3,000 / 个股 5,000；单只上限总资产 6%，单档取 min(定额, 6% 上限剩余空间)；每跌 5% 一档，同只当月最多一档，当月总额 ≤ 总资产 5%；**不设重仓**。

---

## 环境变量

| 变量 | 用途 | 位置 |
|-----|------|------|
| `QUANTDASH_API_KEY` | 云端抓 ETF 前复权 | GitHub Secrets；本机 `~/.config/equity-risk-premium-monitor/quantdash.env` 仅手动补数用 |
| `SCT_KEY` | ServerChan 微信推送 | Secrets，必填 |
| `ALIYUN_API_KEY` | DashScope，趋势/ETF 质量 AI 解读 | Secrets，必填 |
| `ANTHROPIC_API_KEY` | 七牛云（Anthropic 兼容）基本面预警 | Secrets，必填 |
| `LIXINGER_TOKEN` | 理杏仁股息率与 980032 PE | Secrets，必填 |
| `GH_PAT` | 写 Variable 等管理操作 | Secrets，必填 |
| `SHILLER_PATH` | Shiller CAPE 文件路径，默认 `./data/ie_data.xls` | 选填 |
| `DRY_RUN` | `true` 时预览不推送，写 `output_preview.md` | 普通变量，选填 |
| `QQQ_PE_TODAY` | QQQ 今日 PE | Actions Variable，由 Grok 定时任务写入 |

---

## 手动维护事项

- **Shiller CAPE**：`data/ie_data.xls` 从 Shiller 官网手动下载，仅 SPY 用，建议月初检查。
- **QQQ PE**：GuruFocus xlsx + `QQQ_PE_TODAY` 手填，断更则 QQQ ERP 沿用旧值。
