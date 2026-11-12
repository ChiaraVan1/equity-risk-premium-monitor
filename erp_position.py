"""
erp_position.py
主入口：调度数据准备 → 循环分析 → 生成报告 → 推送
"""
import os
from datetime import datetime

import pandas as pd

from config_loader import ALL_INDICES, HOLDING_CATEGORY, INDICES_LIST

# 个股名单（source=baidu 即个股）：它们的动作依据是分红口径，不是 ERP 分位
_STOCK_CODES = {str(i["code"]) for i in ALL_INDICES if i.get("source") == "baidu"}
from prepare_all_data import (
    prepare_all_data,
    load_etf_adjusted_price_series,
    load_etf_price_series,
    MAX_PRICE_LAG_TRADING_DAYS,
    price_lag_trading_days,
    get_etf_price_last_date,
)
from analysis.valuation import build_shiller_block, build_unified_valuation_block, calc_odds, is_holding
from analysis.risk import compute_range_drawdown_rebound
from analysis.trend import compute_erp_slope_signal, build_trend_block
from analysis.sentiment import build_sentiment_block
from analysis.utils import (check_metric_freshness, build_freshness_note, generate_action_sentence,
                             _format_win_odds, _format_range, safe_action_markers)
from report.markdown import build_summary_block, save_html_report, send_to_wechat
from report.erp_chart import build_erp_chart
from fetch.freshness import check_freshness_report_health
from analysis.dividend_yield_analysis import build_dividend_yield_block
from analysis.etf_quality import build_etf_quality_block
from analysis.ma_position import build_ma_position_block
from grid.grid_plan import build_grid_block


def describe_zone(win_rate, is_stock=False):
    """按 ERP 分位给档位与动作（唯一口径）。返回 (档位名, 动作文案, 分位百分数)。

    四档：>90 极便宜 / 60–90 便宜 / 25–60 中性 / <25 贵。
    不论哪一档，单次都只买一档，不重仓。
    【2026-11-11】个股接入后单档定额按标的类型分流：指数/ETF 3,000，个股 5,000
    （投资规则口径）。此前一律写 3,000，个股会拿到偏小的金额。
    """
    amount = "5,000" if is_stock else "3,000"
    if win_rate is None or (isinstance(win_rate, float) and win_rate != win_rate):
        return "数据不足", "─", 0.0
    pct = win_rate * 100
    if win_rate > 0.90:
        return "极便宜", f"可买一档 {amount} 元", pct
    if win_rate >= 0.60:
        return "便宜", f"可买一档 {amount} 元", pct
    if win_rate >= 0.25:
        return "中性", "只持有，不加", pct
    return "贵", "不加，考虑减一档", pct


_PORTFOLIO_PATH = "./data/portfolio_total.csv"
_PORTFOLIO_CACHE = None
_CAP_RATIO = 0.06


def _load_portfolio():
    """读 data/portfolio_total.csv → {key: {name, mv, asof}}，key=TOTAL 是全账户总资产。"""
    global _PORTFOLIO_CACHE
    if _PORTFOLIO_CACHE is None:
        _PORTFOLIO_CACHE = {}
        if os.path.exists(_PORTFOLIO_PATH):
            try:
                import csv as _csv
                with open(_PORTFOLIO_PATH, encoding="utf-8") as f:
                    for r in _csv.DictReader(f):
                        _PORTFOLIO_CACHE[r["key"]] = {
                            "name": r["name"],
                            "mv": float(r["market_value"]),
                            "asof": r["asof"],
                        }
            except Exception:
                _PORTFOLIO_CACHE = {}
    return _PORTFOLIO_CACHE


