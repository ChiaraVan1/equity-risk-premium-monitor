#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""档位口径回归检查：换档距不得改变逻辑，只改变数字。

断言
  A1 深度一致：每只最深的可排档位 = anchor × (1 − DEPTH_PCT%)
  A2 档数一致：档数 = round(DEPTH_PCT/档距) + 1（10% → 5 档，5% → 9 档）
  A3 不跳档：任意价位上，返回的「下一买点」必须是第一个低于现价的档位
     （价格恰好等于档位价时按「严格跌破才算命中」处理，不算跳档）
  A4 单调：价格越低，档位序号不减
任一失败即 exit 1 并列出标的。

用法：python3 grid/grid_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "grid_plan", os.path.join(os.path.dirname(os.path.abspath(__file__)), "grid_plan.py"))
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
DEPTH_PCT, cur_step, level_count, load_state = _m.DEPTH_PCT, _m.cur_step, _m.level_count, _m.load_state


def levels_of(anchor, step):
    return [anchor * (1 - step * k / 100.0) for k in range(level_count(step))]


def next_of(anchor, price, step):
    for k, lv in enumerate(levels_of(anchor, step)):
        if lv < price:
            return lv, k + 1
    return None, None


def main():
    st = load_state()
    if not st:
        print("没有 grid_state（成本表空），跳过")
        return 0
    step = cur_step()
    fails = []

    expected_n = int(round(DEPTH_PCT / step)) + 1
    for code, anchor in st.items():
        lv = levels_of(anchor, step)
        # A1
        if abs(lv[-1] - anchor * (1 - DEPTH_PCT / 100.0)) > 1e-9:
            fails.append(f"{code} A1 最深档 {lv[-1]:.4f} ≠ {anchor * (1 - DEPTH_PCT / 100.0):.4f}")
        # A2
        if len(lv) != expected_n:
            fails.append(f"{code} A2 档数 {len(lv)} ≠ {expected_n}")
        # A3 全价位扫描（含 anchor 上方与最深档下方）
        prev_n = 0
        for i in range(1, 400):
            price = anchor * (1 - i / 400 * 1.20)
            trig, n = next_of(anchor, price, step)
            if n is None:
                if price >= lv[-1]:
                    fails.append(f"{code} A3 价 {price:.4f} 未排到档但高于最深档")
                continue
            if n == 1:
                if not (price >= anchor):
                    fails.append(f"{code} A3 价 {price:.4f} 被判第1档但低于成本")
            else:
                up = levels_of(anchor, step)[n - 2]
                # 恰好等于档位价时按「严格跌破才算」处理，不算跳档
                if price > up + 1e-9:
                    fails.append(f"{code} A3 价 {price:.4f} 跳档（应命中更高一档 {up:.4f}）")
            # A4
            if n < prev_n:
                fails.append(f"{code} A4 价格下降但档位从 {prev_n} 回落到 {n}")
            prev_n = n

    print(f"档距 {step:g}% · 最深 −{DEPTH_PCT:g}% · 档数 {expected_n} · 标的 {len(st)} 只")
    if fails:
        print("\n".join(fails))
        print(f"FAIL {len(fails)} 项")
        return 1
    print("PASS 全部断言通过（A1 深度 / A2 档数 / A3 不跳档 / A4 单调）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
