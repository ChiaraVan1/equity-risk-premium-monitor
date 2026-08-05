"""
erp_position.py
主入口：调度数据准备 → 循环分析 → 生成报告 → 推送
"""
import os
from datetime import datetime

import pandas as pd

from config_loader import HOLDING_CATEGORY, INDICES_LIST
from prepare_all_data import prepare_all_data, load_etf_price_series
from analysis.valuation import build_shiller_block, build_unified_valuation_block, calc_odds, is_holding
from analysis.risk import (compute_exit_signal_summary, build_exit_signal_block,
                            compute_profit_signal_summary, build_profit_signal_block,
                            compute_range_drawdown_rebound)
from analysis.trend import compute_erp_slope_signal, build_trend_block
from analysis.sentiment import build_sentiment_block
from analysis.utils import (check_metric_freshness, build_freshness_note, generate_action_sentence,
                             _format_win_odds, _format_range, safe_action_markers)
from report.markdown import build_summary_block, save_html_report, send_to_wechat
from analysis.dividend_yield_analysis import build_dividend_yield_block
from analysis.popularity_signal import build_popularity_block, compute_popularity_confirmation
from analysis.etf_quality import build_etf_quality_block


def compute_position_sizing(current_erp, quantiles):
    """三仓拆分：泡沫底仓 / 价值主力 / 投机奇兵，规则见 README「仓位框架」。
    返回 (bubble, value, spec, b_msg, v_msg, t_msg)：数值百分比 + 对应文字说明。
    【2026-08-02 恢复说明】b_msg/v_msg/t_msg 这三句文案之前被砍掉了，仪表盘只剩
    数字拆分（30+40+20这种），看不出每一档背后的判断依据。原样恢复。"""
    p25, p50, p75, p90 = quantiles['P25'], quantiles['P50'], quantiles['P75'], quantiles['P90']
    p95 = quantiles.get('P95', p90)

    if current_erp >= p50:
        b_msg, bubble = "泡沫仓: 已进入相对便宜击球区，30% 底仓应长期锁定", 30
    elif current_erp >= p25:
        b_msg, bubble = "泡沫仓: 尚未达到远期目标价，底仓持有不动", 30
    else:
        b_msg, bubble = "泡沫仓: 触发极致远期溢价，考虑收割最后的筹码", 5

    if current_erp >= p75:
        v_msg, value = "价值仓: 足够便宜的价格，40% 核心主力必须在场", 40
    elif current_erp >= p50:
        v_msg, value = "价值仓: 估值修复中，建议持有 30%-40% 主力仓位", 35
    elif current_erp >= p25:
        v_msg, value = "价值仓: 回到合理估值区间，开始减持主力仓位", 10
    else:
        v_msg, value = "价值仓: 估值已高，价值段位应已全部离场", 0

    if current_erp >= p95:
        t_msg, spec = "投机仓: 触发极端惯性下跌，30% 预备队全额出击", 30
    elif current_erp >= p90:
        t_msg, spec = "投机仓: 极低估区，保持 20% 仓位积极做T降本", 20
    elif current_erp >= p50:
        t_msg, spec = "投机仓: 震荡区间，维持 10% 灵活部做T", 10
    else:
        t_msg, spec = "投机仓: 溢价区基本只卖不买，缩减至 5% 观察", 5

    return bubble, value, spec, b_msg, v_msg, t_msg


