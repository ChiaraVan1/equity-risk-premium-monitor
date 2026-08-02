"""
dividend_yield_analysis.py
──────────────────────────────────────────────────────────────────────────────
指数股息率估值锚模块（原 dividend_yield.py 拆分出的"分析/报告生成"部分）
数据获取/缓存管理见 dividend_yield_fetch.py。

【2026-08-02 拆分说明】本文件是从 dividend_yield.py 原样拆分出来的，只做了
"搬家"，函数体没有任何改动，仅把对本地缓存的读取从"文件内直接调用"改成
"import dividend_yield_fetch._load_cache"。
──────────────────────────────────────────────────────────────────────────────
"""

from config_loader import DIVIDEND_INDEX_CODES, YFINANCE_TICKERS
from dividend_yield_fetch import _load_cache

_ZONE_THRESHOLDS = [
    (0.90, "🟢 股息率历史罕见地高"),
    (0.75, "🟢 股息率偏高"),
    (0.50, "🟡 股息率中性偏高"),
    (0.25, "🟠 股息率中性偏低"),
    (0.10, "🔴 股息率偏低"),
    (0.00, "🚨 股息率历史罕见地低"),
]

# ──────────────────────────────────────────────────────────────────────────
# ── 分位计算 & 报告生成
# ──────────────────────────────────────────────────────────────────────────

def compute_dividend_percentile(code: str) -> dict:
    """计算当前股息率在本地缓存历史序列中的分位。股息率越高越便宜，
    分位=历史上有多少比例的值低于当前值，分位越高=越便宜。"""
    df = _load_cache(code)
    if df is None or len(df) == 0:
        return {"has_data": False}

    dy_series = df["dy"]
    cur_dy = dy_series.iloc[-1]
    cur_date = df["date"].iloc[-1]

    percentile = (dy_series < cur_dy).mean()
    zone = next(label for threshold, label in _ZONE_THRESHOLDS if percentile >= threshold)

    return {
        "has_data": True,
        "code": code,
        "cur_dy": cur_dy,
        "cur_date": cur_date,
        "percentile": percentile,
        "zone": zone,
        "n": len(dy_series),
        "p10": dy_series.quantile(0.10),
        "p25": dy_series.quantile(0.25),
        "p50": dy_series.quantile(0.50),
        "p75": dy_series.quantile(0.75),
        "p90": dy_series.quantile(0.90),
        "series": dy_series,
    }

def build_dividend_yield_block(erp_code: str) -> str:
    """股息率估值锚区块，风格仿照 build_shiller_block()。
    支持A股指数和yfinance标的。
    erp_code 不支持（或本地无缓存数据）时返回空字符串，不影响主报告。"""

    # 确定是A股指数还是yfinance标的
    code = None
    if erp_code in DIVIDEND_INDEX_CODES:
        code = DIVIDEND_INDEX_CODES[erp_code]
    elif erp_code in YFINANCE_TICKERS:
        code = erp_code
    else:
        return ""

    result = compute_dividend_percentile(code)
    if not result["has_data"]:
        return "\n> ⚠️ 未能获取股息率数据（本地无缓存，请先调用 ensure_dividend_data_fresh()），跳过股息率估值锚分析。\n"

    date_str = (
        result["cur_date"].strftime("%Y-%m-%d")
        if hasattr(result["cur_date"], "strftime")
        else str(result["cur_date"])
    )
    sample_warning = (
        f"\n> ⚠️ 历史样本仅 {result['n']} 个交易日，参考时请注意统计可靠性。"
        if result["n"] < 250
        else ""
    )

    # 标注数据源
    if erp_code in YFINANCE_TICKERS:
        source_label = "yfinance"
    else:
        source_label = "理杏仁（市值加权口径）"

    block = f"""
---
### 股息率估值锚（{source_label}，周更）

> 方法：股息率越高代表相对越便宜。当前值在本地缓存历史序列中的分位，
> 分位越高 = 当前股息率历史上越少见地高 = 估值越便宜。
> 数据日期：**{date_str}**（周频更新，非日频）
{sample_warning}

| 指标 | 数值 | 说明 |
|:-----|-----:|:-----|
| 当前股息率 | **{result['cur_dy']:.2f}%** | {source_label} |
| 历史分位 | **P{result['percentile']*100:.0f}** | **{result['zone']}** |

| 历史股息率分布 | 数值 |
|:--------------|-----:|
| P90（历史股息率高位） | {result['p90']:.2f}% |
| P75 | {result['p75']:.2f}% |
| P50（中位数） | {result['p50']:.2f}% |
| P25 | {result['p25']:.2f}% |
| P10（历史股息率低位） | {result['p10']:.2f}% |
"""
    return block

# ── 本地测试 ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 80)
    print("股息率数据更新 & 报告生成")
    print("=" * 80)

    # 从config_loader获取所有指数
    from config_loader import INDICES_LIST, get_all_codes
    from dividend_yield_fetch import ensure_dividend_data_fresh

    # 更新所有指数的数据（A股 + yfinance）
    ensure_dividend_data_fresh()

    # 为所有指数生成报告块
    all_codes = get_all_codes()
    for code in all_codes:
        block = build_dividend_yield_block(code)
        if block:
            print(block)
            print("\n")
