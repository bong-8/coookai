"""
자동 가이드(훈련 단계 조건 1) — 순수 로직 부분 (2026-10-21).

서버/네트워크와 무관한 부분만 이 파일에 둔다(그래서 단위 테스트가 쉽다):
  1) 대본(JSON) 읽기·검증      load_script / validate_script
  2) 게임 상태 → 요약(snapshot) make_snapshot
  3) 트리거 판정               TriggerEngine  (언제 멈출지)
  4) 문장 제공자               ScriptProvider(대본 그대로) / LLMProvider(문장만 다듬기)

설계 원칙 (교수님 논의 결과, C안):
  - "언제 멈추고 무엇을 말할지"는 대본이 정한다 → 모든 참가자에게 같은 정보·같은 순서.
  - 문장을 만드는 부분만 바꿔 끼울 수 있다: ScriptProvider(기본) ↔ LLMProvider.
    LLM은 대본의 요점을 현재 상황에 맞춰 풀어 쓸 뿐, 새 규칙·새 조언을 못 더한다.
    검사(길이, 필수 단어)에 실패하거나 시간이 넘으면 대본 문장으로 대체한다.
  - 가이드 코드의 오류가 게임을 멈추면 안 된다 → 호출하는 쪽(guide_game.py)이
    예외를 잡아 가이드만 끈다.
"""
import json
import re
from pathlib import Path

DEFAULT_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "guide" / "script_ko.json"

KNOWN_CONDITIONS = {
    "round_start",       # 라운드 첫 틱 (round, layouts 로 한정 가능)
    "human_pickup",      # 사람이 item 을 새로 집음 (onion/tomato/dish/soup/ingredient)
    "human_put_in_pot",  # 사람이 재료를 냄비에 넣음
    "pot_full",          # 재료가 가득 찬 냄비가 있음(아직 조리 전)
    "soup_ready",        # 다 익은 냄비가 있음
    "human_holds_soup",  # 사람이 수프를 들고 있음
    "first_delivery",    # 점수가 올라감(서빙 성공)
    "human_idle",        # 사람이 seconds 초 동안 안 움직이고 손에 든 것도 그대로
    "pot_mismatch",      # 냄비 재료가 열린 주문 어느 것과도 안 맞음
    "order_added",       # 주문이 새로 늘어남
    "time_left_below",   # 남은 시간이 seconds 이하
}
INGREDIENTS = {"onion", "tomato"}

DEFAULT_SETTINGS = {
    "ticks_per_sec": 10,    # 서버 틱 속도(config.json MAX_FPS 와 맞출 것) — 초→틱 환산용
    "min_gap_sec": 8,       # 가이드와 가이드 사이 최소 간격(라운드 시작 가이드는 예외)
    "max_per_round": 4,     # 한 라운드에 멈추는 최대 횟수(훈련 시간 통제용)
    "min_display_sec": 1.5, # 문장이 뜬 뒤 이 시간이 지나야 Enter 가 먹는다(실수 방지)
    "llm_timeout_sec": 3.0, # LLM 문장 생성 제한 시간
}


# ── 대본 읽기·검증 ───────────────────────────────────────────────────
def load_script(path=None):
    p = Path(path) if path else DEFAULT_SCRIPT_PATH
    with open(p, encoding="utf-8") as f:
        script = json.load(f)
    return validate_script(script)


def validate_script(script):
    """서버 시작/게임 생성 때 한 번 검사한다. 게임 도중에 대본 오류로 죽지 않게."""
    if not isinstance(script, dict) or not isinstance(script.get("steps"), list):
        raise ValueError("대본 형식 오류: 최상위에 steps 리스트가 필요합니다.")
    settings = dict(DEFAULT_SETTINGS)
    settings.update(script.get("settings") or {})
    seen = set()
    steps = []
    for i, raw in enumerate(script["steps"]):
        sid = raw.get("id")
        if not sid or not isinstance(sid, str):
            raise ValueError(f"대본 {i}번째 step 에 id 가 없습니다.")
        if sid in seen:
            raise ValueError(f"대본 id 중복: {sid}")
        seen.add(sid)
        when = raw.get("when")
        if not isinstance(when, dict) or when.get("type") not in KNOWN_CONDITIONS:
            raise ValueError(f"대본 {sid}: when.type 이 올바르지 않습니다: {when}")
        text = raw.get("text")
        pages = [text] if isinstance(text, str) else text
        if (not isinstance(pages, list) or not pages
                or not all(isinstance(t, str) and t.strip() for t in pages)):
            raise ValueError(f"대본 {sid}: text 는 비어 있지 않은 문자열(또는 그 리스트)이어야 합니다.")
        scope = raw.get("once", "session")
        if scope not in ("session", "round", "always"):
            raise ValueError(f"대본 {sid}: once 는 session/round/always 중 하나여야 합니다.")
        # 멈춘 동안 게임 상태는 그대로라, 조건이 계속 참이면 재개 직후 또 멈출 수 있다.
        # 그래서 "always" 는 쿨다운(ignore_limits 아님)이 적용되는 경우에만 허용한다.
        if scope == "always" and (raw.get("ignore_limits") or when["type"] == "round_start"):
            raise ValueError(f"대본 {sid}: once=always 는 ignore_limits/round_start 와 같이 쓸 수 없습니다.")
        step = dict(raw)
        step["pages"] = pages
        step["once"] = scope
        step["priority"] = int(raw.get("priority", 0))
        step["order"] = i
        steps.append(step)
    return {"meta": script.get("meta", {}), "settings": settings, "steps": steps}


