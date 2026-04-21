import akshare as ak
import requests
import pandas as pd
import os
import time
from datetime import datetime
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

# 指数配置：(code, name, currency, bond_code, pe_source)
INDEX_CONFIG = [
    ("000300", "沪深300",        "CNY", "CN10Y", "csindex"),
    ("000688", "科创50",         "CNY", "CN10Y", "csindex"),
    ("000922", "中证红利",       "CNY", "CN10Y", "csindex"),
    ("399989", "中证医疗",       "CNY", "CN10Y", "csindex"),
    ("931071", "人工智能",       "CNY", "CN10Y", "csindex"),
    ("SPY",    "S&P 500",       "USD", "US10Y", "multpl"),
    ("QQQ",    "Nasdaq 100",    "USD", "US10Y", "gurufocus_csv"),  # ✅ 改用 GuruFocus CSV
    ("EWQ",    "MSCI France",   "EUR", "FR10Y", "worldpe_ratio"),
    ("EWG",    "MSCI Germany",  "EUR", "DE10Y", "worldpe_ratio"),
    ("EWJ",    "MSCI Japan",    "JPY", "JP10Y", "worldpe_ratio"),
    ("EEM",    "MSCI Emerging", "USD", "CN10Y", "worldpe_ratio"),
]

# ── ⚠️  每次运行前手动填入 QQQ 今日 PE ────────────────────────────────────────
# 查询地址: https://www.gurufocus.com/economic_indicators/6778/nasdaq-100-pe-ratio
# 填入格式示例: QQQ_PE_TODAY = 37.62
QQQ_PE_TODAY = None   # ← 每次运行前填这里，填 None 则只用 CSV 历史不追加今日

# ── GuruFocus CSV 路径 ────────────────────────────────────────────────────────
# 把下载的 xlsx 放到 ./data/ 目录，改名为 qqq_pe_gurufocus.xlsx
# 下载地址: https://www.gurufocus.com/economic_indicators/6778/nasdaq-100-pe-ratio
QQQ_PE_CSV_PATH = "./data/qqq_pe_gurufocus.xlsx"

# ── 国债获取 ──────────────────────────────────────────────────────────────────

def fetch_cn_bond_history():
    print("   正在获取中国10Y国债...")
    bond_list = []
    current_year = datetime.now().year
    for y in range(2006, current_year + 1):
        try:
            s_date = f"{y}0101"
            e_date = datetime.now().strftime("%Y%m%d") if y == current_year else f"{y}1231"
            df = ak.bond_china_yield(start_date=s_date, end_date=e_date)
            df = df[df['曲线名称'] == '中债国债收益率曲线']
            bond_list.append(df[['日期', '10年']])
            print(f"      ✓ {y} 年")
            time.sleep(0.5)
        except:
            print(f"      ✗ {y} 年跳过")
    df = pd.concat(bond_list)
    df.columns = ['Date', 'Bond_Yield_10Y']
    df['Date'] = pd.to_datetime(df['Date'])
    df['Bond_Yield_10Y'] = df['Bond_Yield_10Y'] / 100
    return df.sort_values('Date').reset_index(drop=True)

def fetch_fred_bond_history(series_id, name):
    print(f"   正在获取 {name} ({series_id})...")
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "observation_start": "2005-01-01",
    }
    r = requests.get(url, params=params)
    data = r.json()
    if "observations" not in data:
        raise ValueError(f"FRED 返回错误: {data.get('error_message')}")
    df = pd.DataFrame(data["observations"])[["date", "value"]]
    df.columns = ['Date', 'Bond_Yield_10Y']
    df['Date'] = pd.to_datetime(df['Date'])
    df['Bond_Yield_10Y'] = pd.to_numeric(df['Bond_Yield_10Y'], errors='coerce') / 100
    df = df.dropna().sort_values('Date').reset_index(drop=True)
    print(f"      ✓ {len(df)} 条, {df['Date'].min().date()} ~ {df['Date'].max().date()}")
    return df

# ── PE 获取 ───────────────────────────────────────────────────────────────────

