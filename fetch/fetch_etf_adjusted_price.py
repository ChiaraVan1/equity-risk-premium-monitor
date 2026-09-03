"""生成 ETF 前复权价格宽表，专供均线、回撤和止盈止损使用。"""

import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_loader import ETF_LIST


OUTPUT_PATH = "./data/etf_price_adj.csv"
LOOKBACK_CALENDAR_DAYS = 400  # 风险模块最长使用120个交易日，留足交易日/节假日余量
MAX_RETRIES = 3


def fetch_one(erp_code: str, etf_code: str, start_date: str, end_date: str):
    """通过 AKShare 的东方财富 ETF 接口取得前复权行情。"""
    symbol = etf_code.split(".")[0]
    df = ak.fund_etf_hist_em(
        symbol=symbol,
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust="qfq",
    )
    if df is None or df.empty or "日期" not in df.columns or "收盘" not in df.columns:
        raise ValueError("前复权行情为空或缺少日期/收盘字段")
    result = df[["日期", "收盘"]].copy()
    result["日期"] = pd.to_datetime(result["日期"], errors="coerce")
    result["收盘"] = pd.to_numeric(result["收盘"], errors="coerce")
    series = result.dropna().drop_duplicates("日期", keep="last").set_index("日期")["收盘"]
    series = series.sort_index()
    series.name = erp_code
    if len(series) < 5:
        raise ValueError(f"前复权行情样本不足: {len(series)}")
    return series


def fetch_one_with_retry(erp_code: str, etf_code: str, start_date: str, end_date: str):
    """本地串行抓取并重试；数据始终来自 AKShare qfq，不做跳变折算。"""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fetch_one(erp_code, etf_code, start_date, end_date)
        except Exception as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                wait_seconds = attempt * 3
                print(
                    f"   ↻ [{erp_code}/{etf_code}] 第 {attempt} 次失败，"
                    f"{wait_seconds} 秒后重试: {exc}"
                )
                time.sleep(wait_seconds)
    raise last_error


def main():
    now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    start_date = (now - timedelta(days=LOOKBACK_CALENDAR_DAYS)).strftime("%Y%m%d")
    end_date = now.strftime("%Y%m%d")

    old = pd.DataFrame()
    if os.path.exists(OUTPUT_PATH):
        try:
            old = pd.read_csv(OUTPUT_PATH, index_col=0, parse_dates=True)
        except Exception as exc:
            print(f"⚠️ 读取旧前复权缓存失败，将重建: {exc}")

    fetched = []
    failed = []
    # 与本地 simple_etf_metrics.py 一致使用串行请求，避免东财限流/断连。
    for code, etf_code in ETF_LIST:
        try:
            series = fetch_one_with_retry(code, etf_code, start_date, end_date)
            fetched.append(series)
            print(f"✅ [{code}/{etf_code}] 前复权价格 {len(series)} 条")
        except Exception as exc:
            failed.append(code)
            print(f"⚠️ [{code}/{etf_code}] 前复权价格失败，保留旧缓存: {exc}")
        time.sleep(0.5)

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
