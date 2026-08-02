"""
erp_position.py (简化版)
主入口：调度数据准备 → 循环分析 → 生成报告 → 推送
"""
import os
from datetime import datetime

from config_loader import HOLDING_CATEGORY, INDICES_LIST
from prepare_all_data import prepare_all_data
from analysis.valuation import build_shiller_block, build_unified_valuation_block, calc_odds, is_holding
from analysis.risk import compute_exit_signal_summary, build_exit_signal_block, compute_profit_signal_summary, build_profit_signal_block, compute_range_drawdown_rebound
from analysis.trend import compute_erp_slope_signal, build_trend_block, build_monthly_trend_ai_block
from analysis.sentiment import build_sentiment_block
from analysis.utils import check_metric_freshness, build_freshness_note, generate_action_sentence, _format_win_odds, _format_range
from report.markdown import build_summary_block, build_etf_ai_interpretation, save_html_report, send_to_wechat, LEGEND_BLOCK
from dividend_yield import build_dividend_yield_block
from popularity_signal import build_popularity_block, compute_popularity_confirmation

import pandas as pd


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

    erp_series = df.set_index('Date')['ERP']
    current_erp = erp_series.iloc[-1]
    quantiles = {
        'P10': erp_series.quantile(0.10),
        'P25': erp_series.quantile(0.25),
        'P50': erp_series.quantile(0.50),
        'P75': erp_series.quantile(0.75),
        'P90': erp_series.quantile(0.90),
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
        erp_zone = "🚨 危险泡沫"

    # 加载价格序列
    from prepare_all_data import load_etf_price_series
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
    
    trend_block = build_trend_block(df, name, erp_series, code, quantiles)
    
    exit_block = build_exit_signal_block(code, win_rate, price_series, holding)
    
    profit_block = build_profit_signal_block(code, win_rate)
    
    popularity_block = build_sentiment_block(code, name, code, win_rate, prepared_data.get("news_df"))
    
    dividend_block = build_dividend_yield_block(code)
    
    etf_block = build_etf_ai_interpretation(code, name, prepared_data.get("etf_df"))

    md = f"""{header_block}{unified_block}{trend_block}{exit_block}{profit_block}{popularity_block}{dividend_block}{etf_block}{shiller_block}"""

    # 汇总信息用于仪表盘
    if summary_list is not None:
        summary_list.append({
            "code": code,
            "name": name,
            "erp_zone": erp_zone,
            "erp": current_erp,
            "win_rate": win_rate,
            "odds_ratio": odds_ratio,
            "win_odds_str": _format_win_odds({"win_rate": win_rate, "odds_ratio": odds_ratio}),
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

    # 排序和输出
    if report_list:
        date_str = datetime.now().strftime("%Y-%m-%d")
        summary_html = build_summary_block(summary_list, output_format="html")
        summary_wechat = build_summary_block(summary_list, output_format="markdown")

        full_report = (
            "# ERP 策略每日监控报告\n"
            + summary_html
            + LEGEND_BLOCK
            + "".join(report_list)
        )

        save_html_report(full_report, date_str)

        if os.getenv("DRY_RUN") == "true":
            print("✅ dry-run 模式，报告已生成，不推送微信。")
        else:
            send_to_wechat(summary_wechat, date_str)
    else:
        print("❌ 未生成任何有效报告，请检查数据文件。")
