import pandas as pd
import numpy as np
import os
from datetime import datetime
import requests

from etf_metrics import load_etf_metrics, build_etf_metrics_block


# ══════════════════════════════════════════════════════════════════════
#  Shiller CAPE 长期回报锚模块
#  数据源: Shiller ie_data.xls (Data sheet)
#  逻辑:
#    1. 读取历史 CAPE 和 Real 10Y Excess Annualized Returns
#    2. 按 CAPE 区间分组，直接取同组的历史回报分布
#    3. 输出当前 CAPE 所在组的均值、分位、样本数
# ══════════════════════════════════════════════════════════════════════

SHILLER_PATH = os.getenv("SHILLER_PATH", "./data/ie_data.xls")

_shiller_cache = {}   # 避免多次读文件

# CAPE 分组区间定义
_CAPE_BINS   = [0,  10,  15,  20,  25,  30,  35,  40,  999]
_CAPE_LABELS = ['<10', '10-15', '15-20', '20-25', '25-30', '30-35', '35-40', '>40']


# ── 顶部总览表 ────────────────────────────────────────────────────────────────

PRICE_CSV_URL = os.getenv(
    "PRICE_CSV_URL",
    "https://github.com/ChiaraVan1/ETF_data_project/releases/latest/download/etf_price.csv"
)


def load_ps_data():
    """加载 HSTECH PS 数据"""
    ps_path = "./data/ps_HSTECH.csv"
    if not os.path.exists(ps_path):
        return None
    df = pd.read_csv(ps_path, index_col=0, parse_dates=True)
    return df


def build_unified_valuation_block(df, code):
    """
    统一的估值决策模块：胜率 = 1 - Percentile；赔率 = 空间法
    支持 ERP (通用) 和 PSY (HSTECH)
    """
    is_hstech = (code == "HSTECH")
    
    if is_hstech:
        data_df = load_ps_data()
        if data_df is None:
            return "\n> ⚠️ 未找到 HSTECH PS 数据。\n"
        val_series = data_df["psy"].dropna()
        price_metric_series = data_df["ps"].dropna()
        m_name, p_name = "PSY", "PS"
    else:
        if df is None or len(df) == 0:
            return "\n> ⚠️ ERP 数据不足。\n"
        val_series = df['ERP'].dropna()
        price_metric_series = df['PE'].dropna()
        m_name, p_name = "ERP", "PE"

    if len(val_series) < 50:
        return "\n> ⚠️ 样本不足，无法计算胜率赔率。\n"

    # 当前值
    cur_val = val_series.iloc[-1]
    cur_p_metric = price_metric_series.iloc[-1]
    
    # 1. 胜率：ERP/PSY 越高越便宜，分位数本身即胜率（高分位 = 高胜率）
    percentile = (val_series < cur_val).mean()
    win_rate = percentile
    
    # 2. 赔率：空间法
    avg_p = price_metric_series.mean()
    max_p = price_metric_series.max()
    min_p = price_metric_series.min()
    
    # 预期涨幅 = (当前PE - 均值PE) / 当前PE (PE 下降 = 价格上涨)
    reward = (cur_p_metric - avg_p) / cur_p_metric if cur_p_metric > avg_p else 0
    
    # 潜在跌幅 = (当前PE - 历史最高PE) / 当前PE（绝对值）
    risk = abs((cur_p_metric - max_p) / cur_p_metric) if cur_p_metric < max_p else 0.05
    
    if cur_p_metric >= max_p:
        risk = 0.05
    
    odds_ratio = reward / risk if risk > 0 else float('inf')
    
    # 估值区间判断
    if percentile >= 0.75:
        zone_icon = "🟢"
        zone_name = "极度低估"
    elif percentile >= 0.50:
        zone_icon = "🟡"
        zone_name = "合理偏低"
    elif percentile >= 0.25:
        zone_icon = "🟠"
        zone_name = "合理偏高"
    else:
        zone_icon = "🔴"
        zone_name = "严重高估"
    
    # 综合评级（win_rate = ERP历史分位，越高代表越便宜，胜率越高）
    if win_rate >= 0.75 and odds_ratio >= 1.5:
        rating = "🟢 高胜率 + 高赔率，极佳买点"
    elif win_rate >= 0.75 and odds_ratio >= 1.0:
        rating = "🟢 胜率尚可 + 赔率合理，较好买点"
    elif win_rate >= 0.50 and odds_ratio >= 1.0:
        rating = "🟡 胜率中等 + 赔率一般，可参与"
    elif win_rate >= 0.50 and odds_ratio >= 0.8:
        rating = "🟡 胜率赔率均衡，中性"
    elif win_rate < 0.25 and odds_ratio < 0.5:
        rating = "🚨 低胜率 + 低赔率，双杀，规避"
    elif win_rate < 0.25:
        rating = "🔴 低胜率，谨慎"
    else:
        rating = "🟠 中性偏弱"
    
    # 期望值 = 胜率 × 预期涨幅 - 败率 × 潜在跌幅
    expected_return = win_rate * reward - (1 - win_rate) * risk
    
    block = f"""
---
### 核心估值决策（基于 {m_name} 框架）

> 方法：胜率 = {m_name}历史分位（越高越便宜）；赔率 = 均值回归空间 / 历史极值风险
> 当前 {m_name} = **{cur_val:.2%}**，历史分位 = **{percentile:.1%}** {zone_icon} **{zone_name}**

| 指标 | 数值 | 说明 |
|:-----|-----:|:-----|
| **胜率** | **{win_rate:.1%}** | {m_name} 历史分位（高分位 = 高胜率） |
| **赔率（盈亏比）** | **{odds_ratio:.2f}x** | 均值回归空间 / 历史极值风险 |
| 预期回归涨幅 | **+{reward:.1%}** | 回归 {p_name} 历史均值的理论空间 |
| 潜在回撤风险 | **-{risk:.1%}** | 跌至 {p_name} 历史最高位的风险 |
| 期望收益 | **{expected_return:+.1%}** | 胜率×涨幅 − 败率×跌幅 |

| {p_name} 统计 | 数值 |
|:-------------|-----:|
| 当前 {p_name} | **{cur_p_metric:.2f}x** |
| 历史均值 | {avg_p:.2f}x |
| 历史最低 | {min_p:.2f}x |
| 历史最高 | {max_p:.2f}x |

**综合评级：{rating}**
"""
    return block


