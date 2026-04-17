import akshare as ak
import pandas as pd
import os
import time
from datetime import datetime, timedelta

def process_incremental(new_pe_df, new_bond_df, code, name):
    """
    增量合并逻辑：读取旧文件 -> 合并新数据 -> 去重 -> 保存
    """
    file_path = f"./data/erp_{code}.csv"
    
    # 1. 计算新的 ERP (仅针对这一个月的新数据)
    new_pe_df['Date'] = pd.to_datetime(new_pe_df['Date'])
    new_data = pd.merge(new_bond_df, new_pe_df, on='Date', how='outer').sort_values('Date')
    new_data['ERP'] = (1 / new_data['PE']) - new_data['Bond_Yield_10Y']
    
    # 补全元数据
    new_data['IndexCode'], new_data['IndexName'] = code, name
    new_data['Currency'], new_data['BondCode'] = 'CNY', 'CN10Y'

    # 2. 读取现有数据并合并
    if os.path.exists(file_path):
        old_data = pd.read_csv(file_path)
        old_data['Date'] = pd.to_datetime(old_data['Date'])
        
        # 合并后按日期去重，保留最新的记录（keep='last'）
        combined = pd.concat([old_data, new_data], ignore_index=True)
        combined = combined.drop_duplicates(subset=['Date'], keep='last').sort_values('Date')
        action = "增量更新"
    else:
        combined = new_data
        action = "全新创建"

    # 3. 保存
    combined.to_csv(file_path, index=False, encoding='utf-8-sig')
    
    valid_now = combined['ERP'].notna().sum()
    print(f"   ✅ {name} {action}成功！当前总记录: {len(combined)} 天，有效ERP样本: {valid_now} 天")

def main():
    if not os.path.exists('./data'): os.makedirs('./data')
    
    # 设置时间范围：最近 30 天
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    
    print(f"--- 1. 正在抓取增量数据 [{start_date} -> {end_date}] ---")
    
    # 1. 抓取最近一个月的国债
    try:
        bond_df = ak.bond_china_yield(start_date=start_date, end_date=end_date)
        bond_df = bond_df[bond_df['曲线名称'] == '中债国债收益率曲线'][['日期', '10年']]
        bond_df.columns = ['Date', 'Bond_Yield_10Y']
        bond_df['Date'] = pd.to_datetime(bond_df['Date'])
        bond_df['Bond_Yield_10Y'] = bond_df['Bond_Yield_10Y'] / 100
        print("   ✓ 国债收益率抓取成功")
    except Exception as e:
        print(f"   ❌ 国债抓取失败: {e}")
        return

    # 2. 抓取指数 PE 并增量保存
    '''# 策略 A: 沪深300 (乐咕)
    try:
        print(f"   正在更新 HS300...")
        pe_lg = ak.stock_index_pe_lg(symbol="沪深300")
        # 乐咕接口通常返回全量，我们手动筛选最近一个月
        pe_lg['日期'] = pd.to_datetime(pe_lg['日期'])
        pe_lg = pe_lg[pe_lg['日期'] >= pd.to_datetime(start_date)][['日期', '滚动市盈率']]
        pe_lg.columns = ['Date', 'PE']
        process_incremental(pe_lg, bond_df, "000300", "HS300")
    except Exception as e:
        print(f"   ❌ HS300 更新失败: {e}")'''

    # 策略 B: 科创50 & 中证红利 (中证官方)
    other_indices = {
    "000300": "沪深300",
    "000688": "科创50",
    "000922": "中证红利",
    "399989": "中证医疗",
    "931071": "人工智能"}
    for code, name in other_indices.items():
        try:
            print(f"   正在更新 {name}...")
            # 注意：中证接口获取的是历史，通常也包含近期数据
            pe_csi = ak.stock_zh_index_hist_csindex(
                symbol=code, 
                start_date=start_date, 
                end_date=end_date
            )[['日期', '滚动市盈率']]
            pe_csi.columns = ['Date', 'PE']
            process_incremental(pe_csi, bond_df, code, name)
            time.sleep(1)
        except Exception as e:
            print(f"   ❌ {name} 更新失败: {e}")

    print("\n增量同步完成。")

if __name__ == "__main__":
    main()
