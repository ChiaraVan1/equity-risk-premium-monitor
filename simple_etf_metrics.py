"""
simple_etf_metrics.py  —  AKShare 版（无需 Tushare token）
──────────────────────────────────────────────────────────────────────────────
数据源替换说明：
  pro.fund_daily()   → ak.fund_etf_hist_sina()          ETF历史行情（新浪）
                        字段: date/open/high/low/close/volume/amount
                        symbol格式: sh510300 / sz159696
  pro.fund_nav()     → ak.fund_etf_fund_info_em()       ETF历史净值（东财）
                        字段: 净值日期/单位净值/累计净值/...
  pro.index_daily()  → ak.stock_zh_index_hist_csindex() 中证官网（全部走这个）
                        字段: 日期/收盘/涨跌幅/...
输出字段与原版完全一致，下游 erp_position.py 无需改动。
──────────────────────────────────────────────────────────────────────────────
并发说明：
  使用 ThreadPoolExecutor(max_workers=5) 并行处理各 ETF，
  网络 IO 等待时间重叠，速度约提升 3-5 倍。
  各线程内部保留 time.sleep(0.3) 防止对同一数据源限流。
  如遇频繁 429/限流，可将 MAX_WORKERS 调小至 3。
──────────────────────────────────────────────────────────────────────────────
"""

import akshare as ak
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

MAX_WORKERS = 5  # 并发线程数，遇到限流可调小

# ── ETF列表 (erp_code, etf_code) ─────────────────────────────────────────────
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
    ("000819", "512400"),
    ("950125", "588710"),
    ("399975", "512880")
]

# 新浪行情 symbol 前缀：上交所 sh，深交所 sz
def _sina_symbol(etf_code: str) -> str:
    if etf_code.startswith(('51', '58', '56', '50', '52', '00')):
        return 'sh' + etf_code
    return 'sz' + etf_code

# ts_code 后缀（兼容下游）
def _ts_code(etf_code: str) -> str:
    if etf_code.startswith(('51', '58', '56', '50', '52', '00')):
        return etf_code + '.SH'
    return etf_code + '.SZ'

# ETF → 基准指数
ETF_TO_BENCHMARK = {
    "510300": "000300",
    "588000": "000688",
    "515180": "000922",
    "512170": "399989",
    "515980": "931071",
    "513180": None,
    "513500": None,
    "159696": None,
    "513080": None,
    "513880": None,
    "510150": "000069",
    "516620": "930781",
    "159936": "000989",
    "515650": "931139",
    "512660": "399967",
    "512710": "931066",
    "516150": "930598",
    "512400": "000819",
    "588710": "950125",
    "512880": "399975"
}


