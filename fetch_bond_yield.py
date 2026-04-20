import akshare as ak
import requests
import pandas as pd
import os
import time
from datetime import datetime
from io import StringIO

def fetch_us_bond_history():
    """美国10年期国债收益率，FRED DGS10，日频，1962年至今"""
    API_KEY = "a8ce66c09bbcedfb9e33de739a0dcbfb"
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": "DGS10",  # 这个series_id是真实存在的
        "api_key": API_KEY,
        "file_type": "json",
        "observation_start": "2005-01-01",
    }
    r = requests.get(url, params=params)
    data = r.json()
    df = pd.DataFrame(data["observations"])[["date", "value"]]
    df.columns = ["Date", "Bond_Yield_10Y"]
    df["Date"] = pd.to_datetime(df["Date"])
    df["Bond_Yield_10Y"] = pd.to_numeric(df["Bond_Yield_10Y"], errors="coerce") / 100
    df = df.dropna()
    return df.sort_values("Date").reset_index(drop=True)

def fetch_spy_pe_history():
    """月频历史 + 今日实时值合并"""
    # 1. multpl 月频历史
    url = "https://www.multpl.com/s-p-500-pe-ratio/table/by-month"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    r = requests.get(url, headers=headers)
    df = pd.read_html(StringIO(r.text))[0]
    df.columns = ["Date", "PE"]
    df["Date"] = pd.to_datetime(df["Date"])
    df["PE"] = pd.to_numeric(df["PE"], errors="coerce")
    # 保留每月1号 + 月内实时点（最后一行）
    df = df.sort_values("Date").reset_index(drop=True)

    # 2. worldperatio 今日实时值
    try:
        url2 = "https://worldperatio.com/major-stock-index-pe-ratios"
        r2 = requests.get(url2, headers=headers)
        df2 = pd.read_html(StringIO(r2.text))[0]
        df2.columns = ['_'.join(c).strip() for c in df2.columns]
        df2 = df2.rename(columns={
            'Unnamed: 1_level_0_Unnamed: 1_level_1': 'symbol',
            'Unnamed: 3_level_0_P/E Ratio▾': 'PE',
        })
        spy_pe = df2[df2['symbol'] == 'SPY']['PE'].values[0]
        today_row = pd.DataFrame({
            "Date": [pd.Timestamp(datetime.now().date())],
            "PE": [float(spy_pe)]
        })
        df = pd.concat([df, today_row], ignore_index=True)
        df = df.drop_duplicates(subset=['Date'], keep='last')
        print(f"   ✓ 今日SPY PE: {spy_pe}")
    except Exception as e:
        print(f"   ⚠️ 今日PE获取失败，仅用历史数据: {e}")

    return df.sort_values("Date").reset_index(drop=True)

def process_and_save(pe_df, bond_df, code, name, currency='CNY', bond_code='CN10Y'):
    if pe_df.empty:
        print(f"   ⚠️ {name} 数据为空，跳过合并")
        return

    pe_df['Date'] = pd.to_datetime(pe_df['Date'])
    merged = pd.merge(bond_df, pe_df, on='Date', how='outer').sort_values('Date')
    
    # 美股PE是月频，用前向填充让每个交易日都有PE值
    if currency == 'USD':
        merged['PE'] = merged['PE'].ffill()
    
    merged['ERP'] = (1 / merged['PE']) - merged['Bond_Yield_10Y']
    merged['IndexCode'] = code
    merged['IndexName'] = name
    merged['Currency'] = currency
    merged['BondCode'] = bond_code

    output_path = f"./data/erp_{code}.csv"
    merged.to_csv(output_path, index=False, encoding='utf-8-sig')

    valid_count = merged['ERP'].notna().sum()
    print(f"   ✅ {name} 处理完成！总日期跨度: {len(merged)} 天，有效ERP样本: {valid_count} 天")
    if valid_count > 0:
        latest_valid = merged[merged['ERP'].notna()].iloc[-1]
        print(f"      最新有效ERP: {latest_valid['ERP']:.2%} ({latest_valid['Date'].date()})")

def main():
    os.makedirs('./data', exist_ok=True)

    # ── A股 ──────────────────────────────────────────
    print("--- 1. 获取中国10Y国债历史 ---")
    cn_bond_list = []
    current_year = datetime.now().year
    for y in range(2006, current_year + 1):
        try:
            s_date = f"{y}0101"
            e_date = datetime.now().strftime("%Y%m%d") if y == current_year else f"{y}1231"
            df = ak.bond_china_yield(start_date=s_date, end_date=e_date)
            df = df[df['曲线名称'] == '中债国债收益率曲线']
            cn_bond_list.append(df[['日期', '10年']])
            print(f"   ✓ {y} 年国债已获取")
            time.sleep(0.5)
        except:
            print(f"   ✗ {y} 年国债跳过")

    cn_bond_df = pd.concat(cn_bond_list)
    cn_bond_df.columns = ['Date', 'Bond_Yield_10Y']
    cn_bond_df['Date'] = pd.to_datetime(cn_bond_df['Date'])
    cn_bond_df['Bond_Yield_10Y'] = cn_bond_df['Bond_Yield_10Y'] / 100

    print("\n--- 2. 获取A股指数PE历史 ---")
    cn_indices = {
        "000300": "沪深300",
        "000688": "科创50",
        "000922": "中证红利",
        "399989": "中证医疗",
        "931071": "人工智能",
    }
    for code, name in cn_indices.items():
        try:
            pe_csi = ak.stock_zh_index_hist_csindex(
                symbol=code,
                start_date="20050408",
                end_date="20260416"
            )[['日期', '滚动市盈率']]
            pe_csi.columns = ['Date', 'PE']
            process_and_save(pe_csi, cn_bond_df, code, name, currency='CNY', bond_code='CN10Y')
            time.sleep(1)
        except Exception as e:
            print(f"   ❌ {name} 失败: {e}")

    # ── 美股 ──────────────────────────────────────────
    print("\n--- 3. 获取美国10Y国债历史 ---")
    us_bond_df = fetch_us_bond_history()
    print(f"   ✓ 美债数据: {us_bond_df['Date'].min().date()} ~ {us_bond_df['Date'].max().date()}")

    print("\n--- 4. 获取美股指数PE历史 ---")
    spy_pe_df = fetch_spy_pe_history()
    process_and_save(spy_pe_df, us_bond_df, "SPY", "S&P 500", currency='USD', bond_code='US10Y')

if __name__ == "__main__":
    main()
