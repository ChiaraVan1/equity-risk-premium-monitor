"""
dividend_yield.py（扩展版）
──────────────────────────────────────────────────────────────────────────────
指数股息率估值锚模块，支持 A股（理杏仁）+ 境外/港股（yfinance）双数据源

数据源：
1. 理杏仁开放平台「指数基本面数据API」 → A股指数
2. yfinance → SPY / QQQ / EWQ / EWJ / EWG / EEM + HSTECH(代理3032.HK)

A股更新频率：周更（免费额度1000/年）
yfinance：周更（无配额限制），港股数据可能有1-2天延迟

缓存策略：本地CSV + 周频增量更新，避免重复调用
──────────────────────────────────────────────────────────────────────────────
"""

import os
import json
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests
import yfinance as yf

# ── 从中心配置文件导入指数/标的配置 ──────────────────────────────────────────
from config_loader import DIVIDEND_INDEX_CODES, YFINANCE_TICKERS

# ── 配置 ──────────────────────────────────────────────────────────────────
LIXINGER_URL = "https://open.lixinger.com/api/cn/index/fundamental"
LIXINGER_TOKEN_ENV = "LIXINGER_TOKEN"

CACHE_DIR = "./data/dividend_yield"
_STATE_PATH = os.path.join(CACHE_DIR, "_last_update.json")

BACKFILL_YEARS = 10 # 单次日期区间查询理杏仁上限就是10年
UPDATE_INTERVAL_DAYS = 7 # 周更，不做日更

_API_MAX_RETRIES = 3
_API_RETRY_BASE_DELAY = 5 # 秒，指数退避：5s, 10s, 20s

_ZONE_THRESHOLDS = [
    (0.90, "🟢 股息率历史罕见地高"),
    (0.75, "🟢 股息率偏高"),
    (0.50, "🟡 股息率中性偏高"),
    (0.25, "🟠 股息率中性偏低"),
    (0.10, "🔴 股息率偏低"),
    (0.00, "🚨 股息率历史罕见地低"),
]

_dy_cache = {} # 内存缓存，避免同一进程内重复读盘

# ──────────────────────────────────────────────────────────────────────────
# ── 理杏仁数据源（A股）
# ──────────────────────────────────────────────────────────────────────────

def _get_token() -> str:
    token = os.getenv(LIXINGER_TOKEN_ENV, "")
    if not token:
        raise RuntimeError(
            f"未设置环境变量 {LIXINGER_TOKEN_ENV}，请先 export {LIXINGER_TOKEN_ENV}=\"你的token\""
        )
    return token

def _call_lixinger(payload: dict) -> list | None:
    """POST请求理杏仁接口，带限流退避重试。失败返回None，不抛异常，由调用方降级处理。"""
    last_exc = None
    for attempt in range(_API_MAX_RETRIES):
        try:
            resp = requests.post(LIXINGER_URL, json=payload, timeout=30)
            if resp.status_code == 429:
                wait = _API_RETRY_BASE_DELAY * (2 ** attempt)
                if attempt < _API_MAX_RETRIES - 1:
                    time.sleep(wait)
                    continue
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])
        except Exception as e:
            last_exc = e
            if attempt < _API_MAX_RETRIES - 1:
                time.sleep(_API_RETRY_BASE_DELAY * (2 ** attempt))
                continue
    print(
        f"⚠️ 理杏仁接口请求失败（已重试{_API_MAX_RETRIES}次）：{last_exc}"
    )
    return None

def _parse_records(records: list) -> pd.DataFrame:
    """理杏仁返回记录形如 {'date': '...', 'dyr.mcw': 0.0272..., 'stockCode': '...'}。"""
    rows = []
    for r in records:
        dy = r.get("dyr.mcw")
        if dy is None:
            continue
        rows.append(
            {"date": pd.to_datetime(r["date"]).tz_localize(None), "dy": dy * 100}
        )
    return pd.DataFrame(rows)

def _backfill_index(index_code: str) -> bool:
    """单个指数一次性回填最长10年历史（一次API调用）。已有本地缓存则跳过。"""
    if os.path.exists(_cache_path(index_code)):
        return True

    end = datetime.now().date()
    start = end - timedelta(days=365 * BACKFILL_YEARS)
    payload = {
        "token": _get_token(),
        "stockCodes": [index_code],
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "metricsList": ["dyr.mcw"],
    }
    records = _call_lixinger(payload)
    if not records:
        print(f"⚠️ 指数 {index_code} 历史回填失败，股息率估值锚将对该标的跳过。")
        return False

    df = _parse_records(records)
    if len(df) == 0:
        print(f"⚠️ 指数 {index_code} 回填结果为空，跳过。")
        return False

    _save_cache(index_code, df)
    print(
        f"✅ 股息率历史回填完成：{index_code}（{len(df)}条，{df['date'].min().date()} ~ {df['date'].max().date()}）"
    )
    return True

