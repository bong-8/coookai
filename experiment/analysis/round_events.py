"""
한 라운드 trajectory(저장된 pkl)에서 행동 사건을 복원하고, 중간 보고서의 지표를 계산한다 (2026-10-05).

로그 한 줄(transition)의 구조: state = 그 스텝 "직전" 상태, joint_action = 그 스텝의 행동,
reward = 그 행동의 결과 보상. 그래서 사건은 (state[i], action[i]) → state[i+1] 비교로 찾는다.

지표 정의(중보 계획의 4층위 중 로그로 계산되는 3층위):
  결과   : 누적 점수, 배달 수, 0점 배달(주문에 없는 수프) 수, 주문 대기시간(주문 추가→배달)
  과정   : 유휴 비율(행동이 STAY인 스텝 비율), 최장 유휴 구간,
           기능적 지연 = "냄비의 수프가 완성된 시점 → 누군가 수프를 접시로 떠 간 시점" 평균(초) 과
                         "카운터에 놓인 물건 → 다른 사람이 집어 간 시점" 평균(초)(인계 지연),
           막힌 이동 = 이동 키를 눌렀는데 위치도 방향도 안 바뀐 스텝(벽/상대에 막힘) 비율
  소통   : 핑 수(종류별), 소통 효율(점수/핑 수), 핑-행동 일치(비켜줘: 핑 시점에 상대가 인접해 있었다면
           WINDOW 안에 상대가 실제로 움직였는가) 및 반응 지연(초)
"""
import json
from collections import defaultdict

FPS = 10.0
MATCH_WINDOW_STEPS = 30  # 비켜줘 → 상대가 움직이는지 보는 창(약 3초)


def _a(x):
    return tuple(x) if isinstance(x, list) else x


def _grid(trajectory):
    g = trajectory[0]["layout"]
    if isinstance(g, str):
        g = json.loads(g)
    return g


def _tile(grid, x, y):
    if 0 <= y < len(grid) and 0 <= x < len(grid[y]):
        return grid[y][x]
    return None


def _held(p):
    h = p.get("held_object")
    return h["name"] if h else None


def extract_events(trajectory):
    grid = _grid(trajectory)
    states = [json.loads(t["state"]) for t in trajectory]
    acts = [[_a(a) for a in json.loads(t["joint_action"])] for t in trajectory]
    events = []          # (step, player, type, extra)
    ready_at = {}        # 냄비 위치 -> 수프가 완성된 스텝
    on_counter = {}      # 카운터 위치 -> (스텝, 놓은 사람, 물건 이름)
    soup_delay, handoff_delay, self_pickup = [], [], 0
    stats = {"blocked_moves": {0: 0, 1: 0}, "moves": {0: 0, 1: 0}}

    for i in range(len(trajectory) - 1):
        s0, s1 = states[i], states[i + 1]
        # 냄비 수프 완성 시점
        for o in s0["objects"]:
            pos = tuple(o["position"])
            if o["name"] == "soup" and o.get("is_ready") and _tile(grid, *pos) == "P":
                ready_at.setdefault(pos, i)
        for pi in (0, 1):
            p0, p1 = s0["players"][pi], s1["players"][pi]
            act = acts[i][pi]
            if act not in ((0, 0), "interact"):
                stats["moves"][pi] += 1
                if p0["position"] == p1["position"] and p0["orientation"] == p1["orientation"]:
                    stats["blocked_moves"][pi] += 1
            h0, h1 = _held(p0), _held(p1)
            if h0 == h1:
                continue
            fx = p0["position"][0] + p0["orientation"][0]
            fy = p0["position"][1] + p0["orientation"][1]
            tile = _tile(grid, fx, fy)
            tpos = (fx, fy)
            if h0 == "dish" and h1 == "soup" and tile == "P":
                events.append((i, pi, "pickup_soup", tpos))
                if tpos in ready_at:
                    soup_delay.append((i - ready_at.pop(tpos), pi))
            elif h0 is None and h1 is not None:
                if tile in ("O", "T", "D"):
                    events.append((i, pi, "pickup_" + {"O": "onion", "T": "tomato", "D": "dish"}[tile], None))
                elif tile == "P" and h1 == "soup":
                    events.append((i, pi, "pickup_soup", tpos))
                    if tpos in ready_at:
                        soup_delay.append((i - ready_at.pop(tpos), pi))
                elif tile == "X":
                    events.append((i, pi, "pickup_counter", tpos))
                    if tpos in on_counter:
                        st, who, name = on_counter.pop(tpos)
                        if who != pi:
                            handoff_delay.append((i - st, who, pi, name))
                        else:
                            self_pickup += 1
            elif h0 is not None and h1 is None:
                if tile == "P":
                    events.append((i, pi, "put_pot", tpos))
                elif tile == "S":
                    events.append((i, pi, "deliver", t_reward(trajectory, i)))
                elif tile == "X":
                    events.append((i, pi, "place_counter", tpos))
                    on_counter[tpos] = (i, pi, h0)
        # 조리 시작
        pots0 = {tuple(o["position"]): o for o in s0["objects"] if o["name"] == "soup"}
        pots1 = {tuple(o["position"]): o for o in s1["objects"] if o["name"] == "soup"}
        for pos, o1 in pots1.items():
            o0 = pots0.get(pos)
            if o0 is not None and not o0.get("is_cooking") and o1.get("is_cooking") and _tile(grid, *pos) == "P":
                who = [pi for pi in (0, 1) if acts[i][pi] == "interact"]
                events.append((i, who[0] if len(who) == 1 else None, "start_cook", pos))
    return {
        "events": events, "states": states, "acts": acts, "grid": grid,
        "soup_delay": soup_delay, "handoff_delay": handoff_delay,
        "self_pickup": self_pickup, "stats": stats,
    }