def fetch_qqq_pe_from_csv():
    """
    从本地 GuruFocus xlsx 读取 QQQ PE 历史（日频，2006至今）
    算法：TTM，与 GuruFocus 网站一致
    如果设置了 QQQ_PE_TODAY，追加今日值
    """
    if not os.path.exists(QQQ_PE_CSV_PATH):
        raise FileNotFoundError(
            f"找不到 QQQ PE 文件: {QQQ_PE_CSV_PATH}\n"
            f"请从 https://www.gurufocus.com/economic_indicators/6778/nasdaq-100-pe-ratio 下载 xlsx，"
            f"放到 ./data/ 并改名为 qqq_pe_gurufocus.xlsx"
        )

    df = pd.read_excel(QQQ_PE_CSV_PATH, skiprows=4)   # 跳过前4行元数据
    df = df.iloc[:, :2]                                # 只取 Date、Value 两列
    df.columns = ['Date', 'PE']
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
    df['PE']   = pd.to_numeric(df['PE'], errors='coerce')
    df = df.dropna().sort_values('Date').reset_index(drop=True)

    # 追加今日手动值
    if QQQ_PE_TODAY is not None:
        today_row = pd.DataFrame({
            'Date': [pd.Timestamp(datetime.now().date())],
            'PE':   [float(QQQ_PE_TODAY)]
        })
        df = pd.concat([df, today_row], ignore_index=True)
        df = df.drop_duplicates(subset=['Date'], keep='last')
        df = df.sort_values('Date').reset_index(drop=True)
        print(f"      ✓ QQQ PE: {len(df)} 条, {df['Date'].min().date()} ~ {df['Date'].max().date()}，今日值: {QQQ_PE_TODAY}")
    else:
        print(f"      ✓ QQQ PE: {len(df)} 条, {df['Date'].min().date()} ~ {df['Date'].max().date()}（今日值未填，使用最近历史值）")

    return df

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

def fetch_spy_pe_history(pe_today_dict):
    print("   正在获取 SPY PE 历史 (multpl.com)...")
    url = "https://www.multpl.com/s-p-500-pe-ratio/table/by-month"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    r = requests.get(url, headers=headers)
    df = pd.read_html(StringIO(r.text))[0]
    df.columns = ["Date", "PE"]
    df["Date"] = pd.to_datetime(df["Date"])
    df["PE"] = pd.to_numeric(df["PE"], errors="coerce")
    df = df.sort_values("Date").reset_index(drop=True)

    if 'SPY' in pe_today_dict:
        today_row = pd.DataFrame({
            "Date": [pd.Timestamp(datetime.now().date())],
            "PE":   [pe_today_dict['SPY']]
        })
        df = pd.concat([df, today_row], ignore_index=True)
        df = df.drop_duplicates(subset=['Date'], keep='last')
        print(f"      ✓ {len(df)} 条, 今日SPY PE: {pe_today_dict['SPY']}")

    return df.sort_values("Date").reset_index(drop=True)

def fetch_pe_history_by_ratio(symbol, spy_pe_df, pe_today_dict):
    """EWQ/EWG/EWJ/EEM：用 SPY×今日比值估算历史，今日用真实值（已知局限，无更好免费源）"""
    if symbol not in pe_today_dict or 'SPY' not in pe_today_dict:
        raise ValueError(f"worldperatio 未返回 {symbol} 或 SPY")
    ratio = pe_today_dict[symbol] / pe_today_dict['SPY']
    print(f"   正在估算 {symbol} PE 历史, 今日比值 {symbol}/SPY = {ratio:.3f}（注：历史为估算值）...")
    df = spy_pe_df.copy()
    df['PE'] = (df['PE'] * ratio).round(2)

    today_row = pd.DataFrame({
        "Date": [pd.Timestamp(datetime.now().date())],
        "PE":   [pe_today_dict[symbol]]
    })
    df = pd.concat([df, today_row], ignore_index=True)
    df = df.drop_duplicates(subset=['Date'], keep='last')
    print(f"      ✓ {len(df)} 条, 今日{symbol} PE: {pe_today_dict[symbol]}")
    return df.sort_values("Date").reset_index(drop=True)

# ── 合并与保存 ────────────────────────────────────────────────────────────────

