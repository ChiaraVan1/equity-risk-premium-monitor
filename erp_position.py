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

    # 3. 构造报告文本 (核心修改点)
    # 使用 Markdown 语法，针对手机端窄屏优化
    report = []
    report.append(f"### 📊 {name} ({code}) 决策报告")
    report.append(f"**日期**: `{current_date}`")
    report.append(f"**当前 ERP**: `{current_erp:.2%}`")
    report.append(f"**历史均值**: {mean_erp:.2%} (±{std_erp:.4f})")
    
    report.append("\n**📍 历史分位参照 (越高越便宜)**")
    # 手机端不建议写在一行，改成小字列表
    report.append(f"- P90 (极度低估): `{quantiles['P90']:.2%}`")
    report.append(f"- P50 (价值中枢): `{quantiles['P50']:.2%}`")
    report.append(f"- P5  (危险泡沫): `{quantiles['P5']:.2%}`")

    report.append("\n**💡 仓位执行建议**")
    
    # 1. 投机仓逻辑
    if current_erp >= quantiles["P90"]:
        report.append(f"> 💎 **投机仓**: 极度低估，买入 30% 头寸")
    elif current_erp <= quantiles["P50"]:
        report.append(f"> 💰 **投机仓**: 回到中枢，平账锁定利润")
    else:
        report.append(f"> ⏳ **投机仓**: 等待信号，暂不建仓")

    # 2. 价值仓逻辑
    if current_erp >= quantiles["P75"]:
        report.append(f"> 📈 **价值仓**: 显著低估，保持 40-60% 仓位")
    elif quantiles["P50"] <= current_erp < quantiles["P75"]:
        report.append(f"> ⚖️ **价值仓**: 合理偏低，建议持有")
    else:
        report.append(f"> 📉 **价值仓**: 估值偏高，建议减仓")

    # 3. 泡沫仓逻辑
    if current_erp <= quantiles["P5"]:
        report.append(f"> 🔥 **泡沫仓**: **触发 P5 终极预警！清仓！**")
    else:
        report.append(f"> 🍀 **泡沫仓**: 尚在安全区")

    report.append("\n---\n") # 分割线
    
    return "\n".join(report)

def main():
    indices = [("000300", "沪深300"), ("000688", "科创50"), ("000922", "中证红利")]
    final_message = ""
    for code, name in indices:
        # 获取每个指数的报告字符串
        result = analyze_and_suggest(code, name)
        if result:
            final_message += result
    
    # 最后将 final_message 发送给 Server酱
    # send_to_wechat(final_message) 
    print(final_message) # 你可以先在控制台看一眼效果