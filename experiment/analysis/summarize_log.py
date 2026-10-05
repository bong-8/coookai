"""한 라운드 로그(pkl) 요약: python experiment/analysis/summarize_log.py <파일.pkl> [...]
점수/배달 시각, 사람·봇 행동 비율, 사람 이동 입력 토막 길이(입력 지연 진단), 핑."""
import collections, json, pickle, sys


def _a(x):
    return tuple(x) if isinstance(x, list) else x


def summarize(path):
    d = pickle.load(open(path, "rb"))
    t = d["trajectory"]
    meta = d.get("meta", {})
    ja = [json.loads(x["joint_action"]) for x in t]
    seq = [[_a(a[p]) for a in ja] for p in (0, 1)]
    print(f"== {path}")
    print(f"레이아웃={meta.get('layout') or t[0].get('layout_name')} 닉네임={d.get('nicknames')} "
          f"스텝={len(t)} 최종점수={t[-1]['score']} partial={meta.get('partial', False)}")
    deliveries = [round(x["time_elapsed"], 1) for x in t if x["reward"]]
    print("배달 시각(초):", deliveries)
    for p, name in ((0, "사람"), (1, "봇")):
        s = seq[p]
        print(f"{name}: STAY {s.count((0, 0)) / len(s):.0%}, 상호작용 {s.count('interact')}회")
    runs, cur, n = [], None, 0
    for a in seq[0]:
        if a == cur and a not in ((0, 0), "interact"):
            n += 1
        else:
            if cur not in (None, (0, 0), "interact"):
                runs.append(n)
            cur, n = a, 1
    print("사람 이동 연속 길이 분포:", sorted(collections.Counter(runs).items()),
          "(1~2틱 토막이 대부분이면 입력 지연/중복 입력 의심)")
    pings = [(x["cur_gameloop"], p["ping_type"]) for x in t for p in x.get("pings", [])]
    print("핑:", pings)
    ev = [e for x in t for e in x.get("order_events", [])]
    print("주문 이벤트:", collections.Counter(e["type"] for e in ev))


if __name__ == "__main__":
    for p in sys.argv[1:]:
        summarize(p)