def t_reward(trajectory, i):
    return trajectory[i].get("reward", 0)


def _mean_sec(vals):
    return round(sum(vals) / len(vals) / FPS, 2) if vals else None


def order_waits(trajectory):
    """주문 추가 시각 → 배달 시각(같은 레시피는 먼저 추가된 것부터 소진)."""
    added = defaultdict(list)
    waits, rejected, delivered = [], 0, 0
    for t in trajectory:
        for e in t.get("order_events", []):
            key = tuple(e["recipe"])
            if e["type"] in ("initial", "added"):
                added[key].append(e["elapsed"])
            elif e["type"] == "delivered":
                delivered += 1
                if added[key]:
                    waits.append(e["elapsed"] - added[key].pop(0))
            elif e["type"] == "rejected":
                rejected += 1
    open_left = sum(len(v) for v in added.values())
    return waits, delivered, rejected, open_left


def long_idle_total_steps(seq, value=(0, 0), min_len=10):
    """연속 STAY가 min_len(=1초) 이상인 구간의 총 스텝 수 — '진짜 기다림'.
    (봇은 속도 제한 때문에 STAY가 원래 많으므로 순수 STAY 비율은 봇에선 의미가 약하다.)"""
    total = cur = 0
    for x in seq + [None]:
        if x == value:
            cur += 1
        else:
            if cur >= min_len:
                total += cur
            cur = 0
    return total


def longest_run(seq, value):
    best = cur = 0
    for x in seq:
        cur = cur + 1 if x == value else 0
        best = max(best, cur)
    return best


def analyze_round(trajectory, meta=None, nick_human=0):
    ev = extract_events(trajectory)
    n = len(trajectory)
    acts = ev["acts"]
    row = {
        "layout": (meta or {}).get("layout") or trajectory[0].get("layout_name"),
        "steps": n,
        "final_score": trajectory[-1]["score"],
    }
    waits, delivered, rejected, open_left = order_waits(trajectory)
    row.update({
        "delivered": delivered, "zero_point_deliveries": rejected,
        "orders_left_open": open_left,
        "order_wait_mean_s": round(sum(waits) / len(waits), 1) if waits else None,
        "first_delivery_s": next((round(t["time_elapsed"], 1) for t in trajectory if t["reward"]), None),
    })
    names = {0: "p0", 1: "p1"}
    for pi in (0, 1):
        seq = [a[pi] for a in acts]
        row[f"idle_ratio_{names[pi]}"] = round(seq.count((0, 0)) / n, 3)
        row[f"longest_idle_s_{names[pi]}"] = round(longest_run(seq, (0, 0)) / FPS, 1)
        row[f"waiting_ratio_{names[pi]}"] = round(long_idle_total_steps(seq) / n, 3)
        mv = ev["stats"]["moves"][pi]
        row[f"blocked_move_ratio_{names[pi]}"] = round(ev["stats"]["blocked_moves"][pi] / mv, 3) if mv else None
    ev_by = defaultdict(lambda: defaultdict(int))
    for (_, pi, typ, _) in ev["events"]:
        ev_by[typ][pi] += 1
    for typ in ("pickup_soup", "deliver", "start_cook", "put_pot", "place_counter", "pickup_counter"):
        row[f"{typ}_p0"] = ev_by[typ][0]
        row[f"{typ}_p1"] = ev_by[typ][1]
    row["soup_ready_to_pickup_delay_s"] = _mean_sec([d for d, _ in ev["soup_delay"]])
    row["soup_delay_n"] = len(ev["soup_delay"])
    row["handoff_n"] = len(ev["handoff_delay"])
    row["handoff_delay_mean_s"] = _mean_sec([d[0] for d in ev["handoff_delay"]])
    row["self_repickup_n"] = ev["self_pickup"]
    row.update(ping_metrics(trajectory, ev, row["final_score"]))
    return row, ev


def ping_metrics(trajectory, ev, final_score):
    pings = [(t["cur_gameloop"], p) for t in trajectory for p in t.get("pings", [])]
    counts = defaultdict(int)
    for _, p in pings:
        counts[p["ping_type"]] += 1
    out = {"num_pings": len(pings),
           **{f"ping_{k}": counts.get(k, 0) for k in ("thanks", "move", "sorry", "ok", "help", "mine")},
           "comm_efficiency": round(final_score / len(pings), 1) if pings else None}
    # 비켜줘: 핑 시점에 상대가 인접(맨해튼 1)했으면 "필요했던 핑", 그때 상대가 창 안에 움직였는가
    needed = matched = 0
    latencies = []
    states, acts = ev["states"], ev["acts"]
    for step, p in pings:
        if p["ping_type"] != "move":
            continue
        s = max(0, min(step - 1, len(states) - 1))
        me = p["sender_idx"]
        other = 1 - me
        a = states[s]["players"][me]["position"]
        b = states[s]["players"][other]["position"]
        if abs(a[0] - b[0]) + abs(a[1] - b[1]) != 1:
            continue
        needed += 1
        for k in range(s, min(s + MATCH_WINDOW_STEPS, len(states))):
            if states[k]["players"][other]["position"] != b:
                matched += 1
                latencies.append(k - s)
                break
    out["move_ping_needed"] = needed
    out["move_ping_matched"] = matched
    out["ping_action_match_rate"] = round(matched / needed, 2) if needed else None
    out["move_ping_latency_s"] = _mean_sec(latencies)
    return out