# ── 게임 상태 요약 ───────────────────────────────────────────────────
def ingredient_names(obj):
    """Recipe / SoupState / 문자열 리스트 어느 쪽이든 재료 이름 리스트로."""
    ings = getattr(obj, "ingredients", obj)
    return [getattr(i, "name", i) for i in ings]


def _fits(have, want):
    """have 가 want 의 부분 멀티셋인가."""
    remaining = list(want)
    for h in have:
        if h in remaining:
            remaining.remove(h)
        else:
            return False
    return True


def make_snapshot(game, now):
    """game(OvercookedGame 계약) → 트리거 판정용 요약 딕셔너리. 사람이 없으면 None."""
    humans = [p for p in game.players if p in getattr(game, "human_players", ())]
    if not humans or getattr(game, "state", None) is None or getattr(game, "mdp", None) is None:
        return None
    idx = game.players.index(humans[0])
    state, mdp = game.state, game.mdp
    player = state.players[idx]
    holding = player.get_object().name if player.has_object() else None
    full_n = getattr(mdp, "num_items_for_soup", 3)
    pots = []
    for pos in mdp.get_pot_locations():
        if not state.has_object(pos):
            continue
        soup = state.get_object(pos)
        names = ingredient_names(soup)
        if soup.is_ready:
            status = "ready"
        elif soup.is_cooking:
            status = "cooking"
        elif len(names) >= full_n:
            status = "full"
        else:
            status = "partial"
        pots.append({"pos": tuple(pos), "ingredients": names, "status": status})
    start = getattr(game, "start_time", None)
    max_time = getattr(game, "max_time", None)
    time_left = None
    if start is not None and max_time is not None:
        time_left = max(max_time - (now - start), 0)
    return {
        "tick": getattr(game, "curr_tick", 0),
        "round": getattr(game, "_guide_round", 1),
        "layout": getattr(game, "curr_layout", None),
        "score": getattr(game, "score", 0),
        "time_left": time_left,
        "human": {"pos": tuple(player.position), "holding": holding},
        "pots": pots,
        "open_orders": [ingredient_names(o) for o in getattr(game, "_open_orders", [])],
    }


# ── 트리거 판정 ──────────────────────────────────────────────────────
class TriggerEngine:
    def __init__(self, script):
        self.steps = script["steps"]
        self.settings = script["settings"]
        self.fired_session = set()
        self._fired_round = set()
        self._hold = {}
        self._prev = None
        self._idle_ticks = 0
        self._last_guide_tick = None
        self._round_count = 0

    # 라운드가 바뀔 때마다(guide_game.activate) 호출
    def on_round_start(self):
        self._fired_round = set()
        self._hold = {}
        self._prev = None
        self._idle_ticks = 0
        self._last_guide_tick = None
        self._round_count = 0

    def _tps(self):
        return max(1, int(self.settings["ticks_per_sec"]))

    def _cond(self, step, snap, prev):
        when = step["when"]
        t = when["type"]
        human, pots = snap["human"], snap["pots"]
        if t == "round_start":
            ok = snap["tick"] == 0
            if "round" in when:
                ok = ok and snap["round"] == when["round"]
            return ok
        if t == "human_pickup":
            if prev is None:
                return False
            item = when.get("item", "ingredient")
            now_h, before = human["holding"], prev["human"]["holding"]
            if now_h is None or now_h == before:
                return False
            return now_h in INGREDIENTS if item == "ingredient" else now_h == item
        if t == "human_put_in_pot":
            if prev is None:
                return False
            before = prev["human"]["holding"]
            total_now = sum(len(p["ingredients"]) for p in pots)
            total_before = sum(len(p["ingredients"]) for p in prev["pots"])
            return before in INGREDIENTS and human["holding"] is None and total_now > total_before
        if t == "pot_full":
            return any(p["status"] == "full" for p in pots)
        if t == "soup_ready":
            return any(p["status"] == "ready" for p in pots)
        if t == "human_holds_soup":
            return human["holding"] == "soup"
        if t == "first_delivery":
            return prev is not None and snap["score"] > prev["score"]
        if t == "human_idle":
            return self._idle_ticks >= when.get("seconds", 12) * self._tps()
        if t == "pot_mismatch":
            orders = snap["open_orders"]
            if not orders:
                return False
            return any(
                p["status"] in ("partial", "full") and p["ingredients"]
                and not any(_fits(p["ingredients"], o) for o in orders)
                for p in pots
            )
        if t == "order_added":
            return prev is not None and len(snap["open_orders"]) > len(prev["open_orders"])
        if t == "time_left_below":
            tl = snap["time_left"]
            return tl is not None and tl <= when.get("seconds", 10)
        return False

    def _allowed(self, step, snap):
        if step["once"] == "session" and step["id"] in self.fired_session:
            return False
        if step["once"] == "round" and step["id"] in self._fired_round:
            return False
        if "layouts" in step and snap["layout"] not in step["layouts"]:
            return False
        if "rounds" in step and snap["round"] not in step["rounds"]:
            return False
        return True

    def evaluate(self, snap):
        """이번 틱에 멈춰야 할 step 하나(없으면 None). 호출 후 snap 을 prev 로 기억한다."""
        prev = self._prev
        # 사람이 가만히 있는 시간 추적
        if prev is not None and (prev["human"] == snap["human"]):
            self._idle_ticks += 1
        else:
            self._idle_ticks = 0

        candidates = []
        for step in self.steps:
            if not self._allowed(step, snap):
                continue
            ok = self._cond(step, snap, prev)
            hold = step["when"].get("hold_sec")
            if hold:
                n = self._hold.get(step["id"], 0) + 1 if ok else 0
                self._hold[step["id"]] = n
                ok = ok and n >= hold * self._tps()
            if ok:
                candidates.append(step)
        self._prev = snap
        if not candidates:
            return None

        in_gap = (
            self._last_guide_tick is not None
            and snap["tick"] - self._last_guide_tick < self.settings["min_gap_sec"] * self._tps()
        )
        capped = self._round_count >= self.settings["max_per_round"]
        usable = [
            s for s in candidates
            if s.get("ignore_limits") or not (in_gap or capped)
        ]
        if not usable:
            return None
        usable.sort(key=lambda s: (-s["priority"], s["order"]))
        return usable[0]

    def mark_fired(self, step, snap):
        self.fired_session.add(step["id"])
        self._fired_round.add(step["id"])
        self._hold[step["id"]] = 0
        self._last_guide_tick = snap["tick"]
        self._idle_ticks = 0
        if not step.get("ignore_limits"):
            self._round_count += 1


