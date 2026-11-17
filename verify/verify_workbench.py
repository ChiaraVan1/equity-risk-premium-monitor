#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/verify_workbench.py — ERP 工作台数据核验（SOP v0.3）

六层检查 L0–L5 + 独立源抽查 S。
只读：不修数据、不重跑取数、不改 config。

用法：
    python3 verify/verify_workbench.py                 # 全量
    python3 verify/verify_workbench.py --no-online     # 跳过需联网的抽查
    python3 verify/verify_workbench.py --dry-run       # 只打印，不写文件、不推送
    python3 verify/verify_workbench.py --push          # 有 FAIL/WARN 时发一条 PushPlus 微信

退出码：0 = 全 PASS 或仅 SKIP；1 = 有 FAIL；2 = 有 WARN 无 FAIL
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DOCS = os.path.join(ROOT, "docs")
VERIFY = os.path.join(ROOT, "verify")
OUT_MD = os.path.join(VERIFY, "REPORT.md")
OUT_JSON = os.path.join(VERIFY, "report.json")

# 判据常量（改这里就是改 SOP）
TH_FRESH_REPORT_H = 26          # 校验器自身 ≤26h
TH_ERP_COMMIT = "18:50"         # ERP commit 时点
TH_ERP_ID_BP = 2.0              # 恒等式容差 (bp)
TH_ERP_ID_WARN_BP = 10.0
TH_PCT_DIFF = 1.0               # 涨跌幅交叉容差 (pct)
TH_PCT_FAIL = 2.0
TH_PAYBACK = 0.02               # 回本价容差 (元)
TH_PORTFOLIO_DAYS = 45
TH_REPORT_KB = 20
BOND_RANGE = (0.0, 0.2)         # 小数口径
ERP_RANGE = (-0.15, 0.35)
TIPS_RANGE = (-2.0, 8.0)


# ══════════════════════════════════════ 基础工具 ══════════════════════════

def p(*a):
    print(*a)


def read_csv(path):
    """返回 (header, rows)；不存在返回 (None, None)。自动去 BOM。"""
    if not os.path.exists(path):
        return None, None
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        r = list(csv.reader(f))
    if not r:
        return [], []
    return [c.strip() for c in r[0]], r[1:]


def parse_date(s):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def to_float(s):
    try:
        v = float((s or "").strip())
        return v
    except (TypeError, ValueError):
        return None


def biz_days_between(a, b):
    """a→b 之间的工作日数（不含 a，含 b）。b>a 为正。"""
    if a is None or b is None:
        return None
    sign = 1 if b >= a else -1
    lo, hi = (a, b) if a <= b else (b, a)
    n = 0
    while lo < hi:
        lo += timedelta(days=1)
        if lo.weekday() < 5:
            n += 1
    return sign * n


def latest_date_of(path, date_col_index=0):
    _, rows = read_csv(path)
    if not rows:
        return None
    best = None
    for r in rows:
        if len(r) <= date_col_index:
            continue
        d = parse_date(r[date_col_index])
        if d and (best is None or d > best):
            best = d
    return best


def col_index(header, name):
    for i, h in enumerate(header or []):
        if h == name:
            return i
    return None


class Collector:
    def __init__(self):
        self.items = []

    def add(self, layer, cid, name, expected, actual, verdict, evidence=""):
        self.items.append({
            "layer": layer, "id": cid, "name": name,
            "expected": expected, "actual": actual,
            "verdict": verdict, "evidence": evidence,
        })

    def counts(self):
        c = {"PASS": 0, "WARN": 0, "FAIL": 0, "SKIP": 0}
        for it in self.items:
            c[it["verdict"]] = c.get(it["verdict"], 0) + 1
        return c

    def overall(self):
        c = self.counts()
        if c["FAIL"]:
            return "FAIL"
        if c["WARN"]:
            return "WARN"
        return "PASS"


# ══════════════════════════════════════ 配置装载 ══════════════════════════

def load_config():
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg["indices"]


def erp_files(cfg):
    """{code: path}，只收存在的文件。"""
    out = {}
    for it in cfg:
        path = os.path.join(DATA, f"erp_{it['code']}.csv")
        if os.path.exists(path):
            out[it["code"]] = path
    return out


def is_stock(code):
    return len(code) == 6 and code.isdigit() and code[0] == "6"


# ══════════════════════════════════════ L0 运行层 ══════════════════════════

