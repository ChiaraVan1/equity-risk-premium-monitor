## 一、指标定义

本系统计算的**核心指标为股权风险溢价（ERP）**：

| 指标名称 | 计算公式 | 说明 |
|---------|---------|------|
| **ERP** (Equity Risk Premium) | ERP = (1/PE) - 国债收益率_10Y | 股票相对于无风险资产的超额收益 |
| **PE** (市盈率) | 指数收盘价 / 指数成分股净利润(TTM) | 指数估值水平的倒数 |
| **国债收益率_10Y** | 各国家10年期国债到期收益率 | 无风险利率基准 |

---

## 二、数据接口来源

### 2.1 国债收益率数据

| 国债代码 | 国家/地区 | 数据来源 | 接口/地址 |
|---------|----------|---------|----------|
| CN10Y | 中国 | akshare | `ak.bond_china_yield()`，筛选"中债国债收益率曲线"的"10年"数据 |
| US10Y | 美国 | FRED API | `https://api.stlouisfed.org/fred/series/observations`，序列ID: `DGS10` |
| FR10Y | 法国 | FRED API | 同上，序列ID: `IRLTLT01FRM156N` |
| DE10Y | 德国 | FRED API | 同上，序列ID: `IRLTLT01DEM156N` |
| JP10Y | 日本 | FRED API | 同上，序列ID: `IRLTLT01JPM156N` |

### 2.2 市盈率(PE)数据

| 指数代码 | 指数名称 | 数据来源 | 接口/地址 | 备注 |
|---------|---------|---------|----------|------|
| 000300 | 沪深300 | 中证指数官网 | `ak.stock_zh_index_hist_csindex()` | 字段: `滚动市盈率` |
| 000688 | 科创50 | 中证指数官网 | `ak.stock_zh_index_hist_csindex()` | 字段: `滚动市盈率` |
| 000922 | 中证红利 | 中证指数官网 | `ak.stock_zh_index_hist_csindex()` | 字段: `滚动市盈率` |
| 399989 | 中证医疗 | 中证指数官网 | `ak.stock_zh_index_hist_csindex()` | 字段: `滚动市盈率` |
| 931071 | 人工智能 | 中证指数官网 | `ak.stock_zh_index_hist_csindex()` | 字段: `滚动市盈率` |
| SPY | S&P 500 | multpl.com | `https://www.multpl.com/s-p-500-pe-ratio/table/by-month` | 月频数据 |
| QQQ | Nasdaq 100 | GuruFocus | 本地文件 `./data/qqq_pe_gurufocus.xlsx` | 需手动下载 |
| EWQ | MSCI France | worldperatio.com | `https://worldperatio.com/major-stock-index-pe-ratios` | 仅今日值 |
| EWG | MSCI Germany | worldperatio.com | 同上 | 仅今日值 |
| EWJ | MSCI Japan | worldperatio.com | 同上 | 仅今日值 |
| EEM | MSCI Emerging | worldperatio.com | 同上 | 仅今日值 |

---

## 三、估算方法说明

### 3.1 需要估算的指标

| 指数代码 | 指数名称 | 是否估算 | 估算方法 | 估算原因 |
|---------|---------|---------|---------|---------|
| EWQ | MSCI France | **是** | 基于SPY历史PE × (EWQ今日PE / SPY今日PE) | worldperatio.com仅提供今日值，无历史数据 |
| EWG | MSCI Germany | **是** | 基于SPY历史PE × (EWG今日PE / SPY今日PE) | 同上 |
| EWJ | MSCI Japan | **是** | 基于SPY历史PE × (EWJ今日PE / SPY今日PE) | 同上 |
| EEM | MSCI Emerging | **是** | 基于SPY历史PE × (EEM今日PE / SPY今日PE) | 同上 |
| QQQ | Nasdaq 100 | 部分 | 历史数据从GuruFocus CSV获取，今日值需手动填入 | 无免费实时API |

### 3.2 估算公式

```
估算历史PE(symbol) = SPY历史PE × [symbol今日PE / SPY今日PE]
```

**示例**：假设今日EWQ的PE为18，SPY的PE为22，则比例 = 18/22 = 0.818

若SPY在某历史日期的PE为25，则估算EWQ当日PE = 25 × 0.818 ≈ 20.45

### 3.3 估算局限性

