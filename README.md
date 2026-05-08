# ERP 策略每日监控报告

每日自动计算各主要指数的股权风险溢价（ERP），结合胜率/赔率框架输出仓位建议，并推送微信。

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
         └── 输出：胜率 / 赔率 / 仓位建议 / ETF 折溢价信号
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
上行空间 = max(P90 − 当前ERP, 0)        # 距历史最便宜还有多少余地
下行风险 = 当前ERP − P10                 # 距历史最贵还有多少距离
           （若当前ERP ≤ P10，则下行风险 = P90 − P10 全区间）

赔率 = 上行空间 / 下行风险
```

赔率 > 1x：潜在收益空间大于潜在风险。

**欧美日锚定区间**：EWQ/EWG/EWJ/SPY/QQQ 使用 2022年1月1日以后的数据作为分位锚（规避负利率时代的历史失真）。

---

### HSTECH 专属：PS / PSY

恒生科技成分股多为早期高增长亏损公司，PE 因净利润为负而失真，改用营收口径：

**PS（市销率）**

```
月末总市值    = Σ 成分股月末市值（yfinance，港币）
TTM 总营收   = Σ 成分股最近4个季度单季营收之和（akshare 季报，港币）
PS           = 月末总市值 / TTM总营收
```

**PSY（营收口径股权风险溢价）**

```
PSY = 1/PS − CN10Y（中国10年期国债收益率）
```

PSY 越高：相对无风险利率越便宜。PSY 用于替代 ERP 参与胜率/赔率计算。

**季报单季营收拆算**（累计报 → 单季）

```
Q1 单季 = 一季报累计值
Q2 单季 = 半年报累计值 − Q1
Q3 单季 = 三季报累计值 − 半年报累计值
Q4 单季 = 年报累计值   − 三季报累计值
```

---

### ETF 执行质量指标（来自另一 Repo）

`simple_etf_metrics.csv` 由另一个 Repo 的 `simple_etf_metrics.py` 每日生成并通过 GitHub Release 发布，本 Repo 运行时从 Release 最新版本下载。

| 指标 | 计算逻辑 |
|-----|---------|
| **折溢价率** | `(单位净值 − 收盘价) / 单位净值`，净值来自 Tushare `fund_nav` |
| **折溢价1年分位** | 近1年折溢价序列的历史百分位排名 |
| **折溢价5/10日变化** | `当日折溢价 − 5/10个交易日前折溢价` |
| **换手率（近似）** | 近1年日均成交额（AUM 未知时的流动性近似） |
| **换手分位** | 近52周周成交额的历史百分位排名 |
| **价格/换手背离** | `sign(近5日价格变化) ≠ sign(本周成交额环比变化)` 则为背离 |
| **年化波动率** | 20日滚动收益率标准差 × √250 |
| **波动率1年分位** | 近1年滚动波动率序列的历史百分位排名 |
| **最大回撤** | 累计净值序列的历史最大峰谷回撤 |
| **回撤1年分位** | 近1年20日滚动最大回撤的历史百分位排名 |
| **超额收益均值** | ETF日收益率 − 基准指数日收益率，取近3年均值 |
| **跟踪误差** | 超额收益日序列标准差 × √250（年化） |
| **MA趋势斜率** | 对5/10/15/20日超额收益MA做线性回归，斜率反映超额动量方向 |

超额收益基准对应关系：

| ETF | 基准指数 |
|-----|---------|
| 510300.SH | 000300.SH（沪深300） |
| 588000.SH | 000688.SH（科创50） |
| 515180.SH | 000922.CSI（中证红利） |
| 512170.SH | 399989.SZ（中证医疗） |
| 159819.SZ | 931071.CSI（人工智能） |
| 510150.SH | 000069.CSI（消费80） |
| 516620.SH | 930781.CSI（中证影视） |
| 159936.SZ | 000989.CSI（全指可选） |
| 515650.SH | 931139.CSI（CS消费50） |
| 513180.SH / 513500.SH / 159696.SZ / 513080.SH / 513880.SH | ─（无可用A股基准，超额收益留空） |

---

## 仓位框架

三仓结构，各自独立触发：

| 仓位 | 触发条件 | 比例 |
|-----|---------|------|
| **泡沫底仓** | ERP ≥ P50：长期锁定 | 30% |
| | ERP < P25：极致溢价，考虑减仓 | 5% |
| **价值主力** | ERP ≥ P75：足够便宜 | 40% |
| | ERP ≥ P50：估值修复中 | 35% |
| | ERP ≥ P25：开始减持 | 10% |
| | ERP < P25：全部离场 | 0% |
| **投机奇兵** | ERP ≥ P95：极端惯性下跌 | 30% |
| | ERP ≥ P90：极低估区 | 20% |
| | ERP ≥ P50：震荡区 | 10% |
| | ERP < P50：缩减观察 | 5% |

---

## 环境变量

| 变量 | 用途 | 必填 |
|-----|------|------|
| `SCT_KEY` | ServerChan 微信推送 Key | 是 |
| `TUSHARE_TOKEN` | ETF 指标数据（另一 Repo 使用） | 是（另一 Repo） |
| `QQQ_PE_TODAY` | QQQ 今日 PE 手动值（GitHub Actions 传入） | 建议每日填写 |
| `SHILLER_PATH` | Shiller CAPE 数据文件路径，默认 `./data/ie_data.xls` | 否 |

---

## 手动维护事项

- **QQQ PE**：每日运行前从 [GuruFocus](https://www.gurufocus.com/economic_indicators/6778/nasdaq-100-pe-ratio) 查询最新值，通过 `QQQ_PE_TODAY` 环境变量传入，或直接填写脚本顶部变量。
- **QQQ PE 历史文件**：`./data/qqq_pe_gurufocus.xlsx` 需从 GuruFocus 手动下载，放入 `data/` 目录（仅全量初始化时需要）。
- **Shiller CAPE**：`./data/ie_data.xls` 需从 [Robert Shiller 网站](http://www.econ.yale.edu/~shiller/data.htm) 手动下载，仅用于 SPY 的长期回报锚分析。数据按月更新但无固定日期，建议每月月初手动检查并替换文件。