# ──────────────────────────────────────────────────────────────────────────
# ── yfinance 数据源（境外/港股）
# ──────────────────────────────────────────────────────────────────────────

def _backfill_yfinance_ticker(erp_code: str) -> bool:
    """用yfinance拉单个标的的分红+价格历史，计算股息率序列。已有本地缓存则跳过。"""
    if os.path.exists(_cache_path(erp_code)):
        return True

    ticker_symbol = YFINANCE_TICKERS.get(erp_code)
    if not ticker_symbol:
        print(f"⚠️ 标的 {erp_code} 未在 YFINANCE_TICKERS 中定义，跳过。")
        return False

    try:
        ticker = yf.Ticker(ticker_symbol)

        # 拉分红序列
        dividends = ticker.dividends
        if dividends is None or len(dividends) == 0:
            print(f"⚠️ 标的 {erp_code}({ticker_symbol}) 无分红数据，跳过。")
            return False

        # 拉历史价格（从10年前至今）
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=365 * BACKFILL_YEARS)
        hist = ticker.history(start=start_date, end=end_date)

        if hist is None or len(hist) == 0:
            print(f"⚠️ 标的 {erp_code}({ticker_symbol}) 无价格数据，跳过。")
            return False

        # 合并分红和价格，计算股息率
        # 按月聚合分红（通常每季度分一次，但为了对齐，按月底计算yield）
        df_list = []

        # 将分红重新索引到月末
        monthly_div = dividends.resample('M').sum()

        # 计算前12个月的滚动分红（TTM）
        monthly_div_ttm = monthly_div.rolling(window=12).sum()

        # 配对月末收盘价和分红
        for date in monthly_div_ttm.index:
            # 找该月或之前最近的交易日收盘价
            hist_up_to_date = hist[:date]
            if len(hist_up_to_date) == 0:
                continue

            last_close = hist_up_to_date['Close'].iloc[-1]
            ttm_div = monthly_div_ttm[date]

            if last_close > 0 and ttm_div > 0:
                dy = (ttm_div / last_close) * 100
                df_list.append({
                    "date": pd.Timestamp(date).tz_localize(None),
                    "dy": dy
                })

        if not df_list:
            print(f"⚠️ 标的 {erp_code}({ticker_symbol}) 无法计算股息率，跳过。")
            return False

        df = pd.DataFrame(df_list)
        df = df.sort_values("date").reset_index(drop=True)

        _save_cache(erp_code, df)
        print(
            f"✅ yfinance 历史回填完成：{erp_code}({ticker_symbol})（{len(df)}条，{df['date'].min().date()} ~ {df['date'].max().date()}）"
        )
        return True

    except Exception as e:
        print(f"⚠️ yfinance 拉取 {erp_code}({ticker_symbol}) 失败：{e}")
        return False

def _update_yfinance_ticker(erp_code: str) -> bool:
    """增量更新单个yfinance标的（追加最新一条数据）。"""
    ticker_symbol = YFINANCE_TICKERS.get(erp_code)
    if not ticker_symbol:
        return False

    try:
        ticker = yf.Ticker(ticker_symbol)
        dividends = ticker.dividends

        if dividends is None or len(dividends) == 0:
            return False

        # 重新计算最近一个月末的数据（可能有补报分红）
        monthly_div = dividends.resample('M').sum()
        monthly_div_ttm = monthly_div.rolling(window=12).sum()

        # 取最近一个月的数据
        if len(monthly_div_ttm) == 0:
            return False

        latest_date = monthly_div_ttm.index[-1]

        # 拉最近的价格
        hist = ticker.history(start=latest_date - timedelta(days=30), end=datetime.now())
        if len(hist) == 0:
            return False

        last_close = hist['Close'].iloc[-1]
        ttm_div = monthly_div_ttm.iloc[-1]

        if last_close <= 0 or ttm_div <= 0:
            return False

        dy = (ttm_div / last_close) * 100
        new_row = {"date": pd.Timestamp(latest_date).tz_localize(None), "dy": dy}

        # 加载已有缓存，追加新行
        existing = _load_cache(erp_code)
        if existing is not None and len(existing) > 0:
            # 如果最新日期已存在，则更新；否则追加
            existing = existing[existing["date"] != pd.Timestamp(latest_date).tz_localize(None)]

        new_df = pd.DataFrame([new_row])
        merged = pd.concat([existing, new_df], ignore_index=True) if existing is not None else new_df

        _save_cache(erp_code, merged)
        return True

    except Exception as e:
        print(f"⚠️ yfinance 增量更新 {erp_code}({ticker_symbol}) 失败：{e}")
        return False