# ── 顶部总览表 ────────────────────────────────────────────────────────────────
def build_summary_block(summary_list: list) -> str:
    """所有标的的信号灯 + 仓位一览，放在报告最顶部"""
    if not summary_list:
        return ""
    rows = []
    for r in summary_list:
        zone_short = r["erp_zone"].split("(")[0].strip()
        rows.append(
            f"| {r['name']} ({r['code']}) "
            f"| {zone_short} "
            f"| **{r['total_pct']}%** "
            f"| {r['b_pct']}+{r['v_pct']}+{r['t_pct']} |"
        )
    rows_md = "\n".join(rows)
    return f"""## 📊 今日总览 · {datetime.now().strftime('%m-%d')}

| 标的 | 估值 | 总仓位 | 底仓+价值+投机 |
|:----|:-----|------:|:-------------|
{rows_md}

---
"""


# ── 颜色图例（插入报告顶部一次） ──────────────────────────────────────────────
LEGEND_BLOCK = """---
**估值颜色说明**（适用于全报告所有 ERP / PS / PSY 区间标注）

| 颜色 | 含义 | ERP 对应分位 |
|:----:|:-----|:------------|
| 🟢 | 低估（便宜） | >= P75 |
| 🟡 | 合理偏低 | P50 – P75 |
| 🟠 | 合理偏高 / 开始高估 | P25 – P50 |
| 🔴 | 高估 | P10 – P25 |
| 🚨 | 极度高估 / 危险泡沫 | < P10 |

> ERP（股权风险溢价）= 1/PE − 无风险利率。ERP 越高表示股票相对债券越便宜，ERP 越低（尤其负值）表示估值越贵。
---
"""


def _load_shiller():
    """读取并缓存 Shiller 数据，返回 (grouped_stats, full_valid_df, cape_now)"""
    if _shiller_cache:
        return _shiller_cache["grouped"], _shiller_cache["valid"], _shiller_cache["cape_now"]

    if not os.path.exists(SHILLER_PATH):
        return None, None, None

    df = pd.read_excel(SHILLER_PATH, engine="xlrd", sheet_name="Data",
                       header=7, skiprows=[8])
    df.columns = df.columns.str.strip()
    df = df.rename(columns={
        "CAPE":      "cape",
        "Returns.2": "excess_return_10y",
    })
    df = df[pd.to_numeric(df["Date"], errors="coerce").notna()].copy()

    valid = df[["cape", "excess_return_10y"]].dropna().copy()
    valid["cape_bin"] = pd.cut(valid["cape"], bins=_CAPE_BINS, labels=_CAPE_LABELS)

    grouped = valid.groupby("cape_bin", observed=True)["excess_return_10y"].agg(
        count="count",
        mean="mean",
        p10=lambda x: x.quantile(0.10),
        p25=lambda x: x.quantile(0.25),
        p50=lambda x: x.quantile(0.50),
        p75=lambda x: x.quantile(0.75),
        p90=lambda x: x.quantile(0.90),
    )

    cape_now = df["cape"].dropna().iloc[-1]

    _shiller_cache["grouped"]  = grouped
    _shiller_cache["valid"]    = valid
    _shiller_cache["cape_now"] = cape_now
    return grouped, valid, cape_now


