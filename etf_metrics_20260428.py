"""
etf_metrics.py
──────────────────────────────────────────────────────────────────────────────
ETF 执行质量补充模块
数据源: etf_metrics_daily_report.csv（每日更新，放 ./data/ 目录）

补充维度（不替代 ERP 估值判断，仅辅助执行决策）：
  1. 折溢价率   — 当前买入/卖出的执行成本
  2. 换手背离   — 价格走势是否有成交量支撑
  3. 波动/回撤  — 当前风险水位（历史分位）
  4. 超额收益   — ETF 跟踪质量 + 近期相对基准动量

使用方式：
  在 erp_position.py 中：
    from etf_metrics import load_etf_metrics, build_etf_metrics_block
    _etf_df = load_etf_metrics()                          # 启动时加载一次
    block = build_etf_metrics_block("000688", _etf_df)    # 每个标的调用
──────────────────────────────────────────────────────────────────────────────
"""

import os
import pandas as pd

ETF_METRICS_PATH = os.getenv(
    "ETF_METRICS_URL",
    "https://github.com/ChiaraVan1/ETF_data_project/releases/latest/download/etf_metrics_daily_report.csv"
)

# ── ERP code → A股 ETF ts_code 映射 ──────────────────────────────────────────
# 一个 ERP 标的可能对应多只 ETF，取流动性最好的主力品种
# 没有对应 A 股 ETF 的境外标的（SPY/QQQ/EWQ/EWG/EWJ/EEM）留空，模块自动跳过
ERP_TO_ETF = {
    "000300": "510300.SH",   # 沪深300   → 华泰柏瑞沪深300ETF（旗舰）
    "000688": "588000.SH",   # 科创50    → 华夏科创50ETF
    "000922": "515180.SH",   # 中证红利  → 中证红利ETF（华泰）
    "399989": "512170.SH",   # 中证医疗  → 医疗ETF华宝
    "931071": "159819.SZ",   # 人工智能  → 人工智能ETF（华夏）
    "HSTECH": "513180.SH",   # 恒生科技  → 恒指科技ETF华夏
    # 境外宽基无对应 A 股 ETF，跳过
    "SPY":    None,
    "QQQ":    None,
    "EWQ":    None,
    "EWG":    None,
    "EWJ":    None,
    "EEM":    None,
}

_metrics_cache = {}


def load_etf_metrics(path: str = ETF_METRICS_PATH) -> pd.DataFrame | None:
    """
    加载 ETF 指标 CSV，返回以 ts_code 为索引的 DataFrame。
    失败时返回 None（不影响主流程）。
    缓存结果，避免重复读盘。
    """
    if _metrics_cache:
        return _metrics_cache.get("df")

    if not os.path.exists(path):
        print(f"⚠️ [etf_metrics] 未找到指标文件: {path}，跳过 ETF 执行质量分析。")
        _metrics_cache["df"] = None
        return None

    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
        df = df.set_index("ts_code")
        _metrics_cache["df"] = df
        return df
    except Exception as e:
        print(f"⚠️ [etf_metrics] 读取失败: {e}")
        _metrics_cache["df"] = None
        return None


# ── 单项指标解读辅助 ──────────────────────────────────────────────────────────

def _discount_comment(rate: float, quantile_1y: float) -> tuple[str, str]:
    """返回 (状态emoji+文字, 操作提示)"""
    pct = rate * 100
    q = quantile_1y

    if rate < -0.003:
        status = f"🟢 折价 {pct:.3f}%（1年{q*100:.0f}%分位低价区）"
        tip = "折价买入，执行成本占优"
    elif rate < -0.0005:
        status = f"🟡 轻微折价 {pct:.3f}%"
        tip = "小幅折价，正常范围"
    elif rate < 0.0005:
        status = f"⚪ 平价 {pct:.3f}%"
        tip = "平价，无额外成本"
    elif rate < 0.003:
        status = f"🟠 轻微溢价 {pct:.3f}%（1年{q*100:.0f}%分位）"
        tip = "小幅溢价，可接受"
    else:
        status = f"🔴 溢价 {pct:.3f}%（1年{q*100:.0f}%分位高溢区）"
        tip = "⚠️ 溢价偏高，建议等折价或限价委托"

    return status, tip