def _empty_record(erp_code, etf_code):
    return {
        'ts_code':                       _ts_code(etf_code),
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


def fetch_etf_price(etf_code: str) -> pd.DataFrame | None:
    try:
        symbol = _sina_symbol(etf_code)
        df = ak.fund_etf_hist_sina(symbol=symbol)
        if df is None or df.empty:
            return None
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').set_index('date')
        df['close']  = pd.to_numeric(df['close'],  errors='coerce')
        df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
        df['pct_chg'] = df['close'].pct_change() * 100
        return df
    except Exception as e:
        print(f"    [行情] {etf_code} ({_sina_symbol(etf_code)}) 失败: {e}")
        return None


def fetch_etf_nav(etf_code: str, start_str: str, end_str: str) -> pd.DataFrame | None:
    try:
        df = ak.fund_etf_fund_info_em(fund=etf_code, start_date=start_str, end_date=end_str)
        if df is None or df.empty:
            return None
        df = df.rename(columns={'净值日期': 'nav_date', '单位净值': 'unit_nav'})
        df['nav_date'] = pd.to_datetime(df['nav_date'])
        df['unit_nav'] = pd.to_numeric(df['unit_nav'], errors='coerce')
        return df[['nav_date', 'unit_nav']].dropna().sort_values('nav_date').set_index('nav_date')
    except Exception as e:
        print(f"    [净值] {etf_code} 失败: {e}")
        return None


def fetch_index_pct(index_code: str, start_str: str, end_str: str) -> pd.Series | None:
    try:
        df = ak.stock_zh_index_hist_csindex(
            symbol=index_code,
            start_date=start_str,
            end_date=end_str
        )
        if df is None or df.empty:
            return None
        df = df.rename(columns={'日期': 'date', '涨跌幅': 'pct_chg_index'})
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').set_index('date')
        df['pct_chg_index'] = pd.to_numeric(df['pct_chg_index'], errors='coerce')
        return df['pct_chg_index']
    except Exception as e:
        print(f"    [基准] {index_code} 失败: {e}")
        return None


# ── 单只 ETF 处理（在线程中运行）────────────────────────────────────────────────

def _process_single_etf(args):
    erp_code, etf_code, start_date, end_date, start_str, end_str = args
    print(f"\n处理 {erp_code} -> {etf_code}")
    m = _empty_record(erp_code, etf_code)
    price_s = None

    try:
        # ── 1. 日行情 ────────────────────────────────────────────────────────
        df_all = fetch_etf_price(etf_code)
        if df_all is None:
            print(f"  警告: {etf_code} 无行情数据，跳过")
            return m, price_s

        df = df_all[df_all.index >= start_date].copy()
        if df.empty:
            print(f"  警告: {etf_code} 3年内无数据，跳过")
            return m, price_s

        latest = df.iloc[-1]
        m['trade_date']     = df.index[-1].strftime('%Y-%m-%d')
        m['latest_close']   = latest['close']
        m['latest_pct_chg'] = latest['pct_chg']

        _price_s = df[['close']].copy()
        _price_s.columns = [erp_code]
        price_s = _price_s

        time.sleep(0.3)

        # ── 2. 净值 & 折溢价 ─────────────────────────────────────────────────
        df_nav = fetch_etf_nav(etf_code, start_str, end_str)
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

        time.sleep(0.3)

        # ── 3. 超额收益 & 跟踪误差 ───────────────────────────────────────────
        bm_code = ETF_TO_BENCHMARK.get(etf_code)
        if bm_code:
            pct_index = fetch_index_pct(bm_code, start_str, end_str)
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

            time.sleep(0.3)

        # ── 4. 波动率 & 分位 ─────────────────────────────────────────────────
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

        # ── 5. 最大回撤 & 分位 ───────────────────────────────────────────────
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

        # ── 6. 换手（成交额）& 背离 ──────────────────────────────────────────
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

            price_chg_5d, turnover_chg_1w = 0.0, 0.0
            if len(df) >= 6:
                price_chg_5d = (df['close'].iloc[-1] - df['close'].iloc[-6]) / df['close'].iloc[-6]
            if len(df_weekly) >= 2:
                turnover_chg_1w = df_weekly.iloc[-1] - df_weekly.iloc[-2]

            m['is_price_turnover_divergence'] = int(
                np.sign(price_chg_5d) != np.sign(turnover_chg_1w)
            )

        print(f"  完成 {erp_code}: 折价={m['latest_discount_rate']:.4f}, "
              f"波动={m['annualized_volatility']:.4f}, "
              f"换手分位={m['turnover_quantile']}, "
              f"背离={m['is_price_turnover_divergence']}")

    except Exception as e:
        print(f"  错误 ({etf_code}): {e}")

    return m, price_s


# ── 数据新鲜度校验 ────────────────────────────────────────────────────────────
# 逐日快照（非时间序列），无法像 erp_*.csv 那样直接看"最近N行是否相同"，
# 改为对比"这次生成的值"和"上一次已提交到 data/ 的值"，用一个隐藏的 _streak 列
# 持久化"连续未变化次数"，跨天累加。达到阈值才标记预警，避免偶发的真实持平被误报。
FRESHNESS_STALE_THRESHOLD = 3
_FRESHNESS_COLS = ['latest_discount_rate', 'turnover_quantile', 'annualized_volatility', 'tracking_error']


def _load_previous_snapshot(path='./data/simple_etf_metrics.csv'):
    try:
        return pd.read_csv(path, index_col='ts_code')
    except Exception:
        return None


def _apply_freshness_check(df: pd.DataFrame, old_df: pd.DataFrame | None) -> pd.DataFrame:
    """对比上一次快照，给每个ETF标记 stale_flag / stale_note，同时把最新streak写回
    _{col}_streak 列（随文件一起提交，下次运行时接着累加）。

    ★ 只在 trade_date 真的推进到新交易日时才累加/清零 streak；同一交易日内
    重复手动跑（测试、临时加标的等）不会推进 streak，避免把"重复运行次数"
    误当成"连续未变化天数"，产生假预警。
    """
    df = df.set_index('ts_code')
    stale_flags, stale_notes = [], []

    for ts_code, row in df.iterrows():
        note_parts = []
        cur_trade_date = row.get('trade_date')
        prev_trade_date = None
        if old_df is not None and ts_code in old_df.index and 'trade_date' in old_df.columns:
            prev_trade_date = old_df.loc[ts_code, 'trade_date']
        is_new_trading_day = pd.notna(cur_trade_date) and (
            pd.isna(prev_trade_date) or cur_trade_date != prev_trade_date
        )

        for col in _FRESHNESS_COLS:
            streak_col = f"_{col}_streak"
            prev_streak = 0
            prev_val = np.nan
            if old_df is not None and ts_code in old_df.index:
                if streak_col in old_df.columns:
                    v = old_df.loc[ts_code, streak_col]
                    prev_streak = 0 if pd.isna(v) else int(v)
                if col in old_df.columns:
                    prev_val = old_df.loc[ts_code, col]

            cur_val = row.get(col)

            if not is_new_trading_day:
                # 同一交易日内的重复运行：streak原样保留，不重复计数
                streak = prev_streak
            elif pd.notna(cur_val) and pd.notna(prev_val) and abs(float(cur_val) - float(prev_val)) < 1e-9:
                streak = prev_streak + 1
            else:
                streak = 0
            df.loc[ts_code, streak_col] = streak

            if streak >= FRESHNESS_STALE_THRESHOLD:
                note_parts.append(f"{col} 已连续 {streak} 次未变化")

        if note_parts:
            stale_flags.append(True)
            stale_notes.append("；".join(note_parts))
        else:
            stale_flags.append(False)
            stale_notes.append("")

    df['stale_flag'] = stale_flags
    df['stale_note'] = stale_notes
    return df.reset_index()


# ── 主入口 ────────────────────────────────────────────────────────────────────

def get_etf_metrics():
    end_date   = datetime.now()
    start_date = end_date - timedelta(days=3 * 365)
    start_str  = start_date.strftime('%Y%m%d')
    end_str    = end_date.strftime('%Y%m%d')

    args_list = [
        (erp_code, etf_code, start_date, end_date, start_str, end_str)
        for erp_code, etf_code in ETF_LIST
    ]

    results      = []
    price_frames = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for m, price_s in executor.map(_process_single_etf, args_list):
            results.append(m)
            if price_s is not None:
                price_frames.append(price_s)

    if price_frames:
        price_df = pd.concat(price_frames, axis=1).sort_index()
        price_df.to_csv('./data/etf_price.csv', encoding='utf-8-sig')
        print("✅ ETF价格序列已保存到 data/etf_price.csv")

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

    old_df = _load_previous_snapshot()
    df = _apply_freshness_check(df, old_df)

    stale_rows = df[df['stale_flag']]
    if len(stale_rows) > 0:
        print(f"\n⚠️ 数据新鲜度预警：{len(stale_rows)} 个ETF指标连续{FRESHNESS_STALE_THRESHOLD}次以上未变化，请检查数据源：")
        for _, r in stale_rows.iterrows():
            print(f"   {r['erp_code']} ({r['ts_code']}): {r['stale_note']}")

    df.set_index('ts_code').to_csv('./data/simple_etf_metrics.csv', encoding='utf-8-sig')
    print("\n✅ 已保存到 data/simple_etf_metrics.csv")
