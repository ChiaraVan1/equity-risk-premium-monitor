"""为已有中证指数 ERP 文件一次性补充 IndexClose，不改动其他字段。"""

import csv
import os
import sys
import tempfile
import time
from datetime import datetime

import akshare as ak

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_loader import BOND_YIELD_CONFIG


def _normalize_date(value):
    return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%Y-%m-%d")


def backfill_one(code):
    path = f"./data/erp_{code}.csv"
    if not os.path.exists(path):
        print(f"   ⚠️ [{code}] 文件不存在，跳过")
        return False

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if not rows or "Date" not in fieldnames:
        print(f"   ⚠️ [{code}] 没有有效日期，跳过")
        return False

    dates = [_normalize_date(row["Date"]) for row in rows if row.get("Date")]
    start_date = min(dates).replace("-", "")
    end_date = max(dates).replace("-", "")
    index_df = ak.stock_zh_index_hist_csindex(
        symbol=code,
        start_date=start_date,
        end_date=end_date,
    )[["日期", "收盘"]]
    close_by_date = {
        str(date)[:10]: str(close)
        for date, close in index_df.itertuples(index=False, name=None)
        if str(close).lower() != "nan"
    }

    if "IndexClose" not in fieldnames:
        fieldnames.insert(fieldnames.index("Date") + 1, "IndexClose")

    filled = 0
    for row in rows:
        if not row.get("IndexClose"):
            close = close_by_date.get(_normalize_date(row["Date"]))
            if close is not None:
                row["IndexClose"] = close
                filled += 1

    if filled == 0:
        print(f"   ℹ️ [{code}] 无需补充")
        return False

    data_dir = os.path.dirname(path)
    fd, temp_path = tempfile.mkstemp(prefix=f"erp_{code}.", suffix=".csv", dir=data_dir)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)

    print(f"   ✅ [{code}] 已补充 {filled} 条 IndexClose")
    return True


def main():
    changed = 0
    failed = []
    for code, _name, _currency, _bond_code, pe_source in BOND_YIELD_CONFIG:
        if pe_source != "csindex":
            continue
        try:
            changed += int(backfill_one(code))
        except Exception as exc:
            print(f"   ❌ [{code}] 回填失败: {exc}")
            failed.append(code)
        time.sleep(1)
    print(f"完成：{changed} 个中证指数文件发生更新")
    if failed:
        raise SystemExit(f"回填失败：{', '.join(failed)}")


if __name__ == "__main__":
    main()
