"""
用富途API(市值) + akshare/东方财富(营收TTM) 计算指数级 PS,写入 data/ps_<INDEX>.csv

用法:
    python fetch_ps_futu.py --index HSTECH

依赖:
    pip install futu-api akshare pandas

运行前提:
    本机需已打开 Futu OpenD 并保持"已连接"状态(你截图里的11111端口),
    本脚本连的是 127.0.0.1:11111,和OpenD在同一台机器上跑才能连上。

输出格式对齐现有 data/ps_HSTECH.csv:
    date, ps, rf, psy
    其中 psy = 1/ps - rf,rf沿用你项目里已有的无风险利率序列/常量。

设计取舍:
    - 市值(market_val):实时,来自富途 get_market_snapshot,精度高、免费、无需财报延迟。
    - 营收TTM(revenue_ttm):富途行情API不提供财务报表原始数据(只有pe/pb等衍生比率,没有ps/revenue),
      所以这部分继续走 akshare(项目里已经在用)或东方财富财报接口,季度更新,比市值滞后是正常的。
    - PS_index = Σ(市值_i) / Σ(营收TTM_i),即"指数总市值 / 指数总营收",
      等价于按市值加权的组合PS,和恒生/中证官方口径一致。

已知限制(demo阶段,合并前务必人工核对):
    1. 成分股列表+权重需要定期跟随指数季度调仓更新,这里给了一个占位的
       get_index_constituents(),需要换成你项目里实际可用的成分股数据源。
    2. 营收字段在不同市场(港股/美股/A股)的财报口径、发布节奏不一致,
       需要按市场分别处理"最新一期TTM"的对齐逻辑。
    3. 富途A股(上证/深证)只有LV1权限,get_market_snapshot部分字段在LV1下
       是否完整返回,需要实测确认(HK LV2 / US LV3下没有问题)。
"""

import argparse
import time
from datetime import datetime

import pandas as pd

try:
    from futu import OpenQuoteContext, RET_OK
except ImportError:
    raise SystemExit("请先 pip install futu-api")

try:
    import akshare as ak
except ImportError:
    ak = None


FUTU_HOST = "127.0.0.1"
FUTU_PORT = 11111


# ══════════════════════════════════════════════════════════════════════
# 1. 成分股 + 权重  —— 占位实现,需替换为项目里实际可用的数据源
# ══════════════════════════════════════════════════════════════════════

def get_index_constituents(index_name: str) -> pd.DataFrame:
    """
    返回 DataFrame,列: code(富途格式,如 'HK.00700')、weight(0~1,加总应≈1)。

    TODO: 换成真实数据源,例如:
      - 恒生指数公司官网公开的成分股权重文件(HSTECH等)
      - akshare 里对应的指数成分股接口(如有)
      - 中证指数官网的成分股权重文件(中证系列指数)
    当前先用等权占位,只是为了让脚本能跑通,不能直接用于生产。
    """
    raise NotImplementedError(
        "请填入真实成分股+权重数据源后再运行,当前仅为脚本骨架。"
    )


# ══════════════════════════════════════════════════════════════════════
# 2. 市值 —— 富途 get_market_snapshot
# ══════════════════════════════════════════════════════════════════════

def get_market_val_futu(codes: list[str]) -> dict[str, float]:
    """
    批量拉取市值(元),code需为富途格式,如 'HK.00700' / 'US.AAPL' / 'SH.600519'。
    富途 get_market_snapshot 单次最多支持约200个代码,超过需分批。
    """
    quote_ctx = OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
    result = {}
    try:
        batch_size = 200
        for i in range(0, len(codes), batch_size):
            batch = codes[i : i + batch_size]
            ret, data = quote_ctx.get_market_snapshot(batch)
            if ret != RET_OK:
                raise RuntimeError(f"get_market_snapshot 失败: {data}")
            for _, row in data.iterrows():
                # total_market_val: 总市值(元)
                result[row["code"]] = row.get("total_market_val", None)
            time.sleep(0.5)  # 简单限流,避免触发频率限制
    finally:
        quote_ctx.close()
    return result


# ══════════════════════════════════════════════════════════════════════
# 3. 营收TTM —— akshare(占位,按市场分流)
# ══════════════════════════════════════════════════════════════════════

def get_revenue_ttm(codes: list[str]) -> dict[str, float]:
    """
    返回 {code: revenue_ttm(元)}。

    TODO: 按 code 前缀(HK./US./SH./SZ.)分流到不同的 akshare 财务接口,
    或改用东方财富财报接口(本次session里已连的 mcp__eastmoney__query_reports /
    download_reports 也可以作为数据源,如果决定用它,需要把这部分改成异步/
    离线批量拉取,而不是在这个同步脚本里直接调MCP工具)。
    当前留空抛异常,提醒必须先补上真实实现。
    """
    raise NotImplementedError(
        "请填入真实营收TTM数据源(akshare财务接口或东方财富财报接口)后再运行。"
    )


# ══════════════════════════════════════════════════════════════════════
# 4. 聚合计算 PS,并按现有CSV格式追加/合并
# ══════════════════════════════════════════════════════════════════════

def calc_index_ps(market_val: dict, revenue_ttm: dict) -> float:
    """PS_index = Σ市值 / Σ营收TTM,自动跳过任一侧缺失的成分股。"""
    total_mv, total_rev = 0.0, 0.0
    for code, mv in market_val.items():
        rev = revenue_ttm.get(code)
        if mv is None or rev is None or rev <= 0:
            continue
        total_mv += mv
        total_rev += rev
    if total_rev <= 0:
        raise ValueError("有效营收合计为0,无法计算PS")
    return total_mv / total_rev


def upsert_ps_row(csv_path: str, date_str: str, ps: float, rf: float):
    """把新的一行 ps 写入/更新到现有 CSV,格式对齐 date,ps,rf,psy。"""
    psy = 1.0 / ps - rf
    new_row = {"date": date_str, "ps": ps, "rf": rf, "psy": psy}

    try:
        df = pd.read_csv(csv_path)
    except FileNotFoundError:
        df = pd.DataFrame(columns=["date", "ps", "rf", "psy"])

    df = df[df["date"] != date_str]  # 避免重复,同日期覆盖
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    df = df.sort_values("date")
    df.to_csv(csv_path, index=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", default="HSTECH", help="指数名称,对应 data/ps_<INDEX>.csv")
    parser.add_argument("--rf", type=float, required=True, help="当期无风险利率,沿用项目现有序列")
    args = parser.parse_args()

    cons = get_index_constituents(args.index)
    codes = cons["code"].tolist()

    market_val = get_market_val_futu(codes)
    revenue_ttm = get_revenue_ttm(codes)

    ps = calc_index_ps(market_val, revenue_ttm)
    date_str = datetime.now().strftime("%Y-%m-%d")

    csv_path = f"./data/ps_{args.index}.csv"
    upsert_ps_row(csv_path, date_str, ps, args.rf)
    print(f"[{date_str}] {args.index} PS = {ps:.4f} 已写入 {csv_path}")


if __name__ == "__main__":
    main()
