# ERP 策略每日监控报告

每日自动计算各主要指数的股权风险溢价（ERP），结合胜率/赔率框架输出仓位建议，并推送微信。

## 已完成

- ERP / PSY 历史数据采集（全量 + 增量）
- 胜率 / 赔率 / 仓位建议框架
- ETF 执行质量模块（折溢价、换手、波动、超额收益）
- Shiller CAPE 长期回报锚（仅 SPY）
- HSTECH PS / PSY 自建数据管道
- ERP 斜率信号（近20日，五档分类，含恐慌踩踏识别）
- 减仓 / 清仓信号（估值门控 + 均线 + 回撤三条件）
- 基本面暴雷预警（AI辅助，仅输出标志位，需人工确认）

## TODO

- 加入其他信号指标

---


## 整体执行流程

```
每日触发（GitHub Actions）
│
├── 1. fetch_bond_yield_incremental.py   ← 增量拉取国债 + PE，更新 erp_*.csv
│        ├── 中国国债：akshare
│        ├── 美/法/德/日国债：FRED API
│        ├── A股PE：中证指数官网（akshare）
│        ├── 美股PE：worldperatio.com（SPY）/ 手动填入（QQQ）
│        ├── 欧日新兴PE：worldperatio.com（当日值）
│        └── HSTECH：yfinance 市值 + akshare 财报 → 自建 PS/PSY/PE/ERP
│
├── 2. etf_metrics.py（从另一 Repo Release 取数）
│        └── simple_etf_metrics.csv → 折溢价 / 换手 / 波动 / 超额收益
│
└── 3. erp_position.py                   ← 生成报告 + 微信推送
         ├── 读取 erp_*.csv（各指数 ERP 历史）
         ├── 读取 ps_HSTECH.csv（恒生科技 PS/PSY）
         ├── 读取 simple_etf_metrics.csv（ETF 执行质量）
         ├── 读取 etf_price.csv（ETF 价格序列，用于减仓信号）
         └── 输出：胜率 / 赔率 / 仓位建议 / ERP斜率信号 / 减仓信号 / 基本面预警 / ETF折溢价
```

**首次使用**须先跑全量脚本 `fetch_bond_yield.py` 建立历史数据，之后每日跑增量脚本即可。

---

## 标的一览

| 指数代码 | 指数名称 | 对应 ETF | PE 数据来源 | 国债 | 更新频率 |
|---------|---------|---------|-----------|------|---------|
| 000300 | 沪深300 | 510300.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 000688 | 科创50 | 588000.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 000922 | 中证红利 | 515180.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 399989 | 中证医疗 | 512170.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 931071 | 人工智能 | 159819.SZ | 中证指数官网（akshare） | CN10Y | 日频 |
| 000069 | 消费80 | 510150.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 930781 | 中证影视 | 516620.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| 000989 | 全指可选 | 159936.SZ | 中证指数官网（akshare） | CN10Y | 日频 |
| 931139 | CS消费50 | 515650.SH | 中证指数官网（akshare） | CN10Y | 日频 |
| SPY | S&P 500 | 513500.SH | multpl.com（月频历史）+ worldperatio.com（今日） | US10Y | 月频历史 + 日频今日 |
| QQQ | Nasdaq 100 | 159696.SZ | GuruFocus xlsx（手动下载）+ 手动填入今日值 | US10Y | 手动维护 |
| EWQ | MSCI France | 513080.SH | worldperatio.com（今日值）× SPY比值估算历史 | FR10Y | 日频今日，历史为估算 |
| EWG | MSCI Germany | ─（无A股ETF） | worldperatio.com（今日值）× SPY比值估算历史 | DE10Y | 日频今日，历史为估算 |
| EWJ | MSCI Japan | 513880.SH | worldperatio.com（今日值）× SPY比值估算历史 | JP10Y | 日频今日，历史为估算 |
| EEM | MSCI Emerging | ─（无A股ETF） | worldperatio.com（今日值）× SPY比值估算历史 | CN10Y | 日频今日，历史为估算 |
| HSTECH | 恒生科技 | 513180.SH | 自建：yfinance 市值 + akshare 季报营收/净利润 | CN10Y | 月频 |

> EWQ/EWG/EWJ/EEM 的**历史 PE 为估算值**：以今日该指数与 SPY 的 PE 比值为固定系数，乘以 SPY 历史 PE 序列反推。今日值为 worldperatio.com 真实数据。

---

## 数据文件说明

| 文件 | 生成方式 | 内容 |
|-----|---------|------|
| `data/erp_{CODE}.csv` | `fetch_bond_yield_incremental.py` 每日写入 | Date / PE / Bond_Yield_10Y / ERP |
| `data/ps_HSTECH.csv` | `fetch_bond_yield_incremental.py` 每日写入 | date / ps / psy / pe / erp / rf（月频） |
| `simple_etf_metrics.csv` | 另一 Repo Release 发布，本 Repo 运行时下载 | ETF 折溢价 / 换手 / 波动 / 超额收益（日频） |
| `data/etf_price.csv` | `simple_etf_metrics.py` 每日生成 | ETF 日收盘价序列，供减仓信号模块使用 |

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

**赔率** = 上行空间 / 下行风险（均以 ERP 绝对值衡量，量纲一致）

```
上行空间 = max(P90 − 当前ERP, 0)
下行风险 = 当前ERP − P10
           （若当前ERP ≤ P10，则下行风险 = P90 − P10 全区间）

赔率 = 上行空间 / 下行风险
```

**欧美日锚定区间**：EWQ/EWG/EWJ/SPY/QQQ 使用 2022年1月1日以后的数据作为分位锚。