def gh_api(path, token=None, timeout=15):
    url = "https://api.github.com" + path
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "verify-workbench",
    })
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def layer_L0(c, cfg, args, data_day):
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")

    # L0-1 当日批次是否成功
    if token and repo:
        try:
            today = datetime.now(CST).date()
            runs = gh_api(f"/repos/{repo}/actions/workflows/daily_trade.yml/runs?per_page=10",
                          token)["workflow_runs"]
            todays = [r for r in runs if r["created_at"][:10] >= str(today - timedelta(days=1))]
            if not todays:
                c.add("L0", "L0-1", "当日批次存在", "至少 1 次", "无", "FAIL",
                      "近 2 日无 daily_trade 运行记录")
            else:
                bad = [r for r in todays if r["conclusion"] not in ("success", None)]
                ok = [r for r in todays if r["conclusion"] == "success"]
                v = "PASS" if ok and not bad else "FAIL"
                c.add("L0", "L0-1", "当日批次成功", "conclusion=success",
                      f"成功 {len(ok)} / 异常 {len(bad)}", v,
                      f"run ids: {[r['id'] for r in todays][:3]}")
        except Exception as e:
            c.add("L0", "L0-1", "当日批次成功", "conclusion=success", "查询失败", "SKIP", f"{e}")
    else:
        c.add("L0", "L0-1", "当日批次成功", "conclusion=success", "无 token/仓库名", "SKIP",
              "本地运行无 GITHUB_TOKEN，需在 Actions 内跑")

    # L0-2 校验器自身新鲜度
    fr_path = os.path.join(DATA, "freshness_report.json")
    if not os.path.exists(fr_path):
        c.add("L0", "L0-2", "校验器自身在跑", "≤26h", "文件缺失", "FAIL", "data/freshness_report.json 不存在")
    else:
        try:
            fr = json.load(open(fr_path, encoding="utf-8"))
            gen = datetime.fromisoformat(fr["generated_at"])
            age = (datetime.now() - gen).total_seconds() / 3600
            v = "PASS" if age <= TH_FRESH_REPORT_H else ("WARN" if age <= 48 else "FAIL")
            c.add("L0", "L0-2", "校验器自身在跑", "≤26h",
                  f"{age:.1f}h 前（{fr['generated_at'][:19]}）", v, "generated_at")
        except Exception as e:
            c.add("L0", "L0-2", "校验器自身在跑", "≤26h", "解析失败", "FAIL", str(e))

    # L0-3 ERP 完成时点（需 API）
    if token and repo:
        try:
            commits = gh_api(f"/repos/{repo}/commits?per_page=5", token)
            day_commits = [x for x in commits
                           if x["commit"]["committer"]["date"][:10]
                           >= str(datetime.now(CST).date() - timedelta(days=1))]
            if day_commits:
                t = day_commits[0]["commit"]["committer"]["date"]
                dt = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(CST)
                v = "PASS" if dt.strftime("%H:%M") <= TH_ERP_COMMIT else "WARN"
                c.add("L0", "L0-3", "ERP 完成时点", f"≤{TH_ERP_COMMIT}", dt.strftime("%H:%M") + " CST", v,
                      day_commits[0]["sha"][:7])
            else:
                c.add("L0", "L0-3", "ERP 完成时点", f"≤{TH_ERP_COMMIT}", "近 2 日无 commit", "FAIL", "")
        except Exception as e:
            c.add("L0", "L0-3", "ERP 完成时点", f"≤{TH_ERP_COMMIT}", "查询失败", "SKIP", str(e))
    else:
        c.add("L0", "L0-3", "ERP 完成时点", f"≤{TH_ERP_COMMIT}", "无 token", "SKIP", "")

    # L0-4 report.html 日期 = 数据日
    rp = os.path.join(DOCS, "report.html")
    if not os.path.exists(rp):
        c.add("L0", "L0-4", "报告产物日期", f"={data_day}", "report.html 缺失", "FAIL", "")
    else:
        html = open(rp, encoding="utf-8", errors="ignore").read()
        dates = set()
        import re
        for m in re.findall(r"20\d{2}-\d{2}-\d{2}", html):
            d = parse_date(m)
            if d:
                dates.add(d)
        hit = data_day in dates if data_day else False
        if hit:
            c.add("L0", "L0-4", "报告产物日期", f"={data_day}", f"含 {data_day}", "PASS", "docs/report.html")
        else:
            lag = max(dates) if dates else None
            diff = biz_days_between(data_day, lag) if (lag and data_day) else None
            v = "WARN" if (diff is not None and abs(diff) <= 1) else "FAIL"
            c.add("L0", "L0-4", "报告产物日期", f"={data_day}",
                  f"最近日期 {lag}", v, f"日期集尾 {sorted(dates)[-3:] if dates else '无'}")


# ══════════════════════════════════════ L1 取数层 ══════════════════════════

OVERSEAS = {"SPY", "QQQ", "EWQ", "EWG", "EWJ", "EEM"}