def build_position_block(bubble, value, spec, b_msg, v_msg, t_msg, quantiles,
                          exit_level=0, profit_level=0, exit_icon="─", profit_icon="─"):
    """仓位建议区块。触发减仓/止盈信号时不展示常规3+4+3拆分，改为提示已进入对应流程
    （避免"回撤已破防但仪表盘还在建议加仓"这种自相矛盾的观感）。估值分档表格随仓位
    建议一起展示（分档是仓位拆分的依据），不再单独出现在核心估值决策模块里。"""
    zone_table = f"""
| 估值分档 | ERP阈值 |
|:--------|--------:|
| P90 极度低估 | > {quantiles['P90']:.2%} |
| P75 显著低估 | > {quantiles['P75']:.2%} |
| P50 中位 | > {quantiles['P50']:.2%} |
| P25 高估 | > {quantiles['P25']:.2%} |
| P10 泡沫 | ≤ {quantiles['P10']:.2%} |
"""
    if exit_level > 0:
        return f"""
---
### 仓位建议

{exit_icon} 已触发减仓/清仓预警（详见下方「减仓 / 清仓信号」模块），暂不展示常规仓位建议（3+4+3拆分）。
低估位置参考：P75 = {quantiles['P75']:.2%}（显著低估） / P90 = {quantiles['P90']:.2%}（极度低估）
{zone_table}"""
    elif profit_level > 0:
        return f"""
---
### 仓位建议

{profit_icon} 已触发止盈预警（详见下方「止盈信号」模块），暂不展示常规仓位建议（3+4+3拆分）。
高估位置参考：P25 = {quantiles['P25']:.2%}（进入高估） / P10 = {quantiles['P10']:.2%}（极度高估）
{zone_table}"""
    else:
        return f"""
---
### 仓位建议

**{b_msg}** ({bubble}%)
**{v_msg}** ({value}%)
**{t_msg}** ({spec}%)
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
    bubble, value, spec, b_msg, v_msg, t_msg = compute_position_sizing(current_erp, quantiles)
    total_pct = bubble + value + spec

    # 加载价格序列
    price_series = load_etf_price_series(code)

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
    header_block = f"## {name}（{code}）\n{freshness_note}"

    shiller_block = build_shiller_block(code, (
        prepared_data.get("shiller_grouped"),
        prepared_data.get("shiller_valid"),
        prepared_data.get("shiller_cape_now"),
    ))

    unified_block = build_unified_valuation_block(df, code, val_series=erp_series, win_rate=win_rate, odds_ratio=odds_ratio)

    trend_block = build_trend_block(df, name, erp_series, code, quantiles, ps_df=prepared_data.get("ps_data"))

    exit_summary = compute_exit_signal_summary(code, win_rate, price_series, holding)
    exit_block = build_exit_signal_block(code, win_rate, price_series, holding)

    # 止盈信号仅对持仓有意义，未持仓无仓位可止盈，维持跳过（拆分前原版逻辑，2026-08-03恢复）。
    if holding:
        profit_summary = compute_profit_signal_summary(code, win_rate, price_series)
        profit_block = build_profit_signal_block(code, win_rate, price_series)
    else:
        profit_summary = {"level": 0, "verdict_icon": "─", "message": ""}
        profit_block = ""

    # 【2026-08-02 恢复】仓位建议区块（三仓文案 + 触发止损/止盈时的override提示），
    # 之前完全没有生成，仪表盘/详情页都看不到"为什么是这个仓位比例"的文字说明。
    position_block = build_position_block(
        bubble, value, spec, b_msg, v_msg, t_msg, quantiles,
        exit_level=exit_summary.get("level", 0), profit_level=profit_summary.get("level", 0),
        exit_icon=exit_summary.get("verdict_icon", "─"), profit_icon=profit_summary.get("verdict_icon", "─"),
    )

    range_stats = compute_range_drawdown_rebound(code, price_series, lookback=120) or {}

    popularity_block = build_sentiment_block(code, name, code, win_rate, prepared_data.get("news_df"))

    dividend_block = build_dividend_yield_block(code)

    etf_df = prepared_data.get("etf_df")
    etf_block = build_etf_quality_block(code, etf_df)

    md = f"""{header_block}{position_block}{unified_block}{trend_block}{exit_block}{profit_block}{popularity_block}{dividend_block}{etf_block}{shiller_block}"""

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
            "position": {"bubble": bubble, "value": value, "spec": spec, "total": total_pct},
            "exit_level": exit_summary.get("level", 0),
            "exit_icon": exit_summary.get("verdict_icon", "─"),
            "exit_message": exit_summary.get("message", ""),
            # 【新增】止盈信号此前只出现在详情页，从未进入仪表盘「🚨 需要处理」判定。
            "profit_level": profit_summary.get("level", 0),
            "profit_icon": profit_summary.get("verdict_icon", "─"),
            "profit_message": profit_summary.get("message", ""),
            "range_str": _format_range(range_stats) if range_stats else "─",
            "vol_icon": markers["vol_icon"],
            "premium_icon": markers["premium_icon"],
            "divergence_flag": markers["divergence_flag"],
            "action_sentence": action_sentence,
            "stale_flag": stale_flag,
            "stale_note": freshness_note,
        })

    return md


if __name__ == "__main__":
    # 第一阶段：准备所有数据
    prepared_data = prepare_all_data()

    # 第二阶段：分析和生成报告
    indices = INDICES_LIST
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
        full_md = summary_html + "\n" + "\n".join(report_list)

        # docs/report.html 照常本地生成——即使是预览模式也生成没关系，
        # 因为 gh-pages 部署那一步在 workflow 里已经用
        # `if: github.event.inputs.dry_run != 'true'` 挡住了，不会真的发布出去。
        save_html_report(full_md, date_str)

        report_url = "https://chiaravan1.github.io/equity-risk-premium-monitor/report.html"
        summary_md = build_summary_block(summary_list, output_format="markdown")
        wechat_md = f"{summary_md}\n\n📄 [查看完整报告]({report_url})"

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
