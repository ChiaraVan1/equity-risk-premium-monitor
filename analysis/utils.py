"""
analysis/utils.py
分析工具函数：新鲜度检查、格式化、辅助计算
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


def generate_action_sentence(disc: dict, divg: dict, vol: dict, zone_label: str) -> str:
    """生成执行动作建议。"""
    actions = []
    
    if disc.get("premium", 0) > 0.02:
        actions.append("⚠️ 溢价过高")
    if divg.get("divergence", False):
        actions.append("📉 量价背离")
    if vol.get("high", False):
        actions.append("🌊 波动偏高")

    if not actions:
        return "无特殊提示"
    return " | ".join(actions)


def _format_win_odds(r: dict) -> str:
    """格式化胜率和赔率。"""
    win = r.get("win_rate", 0.0)
    odds = r.get("odds_ratio", None)
    
    win_str = f"{win*100:.0f}%" if win == win else "─"
    
    if odds is None or odds != odds:
        odds_str = "─"
    elif odds > 100:
        odds_str = "∞"
    else:
        odds_str = f"{odds:.2f}x"
    
    return f"胜{win_str}·赔{odds_str}"


def _format_range(r: dict) -> str:
    """格式化范围统计。"""
    dd = r.get("dd", None)
    rebound = r.get("rebound", None)
    
    if dd is None or dd != dd:
        dd_str = "─"
    else:
        dd_str = f"{dd*100:.1f}%"
    
    if rebound is None or rebound != rebound:
        rebound_str = "─"
    else:
        rebound_str = f"{rebound*100:.1f}%"
    
    return f"回撤{dd_str} / 反弹{rebound_str}"