def build_shiller_block(code):
    """
    仅对 SPY 生成 Shiller 补充模块。
    返回 markdown 字符串，失败时返回空字符串。
    """
    if code != "SPY":
        return ""

    grouped, valid, cape_now = _load_shiller()
    if grouped is None:
        return "\n> ⚠️ 未找到 Shiller 数据文件，跳过长期回报锚分析。\n"

    cape_bin_series = pd.cut([cape_now], bins=_CAPE_BINS, labels=_CAPE_LABELS)
    current_bin = cape_bin_series[0]

    if current_bin not in grouped.index:
        return "\n> ⚠️ 当前 CAPE 超出历史分组范围，无法匹配。\n"

    g = grouped.loc[current_bin]
    n = int(g["count"])

    mean_pct = (valid["excess_return_10y"] < g["mean"]).mean()

    if mean_pct >= 0.90:
        zone = "🟢 极度乐观（历史顶部）"
    elif mean_pct >= 0.75:
        zone = "🟢 显著乐观"
    elif mean_pct >= 0.50:
        zone = "🟡 中性偏好"
    elif mean_pct >= 0.25:
        zone = "🟠 中性偏差"
    elif mean_pct >= 0.10:
        zone = "🔴 长期回报预期偏低"
    else:
        zone = "🚨 历史罕见低回报区"

    sample_warning = f"\n> ⚠️ 当前 CAPE 区间（{current_bin}x）历史样本仅 {n} 个月，参考时请注意统计可靠性。" if n < 30 else ""

    block = f"""
---
### Shiller 长期回报锚（基于150年历史分组均值）

> 方法：将历史所有月份按 CAPE 区间分组，取当前 CAPE 所在组的实际回报分布。
> 当前 CAPE **{cape_now:.1f}x**，落入分组：**{current_bin}x**（共 {n} 个历史月份）
{sample_warning}

| 指标 | 数值 | 说明 |
|:-----|-----:|:-----|
| 当前 CAPE | **{cape_now:.1f}x** | Shiller 最新值 |
| 同区间历史均值 | **{g['mean']:.2%}** | **{zone}** |
| 均值的全历史分位 | **P{mean_pct*100:.0f}** | 历史 {mean_pct*100:.0f}% 时间比现在更悲观 |

| 同 CAPE 区间的历史回报分布 | 超额回报 |
|:--------------------------|--------:|
| P90（乐观情景） | {g['p90']:.2%} |
| P75 | {g['p75']:.2%} |
| P50（中位数） | {g['p50']:.2%} |
| P25 | {g['p25']:.2%} |
| P10（悲观情景） | {g['p10']:.2%} |
"""
    return block