def _load_etf_map():
    """config 里存的是指数代码（000300），持仓里是 ETF 代码（510300）。
    grid_state.csv 的 etf_code 列就是现成的对照表；不读它，10 只 ETF 的 6% 检查会整片漏掉。
    """
    m = {}
    path = "./data/grid_state.csv"
    if not os.path.exists(path):
        return m
    try:
        import csv as _csv
        with open(path, encoding="utf-8-sig") as f:
            for r in _csv.DictReader(f):
                etf = (r.get("etf_code") or "").strip()
                if etf:
                    m[str(r.get("code", "")).strip()] = etf
    except Exception:
        pass
    return m


def build_cap_block(code):
    """单只 ≤6% 检查。分母＝全账户总资产，来自月度复盘定稿的解析（tools/portfolio_snapshot.py）。

    以前只有一句死文案「该只市值不超过总资产 6%」，分母从没进过系统，所以谁都查不了。
    现在把分母和该只市值都接上，直接给「还能加多少 / 已经超了多少」。
    市值基准月更，超过 45 天会标一句偏旧提示。
    """
    pf = _load_portfolio()
    total = pf.get("TOTAL", {}).get("mv")
    if not total:
        return "\n> **6% 上限检查**：缺总资产基准，先在本地跑 `python3 tools/portfolio_snapshot.py` 并同步 Secret\n"
    asof = pf.get("TOTAL", {}).get("asof", "")
    cap = total * _CAP_RATIO
    me = pf.get(str(code))
    # 指数代码查不到时，退回它对应的 ETF 代码（000300 → 510300），否则 ETF 持仓整片漏检
    lookup_code = str(code)
    if not me:
        etf = _load_etf_map().get(str(code))
        if etf and pf.get(etf):
            me = pf.get(etf)
            lookup_code = etf
    if not me:
        return ""
    mv = me["mv"]
    pct = mv / total
    gap = cap - mv
    days = None
    try:
        days = (datetime.now() - datetime.strptime(asof, "%Y-%m-%d")).days
    except Exception:
        pass
    stale = f"，基准偏旧 {days} 天" if days and days > 45 else ""
    if gap >= 0:
        line = f"6% 上限 {cap:,.0f} 元 · 当前 {mv:,.0f} 元（{pct:.2%}）→ **还可加 {gap:,.0f} 元**"
    else:
        line = f"6% 上限 {cap:,.0f} 元 · 当前 {mv:,.0f} 元（{pct:.2%}）→ **已超限 {-gap:,.0f} 元，不再加**"
    return f"\n**仓位上限检查**：{line}\n> 分母＝全账户 {total:,.0f} 元（市值基准 {asof}{stale}）\n"


_STOCK_DIV_PATH = "./data/stock_dividend.csv"
_STOCK_DIV_CACHE = None


def _load_stock_div():
    """读 data/stock_dividend.csv → {code: 每股派息TTM}。"""
    global _STOCK_DIV_CACHE
    if _STOCK_DIV_CACHE is None:
        _STOCK_DIV_CACHE = {}
        if os.path.exists(_STOCK_DIV_PATH):
            try:
                df = pd.read_csv(_STOCK_DIV_PATH, dtype={"code": str})
                for _, r in df.iterrows():
                    _STOCK_DIV_CACHE[str(r["code"])] = float(r["dps_ttm"])
            except Exception:
                _STOCK_DIV_CACHE = {}
    return _STOCK_DIV_CACHE


