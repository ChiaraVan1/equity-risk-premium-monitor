import pandas as pd
import numpy as np
import os
from datetime import datetime
import requests

from etf_metrics import load_etf_metrics, build_etf_metrics_block, ERP_TO_ETF


# ══════════════════════════════════════════════════════════════════════
#  常量 & 配置
# ══════════════════════════════════════════════════════════════════════

SHILLER_PATH = os.getenv("SHILLER_PATH", "./data/ie_data.xls")

# CAPE 分组区间定义（用于 Shiller 长期回报锚）
_CAPE_BINS   = [0,  10,  15,  20,  25,  30,  35,  40,  999]
_CAPE_LABELS = ['<10', '10-15', '15-20', '20-25', '25-30', '30-35', '35-40', '>40']


# ══════════════════════════════════════════════════════════════════════
#  Shiller CAPE 长期回报锚模块
# ══════════════════════════════════════════════════════════════════════

_shiller_cache = {}


def _load_shiller():
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


# ══════════════════════════════════════════════════════════════════════
#  HSTECH PS / PSY 模块
# ══════════════════════════════════════════════════════════════════════

def load_ps_data():
    ps_path = "./data/ps_HSTECH.csv"
    if not os.path.exists(ps_path):
        return None
    df = pd.read_csv(ps_path, index_col=0, parse_dates=True)
    return df


# ══════════════════════════════════════════════════════════════════════
#  赔率计算（ERP绝对值法）
# ══════════════════════════════════════════════════════════════════════

def calc_odds(cur_val, val_series):
    p10_val = val_series.quantile(0.10)
    p90_val = val_series.quantile(0.90)

    upside   = cur_val - p10_val
    downside = p90_val - cur_val

    if downside <= 0:
        return None
    elif upside <= 0:
        return 0.0
    else:
        return upside / downside


# ══════════════════════════════════════════════════════════════════════
#  【新增】ERP 斜率信号模块
# ══════════════════════════════════════════════════════════════════════

_SLOPE_EXTREME_THRESHOLD = 0.02   # 20日内ERP绝对变化 >= 2% 视为极陡


def compute_erp_slope_signal(erp_series: pd.Series) -> dict:
    if len(erp_series) < 21:
        return {"slope_20d": np.nan, "delta_20d": np.nan,
                "signal": "数据不足", "signal_icon": "─", "desc": ""}

    recent = erp_series.dropna().iloc[-21:]
    x = np.arange(len(recent))
    slope = np.polyfit(x, recent.values, 1)[0]
    delta = recent.iloc[-1] - recent.iloc[0]

    if delta >= _SLOPE_EXTREME_THRESHOLD:
        signal, signal_icon = "恐慌踩踏", "🚨"
        desc = (f"近20日ERP急速飙升 {delta:.2%}（斜率 {slope*100:.3f}%/日）"
                "— 市场处于恐慌抛售期，PE急速压缩，历史上往往是买点临近的强化信号，但需警惕基本面是否同步恶化。")
    elif delta >= _SLOPE_EXTREME_THRESHOLD * 0.4:
        signal, signal_icon = "估值快速改善", "🟢"
        desc = (f"近20日ERP持续走高 {delta:.2%}（斜率 {slope*100:.3f}%/日）"
                "— 估值快速修复，买入窗口正在打开。")
    elif delta <= -_SLOPE_EXTREME_THRESHOLD:
        signal, signal_icon = "情绪过热", "⚠️"
        desc = (f"近20日ERP急速坠落 {delta:.2%}（斜率 {slope*100:.3f}%/日）"
                "— 市场情绪快速升温，估值泡沫化加速，警戒高位。")
    elif delta <= -_SLOPE_EXTREME_THRESHOLD * 0.4:
        signal, signal_icon = "估值快速恶化", "🟠"
        desc = (f"近20日ERP持续走低 {delta:.2%}（斜率 {slope*100:.3f}%/日）"
                "— 估值向贵的方向漂移，需提高警惕。")
    else:
        signal, signal_icon = "横盘震荡", "🟡"
        desc = (f"近20日ERP变化 {delta:+.2%}（斜率 {slope*100:.3f}%/日）"
                "— 估值无明显趋势，保持既有仓位。")

    return {"slope_20d": slope, "delta_20d": delta,
            "signal": signal, "signal_icon": signal_icon, "desc": desc}


# ══════════════════════════════════════════════════════════════════════
#  【新增】减仓信号模块
# ══════════════════════════════════════════════════════════════════════

ETF_PRICE_PATH = "./data/etf_price.csv"
_ETF_PRICE_CACHE = {}


def _load_etf_price_series(erp_code: str):
    global _ETF_PRICE_CACHE
    if "df" not in _ETF_PRICE_CACHE:
        if not os.path.exists(ETF_PRICE_PATH):
            return None
        try:
            df = pd.read_csv(ETF_PRICE_PATH, index_col=0, parse_dates=True)
            _ETF_PRICE_CACHE["df"] = df
        except Exception:
            return None
    df = _ETF_PRICE_CACHE.get("df")
    if df is None or erp_code not in df.columns:
        return None
    return df[erp_code].dropna()


