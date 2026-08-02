"""
dividend_yield.py
──────────────────────────────────────────────────────────────────────────────
指数股息率估值锚模块（理杏仁数据源，本地缓存 + 周频增量更新）

数据源: 理杏仁开放平台「指数基本面数据API」
        POST https://open.lixinger.com/api/cn/index/fundamental
        取字段: dyr.mcw（股息率，市值加权口径）

── 为什么不用中证指数官网数据 ──────────────────────────────────────────────
中证指数官网的股息率字段（IndexDYRatio2，调整股本口径）只有约20个交易日的
滚动窗口，没有可查询的完整历史，无法用来算历史分位。
理杏仁能提供最长10年的完整历史，但口径是市值加权（dyr.mcw），和中证官方的
"调整股本"口径不是同一套计算方法论——两者数值不同（实测约有0.4-0.5个百分点
的系统性差异，理杏仁mcw口径更接近中证的"股息率1/总股本"而非"股息率2/调整股本"）。
【结论】历史序列和后续增量更新必须用同一个数据源，不能中途切换，否则会在
切换的那一天造成人为的数值断层，污染历史分位计算。本模块统一全程使用理杏仁，
中证官网数据不接入这条时间序列，最多只能作为独立的旁证参考。

── 更新频率：周更，不是日更 ─────────────────────────────────────────────
股息率是慢变量（不像ERP/PE需要日频捕捉短期波动），日频更新对分位判断没有
实质帮助，却会快速耗尽API配额。免费额度1000次的账算法：
  - 初始回填：每个指数一次性拉满10年历史，18个指数 = 18次（一次性消耗）
  - 之后每周批量更新一次：date模式下最多100个指数代码可以塞进同一次请求，
    18个指数一次请求全部更新完，52次/年
  - 剩余额度可用年数 ≈ (1000-18)/52 ≈ 18.9年，足够长期使用
本模块内置节流：距离上次更新不足7天时，update_weekly() 会跳过，不浪费配额。

使用方式：
    在 erp_position.py 中：
        from dividend_yield import ensure_dividend_data_fresh, build_dividend_yield_block

        ensure_dividend_data_fresh()             # 每次跑主程序时调用一次，
                                                  # 内部会自己判断要不要真的发请求
        block = build_dividend_yield_block(code)  # 每个标的调用
──────────────────────────────────────────────────────────────────────────────
"""

import os
import json
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

# ── 配置 ──────────────────────────────────────────────────────────────────
LIXINGER_URL = "https://open.lixinger.com/api/cn/index/fundamental"
LIXINGER_TOKEN_ENV = "LIXINGER_TOKEN"

CACHE_DIR = "./data/dividend_yield"
_STATE_PATH = os.path.join(CACHE_DIR, "_last_update.json")

BACKFILL_YEARS = 10           # 单次日期区间查询理杏仁上限就是10年
UPDATE_INTERVAL_DAYS = 7      # 周更，不做日更，见上方说明

_API_MAX_RETRIES = 3
_API_RETRY_BASE_DELAY = 5     # 秒，指数退避：5s, 10s, 20s

# key = erp_code（与 erp_position.py / etf_metrics.py 里的标的代码一致）
# value = 传给理杏仁 stockCodes 的指数代码
# 仅列出中证/国证A股指数，SPY/QQQ/EWQ/EWJ/EWG/EEM/HSTECH等境外港股标的
# 理杏仁的cn/index/fundamental接口不覆盖，自动跳过，不影响主报告。
DIVIDEND_INDEX_CODES = {
    "000300": "000300",  # 沪深300
    "000688": "000688",  # 科创50
    "000922": "000922",  # 中证红利
    "000015": "000015",  # 红利指数
    "399989": "399989",  # 中证医疗
    "931071": "931071",  # 人工智能
    "000069": "000069",  # 深证消费
    "930781": "930781",  # 中证影视
    "399967": "399967",  # 中证军工
    "931066": "931066",  # 军工龙头
    "930598": "930598",  # 稀土产业
    "931637": "931637",  # 港股通互联网
    "000819": "000819",  # 有色金属
    "950125": "950125",  # 半导体材料设备
    "399975": "399975",  # 证券公司
    "399986": "399986",  # 中证银行
    "930633": "930633",  # 中证旅游
    "931946": "931946",  # 畜牧养殖
    "980032": "980032",  # 新能源车电池
}

_ZONE_THRESHOLDS = [
    (0.90, "🟢 股息率历史罕见地高"),
    (0.75, "🟢 股息率偏高"),
    (0.50, "🟡 股息率中性偏高"),
    (0.25, "🟠 股息率中性偏低"),
    (0.10, "🔴 股息率偏低"),
    (0.00, "🚨 股息率历史罕见地低"),
]

_dy_cache = {}  # 内存缓存，避免同一进程内重复读盘


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
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])
        except Exception as e:
            last_exc = e
            if attempt < _API_MAX_RETRIES - 1:
                time.sleep(_API_RETRY_BASE_DELAY * (2 ** attempt))
                continue
    print(f"⚠️ 理杏仁接口请求失败（已重试{_API_MAX_RETRIES}次）：{last_exc}")
    return None


def _cache_path(index_code: str) -> str:
    return os.path.join(CACHE_DIR, f"dyr_{index_code}.csv")


