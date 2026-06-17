"""
simple_etf_metrics.py  —  AKShare 版（无需 Tushare token）
──────────────────────────────────────────────────────────────────────────────
数据源替换说明：
  pro.fund_daily()   → ak.fund_etf_hist_em()        ETF历史行情（东财）
  pro.fund_nav()     → ak.fund_open_fund_info_em()  ETF历史净值（东财）
  pro.index_daily()  → ak.index_zh_a_hist_csindex() 中证指数历史 / ak.stock_zh_index_daily_em() 其他
输出字段与原版完全一致，下游 erp_position.py 无需改动。
──────────────────────────────────────────────────────────────────────────────
"""

import akshare as ak
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta

# ── ETF列表 ───────────────────────────────────────────────────────────────────
ETF_LIST = [
    ("000300", "510300"),
    ("000688", "588000"),
    ("000922", "515180"),
    ("399989", "512170"),
    ("931071", "515980"),
    ("HSTECH", "513180"),
    ("SPY",    "513500"),
    ("QQQ",    "159696"),
    ("EWQ",    "513080"),
    ("EWJ",    "513880"),
    ("000069", "510150"),
    ("930781", "516620"),
    ("000989", "159936"),
    ("931139", "515650"),
    ("399967", "512660"),
    ("931066", "512710"),
    ("930598", "516150"),
    ("930794", "009225"),
]

# ETF → 基准指数
# csindex: 走 ak.index_zh_a_hist_csindex()
# em:      走 ak.stock_zh_index_daily_em()
ETF_TO_BENCHMARK = {
    "510300": ("000300", "csindex"),
    "588000": ("000688", "csindex"),
    "515180": ("000922", "csindex"),
    "512170": ("399989", "em"),
    "515980": ("931071", "csindex"),
    "513180": (None,     None),
    "513500": (None,     None),
    "159696": (None,     None),
    "513080": (None,     None),
    "513880": (None,     None),
    "510150": ("000069", "csindex"),
    "516620": ("930781", "csindex"),
    "159936": ("000989", "csindex"),
    "515650": ("931139", "csindex"),
    "512660": ("399967", "em"),
    "512710": ("931066", "csindex"),
    "516150": ("930598", "csindex"),
    "009225": ("930794", "csindex"),
}


def _empty_record(erp_code, etf_code):
    return {
        'ts_code':                       etf_code + ('.SH' if etf_code.startswith(('5', '0')) else '.SZ'),
        'erp_code':                      erp_code,
        'name':                          etf_code,
        'trade_date':                    np.nan,
        'latest_close':                  np.nan,
        'latest_pct_chg':                np.nan,
        'excess_return_mean':            np.nan,
        'tracking_error':                np.nan,
        'excess_return_5d_ma':           np.nan,
        'excess_return_10d_ma':          np.nan,
        'excess_return_15d_ma':          np.nan,
        'excess_return_20d_ma':          np.nan,
        'ma_trend_slope':                np.nan,
        'turnover_rate':                 np.nan,
        'turnover_quantile':             np.nan,
        'is_price_turnover_divergence':  np.nan,
        'turnover_ratio_1w':             np.nan,
        'turnover_ratio_1m':             np.nan,
        'turnover_acceleration':         np.nan,
        'latest_discount_rate':          np.nan,
        'discount_quantile_1y':          np.nan,
        'discount_quantile_3y':          np.nan,
        'change_5d_discount':            np.nan,
        'change_10d_discount':           np.nan,
        'annualized_volatility':         np.nan,
        'volatility_quantile_1y':        np.nan,
        'volatility_quantile_3y':        np.nan,
        'volatility_slope':              np.nan,
        'max_drawdown':                  np.nan,
        'max_drawdown_quantile_1y':      np.nan,
        'max_drawdown_quantile_3y':      np.nan,
        'max_drawdown_slope':            np.nan,
    }


def fetch_etf_price(etf_code: str, start_str: str, end_str: str) -> pd.DataFrame | None:
    """
    ak.fund_etf_hist_em() 返回字段：
      日期 开盘 收盘 最高 最低 成交量 成交额 振幅 涨跌幅 涨跌额 换手率
    """
    try:
        df = ak.fund_etf_hist_em(
            symbol=etf_code,
            period="daily",
            start_date=start_str,
            end_date=end_str,
            adjust=""
        )
        if df is None or df.empty:
            return None
        df = df.rename(columns={
            '日期':  'trade_date',
            '收盘':  'close',
            '涨跌幅': 'pct_chg',
            '成交额': 'amount',
            '换手率': 'turnover',
        })
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        return df.sort_values('trade_date').set_index('trade_date')
    except Exception as e:
        print(f"    [行情] {etf_code} 失败: {e}")
        return None