def build_trend_block(df, erp_series, code, quantiles):
    """
    生成近10个有效 ERP 数据点的趋势模块。
    - 日频指数（A股/美股）取最近10个交易日
    - 月频指数（EWQ/EWG/EWJ/EEM/HSTECH）取最近10个月末
    HSTECH 额外展示 PSY 近期趋势。
    """
    monthly_codes = {'EWQ', 'EWG', 'EWJ', 'EEM', 'HSTECH'}

    recent = df[df['ERP'].notna()][['Date', 'ERP', 'PE', 'Bond_Yield_10Y']].tail(10).copy()
    if len(recent) < 2:
        return ""

    x = np.arange(len(recent))
    slope = np.polyfit(x, recent['ERP'].values, 1)[0]
    n_days = len(recent)
    span_label = "月" if code in monthly_codes else "交易日"

    if slope > 0.0005:
        trend_icon = "持续走高"
    elif slope < -0.0005:
        trend_icon = "持续走低"
    else:
        trend_icon = "基本横盘"

    delta = recent['ERP'].iloc[-1] - recent['ERP'].iloc[0]
    delta_str = f"+{delta:.2%}" if delta >= 0 else f"{delta:.2%}"

    rows = []
    prev_erp = None
    for _, row in recent.iterrows():
        erp_val = row['ERP']
        pe_val  = row['PE']

        if erp_val >= quantiles["P75"]:
            zone_icon = "🟢"
        elif erp_val >= quantiles["P50"]:
            zone_icon = "🟡"
        elif erp_val >= quantiles["P25"]:
            zone_icon = "🟠"
        else:
            zone_icon = "🔴"

        if prev_erp is not None:
            diff = erp_val - prev_erp
            arrow = f"▲{diff:.2%}" if diff > 0 else (f"▼{abs(diff):.2%}" if diff < 0 else "─")
        else:
            arrow = "─"
        prev_erp = erp_val

        date_str = row['Date'].strftime("%m-%d") if code not in monthly_codes else row['Date'].strftime("%Y-%m")
        pe_str   = f"{pe_val:.1f}x" if pd.notna(pe_val) else "N/A"
        rows.append(f"| {date_str} | {pe_str} | **{erp_val:.2%}** {zone_icon} | {arrow} |")

    rows_md = "\n".join(rows)

    # HSTECH 额外展示 PSY 趋势
    psy_section = ""
    if code == "HSTECH":
        ps_df = load_ps_data()
        if ps_df is not None and "psy" in ps_df.columns:
            recent_psy = ps_df[ps_df["psy"].notna()][["ps", "psy"]].tail(10)
            if len(recent_psy) >= 2:
                psy_rows = []
                prev_psy = None
                for d, r in recent_psy.iterrows():
                    arrow = "─"
                    if prev_psy is not None:
                        diff = r["psy"] - prev_psy
                        arrow = f"▲{diff:.2%}" if diff > 0 else (f"▼{abs(diff):.2%}" if diff < 0 else "─")
                    prev_psy = r["psy"]
                    psy_rows.append(f"| {d.strftime('%Y-%m')} | {r['ps']:.2f}x | **{r['psy']:.2%}** | {arrow} |")
                psy_section = f"""
#### PSY 近期趋势（营收口径）

| 月份 | PS | PSY | 环比 |
|:-----|---:|----:|:-----|
{chr(10).join(psy_rows)}
"""

    block = f"""
---
### 近{n_days}{span_label} ERP 趋势

> 趋势方向：**{trend_icon}**，区间变化：**{delta_str}**

| 日期 | PE | ERP | 环比变化 |
|:-----|---:|----:|:---------|
{rows_md}
{psy_section}"""
    return block


def send_to_wechat(content):
    sct_key = os.getenv("SCT_KEY")
    if not sct_key:
        print("⚠️ 未找到 SCT_KEY，推送跳过。")
        return
    url = f"https://sctapi.ftqq.com/{sct_key}.send"
    data = {
        "title": f"ERP 决策报告 ({datetime.now().strftime('%Y-%m-%d')})",
        "desp": content
    }
    try:
        res = requests.post(url, data=data)
        print(f"✅ 方糖推送结果: {res.text}")
    except Exception as e:
        print(f"❌ 推送失败: {e}")