# ──────────────────────────────────────────────────────────────────────────
# ── 缓存管理（通用）
# ──────────────────────────────────────────────────────────────────────────

def _cache_path(code: str) -> str:
    """缓存文件路径（A股和yfinance都用同一目录）。"""
    return os.path.join(CACHE_DIR, f"dyr_{code}.csv")

def _load_cache(code: str) -> pd.DataFrame | None:
    """加载本地缓存。"""
    if code in _dy_cache:
        return _dy_cache[code]

    path = _cache_path(code)
    if not os.path.exists(path):
        _dy_cache[code] = None
        return None

    df = pd.read_csv(path, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    _dy_cache[code] = df
    return df

def _save_cache(code: str, df: pd.DataFrame):
    """保存本地缓存。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    df = (
        df.sort_values("date")
        .drop_duplicates(subset="date", keep="last")
        .reset_index(drop=True)
    )
    df.to_csv(_cache_path(code), index=False)
    _dy_cache[code] = df

# ──────────────────────────────────────────────────────────────────────────
# ── 主入口
# ──────────────────────────────────────────────────────────────────────────

def ensure_dividend_data_fresh(
    index_codes: list[str] | None = None,
    ticker_codes: list[str] | None = None,
):
    """主入口：确保本地缓存存在且不超过UPDATE_INTERVAL_DAYS天没更新。

    参数：
    index_codes: A股指数代码列表（默认所有理杏仁支持的指数）
    ticker_codes: yfinance标的代码列表（默认所有yfinance支持的标的）

    在 erp_position.py 主流程开头调用一次即可，内部自己判断要不要真的发请求。
    """
    a_codes = index_codes or list(set(DIVIDEND_INDEX_CODES.values()))
    yf_codes = ticker_codes or list(YFINANCE_TICKERS.keys())

    # 第一步：给还没有本地缓存的指数/标的做一次性历史回填
    missing_a = [c for c in a_codes if not os.path.exists(_cache_path(DIVIDEND_INDEX_CODES.get(c, c)))]
    for c in missing_a:
        index_code = DIVIDEND_INDEX_CODES.get(c, c)
        _backfill_index(index_code)

    missing_yf = [c for c in yf_codes if not os.path.exists(_cache_path(c))]
    for c in missing_yf:
        _backfill_yfinance_ticker(c)

    # 第二步：判断距离上次批量增量更新是否已超过UPDATE_INTERVAL_DAYS天
    os.makedirs(CACHE_DIR, exist_ok=True)
    last_update = None
    if os.path.exists(_STATE_PATH):
        try:
            with open(_STATE_PATH) as f:
                last_update = datetime.fromisoformat(json.load(f)["last_update"])
        except Exception:
            last_update = None

    if last_update is not None and (datetime.now() - last_update).days < UPDATE_INTERVAL_DAYS:
        return # 距上次更新不到7天，跳过，不浪费配额

    # 第三步：批量增量更新
    # A股：用理杏仁 date 模式一次最多100个指数，18个一次搞定
    today = datetime.now().date().isoformat()
    payload = {
        "token": _get_token(),
        "stockCodes": a_codes,
        "date": today,
        "metricsList": ["dyr.mcw"],
    }
    records = _call_lixinger(payload)
    if records:
        by_code = {}
        for r in records:
            code = r.get("stockCode")
            dy = r.get("dyr.mcw")
            if code is None or dy is None:
                continue
            by_code.setdefault(code, []).append({
                "date": pd.to_datetime(r["date"]).tz_localize(None),
                "dy": dy * 100,
            })
        for code, rows in by_code.items():
            existing = _load_cache(code)
            new_rows_df = pd.DataFrame(rows)
            merged = (
                pd.concat([existing, new_rows_df], ignore_index=True)
                if existing is not None
                else new_rows_df
            )
            _save_cache(code, merged)
        print(f"✅ 股息率周更完成（理杏仁）：{len(by_code)}个指数，{today}")

    # yfinance：逐个更新（因为需要拉最新价格和分红）
    success_count = 0
    for c in yf_codes:
        if _update_yfinance_ticker(c):
            success_count += 1

    if success_count > 0:
        print(f"✅ 股息率周更完成（yfinance）：{success_count}个标的，{today}")

    with open(_STATE_PATH, "w") as f:
        json.dump({"last_update": datetime.now().isoformat()}, f)

# ──────────────────────────────────────────────────────────────────────────
# ── 分位计算 & 报告生成
# ──────────────────────────────────────────────────────────────────────────

def compute_dividend_percentile(code: str) -> dict:
    """计算当前股息率在本地缓存历史序列中的分位。股息率越高越便宜，
    分位=历史上有多少比例的值低于当前值，分位越高=越便宜。"""
    df = _load_cache(code)
    if df is None or len(df) == 0:
        return {"has_data": False}

    dy_series = df["dy"]
    cur_dy = dy_series.iloc[-1]
    cur_date = df["date"].iloc[-1]

    percentile = (dy_series < cur_dy).mean()
    zone = next(label for threshold, label in _ZONE_THRESHOLDS if percentile >= threshold)

    return {
        "has_data": True,
        "code": code,
        "cur_dy": cur_dy,
        "cur_date": cur_date,
        "percentile": percentile,
        "zone": zone,
        "n": len(dy_series),
        "p10": dy_series.quantile(0.10),
        "p25": dy_series.quantile(0.25),
        "p50": dy_series.quantile(0.50),
        "p75": dy_series.quantile(0.75),
        "p90": dy_series.quantile(0.90),
        "series": dy_series,
    }

def build_dividend_yield_block(erp_code: str) -> str:
    """股息率估值锚区块，风格仿照 build_shiller_block()。
    支持A股指数和yfinance标的。
    erp_code 不支持（或本地无缓存数据）时返回空字符串，不影响主报告。"""

    # 确定是A股指数还是yfinance标的
    code = None
    if erp_code in DIVIDEND_INDEX_CODES:
        code = DIVIDEND_INDEX_CODES[erp_code]
    elif erp_code in YFINANCE_TICKERS:
        code = erp_code
    else:
        return ""

    result = compute_dividend_percentile(code)
    if not result["has_data"]:
        return "\n> ⚠️ 未能获取股息率数据（本地无缓存，请先调用 ensure_dividend_data_fresh()），跳过股息率估值锚分析。\n"

    date_str = (
        result["cur_date"].strftime("%Y-%m-%d")
        if hasattr(result["cur_date"], "strftime")
        else str(result["cur_date"])
    )
    sample_warning = (
        f"\n> ⚠️ 历史样本仅 {result['n']} 个交易日，参考时请注意统计可靠性。"
        if result["n"] < 250
        else ""
    )

    # 标注数据源
    if erp_code in YFINANCE_TICKERS:
        source_label = "yfinance"
    else:
        source_label = "理杏仁（市值加权口径）"

    block = f"""
---
### 股息率估值锚（{source_label}，周更）

> 方法：股息率越高代表相对越便宜。当前值在本地缓存历史序列中的分位，
> 分位越高 = 当前股息率历史上越少见地高 = 估值越便宜。
> 数据日期：**{date_str}**（周频更新，非日频）
{sample_warning}

| 指标 | 数值 | 说明 |
|:-----|-----:|:-----|
| 当前股息率 | **{result['cur_dy']:.2f}%** | {source_label} |
| 历史分位 | **P{result['percentile']*100:.0f}** | **{result['zone']}** |

| 历史股息率分布 | 数值 |
|:--------------|-----:|
| P90（历史股息率高位） | {result['p90']:.2f}% |
| P75 | {result['p75']:.2f}% |
| P50（中位数） | {result['p50']:.2f}% |
| P25 | {result['p25']:.2f}% |
| P10（历史股息率低位） | {result['p10']:.2f}% |
"""
    return block

# ── 本地测试 ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 80)
    print("股息率数据更新 & 报告生成")
    print("=" * 80)

    # 从config_loader获取所有指数
    from config_loader import INDICES_LIST, get_all_codes

    # 更新所有指数的数据（A股 + yfinance）
    ensure_dividend_data_fresh()

    # 为所有指数生成报告块
    all_codes = get_all_codes()
    for code in all_codes:
        block = build_dividend_yield_block(code)
        if block:
            print(block)
            print("\n")
