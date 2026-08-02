"""
analysis/sentiment.py
情绪分析模块：热度信号、基本面预警、东方财富快讯
"""
from popularity_signal import build_popularity_block, compute_popularity_confirmation
from config_loader import FUNDAMENTAL_KEYWORDS


def build_sentiment_block(code, name, etf_code, erp_percentile, news_df=None):
    """生成综合情绪块（热度 + 基本面预警）。"""
    blocks = []

    # 1. 热度信号
    popularity_result = compute_popularity_confirmation(code, erp_percentile)
    popularity_block = build_popularity_block(code, erp_percentile, precomputed=popularity_result)
    if popularity_block:
        blocks.append(popularity_block)

    # 2. 基本面预警
    fundamental_block = build_fundamental_alert_block(code, name, news_df)
    if fundamental_block[1]:
        blocks.append(fundamental_block[1])

    return "\n".join(blocks)


def build_fundamental_alert_block(code: str, name: str, news_df=None) -> tuple[dict, str]:
    """
    基本面暴雷预警块。
    返回: (summary_dict, markdown_block)
    """
    keywords = FUNDAMENTAL_KEYWORDS.get(code, [])
    
    if not keywords or news_df is None or news_df.empty:
        return {"level": 0}, ""

    # 简化版：检查新闻中是否有关键词
    matched_news = []
    for _, row in news_df.iterrows():
        title = str(row.get('title', ''))
        for kw in keywords:
            if kw.lower() in title.lower():
                matched_news.append({
                    'title': title,
                    'keyword': kw,
                })
                break

    if not matched_news:
        return {"level": 0}, ""

    summary = {
        "level": 1 if len(matched_news) < 3 else 2,
        "count": len(matched_news),
    }

    block = f"""
---
### 🚨 基本面预警

检测到 **{len(matched_news)}** 条相关快讯（最近30分钟内）：

"""
    for item in matched_news[:5]:
        block += f"- 【{item['keyword']}】{item['title'][:50]}...\n"

    block += "\n> ⚠️ 请核实重要事件后再决策\n"

    return summary, block