def analyze_and_suggest(code, name, etf_df=None, summary_list=None):
    file_path = f"./data/erp_{code}.csv"
    if not os.path.exists(file_path):
        print(f"❌ 未找到 {name} ({code}) 的数据文件")
        return

    df = pd.read_csv(file_path)
    df['Date'] = pd.to_datetime(df['Date'])
    erp_series = df['ERP'].dropna()

    min_samples = 50 if code in ('SPY', 'QQQ', 'EWQ', 'EWG', 'EWJ', 'EEM', 'HSTECH') else 250
    if len(erp_series) < min_samples:
        print(f"\n⚠️ {name} ({code}) 有效样本不足 ({len(erp_series)} < {min_samples})，跳过分析。")
        return

    mean_erp = erp_series.mean()
    # 欧日美负利率指数，用2022年后数据做锚
    if code in ('EWQ', 'EWG', 'EWJ', 'SPY', 'QQQ'):
        anchor = df[df['Date'] >= pd.Timestamp('2022-01-01')]['ERP'].dropna()
        if len(anchor) >= 30:
            erp_series = anchor

    quantiles = {
        "P95": erp_series.quantile(0.95),
        "P90": erp_series.quantile(0.90),
        "P75": erp_series.quantile(0.75),
        "P50": erp_series.quantile(0.50),
        "P25": erp_series.quantile(0.25),
        "P10": erp_series.quantile(0.10),
    }

    current_erp = erp_series.iloc[-1]
    current_date = df['Date'].iloc[-1].date()

    if current_erp >= quantiles["P90"]:
        erp_zone = "🟢 极度低估 (>=P90)"
    elif current_erp >= quantiles["P75"]:
        erp_zone = "🟢 显著低估 (P75-P90)"
    elif current_erp >= quantiles["P50"]:
        erp_zone = "🟡 合理偏低 (P50-P75)"
    elif current_erp >= quantiles["P25"]:
        erp_zone = "🟠 合理区间 (P25-P50)"
    elif current_erp >= quantiles["P10"]:
        erp_zone = "🔴 严重高估 (P10-P25)"
    else:
        erp_zone = "🚨 危险泡沫 (<P10)"

    if current_erp >= quantiles["P50"]:
        b_msg, b_pct = "泡沫仓: 已进入相对便宜击球区，30% 底仓应长期锁定", 30
    elif current_erp >= quantiles["P25"]:
        b_msg, b_pct = "泡沫仓: 尚未达到远期目标价，底仓持有不动", 30
    else:
        b_msg, b_pct = "泡沫仓: 触发极致远期溢价，考虑收割最后的筹码", 5

    if current_erp >= quantiles["P75"]:
        v_msg, v_pct = "价值仓: 足够便宜的价格，40% 核心主力必须在场", 40
    elif current_erp >= quantiles["P50"]:
        v_msg, v_pct = "价值仓: 估值修复中，建议持有 30%-40% 主力仓位", 35
    elif current_erp >= quantiles["P25"]:
        v_msg, v_pct = "价值仓: 回到合理估值区间，开始减持主力仓位", 10
    else:
        v_msg, v_pct = "价值仓: 估值已高，价值段位应已全部离场", 0

    if current_erp >= quantiles["P95"]:
        t_msg, t_pct = "投机仓: 触发极端惯性下跌，30% 预备队全额出击", 30
    elif current_erp >= quantiles["P90"]:
        t_msg, t_pct = "投机仓: 极低估区，保持 20% 仓位积极做T降本", 20
    elif current_erp >= quantiles["P50"]:
        t_msg, t_pct = "投机仓: 震荡区间，维持 10% 灵活部做T", 10
    else:
        t_msg, t_pct = "投机仓: 溢价区基本只卖不买，缩减至 5% 观察", 5

    total_pct = v_pct + b_pct + t_pct

    # ── HSTECH 专属：PS / PSY 补充模块 ──────────────────────────────────────
    ps_block = ""
    if code == "HSTECH":
        ps_df = load_ps_data()
        if ps_df is not None:
            ps_df = ps_df.dropna(subset=["ps"])
            if len(ps_df) >= 6:
                cur_ps = ps_df["ps"].iloc[-1]
                ps_pct = (ps_df["ps"] < cur_ps).mean()
                ps_zone = (
                    "🟢 极度低估 (历史低位)" if ps_pct <= 0.10 else
                    "🟢 显著低估" if ps_pct <= 0.25 else
                    "🟡 合理偏低" if ps_pct <= 0.50 else
                    "🟠 合理偏高" if ps_pct <= 0.75 else
                    "🔴 严重高估" if ps_pct <= 0.90 else
                    "🚨 危险泡沫 (历史高位)"
                )

                psy_rows = ""
                cur_psy = np.nan
                psy_zone = "N/A"
                psy_pct = np.nan
                if "psy" in ps_df.columns:
                    psy_s = ps_df["psy"].dropna()
                    if len(psy_s) >= 6:
                        cur_psy = psy_s.iloc[-1]
                        cur_rf = ps_df["rf"].iloc[-1] if "rf" in ps_df.columns else np.nan
                        psy_pct = (psy_s < cur_psy).mean()
                        psy_zone = (
                            "🟢 极度低估" if psy_pct >= 0.90 else
                            "🟢 显著低估" if psy_pct >= 0.75 else
                            "🟡 合理偏低" if psy_pct >= 0.50 else
                            "🟠 合理偏高" if psy_pct >= 0.25 else
                            "🔴 严重高估" if psy_pct >= 0.10 else
                            "🚨 危险泡沫"
                        )
                        rf_str = f"{cur_rf:.2%}" if pd.notna(cur_rf) else "N/A"
                        psy_rows = f"""
| 当前 PSY | **{cur_psy:.2%}** | **{psy_zone}（历史{psy_pct*100:.0f}%分位）** |
| PSY 历史均值 | {psy_s.mean():.2%} | 无风险利率={rf_str} |
| PSY P75 | {psy_s.quantile(0.75):.2%} | 显著低估门槛 |
| PSY P25 | {psy_s.quantile(0.25):.2%} | 进入高估门槛 |"""

                ps_block = f"""
---
### PS / PSY 估值（恒生科技补充，基于营收口径）

| 指标 | 数值 | 估值区间 |
|:-----|-----:|:---------|
| 当前 PS | **{cur_ps:.2f}x** | **{ps_zone}（历史{ps_pct*100:.0f}%分位）** |
| PS 历史均值 | {ps_df["ps"].mean():.2f}x | 样本{len(ps_df)}个月 |
| PS 历史最低 | {ps_df["ps"].min():.2f}x | |
| PS 历史最高 | {ps_df["ps"].max():.2f}x |{psy_rows} |

> PSY = 1/PS − 中国10年期国债收益率，衡量营收口径下相对无风险利率的超额回报，适用于PE因亏损公司失真时的替代指标。
"""

    shiller_block = build_shiller_block(code)
    trend_block = build_trend_block(df, erp_series, code, quantiles)
    etf_block = build_etf_metrics_block(code, etf_df)
    
    # 核心估值决策模块（统一方案）
    unified_block = build_unified_valuation_block(df, code)

    # ── 追加到顶部总览 ────────────────────────────────────────────────────────
    if summary_list is not None:
        summary_list.append({
            "name": name, "code": code,
            "erp_zone": erp_zone,
            "total_pct": total_pct,
            "b_pct": b_pct, "v_pct": v_pct, "t_pct": t_pct,
        })

    md = f"""## {name} ({code}) 决策报告
日期: {current_date}

| 指标 | 数值 | 估值区间 |
|:-----|-----:|:---------|
| 当前 ERP | **{current_erp:.2%}** | **{erp_zone}** |
| 历史均值 | {mean_erp:.2%} | {len(erp_series)}条样本 |

| 分位点 | ERP值 | 估值状态 |
|:-------|------:|:---------|
| P90 | {quantiles["P90"]:.2%} | 极度低估 |
| P75 | {quantiles["P75"]:.2%} | 显著低估 |
| P50 | {quantiles["P50"]:.2%} | 价值中枢 |
| P25 | {quantiles["P25"]:.2%} | 进入高估 |
| P10 | {quantiles["P10"]:.2%} | 极度高估 |
{unified_block}{trend_block}
---
### 仓位建议

**{b_msg}** ({b_pct}%)
**{v_msg}** ({v_pct}%)
**{t_msg}** ({t_pct}%)

建议总仓位：**{total_pct}%**（泡沫底仓 {b_pct}% + 价值主力 {v_pct}% + 投机奇兵 {t_pct}%）
{etf_block}{shiller_block}{ps_block}"""
    print(md)
    return md