def layer_L1(c, cfg, data_day):
    files = erp_files(cfg)
    cfg_codes = [it["code"] for it in cfg]

    # L1-1 / L1-2 新鲜度
    dom_stale, ovs_stale = [], []
    for code, path in files.items():
        d = latest_date_of(path)
        if d is None:
            c.add("L1", "L1-1", f"新鲜度 {code}", "有日期", "无有效日期", "FAIL", path)
            continue
        if code in OVERSEAS:
            # US 收盘价落次日 CST，属结构性偏移，扣 1 个工作日再判
            raw = biz_days_between(d, datetime.now(CST).date())
            lag = max(0, raw - 1)
            ovs_stale.append((code, d, lag))
        else:
            lag = biz_days_between(d, datetime.now(CST).date())
            dom_stale.append((code, d, lag))

    worst = max(dom_stale, key=lambda x: x[2]) if dom_stale else None
    if worst:
        lag = worst[2]
        v = "PASS" if lag <= 1 else ("WARN" if lag == 2 else "FAIL")
        bad = [x for x in dom_stale if x[2] > 1]
        c.add("L1", "L1-1", "A股/ETF 新鲜度", "落后 ≤1 交易日",
              f"最陈旧 {worst[0]} 落后 {lag} 交易日；超 1 日 {len(bad)} 只", v,
              f"data_day={data_day}；示例 {[(x[0], str(x[1]), x[2]) for x in sorted(dom_stale, key=lambda y: -y[2])[:3]]}")

    worst = max(ovs_stale, key=lambda x: x[2]) if ovs_stale else None
    if worst:
        lag = worst[2]
        v = "PASS" if lag <= 1 else ("WARN" if lag == 2 else "FAIL")
        c.add("L1", "L1-2", "海外/FRED 新鲜度", "落后 ≤1 交易日",
              f"最陈旧 {worst[0]} 落后 {lag} 交易日", v,
              f"样例 {[(x[0], str(x[1]), x[2]) for x in sorted(ovs_stale, key=lambda y: -y[2])[:3]]}")

    # L1-3 关键列非空（末 3 行 PE/ERP/Bond）
    empties, all_empty = [], []
    for code, path in files.items():
        hdr, rows = read_csv(path)
        if not rows:
            continue
        iPE, iERP, iB = col_index(hdr, "PE"), col_index(hdr, "ERP"), col_index(hdr, "Bond_Yield_10Y")
        if iPE is None or iERP is None or iB is None:
            continue
        miss_rows = 0
        for r in rows[-3:]:
            vals = [r[i] if i < len(r) else "" for i in (iPE, iERP, iB)]
            if all((v or "").strip() == "" for v in vals):
                miss_rows += 1
        if miss_rows >= 3:
            all_empty.append(code)
        elif miss_rows > 0:
            empties.append(code)
    v = "FAIL" if all_empty else ("WARN" if empties else "PASS")
    c.add("L1", "L1-3", "关键列非空", "末3行 PE/ERP/Bond 有空 → WARN，全空 → FAIL",
          f"全空 {len(all_empty)} 只、部分空 {len(empties)} 只", v,
          f"全空 {all_empty[:5]}；部分空 {empties[:5]}")

    # L1-4 量纲（erp_*.csv：小数口径）
    bond_bad, erp_bad = [], []
    for code, path in files.items():
        hdr, rows = read_csv(path)
        iB, iERP = col_index(hdr, "Bond_Yield_10Y"), col_index(hdr, "ERP")
        last_b = last_e = None
        for r in reversed(rows or []):
            if iB is not None and iB < len(r) and last_b is None:
                last_b = to_float(r[iB])
            if iERP is not None and iERP < len(r) and last_e is None:
                last_e = to_float(r[iERP])
            if last_b is not None and last_e is not None:
                break
        if last_b is not None and not (BOND_RANGE[0] <= last_b <= BOND_RANGE[1]):
            bond_bad.append((code, last_b))
        if last_e is not None and not (ERP_RANGE[0] <= last_e <= ERP_RANGE[1]):
            erp_bad.append((code, last_e))
    v = "FAIL" if (bond_bad or erp_bad) else "PASS"
    c.add("L1", "L1-4", "量纲 erp(小数)", f"Bond∈{BOND_RANGE}, ERP∈{ERP_RANGE}",
          f"Bond 越界 {len(bond_bad)}、ERP 越界 {len(erp_bad)}", v,
          f"越界样例 {(bond_bad + erp_bad)[:4]}")

    # L1-5 量纲（tips：百分数口径）
    tp = os.path.join(DATA, "tips_10y.csv")
    if os.path.exists(tp):
        hdr, rows = read_csv(tp)
        bad = []
        idxs = [col_index(hdr, n) for n in ("real_yield", "nominal_10y", "inflation_expectation")]
        idxs = [i for i in idxs if i is not None]
        for r in rows[-3:]:
            for i in idxs:
                v0 = to_float(r[i]) if i < len(r) else None
                if v0 is not None and not (TIPS_RANGE[0] <= v0 <= TIPS_RANGE[1]):
                    bad.append((r[0], hdr[i], v0))
        c.add("L1", "L1-5", "量纲 tips(百分数)", f"三列 ∈{TIPS_RANGE}",
              f"越界 {len(bad)} 个", "FAIL" if bad else "PASS", f"{bad[:4]}")
    else:
        c.add("L1", "L1-5", "量纲 tips(百分数)", "文件存在", "缺失", "FAIL", "data/tips_10y.csv")

    # L1-6 个股收盘价 / L1-7 指数收盘价
    stock_bad, idx_bad, idx_soft = [], [], []
    for code, path in files.items():
        hdr, rows = read_csv(path)
        ic = col_index(hdr, "IndexClose")
        if ic is None or not rows:
            continue
        # 末"有效"收盘价：跳过尾部空行（周末补行的空值不算缺失）
        last_val, last_dt = None, None
        for r in reversed(rows):
            v = r[ic] if ic < len(r) else ""
            if (v or "").strip() != "":
                last_val, last_dt = v.strip(), parse_date(r[0])
                break
        if last_val is None:
            (stock_bad if is_stock(code) else idx_bad).append((code, "从未有收盘价"))
        elif last_dt and data_day and biz_days_between(last_dt, data_day) > 0:
            lag = biz_days_between(last_dt, data_day)
            (stock_bad if is_stock(code) else idx_soft).append((code, f"末值 {last_dt} 落后 {lag} 日"))
    c.add("L1", "L1-6", "个股收盘价", "末个有效收盘价不落后数据日",
          f"问题 {len(stock_bad)} 只 {stock_bad}", "FAIL" if stock_bad else "PASS",
          f"data_day={data_day}")
    c.add("L1", "L1-7", "指数收盘价", "为空/落后仅 WARN",
          f"无值 {len(idx_bad)}、末值落后 {len(idx_soft)}；示例 {(idx_bad + idx_soft)[:2]}",
          "WARN" if (idx_bad or idx_soft) else "PASS", "价格主用 etf_price.csv")

    # L1-8 PE 卡住
    sp = os.path.join(DATA, "_pe_value_streak.json")
    if os.path.exists(sp):
        try:
            st = json.load(open(sp))
            over = [k for k, v in st.items() if isinstance(v, dict) and v.get("PE", 0) >= 3]
            c.add("L1", "L1-8", "PE 数值卡住", "streak<3",
                  f"连续 3 次不变 {len(over)} 只", "WARN" if over else "PASS",
                  f"{over[:6]}{'…' if len(over) > 6 else ''}")
        except Exception as e:
            c.add("L1", "L1-8", "PE 数值卡住", "可解析", "解析失败", "WARN", str(e))
    else:
        c.add("L1", "L1-8", "PE 数值卡住", "文件存在", "缺失", "SKIP", "data/_pe_value_streak.json")

    # L1-9 覆盖率
    must = [it["code"] for it in cfg
            if not it.get("payback_candidate") and it["code"] not in OVERSEAS]
    alias = {"HSTECH": "ps_HSTECH.csv"}   # 港股互联网用 PS 口径，文件不叫 erp_*
    missing = [cd for cd in must if cd not in files
               and not os.path.exists(os.path.join(DATA, alias.get(cd, "")))]
    c.add("L1", "L1-9", "标的覆盖", f"在监 {len(must)} 只全覆盖",
          f"缺文件 {len(missing)} 只 {missing[:5]}", "FAIL" if missing else "PASS",
          f"config={len(cfg_codes)}（含 {len(cfg_codes)-len(must)} 只股息候选/海外），文件={len(files)}")

    # L1-10 末行日期是否交易日（周末补行会把数据日整体推后）
    weekend = []
    for code, path in files.items():
        if code in OVERSEAS or code == "HSTECH":
            continue
        d = latest_date_of(path)
        if d and d.weekday() >= 5:
            weekend.append((code, str(d)))
    c.add("L1", "L1-10", "末行须为交易日", "无周末行",
          f"末行落在周末 {len(weekend)} 只", "WARN" if weekend else "PASS",
          f"{weekend[:5]}")


