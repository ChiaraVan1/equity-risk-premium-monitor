"""生成 ETF 前复权价格宽表，专供均线、回撤和止盈止损使用。"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_loader import ETF_LIST


OUTPUT_PATH = "./data/etf_price_adj.csv"
LOOKBACK_CALENDAR_DAYS = 400  # 风险模块最长使用120个交易日，留足交易日/节假日余量
MAX_WORKERS = 3


def fetch_one(erp_code: str, etf_code: str, start_date: str, end_date: str):
    """东财 ETF 行情支持 qfq；每次重拉完整窗口以吸收最新复权因子变化。"""
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
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(fetch_one, code, etf_code, start_date, end_date): (code, etf_code)
            for code, etf_code in ETF_LIST
        }
        for future in as_completed(futures):
            code, etf_code = futures[future]
            try:
                series = future.result()
                fetched.append(series)
                print(f"✅ [{code}/{etf_code}] 前复权价格 {len(series)} 条")
            except Exception as exc:
                failed.append(code)
                print(f"⚠️ [{code}/{etf_code}] 前复权价格失败，保留旧缓存: {exc}")
            time.sleep(0.1)

    if not fetched:
        raise SystemExit("所有 ETF 前复权价格均获取失败，拒绝覆盖缓存")

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