def process_and_save(pe_df, bond_df, code, name, currency, bond_code):
    if pe_df is None or pe_df.empty:
        print(f"   ⚠️ {name} PE数据为空，跳过")
        return

    pe_df = pe_df.copy()
    pe_df['Date'] = pd.to_datetime(pe_df['Date'])
    bond_df = bond_df.copy()
    bond_df['Date'] = pd.to_datetime(bond_df['Date'])

    merged = pd.merge(bond_df, pe_df[['Date', 'PE']], on='Date', how='outer').sort_values('Date')

    if bond_code in ['FR10Y', 'DE10Y', 'JP10Y']:
        merged['Bond_Yield_10Y'] = merged['Bond_Yield_10Y'].bfill()
    else:
        merged['Bond_Yield_10Y'] = merged['Bond_Yield_10Y']

    merged['PE'] = merged['PE'].ffill()

    merged['ERP'] = (1 / merged['PE']) - merged['Bond_Yield_10Y']
    merged['IndexCode'] = code
    merged['IndexName'] = name
    merged['Currency'] = currency
    merged['BondCode'] = bond_code

    os.makedirs('./data', exist_ok=True)
    merged.to_csv(f"./data/erp_{code}.csv", index=False, encoding='utf-8-sig')

    valid_count = merged['ERP'].notna().sum()
    print(f"   ✅ {name} 完成！总跨度: {len(merged)} 天，有效ERP: {valid_count} 天")
    if valid_count > 0:
        latest = merged[merged['ERP'].notna()].iloc[-1]
        print(f"      最新ERP: {latest['ERP']:.2%} ({latest['Date'].date()})")

# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    os.makedirs('./data', exist_ok=True)

    # 运行前检查提醒
    if QQQ_PE_TODAY is None:
        print("⚠️  提示: QQQ_PE_TODAY 未填写，将使用 CSV 中最近的历史值。")
        print("    请访问 https://www.gurufocus.com/economic_indicators/6778/nasdaq-100-pe-ratio 查询今日值后填入。\n")

    print("=" * 60)
    print("--- 1. 获取国债历史 ---")
    bonds = {}
    try:
        bonds['CN10Y'] = fetch_cn_bond_history()
    except Exception as e:
        print(f"   ❌ 中国国债失败: {e}")

    for bond_code, series_id in BOND_CONFIG.items():
        if bond_code == 'CN10Y':
            continue
        try:
            bonds[bond_code] = fetch_fred_bond_history(series_id, bond_code)
        except Exception as e:
            print(f"   ❌ {bond_code} 失败: {e}")

    print("\n--- 2. 获取今日 PE ---")
    try:
        pe_today_dict = fetch_worldpe_today()
        print(f"   ✓ worldperatio 获取到 {len(pe_today_dict)} 个标的")
    except Exception as e:
        print(f"   ❌ worldperatio 失败: {e}")
        pe_today_dict = {}

    print("\n--- 3. 处理各指数 ERP ---")
    spy_pe_df = None
    try:
        spy_pe_df = fetch_spy_pe_history(pe_today_dict)
    except Exception as e:
        print(f"   ❌ SPY PE历史获取失败: {e}")

    qqq_pe_df = None
    try:
        print("   正在读取 QQQ PE 历史 (GuruFocus CSV)...")
        qqq_pe_df = fetch_qqq_pe_from_csv()
    except Exception as e:
        print(f"   ❌ QQQ PE CSV读取失败: {e}")

    for code, name, currency, bond_code, pe_source in INDEX_CONFIG:
        print(f"\n   [{code}] {name}")
        if bond_code not in bonds:
            print(f"      ⚠️ 国债 {bond_code} 未获取，跳过")
            continue
        try:
            if pe_source == 'csindex':
                pe_df = ak.stock_zh_index_hist_csindex(
                    symbol=code,
                    start_date="20050408",
                    end_date=datetime.now().strftime("%Y%m%d")
                )[['日期', '滚动市盈率']]
                pe_df.columns = ['Date', 'PE']
                time.sleep(1)
            elif pe_source == 'multpl':
                pe_df = spy_pe_df
            elif pe_source == 'gurufocus_csv':
                if qqq_pe_df is None:
                    raise ValueError("QQQ PE CSV 未加载")
                pe_df = qqq_pe_df
            elif pe_source == 'worldpe_ratio':
                if spy_pe_df is None:
                    raise ValueError("SPY历史未获取，无法估算")
                pe_df = fetch_pe_history_by_ratio(code, spy_pe_df, pe_today_dict)
            else:
                raise ValueError(f"未知 pe_source: {pe_source}")
            process_and_save(pe_df, bonds[bond_code], code, name, currency, bond_code)
        except Exception as e:
            print(f"      ❌ {name} 失败: {e}")

    print("\n" + "=" * 60)
    print("历史数据初始化完成。")

if __name__ == "__main__":
    main()
