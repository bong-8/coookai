"""한 세션(여러 라운드)의 로그를 지표 표로 만든다 (2026-10-05).

  python experiment/analysis/analyze_session.py <pkl 또는 폴더> [...] [--csv out.csv]
폴더를 주면 안의 round*.pkl을 라운드 번호 순서로 읽는다. 지표 정의는 round_events.py 상단 참고.
"""
import argparse
import glob
import os
import pickle
import re

import pandas as pd

from experiment.analysis.round_events import analyze_round


def _round_no(path):
    m = re.search(r"round(\d+)", os.path.basename(path))
    return int(m.group(1)) if m else 0


def collect(paths):
    files = []
    for p in paths:
        files += sorted(glob.glob(os.path.join(p, "**", "*round*.pkl"), recursive=True)) if os.path.isdir(p) else [p]
    files.sort(key=lambda f: (os.path.dirname(f), _round_no(f)))
    rows = []
    for f in files:
        d = pickle.load(open(f, "rb"))
        if not d.get("trajectory"):
            continue
        row, _ = analyze_round(d["trajectory"], d.get("meta"))
        meta = d.get("meta", {})
        row = {"file": os.path.basename(f), "nickname": (d.get("nicknames") or {}).get("0"),
               "round": meta.get("round"), "order_interval_s": meta.get("order_arrival_interval_sec"), **row}
        rows.append(row)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--csv")
    a = ap.parse_args()
    df = collect(a.paths)
    pd.set_option("display.width", 250, "display.max_columns", 80)
    print(df.T.to_string())
    if a.csv:
        df.to_csv(a.csv, index=False, encoding="utf-8-sig")
        print("saved", a.csv)