def build_exit_signal_block(erp_code: str, current_erp_percentile: float) -> str:
    price_s = _load_etf_price_series(erp_code)

    if price_s is None or len(price_s) < 5:
        return "\n> ⚠️ 减仓信号：无ETF价格数据，跳过。\n"

    # 低估区门控
    if current_erp_percentile >= 0.50:
        return f"""
---
### 减仓 / 清仓信号

> 🛡️ **低估区保护（ERP ≥ P50，当前分位 {current_erp_percentile:.0%}）**
> 均线与回撤条件已屏蔽，避免在恐慌底部触发错误减仓。
> 减仓只由 ERP 框架本身决定（ERP < P50 时重新激活）。
"""

    cur_price    = price_s.iloc[-1]
    lookback     = min(120, len(price_s))
    recent_high  = price_s.iloc[-lookback:].max()
    drawdown_from_high = (cur_price - recent_high) / recent_high

    ma20  = price_s.iloc[-min(20,  len(price_s)):].mean() if len(price_s) >= 5  else np.nan
    ma120 = price_s.iloc[-min(120, len(price_s)):].mean() if len(price_s) >= 30 else np.nan

    below_ma20  = cur_price < ma20  if pd.notna(ma20)  else False
    below_ma120 = cur_price < ma120 if pd.notna(ma120) else False
    dd_pct      = drawdown_from_high * 100

    alerts    = []
    max_level = 0

    if drawdown_from_high <= -0.10 and below_ma20:
        alerts.append(f"回撤 {dd_pct:.1f}%（≥10%）且跌破20日均线（MA20={ma20:.3f}）")
        max_level = max(max_level, 1)
    if drawdown_from_high <= -0.20:
        alerts.append(f"回撤 {dd_pct:.1f}%（≥20%），已触发强制清仓阈值")
        max_level = max(max_level, 2)
    if below_ma120 and pd.notna(ma120):
        alerts.append(f"跌破120日均线（MA120={ma120:.3f}，当前={cur_price:.3f}）")
        max_level = max(max_level, 2)

    ma20_str  = f"{ma20:.3f}"  if pd.notna(ma20)  else "─"
    ma120_str = f"{ma120:.3f}" if pd.notna(ma120) else "─"

    if max_level == 0:
        level_line = "✅ **无减仓信号** — 价格结构健康，持仓不动"
    elif max_level == 1:
        level_line = "⚠️ **第一次减仓预警** — 建议酌情减持部分仓位"
    else:
        level_line = "🚨 **强烈清仓预警**" if len(alerts) >= 2 else "🔴 **清仓预警** — 趋势破坏，建议清仓"

    alerts_md = "\n".join(f"  - {a}" for a in alerts) if alerts else "  - 无"

    return f"""
---
### 减仓 / 清仓信号

> ⚡ ERP当前分位 {current_erp_percentile:.0%}（< P50），价格信号已激活

{level_line}

| 指标 | 数值 | 状态 |
|:-----|-----:|:-----|
| 当前价格 | {cur_price:.3f} | ─ |
| 近期最高（120日内） | {recent_high:.3f} | ─ |
| 从高点回撤 | **{dd_pct:.1f}%** | {"🔴 ≥20%" if drawdown_from_high <= -0.20 else ("⚠️ ≥10%" if drawdown_from_high <= -0.10 else "✅ <10%")} |
| MA20 | {ma20_str} | {"🔴 跌破" if below_ma20 else "✅ 站上"} |
| MA120 | {ma120_str} | {"🔴 跌破" if below_ma120 else "✅ 站上"} |

**触发条件：**
{alerts_md}

> ⚠️ 基本面暴雷属于独立预警，见下方「基本面预警」模块。
"""


# ══════════════════════════════════════════════════════════════════════
#  【新增】基本面暴雷预警模块
# ══════════════════════════════════════════════════════════════════════

_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

def _fundamental_queries() -> dict:
    """动态生成搜索词，年份跟随当前年份"""
    y = datetime.now().year
    return {
        "000300": (f"沪深300 基本面 重大风险 监管 暴雷 {y}", f"CSI 300 major risk earnings collapse {y}"),
        "000688": (f"科创50 基本面 重大风险 监管 退市 {y}", f"STAR50 major risk earnings warning {y}"),
        "000922": (f"中证红利 分红 基本面 风险 {y}", f"CSI dividend index major risk {y}"),
        "399989": (f"中证医疗 医药 监管 集采 暴雷 {y}", f"China medical index regulation risk {y}"),
        "931071": (f"人工智能指数 监管 泡沫 风险 {y}", f"China AI index regulation bubble risk {y}"),
        "000069": (f"消费80 消费指数 基本面 风险 {y}", f"China consumer index fundamental risk {y}"),
        "930781": (f"中证影视 监管 票房 基本面 {y}", f"China media entertainment index risk {y}"),
        "000989": (f"全指可选 消费 基本面 风险 {y}", f"China optional consumer index risk {y}"),
        "931139": (f"CS消费50 基本面 风险 {y}", f"China consumer 50 index risk {y}"),
        "SPY":    (f"S&P 500 earnings collapse recession risk {y}", f"标普500 经济衰退 基本面 风险 {y}"),
        "QQQ":    (f"Nasdaq 100 tech earnings collapse regulation {y}", f"纳斯达克 科技股 监管 暴雷 {y}"),
        "EWQ":    (f"France stock market fundamental risk recession {y}", f"法国股市 经济 风险 {y}"),
        "EWG":    (f"Germany stock market fundamental risk recession {y}", f"德国股市 经济 风险 {y}"),
        "EWJ":    (f"Japan stock market fundamental risk BOJ {y}", f"日本股市 央行 基本面 风险 {y}"),
        "EEM":    (f"emerging market fundamental risk geopolitical {y}", f"新兴市场 地缘 基本面 风险 {y}"),
        "HSTECH": (f"恒生科技 监管 互联网 基本面 暴雷 {y}", f"Hang Seng Tech regulation crackdown earnings {y}"),
        "399967": (f"中证军工 军工 政策 订单 风险 {y}", f"China defense index policy orders risk {y}"),
        "931066": (f"军工龙头 业绩 基本面 风险 {y}", f"China defense leading stocks earnings risk {y}"),
        "930794": (f"中美互联网 监管 中美关系 风险 {y}", f"China US internet index regulation geopolitical risk {y}"),
        "931946": (f"畜牧养殖 猪价 周期 基本面 风险 {y}", f"China livestock index hog price cycle risk {y}"),
        "930598": (f"稀土产业 政策 出口管制 风险 {y}", f"China rare earth index policy export control risk {y}"),
    }


