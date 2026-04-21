import akshare as ak
import requests
import pandas as pd
import os
import time
from datetime import datetime, timedelta
from io import StringIO

# ── 配置表 ────────────────────────────────────────────────────────────────────

FRED_API_KEY = "a8ce66c09bbcedfb9e33de739a0dcbfb"

BOND_CONFIG = {
    'CN10Y': 'bond_china',
    'US10Y': 'DGS10',
    'FR10Y': 'IRLTLT01FRM156N',
    'DE10Y': 'IRLTLT01DEM156N',
    'JP10Y': 'IRLTLT01JPM156N',
}

INDEX_CONFIG = [
    ("000300", "沪深300",        "CNY", "CN10Y", "csindex"),
    ("000688", "科创50",         "CNY", "CN10Y", "csindex"),
    ("000922", "中证红利",       "CNY", "CN10Y", "csindex"),
    ("399989", "中证医疗",       "CNY", "CN10Y", "csindex"),
    ("931071", "人工智能",       "CNY", "CN10Y", "csindex"),
    ("SPY",    "S&P 500",       "USD", "US10Y", "worldpe"),
    ("QQQ",    "Nasdaq 100",    "USD", "US10Y", "manual"),   # 手动填入
    ("EWQ",    "MSCI France",   "EUR", "FR10Y", "worldpe"),
    ("EWG",    "MSCI Germany",  "EUR", "DE10Y", "worldpe"),
    ("EWJ",    "MSCI Japan",    "JPY", "JP10Y", "worldpe"),
    ("EEM",    "MSCI Emerging", "USD", "CN10Y", "worldpe"),
    # ========== 新增恒生科技指数 ==========
    ("HSTECH", "恒生科技指数",   "CNY", "CN10Y", "manual"),   # 手动填入，与 QQQ 一致
]

# ── 手动填入今日 PE（与 QQQ 一致）─────────────────────────────────────────────
# 优先从环境变量读（GitHub Actions），没有则用硬编码值（本地调试）
_qqq_pe_env = os.environ.get("QQQ_PE_TODAY")
QQQ_PE_TODAY = float(_qqq_pe_env) if _qqq_pe_env else None

_hstech_pe_env = os.environ.get("HS_TECH_PE_TODAY")
HS_TECH_PE_TODAY = float(_hstech_pe_env) if _hstech_pe_env else None

# ── 国债增量获取 ───────────────────────────────────────────────────────────────

def fetch_cn_bond_incremental(start_date, end_date):
    df = ak.bond_china_yield(start_date=start_date, end_date=end_date)
    df = df[df['曲线名称'] == '中债国债收益率曲线'][['日期', '10年']]
    df.columns = ['Date', 'Bond_Yield_10Y']
    df['Date'] = pd.to_datetime(df['Date'])
    df['Bond_Yield_10Y'] = df['Bond_Yield_10Y'] / 100
    return df

def fetch_fred_bond_incremental(series_id, start_date_str):
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "observation_start": start_date_str,
    }
    r = requests.get(url, params=params)
    data = r.json()
    if "observations" not in data:
        raise ValueError(f"FRED 错误: {data.get('error_message')}")
    df = pd.DataFrame(data["observations"])[["date", "value"]]
    df.columns = ['Date', 'Bond_Yield_10Y']
    df['Date'] = pd.to_datetime(df['Date'])
    df['Bond_Yield_10Y'] = pd.to_numeric(df['Bond_Yield_10Y'], errors='coerce') / 100
    return df.dropna().sort_values('Date').reset_index(drop=True)

# ── PE 增量获取 ────────────────────────────────────────────────────────────────

def fetch_worldpe_today():
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
    return dict(zip(df['symbol'], df['PE']))

# ── 增量合并保存 ───────────────────────────────────────────────────────────────

def process_incremental(new_pe_df, new_bond_df, code, name, currency, bond_code):
    file_path = f"./data/erp_{code}.csv"

    new_pe_df = new_pe_df.copy()
    new_pe_df['Date'] = pd.to_datetime(new_pe_df['Date'])
    new_bond_df = new_bond_df.copy()
    new_bond_df['Date'] = pd.to_datetime(new_bond_df['Date'])

    new_data = pd.merge(new_bond_df, new_pe_df[['Date', 'PE']], on='Date', how='outer').sort_values('Date')
    new_data['IndexCode'] = code
    new_data['IndexName'] = name
    new_data['Currency'] = currency
    new_data['BondCode'] = bond_code

    if os.path.exists(file_path):
        old_data = pd.read_csv(file_path)
        old_data['Date'] = pd.to_datetime(old_data['Date'])
        combined = pd.concat([old_data, new_data], ignore_index=True)
        combined = combined.drop_duplicates(subset=['Date'], keep='last').sort_values('Date')
        action = "增量更新"
    else:
        combined = new_data
        action = "全新创建"

    combined['PE'] = combined['PE'].ffill()
    combined['ERP'] = (1 / combined['PE']) - combined['Bond_Yield_10Y']

    combined.to_csv(file_path, index=False, encoding='utf-8-sig')
    valid_now = combined['ERP'].notna().sum()
    print(f"   ✅ {name} {action}！总记录: {len(combined)} 天，有效ERP: {valid_now} 天")

# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    os.makedirs('./data', exist_ok=True)

    # 检查 QQQ 今日值
    if QQQ_PE_TODAY is None:
        print("⚠️  警告: QQQ_PE_TODAY 未填写，今日 QQQ ERP 将不会更新！")
        print("    请访问 https://www.gurufocus.com/economic_indicators/6778/nasdaq-100-pe-ratio")
        print("    查询今日值后填入脚本顶部的 QQQ_PE_TODAY 再运行。\n")
    else:
        print(f"✅ QQQ 今日 PE: {QQQ_PE_TODAY}（来源: GuruFocus TTM）\n")

    # 检查恒生科技今日值
    if HS_TECH_PE_TODAY is None:
        print("⚠️  警告: HS_TECH_PE_TODAY 未填写，今日恒生科技 ERP 将不会更新！")
        print("    请根据最新数据手动填写（例如从 GuruFocus 或其它数据源获取）。\n")
    else:
        print(f"✅ 恒生科技 今日 PE: {HS_TECH_PE_TODAY}\n")

    end_date_str   = datetime.now().strftime("%Y%m%d")
    start_date_str = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    fred_start     = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

    print("=" * 60)
    print(f"增量更新 [{start_date_str} -> {end_date_str}]")

    # 1. 国债增量
    print("\n--- 1. 获取国债增量 ---")
    bonds = {}
    try:
        bonds['CN10Y'] = fetch_cn_bond_incremental(start_date_str, end_date_str)
        print(f"   ✓ 中国国债: {len(bonds['CN10Y'])} 条")
    except Exception as e:
        print(f"   ❌ 中国国债失败: {e}")

    for bond_code, series_id in BOND_CONFIG.items():
        if bond_code == 'CN10Y':
            continue
        try:
            bonds[bond_code] = fetch_fred_bond_incremental(series_id, fred_start)
            print(f"   ✓ {bond_code}: {len(bonds[bond_code])} 条")
        except Exception as e:
            print(f"   ❌ {bond_code} 失败: {e}")

    # 2. 今日 PE
    print("\n--- 2. 获取今日 PE ---")
    try:
        pe_today_dict = fetch_worldpe_today()
        for sym in ['SPY', 'EWQ', 'EWG', 'EWJ', 'EEM']:
            if sym in pe_today_dict:
                print(f"   ✓ {sym}: {pe_today_dict[sym]}")
        if QQQ_PE_TODAY is not None:
            print(f"   ✓ QQQ: {QQQ_PE_TODAY} (手动填入, GuruFocus TTM)")
        if HS_TECH_PE_TODAY is not None:
            print(f"   ✓ HSTECH: {HS_TECH_PE_TODAY} (手动填入)")
    except Exception as e:
        print(f"   ❌ worldperatio 失败: {e}")
        pe_today_dict = {}

    # 3. 更新各指数
    print("\n--- 3. 更新各指数 ERP ---")
    today = pd.Timestamp(datetime.now().date())

    for code, name, currency, bond_code, pe_source in INDEX_CONFIG:
        if bond_code not in bonds:
            print(f"   ⚠️ [{code}] 国债 {bond_code} 未获取，跳过")
            continue
        try:
            if pe_source == 'csindex':
                pe_df = ak.stock_zh_index_hist_csindex(
                    symbol=code,
                    start_date=start_date_str,
                    end_date=end_date_str
                )[['日期', '滚动市盈率']]
                pe_df.columns = ['Date', 'PE']
                time.sleep(1)

            elif pe_source == 'worldpe':
                if code not in pe_today_dict:
                    raise ValueError(f"worldperatio 未返回 {code}")
                pe_df = pd.DataFrame({'Date': [today], 'PE': [pe_today_dict[code]]})

            elif pe_source == 'manual':
                # QQQ 和恒生科技都走这个分支
                if code == 'QQQ':
                    if QQQ_PE_TODAY is None:
                        print(f"   ⚠️ [{code}] QQQ_PE_TODAY 未填写，跳过今日更新")
                        continue
                    pe_df = pd.DataFrame({'Date': [today], 'PE': [float(QQQ_PE_TODAY)]})
                elif code == 'HSTECH':
                    if HS_TECH_PE_TODAY is None:
                        print(f"   ⚠️ [{code}] HS_TECH_PE_TODAY 未填写，跳过今日更新")
                        continue
                    pe_df = pd.DataFrame({'Date': [today], 'PE': [float(HS_TECH_PE_TODAY)]})
                else:
                    raise ValueError(f"未知 manual 指数: {code}")

            else:
                raise ValueError(f"未知 pe_source: {pe_source}")

            process_incremental(pe_df, bonds[bond_code], code, name, currency, bond_code)

        except Exception as e:
            print(f"   ❌ [{code}] {name} 失败: {e}")

    print("\n" + "=" * 60)
    print("增量同步完成。")

if __name__ == "__main__":
    main()
