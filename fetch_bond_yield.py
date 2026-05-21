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
    ("QQQ",    "Nasdaq 100",    "USD", "US10Y", "gurufocus_csv"),
    ("EWQ",    "MSCI France",   "EUR", "FR10Y", "worldpe_ratio"),
    ("EWG",    "MSCI Germany",  "EUR", "DE10Y", "worldpe_ratio"),
    ("EWJ",    "MSCI Japan",    "JPY", "JP10Y", "worldpe_ratio"),
    ("EEM",    "MSCI Emerging", "USD", "CN10Y", "worldpe_ratio"),
    # ========== 新增指数 ==========
    ("HSTECH", "恒生科技指数",   "CNY", "CN10Y", "hstech_csv"),
    ("000069", "消费80",      "CNY", "CN10Y", "csindex"),
    ("930781", "中证影视",    "CNY", "CN10Y", "csindex"),
    ("000989", "全指可选",    "CNY", "CN10Y", "csindex"),
    ("931139", "CS消费50",   "CNY", "CN10Y", "csindex"),
    ("399967", "中证军工",   "CNY", "CN10Y", "csindex"),
    ("931066", "军工龙头",   "CNY", "CN10Y", "csindex"),
    ("930598", "稀土产业",    "CNY", "CN10Y", "csindex"),
    ("930794", "中美互联网",    "CNY", "CN10Y", "csindex"),
]

# ── 手动填入今日 PE（与 QQQ 一致）─────────────────────────────────────────────
QQQ_PE_TODAY = None          # 每次运行前填写
HS_TECH_PE_TODAY = None      # 恒生科技今日 PE，查询来源：用户提供的 CSV 或 GuruFocus 类似网站

# ── 本地 CSV 路径 ────────────────────────────────────────────────────────────
QQQ_PE_CSV_PATH = "./data/qqq_pe_gurufocus.xlsx"
HS_TECH_CSV_PATH = "./data/hstech_pe.csv"   # 用户提供的 CSV 文件，请重命名为此并放入 ./data/

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

def fetch_hstech_pe_from_csv():
    """
    从本地 CSV 读取恒生科技指数 PE 历史
    列名要求：日期 和 PE-TTM等权
    自动处理：
      - 跳过非数据行（如末尾的说明文字）
      - 清理 PE 值中的等号前缀
      - 如果设置了 HS_TECH_PE_TODAY，追加今日值
    """
    if not os.path.exists(HS_TECH_CSV_PATH):
        raise FileNotFoundError(
            f"找不到恒生科技 PE 文件: {HS_TECH_CSV_PATH}\n"
            f"请将用户提供的 CSV 文件重命名为 hstech_pe.csv 并放入 ./data/ 目录"
        )

    # 读取 CSV，不跳过任何行，后续手动清洗
    df = pd.read_csv(HS_TECH_CSV_PATH, encoding='utf-8-sig')
    df.columns = df.columns.str.strip()

    if '日期' not in df.columns or 'PE-TTM等权' not in df.columns:
        raise ValueError("CSV 文件中必须包含 '日期' 和 'PE-TTM等权' 列")

    df = df[['日期', 'PE-TTM等权']].copy()
    df.columns = ['Date', 'PE']

    # 1. 清理 PE 列：去掉等号，转为数值
    df['PE'] = df['PE'].astype(str).str.replace('=', '', regex=False)
    df['PE'] = pd.to_numeric(df['PE'], errors='coerce')

    # 2. 清理日期列：转为 datetime，无效的变成 NaT
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')

    # 3. 删除日期或 PE 无效的行（包括末尾的说明文字行）
    df = df.dropna(subset=['Date', 'PE'])

    # 4. 按日期排序
    df = df.sort_values('Date').reset_index(drop=True)

    # 5. 追加今日手动值（如果有）
    if HS_TECH_PE_TODAY is not None:
        today_row = pd.DataFrame({
            'Date': [pd.Timestamp(datetime.now().date())],
            'PE':   [float(HS_TECH_PE_TODAY)]
        })
        df = pd.concat([df, today_row], ignore_index=True)
        df = df.drop_duplicates(subset=['Date'], keep='last')
        df = df.sort_values('Date').reset_index(drop=True)
        print(f"      ✓ 恒生科技 PE: {len(df)} 条, {df['Date'].min().date()} ~ {df['Date'].max().date()}，今日值: {HS_TECH_PE_TODAY}")
    else:
        print(f"      ✓ 恒生科技 PE: {len(df)} 条, {df['Date'].min().date()} ~ {df['Date'].max().date()}（今日值未填，使用最近历史值）")

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
        # ★ 修复：对国债收益率做 ffill，防止月末等偶发缺失导致 ERP/PSY 为空
        merged['Bond_Yield_10Y'] = merged['Bond_Yield_10Y'].ffill()

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
    if HS_TECH_PE_TODAY is None:
        print("⚠️  提示: HS_TECH_PE_TODAY 未填写，将使用 CSV 中最近的历史值。")
        print("    请根据最新数据手动填写（例如从 GuruFocus 或其它数据源获取）。\n")

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

    hstech_pe_df = None
    try:
        print("   正在读取 恒生科技 PE 历史 (本地 CSV)...")
        hstech_pe_df = fetch_hstech_pe_from_csv()
    except Exception as e:
        print(f"   ❌ 恒生科技 PE CSV读取失败: {e}")

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
            elif pe_source == 'hstech_csv':        # 新增恒生科技处理
                if hstech_pe_df is None:
                    raise ValueError("恒生科技 PE CSV 未加载")
                pe_df = hstech_pe_df
            else:
                raise ValueError(f"未知 pe_source: {pe_source}")
            process_and_save(pe_df, bonds[bond_code], code, name, currency, bond_code)
        except Exception as e:
            print(f"      ❌ {name} 失败: {e}")

    print("\n" + "=" * 60)
    print("历史数据初始化完成。")

if __name__ == "__main__":
    main()