# ── 문장 제공자 ──────────────────────────────────────────────────────
class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _slots(snap):
    return _SafeDict(
        layout=snap.get("layout") or "",
        score=snap.get("score", 0),
        time_left=int(snap["time_left"]) if snap.get("time_left") is not None else "",
    )


def render_script_pages(step, snap):
    return [p.format_map(_slots(snap)) for p in step["pages"]]


class ScriptProvider:
    """대본의 문장을 그대로 쓴다(기본). 즉시 반환."""
    name = "script"
    is_instant = True

    def render(self, step, snap):
        return {"pages": render_script_pages(step, snap), "source": "script", "fallback": False}

    def fallback(self, step, snap, reason):
        r = self.render(step, snap)
        r["fallback"] = True
        r["fallback_reason"] = reason
        return r


class LLMProvider:
    """
    문장 표현만 LLM 이 만든다. 요점(key_points)·필수 단어(must_include)는 대본이 정한다.

    client: callable(prompt: str, timeout: float) -> str   (실제 API 연결은 별도로 주입)
    검사에 실패하면 ValueError → 호출한 쪽이 대본 문장으로 대체한다.
    """
    name = "llm"
    is_instant = False

    def __init__(self, client, timeout=3.0, max_chars=140):
        self.client = client
        self.timeout = timeout
        self.max_chars = max_chars

    def build_prompt(self, step, snap, page_text):
        pots = ", ".join(
            f"{'/'.join(p['ingredients']) or '빈'}냄비({p['status']})" for p in snap["pots"]
        ) or "냄비 비어 있음"
        key_points = step.get("key_points") or []
        limit = step.get("max_chars", self.max_chars)
        return (
            "당신은 협동 요리 게임의 안내 도우미입니다. 아래 '기본 문장'의 뜻을 바꾸지 말고, "
            "현재 상황에 맞게 자연스러운 한국어(해요체)로 다시 써 주세요.\n"
            "규칙: 새로운 규칙이나 조언을 추가하지 말 것. 이모지 금지. "
            f"{limit}자 이내. 문장만 출력.\n"
            f"반드시 담을 요점: {', '.join(key_points) if key_points else '(기본 문장의 모든 내용)'}\n"
            f"현재 상황: 난이도={snap.get('layout')}, 손에 든 것={snap['human']['holding'] or '없음'}, "
            f"냄비={pots}, 점수={snap.get('score')}\n"
            f"기본 문장: {page_text}"
        )

    def render(self, step, snap):
        pages = []
        for base in render_script_pages(step, snap):
            out = self.client(self.build_prompt(step, snap, base), self.timeout)
            pages.append(self._check(step, out))
        return {"pages": pages, "source": "llm", "fallback": False}

    def _check(self, step, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("빈 응답")
        text = text.strip()
        limit = step.get("max_chars", self.max_chars)
        if len(text) > limit:
            raise ValueError(f"길이 초과 {len(text)}>{limit}")
        missing = [w for w in step.get("must_include", []) if w not in text]
        if missing:
            raise ValueError(f"필수 단어 누락: {missing}")
        if re.search(r"[\U0001F300-\U0001FAFF]", text):
            raise ValueError("이모지 포함")
        return text
