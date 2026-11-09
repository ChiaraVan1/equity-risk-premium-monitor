"""
ma_position.py
均线位置块（报告"执行层"用）

定位说明：
    本模块只回答一个问题——「现在这个价，能不能下手」。
    同一根 MA20 在「减仓 / 清仓信号」「止盈信号」里被当作卖出条件使用（且被回撤/乖离
    阈值包裹），那是它的另一套用法。两处口径不同，不要混着看，所以这里单独成块、
    放在 ETF 执行质量旁边（第 4 层：怎么下单），而不是塞进卖出的明细表。

输出风格与其它分析块一致：三级标题 + markdown 表格 + 一行用途说明。
"""

import numpy as np
import pandas as pd

from analysis.risk import compute_ma_stats


def _fmt(v, nd=3):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "─"
    return f"{v:.{nd}f}"


def build_ma_position_block(code: str, price_series, holding: bool = True) -> str:
    """生成均线位置块。price_series 为前复权日线 pd.Series（索引为日期）。
    数据缺失时返回空串，不影响主报告。"""
    try:
        if price_series is None or len(price_series) < 5:
            return ""
        last = float(price_series.iloc[-1])
        ma20, ma60, ma120 = compute_ma_stats(price_series)
        if ma20 is None or (isinstance(ma20, float) and np.isnan(ma20)):
            return ""

        dev20 = (last - ma20) / ma20 * 100
        if dev20 > 0:
            state20 = f"🟢 站上均线（乖离 {dev20:+.1f}%）"
            brief20 = f"🟢 站上 {dev20:+.1f}%"
            dist20 = f"回踩到 MA20 需再跌 {dev20:.1f}%"
        else:
            state20 = f"🔴 跌破均线（乖离 {dev20:+.1f}%）"
            brief20 = f"🔴 跌破 {dev20:+.1f}%"
            dist20 = f"需收回 MA20 上方 {abs(dev20):.1f}% 才算站上"

        if ma60 is not None and not (isinstance(ma60, float) and np.isnan(ma60)) and ma60 > 0:
            dev60 = (last - ma60) / ma60 * 100
            state60 = f"{'🟢 站上' if dev60 > 0 else '🔴 跌破'}（乖离 {dev60:+.1f}%）"
            brief60 = f"{'🟢 站上' if dev60 > 0 else '🔴 跌破'} {dev60:+.1f}%"
        else:
            state60 = brief60 = "─"

        if ma120 is not None and not (isinstance(ma120, float) and np.isnan(ma120)) and ma120 > 0:
            dev120 = (last - ma120) / ma120 * 100
            state120 = f"{'🟢 站上' if dev120 > 0 else '🔴 跌破'}（乖离 {dev120:+.1f}%）"
        else:
            state120 = "─"

        action_line = f"加仓参考：现价回到 MA20（{_fmt(ma20)}）附近且收盘不破，才构成加仓位置。"

        # 未持仓：只给一行摘要，避免 18 只非持仓标的把报告撑长
        if not holding:
            return f"""### 📏 均线位置（MA20）

现价 {_fmt(last)} · MA20 {_fmt(ma20)} {brief20} · MA60 {_fmt(ma60)} {brief60} · 仅观察
"""

        return f"""### 📏 均线位置（MA20 · 回踩加仓参考）

| 指标 | 数值 | 状态 |
| --- | --- | --- |
| 现价 | {_fmt(last)} | — |
| MA20 | {_fmt(ma20)} | {state20} |
| MA60 | {_fmt(ma60)} | {state60} |
| MA120 | {_fmt(ma120)} | {state120} |

- {dist20}
- {action_line}

> 用途：只回答「现在这个价能不能下手」。下方「减仓 / 清仓」「止盈」模块里的 MA20 是卖出条件（且需配合回撤/乖离阈值），口径不同，勿混用。
"""
    except Exception:
        return ""
