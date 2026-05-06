"""
simple_etf_metrics.py
──────────────────────────────────────────────────────────────────────────────
ETF 指标生产脚本（GitHub Actions 每日运行）
输出 simple_etf_metrics.csv，供 etf_metrics.py 读取

计算口径：与 ETF监控模型 完全一致
──────────────────────────────────────────────────────────────────────────────
"""

import tushare as ts
import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta

# ── ETF列表（以实际投资标的为准）────────────────────────────────────────────────
ETF_LIST = [
    ("000300", "510300.SH"),   # 沪深300
    ("000688", "588000.SH"),   # 科创50
    ("000922", "515180.SH"),   # 中证红利
    ("399989", "512170.SH"),   # 中证医疗
    ("931071", "159819.SZ"),   # 人工智能
    ("HSTECH", "513180.SH"),   # 恒生科技
    ("SPY",    "513500.SH"),   # 标普500
    ("QQQ",    "159696.SZ"),   # 纳斯达克
    ("EWQ",    "513080.SH"),   # 法国ETF
    ("EWJ",    "513880.SH"),   # 日本ETF
]

# ETF → 基准指数（仅 tushare index_daily 可拉的 A 股基准参与超额收益计算）
ETF_TO_BENCHMARK = {
    "510300.SH": "000300.SH",
    "588000.SH": "000688.SH",
    "515180.SH": "000922.CSI",
    "512170.SH": "399989.SZ",
    "159819.SZ": "931071.CSI",
    # 以下无可用 A 股基准，超额收益留 NaN
    "513180.SH": None,
    "513500.SH": None,
    "159696.SZ": None,
    "513080.SH": None,
    "513880.SH": None,
}


