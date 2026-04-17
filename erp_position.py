import pandas as pd
import numpy as np
import os
from datetime import datetime

def analyze_and_suggest(code, name):
    file_path = f"./data/erp_{code}.csv"
    if not os.path.exists(file_path):
        print(f"❌ 未找到 {name} ({code}) 的数据文件")
        return

    # 1. 加载数据
    df = pd.read_csv(file_path)
    df['Date'] = pd.to_datetime(df['Date'])
    
    # 过滤掉空值
    erp_series = df['ERP'].dropna()
    
    if len(erp_series) < 250:
        print(f"\n⚠️ {name} ({code}) 有效样本不足，当前仅有 {len(erp_series)} 天数据，跳过分析。")
        return

    # 2. 分析 ERP 分布特征
    mean_erp = erp_series.mean()
    std_erp = erp_series.std()
    
    # 修正了空格问题，确保 key 的一致性
    quantiles = {
        "P90": erp_series.quantile(0.90),
        "P75": erp_series.quantile(0.75),
        "P50": erp_series.quantile(0.50),
        "P25": erp_series.quantile(0.25),
        "P10": erp_series.quantile(0.10),
        "P5":  erp_series.quantile(0.05),
    }

    current_erp = erp_series.iloc[-1]
    current_date = df['Date'].iloc[-1].date()

    # 3. 生成报告
    print(f"\n" + "="*60)
    print(f"【{name} ({code})】 仓位决策报告 | 日期: {current_date}")
    print(f"当前 ERP: {current_erp:.2%} | 历史均值: {mean_erp:.2%} | 标准差: {std_erp:.4f}")
    print("-" * 60)
    print("历史分位点参照 (ERP越高越便宜):")
    for k, v in quantiles.items():
        status = ""
        if k == "P90": status = "(极度低估)"
        elif k == "P75": status = "(显著低估)"
        elif k == "P50": status = "(价值中枢)"
        elif k == "P25": status = "(进入高估)"
        elif k == "P10": status = "(极度高估)"
        elif k == "P5":  status = "(危险泡沫)"
        
        mark = " ← [当前位置]" if (current_erp >= v if k=="P90" else False) else "" # 这里仅作演示
        print(f"  {k} {status:<10}: {v:.2%}")
    print("-" * 60)

    # 决策建议
    suggestions = []
    
    # 1. 投机仓 (0-30%)
    if current_erp >= quantiles["P90"]:
        suggestions.append("💎 [投机仓]：ERP >= P90！极度低估。建议买入投机头寸 (30%)。")
    elif current_erp <= quantiles["P50"]:
        suggestions.append("止 [投机仓]：回到 P50 中枢。建议卖出平账，锁定波段利润。")
    else:
        suggestions.append("⏳ [投机仓]：等待极度低估信号，当前不建议新开仓。")

    # 2. 价值仓 (0-60%)
    if current_erp >= quantiles["P75"]:
        suggestions.append("📈 [价值仓]：ERP >= P75。显著低估，建议建立 40-60% 仓位。")
    elif quantiles["P50"] <= current_erp < quantiles["P75"]:
        suggestions.append("⚖️ [价值仓]：处于 P50-P75 之间。估值合理偏低，建议持有。")
    elif quantiles["P25"] <= current_erp < quantiles["P50"]:
        suggestions.append("📉 [价值仓]：进入 P25-P50 高估区间。建议分批止盈，减至 30% 以下。")
    else:
        suggestions.append("🚫 [价值仓]：ERP < P25。严重高估，建议清空价值仓。")

    # 3. 泡沫仓 (0-20%)
    if current_erp <= quantiles["P5"]:
        suggestions.append("🔥 [泡沫仓]：触发 P5 终极预警！执行强制清仓，一股不留。")
    elif current_erp <= quantiles["P10"]:
        suggestions.append("⚠️ [泡沫仓]：处于 P10 极高估区。建议撤回大部分利润，仅留极少底仓。")
    else:
        suggestions.append("🍀 [泡沫仓]：ERP 尚在安全区，无需恐慌清仓。")

    for s in suggestions:
        print(s)
    print("="*60)

def main():
    indices = [
        ("000300", "沪深300"),
        ("000688", "科创50"),
        ("000922", "中证红利")
    ]
    for code, name in indices:
        analyze_and_suggest(code, name)

if __name__ == "__main__":
    main()
