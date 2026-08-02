"""
analysis/risk.py
风险分析模块：止损信号、止盈信号、回撤反弹统计
"""
import pandas as pd
import numpy as np

from config_loader import HOLDING_CATEGORY

# ══════════════════════════════════════════════════════════════════════
#  止损信号计算与报告
# ══════════════════════════════════════════════════════════════════════

_DD_L1 = 0.10   # 第一级：回撤≥10% + 跌破MA20 → 减仓1/3
_DD_L2 = 0.15   # 第二级：回撤≥15% + 跌破MA60 → 减仓至底仓
_DD_L3 = 0.20   # 第三级：回撤≥20%            → 全清止损


def compute_ma_stats(price_s: pd.Series):
    """计算MA20/MA60统计。"""
    ma20 = price_s.rolling(20).mean()
    ma60 = price_s.rolling(60).mean()
    return {"MA20": ma20, "MA60": ma60}


def compute_exit_signal_summary(erp_code: str, current_erp_percentile: float, 
                                price_series=None, holding: bool = True) -> dict:
    """
    计算止损信号汇总。
    返回: {"level": 0/1/2/3, "verdict_icon": str, "message": str, ...}
    """
    if price_series is None or len(price_series) < 60:
        return {"level": 0, "verdict_icon": "─", "message": ""}

    # 计算回撤
    peak = price_series.rolling(window=len(price_series), min_periods=1).max().iloc[-1]
    current = price_series.iloc[-1]
    drawdown = (current - peak) / peak if peak != 0 else 0

    # MA统计
    ma20 = price_series.rolling(20).mean().iloc[-1]
    ma60 = price_series.rolling(60).mean().iloc[-1]

    below_ma20 = current < ma20
    below_ma60 = current < ma60

    level = 0
    message = ""
    verdict_icon = "─"

    # 判断止损级别
    if drawdown <= _DD_L1 and not (below_ma20 or below_ma60):
        # 无止损触发
        level = 0
    elif drawdown >= _DD_L3:
        # L3: 全清
        level = 3
        if holding:
            verdict_icon = "🚨"
            message = f"回撤 {abs(drawdown)*100:.1f}% ≥ 20%，强制全清止损"
        else:
            verdict_icon = "🔎"
            message = f"价格结构风险（回撤{abs(drawdown)*100:.1f}%），观察提示"
    elif drawdown >= _DD_L2 and below_ma60:
        # L2: 减至底仓
        level = 2
        if holding:
            verdict_icon = "🔴"
            message = f"回撤 {abs(drawdown)*100:.1f}% ≥ 15% 且跌破MA60，减至底仓"
        else:
            verdict_icon = "⚠️"
            message = f"价格结构恶化（回撤{abs(drawdown)*100:.1f}%），L1级观察"
    elif drawdown >= _DD_L1 and below_ma20:
        # L1: 减仓1/3
        level = 1
        if holding:
            verdict_icon = "⚠️"
            message = f"回撤 {abs(drawdown)*100:.1f}% ≥ 10% 且跌破MA20，减持1/3"
        else:
            verdict_icon = "🔎"
            message = f"价格短期走弱（回撤{abs(drawdown)*100:.1f}%），观察提示"

    return {
        "level": level,
        "verdict_icon": verdict_icon,
        "message": message,
        "drawdown": drawdown,
        "current": current,
        "peak": peak,
        "ma20": ma20,
        "ma60": ma60,
    }


def build_exit_signal_block(erp_code: str, current_erp_percentile: float, 
                            price_series=None, holding: bool = True) -> str:
    """生成止损信号报告块。"""
    summary = compute_exit_signal_summary(erp_code, current_erp_percentile, 
                                         price_series, holding)
    
    if summary["level"] == 0:
        return ""

    block = f"""
---
### 🚨 减仓 / 清仓信号

{summary['verdict_icon']} {summary['message']}

| 指标 | 数值 |
|:-----|-----:|
| 当前回撤 | {summary['drawdown']*100:.1f}% |
| 峰值 | {summary['peak']:.2f} |
| 当前价格 | {summary['current']:.2f} |
| MA20 | {summary['ma20']:.2f} |
| MA60 | {summary['ma60']:.2f} |
"""
    return block


# ══════════════════════════════════════════════════════════════════════
#  止盈信号
# ══════════════════════════════════════════════════════════════════════

def compute_profit_signal_summary(erp_code: str, current_erp_percentile: float) -> dict:
    """
    计算止盈信号。
    高估区（ERP < P50）时，乖离率过热触发止盈。
    """
    if current_erp_percentile >= 0.50:
        return {"level": 0, "verdict_icon": "─", "message": ""}

    # 简化版：高估区直接提示
    return {
        "level": 1,
        "verdict_icon": "📈",
        "message": f"处于高估区（分位{current_erp_percentile*100:.0f}%），考虑止盈",
    }


def build_profit_signal_block(erp_code: str, current_erp_percentile: float) -> str:
    """生成止盈信号报告块。"""
    summary = compute_profit_signal_summary(erp_code, current_erp_percentile)
    
    if summary["level"] == 0:
        return ""

    block = f"""
---
### 📈 止盈信号

{summary['verdict_icon']} {summary['message']}
"""
    return block


# ══════════════════════════════════════════════════════════════════════
#  回撤反弹统计
# ══════════════════════════════════════════════════════════════════════

def compute_range_drawdown_rebound(erp_code: str, price_series=None, lookback: int = 120) -> dict:
    """
    120天窗口内：峰值→峰值之后的谷值回撤 + 谷值→现在反弹。
    """
    if price_series is None or len(price_series) < 5:
        return None

    window = price_series.iloc[-min(lookback, len(price_series)):]
    peak_pos = window.values.argmax()
    high = window.iloc[peak_pos]
    post_peak = window.iloc[peak_pos:]
    low  = post_peak.min()
    cur  = price_series.iloc[-1]

    dd_high_to_low   = (low - high) / high if high != 0 else float("nan")
    rebound_from_low = (cur - low) / low if low != 0 else float("nan")

    return {
        "high": high, "low": low, "cur": cur,
        "dd": dd_high_to_low, "rebound": rebound_from_low,
    }