def fetch_etf_nav(etf_code: str) -> pd.DataFrame | None:
    """
    ak.fund_open_fund_info_em() 返回净值历史。
    字段：净值日期 单位净值 累计净值 ...
    适用于场内ETF（其本质是开放式基金）。
    """
    try:
        df = ak.fund_open_fund_info_em(fund=etf_code, indicator="单位净值走势")
        if df is None or df.empty:
            return None
        df = df.rename(columns={'净值日期': 'nav_date', '单位净值': 'unit_nav'})
        df['nav_date'] = pd.to_datetime(df['nav_date'])
        df['unit_nav'] = pd.to_numeric(df['unit_nav'], errors='coerce')
        return df[['nav_date', 'unit_nav']].dropna().sort_values('nav_date').set_index('nav_date')
    except Exception as e:
        print(f"    [净值] {etf_code} 失败: {e}")
        return None


def fetch_index_pct(index_code: str, source: str, start_str: str, end_str: str) -> pd.Series | None:
    """
    返回基准指数日涨跌幅 Series，index 为 trade_date。
    source='csindex' → ak.index_zh_a_hist_csindex()  字段：日期 收盘
    source='em'      → ak.stock_zh_index_daily_em()  字段：date close
    """
    try:
        if source == 'csindex':
            df = ak.index_zh_a_hist_csindex(
                symbol=index_code,
                start_date=start_str,
                end_date=end_str
            )
            if df is None or df.empty:
                return None
            df = df.rename(columns={'日期': 'date', '收盘': 'close'})
        else:  # em
            df = ak.stock_zh_index_daily_em(symbol=index_code)
            if df is None or df.empty:
                return None
            # 字段：date open close high low volume
            df = df[df['date'] >= start_str[:4] + '-' + start_str[4:6] + '-' + start_str[6:]]

        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').set_index('date')
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        pct = df['close'].pct_change() * 100
        pct.name = 'pct_chg_index'
        return pct
    except Exception as e:
        print(f"    [基准] {index_code} 失败: {e}")
        return None