if __name__ == "__main__":
    # 启动时加载一次 ETF 指标数据（失败不影响主流程）
    try:
        _etf_df = load_etf_metrics()
    except Exception as e:
        print(f"⚠️ ETF 指标加载意外失败：{e}，主报告继续运行。")
        _etf_df = None

    indices = [
        ("000300", "沪深300"),
        ("000688", "科创50"),
        ("000922", "中证红利"),
        ("399989", "中证医疗"),
        ("931071", "人工智能"),
        ("SPY",    "S&P 500"),
        ("QQQ",    "Nasdaq 100"),
        ("EWQ",    "MSCI France"),
        ("EWG",    "MSCI Germany"),
        ("EWJ",    "MSCI Japan"),
        ("EEM",    "MSCI Emerging"),
        ("HSTECH", "恒生科技指数"),
    ]

    summary_list = []
    report_list = []
    for code, name in indices:
        report_md = analyze_and_suggest(code, name, _etf_df, summary_list)
        if report_md:
            report_list.append(report_md)

    if report_list:
        full_report = (
            "# ERP 策略每日监控报告\n"
            + build_summary_block(summary_list)
            + LEGEND_BLOCK
            + "".join(report_list)
        )
        print("正在生成报告并准备推送...")
        send_to_wechat(full_report)
    else:
        print("❌ 未生成任何有效报告，请检查数据文件。")
