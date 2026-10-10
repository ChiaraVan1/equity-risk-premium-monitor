#!/usr/bin/env python3
"""
雪球热榜人气信号模块（三因子融合版）
==================================================================
核心规则（重构后，三因子各司其职）：
  ① ERP 分位         → 决定【估值方向】：≥P75 低估(便宜)、≤P25 高估(贵)、中间合理
  ② 板块轮动数据     → 决定【资金/趋势方向】：模块拉取指数基金板块轮动接口，
                        判断该行业对应板块处于 升温(资金流入)/降温(资金流出)/平稳
  ③ 雪球热榜排名趋势 → 决定【拥挤/关注度】：排名上升/霸榜 作为叠加，不再单独触发

融合判定：
  估值低估 + 板块升温            → 🟢 加仓确认（估值+资金共振）
  估值低估 + 板块升温 + 热度上升  → 🟢 加仓确认（共振 + 启动，最强）
  估值低估 + 板块降温            → ⚠️ 观望/警惕价值陷阱（便宜但资金在撤）
  估值高估 + 板块升温            → 🔴 减仓/规避确认（贵+资金追 = 拥挤）
  估值高估 + 板块降温            → 🔴 减仓/规避确认
  估值合理 / 数据不足             → ─ 无信号

本模块只产出【展示层的确认/无信号】结果，不直接触发仓位改动。
调用方（analyze_and_suggest）自行决定是否据此手动调整仓位。
==================================================================
"""
import json
import csv
import io
import re
from pathlib import Path
from collections import defaultdict

import requests

INDUSTRY_MAP_PATH = Path(__file__).resolve().parent.parent / "industry_map.json"

MASTER_CSV_URL = (
    "https://raw.githubusercontent.com/ChiaraVan1/xueqiu_hot/main/"
    "xueqiu_data/xueqiu_hot_master.csv"
)
# 【新增】板块轮动数据源：qwenwork 基金工作台的后端接口（服务端拉天天基金指数基金计算）。
# 返回结构：{"ok":true,"result":{"periods":{"w1":{"top":[{"key","code","name","inception","value"}...],
#          "bottom":[...]}, "m1":..., "m3":..., "m6":..., "ytd":...}}}
ROTATION_API_URL = "https://4vh8j4cl.qwenwork.host/api/rotation?qdii=1"

# 只分析「全球」榜单（沪深/港股/美股一律不参与统计）
TARGET_LIST = "全球"

TREND_LOOKBACK_DAYS = 3

_industry_map_cache = None
_hot_rows_cache = {"rows": None, "url": None}
_rotation_cache = None


# =====================================================================
# 0. 数据修复：行业标签归一化（关键 bug 修复）
#    原始 master.csv 里同一只股票可能写不同行业标签，如：
#      美光=「半导体」也=「半导体存储」；药明康德=「医药/医药外包/医药研发」；
#      英伟达=「半导体/芯片」；中际旭创=「光模块/光通信」
#    若不归一化，industry_map.get(行业) 精确匹配会静默丢行 → 样本骤减、趋势断裂。
# =====================================================================
_INDUSTRY_NORM_RULES = [
    ("半导体", ["半导体", "存储", "芯片", "封装", "晶圆", "内存", "闪存"]),
    ("光通信/光模块", ["光模块", "光通信", "光纤", "光学", "通信设备"]),
    ("互联网", ["互联网", "软件", "电商", "云计算", "游戏", "大数据", "社交", "传媒"]),
    ("消费电子", ["消费电子", "电子制造", "电子元件", "电子元器件", "面板", "显示", "PCB"]),
    ("医药", ["医药", "生物", "制药", "医疗"]),
    ("人工智能", ["人工智能", "AI"]),
    ("机器人", ["机器人"]),
    ("消费/潮玩", ["消费", "潮玩", "零售", "玩具"]),
]


def normalize_industry(raw: str) -> str:
    """把碎片化的行业标签归并到顶层主题，返回归一化标签（无法归并则原样返回）。"""
    if not raw:
        return ""
    s = str(raw)
    for canon, kws in _INDUSTRY_NORM_RULES:
        if any(k in s for k in kws):
            return canon
    return s