def _load_cache(index_code: str) -> pd.DataFrame | None:
    if index_code in _dy_cache:
        return _dy_cache[index_code]

    path = _cache_path(index_code)
    if not os.path.exists(path):
        _dy_cache[index_code] = None
        return None

    df = pd.read_csv(path, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    _dy_cache[index_code] = df
    return df


def _save_cache(index_code: str, df: pd.DataFrame):
    os.makedirs(CACHE_DIR, exist_ok=True)
    df = df.sort_values("date").drop_duplicates(subset="date", keep="last").reset_index(drop=True)
    df.to_csv(_cache_path(index_code), index=False)
    _dy_cache[index_code] = df


def _parse_records(records: list) -> pd.DataFrame:
    """理杏仁返回记录形如 {'date': '...', 'dyr.mcw': 0.0272..., 'stockCode': '...'}。
    字段名里带点号，DataFrame直接按key取列即可。"""
    rows = []
    for r in records:
        dy = r.get("dyr.mcw")
        if dy is None:
            continue
        rows.append({"date": pd.to_datetime(r["date"]).tz_localize(None), "dy": dy * 100})
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
    print(f"✅ 股息率历史回填完成：{index_code}（{len(df)}条，{df['date'].min().date()} ~ {df['date'].max().date()}）")
    return True


def ensure_dividend_data_fresh(index_codes: list[str] | None = None):
    """主入口：确保本地缓存存在且不超过UPDATE_INTERVAL_DAYS天没更新。
    在erp_position.py主流程开头调用一次即可，内部自己判断要不要真的发请求，
    不会每次跑都消耗API配额。"""
    codes = index_codes or list(set(DIVIDEND_INDEX_CODES.values()))

    # 第一步：给还没有本地缓存的指数做一次性历史回填
    missing = [c for c in codes if not os.path.exists(_cache_path(c))]
    for c in missing:
        _backfill_index(c)

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
        return  # 距上次更新不到7天，跳过，不浪费配额

    # 第三步：批量增量更新——date模式一次最多100个指数代码，18个一次搞定，只耗1次配额
    today = datetime.now().date().isoformat()
    payload = {
        "token": _get_token(),
        "stockCodes": codes,
        "date": today,
        "metricsList": ["dyr.mcw"],
    }
    records = _call_lixinger(payload)
    if records:
        new_df = _parse_records(records)
        # 按stockCode拆分，分别追加进各自的本地缓存
        by_code = {}
        for r in records:
            code = r.get("stockCode")
            dy = r.get("dyr.mcw")
            if code is None or dy is None:
                continue
            by_code.setdefault(code, []).append({
                "date": pd.to_datetime(r["date"]).tz_localize(None), "dy": dy * 100
            })
        for code, rows in by_code.items():
            existing = _load_cache(code)
            new_rows_df = pd.DataFrame(rows)
            merged = pd.concat([existing, new_rows_df], ignore_index=True) if existing is not None else new_rows_df
            _save_cache(code, merged)
        print(f"✅ 股息率周更完成：{len(by_code)}个指数，{today}")

    with open(_STATE_PATH, "w") as f:
        json.dump({"last_update": datetime.now().isoformat()}, f)


def compute_dividend_percentile(index_code: str) -> dict:
    """计算当前股息率在本地缓存历史序列中的分位。股息率越高越便宜，
    分位=历史上有多少比例的值低于当前值，分位越高=越便宜。"""
    df = _load_cache(index_code)
    if df is None or len(df) == 0:
        return {"has_data": False}

    dy_series = df["dy"]
    cur_dy = dy_series.iloc[-1]
    cur_date = df["date"].iloc[-1]

    percentile = (dy_series < cur_dy).mean()
    zone = next(label for threshold, label in _ZONE_THRESHOLDS if percentile >= threshold)

    return {
        "has_data": True,
        "index_code": index_code,
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
        "series": dy_series,  # 供 calc_odds() 直接复用，股息率天然"越高越便宜"，无需取倒数
    }


def build_dividend_yield_block(erp_code: str) -> str:
    """股息率估值锚区块，风格仿照 build_shiller_block()。
    erp_code 不在 DIVIDEND_INDEX_CODES 中（如SPY/QQQ/HSTECH等境外港股标的）
    或本地无缓存数据时返回空字符串，不影响主报告。"""
    index_code = DIVIDEND_INDEX_CODES.get(erp_code)
    if index_code is None:
        return ""

    result = compute_dividend_percentile(index_code)
    if not result["has_data"]:
        return "\n> ⚠️ 未能获取股息率数据（本地无缓存，请先调用 ensure_dividend_data_fresh()），跳过股息率估值锚分析。\n"

    date_str = result["cur_date"].strftime("%Y-%m-%d") if hasattr(result["cur_date"], "strftime") else str(result["cur_date"])
    sample_warning = f"\n> ⚠️ 历史样本仅 {result['n']} 个交易日，参考时请注意统计可靠性。" if result["n"] < 250 else ""

    block = f"""
---
### 股息率估值锚（理杏仁数据，市值加权口径，周更）

> 方法：股息率越高代表相对越便宜。当前值在本地缓存历史序列中的分位，
> 分位越高 = 当前股息率历史上越少见地高 = 估值越便宜。
> 数据日期：**{date_str}**（周频更新，非日频）
{sample_warning}

| 指标 | 数值 | 说明 |
|:-----|-----:|:-----|
| 当前股息率 | **{result['cur_dy']:.2f}%** | 理杏仁（dyr.mcw，市值加权口径） |
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


# ── 本地测试 ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ensure_dividend_data_fresh()
    for erp_code in ["000300", "000922", "000015"]:
        block = build_dividend_yield_block(erp_code)
        if block:
            print(block)
            print("\n" + "=" * 80 + "\n")
