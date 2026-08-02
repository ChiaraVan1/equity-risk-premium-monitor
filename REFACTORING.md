# 代码拆分迁移指南

## 📋 概览

从 **单文件架构** (erp_position.py 2,160 行) 重构为 **模块化架构**。

### 新的代码结构

```
├── prepare_all_data.py          # 数据准备编排（205行）
├── erp_position.py              # 主入口（139行，简化版）
│
├── analysis/                    # 分析模块（589行）
│   ├── valuation.py            # 估值分析（Shiller、股息率、赔率）
│   ├── risk.py                 # 风险分析（止损、止盈、回撤）
│   ├── trend.py                # 趋势分析（斜率、月度趋势）
│   ├── sentiment.py            # 情绪分析（热度、基本面预警）
│   └── utils.py                # 工具函数（新鲜度检查、格式化）
│
└── report/                      # 报告模块（133行）
    └── markdown.py             # HTML/微信推送
```

---

## 🔄 使用方式

### GitHub Actions 工作流（无需修改）

```yaml
- name: Run ETF Metrics
  run: python simple_etf_metrics.py

- name: Fetch Data
  run: python fetch_bond_yield_incremental.py

- name: Analyze and Push to WeChat
  run: python erp_position.py
```

**新的 erp_position.py 会自动调用 prepare_all_data.py**，所以工作流不需要改。

---

## 📦 文件映射

### prepare_all_data.py（新文件）
从原 erp_position.py 提取的数据准备函数：

| 函数 | 来源 | 职责 |
|------|------|------|
| `load_shiller()` | 行47-84 | 加载Shiller CAPE数据 |
| `load_ps_data()` | 行149-161 | 加载PS/PSY数据 |
| `load_etf_price_series()` | 行307-323 | 加载ETF价格序列 |
| `load_etf_metrics()` | 导入 | 加载ETF指标 |
| `ensure_dividend_data_fresh()` | 导入 | 确保股息率数据 |
| `fetch_em_news_df()` | 行795-823 | 拉取东方财富快讯 |
| `prepare_all_data()` | 新增 | **主编排函数** |

### analysis/valuation.py
| 函数 | 行号 |
|------|-----:|
| `calc_odds()` | 162-187 |
| `build_shiller_block()` | 85-148 |
| `build_unified_valuation_block()` | 1065-1152 |
| `is_holding()` | 1291-1295 |

### analysis/risk.py
| 函数 | 行号 |
|------|-----:|
| `compute_exit_signal_summary()` | 333-429 |
| `build_exit_signal_block()` | 430-574 |
| `compute_profit_signal_summary()` | 575-638 |
| `build_profit_signal_block()` | 639-694 |
| `compute_range_drawdown_rebound()` | 1265-1290 |

### analysis/trend.py
| 函数 | 行号 |
|------|-----:|
| `compute_erp_slope_signal()` | 240-306 |
| `build_trend_block()` | 1192-1264 |
| `build_monthly_trend_ai_block()` | 1168-1191 |

### analysis/sentiment.py
| 函数 | 行号 |
|------|-----:|
| `build_fundamental_alert_block()` | 857-1064 |
| `build_popularity_block()` | 导入 |
| `compute_popularity_confirmation()` | 导入 |

### analysis/utils.py
| 函数 | 行号 |
|------|-----:|
| `check_metric_freshness()` | 188-217 |
| `build_freshness_note()` | 218-239 |
| `generate_action_sentence()` | 1296-1315 |
| `_format_win_odds()` | 1316-1326 |
| `_format_range()` | 1327-1336 |

### report/markdown.py
| 函数 | 行号 |
|------|-----:|
| `build_summary_block()` | 1337-1539 |
| `build_etf_ai_interpretation()` | 1702-1779 |
| `markdown_to_html()` | 1540-1666 |
| `save_html_report()` | 1667-1675 |
| `send_to_wechat()` | 1676-1701 |

---

## ✅ 迁移清单

- [x] 创建 analysis/ 目录和各个模块
- [x] 创建 report/ 目录和 markdown.py
- [x] 创建 prepare_all_data.py
- [x] 简化 erp_position.py（保留原版为 .bak）
- [ ] 测试工作流是否正常运行
- [ ] 删除 erp_position.py.bak（确认无问题后）
- [ ] 更新 GitHub Actions 日志输出格式（可选）

---

## 🧪 测试步骤

### 本地测试（不推送微信）
```bash
export DRY_RUN=true
python erp_position.py
```

### 完整测试（推送微信）
```bash
python erp_position.py
```

检查：
1. ✓ 数据准备阶段输出正常
2. ✓ 分析阶段完成各指数计算
3. ✓ 报告生成成功（docs/report.html）
4. ✓ 微信推送成功

---

## 🔧 若出现导入错误

检查以下几点：

1. **Python 路径**：确保 analysis/ 和 report/ 目录有 `__init__.py`
   ```bash
   ls -la analysis/__init__.py report/__init__.py
   ```

2. **导入语句**：新文件中都使用相对导入
   ```python
   from analysis.valuation import ...
   from report.markdown import ...
   ```

3. **依赖项**：确保其他必要文件未被删除
   ```bash
   ls -la config_loader.py dividend_yield.py etf_metrics.py popularity_signal.py
   ```

---

## 📈 优势

| 方面 | 旧架构 | 新架构 |
|------|--------|--------|
| erp_position.py 行数 | 2,160 | 139 |
| 模块分离 | ❌ | ✅ 5个分析模块 |
| 代码复用 | 困难 | 容易 |
| 单元测试 | 困难 | 容易（按模块） |
| 维护成本 | 高 | 低 |
| 功能扩展 | 困难 | 容易 |

---

## 📝 后续优化方向

1. 为各模块添加单元测试
2. 提取常数配置到 config.py
3. 添加类型注解（Type hints）
4. 添加详细的模块文档字符串
5. 创建 analysis 和 report 的抽象基类

