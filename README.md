# ERP 监控系统技术说明文档

## 一、各指数数据源与更新方式

### 1. 中国国债（CN10Y）

| 项目 | 说明 |
|------|------|
| **数据源** | akshare：`bond_china_yield` |
| **更新频率** | 日频（交易日） |
| **全量获取** | 循环 2006 年至今，每年拉取一次 |
| **增量获取** | 近 30 天 |
| **取值字段** | 中债国债收益率曲线 → 10 年 |
| **单位转换** | 原始值 ÷ 100 |

---

### 2. 美国 / 法国 / 德国 / 日本 10 年国债

| 项目 | 说明 |
|------|------|
| **数据源** | FRED API（圣路易斯联储） |
| **API Key** | `a8ce66c09bbcedfb9e33de739a0dcbfb` |
| **更新频率** | 日频 |
| **全量获取** | 2005‑01‑01 至今 |
| **增量获取** | 近 60 天 |
| **映射关系** | `US10Y` → `DGS10`<br>`FR10Y` → `IRLTLT01FRM156N`<br>`DE10Y` → `IRLTLT01DEM156N`<br>`JP10Y` → `IRLTLT01JPM156N` |
| **单位转换** | 原始值 ÷ 100 |

---

### 3. 中国 A 股指数（沪深300 / 科创50 / 中证红利 / 中证医疗 / 人工智能）

| 项目 | 说明 |
|------|------|
| **数据源** | akshare：`stock_zh_index_hist_csindex` |
| **更新频率** | 日频 |
| **全量获取** | 2005‑04‑08 至今 |
| **增量获取** | 近 30 天 |
| **取值字段** | 滚动市盈率（TTM PE） |
| **国债锚定** | CN10Y |

---

### 4. S&P 500（SPY）

| 项目 | 说明 |
|------|------|
| **数据源** | multpl.com（历史月频） + worldperatio.com（今日值） |
| **更新频率** | 月频 → 日频（今日值单独填充） |
| **全量获取** | 爬取 multpl 表格（by month） |
| **增量获取** | 今日值从 worldperatio 获取 |
| **计算方式** | 历史月频数据 + 今日 PE 追加 |
| **国债锚定** | US10Y |

---

### 5. Nasdaq 100（QQQ）

| 项目 | 说明 |
|------|------|
| **数据源** | GuruFocus 下载的 Excel（本地 `./data/qqq_pe_gurufocus.xlsx`） |
| **更新频率** | 日频 |
| **全量获取** | 读取本地 Excel（skiprows=4，取前两列） |
| **增量获取** | 手动填写 `QQQ_PE_TODAY`（环境变量或硬编码） |
| **数据说明** | TTM PE，与 GuruFocus 网站一致 |
| **国债锚定** | US10Y |

---

### 6. MSCI 各国 / 新兴市场（EWQ / EWG / EWJ / EEM）

| 项目 | 说明 |
|------|------|
| **数据源** | worldperatio.com（今日值） + SPY 历史 × 今日比值（历史估算） |
| **更新频率** | 今日值 → 日频，历史为估算 |
| **全量获取** | `PE_history = SPY_history × (PE_today_local / PE_today_SPY)` |
| **增量获取** | 今日值从 worldperatio 获取 |
| **局限性** | 历史数据为线性缩放估算，非真实历史 |
| **国债锚定** | EWQ → FR10Y<br>EWG → DE10Y<br>EWJ → JP10Y<br>EEM → CN10Y |

---

### 7. 恒生科技指数（HSTECH）

#### 数据源

| 类型 | 数据源 |
|------|--------|
| **成分股财务** | akshare：`stock_financial_hk_report_em`（利润表） |
| **成分股市值** | yfinance：`Ticker.history` + `fast_info.shares` |

#### 更新频率与方式

| 项目 | 说明 |
|------|------|
| **全量计算** | `fetch_ps_20260424.py`（一次性） |
| **增量更新** | `fetch_bond_yield_incremental_20260424.py` 自动执行 |
| **更新触发** | 每次增量脚本运行，重新计算最近 3 个月 |
| **计算频率** | 月末频率 |

#### 计算逻辑

| 指标 | 公式 |
|------|------|
| **TTM 营收** | 单季度营收滚动 4 个季度求和 |
| **TTM 净利润** | 单季度净利润滚动 4 个季度求和 |
| **月末总市值** | 收盘价 × 总股本（取月末最后一天） |
| **PS** | 总市值 ÷ TTM 总营收 |
| **PE** | 总市值 ÷ TTM 总净利润 |
| **PSY** | 1/PS − 中国 10 年国债收益率 |
| **ERP** | 1/PE − 中国 10 年国债收益率 |

#### 特殊处理

- 累计利润表 → 单季度转换（Q1/Q2/Q3/Q4 差分）
- 成分股共 29 只（0700.HK 等）
- 若 PE 缺失，ERP 为空；PSY 作为替代估值指标
- 国债收益率复用 CN10Y 月末值

---

## 二、完整指数清单

| 代码 | 名称 | PE 来源 | 国债锚定 |
|------|------|---------|----------|
| 000300 | 沪深300 | csindex（akshare） | CN10Y |
| 000688 | 科创50 | csindex（akshare） | CN10Y |
| 000922 | 中证红利 | csindex（akshare） | CN10Y |
| 399989 | 中证医疗 | csindex（akshare） | CN10Y |
| 931071 | 人工智能 | csindex（akshare） | CN10Y |
| SPY | S&P 500 | multpl + worldperatio | US10Y |
| QQQ | Nasdaq 100 | GuruFocus CSV + 手动 | US10Y |
| EWQ | MSCI France | worldperatio（今日） + SPY 估算（历史） | FR10Y |
| EWG | MSCI Germany | worldperatio（今日） + SPY 估算（历史） | DE10Y |
| EWJ | MSCI Japan | worldperatio（今日） + SPY 估算（历史） | JP10Y |
| EEM | MSCI Emerging | worldperatio（今日） + SPY 估算（历史） | CN10Y |
| HSTECH | 恒生科技指数 | akshare（财务） + yfinance（市值） | CN10Y |