> ⚠️ **已知限制**：
> - 假设各指数与SPY的PE比值在历史上保持恒定
> - 实际情况中，估值水平会随市场风格、经济周期变化
> - 估算值仅供参考，不代表真实历史估值

---

## 四、数据更新机制

| 脚本 | 用途 | 更新频率 | 数据范围 |
|-----|------|---------|---------|
| `fetch_bond_yield_v5.py` | 全量初始化 | 一次性 | 2005年至今历史数据 |
| `fetch_bond_yield_incremental_v5.py` | 增量更新 | 每日 | 最近30天数据 |

---

## 五、配置对照表

### 5.1 国债配置

```python
BOND_CONFIG = {
    'CN10Y': 'bond_china',        # 中国 - akshare
    'US10Y': 'DGS10',            # 美国 - FRED
    'FR10Y': 'IRLTLT01FRM156N',  # 法国 - FRED
    'DE10Y': 'IRLTLT01DEM156N',  # 德国 - FRED
    'JP10Y': 'IRLTLT01JPM156N',  # 日本 - FRED
}
```

### 5.2 指数与PE来源配置

| 指数代码 | 名称 | 货币 | 对应国债 | PE数据源类型 |
|---------|-----|-----|---------|-------------|
| 000300 | 沪深300 | CNY | CN10Y | `csindex` (中证指数) |
| 000688 | 科创50 | CNY | CN10Y | `csindex` |
| 000922 | 中证红利 | CNY | CN10Y | `csindex` |
| 399989 | 中证医疗 | CNY | CN10Y | `csindex` |
| 931071 | 人工智能 | CNY | CN10Y | `csindex` |
| SPY | S&P 500 | USD | US10Y | `multpl` / `worldpe` |
| QQQ | Nasdaq 100 | USD | US10Y | `manual` / `gurufocus_csv` |
| EWQ | MSCI France | EUR | FR10Y | `worldpe` (估算) |
| EWG | MSCI Germany | EUR | DE10Y | `worldpe` (估算) |
| EWJ | MSCI Japan | JPY | JP10Y | `worldpe` (估算) |
| EEM | MSCI Emerging | USD | CN10Y | `worldpe` (估算) |

---

## 六、第三方API密钥

| 服务商 | 用途 | API Key | 备注 |
|-------|-----|---------|-----|
| FRED (美联储) | 获取海外国债数据 | `a8ce66c09bbcedfb9e33de739a0dcbfb` | 圣路易斯联储银行 |

---

## 七、文件输出

| 文件路径 | 内容 | 字段 |
|---------|-----|------|
| `./data/erp_{code}.csv` | 各指数ERP数据 | Date, PE, Bond_Yield_10Y, ERP, IndexCode, IndexName, Currency, BondCode |

---

## 八、数据源质量评估

| 数据源 | 数据完整性 | 实时性 | 可靠性 | 推荐度 |
|-------|----------|--------|--------|--------|
| 中证指数 (csindex) | ★★★★★ | ★★★★☆ | ★★★★★ | 优先使用 |
| FRED API | ★★★★★ | ★★★☆☆ | ★★★★★ | 优先使用 |
| multpl.com | ★★★★☆ | ★★★☆☆ | ★★★★☆ | 可用 |
| worldperatio.com | ★★☆☆☆ | ★★★★☆ | ★★★☆☆ | 仅获取今日值 |
| GuruFocus | ★★★★★ | ★★☆☆☆ | ★★★★☆ | 需手动更新 |

---

## 九、相关脚本说明

### 9.1 fetch_bond_yield_v5.py（全量初始化脚本）

- **用途**：首次运行时获取所有历史数据
- **数据范围**：2005年至今
- **主要函数**：
  - `fetch_cn_bond_history()` - 获取中国国债历史
  - `fetch_fred_bond_history()` - 获取海外国债历史
  - `fetch_qqq_pe_from_csv()` - 读取QQQ PE历史
  - `fetch_spy_pe_history()` - 获取SPY PE历史
  - `fetch_pe_history_by_ratio()` - 估算海外指数PE历史

### 9.2 fetch_bond_yield_incremental_v5.py（增量更新脚本）

- **用途**：每日定时更新最新数据
- **数据范围**：最近30天
- **主要函数**：
  - `fetch_cn_bond_incremental()` - 增量获取中国国债
  - `fetch_fred_bond_incremental()` - 增量获取海外国债
  - `fetch_worldpe_today()` - 获取今日PE

---
