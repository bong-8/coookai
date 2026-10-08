"""라운드 로그(.pkl)를 엑셀/메모장으로 열 수 있는 CSV로 변환한다.

왜 pkl로 저장하나
    원본 Overcooked 데모가 pkl(파이썬 객체 저장)을 쓰고, 분석 도구들(이 저장소의
    analysis/, 원본 human_aware_rl)이 그 형식을 그대로 읽기 때문이다. 파이썬 없이는
    못 열지만 구조(상태·행동·핑·주문 이벤트)를 통째로 보존한다.
    그래서 pkl은 "원본 보관용", 이 CSV는 "눈으로 보는 용"으로 둘 다 남긴다.
    서버는 pkl을 저장할 때 같은 이름의 .csv(틱별 기록)와 _events.csv(사건 목록)를
    자동으로 같이 만든다. 이미 있는 pkl은 아래처럼 한꺼번에 변환할 수 있다.

사용:  python experiment\\analysis\\export_readable.py <로그폴더 또는 pkl파일>
"""
import csv
import json
import pickle
import sys
from pathlib import Path

DIRS = {(0, -1): "위", (0, 1): "아래", (1, 0): "오른쪽", (-1, 0): "왼쪽", (0, 0): "정지"}
FOOD = {"onion": "양파", "tomato": "토마토", "dish": "접시", "soup": "수프"}
PING = {"thanks": "고마워", "move": "비켜줘", "sorry": "미안해", "ok": "OK",
        "help": "도와줘", "mine": "내가 할게", "look": "이거 봐"}


def _load(x):
    return json.loads(x) if isinstance(x, str) else x


def _act(a):
    if a == "interact":
        return "상호작용"
    return DIRS.get(tuple(a), str(a))


def _food(name):
    return FOOD.get(name, name)


def _held(h):
    if not h:
        return ""
    if h.get("name") == "soup":
        ing = "+".join(_food(i["name"]) for i in h.get("_ingredients", []))
        return f"수프({ing})" if ing else "수프"
    return _food(h.get("name", "?"))


def _recipe(r):
    return "+".join(_food(i) for i in r)


def _pots(objects):
    out = []
    for o in objects:
        if o.get("name") != "soup":
            continue
        ing = "+".join(_food(i["name"]) for i in o.get("_ingredients", []))
        if o.get("is_ready"):
            st = "완성"
        elif o.get("is_cooking"):
            st = "조리중"
        else:
            st = "대기"
        out.append(f"({o['position'][0]},{o['position'][1]}){ing}:{st}")
    return " / ".join(out)


STEP_COLS = ["step", "time_elapsed_s", "time_left_s", "score", "reward",
             "p0_x", "p0_y", "p0_facing", "p0_holding", "p0_action",
             "p1_x", "p1_y", "p1_facing", "p1_holding", "p1_action",
             "pots_and_soups", "open_orders", "pings", "order_events"]


def step_rows(data):
    rows = []
    for i, r in enumerate(data["trajectory"]):
        s = _load(r["state"])
        ja = _load(r["joint_action"])
        pl = s["players"]
        row = {"step": i, "time_elapsed_s": round(r.get("time_elapsed", 0), 2),
               "time_left_s": round(r.get("time_left", 0), 2),
               "score": r.get("score"), "reward": r.get("reward")}
        for k in (0, 1):
            p = pl[k]
            row[f"p{k}_x"], row[f"p{k}_y"] = p["position"]
            row[f"p{k}_facing"] = DIRS.get(tuple(p["orientation"]), "")
            row[f"p{k}_holding"] = _held(p.get("held_object"))
            row[f"p{k}_action"] = _act(ja[k])
        row["pots_and_soups"] = _pots(s.get("objects", []))
        row["open_orders"] = " | ".join(_recipe(o) for o in r.get("open_orders", []))
        row["pings"] = ";".join(
            f"p{p.get('sender_idx')}:{PING.get(p.get('ping_type'), p.get('ping_type'))}"
            for p in r.get("pings", []))
        row["order_events"] = ";".join(
            f"{e['type']}({_recipe(e['recipe'])})" + (f"+{e['reward']}" if "reward" in e else "")
            for e in r.get("order_events", []))
        rows.append(row)
    return rows


EVENT_KO = {"initial": "처음 주문", "added": "주문 추가", "delivered": "서빙(배달)",
            "rejected": "서빙했지만 0점(목록에 없는 수프)"}


def event_rows(data):
    out = []
    for i, r in enumerate(data["trajectory"]):
        t = round(r.get("time_elapsed", 0), 1)
        for e in r.get("order_events", []):
            out.append({"step": i, "time_s": t, "event": EVENT_KO.get(e["type"], e["type"]),
                        "detail": _recipe(e["recipe"]) + (f" (+{e['reward']}점)" if "reward" in e else "")})
        for p in r.get("pings", []):
            who = "플레이어0" if p.get("sender_idx") == 0 else "플레이어1"
            out.append({"step": i, "time_s": t, "event": "핑",
                        "detail": f"{who}: {PING.get(p.get('ping_type'), p.get('ping_type'))}"})
    return out


def export_data(data, base):
    """data(dict) -> base.csv, base_events.csv. base는 확장자 없는 Path."""
    base = Path(base)
    with open(f"{base}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=STEP_COLS)
        w.writeheader()
        w.writerows(step_rows(data))
    with open(f"{base}_events.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["step", "time_s", "event", "detail"])
        w.writeheader()
        w.writerows(event_rows(data))


def export_pkl(path):
    path = Path(path)
    with open(path, "rb") as f:
        data = pickle.load(f)
    export_data(data, path.with_suffix(""))
    return path.with_suffix(".csv")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    target = Path(argv[1])
    files = [target] if target.is_file() else sorted(target.rglob("round*.pkl"))
    for p in files:
        try:
            print("변환:", export_pkl(p))
        except Exception as e:
            print("실패:", p, repr(e))
    print(f"{len(files)}개 처리")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
