import akshare as ak
import pandas as pd
import os
import time
from datetime import datetime

def process_and_save(pe_df, bond_df, code, name):
    """
    通用数据处理与合并函数：保留全部时间，不填充空值
    """
    if pe_df.empty:
        print(f"   ⚠️ {name} 数据为空，跳过合并")
        return
        
    pe_df['Date'] = pd.to_datetime(pe_df['Date'])
    
    # 使用 outer join 保留国债和指数的所有日期并集
    merged = pd.merge(bond_df, pe_df, on='Date', how='outer')
    
    # 仅排序，不进行 ffill() 或 bfill()
    merged = merged.sort_values('Date')
    
    # 只有当 PE 和国债收益率同时存在时，计算 ERP
    # 如果其中一个为 NaN，ERP 结果自然也会是 NaN
    merged['ERP'] = (1 / merged['PE']) - merged['Bond_Yield_10Y']
    
    # 补全元数据
    merged['IndexCode'] = code
    merged['IndexName'] = name
    merged['Currency'] = 'CNY'
    merged['BondCode'] = 'CN10Y'
    
    # 导出 CSV (保留含有 NaN 的行)
    output_path = f"./data/erp_{code}.csv"
    merged.to_csv(output_path, index=False, encoding='utf-8-sig')
    
    # 计算一些统计信息用于打印输出
    valid_count = merged['ERP'].notna().sum()
    total_count = len(merged)
    
    print(f"   ✅ {name} 处理完成！")
    print(f"      - 总日期跨度: {total_count} 天")
    print(f"      - 有效 ERP 样本: {valid_count} 天")
    
    if valid_count > 0:
        latest_valid = merged[merged['ERP'].notna()].iloc[-1]
        print(f"      - 最新有效 ERP: {latest_valid['ERP']:.2%} ({latest_valid['Date'].date()})")

def main():
    data_dir = "./data"
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    
    print("--- 1. 正在获取 10Y 国债收益率 ---")
    bond_list = []
    current_year = datetime.now().year
    for y in range(2006, current_year + 1):
        try:
            s_date = f"{y}0101"
            e_date = datetime.now().strftime("%Y%m%d") if y == current_year else f"{y}1231"
            
            df = ak.bond_china_yield(start_date=s_date, end_date=e_date)
            df = df[df['曲线名称'] == '中债国债收益率曲线']
            bond_list.append(df[['日期', '10年']])
            print(f"   ✓ {y} 年国债已获取")
            time.sleep(0.5) 
        except:
            print(f"   ✗ {y} 年国债跳过")
    
    bond_df = pd.concat(bond_list)
    bond_df.columns = ['Date', 'Bond_Yield_10Y']
    bond_df['Date'] = pd.to_datetime(bond_df['Date'])
    bond_df['Bond_Yield_10Y'] = bond_df['Bond_Yield_10Y'] / 100

    print("\n--- 2. 正在获取指数 PE (不填充空值) ---")

    '''# 策略 A: 沪深300
    try:
        pe_lg = ak.stock_index_pe_lg(symbol="沪深300")[['日期', '滚动市盈率']]
        pe_lg.columns = ['Date', 'PE']
        process_and_save(pe_lg, bond_df, "000300", "HS300")
    except Exception as e:
        print(f"   ❌ HS300 失败: {e}")'''

    # 策略 B: 科创50 & 中证红利
    other_indices = {"000688": "科创50", "000300": "沪深300", "000922": "中证红利", "399989": "中证医疗",
    "931071": "人工智能"}
    for code, name in other_indices.items():
        try:
            pe_csi = ak.stock_zh_index_hist_csindex(
            symbol=code, 
            start_date="20050408",  
            end_date="20260416"
        )[['日期', '滚动市盈率']]
            pe_csi.columns = ['Date', 'PE']
            process_and_save(pe_csi, bond_df, code, name)
            time.sleep(1)
        except Exception as e:
            print(f"   ❌ {name} 失败: {e}")    

if __name__ == "__main__":
    main()
