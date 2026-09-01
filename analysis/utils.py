"""
analysis/utils.py
分析工具函数：新鲜度检查、格式化、辅助计算

【2026-08-02 恢复说明】
generate_action_sentence() 在重构中被改写成了另一套逻辑（输出"⚠️溢价过高"这类
markers列表），丢失了原版"分批建仓/一次建仓/规避不建仓"这套仪表盘执行动作文案。
现恢复为原版实现（接收的是图标字符串，不是原始数值字典），调用方
erp_position.py 里对应的调用需要同步传入图标而不是数值字典。
其余函数（新鲜度检测、_format_win_odds、_format_range、safe_action_markers）
本身没问题，原样保留。
"""
import pandas as pd

FRESHNESS_STALE_POINTS = 3


def check_metric_freshness(value_series: pd.Series, stale_points: int = FRESHNESS_STALE_POINTS) -> dict:
    """检测PE/PS序列是否连续stale_points个最新数据点数值完全未变化。"""
    s = value_series.dropna()
    s = s[s.index.dayofweek < 5]
    if len(s) == 0:
        return {"is_stale": False, "unchanged_count": 0, "last_value": None,
                "last_date": None, "first_unchanged_date": None}

    last_value = s.iloc[-1]
    unchanged_count = 0
    first_unchanged_date = None

    for i in range(len(s) - 1, -1, -1):
        if abs(s.iloc[i] - last_value) < 1e-9:
            unchanged_count += 1
            first_unchanged_date = s.index[i]
        else:
            break

    is_stale = unchanged_count >= stale_points
    return {
        "is_stale": is_stale,
        "unchanged_count": unchanged_count,
        "last_value": last_value,
        "last_date": s.index[-1] if len(s) > 0 else None,
        "first_unchanged_date": first_unchanged_date,
    }


def build_freshness_note(freshness: dict, metric_name: str) -> str:
    """生成新鲜度说明。"""
    if not freshness["is_stale"]:
        return ""

    return (f"\n> ⚠️ 数据新鲜度预警：{metric_name} 连续 {freshness['unchanged_count']} 个交易日未更新，"
            f"最后值为 {freshness['last_value']:.4f}（{freshness['first_unchanged_date'].strftime('%Y-%m-%d')} 起）\n")


def generate_action_sentence(disc: str, divg: str, vol: str, zone_label: str) -> str:
    """把折溢价/量价背离/波动率信号拼成一句操作建议文案。
    disc: 折溢价图标（"💎"/"🟢"/"🟡"/"🟠"/"🔴"/"─"）
    divg: 量价背离图标（"⚠️" 表示背离，"─" 表示无背离）
    vol:  波动率图标（"🔴"/"🟠"/"🟢"/"─"）
    zone_label: ERP估值分档标签（如 "🔴 高估/规避"），以 🔴 或 🚨 开头时直接规避不建仓。
    """
    if zone_label and (zone_label.startswith("🔴") or zone_label.startswith("🚨")):
        return "规避，不建仓"
    prefix = "等量能确认，" if divg == "⚠️" else ""
    if vol == "🔴":
        mid = "分批建仓"
    elif vol == "🟠":
        mid = "建仓，注意分批"
    else:
        mid = "一次建仓"
    if disc == "🔴":
        suffix = "，等折价再入"
    elif disc in ["💎", "🟢"]:
        suffix = "，折价窗口开着"
    else:
        suffix = ""
    return prefix + mid + suffix


def _format_win_odds(r: dict) -> str:
    """把 win_rate/odds_ratio 格式化为纯数字展示，如 '胜78%·赔1.85x' 或 '胜92%·赔∞'。
    odds_ratio=None 表示已跌破P10、亏损空间趋近于0（理论无穷大），用符号∞而非文字。
    win_rate为NaN（样本不足/计算失败）时返回占位符─。"""
    win = r.get("win_rate", None)
    odds = r.get("odds_ratio", None)

    if win is None or win != win:
        return "─"
    win_str = f"{win*100:.0f}%"

    if odds is None or odds != odds:
        odds_str = "∞"
    elif odds > 100:
        odds_str = "∞"
    else:
        odds_str = f"{odds:.2f}x"

    return f"胜{win_str}·赔{odds_str}"


def _format_range(r: dict) -> str:
    """把120天区间回撤/反弹格式化为纯数字展示，如 '回撤-18.2%·反弹+9.4%'。
    无价格数据时返回占位符─。"""
    dd = r.get("dd", None)
    rb = r.get("rebound", None)
    if dd is None or dd != dd or rb is None or rb != rb:
        return "─"
    return f"回撤{dd:+.1%}·反弹{rb:+.1%}"


def safe_action_markers(etf_df) -> dict:
    """从 etf_df 里尽量安全地提取 波动/量能/折溢价 标记，供仪表盘展示。
    多重 key 兜底 + try/except，任何字段取不到都优雅降级为"─"。"""
    result = {"vol_icon": "─", "vol_flag": False,
              "divergence_flag": False,
              "premium_icon": "─", "premium_val": 0.0}

    if etf_df is None:
        return result

    def _pick(row_or_df, candidates):
        for c in candidates:
            try:
                v = row_or_df[c]
                if hasattr(v, "iloc"):
                    v = v.iloc[-1]
                if v == v:  # not NaN
                    return v
            except Exception:
                continue
        return None

    try:
        row = etf_df.iloc[-1] if hasattr(etf_df, "iloc") else etf_df

        vol_pct = _pick(row, ["vol_percentile", "volatility_percentile", "vol_pct"])
        if vol_pct is not None:
            if vol_pct >= 0.75:
                result["vol_icon"] = "🔴"
                result["vol_flag"] = True
            elif vol_pct >= 0.5:
                result["vol_icon"] = "🟠"
            else:
                result["vol_icon"] = "🟢"

        divg = _pick(row, ["divergence", "price_volume_divergence", "is_divergence"])
        if divg:
            result["divergence_flag"] = True

        premium = _pick(row, ["premium_discount", "premium_rate", "discount_premium"])
        if premium is not None:
            result["premium_val"] = premium
            if premium <= -0.02:
                result["premium_icon"] = "💎"
            elif premium < 0:
                result["premium_icon"] = "🟢"
            elif premium == 0:
                result["premium_icon"] = "🟡"
            elif premium < 0.02:
                result["premium_icon"] = "🟠"
            else:
                result["premium_icon"] = "🔴"
    except Exception:
        pass

    return result