def build_dividend_value_block(code, price, bond_yield, pe=None):
    """个股「分红口径」区块：股息率 = 每股派息TTM / 现价，再与十年国债比利差。

    【2026-11-11】个股不用 ERP 分位做动作依据（周期股盈利崩塌时 PE 会虚高，
    分位会长期卡在"贵"）。改按用户的分红价值法看「股息率 − 国债」。
    ★ 阈值暂定、未经回测：≥3 个点便宜 / 1–3 中性 / <1 偏贵。要改先回测再动。
    """
    dps = _load_stock_div().get(str(code))
    if not dps or not price or bond_yield is None:
        return ""
    dy = dps / price
    # 判定与显示绑定同一个值（保留 2 位），否则会出现「显示 +3.00 却判中性」的自相矛盾
    spread = round((dy - bond_yield) * 100, 2)
    if spread >= 3:
        verdict = "便宜（可买一档）"
    elif spread >= 1:
        verdict = "中性（只持有）"
    else:
        verdict = "偏贵（考虑减一档）"
    pe_line = f"| PE(TTM) | {pe:.2f} |\n" if pe else ""
    return f"""
<details>
<summary>分红口径（个股看这个，ERP 分位不作动作依据）</summary>

| 指标 | 数值 |
|:--|--:|
{pe_line}| 每股派息TTM | {dps:.4f} 元 |
| 股息率 | {dy:.2%} |
| 十年国债 | {bond_yield:.2%} |
| **股息率 − 国债** | **{spread:+.2f} 个点** |

**分红口径判定：{verdict}**
> 阈值暂定（≥3 便宜 / 1–3 中性 / <1 偏贵），未经回测验证。

</details>
"""


def build_reduce_alert_block(summary_list):
    """ERP 分位跌破 25% 的持仓集中提示，放在日报最前面。

    只列命中的标的；一只都没有时返回空串，不占版面。
    这不是新规则，只是把原本埋在"仓位建议"表格里的一行文案提到顶部。
    """
    hits = []
    for s in summary_list:
        try:
            pct = float(s.get("position", {}).get("total", 100))
        except (TypeError, ValueError):
            continue
        # 【2026-11-11】个股排除在外：它们的动作依据是分红口径（股息率 − 国债），
        # ERP 分位对周期股会长期卡在"贵"，列进来会和个股区块里的结论直接打架。
        if str(s.get("code", "")) in _STOCK_CODES:
            continue
        if s.get("holding") and pct < 25:
            hits.append(s)
    if not hits:
        return ""
    lines = ["### ⚠️ ERP 分位进入高估区，可考虑减一档\n"]
    for h in hits:
        erp_val = h.get("erp")
        erp_txt = f" · ERP {erp_val:.2%}" if isinstance(erp_val, (int, float)) else ""
        lines.append(
            f"- **{h.get('name', '')}**（{h.get('code', '')}）分位 {h.get('zone_pct', '')}{erp_txt} → 贵，考虑减一档"
        )
    return "\n".join(lines) + "\n\n"


def build_position_block(tier, action_text, zone_pct, quantiles, current_erp=None):
    """仓位建议区块：按 ERP 分位给档位与动作（唯一口径）。
    三仓拆分（泡沫/价值/投机）与减仓/止盈 override 已于 2026-11-10 停用。
    估值分档表格随仓位建议一起展示，折叠呈现，只留结论文字。"""
    erp_line = f"**当前 ERP：{current_erp:.2%}**\n" if current_erp is not None else ""
    zone_table = f"""
<details>
<summary>查看估值分档表格</summary>

| 估值分档 | ERP阈值 |
|:--------|--------:|
| P90 极度低估 | > {quantiles['P90']:.2%} |
| P75 显著低估 | > {quantiles['P75']:.2%} |
| P50 中位 | > {quantiles['P50']:.2%} |
| P25 高估 | > {quantiles['P25']:.2%} |
| P10 泡沫 | ≤ {quantiles['P10']:.2%} |

</details>
"""
    return f"""
---
### 仓位建议

{erp_line}**分位 {zone_pct:.0f}% → {tier}**
**动作：{action_text}**
{zone_table}"""


