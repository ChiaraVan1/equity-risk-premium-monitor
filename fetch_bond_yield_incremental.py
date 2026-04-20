import akshare as ak
import requests
import pandas as pd
import os
import time
from datetime import datetime, timedelta
from io import StringIO

def process_incremental(new_pe_df, new_bond_df, code, name, currency='CNY', bond_code='CN10Y'):
    file_path = f"./data/erp_{code}.csv"

    new_pe_df['Date'] = pd.to_datetime(new_pe_df['Date'])
    new_data = pd.merge(new_bond_df, new_pe_df, on='Date', how='outer').sort_values('Date')
    
    # 美股PE月频，前向填充
    if currency == 'USD':
        new_data['PE'] = new_data['PE'].ffill()

    new_data['ERP'] = (1 / new_data['PE']) - new_data['Bond_Yield_10Y']
    new_data['IndexCode'] = code
    new_data['IndexName'] = name
    new_data['Currency'] = currency
    new_data['BondCode'] = bond_code

    if os.path.exists(file_path):
        old_data = pd.read_csv(file_path)
        old_data['Date'] = pd.to_datetime(old_data['Date'])
        combined = pd.concat([old_data, new_data], ignore_index=True)
        combined = combined.drop_duplicates(subset=['Date'], keep='last').sort_values('Date')
        # 合并后再做一次ffill，确保历史衔接处没有空值
        if currency == 'USD':
            combined['PE'] = combined['PE'].ffill()
            combined['ERP'] = (1 / combined['PE']) - combined['Bond_Yield_10Y']
        action = "增量更新"
    else:
        combined = new_data
        action = "全新创建"

    combined.to_csv(file_path, index=False, encoding='utf-8-sig')
    valid_now = combined['ERP'].notna().sum()
    print(f"   ✅ {name} {action}成功！总记录: {len(combined)} 天，有效ERP: {valid_now} 天")

def fetch_us_pe_today():
    """从 worldperatio 抓当日各指数 PE"""
    url = "https://worldperatio.com/major-stock-index-pe-ratios"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    r = requests.get(url, headers=headers)
    df = pd.read_html(StringIO(r.text))[0]
    df.columns = ['_'.join(c).strip() for c in df.columns]
    df = df.rename(columns={
        'Unnamed: 1_level_0_Unnamed: 1_level_1': 'symbol',
        'Unnamed: 3_level_0_P/E Ratio▾': 'PE',
    })
    df['PE'] = pd.to_numeric(df['PE'], errors='coerce')
    df['Date'] = pd.Timestamp(datetime.now().date())
    return df[['symbol', 'Date', 'PE']]

def main():
    os.makedirs('./data', exist_ok=True)

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    print(f"--- 1. 抓取增量数据 [{start_date} -> {end_date}] ---")

    # ── A股国债 ───────────────────────────────────────
    try:
        bond_df = ak.bond_china_yield(start_date=start_date, end_date=end_date)
        bond_df = bond_df[bond_df['曲线名称'] == '中债国债收益率曲线'][['日期', '10年']]
        bond_df.columns = ['Date', 'Bond_Yield_10Y']
        bond_df['Date'] = pd.to_datetime(bond_df['Date'])
        bond_df['Bond_Yield_10Y'] = bond_df['Bond_Yield_10Y'] / 100
        print("   ✓ 中国国债收益率抓取成功")
    except Exception as e:
        print(f"   ❌ 中国国债抓取失败: {e}")
        bond_df = pd.DataFrame()

    # ── A股PE增量 ─────────────────────────────────────
    print("\n--- 2. 更新A股指数PE ---")
    cn_indices = {
        "000300": "沪深300",
        "000688": "科创50",
        "000922": "中证红利",
        "399989": "中证医疗",
        "931071": "人工智能",
    }
    if not bond_df.empty:
        for code, name in cn_indices.items():
            try:
                print(f"   正在更新 {name}...")
                pe_csi = ak.stock_zh_index_hist_csindex(
                    symbol=code,
                    start_date=start_date,
                    end_date=end_date
                )[['日期', '滚动市盈率']]
                pe_csi.columns = ['Date', 'PE']
                process_incremental(pe_csi, bond_df, code, name, currency='CNY', bond_code='CN10Y')
                time.sleep(1)
            except Exception as e:
                print(f"   ❌ {name} 更新失败: {e}")

    # 增量脚本里替换原来的 ak.bond_gb_us_sina 部分
    print("\n--- 3. 更新美债收益率 ---")
    try:
        API_KEY = "a8ce66c09bbcedfb9e33de739a0dcbfb"
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": "DGS10",
            "api_key": API_KEY,
            "file_type": "json",
            "observation_start": (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
        }
        r = requests.get(url, params=params)
        us_bond_df = pd.DataFrame(r.json()["observations"])[["date", "value"]]
        us_bond_df.columns = ["Date", "Bond_Yield_10Y"]
        us_bond_df["Date"] = pd.to_datetime(us_bond_df["Date"])
        us_bond_df["Bond_Yield_10Y"] = pd.to_numeric(us_bond_df["Bond_Yield_10Y"], errors="coerce") / 100
        us_bond_df = us_bond_df.dropna()
        print(f"   ✓ 美债获取成功，{len(us_bond_df)} 条记录")
    except Exception as e:
        print(f"   ❌ 美债抓取失败: {e}")
        us_bond_df = pd.DataFrame()

    # ── 美股PE增量 ────────────────────────────────────
    print("\n--- 4. 更新美股指数PE ---")
    if not us_bond_df.empty:
        try:
            pe_today = fetch_us_pe_today()
            us_indices = [("SPY", "S&P 500")]
            for code, name in us_indices:
                pe_df = pe_today[pe_today['symbol'] == code][['Date', 'PE']].copy()
                process_incremental(pe_df, us_bond_df, code, name, currency='USD', bond_code='US10Y')
        except Exception as e:
            print(f"   ❌ 美股PE更新失败: {e}")

    print("\n增量同步完成。")

if __name__ == "__main__":
    main()