def _turnover_comment(rate: float, quantile: float, divergence: bool) -> str:
    """换手率 + 背离综合解读"""
    q_pct = quantile * 100
    lines = []

    if quantile >= 0.8:
        lines.append(f"🔥 换手率处于1年 {q_pct:.0f}% 高位，市场高度活跃")
    elif quantile >= 0.5:
        lines.append(f"🟡 换手率中等（1年 {q_pct:.0f}% 分位）")
    else:
        lines.append(f"🧊 换手率偏低（1年 {q_pct:.0f}% 分位），成交清淡")

    if divergence:
        lines.append("⚠️ **价格/换手背离**：价格走势缺乏成交量支撑，需警惕假突破或趋势反转")
    else:
        lines.append("✅ 价格/换手无背离，走势有量配合")

    return "；".join(lines)


def _volatility_comment(vol_q1y: float, dd_q1y: float) -> str:
    """波动率 + 回撤分位综合风险解读"""
    risk_items = []

    if vol_q1y >= 0.85:
        risk_items.append(f"波动率处于1年 {vol_q1y*100:.0f}% 高位 🔴")
    elif vol_q1y >= 0.6:
        risk_items.append(f"波动率中高（1年 {vol_q1y*100:.0f}% 分位）🟠")
    else:
        risk_items.append(f"波动率偏低（1年 {vol_q1y*100:.0f}% 分位）🟢")

    if dd_q1y >= 0.85:
        risk_items.append(f"近期回撤处于1年 {dd_q1y*100:.0f}% 高位，风险释放充分")
    elif dd_q1y >= 0.5:
        risk_items.append(f"回撤处于中等水平（1年 {dd_q1y*100:.0f}% 分位）")
    else:
        risk_items.append(f"回撤相对较小（1年 {dd_q1y*100:.0f}% 分位），下行空间尚未充分释放")

    return "；".join(risk_items)


def _excess_return_comment(mean: float, slope: float,
                            ma5: float, ma20: float) -> str:
    """超额收益质量 + 短期动量"""
    lines = []

    # 跟踪质量
    if mean > 0.01:
        lines.append(f"✅ 长期超额收益 +{mean:.3f}%/日，跟踪优秀")
    elif mean > 0:
        lines.append(f"✅ 长期超额收益微正（+{mean:.4f}%/日），跟踪正常")
    elif mean > -0.01:
        lines.append(f"🟡 长期超额收益略负（{mean:.4f}%/日），轻微跟踪偏差")
    else:
        lines.append(f"🔴 长期超额收益 {mean:.3f}%/日，跟踪质量需关注")

    # 短期动量（5日MA vs 20日MA）
    if pd.notna(ma5) and pd.notna(ma20):
        if ma5 > ma20 + 0.001:
            lines.append("近期超额动量向上（5MA > 20MA）📈")
        elif ma5 < ma20 - 0.001:
            lines.append("近期超额动量向下（5MA < 20MA）📉")
        else:
            lines.append("近期超额动量持平")

    # MA趋势斜率
    if slope > 0.0002:
        lines.append("超额趋势斜率向上，相对基准改善中")
    elif slope < -0.0002:
        lines.append("超额趋势斜率向下，相对基准略有走弱")

    return "；".join(lines)


# ── 主函数：生成 markdown 块 ───────────────────────────────────────────────────

