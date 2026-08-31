"""
一次性脚本：补齐 erp_QQQ.csv 中 2026-08-03 ~ 2026-08-18 的缺失数据。
背景：config.json 里 QQQ 的 pe_source 字段命名不匹配（已修复），导致这段时间
process_incremental() 从未被调用，这几天的 PE/Bond_Yield_10Y/ERP 完全没有写入。

数据来源：
  - PE：用户从 GuruFocus Nasdaq-100 PE 历史页面手工核对提供
  - Bond_Yield_10Y：FRED API 实时查询 DGS10（US10Y），确保和自动化流程口径一致

用法：把本脚本放在仓库根目录跑（需要能访问 data/erp_QQQ.csv），
      或者把 CSV_PATH 改成你本地实际路径。
"""
import pandas as pd
import requests

CSV_PATH = "/Users/chiaravan/equity-risk-premium-monitor/data/erp_QQQ.csv"   # 按你实际路径调整
FRED_API_KEY = "a8ce66c09bbcedfb9e33de739a0dcbfb"

# ── 1. 用户提供的 QQQ PE（GuruFocus，日度）───────────────────────────────
qqq_pe_manual = {
    "2026-08-03": 27.90,
    "2026-08-04": 28.82,
    "2026-08-05": 28.59,
    "2026-08-06": 28.48,
    "2026-08-07": 28.81,
    "2026-08-10": 28.72,
    "2026-08-11": 28.62,
    "2026-08-12": 28.83,
    "2026-08-13": 29.16,
    "2026-08-14": 29.13,
    "2026-08-17": 29.08,
    "2026-08-18": 28.59,
}

# ── 2. 从 FRED 补拉这段时间的 US10Y（和自动化流程用同一个 series/key）──────
def fetch_fred_bond(start_date_str):
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": "DGS10",
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "observation_start": start_date_str,
    }
    r = requests.get(url, params=params)
    data = r.json()
    if "observations" not in data:
        raise ValueError(f"FRED 错误: {data.get('error_message')}")
    df = pd.DataFrame(data["observations"])[["date", "value"]]
    df.columns = ["Date", "Bond_Yield_10Y"]
    df["Date"] = pd.to_datetime(df["Date"])
    df["Bond_Yield_10Y"] = pd.to_numeric(df["Bond_Yield_10Y"], errors="coerce") / 100
    return df.dropna().set_index("Date")["Bond_Yield_10Y"]


def main():
    bond_s = fetch_fred_bond("2026-08-01")
    print(f"FRED US10Y 补拉到 {len(bond_s)} 条：")
    print(bond_s)

    old = pd.read_csv(CSV_PATH)
    old["Date"] = pd.to_datetime(old["Date"])
    old = old.set_index("Date").sort_index()

    new_rows = []
    for date_str, pe in qqq_pe_manual.items():
        d = pd.Timestamp(date_str)
        if d in old.index:
            print(f"⚠️  {date_str} 已存在于原文件，跳过（不覆盖）")
            continue
        rf = bond_s.get(d)
        if rf is None:
            # 找不到当天国债数据（比如美股交易日但国债市场休市），用最近前一天的值兜底
            rf = bond_s[bond_s.index <= d].iloc[-1] if len(bond_s[bond_s.index <= d]) else float("nan")
            print(f"   {date_str} 国债无当日值，使用最近值兜底: {rf}")
        erp = (1 / pe) - rf
        new_rows.append({
            "Date": d, "Bond_Yield_10Y": rf, "PE": pe, "ERP": erp,
            "IndexCode": "QQQ", "IndexName": "Nasdaq 100",
            "Currency": "USD", "BondCode": "US10Y",
        })

    new_df = pd.DataFrame(new_rows).set_index("Date")
    combined = pd.concat([old, new_df]).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]

    combined = combined.reset_index()
    combined.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")

    print(f"\n✅ 补数完成，新增 {len(new_rows)} 行，文件总行数: {len(combined)}")
    print(combined.tail(15).to_string(index=False))


if __name__ == "__main__":
    main()