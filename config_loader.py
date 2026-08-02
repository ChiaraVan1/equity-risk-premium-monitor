"""
中心配置管理 - 所有 py 文件从这里读取配置
避免在各个文件里重复维护 indices / etf_code / holding 等信息
"""
import json
import os

def _load_config_file():
    """读取 config.json"""
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)

# ══════════════════════════════════════════════════════════════════════
# 全局配置对象
# ══════════════════════════════════════════════════════════════════════
_RAW_CONFIG = _load_config_file()
ALL_INDICES = _RAW_CONFIG["indices"]

# ══════════════════════════════════════════════════════════════════════
# dividend_yield.py 用
# ══════════════════════════════════════════════════════════════════════
# A股指数（理杏仁数据源）
DIVIDEND_INDEX_CODES = {
    idx["code"]: idx["code"]
    for idx in ALL_INDICES
    if idx["source"] == "lixinger"
}

# 国际/港股标的（yfinance数据源）
YFINANCE_TICKERS = {
    idx["code"]: idx["yfinance_ticker"]
    for idx in ALL_INDICES
    if idx["source"] == "yfinance" and idx["yfinance_ticker"]
}

# ══════════════════════════════════════════════════════════════════════
# erp_position.py 用
# ══════════════════════════════════════════════════════════════════════
# indices 列表（用于主循环）
INDICES_LIST = [(idx["code"], idx["name"]) for idx in ALL_INDICES]

# 持仓标记（用于仪表盘展示和信号优先级）
HOLDING_CATEGORY = {idx["code"]: idx["holding"] for idx in ALL_INDICES}

# 基本面预警关键词（用于 build_fundamental_alert_block）
FUNDAMENTAL_KEYWORDS = {
    idx["code"]: idx.get("keywords", [])
    for idx in ALL_INDICES
    if idx.get("keywords")
}

# ══════════════════════════════════════════════════════════════════════
# etf_metrics.py 用
# ══════════════════════════════════════════════════════════════════════
# 指数代码 -> A股ETF代码映射
ERP_TO_ETF = {idx["code"]: idx.get("etf_code") for idx in ALL_INDICES}

# ══════════════════════════════════════════════════════════════════════
# fetch_bond_yield.py / fetch_bond_yield_incremental.py 用
# ══════════════════════════════════════════════════════════════════════
# INDEX_CONFIG: (code, name, currency, bond_code, pe_source)
BOND_YIELD_CONFIG = [
    (
        idx["code"],
        idx["name"],
        idx["currency"],
        idx["bond_code"],
        idx["pe_source"],
    )
    for idx in ALL_INDICES
]

# ══════════════════════════════════════════════════════════════════════
# fetch_ps.py 用 - HSTECH 成分股
# ══════════════════════════════════════════════════════════════════════
def get_hstech_components():
    """获取恒生科技指数的成分股列表"""
    for idx in ALL_INDICES:
        if idx["code"] == "HSTECH":
            return idx.get("hstech_components", [])
    return []

HSTECH_TICKERS = get_hstech_components()

# ══════════════════════════════════════════════════════════════════════
# simple_etf_metrics.py 用
# ══════════════════════════════════════════════════════════════════════
# ETF_LIST: (code, etf_code)
ETF_LIST = [
    (idx["code"], idx.get("etf_code"))
    for idx in ALL_INDICES
    if idx.get("etf_code")  # 只包含有对应ETF的指数
]

# ETF_TO_BENCHMARK: etf_code -> benchmark_code
ETF_TO_BENCHMARK = {}
for idx in ALL_INDICES:
    etf_code = idx.get("etf_code")
    if etf_code:
        ETF_TO_BENCHMARK[etf_code] = idx["code"]

# ══════════════════════════════════════════════════════════════════════
# pe_band.py 用 — code -> name 映射
# ══════════════════════════════════════════════════════════════════════
CODE_NAME = {idx["code"]: idx["name"] for idx in ALL_INDICES}

# ══════════════════════════════════════════════════════════════════════
# 便利函数
# ══════════════════════════════════════════════════════════════════════
def get_index_by_code(code: str):
    """根据 code 获取完整的 index 配置"""
    for idx in ALL_INDICES:
        if idx["code"] == code:
            return idx
    return None

def get_all_codes():
    """获取所有指数代码列表"""
    return [idx["code"] for idx in ALL_INDICES]

def get_yfinance_codes():
    """获取所有 yfinance 数据源的指数代码"""
    return [idx["code"] for idx in ALL_INDICES if idx["source"] == "yfinance"]

def get_lixinger_codes():
    """获取所有 lixinger 数据源的指数代码"""
    return [idx["code"] for idx in ALL_INDICES if idx["source"] == "lixinger"]

def reload_config():
    """重新加载 config.json（用于热更新，一般不需要）"""
    global _RAW_CONFIG, ALL_INDICES
    _RAW_CONFIG = _load_config_file()
    ALL_INDICES = _RAW_CONFIG["indices"]
    # 其他导出变量会自动使用新的 ALL_INDICES
