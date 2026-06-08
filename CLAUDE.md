# equity-risk-premium-monitor

每日自动计算各主要指数的股权风险溢价（ERP），输出胜率/赔率/仓位建议，推送微信，并将 HTML 报告部署到 GitHub Pages。

---

## 项目职责

1. **数据采集**（`fetch_bond_yield_incremental.py`）：每日增量拉取各市场10年期国债收益率和指数 PE，写入 `data/erp_{CODE}.csv`；同步更新 HSTECH 的 PS/PSY 数据（`data/ps_HSTECH.csv`）。
2. **ETF 执行质量**（`etf_metrics.py`）：从 `ETF_data_project` GitHub Release 下载 `simple_etf_metrics.csv`，补充折溢价/换手/波动/超额收益信号。
3. **报告生成 & 推送**（`erp_position.py`）：读取所有数据，计算胜率/赔率/仓位，生成 Markdown + HTML 报告，推送微信（ServerChan），部署 HTML 到 gh-pages。

---

## 文件结构

```
equity-risk-premium-monitor/
├── .github/workflows/
│   ├── daily_trade.yml        # 每日 18:30 BT 自动运行（含 dry_run 选项）
│   └── init_history.yml       # 手动触发，全量初始化历史数据
│
├── data/
│   ├── erp_{CODE}.csv         # 每个指数的历史 ERP（Date/PE/Bond_Yield_10Y/ERP）
│   ├── ps_HSTECH.csv          # HSTECH 月频 PS/PSY/rf（自建，yfinance+akshare）
│   ├── ie_data.xls            # Shiller CAPE 历史数据（手动维护，月更）
│   ├── qqq_pe_gurufocus.xlsx  # QQQ PE 历史（手动下载，仅全量初始化需要）
│   └── hstech_pe.csv          # HSTECH PE 历史（手动下载，仅全量初始化需要）
│
├── docs/
│   └── report.html            # 每日生成，gh-pages 自动部署
│
├── erp_position.py            # 主分析脚本：读数据→计算→生成报告→推送微信
├── etf_metrics.py             # ETF 执行质量模块（从 ETF_data_project Release 取数）
├── fetch_bond_yield_incremental.py  # 每日增量数据更新（CI 调用）
├── fetch_bond_yield.py        # 全量历史初始化（手动触发）
├── fetch_ps.py                # HSTECH PS 全量计算（独立脚本，手动运行）
├── simple_etf_metrics.py      # ETF 指标生产脚本（与 ETF_data_project 同步，本地测试用）
└── requirements.txt           # pandas / akshare / requests / yfinance
```

---

## 依赖的外部 Repo

### `../ETF_data_project`（`github.com/ChiaraVan1/ETF_data_project`）

- **作用**：每日用 Tushare 计算 A 股 ETF 的折溢价/换手/波动/超额收益，生成 `simple_etf_metrics.csv`，通过 GitHub Release（tag=`latest`）发布。
- **下载地址**：`etf_metrics.py` 在运行时自动拉取：
  ```
  https://github.com/ChiaraVan1/ETF_data_project/releases/download/latest/simple_etf_metrics.csv
  ```
- **CI 时序**：ETF_data_project `test.yaml` 每天 UTC 10:00（北京时间 18:00）运行；本项目 `daily_trade.yml` 在 UTC 10:30（北京时间 18:30）运行，保证拿到当日最新指标。
- **失败降级**：若下载失败，`erp_position.py` 会跳过 ETF 执行质量模块，主报告正常生成。

---

## 手动维护事项

| 事项 | 频率 | 操作方式 |
|-----|------|---------|
| **QQQ 今日 PE** | 每日 | 从 [GuruFocus](https://www.gurufocus.com/economic_indicators/6778/nasdaq-100-pe-ratio) 查询，在 GitHub Actions → Variables 中更新 `QQQ_PE_TODAY` |
| **Shiller CAPE 数据**（`data/ie_data.xls`） | 每月初 | 从 [Yale Shiller 网站](http://www.econ.yale.edu/~shiller/data.htm) 下载最新 xls，替换 `data/ie_data.xls`，commit 并 push |
| **QQQ PE 历史**（`data/qqq_pe_gurufocus.xlsx`） | 仅全量初始化时一次 | 从 GuruFocus 下载 xlsx，放入 `data/`，仅在跑 `init_history.yml` 时需要 |
| **HSTECH PE CSV**（`data/hstech_pe.csv`） | 仅全量初始化时一次 | 需包含"日期"和"PE-TTM等权"两列，放入 `data/` |
| **添加新指数** | 按需 | 同步更新 `fetch_bond_yield_incremental.py`、`fetch_bond_yield.py`、`erp_position.py` 三处 `INDEX_CONFIG`/`indices` 列表，以及 `etf_metrics.py` 的 `ERP_TO_ETF` 映射 |

---

## 环境变量清单

### GitHub Secrets（加密存储）

| 变量名 | 用途 | 必填 |
|-------|------|------|
| `SCT_KEY` | ServerChan 微信推送 Key（`erp_position.py`） | 是 |
| `GITHUB_TOKEN` | GitHub Actions 内置，用于 gh-pages 部署 | 自动 |

### GitHub Variables（明文可见）

| 变量名 | 用途 | 必填 |
|-------|------|------|
| `QQQ_PE_TODAY` | QQQ 当日 PE 手动值，每日更新（`fetch_bond_yield_incremental.py`） | 建议每日维护 |

### 运行时可选环境变量

| 变量名 | 用途 | 默认值 |
|-------|------|-------|
| `SHILLER_PATH` | Shiller CAPE 数据文件路径 | `./data/ie_data.xls` |
| `DRY_RUN` | `true` 时只生成报告文件，不推送微信 | `false` |
| `HS_TECH_PE_TODAY` | 恒生科技今日 PE（目前 CI 未使用，HSTECH 改用 PSY） | 无 |

### 硬编码在脚本中（注意：不建议长期保持）

| 变量名 | 所在文件 | 用途 |
|-------|---------|------|
| `FRED_API_KEY` | `fetch_bond_yield.py` 第 11 行，`fetch_bond_yield_incremental.py` 第 13 行 | FRED API 拉取美/法/德/日国债数据 |

---

## 核心算法概览

- **ERP** = `1/PE − 10Y国债收益率`；值越高代表股票相对债券越便宜。
- **胜率** = 当前 ERP 在历史序列中的百分位分位（越高越便宜）。
- **赔率** = `(当前ERP − P10) / (P90 − 当前ERP)`（ERP 绝对值法）。
- **HSTECH 用 PSY 替代 ERP**：`PSY = 1/PS − CN10Y`，PS 为自建月频市销率。
- **欧美日锚定区间**：EWQ/EWG/EWJ/SPY/QQQ 的历史分位仅用 2022-01-01 之后数据（规避负利率失真）。
- **三仓结构**：泡沫底仓（30%）+ 价值主力（0–40%）+ 投机奇兵（5–30%），各自独立按 ERP 分位触发。