def analyze_and_suggest(code, name, prepared_data, summary_list=None):
    """
    核心分析函数：为单个指数生成完整分析和建议。
    """
    erp_file = f"./data/erp_{code}.csv"
    if not os.path.exists(erp_file):
        return None

    df = pd.read_csv(erp_file)
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.sort_values('Date')

    # 【2026-08-02 修复】.dropna() 之前缺失：早期历史（如000300数据里2005年那段
    # PE数据源尚未覆盖）留下大量空值行，(erp_series < current_erp).mean() 的分母
    # 会把这些空值行也算进去，导致胜率/历史分位被系统性拉低。
    # quantile() 本身会自动跳过NaN所以P10/P25/P50/P75/P90不受影响，只有这里的
    # 布尔均值计算受影响——这也是为什么此前"胜率"和"ERP历史分位"两个数字一起错，
    # 但估值分档阈值本身是对的。
    erp_series = df.set_index('Date')['ERP'].dropna()

    # 【2026-08-02 恢复】欧美日锚定区间：SPY/QQQ/EWQ/EWG/EWJ 的早期历史PE
    # 是用"今日值 × 与SPY的比值"反推估算出来的（不是真实数据），如果让分位/
    # 胜率计算把这段估算历史也算进去，会得到失真的分位。原版做法是：这几个
    # 标的只用 2022-01-01 之后的真实数据段作为分位锚（样本量不足30条时退回
    # 全量，避免样本太少反而更不可靠）。
    if code in ('EWQ', 'EWG', 'EWJ', 'SPY', 'QQQ'):
        anchor = df[df['Date'] >= pd.Timestamp('2022-01-01')].set_index('Date')['ERP'].dropna()
        if len(anchor) >= 30:
            erp_series = anchor

    # 【2026-08-04】HSTECH 专用估值口径：港股科技盈利波动大，PE长期失真且
    # HS_TECH_PE_TODAY 需人工手填，长期无人维护导致PE被ffill冻结近一个月，
    # 静默产出失真的"极度低估"信号。HSTECH 改用 prepare_all_data.py 里
    # 已经在维护的 PS/PSY 口径（psy = 1/PS - 国债收益率，与ERP同构，"越高越
    # 便宜"），彻底不再依赖erp_HSTECH.csv里的PE/ERP列。
    if code == 'HSTECH':
        ps_data = prepared_data.get("ps_data")
        if ps_data is not None and 'psy' in ps_data.columns:
            hstech_psy = ps_data['psy'].dropna()
            if len(hstech_psy) > 0:
                erp_series = hstech_psy

    current_erp = erp_series.iloc[-1]
    quantiles = {
        'P10': erp_series.quantile(0.10),
        'P25': erp_series.quantile(0.25),
        'P50': erp_series.quantile(0.50),
        'P75': erp_series.quantile(0.75),
        'P90': erp_series.quantile(0.90),
        'P95': erp_series.quantile(0.95),
    }

    # 计算胜率、赔率
    win_rate = (erp_series < current_erp).mean()
    odds_ratio = calc_odds(current_erp, erp_series)

    # 估值分档
    if current_erp >= quantiles['P90']:
        erp_zone = "🟢 极度低估"
    elif current_erp >= quantiles['P75']:
        erp_zone = "🟢 显著低估"
    elif current_erp >= quantiles['P50']:
        erp_zone = "🟡 合理偏低"
    elif current_erp >= quantiles['P25']:
        erp_zone = "🟠 合理区间"
    else:
        erp_zone = "🔴 高估/规避"

    # 三仓拆分（仓位建议区块 + 仪表盘共用同一份计算结果，避免重复算两次）
    is_stock = str(code) in _STOCK_CODES
    tier, action_text, zone_pct_num = describe_zone(win_rate, is_stock=is_stock)
    # 【2026-11-11】个股的动作不以 ERP 分位为准：周期股盈利崩塌时 PE 虚高、分位长期卡在"贵"，
    # 与分红口径（股息率 − 国债）会给出相反结论。这里统一改成指向分红口径，避免同页自相矛盾。
    if is_stock:
        action_text = "见下方分红口径（ERP 分位仅参考）"

    # 【2026-11-09】价格源按"新鲜度"选，不再按"文件在不在"选。
    # 旧逻辑：只要 etf_price_adj.csv 里有这只标的就用前复权 —— 于是内容停在
    # 2026-09-23 的前复权价永远赢过更新到当日的新价格，回撤/均线/止损全部基于旧价。
    # 新逻辑：前复权价滞后超过 MAX_PRICE_LAG_TRADING_DAYS 个交易日就强制回退到
    # 未复权价，并在报告里显式标出回退原因；价格数据日期与生成时间并列展示。
    raw_price_series = load_etf_price_series(code)
    adj_price_series = load_etf_adjusted_price_series(code)

    price_series = None
    price_source_note = ""
    price_source_label = "无价格数据"
    price_end = None
    price_lag = None
    adj_end = pd.Timestamp(adj_price_series.index[-1]) if adj_price_series is not None else None
    adj_lag = price_lag_trading_days(adj_end) if adj_end is not None else None

    if adj_price_series is not None and (adj_lag is None or adj_lag <= MAX_PRICE_LAG_TRADING_DAYS):
        price_series = adj_price_series
        price_end, price_lag = adj_end, adj_lag
        price_source_label = "前复权"
    elif raw_price_series is not None:
        price_series = raw_price_series
        price_end = pd.Timestamp(raw_price_series.index[-1])
        price_lag = price_lag_trading_days(price_end)
        price_source_label = "未复权"
        if adj_price_series is not None:
            price_source_note = (
                f"\n> ⚠️ 前复权价已过期：最新只到 {adj_end:%Y-%m-%d}（滞后 {adj_lag} 个交易日，"
                f"阈值 {MAX_PRICE_LAG_TRADING_DAYS}），已回退到未复权 ETF 收盘价。"
                f"分红或份额折算附近的回撤、均线及止盈止损信号可能失真。\n"
            )
        else:
            price_source_note = (
                "\n> ⚠️ 价格风险信号暂用未复权 ETF 收盘价；分红或份额折算附近的"
                "回撤、均线及止盈止损信号可能失真。\n"
            )

    # 硬闸门：价格数据年龄超过阈值时，任何减仓/止盈/清仓信号都不许输出。
    # 宁可没有建议，也不能基于一个多月前的价格给出"强制全清"。
    price_gate = (price_end is not None and price_lag is not None
                  and price_lag > MAX_PRICE_LAG_TRADING_DAYS)

    if price_end is not None:
        price_meta_line = (
            f"> 📅 价格数据日期：**{price_end:%Y-%m-%d}**（{price_source_label}，"
            f"滞后 {price_lag if price_lag is not None else '?'} 个交易日）\n\n"
        )
    else:
        price_meta_line = "> 📅 价格数据日期：**无**（未找到价格文件）\n\n"

    # 【2026-08-02 恢复】PE/PSY 数据新鲜度校验——check_metric_freshness()/
    # build_freshness_note() 之前只在 import 列表里，从没被实际调用过，导致
    # 「🕓 数据新鲜度预警」这个仪表盘板块形同虚设（stale_flag 永远是默认值，
    # 永远不会触发）。这个校验专门抓"抓取失败被前值填充掩盖"或"QQQ/HSTECH
    # 手动填值忘记更新"这类问题——数值本身没报错，但连续多个更新点纹丝不动。
    ps_data = prepared_data.get("ps_data")
    if code == "HSTECH":
        freshness_metric_name = "PS"
        if ps_data is not None and "ps" in ps_data.columns:
            freshness = check_metric_freshness(ps_data["ps"])
        else:
            freshness = {"is_stale": False, "unchanged_count": 0, "last_value": None,
                         "last_date": None, "first_unchanged_date": None}
    else:
        freshness_metric_name = "PE"
        freshness = check_metric_freshness(df.set_index('Date')['PE'])
    freshness_note = build_freshness_note(freshness, freshness_metric_name)
    stale_flag = "⚠️" if freshness["is_stale"] else "─"

    # 生成各类分析块
    holding = is_holding(code)
    header_block = f"## {name}（{code}）\n{price_meta_line}{freshness_note}{price_source_note}"

    shiller_block = build_shiller_block(code, (
        prepared_data.get("shiller_grouped"),
        prepared_data.get("shiller_valid"),
        prepared_data.get("shiller_cape_now"),
    ))

    unified_block = build_unified_valuation_block(df, code, val_series=erp_series, win_rate=win_rate, odds_ratio=odds_ratio)

    trend_block = build_trend_block(df, name, erp_series, code, quantiles, ps_df=prepared_data.get("ps_data"))

    # 【2026-11-09 硬闸门】价格数据过期时，回撤/均线/止盈/止损一律不输出。
    # 这一段必须在 build_exit_signal_block 之前短路，否则旧价会被算成"🚨 强制全清"。
    if price_gate:
        gate_msg = (
            f"⛔ 数据已过期，信号暂停 — 价格数据止于 {price_end:%Y-%m-%d}，"
            f"滞后 {price_lag} 个交易日（阈值 {MAX_PRICE_LAG_TRADING_DAYS} 个交易日）。"
            f"回撤、均线、减仓/止盈/强制清仓信号全部基于旧价，已停止输出。"
        )
        exit_summary = {"level": 0, "verdict_icon": "─", "message": ""}
        profit_summary = {"level": 0, "verdict_icon": "─", "message": ""}
        exit_block = ""
        profit_block = ""
        range_stats = None
        position_block = build_position_block(
            tier, action_text, zone_pct_num, quantiles,
            current_erp=current_erp,
        )
    else:
        # 2026-11-10 起：价格止损（回撤+均线）与止盈信号不再参与决策，两个模块不再生成。
        exit_summary = {"level": 0, "verdict_icon": "─", "message": ""}
        profit_summary = {"level": 0, "verdict_icon": "─", "message": ""}
        exit_block = ""
        profit_block = ""

        # 【2026-08-02 恢复】仓位建议区块（三仓文案 + 触发止损/止盈时的override提示），
        # 之前完全没有生成，仪表盘/详情页都看不到"为什么是这个仓位比例"的文字说明。
        position_block = build_position_block(
            tier, action_text, zone_pct_num, quantiles,
            current_erp=current_erp,
        )

        range_stats = compute_range_drawdown_rebound(code, price_series, lookback=120) or {}

    sentiment_block = build_sentiment_block(code, name, code, win_rate, prepared_data.get("news_df"))

    dividend_block = build_dividend_yield_block(code)

    etf_df = prepared_data.get("etf_df")
    etf_block = build_etf_quality_block(code, etf_df)

    # 【新增】均线位置块：MA20 只回答"这个价能不能下手"，放在执行层（ETF 执行质量之前），
    # 不再埋在"减仓/止盈"明细表里。失败返回空串，不影响主报告。
    ma_position_block = build_ma_position_block(code, price_series, holding)

    # 网格档位：固定成本价 + 等差 5%，只给持有标的。成本数据缺失时返回空串。
    grid_block = build_grid_block(code)

    # 个股「分红口径」区块：股息率 − 国债利差。只对个股（source=baidu）生成。
    dividend_value_block = ""
    if is_stock:
        try:
            _bond_last = df.dropna(subset=['Bond_Yield_10Y'])['Bond_Yield_10Y'].iloc[-1]
            _pe = float(df['PE'].dropna().iloc[-1]) if df['PE'].notna().any() else None
            _px = float(price_series.iloc[-1]) if price_series is not None and len(price_series) else None
            dividend_value_block = build_dividend_value_block(
                code, _px, float(_bond_last), _pe
            )
        except Exception:
            dividend_value_block = ""

    # 单只 6% 仓位上限检查：分母＝全账户总资产（月度复盘解析），分子＝该只市值
    cap_block = build_cap_block(code) if holding else ""

    if holding:
        md = f"""{header_block}{position_block}{cap_block}{grid_block}{dividend_value_block}{unified_block}{trend_block}{sentiment_block}{dividend_block}{ma_position_block}{etf_block}{shiller_block}"""
    else:
        # 观察池：不逐只展开模块，只保留一行估值摘要，降低报告长度
        md = (f"{header_block}\n\n"
              f"**观察池 · 分位 {zone_pct_num:.0f}% · ERP {current_erp:.2%} → {tier}（{action_text}）**\n")

    # 汇总信息用于仪表盘
    if summary_list is not None:
        markers = safe_action_markers(etf_df)

        # 【2026-08-02 修复】generate_action_sentence() 已恢复为接收图标字符串的原版实现
        # （分批建仓/一次建仓/规避不建仓这套文案），不再是数值字典。
        divergence_icon = "⚠️" if markers["divergence_flag"] else "─"
        action_sentence = generate_action_sentence(
            markers["premium_icon"],
            divergence_icon,
            markers["vol_icon"],
            erp_zone,
        )

        summary_list.append({
            "code": code,
            "name": name,
            "erp_zone": erp_zone,
            "erp": current_erp,
            "win_rate": win_rate,
            "odds_ratio": odds_ratio,
            "win_odds_str": _format_win_odds({"win_rate": win_rate, "odds_ratio": odds_ratio}),
            "holding": holding,
            "position": {"total": zone_pct_num},
            "tier": tier,
            "zone_pct": f"{zone_pct_num:.0f}%",
            "exit_level": exit_summary.get("level", 0),
            "exit_icon": exit_summary.get("verdict_icon", "─"),
            "exit_message": exit_summary.get("message", ""),
            # 【新增】止盈信号此前只出现在详情页，从未进入仪表盘「🚨 需要处理」判定。
            "profit_level": profit_summary.get("level", 0),
            "profit_icon": profit_summary.get("verdict_icon", "─"),
            "profit_message": profit_summary.get("message", ""),
            "range_str": ("⛔ 价格过期" if price_gate else
                          (_format_range(range_stats) if range_stats else "─")),
            "price_date": f"{price_end:%Y-%m-%d}" if price_end is not None else "无",
            "price_gate": price_gate,
            "vol_icon": markers["vol_icon"],
            "premium_icon": markers["premium_icon"],
            "divergence_flag": markers["divergence_flag"],
            "action_sentence": action_sentence,
            "stale_flag": stale_flag,
            "stale_note": freshness_note,
        })

    return md


