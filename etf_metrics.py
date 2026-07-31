"""
etf_metrics.py
──────────────────────────────────────────────────────────────────────────────
ETF 执行质量补充模块
数据源: simple_etf_metrics.csv
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


# ── ERP code → A股 ETF ts_code 映射 ──────────────────────────────────────────
# 一个 ERP 标的可能对应多只 ETF，取流动性最好的主力品种
# EWG/EEM 无对应 A 股 ETF，模块自动跳过
ERP_TO_ETF = {
    "000300": "510300.SH",
    "000688": "588000.SH",
    "000922": "515180.SH",
    "000015": "510880.SH",
    "399989": "512170.SH",
    "931071": "515980.SH",
    "HSTECH": "513180.SH",
    "SPY":    "513500.SH",
    "QQQ":    "159696.SZ",
    "EWQ":    "513080.SH",
    "EWJ":    "513880.SH",
    "EWG":    "159561.SZ",
    "EEM":    "520580.SH",   # 新兴亚洲
    "000069": "510150.SH",
    "930781": "516620.SH",
    "399967": "512660.SH",   # 中证军工
    "931066": "512710.SH",   # 军工龙头
    "930598": "516150.SH",   # 稀土产业
    "930794": None,   # 中美互联网，暂无对应ETF
    "931637": "513770.SH",   # 港股通互联网
      "000819": "512400.SH",   # 有色金属
    "950125": "588710.SH",   # 半导体材料设备
    "399975": "512880.SH",   # 证券公司
    "399986": "512800.SH",   # 中证银行
    "930633": "159766.SZ",      #  中证旅游
    "931946": "159172.SZ",      # 中证畜牧养殖
    "980032": "159755.SZ",   # 广发国证新能源车电池ETF
}

_metrics_cache = {}


def load_etf_metrics() -> pd.DataFrame | None:
    if _metrics_cache:
        return _metrics_cache.get("df")

    local_path = "./data/simple_etf_metrics.csv"
    try:
        df = pd.read_csv(local_path, index_col="ts_code")
        print(f"✅ 从本地加载 ETF 指标：{local_path}（{len(df)} 条）")
        _metrics_cache["df"] = df
        return df
    except FileNotFoundError:
        print("⚠️ 未找到 data/simple_etf_metrics.csv，ETF 执行质量模块将跳过，不影响主报告。")
    except Exception as e:
        print(f"⚠️ ETF 指标加载失败：{e}，ETF 执行质量模块将跳过，不影响主报告。")
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


def build_etf_metrics_block(erp_code: str, etf_df: pd.DataFrame | None) -> str:
    """
    ETF 执行质量 —— AI 解读版：只给一段结论，不再罗列全部原始数字。
    """
    if etf_df is None:
        return ""

    ts_code = ERP_TO_ETF.get(erp_code)
    if ts_code is None:
        return ""

    if ts_code not in etf_df.index:
        return f"\n> ⚠️ ETF {ts_code} 不在今日指标文件中，跳过执行质量分析。\n"

    row = etf_df.loc[ts_code]

    def safe(col, default=float("nan")):
        v = row.get(col, default)
        return default if pd.isna(v) else v

    discount_rate = safe("latest_discount_rate", 0.0)
    divergence    = bool(safe("is_price_turnover_divergence", False))
    vol_q1y       = safe("volatility_quantile_1y", 0.5)
    dd_q1y        = safe("max_drawdown_quantile_1y", 0.5)
    excess_mean   = safe("excess_return_mean", 0.0)

    # ── 折溢价：怎么下单 ──
    if discount_rate < -0.003:
        disc_note = "当前折价买入，执行成本占优，可直接下单"
    elif discount_rate < 0.0005:
        disc_note = "当前折溢价接近平价，下单无额外成本"
    elif discount_rate < 0.003:
        disc_note = "当前小幅溢价，建议挂限价单而非市价追高"
    else:
        disc_note = "当前溢价偏高，建议等折价窗口或用限价委托，避免多付冤枉钱"

    # ── 建仓节奏：波动 + 回撤 ──
    if vol_q1y >= 0.85 and dd_q1y >= 0.7:
        build_note = "波动率处于历史高位但回撤也已充分释放，适合分批建仓"
    elif vol_q1y >= 0.85:
        build_note = "波动率处于历史高位，单次建仓量要控制小，分批进场更稳"
    elif dd_q1y < 0.3 and vol_q1y < 0.4:
        build_note = "表面波动和回撤都不大，但这更像是风险尚未释放，别被「平静」误导去追高"
    else:
        build_note = "波动和回撤都处于正常水平，可按计划正常建仓"

    # ── 量能确认 ──
    if divergence:
        vol_confirm_note = "不过价格走势和成交量出现背离，追涨杀跌前最好等放量确认，避免踩中假突破"
    else:
        vol_confirm_note = "且价格与成交量方向一致，走势有量能支撑，可信度较高"

    # ── 跟踪质量 ──
    if excess_mean < -0.01:
        track_note = "这只ETF长期跑输基准，如果有同指数更优的替代品，可以考虑换仓"
    elif excess_mean > 0.01:
        track_note = "这只ETF长期跑赢基准，跟踪质量不错，值得继续持有"
    else:
        track_note = None

    parts = [disc_note, build_note, vol_confirm_note]
    if track_note:
        parts.append(track_note)
    conclusion = "；".join(parts) + "。"

    block = f"""
---
### ETF 执行质量（{ts_code}）· AI 解读

{conclusion}
"""
    return block

# ── 本地测试 ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    df = load_etf_metrics()
    if df is not None:
        for code in ["000688", "000300", "399989"]:
            block = build_etf_metrics_block(code, df)
            if block:
                print(block)
                print("\n" + "="*80 + "\n")