---

## 三、ETF 执行质量指标（simple_etf_metrics.py）

### 1. 数据接口

| 项目 | 说明 |
|------|------|
| **数据源** | Tushare Pro |
| **接口函数** | `fund_daily`（日行情）、`fund_nav`（净值） |
| **认证方式** | 环境变量 `TUSHARE_TOKEN` |
| **拉取范围** | 最近 365 天 |
| **更新频率** | 每次运行全量拉取过去 1 年 |

### 2. ETF 标的映射

| ERP 代码 | 名称 | Tushare 代码 |
|----------|------|--------------|
| 000300 | 沪深300 | 510300.SH |
| 000688 | 科创50 | 588000.SH |
| 000922 | 中证红利 | 515180.SH |
| 399989 | 中证医疗 | 512170.SH |
| 931071 | 人工智能 | 159819.SZ |
| HSTECH | 恒生科技 | 513180.SH |
| SPY | 标普500 | 513500.SH |
| QQQ | 纳斯达克 | 159696.SZ |
| EWQ | 法国ETF | 513080.SH |
| EWJ | 日本ETF | 513880.SH |

### 3. 计算指标

| 指标 | 公式 | 数据来源字段 |
|------|------|--------------|
| 折溢价率 | `(unit_nav - close) / unit_nav` | `fund_daily.close` + `fund_nav.unit_nav` |
| 成交额（万元） | `amount / 10000` | `fund_daily.amount`（单位：千元） |
| 5日/10日平均成交额 | 最近 N 个交易日成交额均值 | `fund_daily.amount` |
| 年化波动率 | `日收益率标准差 × √252` | `fund_daily.pct_chg` |
| 最大回撤 | `max((峰值-当前)/峰值)` | `fund_daily.close` 计算累积收益 |
| 最新涨跌幅 | 当日涨跌幅 | `fund_daily.pct_chg` |

### 4. 输出文件

| 文件 | 格式 | 更新方式 |
|------|------|----------|
| `simple_etf_metrics.csv` | CSV | 每次运行全量覆盖 |

---

## 四、ERP 决策报告（erp_position.py）

### 1. 数据来源整合

| 数据 | 来源文件 |
|------|----------|
| 各指数 ERP | `data/erp_{code}.csv` |
| 恒生科技 PS/PSY | `data/ps_HSTECH.csv` |
| ETF 执行质量 | `simple_etf_metrics.csv` |
| Shiller CAPE | `data/ie_data.xls`（手动维护） |

### 2. 核心判断逻辑

| 判断项 | 计算方式 |
|--------|----------|
| 估值区间 | 当前 ERP 在历史序列中的分位（P90/P75/P50/P25/P10） |
| 趋势判断 | 最近 10 个有效 ERP 点的线性回归斜率 |
| 仓位建议 | 三层结构：泡沫仓（5-30%）+ 价值仓（0-40%）+ 投机仓（5-30%） |
| 颜色映射 | ≥P75 🟢 / P50-P75 🟡 / P25-P50 🟠 / P10-P25 🔴 / <P10 🚨 |

### 3. 特殊处理

| 场景 | 处理方式 |
|------|----------|
| 欧日美负利率指数 | 用 2022‑01‑01 至今数据做锚 |
| 境外 PE 历史缺失 | 用 SPY 历史 × 今日比值估算 |
| 恒生科技 PE 缺失 | 用 PSY 替代估值 |
| 日频/月频指数 | 日频取最近 10 个交易日，月频取最近 10 个月末 |

### 4. 输出

| 输出方式 | 内容 |
|----------|------|
| 控制台打印 | Markdown 格式报告 |
| 微信推送（方糖） | 全量报告，需 `SCT_KEY` 环境变量 |
| 报告标题 | `ERP 决策报告 (YYYY-MM-DD)` |

---

## 五、自动化部署（daily_trade.yml）

| 项目 | 说明 |
|------|------|
| **运行平台** | GitHub Actions |
| **触发时间** | 每天 `30 10 * * *`（UTC 10:30 = 北京时间 18:30） |
| **手动触发** | 支持 `workflow_dispatch` |
| **Python 版本** | 3.10 |

### 执行步骤

| 步骤 | 命令 / 说明 |
|------|-------------|
| 1. 检出代码 | `actions/checkout@v4` |
| 2. 设置 Python | `actions/setup-python@v5` |
| 3. 安装依赖 | `pip install -r requirements.txt` |
| 4. 增量抓取数据 | `python fetch_bond_yield_incremental_20260424.py` |
| 5. 分析并推送 | `python erp_position.py`（需 `SCT_KEY` 和 `ETF_METRICS_URL`） |
| 6. 提交更新 | `git add data/` → `git commit` → `git push` |

### 环境变量

| 变量 | 用途 | 来源 |
|------|------|------|
| `SCT_KEY` | 方糖微信推送 | GitHub Secrets |
| `TUSHARE_TOKEN` | Tushare API | GitHub Secrets |
| `QQQ_PE_TODAY` | QQQ 今日 PE | GitHub Secrets |
| `ETF_METRICS_URL` | ETF 指标 CSV 地址 | 硬编码（外部 Release） |

---

**文档版本**：v2.0  
**最后更新**：2026-04-28
