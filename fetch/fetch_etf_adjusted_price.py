"""通过 QuantDash 生成 ETF 前复权价格宽表，专供价格风险信号使用。"""

import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from quantdash import QuantDash

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_loader import ETF_LIST


OUTPUT_PATH = "./data/etf_price_adj.csv"
LOOKBACK_CALENDAR_DAYS = 400  # 风险模块最长使用120个交易日，留足交易日/节假日余量
MAX_RETRIES = 3
REQUESTS_PER_MINUTE = 9
QUANTDASH_ENV_PATH = os.path.expanduser(
    "~/.config/equity-risk-premium-monitor/quantdash.env"
)


def load_local_api_key():
    """读取仓库外的本地 Key；不依赖 launchd 继承交互式 shell 环境。"""
    api_key = os.environ.get("QUANTDASH_API_KEY", "").strip()
    if api_key:
        return api_key

    try:
        with open(QUANTDASH_ENV_PATH, encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                if name.strip() == "QUANTDASH_API_KEY":
                    return value.strip().strip("'\"")
    except FileNotFoundError:
        pass

    raise SystemExit(
        "缺少 QUANTDASH_API_KEY。请写入环境变量，或写入本地私有文件 "
        f"{QUANTDASH_ENV_PATH}（该文件位于仓库外，不会提交 GitHub）"
    )


def fetch_one_with_retry(client, erp_code: str, etf_code: str, start_time: int, end_time: int):
    """单标的请求；按账户限流窗口串行节流并复用 SDK 重试。"""
    data = client.klines.get(
        etf_code,
        period="1d",
        count=LOOKBACK_CALENDAR_DAYS,
        start_time=start_time,
        end_time=end_time,
        adjust="forward",
    )
    return series_from_kline(erp_code, data)


def series_from_kline(erp_code: str, data):
    """将 QuantDash 紧凑 K 线响应转换为日期索引的收盘序列。"""
    if not data or "timestamp" not in data or "close" not in data:
        raise ValueError("QuantDash 前复权行情为空或缺少 timestamp/close 字段")
    result = pd.DataFrame({"timestamp": data["timestamp"], "close": data["close"]})
    result["trade_date"] = pd.to_datetime(result["timestamp"], unit="ms", utc=True).dt.tz_convert(
        "Asia/Shanghai"
    ).dt.tz_localize(None).dt.normalize()
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    series = result.dropna().drop_duplicates("trade_date", keep="last").set_index("trade_date")["close"]
    series = series.sort_index()
    series.name = erp_code
    if len(series) < 5:
        raise ValueError(f"前复权行情样本不足: {len(series)}")
    return series


def main():
    now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    start_at = now - timedelta(days=LOOKBACK_CALENDAR_DAYS)
    shanghai = ZoneInfo("Asia/Shanghai")
    start_time = int(start_at.replace(tzinfo=shanghai).timestamp() * 1000)
    end_time = int(now.replace(tzinfo=shanghai).timestamp() * 1000)

    old = pd.DataFrame()
    if os.path.exists(OUTPUT_PATH):
        try:
            old = pd.read_csv(OUTPUT_PATH, index_col=0, parse_dates=True)
        except Exception as exc:
            print(f"⚠️ 读取旧前复权缓存失败，将重建: {exc}")

    fetched = []
    failed = []
    # SDK 内置连接错误、超时及 5xx 重试；本地按账户限流窗口串行请求。
    with QuantDash(api_key=load_local_api_key(), max_retries=MAX_RETRIES) as client:
        window_started = time.monotonic()
        requests_in_window = 0
        for code, etf_code in ETF_LIST:
            if requests_in_window >= REQUESTS_PER_MINUTE:
                wait_seconds = max(0, 61 - (time.monotonic() - window_started))
                if wait_seconds:
                    print(f"⏳ QuantDash 达到本地限流阈值，等待 {wait_seconds:.0f} 秒")
                    time.sleep(wait_seconds)
                window_started = time.monotonic()
                requests_in_window = 0
            requests_in_window += 1
            try:
                series = fetch_one_with_retry(client, code, etf_code, start_time, end_time)
                fetched.append(series)
                print(f"✅ [{code}/{etf_code}] QuantDash 前复权价格 {len(series)} 条")
            except Exception as exc:
                failed.append(code)
                print(f"⚠️ [{code}/{etf_code}] QuantDash 前复权价格失败，保留旧缓存: {exc}")

    if not fetched:
        raise SystemExit("所有 ETF 前复权价格均获取失败，拒绝覆盖缓存")

    missing_without_cache = [code for code in failed if code not in old.columns]
    if missing_without_cache:
        raise SystemExit(
            "首次建库仍缺少前复权列，拒绝写入不完整缓存: "
            + ", ".join(missing_without_cache)
        )

    new = pd.concat(fetched, axis=1).sort_index()
    # 成功列用完整400天窗口替换；失败列保留旧缓存，避免局部接口失败删列。
    successful_codes = [series.name for series in fetched]
    kept_old = old.drop(columns=successful_codes, errors="ignore")
    combined = pd.concat([kept_old, new], axis=1).sort_index()
    combined.index.name = "date"
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    combined.to_csv(OUTPUT_PATH, encoding="utf-8-sig")
    print(f"✅ 已保存 {OUTPUT_PATH}；成功 {len(fetched)}，失败 {len(failed)}")


if __name__ == "__main__":
    main()