# =====================================================================
# 0.5 ETF代码 -> 板块轮动 sector 的映射
#     已按 industry_map.json 对齐：只保留有 ERP 标的覆盖的行业，不强行凑映射
#     （白酒/石油/光模块等未列入）。
#     ⚠️ 按 ETF 代码而不是行业标签做键，因为你的 industry_map 里多个标签
#        （半导体/消费电子/面板 → 950125）同指一个代码，按代码映射能消除歧义，
#        调用方也无需再传 industry_label。
#     取值：板块轮动接口中 sector key 的模糊匹配关键词（命中任一即视为该板块）。
#     若某代码在轮动数据里没有对应板块（如生猪、旅游），compute_sector_trend
#     会自动返回「数据不足」并降级，不会误判。
# =====================================================================
ETF_TO_SECTOR = {
    "950125": ["半导体", "芯片", "集成电路", "存储", "电子"],      # 半导体/电子
    "931071": ["人工智能", "AI", "机器人"],                        # AI/机器人
    "930794": ["互联网", "软件", "云计算", "大数据", "恒生互联网"],  # 互联网
    "399989": ["创新药", "医药", "生物科技", "医疗", "疫苗"],       # 医药
    "399967": ["军工", "国防", "航空航天", "卫星"],                 # 军工
    "399975": ["证券", "券商"],                                     # 证券
    "000819": ["有色", "铜", "工业金属"],                            # 有色金属
    "930598": ["稀土", "稀有金属"],                                  # 稀土/工业气体
    "HSTECH": ["恒生科技", "港股科技"],                              # 恒生科技
    "399986": ["银行"],                                              # 银行
    "930633": ["旅游", "出行"],                                      # 旅游
    "980032": ["电池", "锂电", "新能源", "储能"],                    # 新能源电池
    "931946": ["养殖", "畜牧", "生猪"],                              # 生猪
}