# ══════════════════════════════════════ L2 计算层 ══════════════════════════

def layer_L2(c, cfg, data_day):
    # L2-1 ERP 恒等式
    bad, warn, worst = [], [], (0, None)
    for code in [it["code"] for it in cfg]:
        path = os.path.join(DATA, f"erp_{code}.csv")
        if not os.path.exists(path):
            continue
        hdr, rows = read_csv(path)
        iPE, iB, iE = col_index(hdr, "PE"), col_index(hdr, "Bond_Yield_10Y"), col_index(hdr, "ERP")
        if None in (iPE, iB, iE):
            continue
        for r in reversed(rows):
            pe = to_float(r[iPE]) if iPE < len(r) else None
            b = to_float(r[iB]) if iB < len(r) else None
            e = to_float(r[iE]) if iE < len(r) else None
            if None in (pe, b, e) or pe == 0:
                continue
            calc = 1 / pe - b
            diff_bp = abs(calc - e) * 10000
            if diff_bp > worst[0]:
                worst = (diff_bp, code)
            if diff_bp > TH_ERP_ID_WARN_BP:
                bad.append((code, round(diff_bp, 1)))
            elif diff_bp > TH_ERP_ID_BP:
                warn.append((code, round(diff_bp, 1)))
            break
    v = "FAIL" if bad else ("WARN" if warn else "PASS")
    c.add("L2", "L2-1", "ERP 恒等式", f"误差 ≤{TH_ERP_ID_BP}bp（>{TH_ERP_ID_WARN_BP}bp FAIL）",
          f"最大 {worst[0]:.1f}bp @{worst[1]}；超 {TH_ERP_ID_BP}bp {len(warn)} 只、超 {TH_ERP_ID_WARN_BP}bp {len(bad)} 只",
          v, f"样例 {(bad + warn)[:4]}")

    # L2-2 涨跌幅交叉：index_pct vs etf_price 同日变动
    ip_path, ep_path = os.path.join(DATA, "index_pct.csv"), os.path.join(DATA, "etf_price.csv")
    if not (os.path.exists(ip_path) and os.path.exists(ep_path)):
        c.add("L2", "L2-2", "涨跌幅交叉", "两文件存在", "缺失", "FAIL", "")
    else:
        h_ip, r_ip = read_csv(ip_path)
        h_ep, r_ep = read_csv(ep_path)
        ip_map = {r[0]: r for r in r_ip if r and parse_date(r[0])}
        ep_rows = [r for r in r_ep if r and parse_date(r[0])]
        ep_rows.sort(key=lambda r: parse_date(r[0]))
        common = [r for r in ep_rows if r[0] in ip_map]
        if len(common) < 2:
            c.add("L2", "L2-2", "涨跌幅交叉", "≥2 个共同日期", "不足", "SKIP",
                  "index_pct 与 etf_price 无可比日期")
        else:
            last = common[-1]
            d_last = last[0]
            # prev 用 etf_price 里紧邻上一行（不必在 index_pct 中）
            i_cur = ep_rows.index(last)
            prev = ep_rows[i_cur - 1] if i_cur > 0 else None
            if prev is None:
                c.add("L2", "L2-2", "涨跌幅交叉", "可对比", "无前一行", "SKIP", "")
                return
            diffs = []
            for j, name in enumerate(h_ep):
                if j == 0 or name not in h_ip:
                    continue
                a, b = to_float(prev[j]), to_float(last[j])
                if a is None or b is None or a == 0:
                    continue
                chg = (b / a - 1) * 100
                k = col_index(h_ip, name)
                if k is None or d_last not in ip_map:
                    continue
                r = ip_map[d_last]
                v0 = to_float(r[k]) if k < len(r) else None
                if v0 is None:
                    continue
                diffs.append((name, round(chg, 2), v0, round(abs(chg - v0), 2)))
            if diffs:
                big = [x for x in diffs if x[3] > TH_PCT_FAIL]
                mid = [x for x in diffs if TH_PCT_DIFF < x[3] <= TH_PCT_FAIL]
                worst = max(diffs, key=lambda x: x[3])
                v = "FAIL" if big else ("WARN" if mid else "PASS")
                c.add("L2", "L2-2", "涨跌幅交叉", f"差 ≤{TH_PCT_DIFF}pct（>{TH_PCT_FAIL}pct FAIL）",
                      f"最大差 {worst[3]}pct @{worst[0]}（etf {worst[1]} vs pct {worst[2]}）；超 2pct {len(big)} 只",
                      v, f"{d_last}；样例 {(big + mid)[:3]}")
            else:
                c.add("L2", "L2-2", "涨跌幅交叉", "可对比", "无共同列", "SKIP", "")

    # L2-3 930794 分位口径
    p794 = os.path.join(DATA, "erp_930794.csv")
    if os.path.exists(p794):
        hdr, rows = read_csv(p794)
        valid = [r for r in rows if len(r) > 4 and to_float(r[4]) is not None]
        start = parse_date(rows[0][0]) if rows else None
        ok = len(valid) >= 3000 and start and start <= date(2006, 1, 1)
        c.add("L2", "L2-3", "930794 分位口径", "样本≥3000 且起始≤2006-01-01",
              f"样本 {len(valid)}，起始 {start}", "PASS" if ok else "WARN", "data/erp_930794.csv")
    else:
        c.add("L2", "L2-3", "930794 分位口径", "文件存在", "缺失", "FAIL", "")


