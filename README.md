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