def build_fundamental_alert_block(code: str, name: str) -> str:
    queries = _fundamental_queries().get(code)
    if queries is None:
        return ""

    query_cn, query_en = queries
    prompt = f"""你是一位专业的股票基本面分析师。请搜索以下两个查询，判断"{name}({code})"在过去30天内是否存在重大基本面负面事件。

搜索查询1（中文）：{query_cn}
搜索查询2（英文）：{query_en}

判断标准（以下任一即为"疑似暴雷"）：
- 核心成分股出现重大财务造假、业绩暴雷、退市风险
- 行业遭遇超预期强监管、重大政策打压
- 宏观层面出现系统性风险（如金融危机苗头、主权债务危机）
- 指数或ETF本身出现清盘、停牌等结构性风险

请严格按以下JSON格式输出，不要输出任何其他内容：
{{"alert_level": "正常" | "关注" | "疑似暴雷", "confidence": "低" | "中" | "高", "summary": "不超过80字的摘要", "sources": ["来源1", "来源2"]}}"""

    try:
        import json
        payload = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1000,
            "tools": [{"type": "web_search_20250305", "name": "web_search"}],
            "messages": [{"role": "user", "content": prompt}]
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": os.getenv("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01",
        }
        resp = requests.post(_ANTHROPIC_API_URL, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        raw_text    = "\n".join(text_blocks).strip().replace("```json", "").replace("```", "").strip()
        result      = json.loads(raw_text)

        alert_level = result.get("alert_level", "正常")
        confidence  = result.get("confidence", "低")
        summary     = result.get("summary", "")
        sources     = result.get("sources", [])

        if alert_level == "疑似暴雷":
            level_icon = "🚨"
            action_tip = "**⚠️ 需人工确认后才可触发减仓/清仓操作，请立即核查。**"
        elif alert_level == "关注":
            level_icon = "⚠️"
            action_tip = "建议持续关注，暂不需要立即操作。"
        else:
            level_icon = "✅"
            action_tip = "近期无重大基本面异常。"

        sources_md = "\n".join(f"  - {s}" for s in sources) if sources else "  - 无"

        return f"""
---
### 基本面暴雷预警（AI辅助，需人工确认）

> 🤖 搜索近30天新闻。**本模块仅供参考，不自动触发任何交易动作。**

{level_icon} **{alert_level}**（置信度：{confidence}）

{action_tip}

**摘要：** {summary}

**参考来源：**
{sources_md}
"""
    except requests.exceptions.Timeout:
        return "\n> ⚠️ 基本面预警：API请求超时，跳过。\n"
    except Exception as e:
        return f"\n> ⚠️ 基本面预警：发生异常（{e}），跳过。\n"


# ══════════════════════════════════════════════════════════════════════
#  报告构建模块
# ══════════════════════════════════════════════════════════════════════

def build_unified_valuation_block(df, code):
    """
    统一的估值决策模块
    胜率 = ERP历史分位
    赔率 = (当前ERP - P10) / (P90 - 当前ERP)，ERP绝对值法
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

    cur_val      = val_series.iloc[-1]
    cur_p_metric = price_metric_series.iloc[-1]
    avg_p        = price_metric_series.mean()
    max_p        = price_metric_series.max()
    min_p        = price_metric_series.min()

    p10_val = val_series.quantile(0.10)
    p90_val = val_series.quantile(0.90)

    win_rate   = (val_series < cur_val).mean()
    odds_ratio = calc_odds(cur_val, val_series)
    if odds_ratio is None:
        odds_str = "极高（已超P90极度低估区）"
    else:
        odds_str = f"{odds_ratio:.2f}x"

    upside   = cur_val - p10_val
    downside = p90_val - cur_val

    if win_rate >= 0.90:
        zone_icon, zone_name = "🟢", "极度低估"
    elif win_rate >= 0.75:
        zone_icon, zone_name = "🟢", "显著低估"
    elif win_rate >= 0.50:
        zone_icon, zone_name = "🟡", "合理偏低"
    elif win_rate >= 0.25:
        zone_icon, zone_name = "🟠", "合理区间"
    elif win_rate >= 0.10:
        zone_icon, zone_name = "🔴", "严重高估"
    else:
        zone_icon, zone_name = "🚨", "危险泡沫"

    if odds_ratio is None:
        rating = "🟢 已进入极度低估区，极佳买点"
    elif odds_ratio == 0.0:
        rating = "🚨 已进入极度高估区，规避"
    elif win_rate >= 0.75 and odds_ratio >= 1.5:
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

    block = f"""
---
### 核心估值决策（基于 {m_name} 框架）

> 胜率 = {m_name}历史分位（越高代表当前越便宜）
> 赔率 = {m_name}回落盈利空间（当前{m_name} − P10） / {m_name}走高亏损空间（P90 − 当前{m_name}）
> 当前 {m_name} = **{cur_val:.2%}**，历史分位 = **{win_rate:.1%}** {zone_icon} **{zone_name}**

| 指标 | 数值 | 说明 |
|:-----|-----:|:-----|
| **胜率** | **{win_rate:.1%}** | 历史上 {win_rate:.1%} 的时间比现在更贵（{m_name}更低） |
| **赔率（盈亏比）** | **{odds_str}** | 盈利空间 {upside:.2%} / 亏损空间 {downside:.2%} |
| 当前 {p_name} | {cur_p_metric:.1f}x | 历史均值 {avg_p:.1f}x，最高 {max_p:.1f}x，最低 {min_p:.1f}x |

**综合评级：{rating}**
"""
    return block


def build_trend_block(df, erp_series, code, quantiles):
    """
    生成近10个月末 ERP 数据点的趋势模块。
    """
    if code == "HSTECH":
        ps_df = load_ps_data()
        if ps_df is None or "psy" not in ps_df.columns:
            return ""
        recent_psy = ps_df[ps_df["psy"].notna()][["ps", "psy"]].tail(10)
        if len(recent_psy) < 2:
            return ""

        slope_info = compute_erp_slope_signal(ps_df["psy"].dropna())

        psy_rows = []
        prev_psy = None
        for d, r in recent_psy.iterrows():
            arrow = "─"
            if prev_psy is not None:
                diff = r["psy"] - prev_psy
                arrow = f"▲{diff:.2%}" if diff > 0 else (f"▼{abs(diff):.2%}" if diff < 0 else "─")
            prev_psy = r["psy"]
            psy_rows.append(f"| {d.strftime('%Y-%m')} | {r['ps']:.2f}x | **{r['psy']:.2%}** | {arrow} |")
        return f"""
---
### 近10月 PSY 趋势（营收口径）

> 📐 近20日斜率信号：{slope_info['signal_icon']} **{slope_info['signal']}** — {slope_info['desc']}

| 月份 | PS | PSY | 环比 |
|:-----|---:|----:|:-----|
{chr(10).join(psy_rows)}
"""

    valid = df[df['ERP'].notna()][['Date', 'ERP', 'PE']].copy()
    if len(valid) < 2:
        return ""

    slope_info = compute_erp_slope_signal(erp_series)

    valid['YM'] = valid['Date'].dt.to_period('M')
    month_end = valid.groupby('YM').last().reset_index(drop=True)
    recent = month_end.tail(10).copy()

    x = np.arange(len(recent))
    slope = np.polyfit(x, recent['ERP'].values, 1)[0]

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

        date_str = row['Date'].strftime("%Y-%m")
        pe_str   = f"{pe_val:.1f}x" if pd.notna(pe_val) else "N/A"
        rows.append(f"| {date_str} | {pe_str} | **{erp_val:.2%}** {zone_icon} | {arrow} |")

    rows_md = "\n".join(rows)

    return f"""
---
### 近10月 ERP 趋势

> 趋势方向：**{trend_icon}**，区间变化：**{delta_str}**
> 📐 近20日斜率信号：{slope_info['signal_icon']} **{slope_info['signal']}** — {slope_info['desc']}

| 月份 | PE | ERP | 环比变化 |
|:-----|---:|----:|:---------|
{rows_md}
"""


# ══════════════════════════════════════════════════════════════════════
#  仪表盘 & 图例
# ══════════════════════════════════════════════════════════════════════

HOLDING_CATEGORY = {
    "000300": "低估-宽基",
    "000688": "科技-A股",
    "000922": "",
    "399989": "低估-医疗",
    "931071": "",
    "000069": "低估-消费",
    "930781": "",
    "000989": "",
    "931139": "",
    "SPY":    "",
    "QQQ":    "",
    "EWQ":    "",
    "EWG":    "",
    "EWJ":    "",
    "EEM":    "",
    "HSTECH": "低估-港股科技",
    "399967": "低估-军工",
    "931066": "",
    "930794": "",
    "931946": "",
    "930598": "低估-资源",
}


def generate_action_sentence(disc, divg, vol, zone_label):
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


def build_summary_block(summary_list: list, output_format: str = "html") -> str:
    if not summary_list:
        return ""

    date_str = datetime.now().strftime("%Y-%m-%d")

    def pos_color_class(pct):
        if pct >= 80: return "pos-high"
        if pct >= 60: return "pos-mid"
        if pct >= 40: return "pos-low"
        return "pos-min"

    zone_groups = [
        ("🟢 极度低估", lambda z: z.startswith("🟢 极度低估")),
        ("🟢 显著低估", lambda z: z.startswith("🟢 显著低估")),
        ("🟡 合理偏低", lambda z: z.startswith("🟡")),
        ("🟠 合理区间", lambda z: z.startswith("🟠")),
        ("🔴 高估/规避", lambda z: z.startswith("🔴") or z.startswith("🚨")),
    ]

    header = f"## 📊 决策仪表盘 · {date_str}"
    legend = (
        "胜率/赔率：🟢≥75% 🟡50-75% 🟠25-50% 🔴<25% · 赔率>1x为正 · 🟢极高=已超P90\n\n"
        "折溢价：💎大折价 🟢折价 🟡平价 🟠溢价 🔴大溢价 · 量：✅无背离 ⚠️背离 · 波动：🟢低 🟠中高 🔴高位分批\n\n"
        "ERP斜率：🚨恐慌踩踏 🟢快速改善 ⚠️情绪过热 🟠快速恶化 🟡横盘\n\n"
        "减仓信号：✅正常 ⚠️减仓预警 🔴清仓预警 🚨强烈清仓 🛡️低估区保护\n\n"
        "估值区间：🟢低估(≥P75) 🟡合理偏低(P50-P75) 🟠合理偏高(P25-P50) 🔴高估(P10-P25) 🚨危险泡沫(<P10)\n\n---"
    )

    if output_format == "markdown":
        lines = [header, "", legend, ""]
        for group_label, match_fn in zone_groups:
            group_items = [r for r in summary_list if match_fn(r.get("erp_zone", ""))]
            if not group_items:
                continue
            lines.append(f"\n**{group_label}**")
            for r in group_items:
                disc       = r.get("etf_discount",   "─")
                divg       = r.get("etf_divergence", "─")
                vol        = r.get("etf_vol",        "─")
                slope_sig  = r.get("slope_signal",   "─")
                exit_sig   = r.get("exit_signal",    "─")
                zone_label = r.get("erp_zone", "")
                action     = generate_action_sentence(disc, divg, vol, zone_label)
                cat        = HOLDING_CATEGORY.get(r["code"], "")
                cat_str    = f" [{cat}]" if cat else ""
                pos_structure = f"{r['b_pct']}+{r['v_pct']}+{r['t_pct']}"
                lines.append(
                    f"\n{r['name']} · {r['total_pct']}%({pos_structure}) · "
                    f"量{divg} 波{vol} 折{disc} · 斜率{slope_sig} · 减仓{exit_sig} · {action}{cat_str}"
                )
        return "\n".join(lines) + "\n\n---\n"

    else:
        rows_html = []
        for group_label, match_fn in zone_groups:
            group_items = [r for r in summary_list if match_fn(r.get("erp_zone", ""))]
            if not group_items:
                continue
            rows_html.append(
                f'<tr><td colspan="5" class="section-header">{group_label}</td></tr>'
            )
            for r in group_items:
                code        = r.get("code", "")
                etf_ticker  = ERP_TO_ETF.get(code, "─")
                etf_display = etf_ticker.split(".")[0] if "." in str(etf_ticker) else str(etf_ticker)
                total_pct     = r["total_pct"]
                pos_cls       = pos_color_class(total_pct)
                pos_structure = f"{r['b_pct']}+{r['v_pct']}+{r['t_pct']}"
                disc       = r.get("etf_discount",   "─")
                divg       = r.get("etf_divergence", "─")
                vol        = r.get("etf_vol",        "─")
                slope_sig  = r.get("slope_signal",   "─")
                exit_sig   = r.get("exit_signal",    "─")
                zone_label = r.get("erp_zone", "")
                action     = generate_action_sentence(disc, divg, vol, zone_label)
                cat        = HOLDING_CATEGORY.get(code, "")
                cat_str    = f" [{cat}]" if cat else ""
                rows_html.append(
                    f'<tr>'
                    f'<td class="col-name">{r["name"]}<br><span class="col-etf">{etf_display}</span></td>'
                    f'<td class="col-pos {pos_cls}">{total_pct}%<br><span class="col-sub">{pos_structure}</span></td>'
                    f'<td class="col-sig">量{divg}&nbsp;波{vol}&nbsp;折{disc}</td>'
                    f'<td class="col-sig2">斜{slope_sig}&nbsp;仓{exit_sig}</td>'
                    f'<td class="col-action">{action}{cat_str}</td>'
                    f'</tr>'
                )
        table_html = (
            '<table class="dashboard-table">\n'
            + "\n".join(rows_html)
            + "\n</table>"
        )
        return f"{header}\n{legend}\n\n{table_html}\n\n---\n"


LEGEND_BLOCK = """
ERP = 1/PE − 无风险利率；PSY = 1/PS − 无风险利率。越高越便宜。
赔率 = ERP回落盈利空间（当前ERP − P10） / ERP走高亏损空间（P90 − 当前ERP）

---
"""


# ══════════════════════════════════════════════════════════════════════
#  推送模块
# ══════════════════════════════════════════════════════════════════════

REPORT_URL = "https://chiaravan1.github.io/equity-risk-premium-monitor/report.html"


def markdown_to_html(md_text: str, date_str: str) -> str:
    import re

    def _inline(text):
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
        text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
        return text

    def convert_md(text):
        lines = text.split("\n")
        html_lines = []
        in_table = False
        in_code = False
        for line in lines:
            if line.startswith("```"):
                if not in_code:
                    html_lines.append('<pre><code>')
                    in_code = True
                else:
                    html_lines.append('</code></pre>')
                    in_code = False
                continue
            if in_code:
                html_lines.append(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
                continue
            if line.startswith("|"):
                if not in_table:
                    html_lines.append('<table>')
                    in_table = True
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if all(re.match(r'^:?-+:?$', c) for c in cells):
                    continue
                row_html = "".join(f"<td>{_inline(c)}</td>" for c in cells)
                html_lines.append(f"<tr>{row_html}</tr>")
                continue
            else:
                if in_table:
                    html_lines.append('</table>')
                    in_table = False
            if line.startswith("# "):
                html_lines.append(f'<h1>{_inline(line[2:])}</h1>')
            elif line.startswith("## "):
                html_lines.append(f'<h2>{_inline(line[3:])}</h2>')
            elif line.startswith("### "):
                html_lines.append(f'<h3>{_inline(line[4:])}</h3>')
            elif line.startswith("#### "):
                html_lines.append(f'<h4>{_inline(line[5:])}</h4>')
            elif line.startswith("> "):
                html_lines.append(f'<blockquote>{_inline(line[2:])}</blockquote>')
            elif line.startswith("---"):
                html_lines.append('<hr>')
            elif line.strip() == "":
                html_lines.append('<br>')
            elif line.startswith("<"):
                html_lines.append(line)
            else:
                html_lines.append(f'<p>{_inline(line)}</p>')
        if in_table:
            html_lines.append('</table>')
        return "\n".join(html_lines)

    body_html = convert_md(md_text)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ERP 监控报告 · {date_str}</title>
<style>
  :root {{
    --bg: #0d1117; --surface: #161b22; --surface2: #21262d;
    --border: #30363d; --text: #e6edf3; --muted: #8b949e;
    --green: #3fb950; --blue: #58a6ff; --accent: #1f6feb;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'SF Mono', 'Fira Code', monospace; font-size: 14px; line-height: 1.7; padding: 24px 16px; }}
  .wrap {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 20px; color: var(--blue); margin: 24px 0 12px; }}
  h2 {{ font-size: 17px; margin: 20px 0 10px; border-bottom: 1px solid var(--border); padding-bottom: 6px; }}
  h3 {{ font-size: 15px; color: var(--blue); margin: 16px 0 8px; }}
  h4 {{ font-size: 13px; color: var(--muted); margin: 12px 0 6px; }}
  p {{ margin: 6px 0; }}
  hr {{ border: none; border-top: 1px solid var(--border); margin: 20px 0; }}
  blockquote {{ border-left: 3px solid var(--accent); padding: 6px 12px; color: var(--muted); background: var(--surface); border-radius: 0 4px 4px 0; margin: 8px 0; font-size: 13px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 13px; }}
  td {{ padding: 6px 10px; border: 1px solid var(--border); }}
  tr:nth-child(even) td {{ background: var(--surface); }}
  tr:first-child td {{ background: var(--surface2); font-weight: 600; color: var(--muted); }}
  strong {{ color: var(--text); }}
  code {{ background: var(--surface2); padding: 2px 5px; border-radius: 3px; font-size: 12px; color: var(--blue); }}
  pre {{ background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 12px; overflow-x: auto; margin: 10px 0; }}
  pre code {{ background: none; padding: 0; color: var(--text); }}
  .footer {{ text-align: center; color: var(--muted); font-size: 11px; margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--border); }}
  .dashboard-table {{ width: 100%; border-collapse: collapse; }}
  .dashboard-table tr {{ border-bottom: 1px solid #21262d; }}
  .dashboard-table td {{ padding: 10px 8px; vertical-align: middle; border: none; }}
  .col-name  {{ width: 110px; font-weight: bold; }}
  .col-etf   {{ color: #8b949e; font-size: 11px; }}
  .col-pos   {{ width: 64px; text-align: center; font-size: 20px; font-weight: bold; }}
  .col-sub   {{ font-size: 10px; color: #8b949e; }}
  .col-sig   {{ width: 120px; }}
  .col-sig2  {{ width: 80px; }}
  .col-action {{ font-size: 12px; }}
  .pos-high   {{ color: #3fb950; }}
  .pos-mid    {{ color: #d29922; }}
  .pos-low    {{ color: #e3b341; }}
  .pos-min    {{ color: #f85149; }}
  .section-header {{
    font-size: 10px; color: #8b949e;
    text-transform: uppercase; letter-spacing: 1px;
    border-bottom: 1px solid #21262d; padding: 12px 0 5px;
    border: none;
  }}
</style>
</head>
<body>
<div class="wrap">
<h1>📊 ERP 策略监控报告 · {date_str}</h1>
{body_html}
<div class="footer">自动生成于 {date_str} · equity-risk-premium-monitor</div>
</div>
</body>
</html>"""


def save_html_report(full_report_md: str, date_str: str):
    os.makedirs("./docs", exist_ok=True)
    html = markdown_to_html(full_report_md, date_str)
    with open("./docs/report.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ HTML 报告已保存到 ./docs/report.html")


def send_to_wechat(summary_md: str, date_str: str):
    sct_key = os.getenv("SCT_KEY")
    if not sct_key:
        print("⚠️ 未找到 SCT_KEY，推送跳过。")
        return
    url = f"https://sctapi.ftqq.com/{sct_key}.send"
    data = {
        "title": f"ERP 决策报告 ({date_str})",
        "desp": summary_md + f"\n\n📄 [查看完整报告]({REPORT_URL})",
    }
    try:
        res = requests.post(url, data=data)
        print(f"✅ 方糖推送结果: {res.text}")
    except Exception as e:
        print(f"❌ 推送失败: {e}")


# ══════════════════════════════════════════════════════════════════════
#  主分析函数
# ══════════════════════════════════════════════════════════════════════

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

    current_erp  = erp_series.iloc[-1]
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

    _hstech_psy_s = None
    if code == "HSTECH":
        _ps_df = load_ps_data()
        if _ps_df is not None and "psy" in _ps_df.columns:
            _hstech_psy_s = _ps_df["psy"].dropna()
            current_erp = _hstech_psy_s.iloc[-1]
            erp_series  = _hstech_psy_s
            quantiles = {
                "P95": _hstech_psy_s.quantile(0.95),
                "P90": _hstech_psy_s.quantile(0.90),
                "P75": _hstech_psy_s.quantile(0.75),
                "P50": _hstech_psy_s.quantile(0.50),
                "P25": _hstech_psy_s.quantile(0.25),
                "P10": _hstech_psy_s.quantile(0.10),
            }

    # ── 仓位建议 ──────────────────────────────────────────────────────
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

    # ── 胜率/赔率 ─────────────────────────────────────────────────────
    if code == "HSTECH":
        _psy_s = _hstech_psy_s
        if _psy_s is not None:
            _win  = (_psy_s < _psy_s.iloc[-1]).mean()
            _odds = calc_odds(_psy_s.iloc[-1], _psy_s)
            erp_zone = (
                "🟢 极度低估 (>=P90)" if _win >= 0.90 else
                "🟢 显著低估 (P75-P90)" if _win >= 0.75 else
                "🟡 合理偏低 (P50-P75)" if _win >= 0.50 else
                "🟠 合理区间 (P25-P50)" if _win >= 0.25 else
                "🔴 严重高估 (P10-P25)" if _win >= 0.10 else
                "🚨 危险泡沫 (<P10)"
            )
        else:
            _win, _odds = float("nan"), float("nan")
    else:
        _win  = (erp_series < current_erp).mean()
        _odds = calc_odds(current_erp, erp_series)

    # ── ETF折溢价执行信号 ─────────────────────────────────────────────
    _etf_signal            = "─"
    _etf_discount_signal   = "─"
    _etf_divergence_signal = "─"
    _etf_vol_signal        = "─"
    if etf_df is not None:
        try:
            _ts = ERP_TO_ETF.get(code)
            if _ts and _ts in etf_df.index:
                _row  = etf_df.loc[_ts]
                _prem = float(_row.get("latest_discount_rate", float("nan")))
                if _prem == _prem:
                    if   _prem < -0.02:  _etf_signal = _etf_discount_signal = "💎"
                    elif _prem < -0.005: _etf_signal = _etf_discount_signal = "🟢"
                    elif _prem <  0.005: _etf_signal = _etf_discount_signal = "🟡"
                    elif _prem <  0.02:  _etf_signal = _etf_discount_signal = "🟠"
                    else:                _etf_signal = _etf_discount_signal = "🔴"
                _div = _row.get("is_price_turnover_divergence", float("nan"))
                if _div == _div:
                    _etf_divergence_signal = "⚠️" if int(_div) == 1 else "✅"
                _vq = float(_row.get("volatility_quantile_1y", float("nan")))
                if _vq == _vq:
                    if   _vq >= 0.85: _etf_vol_signal = "🔴"
                    elif _vq >= 0.60: _etf_vol_signal = "🟠"
                    else:             _etf_vol_signal = "🟢"
        except Exception:
            pass

    # ── 斜率信号 ──────────────────────────────────────────────────────
    slope_info = compute_erp_slope_signal(erp_series)

    # ── 减仓信号 ──────────────────────────────────────────────────────
    erp_percentile = (erp_series < current_erp).mean()
    exit_block     = build_exit_signal_block(code, erp_percentile)
    if "强烈清仓预警" in exit_block:   _exit_signal = "🚨"
    elif "清仓预警" in exit_block:      _exit_signal = "🔴"
    elif "减仓预警" in exit_block:      _exit_signal = "⚠️"
    elif "低估区保护" in exit_block:    _exit_signal = "🛡️"
    else:                               _exit_signal = "✅"

    if summary_list is not None:
        summary_list.append({
            "name": name, "code": code,
            "erp_zone": erp_zone,
            "total_pct": total_pct,
            "b_pct": b_pct, "v_pct": v_pct, "t_pct": t_pct,
            "win_rate": _win,
            "odds": _odds,
            "etf_signal":     _etf_signal,
            "etf_discount":   _etf_discount_signal,
            "etf_divergence": _etf_divergence_signal,
            "etf_vol":        _etf_vol_signal,
            "slope_signal":   slope_info["signal_icon"],
            "exit_signal":    _exit_signal,
        })

    # ── 报告头部 ──────────────────────────────────────────────────────
    if code == "HSTECH":
        if _hstech_psy_s is not None:
            header_block = f"""
---
# ═══ {name} ({code}) ═══
> 数据源：PS / PSY（营收口径），ERP 数据已停止更新不再展示。　{current_date}

| 指标 | 数值 | 估值区间 |
|:-----|-----:|:---------|
| 当前 PSY | **{current_erp:.2%}** | **{erp_zone}** |
| 历史均值 | {erp_series.mean():.2%} | {len(erp_series)}条样本 |

| 分位点 | PSY值（越高越便宜） | 价格估值状态 |
|:-------|-------------------:|:------------|
| P90 | {quantiles["P90"]:.2%} | 极度低估 |
| P75 | {quantiles["P75"]:.2%} | 显著低估 |
| P50 | {quantiles["P50"]:.2%} | 价值中枢 |
| P25 | {quantiles["P25"]:.2%} | 进入高估 |
| P10 | {quantiles["P10"]:.2%} | 极度高估 |
"""
        else:
            header_block = f"""
---
# ═══ {name} ({code}) ═══
> 数据源：PS / PSY（营收口径），ERP 数据已停止更新不再展示。　{current_date}
"""
    else:
        header_block = f"""
---
# ═══ {name} ({code}) ═══

| 指标 | 数值 | 估值区间 |
|:-----|-----:|:---------|
| 当前 ERP | **{current_erp:.2%}** | **{erp_zone}** |
| 历史均值 | {mean_erp:.2%} | {len(erp_series)}条样本 |

| 分位点 | ERP值（越高越便宜） | 价格估值状态 |
|:-------|-------------------:|:------------|
| P90 | {quantiles["P90"]:.2%} | 极度低估 |
| P75 | {quantiles["P75"]:.2%} | 显著低估 |
| P50 | {quantiles["P50"]:.2%} | 价值中枢 |
| P25 | {quantiles["P25"]:.2%} | 进入高估 |
| P10 | {quantiles["P10"]:.2%} | 极度高估 |
"""

    # ── 组装各模块 ────────────────────────────────────────────────────
    unified_block     = build_unified_valuation_block(df, code)
    trend_block       = build_trend_block(df, erp_series, code, quantiles)
    exit_block_final  = exit_block
    fundamental_block = build_fundamental_alert_block(code, name)
    etf_block         = build_etf_metrics_block(code, etf_df)
    shiller_block     = build_shiller_block(code)

    md = f"""{header_block}
---
### 仓位建议

**{b_msg}** ({b_pct}%)
**{v_msg}** ({v_pct}%)
**{t_msg}** ({t_pct}%)

建议总仓位：**{total_pct}%**（泡沫底仓 {b_pct}% + 价值主力 {v_pct}% + 投机奇兵 {t_pct}%）
{unified_block}{trend_block}{exit_block_final}{fundamental_block}{etf_block}{shiller_block}"""
    print(md)
    return md


# ══════════════════════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
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
        ("000069", "消费80"),
        ("930781", "中证影视"),
        ("000989", "全指可选"),
        ("931139", "CS消费50"),
        ("SPY",    "S&P 500"),
        ("QQQ",    "Nasdaq 100"),
        ("EWQ",    "MSCI France"),
        ("EWG",    "MSCI Germany"),
        ("EWJ",    "MSCI Japan"),
        ("EEM",    "MSCI Emerging"),
        ("HSTECH", "恒生科技指数"),
        ("399967", "中证军工"),
        ("931066", "军工龙头"),
        ("930794", "中美互联网"),
        ("931946", "畜牧养殖"),
        ("930598", "稀土产业"),
    ]

    summary_list = []
    report_list  = []
    for code, name in indices:
        report_md = analyze_and_suggest(code, name, _etf_df, summary_list)
        if report_md:
            report_list.append(report_md)

    _zone_order = {
        "🟢 极度低估": 0, "🟢 显著低估": 1, "🟡 合理偏低": 2,
        "🟠 合理区间": 3, "🔴 严重高估": 4, "🚨 危险泡沫": 5,
    }
    def _sort_key(r):
        zone_str  = r.get("erp_zone", "")
        zone_rank = next((v for k, v in _zone_order.items() if zone_str.startswith(k)), 99)
        win = r.get("win_rate", 0.0)
        win = win if win == win else 0.0
        return (zone_rank, -win)

    summary_list.sort(key=_sort_key)

    if report_list:
        date_str       = datetime.now().strftime("%Y-%m-%d")
        summary_html   = build_summary_block(summary_list, output_format="html")
        summary_wechat = build_summary_block(summary_list, output_format="markdown")

        full_report = (
            "# ERP 策略每日监控报告\n"
            + summary_html
            + LEGEND_BLOCK
            + "".join(report_list)
        )

        save_html_report(full_report, date_str)

        if os.getenv("DRY_RUN") == "true":
            preview_path = "./output_preview.md"
            with open(preview_path, "w", encoding="utf-8") as f:
                f.write(full_report)
            print(f"✅ dry-run 模式，报告已写入 {preview_path}，不推送微信。")
        else:
            print("正在生成报告并准备推送...")
            send_to_wechat(summary_wechat, date_str)
    else:
        print("❌ 未生成任何有效报告，请检查数据文件。")