# ══════════════════════════════════════ L3 派生层 ══════════════════════════

def layer_L3(c):
    # L3-1 回本价复算 + L3-2 单位换算
    dp = os.path.join(DATA, "dividend_payback_candidates.csv")
    if not os.path.exists(dp):
        c.add("L3", "L3-1", "回本价复算", "文件存在", "缺失", "FAIL", "")
    else:
        with open(dp, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        bad, blank, unit_sus = [], [], []
        for r in rows:
            try:
                sh = float(r["最新股本(亿股)"].strip())
                fc = float(r["作者预测未来13年(亿)"].strip())
                pb = float(r["回本价13年(元)"].strip())
            except (ValueError, KeyError):
                blank.append(r.get("code"))
                continue
            calc = fc / sh
            if abs(calc - pb) > TH_PAYBACK:
                bad.append((r["code"], r.get("name"), round(calc, 3), pb))
            # 单位可疑：备注写了万亿，但数值仍是万亿量级（应已换算为亿）
            note = (r.get("备注") or "")
            if "万亿" in note and fc < 100:
                unit_sus.append((r["code"], r.get("name"), fc))
        c.add("L3", "L3-1", "回本价复算", f"误差 ≤{TH_PAYBACK} 元",
              f"{len(rows)} 行，不符 {len(bad)} 行，留空 {len(blank)} 行",
              "FAIL" if bad else "PASS", f"不符 {bad[:3]}；留空 {blank}")
        c.add("L3", "L3-2", "单位换算一致", "备注万亿的行，数值须已换算为亿（≥100）",
              f"可疑 {len(unit_sus)} 行", "FAIL" if unit_sus else "PASS", f"{unit_sus}")

    # L3-3 网格锚
    gs = os.path.join(DATA, "grid_state.csv")
    if not os.path.exists(gs):
        c.add("L3", "L3-3", "网格锚", "anchor=13 且无重复", "文件缺失（靠 secret 还原）", "SKIP",
              "data/grid_state.csv 缺失")
    else:
        with open(gs, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        codes = [r.get("code") or r.get("etf_code") for r in rows]
        dup = len(codes) - len(set(codes))
        c.add("L3", "L3-3", "网格锚", "anchor=13 且无重复",
              f"{len(rows)} 条，重复 {dup}", "WARN" if dup else "PASS", str(len(rows)))

    # L3-4 单档定额：源码常量
    ep = os.path.join(ROOT, "erp_position.py")
    if os.path.exists(ep):
        src = open(ep, encoding="utf-8").read()
        ok = ('"5,000" if is_stock else "3,000"' in src) or ("5000" in src and "3000" in src)
        c.add("L3", "L3-4", "单档定额", "指数3000 / 个股5000",
              "源码常量命中" if ok else "未找到 3000/5000 常量", "PASS" if ok else "WARN",
              "erp_position.py:46")
    else:
        c.add("L3", "L3-4", "单档定额", "可读源码", "缺失", "SKIP", "")

    # L3-5 6% 分母
    pt = os.path.join(DATA, "portfolio_total.csv")
    if not os.path.exists(pt):
        c.add("L3", "L3-5", "6% 分母基准", f"TOTAL 存在且 ≤{TH_PORTFOLIO_DAYS} 天",
              "文件缺失（靠 secret 还原）", "SKIP", "data/portfolio_total.csv 缺失")
    else:
        hdr, rows = read_csv(pt)
        total = None
        for r in rows:
            if r and r[0].strip().upper() == "TOTAL":
                total = r
                break
        if total is None:
            c.add("L3", "L3-5", "6% 分母基准", "含 TOTAL 行", "未找到 TOTAL", "FAIL", "")
        else:
            asof = parse_date(total[-1]) if len(total) > 1 else None
            age = biz_days_between(asof, datetime.now(CST).date()) if asof else None
            v = "FAIL" if age is None else ("PASS" if age <= TH_PORTFOLIO_DAYS else "WARN")
            c.add("L3", "L3-5", "6% 分母基准", f"≤{TH_PORTFOLIO_DAYS} 天",
                  f"基准日 {asof}（{age} 交易日）", v, "portfolio_total.csv TOTAL")


# ══════════════════════════════════════ L4 呈现层 ══════════════════════════

def layer_L4(c, cfg):
    rp = os.path.join(DOCS, "report.html")
    if not os.path.exists(rp):
        c.add("L4", "L4-3", "产物完整性", "存在且 >20KB", "缺失", "FAIL", "")
        return
    size_kb = os.path.getsize(rp) / 1024
    html = open(rp, encoding="utf-8", errors="ignore").read()

    # L4-1 总资产一致
    pt = os.path.join(DATA, "portfolio_total.csv")
    if not os.path.exists(pt):
        c.add("L4", "L4-1", "总资产一致", "html 总资产=TOTAL", "portfolio_total 缺失", "SKIP",
              "本地未还原 secret")
    else:
        with open(pt, encoding="utf-8-sig") as f:
            for r in csv.reader(f):
                if r and r[0].strip().upper() == "TOTAL":
                    mv = to_float(r[2]) if len(r) > 2 else None
                    if mv is None:
                        break
                    shown = f"{mv:,.2f}" in html or f"{mv:,.0f}" in html
                    c.add("L4", "L4-1", "总资产一致", f"html 含 {mv}",
                          "命中" if shown else "未命中", "PASS" if shown else "WARN",
                          "portfolio_total.csv TOTAL vs docs/report.html")
                    break

    # L4-2 缺数显式化
    import re as _re
    dirty = sorted({m for m in _re.findall(r"\b(None|nan|NaN|undefined|NULL)\b", html)})
    c.add("L4", "L4-2", "缺数显式化", "无 None/nan/undefined 裸露",
          f"命中 {dirty}", "WARN" if dirty else "PASS", "docs/report.html")

    # L4-3 产物完整性
    blocks = [k for k in ("档位", "ERP", "仓位建议") if k in html]
    ok = size_kb >= TH_REPORT_KB and len(blocks) >= 3
    c.add("L4", "L4-3", "产物完整性", f">{TH_REPORT_KB}KB 且含档位/ERP/仓位建议",
          f"{size_kb:.0f}KB，区块命中 {blocks}", "PASS" if ok else "FAIL", "docs/report.html")


# ══════════════════════════════════════ L5 提醒层 ══════════════════════════

FD_RAW = "https://raw.githubusercontent.com/ChiaraVan1/fund-daily-report/main"


def fetch_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "verify-workbench"})
    tk = os.environ.get("FD_GH_TOKEN") or os.environ.get("GH_PAT")
    if tk and "api.github.com" in url:
        req.add_header("Authorization", f"Bearer {tk}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fd_file(path):
    """读 fund-daily-report 文件：先 raw，私有仓库回退 API"""
    try:
        return fetch_json(f"{FD_RAW}/{path}")
    except Exception:
        tk = os.environ.get("FD_GH_TOKEN") or os.environ.get("GH_PAT")
        if not tk:
            raise RuntimeError("仓库私有/raw 不可达，需配置 FD_GH_TOKEN")
        import base64
        j = fetch_json(f"https://api.github.com/repos/ChiaraVan1/fund-daily-report/contents/{path}", timeout=20)
        return json.loads(base64.b64decode(j["content"]).decode("utf-8"))


def layer_L5(c, args):
    # L5-1 目标价范围（远端 config）
    cfg_ok = None
    if args.online:
        try:
            tc = fd_file("target_price/config.json")
            # config.json 里标的键叫 secid（不是 code），两种都兼容
            _k = lambda s: (s.get("secid") or s.get("code") or "") if isinstance(s, dict) else s
            stocks = [_k(s) for s in tc.get("stocks", [])]
            arch = [_k(s) for s in tc.get("_archive", [])]
            overlap = set(stocks) & set(arch)
            c.add("L5", "L5-1", "目标价范围", "归档 7 只不出现在 stocks",
                  f"stocks {len(stocks)} 只、archive {len(arch)} 只、交集 {len(overlap)}",
                  "FAIL" if overlap else "PASS", f"交集 {sorted(overlap)}")
            cfg_ok = True
        except Exception as e:
            c.add("L5", "L5-1", "目标价范围", "远端可读", "读取失败", "SKIP", str(e))

        # L5-2 每日一次（local_alerts 是否已有 last_push_date）
        try:
            la = fd_file("target_price/local_alerts.json")
            has_date = any(isinstance(v, dict) and ("last_push_date" in v or "last_alert_date" in v)
                           for v in la.values()) if isinstance(la, dict) else False
            c.add("L5", "L5-2", "提醒每日一次", "存在最后推送日期字段",
                  "有" if has_date else "无（仍为波段去重）",
                  "PASS" if has_date else "WARN",
                  "local_alerts.json；无日期字段则无法判同交易日重复")
        except Exception as e:
            c.add("L5", "L5-2", "提醒每日一次", "远端可读", "读取失败", "SKIP", str(e))
    else:
        c.add("L5", "L5-1", "目标价范围", "远端 config", "--no-online", "SKIP", "")
        c.add("L5", "L5-2", "提醒每日一次", "远端 local_alerts", "--no-online", "SKIP", "")

    # L5-3 MA20 翻转日：需日报文本，本地不可得
    c.add("L5", "L5-3", "MA20 提示", "仅翻转日出现", "本期未验（需日报正文）", "SKIP",
          "跨仓库，取日报 report.md 才可验")

    # L5-4 下游可读：etf_price_adj.csv 新鲜度
    adj = os.path.join(DATA, "etf_price_adj.csv")
    if os.path.exists(adj):
        d = latest_date_of(adj)
        lag = biz_days_between(d, datetime.now(CST).date()) if d else None
        v = "PASS" if (lag is not None and lag <= 1) else ("WARN" if lag == 2 else "FAIL")
        c.add("L5", "L5-4", "下游可读", "etf_price_adj 落后 ≤1 交易日",
              f"最新 {d}（{lag} 交易日）", v, "基金日报 MA20 依赖此文件")
    else:
        c.add("L5", "L5-4", "下游可读", "文件存在", "缺失", "FAIL", "")


# ══════════════════════════════════════ S 抽查 ══════════════════════════

TX_KLINE = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
            "param={sym},day,,,{n},")