---

### ERP 斜率信号

基于近20日 ERP（或PSY）线性回归斜率，量化当前市场情绪速度：

| 信号 | 条件（20日ERP绝对变化） | 含义 |
|------|----------------------|------|
| 🚨 恐慌踩踏 | ≥ +2% | PE急速压缩，市场抛售，历史上往往是买点临近的前兆 |
| 🟢 估值快速改善 | +0.8% ~ +2% | 估值持续修复，买入窗口打开 |
| 🟡 横盘震荡 | -0.8% ~ +0.8% | 无明显趋势，保持既有仓位 |
| 🟠 估值快速恶化 | -2% ~ -0.8% | 估值向贵漂移，提高警惕 |
| ⚠️ 情绪过热 | ≤ -2% | 市场情绪快速升温，泡沫化加速，警戒高位 |

斜率信号同步展示在仪表盘和趋势模块中。

---

### 减仓 / 清仓信号

**设计原则：ERP框架（逆向估值）与均线/回撤（趋势跟踪）天然冲突，通过估值门控解决。**

```
估值门控：
  ERP ≥ P50（低估区）→ 屏蔽价格信号，避免恐慌底部错误减仓
  ERP < P50（高估区）→ 激活均线 + 回撤条件
```

激活后的触发规则：

| 信号级别 | 触发条件 | 建议动作 |
|---------|---------|---------|
| ⚠️ 第一次减仓预警 | 从近期高点回撤 ≥10% **且** 跌破20日均线 | 酌情减持部分仓位，观察 |
| 🔴 清仓预警 | 从近期高点回撤 ≥20% **或** 跌破120日均线 | 趋势破坏，建议清仓 |
| 🚨 强烈清仓预警 | 多项条件同时触发 | 大幅减仓或清仓 |

价格数据来源：`etf_price.csv`（由 `simple_etf_metrics.py` 生成）。

> ⚠️ 注意：基本面暴雷属于独立预警，与减仓信号并列展示。价格信号触发后应同步核查基本面再决策。

---

### 基本面暴雷预警

调用 Anthropic API（带 web_search 工具）搜索各标的近30天重大负面新闻，输出三档预警：

| 预警等级 | 含义 | 处理方式 |
|---------|------|---------|
| ✅ 正常 | 近期无重大基本面异常 | 无需操作 |
| ⚠️ 关注 | 存在值得注意的信息 | 持续关注，暂不操作 |
| 🚨 疑似暴雷 | 检测到重大负面事件 | **⚠️ 需人工确认后才可触发减仓/清仓** |

**重要设计约束：本模块仅输出置信度预警标志位，不接任何自动交易执行链。** 清仓动作必须由人工确认。

判断标准包括：核心成分股财务造假/业绩暴雷/退市风险、行业超预期强监管、系统性宏观风险、ETF结构性风险（清盘/停牌）。

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

`simple_etf_metrics.csv` 由另一个 Repo 每日生成并通过 GitHub Release 发布。

| 指标 | 说明 |
|-----|------|
| 折溢价率 | 净值 vs 收盘价，决定买入实际成本 |
| 换手率分位 | 资金活跃度，配合价格方向判断真实性 |
| 价格/换手背离 | 量价不配合，警惕假突破 |
| 年化波动率/回撤分位 | 当前风险水位 |
| 超额收益均值 | ETF 跟踪质量，长期负值考虑换标的 |

---

## 仓位框架

三仓结构，各自独立触发：

| 仓位 | 触发条件 | 比例 |
|-----|---------|------|
| **泡沫底仓** | ERP ≥ P50 | 30% |
| | ERP < P25 | 5% |
| **价值主力** | ERP ≥ P75 | 40% |
| | ERP ≥ P50 | 35% |
| | ERP ≥ P25 | 10% |
| | ERP < P25 | 0% |
| **投机奇兵** | ERP ≥ P95 | 30% |
| | ERP ≥ P90 | 20% |
| | ERP ≥ P50 | 10% |
| | ERP < P50 | 5% |

> 减仓/清仓信号在高估区（ERP < P50）激活后，可覆盖上表的持仓建议。

---

## 环境变量（GitHub Actions Secrets）

| 变量 | 用途 | 必填 |
|-----|------|------|
| `SCT_KEY` | ServerChan 微信推送 Key | 是 |
| `ANTHROPIC_API_KEY` | Anthropic API Key，供基本面暴雷预警模块调用 | 是 |
| `TUSHARE_TOKEN` | ETF 指标数据（另一 Repo 使用） | 是（另一 Repo） |
| `SHILLER_PATH` | Shiller CAPE 数据文件路径，默认 `./data/ie_data.xls` | 否 |
| `GH_PAT` | GitHub Personal Access Token，供 Claude Cowork 通过 Chrome MCP 模拟操作（如写入 Variable） | 是 |

## GitHub Actions Variables

| 变量 | 用途 | 更新方式 |
|-----|------|---------|
| `QQQ_PE_TODAY` | QQQ 今日 PE | 由 Claude Cowork 通过 Chrome MCP 每日自动写入 |

---

## 手动维护事项

- **QQQ PE**：每日由 Claude Cowork 通过 Claude Chrome MCP 自动获取，写入 `QQQ_PE_TODAY` 环境变量传入 GitHub Actions。历史文件不再手动下载维护。
- **Shiller CAPE**：`./data/ie_data.xls` 需从 [Robert Shiller 网站](http://www.econ.yale.edu/~shiller/data.htm) 手动下载，仅用于 SPY 长期回报锚分析，建议每月月初检查更新。