def get_etf_metrics():
    token = os.environ.get('TUSHARE_TOKEN')
    if not token:
        raise ValueError("请设置 TUSHARE_TOKEN 环境变量")
    ts.set_token(token)
    pro = ts.pro_api()

    end_date   = datetime.now()
    start_date = end_date - timedelta(days=3 * 365)   # 取3年，与监控模型一致
    start_str  = start_date.strftime('%Y%m%d')
    end_str    = end_date.strftime('%Y%m%d')

    results = []
    price_frames = []   # ← 新增：收集各ETF价格序列，用于胜率赔率计算

    for erp_code, etf_code in ETF_LIST:
        print(f"\n处理 {erp_code} -> {etf_code}")

        # 初始化所有指标为 NaN，与监控模型字段完全对齐
        m = {
            'ts_code':                       etf_code,
            'erp_code':                      erp_code,
            'name':                          etf_code,
            'trade_date':                    np.nan,
            'latest_close':                  np.nan,
            'latest_pct_chg':                np.nan,
            # 超额收益
            'excess_return_mean':            np.nan,
            'tracking_error':                np.nan,
            'excess_return_5d_ma':           np.nan,
            'excess_return_10d_ma':          np.nan,
            'excess_return_15d_ma':          np.nan,
            'excess_return_20d_ma':          np.nan,
            'ma_trend_slope':                np.nan,
            # 换手
            'turnover_rate':                 np.nan,
            'turnover_quantile':             np.nan,
            'is_price_turnover_divergence':  np.nan,
            'turnover_ratio_1w':             np.nan,
            'turnover_ratio_1m':             np.nan,
            'turnover_acceleration':         np.nan,
            # 折溢价
            'latest_discount_rate':          np.nan,
            'discount_quantile_1y':          np.nan,
            'discount_quantile_3y':          np.nan,
            'change_5d_discount':            np.nan,
            'change_10d_discount':           np.nan,
            # 波动率
            'annualized_volatility':         np.nan,
            'volatility_quantile_1y':        np.nan,
            'volatility_quantile_3y':        np.nan,
            'volatility_slope':              np.nan,
            # 回撤
            'max_drawdown':                  np.nan,
            'max_drawdown_quantile_1y':      np.nan,
            'max_drawdown_quantile_3y':      np.nan,
            'max_drawdown_slope':            np.nan,
        }

        try:
            # ── 1. 日行情 ────────────────────────────────────────────────────
            df = pro.fund_daily(ts_code=etf_code,
                                start_date=start_str, end_date=end_str)
            if df is None or df.empty:
                print(f"  警告: {etf_code} 无行情数据，跳过")
                results.append(m)
                continue

            df.rename(columns={'close': 'close_fund', 'pct_chg': 'pct_chg_fund',
                                'amount': 'amount_fund'}, inplace=True)
            df['trade_date'] = pd.to_datetime(df['trade_date'])
            df = df.sort_values('trade_date').set_index('trade_date')

            latest = df.iloc[-1]
            m['trade_date']     = df.index[-1].strftime('%Y-%m-%d')
            m['latest_close']   = latest['close_fund']
            m['latest_pct_chg'] = latest['pct_chg_fund']

            # ← 新增：保存价格序列，供胜率赔率计算使用
            price_s = df[['close_fund']].copy()
            price_s.columns = [erp_code]
            price_frames.append(price_s)

            # ── 2. 净值（折溢价）────────────────────────────────────────────
            df_nav = pro.fund_nav(ts_code=etf_code,
                                  start_date=start_str, end_date=end_str)
            if df_nav is not None and not df_nav.empty:
                df_nav['nav_date'] = pd.to_datetime(df_nav['nav_date'])
                df_nav = df_nav.sort_values('nav_date').set_index('nav_date')
                df = df.join(df_nav[['unit_nav']], how='left')
                df['unit_nav'] = df['unit_nav'].ffill()

                df['discount_rate'] = (df['unit_nav'] - df['close_fund']) / df['unit_nav']
                disc = df['discount_rate'].dropna()

                if len(disc) > 0:
                    m['latest_discount_rate'] = disc.iloc[-1]

                    disc_1y = disc[disc.index >= (end_date - timedelta(days=365))]
                    if len(disc_1y) > 1:
                        m['discount_quantile_1y'] = disc_1y.rank(pct=True).iloc[-1]

                    disc_3y = disc[disc.index >= (end_date - timedelta(days=3 * 365))]
                    if len(disc_3y) > 1:
                        m['discount_quantile_3y'] = disc_3y.rank(pct=True).iloc[-1]

                    if len(disc) > 5:
                        m['change_5d_discount']  = disc.iloc[-1] - disc.iloc[-6]
                    if len(disc) > 10:
                        m['change_10d_discount'] = disc.iloc[-1] - disc.iloc[-11]

            # ── 3. 超额收益 & 跟踪误差 ───────────────────────────────────────
            benchmark_code = ETF_TO_BENCHMARK.get(etf_code)
            if benchmark_code:
                try:
                    df_bm = pro.index_daily(ts_code=benchmark_code,
                                            start_date=start_str, end_date=end_str)
                    if df_bm is not None and not df_bm.empty:
                        df_bm.rename(columns={'pct_chg': 'pct_chg_index'}, inplace=True)
                        df_bm['trade_date'] = pd.to_datetime(df_bm['trade_date'])
                        df_bm = df_bm.sort_values('trade_date').set_index('trade_date')
                        df = df.join(df_bm[['pct_chg_index']], how='left')

                        valid = df[['pct_chg_fund', 'pct_chg_index']].dropna()
                        if len(valid) > 20:
                            df['excess_return'] = df['pct_chg_fund'] - df['pct_chg_index']
                            ex = df['excess_return'].dropna()

                            # 3年均值和跟踪误差（与监控模型一致）
                            ex_3y = ex[ex.index >= (end_date - timedelta(days=3 * 365))]
                            m['excess_return_mean'] = ex_3y.mean()
                            m['tracking_error']     = ex_3y.std() * np.sqrt(250)

                            # 滚动MA（5/10/15/20日）
                            m['excess_return_5d_ma']  = ex.rolling(5).mean().iloc[-1]
                            m['excess_return_10d_ma'] = ex.rolling(10).mean().iloc[-1]
                            m['excess_return_15d_ma'] = ex.rolling(15).mean().iloc[-1]
                            m['excess_return_20d_ma'] = ex.rolling(20).mean().iloc[-1]

                            # ma_trend_slope：4个MA点线性拟合（与监控模型一致）
                            y = np.array([m['excess_return_5d_ma'],  m['excess_return_10d_ma'],
                                          m['excess_return_15d_ma'], m['excess_return_20d_ma']])
                            x = np.array([5, 10, 15, 20])
                            if not np.any(np.isnan(y)):
                                try:
                                    m['ma_trend_slope'] = np.polyfit(x, y, 1)[0]
                                except np.linalg.LinAlgError:
                                    pass
                except Exception as e:
                    print(f"  超额收益计算失败（{benchmark_code}）: {e}")

            # ── 4. 波动率 & 分位 ─────────────────────────────────────────────
            if 'pct_chg_fund' in df.columns:
                df['rolling_vol'] = df['pct_chg_fund'].rolling(20).std() * np.sqrt(250)
                roll_vol = df['rolling_vol'].dropna()

                if len(roll_vol) > 0:
                    m['annualized_volatility'] = roll_vol.iloc[-1]

                    vol_1y = roll_vol[roll_vol.index >= (end_date - timedelta(days=365))]
                    if len(vol_1y) > 1:
                        m['volatility_quantile_1y'] = vol_1y.rank(pct=True).iloc[-1]

                    vol_3y = roll_vol[roll_vol.index >= (end_date - timedelta(days=3 * 365))]
                    if len(vol_3y) > 1:
                        m['volatility_quantile_3y'] = vol_3y.rank(pct=True).iloc[-1]

                    if len(roll_vol) >= 20:
                        y = roll_vol.iloc[-20:].values
                        x = np.arange(len(y))
                        try:
                            m['volatility_slope'] = np.polyfit(x, y, 1)[0]
                        except np.linalg.LinAlgError:
                            pass

            # ── 5. 最大回撤 & 分位 ───────────────────────────────────────────
            if 'pct_chg_fund' in df.columns:
                cum  = (1 + df['pct_chg_fund'] / 100).cumprod()
                peak = cum.cummax()
                dd   = (peak - cum) / peak
                df['drawdown'] = dd

                m['max_drawdown'] = dd.max()

                df['rolling_dd'] = dd.rolling(20).max()
                roll_dd = df['rolling_dd'].dropna()

                if len(roll_dd) > 0:
                    dd_1y = roll_dd[roll_dd.index >= (end_date - timedelta(days=365))]
                    if len(dd_1y) > 1:
                        m['max_drawdown_quantile_1y'] = dd_1y.rank(pct=True).iloc[-1]

                    dd_3y = roll_dd[roll_dd.index >= (end_date - timedelta(days=3 * 365))]
                    if len(dd_3y) > 1:
                        m['max_drawdown_quantile_3y'] = dd_3y.rank(pct=True).iloc[-1]

                    if len(roll_dd) >= 20:
                        y = roll_dd.iloc[-20:].values
                        x = np.arange(len(y))
                        try:
                            m['max_drawdown_slope'] = np.polyfit(x, y, 1)[0]
                        except np.linalg.LinAlgError:
                            pass

            # ── 6. 换手 & 背离 ───────────────────────────────────────────────
            if 'amount_fund' in df.columns:
                # 周/月成交额（与监控模型一致，用 resample）
                df_weekly  = df['amount_fund'].resample('W').sum()
                df_monthly = df['amount_fund'].resample('ME').sum()

                # turnover_rate = 1年日均成交额（AUM未知时的近似）
                amt_1y = df['amount_fund'][df.index >= (end_date - timedelta(days=365))]
                m['turnover_rate'] = amt_1y.mean() if len(amt_1y) > 0 else np.nan

                # 周/月成交额原始值
                if len(df_weekly) > 0:
                    m['turnover_ratio_1w'] = df_weekly.iloc[-1]
                if len(df_monthly) > 0:
                    m['turnover_ratio_1m'] = df_monthly.iloc[-1]

                # 换手加速度：本周成交额 / 本月成交额
                if len(df_monthly) > 0 and df_monthly.iloc[-1] > 0 and len(df_weekly) > 0:
                    m['turnover_acceleration'] = df_weekly.iloc[-1] / df_monthly.iloc[-1]

                # 换手分位：周成交额52周分位（与监控模型一致）
                if len(df_weekly) >= 52:
                    m['turnover_quantile'] = df_weekly.iloc[-52:].rank(pct=True).iloc[-1]

                # 背离：5日价格方向 vs 周成交额环比（与监控模型一致）
                price_chg_5d = 0.0
                if len(df) >= 6:
                    price_chg_5d = (df['close_fund'].iloc[-1] - df['close_fund'].iloc[-6]) / df['close_fund'].iloc[-6]

                turnover_chg_1w = 0.0
                if len(df_weekly) >= 2:
                    turnover_chg_1w = df_weekly.iloc[-1] - df_weekly.iloc[-2]

                m['is_price_turnover_divergence'] = int(
                    np.sign(price_chg_5d) != np.sign(turnover_chg_1w)
                )

            results.append(m)
            print(f"  完成: 折价={m['latest_discount_rate']:.4f}, "
                  f"波动={m['annualized_volatility']:.4f}, "
                  f"换手分位={m['turnover_quantile']}, "
                  f"背离={m['is_price_turnover_divergence']}")

        except Exception as e:
            print(f"  错误 ({etf_code}): {e}")
            results.append(m)

    # ← 新增：合并所有ETF价格序列并保存
    if price_frames:
        price_df = pd.concat(price_frames, axis=1).sort_index()
        price_df.to_csv('etf_price.csv', encoding='utf-8-sig')
        print("✅ ETF价格序列已保存到 etf_price.csv")

    return pd.DataFrame(results)


if __name__ == '__main__':
    df = get_etf_metrics()

    if df.empty:
        print("❌ 未获取到任何数据，请检查 TUSHARE_TOKEN 和网络")
        exit(1)

    print("\n" + "=" * 80)
    print("ETF 指标汇总")
    print("=" * 80)
    cols = ['erp_code', 'ts_code',
            'latest_discount_rate', 'discount_quantile_1y',
            'annualized_volatility', 'volatility_quantile_1y',
            'max_drawdown', 'max_drawdown_quantile_1y',
            'turnover_quantile', 'is_price_turnover_divergence',
            'excess_return_mean', 'tracking_error', 'ma_trend_slope']
    print(df[cols].to_string(index=False))

    # 以 ts_code 为索引保存（load_etf_metrics 读取时 index_col="ts_code"）
    df.set_index('ts_code').to_csv('simple_etf_metrics.csv', encoding='utf-8-sig')
    print("\n✅ 已保存到 simple_etf_metrics.csv")