EM_KLINE = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?"
            "secid={secid}&fields1=f1,f2&fields2=f51,f53&klt=101&fqt=0&end=20500101&lmt={n}")


def _http_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def tx_closes(sym, n=8, retries=3):
    """腾讯不复权日收盘 → [(date, close)]"""
    for _ in range(retries):
        try:
            j = _http_json(TX_KLINE.format(sym=sym, n=n))
            d = (j.get("data") or {}).get(sym) or {}
            kl = d.get("day") or d.get("qfqday") or []
            out = [(parse_date(x[0]), to_float(x[2])) for x in kl if len(x) >= 3]
            out = [(dd, c) for dd, c in out if dd and c]
            if out:
                return out, "腾讯"
        except Exception:
            pass
        time.sleep(1.5)
    return [], "腾讯不可用"


def em_closes(secid, n=8, retries=3):
    for _ in range(retries):
        try:
            j = _http_json(EM_KLINE.format(secid=secid, n=n))
            kl = (j.get("data") or {}).get("klines") or []
            out = []
            for x in kl:
                parts = x.split(",")
                dd, c = parse_date(parts[0]), to_float(parts[1])
                if dd and c:
                    out.append((dd, c))
            if out:
                return out, "东财"
        except Exception:
            pass
        time.sleep(1.5)
    return [], "东财不可用"