def load_industry_map() -> dict:
    """加载 行业标签 -> ETF代码 映射表，跳过 _comment 字段。进程内缓存一次。"""
    global _industry_map_cache
    if _industry_map_cache is not None:
        return _industry_map_cache
    with open(INDUSTRY_MAP_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    _industry_map_cache = {k: v for k, v in raw.items() if not k.startswith("_")}
    return _industry_map_cache


# =====================================================================
# 1. 板块轮动数据拉取（新增）
# =====================================================================
def fetch_sector_rotation(timeout: int = 20, use_cache: bool = True):
    """
    调用板块轮动接口，返回轮动数据字典：
      {
        "computed_at": str,
        "periods": {
          "w1": {"label":"近一周","top":[{"key","code","name","value"},...], "bottom":[...]},
          "m1"/"m3"/"m6"/"ytd"/"y5"/"all": ...
        }
      }
    请求失败返回 None，调用方已做“数据不足”降级，不会中断报告。
    """
    global _rotation_cache
    if use_cache and _rotation_cache is not None:
        return _rotation_cache
    try:
        resp = requests.get(ROTATION_API_URL, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        print(f"⚠️ 拉取板块轮动数据失败（{ROTATION_API_URL}）：{e}")
        return None
    if not (payload.get("ok") and payload.get("result")):
        return None
    result = payload["result"]
    data = {"computed_at": result.get("computedAt"),
            "periods": result.get("periods", {})}
    if use_cache:
        _rotation_cache = data
    return data


def _sector_returns(etf_code: str, rotation: dict) -> dict:
    """
    根据 ETF 代码，从轮动数据里找出对应板块在各周期的涨跌幅。
    返回 {"w1":float|None,"m1":...,"m3":...,"m6":...,"ytd":...}，
    找不到任何匹配周期则为空 dict。
    """
    keywords = ETF_TO_SECTOR.get(etf_code, [])
    if not keywords or not rotation:
        return {}
    periods = rotation.get("periods", {})
    out = {}
    for pkey, pinfo in periods.items():
        if not isinstance(pinfo, dict):
            continue
        best = None
        for lst in ("top", "bottom"):
            for item in pinfo.get(lst, []):
                k = item.get("key", "")
                if any(kw in k for kw in keywords):
                    v = item.get("value")
                    if v is None:
                        continue
                    # 取该板块在该周期最极端的值（涨则领涨、跌则领跌）
                    if best is None or (best < 0 and v < best) or (best >= 0 and v > best):
                        best = v
        if best is not None:
            out[pkey] = best
    return out


def compute_sector_trend(etf_code: str, rotation: dict) -> dict:
    """
    判断某 ETF 对应板块的资金/趋势方向。
    返回：
      {
        "has_data": bool,
        "direction": "升温"|"降温"|"平稳"|None,
        "m3": float|None,   # 近三月涨跌幅（主判据）
        "w1": float|None,   # 近一周涨跌幅（近期加速度）
        "detail": str,
      }
    判定：
      m3 明显为正 且 w1 未明显转负  → 升温（资金流入）
      m3 为负，或 m3 为正但 w1 明显转负 → 降温（资金流出/高位回落）
      其余 → 平稳
    """
    rets = _sector_returns(etf_code, rotation)
    if not rets:
        return {"has_data": False, "direction": None, "m3": None, "w1": None,
                "detail": "板块轮动数据中未找到该代码对应板块，无法判断资金方向。"}
    m3 = rets.get("m3")
    w1 = rets.get("w1")
    direction = "平稳"
    if m3 is not None:
        if m3 >= 8 and (w1 is None or w1 >= 0):
            direction = "升温"
        elif m3 <= -8 or (w1 is not None and w1 <= -5):
            direction = "降温"
        elif m3 >= 0:
            direction = "平稳偏强"
        else:
            direction = "平稳偏弱"
    detail = (f"对应板块近三月 {m3:+.1f}%、近一周 {w1:+.1f}%"
              if m3 is not None or w1 is not None else "数据不足")
    return {"has_data": True, "direction": direction, "m3": m3, "w1": w1, "detail": detail}


# =====================================================================
# 2. 热榜数据（沿用，但加入归一化 + 去重）
# =====================================================================
def load_hot_rows(master_csv_url: str = MASTER_CSV_URL, timeout: int = 15,
                  use_cache: bool = True) -> list[dict]:
    """直接从 xueqiu_hot 仓库 raw 地址拉取 master.csv 并解析。失败返回 []。"""
    if use_cache and _hot_rows_cache["rows"] is not None and _hot_rows_cache["url"] == master_csv_url:
        return _hot_rows_cache["rows"]
    try:
        resp = requests.get(master_csv_url, timeout=timeout)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"⚠️ 拉取热榜数据失败（{master_csv_url}）：{e}")
        return []
    text = resp.content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    if use_cache:
        _hot_rows_cache["rows"] = rows
        _hot_rows_cache["url"] = master_csv_url
    return rows


def _dedup_snapshots(rows: list[dict]) -> list[dict]:
    """
    【新增】只保留 TARGET_LIST（全球）榜单的行，并去掉重复快照：
    7-30、9-14 等日期同一榜单被抓了两次（18 行），每个 (日期, 榜单) 只保留前 9 名。
    """
    per_key = defaultdict(list)
    for r in rows:
        if r.get("榜单") != TARGET_LIST:
            continue
        per_key[(r.get("日期"), r.get("榜单"))].append(r)
    out = []
    for v in per_key.values():
        out.extend(v[:9])
    return out


def _rows_by_etf_code(rows: list[dict], industry_map: dict) -> dict:
    """
    按 ETF代码 分组。注意：查映射表前先把 行业标签 归一化，
    避免「半导体」vs「半导体存储」这类碎片标签导致精确匹配丢行。
    """
    rows = _dedup_snapshots(rows)
    grouped = defaultdict(list)
    for r in rows:
        etf_code = industry_map.get(normalize_industry(r.get("行业", "")))
        if not etf_code:
            continue
        try:
            rank = int(r["排名"])
        except (ValueError, KeyError):
            continue
        grouped[etf_code].append({
            "日期": r["日期"],
            "排名": rank,
            "涨跌幅": float(r["涨跌幅(%)"]) if r.get("涨跌幅(%)") not in (None, "") else None,
            "成交额": float(r["成交额(亿)"]) if r.get("成交额(亿)") not in (None, "") else None,
            "股票代码": r.get("股票代码", ""),
            "股票名称": r.get("股票名称", ""),
        })
    for etf_code in grouped:
        grouped[etf_code].sort(key=lambda x: x["日期"])
    return grouped


def compute_rank_trend(etf_code: str, rows: list[dict], industry_map: dict,
                        lookback_days: int = TREND_LOOKBACK_DAYS) -> dict:
    """计算最近 lookback_days 天内该行业最好排名的变化（排名数字变小=热度上升）。"""
    grouped = _rows_by_etf_code(rows, industry_map)
    series = grouped.get(etf_code, [])
    if not series:
        return {"has_data": False, "rank_rising": None,
                "best_rank_start": None, "best_rank_end": None,
                "avg_pct_change": None, "sample_days": 0}
    dates = sorted(set(r["日期"] for r in series))
    recent_dates = dates[-lookback_days:]
    if len(recent_dates) < 2:
        return {"has_data": True, "rank_rising": None,
                "best_rank_start": None, "best_rank_end": None,
                "avg_pct_change": None, "sample_days": len(recent_dates)}

    def best_rank_on(date):
        day_rows = [r for r in series if r["日期"] == date]
        return min(r["排名"] for r in day_rows) if day_rows else None

    start_rank = best_rank_on(recent_dates[0])
    end_rank = best_rank_on(recent_dates[-1])
    pct_values = [r["涨跌幅"] for r in series
                  if r["日期"] in recent_dates and r["涨跌幅"] is not None]
    avg_pct = sum(pct_values) / len(pct_values) if pct_values else None
    rank_rising = None
    if start_rank is not None and end_rank is not None:
        rank_rising = end_rank < start_rank  # 数字变小 = 排名上升
    return {"has_data": True, "rank_rising": rank_rising,
            "best_rank_start": start_rank, "best_rank_end": end_rank,
            "avg_pct_change": avg_pct, "sample_days": len(recent_dates)}


# =====================================================================
# 3. 三因子融合核心函数（重写）
# =====================================================================
def compute_popularity_confirmation(etf_code: str, erp_percentile: float,
                                     rows: list[dict] = None,
                                     industry_map: dict = None,
                                     rotation: dict = None) -> dict:
    """
    三因子融合：
      erp_percentile      : ERP历史分位(0~1)，越高越便宜（与 analyze_and_suggest 定义一致）
      rotation            : 板块轮动数据（fetch_sector_rotation() 返回值）；不传则跳过该因子
    返回 {signal, icon, detail}：加仓确认/减仓确认/观望/无信号/数据不足
    """
    if rows is None:
        rows = load_hot_rows()
    if industry_map is None:
        industry_map = load_industry_map()

    # 热榜排名趋势
    trend = compute_rank_trend(etf_code, rows, industry_map)
    rank_rising = trend.get("rank_rising")

    # 板块轮动方向（可选因子，直接按 ETF 代码查）
    rot = None
    if rotation is not None:
        rot = compute_sector_trend(etf_code, rotation)

    # ---- 数据不足兜底 ----
    if not trend.get("has_data"):
        return {"signal": "数据不足", "icon": "─",
                "detail": "热榜数据不足（该行业近期未上榜或样本过少），跳过人气信号判断。"}

    rot_detail = f"｜{rot['detail']}" if rot else ""
    rank_detail = f"（{trend.get('best_rank_start')}→{trend.get('best_rank_end')}）" \
        if trend.get("best_rank_start") is not None else ""

    # ---- 估值方向 ----
    if erp_percentile >= 0.75:
        val = "低估"
    elif erp_percentile <= 0.25:
        val = "高估"
    else:
        val = "合理"

    # ---- 融合判定 ----
    # 板块轮动可用：以「估值 × 资金」共振为主，热榜做叠加
    if rot and rot.get("has_data"):
        direction = rot.get("direction")
        if val == "低估":
            if direction in ("升温", "平稳偏强"):
                if rank_rising:
                    return {"signal": "加仓确认", "icon": "🟢",
                            "detail": f"ERP{erp_percentile:.0%}分位(低估) + 板块轮动{direction}"
                                      f"({rot['detail']}) + 热榜排名上升{rank_detail} → "
                                      "估值与资金共振且关注度启动，加仓确认。"}
                return {"signal": "加仓确认", "icon": "🟢",
                        "detail": f"ERP{erp_percentile:.0%}分位(低估) + 板块轮动{direction}"
                                  f"({rot['detail']}) → 估值与资金共振，加仓确认。"}
            if direction == "降温":
                return {"signal": "观望", "icon": "⚠️",
                        "detail": f"ERP{erp_percentile:.0%}分位(低估)但板块轮动{direction}"
                                  f"({rot['detail']}) → 便宜但资金在撤，警惕价值陷阱，观望。"}
            return {"signal": "无信号", "icon": "─",
                    "detail": f"ERP{erp_percentile:.0%}分位(低估) + 板块轮动{direction}"
                              f"({rot['detail']}) → 资金方向偏弱，暂不确认。"}
        if val == "高估":
            if direction in ("升温", "平稳偏强"):
                return {"signal": "减仓确认", "icon": "🔴",
                        "detail": f"ERP{erp_percentile:.0%}分位(高估) + 板块轮动{direction}"
                                  f"({rot['detail']}) → 贵且资金在追，拥挤风险，减仓/规避确认。"}
            return {"signal": "减仓确认", "icon": "🔴",
                    "detail": f"ERP{erp_percentile:.0%}分位(高估) + 板块轮动{direction}"
                              f"({rot['detail']}) → 高估且资金转弱，减仓/规避确认。"}
        # 合理区间
        return {"signal": "无信号", "icon": "─",
                "detail": f"ERP{erp_percentile:.0%}分位(合理区间)，估值不够极端"
                          f"{rot_detail}，不构成加仓/减仓确认。"}

    # ---- 板块轮动不可用时：退化为「估值 + 热榜排名」双因子（保守版）----
    if not trend.get("rank_rising"):
        return {"signal": "无信号", "icon": "─",
                "detail": f"热榜排名未见持续上升{rank_detail}{rot_detail}，关注度未提升，不构成确认。"}
    if val == "低估":
        return {"signal": "加仓确认", "icon": "🟢",
                "detail": f"ERP{erp_percentile:.0%}分位(低估) + 热榜排名上升{rank_detail} → "
                          "低估且关注度提升（无板块轮动佐证，弱确认）。"}
    if val == "高估":
        return {"signal": "减仓确认", "icon": "🔴",
                "detail": f"ERP{erp_percentile:.0%}分位(高估) + 热榜排名上升{rank_detail} → "
                          "高估且关注度上升，减仓/规避确认。"}
    return {"signal": "无信号", "icon": "─",
            "detail": f"ERP{erp_percentile:.0%}分位(合理区间)，不构成确认。"}


def build_popularity_block(etf_code: str, erp_percentile: float,
                            rows: list[dict] = None,
                            industry_map: dict = None,
                            precomputed: dict = None,
                            rotation: dict = None) -> str:
    """生成可直接插入报告的 Markdown 区块。"""
    result = precomputed if precomputed is not None else \
        compute_popularity_confirmation(etf_code, erp_percentile, rows, industry_map,
                                        rotation)
    return f"""
---
### 热榜人气信号（辅助确认，非独立交易依据）
> 规则（三因子融合）：估值方向(ERP分位) × 资金方向(板块轮动) 共振 → 加仓/减仓确认；
> 热榜排名上升作为关注度叠加；其余为「无信号」。
{result['icon']} **{result['signal']}**
{result['detail']}
"""


if __name__ == "__main__":
    # 自测：加载映射表 + 板块轮动，跑一次半导体（模拟 ERP 分位=0.85 低估场景）
    industry_map = load_industry_map()
    rows = load_hot_rows()
    rotation = fetch_sector_rotation()
    print(f"已加载映射表 {len(industry_map)} 个行业标签")
    print(f"已加载热榜记录 {len(rows)} 条（去重后 {len(_dedup_snapshots(rows))} 条）")
    print(f"板块轮动数据：{'已加载' if rotation else '失败'}")

    test_code = "950125"
    print(f"\n测试：{test_code} 模拟 ERP 分位=0.85（低估）：")
    print(compute_popularity_confirmation(test_code, 0.85,
                                          rows=rows, industry_map=industry_map,
                                          rotation=rotation))
    print(f"\n测试：{test_code} 模拟 ERP 分位=0.10（高估）：")
    print(compute_popularity_confirmation(test_code, 0.10,
                                          rows=rows, industry_map=industry_map,
                                          rotation=rotation))
