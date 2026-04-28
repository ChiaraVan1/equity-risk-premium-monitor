"""
simple_etf_metrics.py
简化版 ETF 指标获取，专门用于 ERP_TO_ETF 映射中的标的
"""

import tushare as ts
import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta

def get_etf_metrics():
    """获取指定 ETF 的核心指标"""
    
    # 初始化
    token = os.environ.get('TUSHARE_TOKEN')
    if not token:
        raise ValueError("请设置 TUSHARE_TOKEN")
    ts.set_token(token)
    pro = ts.pro_api()
    
    # 目标 ETF 列表 (ERP代码 -> ETF代码)
    etf_list = [
        ("000300", "510300.SH"),   # 沪深300
        ("000688", "588000.SH"),   # 科创50
        ("000922", "515180.SH"),   # 中证红利
        ("399989", "512170.SH"),   # 中证医疗
        ("931071", "159819.SZ"),   # 人工智能
        ("HSTECH", "513180.SH"),   # 恒生科技
        ("SPY",    "513500.SH"),   # 标普500
        ("QQQ",    "159696.SZ"),   # 纳斯达克
        ("EWQ",    "513080.SH"),   # 法国ETF
        ("EWJ",    "513880.SH"),   # 日本ETF
    ]
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365)  # 取1年数据就够了
    
    results = []
    
    for erp_code, etf_code in etf_list:
        print(f"处理 {erp_code} -> {etf_code}")
        
        try:
            # 获取ETF日行情
            df_daily = pro.fund_daily(
                ts_code=etf_code, 
                start_date=start_date.strftime('%Y%m%d'),
                end_date=end_date.strftime('%Y%m%d')
            )
            
            if df_daily.empty:
                print(f"  警告: {etf_code} 无数据")
                continue
            
            # 获取净值数据（用于折价率）
            df_nav = pro.fund_nav(
                ts_code=etf_code,
                start_date=start_date.strftime('%Y%m%d'),
                end_date=end_date.strftime('%Y%m%d')
            )
            
            # 处理数据
            df_daily['trade_date'] = pd.to_datetime(df_daily['trade_date'])
            df_daily = df_daily.sort_values('trade_date')
            
            # 基础信息
            latest = df_daily.iloc[-1]
            prev_5d = df_daily.iloc[-6] if len(df_daily) >= 6 else latest
            prev_10d = df_daily.iloc[-11] if len(df_daily) >= 11 else latest
            
            # 1. 折溢价率
            discount_rate = np.nan
            if not df_nav.empty:
                df_nav['nav_date'] = pd.to_datetime(df_nav['nav_date'])
                df_nav = df_nav.sort_values('nav_date')
                latest_nav = df_nav.iloc[-1]['unit_nav']
                discount_rate = (latest_nav - latest['close']) / latest_nav
            
            # 2. 换手率相关
            turnover = latest['amount']  # 成交额(千元)
            turnover_5d = df_daily['amount'].tail(5).mean()
            turnover_10d = df_daily['amount'].tail(10).mean()
            
            # 3. 波动率
            returns = df_daily['pct_chg'] / 100
            volatility = returns.std() * np.sqrt(252)
            
            # 4. 最大回撤
            cumprod = (1 + returns).cumprod()
            rolling_max = cumprod.expanding().max()
            drawdown = (rolling_max - cumprod) / rolling_max
            max_drawdown = drawdown.max()
            
            # 汇总
            result = {
                'erp_code': erp_code,
                'ts_code': etf_code,
                'name': latest.get('name', etf_code),
                'latest_discount_rate': discount_rate,
                'turnover_amount': turnover / 10000,  # 转为万元
                'turnover_5d_avg': turnover_5d / 10000,
                'turnover_10d_avg': turnover_10d / 10000,
                'annualized_volatility': volatility,
                'max_drawdown': max_drawdown,
                'latest_close': latest['close'],
                'latest_pct_chg': latest['pct_chg'],
                'trade_date': latest['trade_date'].strftime('%Y-%m-%d')
            }
            
            results.append(result)
            print(f"  完成: 折价率={discount_rate:.4f}, 波动率={volatility:.2%}")
            
        except Exception as e:
            print(f"  错误: {e}")
    
    return pd.DataFrame(results)

if __name__ == '__main__':
    df = get_etf_metrics()
    print("\n" + "="*80)
    print("ETF 指标汇总")
    print("="*80)
    print(df[['erp_code', 'ts_code', 'latest_discount_rate', 'annualized_volatility', 'max_drawdown']].to_string())
    
    # 保存
    df.to_csv('simple_etf_metrics.csv', index=False, encoding='utf-8-sig')
    print("\n已保存到 simple_etf_metrics.csv")