def pick_series(candidates):
    for fn, arg in candidates:
        got, name = fn(arg)
        if got:
            return got, name
    return [], "两源均无数据"


def layer_S(c, cfg, args):
    """抽查：3 只个股 + 3 只 ETF 轮换，同日期收盘价与独立源比对"""
    stocks = ["600585", "601006", "600600"]
    etf_items = [it for it in cfg if it.get("etf_code")]
    day = datetime.now(CST).day
    rot = [etf_items[(i * 3 + (0 if day <= 8 else 1)) % len(etf_items)] for i in range(3)] if etf_items else []

    if not args.online:
        c.add("S", "S-1", "独立源抽查", "6 只（3 个股 + 3 ETF）", "--no-online", "SKIP", "")
        return

    # 本地 ETF/指数价格表（列名 = config code）
    ep = os.path.join(DATA, "etf_price.csv")
    hdr_ep, rows_ep = read_csv(ep) if os.path.exists(ep) else ([], [])
    local_etf = {}
    if hdr_ep:
        for j, code in enumerate(hdr_ep):
            if j == 0:
                continue
            best = None
            for r in rows_ep:
                v = to_float(r[j]) if j < len(r) else None
                if v:
                    best = (parse_date(r[0]), v)
            if best:
                local_etf[code] = best

    results, fails, skips = [], [], []
    targets = [(code, "stock", None) for code in stocks] + \
              [(it["code"], "etf", it["etf_code"]) for it in rot]

    for code, kind, etf_code in targets:
        if kind == "stock":
            series, src = pick_series([(tx_closes, "sh" + code), (em_closes, "1." + code)])
            path = os.path.join(DATA, f"erp_{code}.csv")
            hdr, rows = read_csv(path)
            ic = col_index(hdr, "IndexClose")
            local = None
            for r in rows:
                v = to_float(r[ic]) if ic is not None and ic < len(r) else None
                if v:
                    local = (parse_date(r[0]), v)
            tol = 0.6
        else:
            sym = ("sh" if etf_code.upper().endswith(".SH") else "sz") + etf_code.split(".")[0]
            series, src = pick_series([
                (tx_closes, sym),
                (em_closes, ("1." if sym.startswith("sh") else "0.") + sym[2:]),
            ])
            local = local_etf.get(code)
            tol = 1.0

        if not series:
            skips.append((code, src))
            continue
        if not local:
            skips.append((code, "本地无价格"))
            continue
        ldate, lpx = local
        match = [(d, px) for d, px in series if d == ldate]
        if not match:
            skips.append((code, f"远端无 {ldate}（最新 {series[-1][0]}）"))
            continue
        rpx = match[0][1]
        dev = abs(rpx / lpx - 1) * 100
        tag = f"{code} {ldate} 本地{lpx}/{src}{rpx} 差{dev:.2f}%"
        (fails if dev > tol else results).append(tag)

    v = "WARN" if fails else ("PASS" if results else "SKIP")
    c.add("S", "S-1", "独立源抽查", "同日期收盘价差 ≤0.6%（ETF ≤1.0%）",
          f"通过 {len(results)} / 超差 {len(fails)} / 未验 {len(skips)}", v,
          f"超差 {fails}；未验 {skips}")

