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
# 注：compute_profit_signal_summary 之前已在导入列表里，但主流程从未实际调用它，
# 导致止盈信号完全没有进入仪表盘的「🚨 需要处理」判定——只有详情页里的
# build_profit_signal_block() 在用。下面 analyze_and_suggest() 里补上调用。
from analysis.trend import compute_erp_slope_signal, build_trend_block
from analysis.sentiment import build_sentiment_block
from analysis.utils import (check_metric_freshness, build_freshness_note, generate_action_sentence,
                             _format_win_odds, _format_range, safe_action_markers)
from report.markdown import build_summary_block, save_html_report, send_to_wechat, LEGEND_BLOCK
from analysis.dividend_yield_analysis import build_dividend_yield_block
from analysis.popularity_signal import build_popularity_block, compute_popularity_confirmation
from analysis.etf_quality import build_etf_quality_block


def compute_position_sizing(current_erp, quantiles):
    """三仓拆分：泡沫底仓 / 价值主力 / 投机奇兵，规则见 README「仓位框架」。"""
    p25, p50, p75, p90 = quantiles['P25'], quantiles['P50'], quantiles['P75'], quantiles['P90']
    p95 = quantiles.get('P95', p90)

    bubble = 30 if current_erp >= p25 else 5

    if current_erp >= p75:
        value = 40
    elif current_erp >= p50:
        value = 35
    elif current_erp >= p25:
        value = 10
    else:
        value = 0

    if current_erp >= p95:
        spec = 30
    elif current_erp >= p90:
        spec = 20
    elif current_erp >= p50:
        spec = 10
    else:
        spec = 5

    return bubble, value, spec


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

    # 加载价格序列
    price_series = load_etf_price_series(code)

    # 生成各类分析块
    holding = is_holding(code)
    header_block = f"## {name}（{code}）\n"

    shiller_block = build_shiller_block(code, (
        prepared_data.get("shiller_grouped"),
        prepared_data.get("shiller_valid"),
        prepared_data.get("shiller_cape_now"),
    ))

    unified_block = build_unified_valuation_block(df, code, val_series=erp_series, win_rate=win_rate, odds_ratio=odds_ratio)

    trend_block = build_trend_block(df, name, erp_series, code, quantiles, ps_df=prepared_data.get("ps_data"))

    exit_summary = compute_exit_signal_summary(code, win_rate, price_series, holding)
    exit_block = build_exit_signal_block(code, win_rate, price_series, holding)

    profit_summary = compute_profit_signal_summary(code, win_rate, price_series)
    profit_block = build_profit_signal_block(code, win_rate, price_series)

    range_stats = compute_range_drawdown_rebound(code, price_series, lookback=120) or {}

    popularity_block = build_sentiment_block(code, name, code, win_rate, prepared_data.get("news_df"))

    dividend_block = build_dividend_yield_block(code)

    etf_df = prepared_data.get("etf_df")
    etf_block = build_etf_quality_block(code, etf_df)

    md = f"""{header_block}{unified_block}{trend_block}{exit_block}{profit_block}{popularity_block}{dividend_block}{etf_block}{shiller_block}"""

    # 汇总信息用于仪表盘
    if summary_list is not None:
        bubble, value, spec = compute_position_sizing(current_erp, quantiles)
        total_pct = bubble + value + spec
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
        full_md = summary_html + "\n" + LEGEND_BLOCK + "\n".join(report_list)

        # docs/report.html 照常本地生成——即使是预览模式也生成没关系，
        # 因为 gh-pages 部署那一步在 workflow 里已经用
        # `if: github.event.inputs.dry_run != 'true'` 挡住了，不会真的发布出去。
        save_html_report(full_md, date_str)

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
            send_to_wechat(full_md, date_str)
            print(f"\n✅ {date_str} 报告已生成并推送")
    else:
        print("\n⚠️ 未生成任何报告")
