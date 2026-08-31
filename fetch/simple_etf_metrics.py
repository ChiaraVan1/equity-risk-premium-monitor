"""
simple_etf_metrics.py  —  AKShare 版（无需 Tushare token）
──────────────────────────────────────────────────────────────────────────────
数据源替换说明：
  pro.fund_daily()   → ak.fund_etf_hist_sina()          ETF历史行情（新浪）
                        字段: date/open/high/low/close/volume/amount
                        symbol格式: sh510300 / sz159696
  pro.fund_nav()     → ak.fund_etf_fund_info_em()       ETF历史净值（东财）
                        字段: �_�����~j�S�e���N�-�段: 
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
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor

# 【2026-08-02 目录重组说明】本文件从仓库根目录挪到了 fetch/ 下，被
# prepare_all_data.py 以子进程方式直接运行，手动把仓库根目录加回 sys.path，
# 否则 `from config_loader import` 会报 ModuleNotFoundError。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_loader import ETF_LIST, ETF_TO_BENCHMARK

MAX_WORKERS = 1  # 并发线程数。【2026-08-06】原为5，本地macOS环境下多线程并发触发
                 # py_mini_racer(akshare内部用的V8引擎)的 address_pool_manager 崩溃
                 # （Check failed: !pool->IsInitialized()，属于V8多isolate并发初始化在该环境下的稳定性问题，
                 # 与本次增量拉取改动无关），调小并发可显著降低触发概率。遇到限流可再调小至1（等价串行）。

# 【2026-08-06 增量拉取优化】净值(nav)和基准指数(index)接口支持按日期范围查询，
# 以前每次都固定拉满3年，现在改为落盘缓存历史 + 只拉取增量：
#   data/etf_nav.csv   —— 各ETF净值历史宽表（date为索引，列=erp_code）
#   data/index_pct.csv —— 各基准指数涨跌幅历史宽表（date为索引，列=指数代码）
# 每次运行只拉"上次缓存最后日期 - 缓冲天数"到今天这一小段，
# 拉回来的新数据与本地历史合并（重叠日期以新数据为准，覆盖可能的历史修订）后
# 再用于3年滚动窗口计算，计算结果与全量拉取完全一致，只是省掉了重复请求历史数据的时间。
# 行情(price)走新浪 ak.fund_etf_hist_sina()，该接口不支持日期范围参数、只能返回整表，
# 因此不受此优化影响，仍是每次整表拉取。
NAV_CACHE_PATH = './data/etf_nav.csv'
INDEX_CACHE_PATH = './data/index_pct.csv'
PRICE_CACHE_PATH = './data/etf_price.csv'
INCREMENTAL_BUFFER_DAYS = 5  # 增量起点在"上次缓存最后日期"基础上再往前留几天，覆盖数据源可能的历史修订

# 新浪行情 symbol 前缀：上交所 sh，深交所 sz
def _sina_symbol(etf_code: str) -> str:
    code = etf_code.split('.')[0]  # 兼容 config.json 里带 .SH/.SZ 后缀的 etf_code
    if code.startswith(('51', '58', '56', '50', '52', '00')):
        return 'sh' + code
    return 'sz' + code

# ts_code 后缀（兼容下游）
def _ts_code(etf_code: str) -> str:
    code = etf_code.split('.')[0]
    if code.startswith(('51', '58', '56', '50', '52', '00')):
        return code + '.SH'
    return code + '.SZ'


def _load_wide_cache(path: str) -> pd.DataFrame:
    """读取宽表历史缓存（date为索引，每列一个代码）。文件不存在或读取失败则返回空表，
    此时下游会自动退化为"3年前"作为起点，等价于首次全量拉取。"""
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path, index_col=0, parse_dates=True)
    except Exception as e:
        print(f"⚠️ 读取缓存 {path} 失败，按空缓存处理: {e}")
        return pd.DataFrame()


def _incremental_start_str(cache_df: pd.DataFrame, col: str, default_start: datetime) -> str:
    """根据某一列在缓存里的最后日期，算出这次增量拉取该从哪天开始（留缓冲天数覆盖修订）。
    缓存里没有这一列、或列为空时，退化为 default_start（3年前，相当于全量拉取）。"""
    if col and col in cache_df.columns:
        s = cache_df[col].dropna()
        if len(s) > 0:
            start = s.index.max() - timedelta(days=INCREMENTAL_BUFFER_DAYS)
            return max(start, default_start).strftime('%Y%m%d')
    return default_start.strftime('%Y%m%d')


def _merge_series_with_cache(cache_df: pd.DataFrame, new_frames: list) -> pd.DataFrame:
    """把这次新抓到的各列数据和本地历史缓存合并成完整宽表。
    重叠日期以新抓到的数据为准（新数据更权威，可能是数据源的历史修订值），
    缺口部分用缓存里的历史数据补齐。"""
    if not new_frames:
        return cache_df
    new_df = pd.concat(new_frames, axis=1, sort=False)
    if cache_df.empty:
        return new_df.sort_index()
    merged = new_df.combine_first(cache_df)
    return merged.sort_index()


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
        df = ak.fund_etf_fund_info_em(fund=etf_code.split('.')[0], start_date=start_str, end_date=end_str)
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
    (erp_code, etf_code, start_date, end_date,
     nav_start_str, index_start_str, end_str,
     nav_cache_df, index_cache_df) = args
    print(f"\n处理 {erp_code} -> {etf_code}")
    m = _empty_record(erp_code, etf_code)
    price_s = None
    nav_s = None
    index_s = None

    try:
        # ── 1. 日行情 ──────────────────────────────────────────────────────────
        df_all = fetch_etf_price(etf_code)
        if df_all is None:
            print(f"  警告: {etf_code} 无行情数据，跳过")
            return m, price_s, nav_s, index_s

        # 计算窗口以实际最新交易日为锚点，而不是以脚本运行机器的“当前时间”为锚点。
        # GitHub Actions 使用 UTC，本地 Mac 使用 Asia/Shanghai；如果直接用 datetime.now()，
        # 同一批行情可能因为跨时区而让 1 年/3 年窗口相差一天。
        calculation_end_date = pd.Timestamp(df_all.index.max()).normalize()
        calculation_start_date = calculation_end_date - timedelta(days=3 * 365)
        df = df_all[
            (df_all.index >= calculation_start_date)
            & (df_all.index <= calculation_end_date)
        ].copy()
        if df.empty:
            print(f"  警告: {etf_code} 3年内无数据，跳过")
            return m, price_s, nav_s, index_s

        latest = df.iloc[-1]
        m['trade_date']     = df.index[-1].strftime('%Y-%m-%d')
        m['latest_close']   = latest['close']
        m['latest_pct_chg'] = latest['pct_chg']

        _price_s = df[['close']].copy()
        _price_s.columns = [erp_code]
        price_s = _price_s

        time.sleep(0.3)

        # ── 2. 净值 & 折溢价 ─────────────────────────────────────────────────
        # 只拉 [nav_start_str, end_str] 这段增量，再与本地历史缓存合并出完整3年序列，
        # 用于后续滚动分位数计算——计算口径与全量拉取时完全一致。
        cached_nav = nav_cache_df[erp_code].dropna() if erp_code in nav_cache_df.columns else pd.Series(dtype=float)
        df_nav = fetch_etf_nav(etf_code, nav_start_str, end_str)
        if df_nav is not None:
            new_nav_s = df_nav['unit_nav']
            new_nav_s.name = erp_code
            nav_s = new_nav_s  # 只含本次新抓到的增量，用于落盘合并
            full_nav = new_nav_s.combine_first(cached_nav)  # 新数据覆盖重叠日期（修订），历史缺口用缓存补
        else:
            full_nav = cached_nav

        if len(full_nav) > 0:
            df = df.join(full_nav.rename('unit_nav'), how='left')
            df['unit_nav'] = df['unit_nav'].ffill()
            df['discount_rate'] = (df['unit_nav'] - df['close']) / df['unit_nav']
            disc = df['discount_rate'].dropna()

            if len(disc) > 0:
                m['latest_discount_rate'] = disc.iloc[-1]

                disc_1y = disc[disc.index >= (calculation_end_date - timedelta(days=365))]
                if len(disc_1y) > 1:
                    m['discount_quantile_1y'] = disc_1y.rank(pct=True).iloc[-1]

                disc_3y = disc[disc.index >= (calculation_end_date - timedelta(days=3 * 365))]
                if len(disc_3y) > 1:
                    m['discount_quantile_3y'] = disc_3y.rank(pct=True).iloc[-1]

                if len(disc) > 5:
                    m['change_5d_discount']  = disc.iloc[-1] - disc.iloc[-6]
                if len(disc) > 10:
                    m['change_10d_discount'] = disc.iloc[-1] - disc.iloc[-11]

        time.sleep(0.3)

        # ── 3. 超额收益 & 跟踪误差 ───────────────────────────────────────────
        # 基准指数同样只拉 [index_start_str, end_str] 增量，与历史缓存合并出完整序列。
        bm_code = ETF_TO_BENCHMARK.get(etf_code)
        if bm_code:
            cached_index = index_cache_df[bm_code].dropna() if bm_code in index_cache_df.columns else pd.Series(dtype=float)
            pct_index_new = fetch_index_pct(bm_code, index_start_str, end_str)
            if pct_index_new is not None:
                new_index_s = pct_index_new.copy()
                new_index_s.name = bm_code
                index_s = new_index_s  # 只含本次新抓到的增量，用于落盘合并
                full_index = new_index_s.combine_first(cached_index)
            else:
                full_index = cached_index

            if len(full_index) > 0:
                df = df.join(full_index.rename('pct_chg_index'), how='left')
                valid = df[['pct_chg', 'pct_chg_index']].dropna()
                if len(valid) > 20:
                    df['excess_return'] = df['pct_chg'] - df['pct_chg_index']
                    ex = df['excess_return'].dropna()

                    ex_3y = ex[ex.index >= (calculation_end_date - timedelta(days=3 * 365))]
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

        # ── 4. 波动率 & 分位 ────────────────────────────────────────────────
        if 'pct_chg' in df.columns:
            df['rolling_vol'] = df['pct_chg'].rolling(20).std() * np.sqrt(250)
            roll_vol = df['rolling_vol'].dropna()

            if len(roll_vol) > 0:
                m['annualized_volatility'] = roll_vol.iloc[-1]

                vol_1y = roll_vol[roll_vol.index >= (calculation_end_date - timedelta(days=365))]
                if len(vol_1y) > 1:
                    m['volatility_quantile_1y'] = vol_1y.rank(pct=True).iloc[-1]

                vol_3y = roll_vol[roll_vol.index >= (calculation_end_date - timedelta(days=3 * 365))]
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
                dd_1y = roll_dd[roll_dd.index >= (calculation_end_date - timedelta(days=365))]
                if len(dd_1y) > 1:
                    m['max_drawdown_quantile_1y'] = dd_1y.rank(pct=True).iloc[-1]

                dd_3y = roll_dd[roll_dd.index >= (calculation_end_date - timedelta(days=3 * 365))]
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

            amt_1y = df['amount'][df.index >= (calculation_end_date - timedelta(days=365))]
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

    return m, price_s, nav_s, index_s


# ── 数据新鲜度校验 ────────────────────────────────────────────────────────────────────

def get_etf_metrics():
    # 拉取请求统一按中国市场时区生成日期，避免 GitHub UTC 与本地北京时间跨日。
    # 清零时分秒也避免日期索引（每天 00:00）在窗口边界被意外排除。
    end_date = datetime.now(ZoneInfo("Asia/Shanghai")).replace(
        tzinfo=None, hour=0, minute=0, second=0, microsecond=0
    )
    # 给首次全量拉取留 7 天安全垫；真正的指标窗口会在单只 ETF 内按最新交易日裁剪。
    start_date = end_date - timedelta(days=3 * 365 + 7)
    start_str  = start_date.strftime('%Y%m%d')
    end_str    = end_date.strftime('%Y%m%d')

    # 加载净值/基准指数的本地历史缓存，用于算出每个标的这次该从哪天开始增量拉取。
    # 缓存不存在（比如第一次跑）时 _incremental_start_str 会自动退化为 start_str，
    # 即本次仍是全量拉取，行为和优化前完全一致。
    price_cache_df = _load_wide_cache(PRICE_CACHE_PATH)
    nav_cache_df   = _load_wide_cache(NAV_CACHE_PATH)
    index_cache_df = _load_wide_cache(INDEX_CACHE_PATH)

    args_list = []
    for erp_code, etf_code in ETF_LIST:
        nav_start_str = _incremental_start_str(nav_cache_df, erp_code, start_date)
        bm_code = ETF_TO_BENCHMARK.get(etf_code)
        index_start_str = (
            _incremental_start_str(index_cache_df, bm_code, start_date) if bm_code else start_str
        )
        args_list.append((
            erp_code, etf_code, start_date, end_date,
            nav_start_str, index_start_str, end_str,
            nav_cache_df, index_cache_df,
        ))

    results      = []
    price_frames = []
    nav_frames   = []
    index_frames = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for m, price_s, nav_s, index_s in executor.map(_process_single_etf, args_list):
            results.append(m)
            if price_s is not None:
                price_frames.append(price_s)
            if nav_s is not None:
                nav_frames.append(nav_s)
            if index_s is not None:
                index_frames.append(index_s)

    if price_frames:
        # 新浪接口单个标的失败时，保留缓存里的旧列/旧值，不能让整列从宽表中消失。
        price_df = _merge_series_with_cache(price_cache_df, price_frames)
        price_df.to_csv(PRICE_CACHE_PATH, encoding='utf-8-sig')
        print(f"✅ ETF价格序列已保存到 {PRICE_CACHE_PATH}（成功标的更新，失败标的保留缓存）")

    if nav_frames:
        nav_df = _merge_series_with_cache(nav_cache_df, nav_frames)
        nav_df.to_csv(NAV_CACHE_PATH, encoding='utf-8-sig')
        print(f"✅ ETF净值序列已保存到 {NAV_CACHE_PATH}（增量+历史合并）")

    if index_frames:
        index_df = _merge_series_with_cache(index_cache_df, index_frames)
        index_df.to_csv(INDEX_CACHE_PATH, encoding='utf-8-sig')
        print(f"✅ 基准指数序列已保存到 {INDEX_CACHE_PATH}（增量+历史合并）")

    return pd.DataFrame(results)


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

if __name__ == '__main__':
    df = get_etf_metrics()

    if df.empty:
        print("❌ 未获取到任何数据，请检查网络")
        exit(1)

    failed_ts_codes = set(df.loc[df['trade_date'].isna(), 'ts_code'])
    empty_count = len(failed_ts_codes)
    if empty_count > 10:
        print(f"❌ 健全性校验失败：{empty_count} 个标的未获取到行情数据（阈值10），判定本次抓取异常")
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

    # 少量标的临时失败时，保留上一次完整快照，避免用整行空值覆盖有效数据。
    # 失败数量超过阈值仍在上面整体退出；这里仅处理可容忍的局部失败。
    if old_df is not None and failed_ts_codes:
        df = df.set_index('ts_code')
        for ts_code in failed_ts_codes:
            if ts_code not in old_df.index:
                continue
            for col in df.columns:
                if col in old_df.columns and col not in ('erp_code', 'name'):
                    df.loc[ts_code, col] = old_df.loc[ts_code, col]
        df = df.reset_index()
        print(f"⚠️ {len(failed_ts_codes)} 个标的本次行情抓取失败，已保留上次有效快照")

    df = _apply_freshness_check(df, old_df)

    # 抓取失败是明确的新鲜度风险，即使保留旧值也必须在输出中标记。
    for ts_code in failed_ts_codes:
        mask = df['ts_code'] == ts_code
        if not mask.any():
            continue
        previous_note = df.loc[mask, 'stale_note'].fillna('')
        df.loc[mask, 'stale_flag'] = True
        df.loc[mask, 'stale_note'] = previous_note.map(
            lambda note: "本次行情抓取失败，保留上次有效值" + (f"；{note}" if note else "")
        )

    stale_rows = df[df['stale_flag']]
    if len(stale_rows) > 0:
        print(f"\n⚠️ 数据新鲜度预警：{len(stale_rows)} 个ETF指标连续{FRESHNESS_STALE_THRESHOLD}次以上未变化，请检查数据源⚚")
        for _, r in stale_rows.iterrows():
            print(f"   {r['erp_code']} ({r['ts_code']}): {r['stale_note']}")

    df.set_index('ts_code').to_csv('./data/simple_etf_metrics.csv', encoding='utf-8-sig')
    print("\n✅ 已保存到 data/simple_etf_metrics.csv")