# ══════════════════════════════════════ 输出 ══════════════════════════

LAYER_NAMES = {
    "L0": "L0 运行层", "L1": "L1 取数层", "L2": "L2 计算层",
    "L3": "L3 派生层", "L4": "L4 呈现层", "L5": "L5 提醒层", "S": "S 抽查",
}


def render_md(c, meta):
    cnt = c.counts()
    lines = []
    lines.append("# 工作台数据核验报告")
    lines.append("")
    lines.append(f"- 生成时间：{meta['generated_at']}")
    lines.append(f"- 数据日：{meta['data_day']}")
    lines.append(f"- 结论：**{c.overall()}**（FAIL {cnt['FAIL']} / WARN {cnt['WARN']} / SKIP {cnt['SKIP']} / PASS {cnt['PASS']}）")
    lines.append("")
    if cnt["SKIP"]:
        lines.append("**本期未验（SKIP，不等于通过）**")
        for it in c.items:
            if it["verdict"] == "SKIP":
                lines.append(f"- {it['id']} {it['name']}：{it['actual']}")
        lines.append("")
    for layer in ("L0", "L1", "L2", "L3", "L4", "L5", "S"):
        sub = [it for it in c.items if it["layer"] == layer]
        if not sub:
            continue
        lines.append(f"## {LAYER_NAMES[layer]}")
        lines.append("")
        lines.append("| 项 | 检查 | 期望 | 实际 | 结论 | 证据 |")
        lines.append("|---|---|---|---|---|---|")
        for it in sub:
            lines.append(f"| {it['id']} | {it['name']} | {it['expected']} | {it['actual']} | **{it['verdict']}** | {it['evidence']} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def build_alert(c, meta):
    cnt = c.counts()
    verdict = c.overall()
    title = f"工作台核验 {meta['data_day']}：{'不通过' if verdict == 'FAIL' else '通过但有警告'}"
    body = [f"**结论**：{verdict}（FAIL {cnt['FAIL']} / WARN {cnt['WARN']} / SKIP {cnt['SKIP']}）", ""]
    for tag in ("FAIL", "WARN"):
        sub = [it for it in c.items if it["verdict"] == tag]
        if sub:
            body.append(f"**{tag}**")
            for it in sub:
                body.append(f"- {it['id']} {it['name']}：{it['actual']}｜{it['evidence']}")
            body.append("")
    sk = [it for it in c.items if it["verdict"] == "SKIP"]
    body.append("**未验证**")
    body.append("- 无" if not sk else "\n".join(f"- {it['id']} {it['name']}" for it in sk))
    return title, "\n".join(body)


def push_wechat(title, body):
    """微信通道统一走 PushPlus。Server酱仅限 ERP 日报自身使用，核验侧不用。"""
    token = os.environ.get("PUSHPLUS_TOKEN")
    if not token:
        p("  [push] 无 PUSHPLUS_TOKEN，跳过（不会改用 Server酱）")
        return False
    payload = json.dumps({
        "token": token,
        "title": title[:60],
        "content": body,
        "template": "markdown",
    }).encode()
    req = urllib.request.Request(
        "https://www.pushplus.plus/send",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            p(f"  [push] {r.read().decode()[:200]}")
        return True
    except Exception as e:
        p(f"  [push] 失败 {e}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-online", action="store_true", help="跳过联网检查")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写文件不推送")
    ap.add_argument("--push", action="store_true", help="有 FAIL/WARN 时发一条 PushPlus 微信")
    ap.add_argument("--no-push", dest="push", action="store_false", help="不发微信")
    args = ap.parse_args()
    args.online = not args.no_online

    cfg = load_config()
    files = erp_files(cfg)
    # 数据日 = etf_price.csv 末行（价格主表）；不取 max，避免周末补行把数据日推到周日
    data_day = latest_date_of(os.path.join(DATA, "etf_price.csv"))
    if data_day is None:
        dom_dates = [latest_date_of(v) for k, v in files.items() if k not in OVERSEAS]
        data_day = max(d for d in dom_dates if d) if dom_dates else None

    c = Collector()
    layer_L0(c, cfg, args, data_day)
    layer_L1(c, cfg, data_day)
    layer_L2(c, cfg, data_day)
    layer_L3(c)
    layer_L4(c, cfg)
    layer_L5(c, args)
    layer_S(c, cfg, args)

    meta = {
        "generated_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
        "data_day": str(data_day),
    }

    md = render_md(c, meta)
    p(md)

    if args.dry_run:
        if c.overall() != "PASS":
            t, b = build_alert(c, meta)
            p(f"[dry-run] 告警标题：{t}\n{b}")
        return 1 if c.overall() == "FAIL" else (2 if c.overall() == "WARN" else 0)

    # 落盘
    os.makedirs(DATA, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)
    payload = {"meta": meta, "verdict": c.overall(), "counts": c.counts(), "items": c.items}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    if args.push and c.overall() != "PASS":
        t, b = build_alert(c, meta)
        push_wechat(t, b)

    return 1 if c.overall() == "FAIL" else (2 if c.overall() == "WARN" else 0)


if __name__ == "__main__":
    sys.exit(main())