def build_etf_metrics_block(erp_code: str, etf_df: pd.DataFrame | None) -> str:
    """
    给定 ERP 代码（如 "000688"）和已加载的 DataFrame，
    返回 markdown 格式的 ETF 执行质量补充块。
    若无对应数据则返回空字符串，不影响主报告。
    """
    if etf_df is None:
        return ""

    ts_code = ERP_TO_ETF.get(erp_code)
    if ts_code is None:
        return ""   # 境外标的或未配置，静默跳过

    if ts_code not in etf_df.index:
        return f"\n> ⚠️ ETF {ts_code} 不在今日指标文件中，跳过执行质量分析。\n"

    row = etf_df.loc[ts_code]

    # ── 安全读取字段 ──────────────────────────────────────────────────────────
    def safe(col, default=float("nan")):
        v = row.get(col, default)
        return default if pd.isna(v) else v

    etf_name          = safe("name", ts_code)
    discount_rate     = safe("latest_discount_rate", 0.0)
    discount_q1y      = safe("discount_quantile_1y", 0.5)
    discount_5d_chg   = safe("change_5d_discount", 0.0)
    discount_10d_chg  = safe("change_10d_discount", 0.0)
    turnover_rate     = safe("turnover_rate", 0.0)
    turnover_q        = safe("turnover_quantile", 0.5)
    divergence        = bool(safe("is_price_turnover_divergence", False))
    vol_q1y           = safe("volatility_quantile_1y", 0.5)
    dd_q1y            = safe("max_drawdown_quantile_1y", 0.5)
    ann_vol           = safe("annualized_volatility", 0.0)
    max_dd            = safe("max_drawdown", 0.0)
    excess_mean       = safe("excess_return_mean", 0.0)
    tracking_err      = safe("tracking_error", 0.0)
    ma_slope          = safe("ma_trend_slope", 0.0)
    ma5               = safe("excess_return_5d_ma", float("nan"))
    ma20              = safe("excess_return_20d_ma", float("nan"))

    # ── 各维度解读 ────────────────────────────────────────────────────────────
    discount_status, discount_tip = _discount_comment(discount_rate, discount_q1y)
    turnover_text  = _turnover_comment(turnover_rate, turnover_q, divergence)
    risk_text      = _volatility_comment(vol_q1y, dd_q1y)
    excess_text    = _excess_return_comment(excess_mean, ma_slope, ma5, ma20)

    # ── 折溢价变化趋势 ────────────────────────────────────────────────────────
    d5_str  = f"+{discount_5d_chg*100:.3f}%" if discount_5d_chg >= 0 else f"{discount_5d_chg*100:.3f}%"
    d10_str = f"+{discount_10d_chg*100:.3f}%" if discount_10d_chg >= 0 else f"{discount_10d_chg*100:.3f}%"

    # ── 综合执行建议 ──────────────────────────────────────────────────────────
    warnings = []
    if discount_rate > 0.003:
        warnings.append("当前溢价偏高，建议限价或等待折价再入")
    if divergence:
        warnings.append("价格/换手背离，入场需确认量能跟进")
    if vol_q1y >= 0.85:
        warnings.append("波动率处于高位，注意仓位控制与止损设置")
    if dd_q1y < 0.3 and vol_q1y < 0.4:
        warnings.append("波动率和回撤均偏低，下行风险尚未释放，谨慎追高")

    exec_summary = (
        "⚠️ **执行注意**：" + "；".join(warnings)
        if warnings
        else "✅ 执行条件正常，无明显异常信号"
    )

    block = f"""
---
### ETF 执行质量补充（{etf_name} · {ts_code}）

> 数据来源：ETF 量化指标日报 | 补充维度：折溢价 / 换手 / 风险水位 / 跟踪质量

| 维度 | 数值 | 解读 |
|:-----|-----:|:-----|
| 折溢价率 | **{discount_rate*100:.3f}%** | {discount_status} |
| 折溢价趋势 | 5日{d5_str} / 10日{d10_str} | {"折价收窄（溢价扩大），买入窗口或趋于关闭" if discount_5d_chg > 0.001 else "折价扩大或保持，买入条件改善" if discount_5d_chg < -0.001 else "折溢价近期基本稳定"} |
| 换手率 | {turnover_rate:.2f}% | {turnover_text} |
| 年化波动率 | {ann_vol:.1f}% | {risk_text} |
| 最大回撤 | {max_dd*100:.1f}% | — |
| 超额收益均值 | {excess_mean*100:.4f}%/日 | {excess_text} |
| 跟踪误差 | {tracking_err:.2f}% | {"偏高，建议关注同类替代品" if tracking_err > 8 else "正常范围"} |

**{exec_summary}**
"""
    return block