def build_price_meta_summary() -> str:
    """报告头用：未复权价与前后复权价的最新日期各自可见。

    【2026-11-09】此前报告只有"生成时间"，价格数据日期被隐掉——那正是"报告写
    11/07、价格其实是 9/23"这类问题的遮羞布。
    """
    raw_last = get_etf_price_last_date()
    adj_last = None
    try:
        idx = pd.read_csv("./data/etf_price_adj.csv", index_col=0, parse_dates=True).index
        adj_last = pd.Timestamp(idx.max()).normalize() if len(idx) else None
    except Exception:
        adj_last = None

    if raw_last is None:
        return "无价格文件"
    if adj_last is None:
        return f"{raw_last:%Y-%m-%d}（未复权）；前复权文件缺失"
    lag = price_lag_trading_days(adj_last)
    if lag is not None and lag > MAX_PRICE_LAG_TRADING_DAYS:
        return (f"{raw_last:%Y-%m-%d}（未复权，信号已回退到此）｜前复权 {adj_last:%Y-%m-%d} "
                f"⛔ 滞后 {lag} 个交易日，已停用")
    return f"{raw_last:%Y-%m-%d}（未复权）｜前复权 {adj_last:%Y-%m-%d}（滞后 {lag} 个交易日）"


if __name__ == "__main__":
    # 第一阶段：准备所有数据
    prepared_data = prepare_all_data()

    # 第二阶段：分析和生成报告
    # 持仓标的优先展示：先按是否持仓排序，同为持仓/非持仓内部保持原有顺序（stable sort）
    indices = sorted(INDICES_LIST, key=lambda x: not HOLDING_CATEGORY.get(x[0], False))
    summary_list = []
    report_list = []

    for code, name in indices:
        report_md = analyze_and_suggest(code, name, prepared_data, summary_list)
        if report_md:
            report_list.append(report_md)

    # 第三阶段：生成 HTML 报告并推送
    if report_list:
        date_str = datetime.now().strftime("%Y-%m-%d")
        summary_html = build_summary_block(summary_list, output_format="html")
        full_md = "\n".join(report_list)

        # ERP 分位 <25% 的持仓集中提示：原本只在"仓位建议"表格里占一行，
        # 用户要翻表才看得见，这里提到报告最前面。
        reduce_alert = build_reduce_alert_block(summary_list)
        if reduce_alert:
            full_md = reduce_alert + full_md
            print(f"\n⚠️ 有 {reduce_alert.count(chr(10) + '- ')} 只持仓 ERP 分位低于 25%")
        price_meta = build_price_meta_summary()

        # 【2026-11-09】守卫自己也要被守卫：freshness 报告太久没更新，
        # 说明整条校验链没在跑，此时报告里的 fresh 判定全部不可信。
        health = check_freshness_report_health()
        guard_warn = ""
        if health.get("level") == "error":
            guard_warn = f"> ⚠️【守卫自检】{health.get('reason')}\n\n"
            print(f"\n⚠️ 守卫自检异常：{health.get('reason')}")
            full_md = guard_warn + full_md
        else:
            print(f"\n✅ 守卫自检正常（freshness_report 更新于 {health.get('age_hours')} 小时前）")

        # docs/report.html 照常本地生成——即使是预览模式也生成没关系，
        # 因为 gh-pages 部署那一步在 workflow 里已经用
        # `if: github.event.inputs.dry_run != 'true'` 挡住了，不会真的发布出去。
        save_html_report(full_md, date_str, price_meta)

        # 生成 ERP 概览条形图到 docs/erp_chart.png（gh-pages 部署后获得稳定 URL）。
        # Server酱推送发生在 gh-pages 部署之前，微信正文里当天的图 URL 指向的是
        # 前一天部署的图（滞后一天）。文字部分（分位、档位、买卖点）都是当天数据，
        # 只有这张概览图晚一天，已评估并接受，不做改动。
        erp_chart_url = build_erp_chart()

        report_url = "https://chiaravan1.github.io/equity-risk-premium-monitor/report.html"
        summary_md = build_summary_block(summary_list, output_format="markdown")
        wechat_md = summary_md
        if erp_chart_url:
            wechat_md = f"![各标的ERP概览]({erp_chart_url})\n\n{wechat_md}"
        wechat_md = f"{wechat_md}\n\n📄 [查看完整报告]({report_url})"
        if guard_warn:
            wechat_md = f"{guard_warn}{wechat_md}"
        wechat_md = f"> 📅 价格数据日期：{price_meta}\n\n{wechat_md}"
        if reduce_alert:
            wechat_md = reduce_alert + wechat_md

        dry_run = os.getenv("DRY_RUN", "").strip().lower() == "true"
        if dry_run:
            # 预览模式：不推真实微信，而是把完整 markdown 写到
            # output_preview.md，配合 workflow 里的 actions/upload-artifact
            # 步骤（path: output_preview.md）下载查看，方便验证改动对不对
            # 再决定要不要真正跑一次正式（非 dry_run）流程。
            with open("output_preview.md", "w", encoding="utf-8") as f:
                f.write(full_md)
            print(f"\n🔎 预览模式（DRY_RUN=true）：已生成 output_preview.md，未推送微信")
        else:
            send_to_wechat(wechat_md, date_str)
            print(f"\n✅ {date_str} 报告已生成并推送")
    else:
        print("\n⚠️ 未生成任何报告")