def get_etf_metrics():
    end_date   = datetime.now()
    start_date = end_date - timedelta(days=3 * 365)
    start_str  = start_date.strftime('%Y%m%d')
    end_str    = end_date.strftime('%Y%m%d')

    results = []
    price_frames = []

    for erp_code, etf_code in ETF_LIST:
        print(f"\n处理 {erp_code} -> {etf_code}")
        m = _empty_record(erp_code, etf_code)

        # ── 修正 ts_code 后缀 ───────────────────────────────────────────────
        if etf_code.startswith(('51', '58', '56', '50', '52', '00')):
            m['ts_code'] = etf_code + '.SH'
        else:
            m['ts_code'] = etf_code + '.SZ'

        try:
            # ── 1. 日行情 ──────────────────────────────────────────────────
            df = fetch_etf_price(etf_code, start_str, end_str)
            if df is None:
                print(f"  警告: {etf_code} 无行情数据，跳过")
                results.append(m)
                continue

            latest = df.iloc[-1]
            m['trade_date']     = df.index[-1].strftime('%Y-%m-%d')
            m['latest_close']   = latest['close']
            m['latest_pct_chg'] = latest['pct_chg']

            price_s = df[['close']].copy()
            price_s.columns = [erp_code]
            price_frames.append(price_s)

            time.sleep(0.5)

            # ── 2. 净值 & 折溢价 ───────────────────────────────────────────
            df_nav = fetch_etf_nav(etf_code)
            if df_nav is not None:
                df = df.join(df_nav[['unit_nav']], how='left')
                df['unit_nav'] = df['unit_nav'].ffill()
                df['discount_rate'] = (df['unit_nav'] - df['close']) / df['unit_nav']
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

            time.sleep(0.5)

            # ── 3. 超额收益 & 跟踪误差 ────────────────────────────────────
            bm_info = ETF_TO_BENCHMARK.get(etf_code)
            if bm_info and bm_info[0]:
                bm_code, bm_source = bm_info
                pct_index = fetch_index_pct(bm_code, bm_source, start_str, end_str)
                if pct_index is not None:
                    df = df.join(pct_index, how='left')
                    valid = df[['pct_chg', 'pct_chg_index']].dropna()
                    if len(valid) > 20:
                        df['excess_return'] = df['pct_chg'] - df['pct_chg_index']
                        ex = df['excess_return'].dropna()

                        ex_3y = ex[ex.index >= (end_date - timedelta(days=3 * 365))]
                        m['excess_return_mean'] = ex_3y.mean()
                        m['tracking_error']     = ex_3y.std() * np.sqrt(250)

                        m['excess_return_5d_ma']  = ex.rolling(5).mean().iloc[-1]
                        m['excess_return_10d_ma'] = ex.rolling(10).mean().iloc[-1]
                        m['excess_return_15d_ma'] = ex.rolling(15).mean().iloc[-1]
                        m['excess_return_20d_ma'] = ex.rolling(20).mean().iloc[-1]

                        y = np.array([m['excess_return_5d_ma'],  m['excess_return_10d_ma'],
                                      m['excess_return_15d_ma'], m['excess_return_20d_ma']])
                        x = np.array([5, 10, 15, 20])
                        if not np.any(np.isnan(y)):
                            try:
                                m['ma_trend_slope'] = np.polyfit(x, y, 1)[0]
                            except np.linalg.LinAlgError:
                                pass

                time.sleep(0.5)

            # ── 4. 波动率 & 分位 ──────────────────────────────────────────
            if 'pct_chg' in df.columns:
                df['rolling_vol'] = df['pct_chg'].rolling(20).std() * np.sqrt(250)
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

            # ── 5. 最大回撤 & 分位 ────────────────────────────────────────
            if 'pct_chg' in df.columns:
                cum  = (1 + df['pct_chg'] / 100).cumprod()
                peak = cum.cummax()
                dd   = (peak - cum) / peak
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

            # ── 6. 换手 & 背离 ────────────────────────────────────────────
            if 'amount' in df.columns:
                df_weekly  = df['amount'].resample('W').sum()
                df_monthly = df['amount'].resample('ME').sum()

                amt_1y = df['amount'][df.index >= (end_date - timedelta(days=365))]
                m['turnover_rate'] = amt_1y.mean() if len(amt_1y) > 0 else np.nan

                if len(df_weekly) > 0:
                    m['turnover_ratio_1w'] = df_weekly.iloc[-1]
                if len(df_monthly) > 0:
                    m['turnover_ratio_1m'] = df_monthly.iloc[-1]

                if len(df_weekly) >= 5:
                    avg_4w = df_weekly.iloc[-5:-1].mean()
                    if avg_4w > 0:
                        m['turnover_acceleration'] = df_weekly.iloc[-1] / avg_4w

                if len(df_weekly) >= 52:
                    m['turnover_quantile'] = df_weekly.iloc[-52:].rank(pct=True).iloc[-1]

                price_chg_5d = 0.0
                if len(df) >= 6:
                    price_chg_5d = (df['close'].iloc[-1] - df['close'].iloc[-6]) / df['close'].iloc[-6]

                turnover_chg_1w = 0.0
                if len(df_weekly) >= 2:
                    turnover_chg_1w = df_weekly.iloc[-1] - df_weekly.iloc[-2]

                m['is_price_turnover_divergence'] = int(
                    np.sign(price_chg_5d) != np.sign(turnover_chg_1w)
                )

            results.append(m)
            print(f"  完成: 折价={m['latest_discount_rate']}, "
                  f"波动={m['annualized_volatility']}, "
                  f"换手分位={m['turnover_quantile']}, "
                  f"背离={m['is_price_turnover_divergence']}")

        except Exception as e:
            print(f"  错误 ({etf_code}): {e}")
            results.append(m)

    if price_frames:
        price_df = pd.concat(price_frames, axis=1).sort_index()
        price_df.to_csv('etf_price.csv', encoding='utf-8-sig')
        print("✅ ETF价格序列已保存到 etf_price.csv")

    return pd.DataFrame(results)


if __name__ == '__main__':
    df = get_etf_metrics()

    if df.empty:
        print("❌ 未获取到任何数据，请检查网络")
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

    df.set_index('ts_code').to_csv('simple_etf_metrics.csv', encoding='utf-8-sig')
    print("\n✅ 已保存到 simple_etf_metrics.csv")
