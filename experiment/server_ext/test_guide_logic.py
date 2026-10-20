"""
자동 가이드(guide_engine.py / guide_game.py) 검증 — 실제 서버(ray 등) 없이.

test_ping_logic.py 와 같은 방식: 원본 OvercookedGame 의 "계약"만 흉내 낸 가짜 베이스에
GuideMixin 을 실제로 얹어서 검증한다. 게임 상태·냄비·플레이어는 진짜 overcooked_ai_py 객체를 쓴다.

실행:  python -m pytest experiment/server_ext/test_guide_logic.py -q
"""
import json
import queue
import sys
import time as _time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import pytest

from overcooked_ai_py.mdp.overcooked_mdp import (
    ObjectState, OvercookedGridworld, Recipe, SoupState,
)

from experiment.server_ext import guide_game
from experiment.server_ext.guide_engine import (
    DEFAULT_SCRIPT_PATH, LLMProvider, ScriptProvider, TriggerEngine,
    load_script, make_snapshot, validate_script,
)
from experiment.server_ext.guide_game import GUIDE_CONTINUE, GuideMixin


# ── 가짜 시계 ────────────────────────────────────────────────────────
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(guide_game, "_now", c)
    return c


# ── 원본 OvercookedGame 계약을 흉내 낸 가짜 베이스 ───────────────────────
class FakeBase:
    class Status:
        ACTIVE = "active"
        DONE = "done"
        INACTIVE = "inactive"

    def __init__(self, layout="cramped_room_mixed", max_time=60):
        self.layout_name = layout
        self.players = ["human_1", "bot_1"]
        self.human_players = {"human_1"}
        self.npc_policies = {}
        self.pending_actions = [queue.Queue(maxsize=1), queue.Queue(maxsize=1)]
        self.trajectory = []
        self.curr_tick = 0
        self.score = 0
        self.max_time = max_time
        self.start_time = None
        self.state = None
        self.mdp = None
        self.curr_layout = None
        self._is_active = False
        self._open_orders = []
        self.base_enqueued = []
        self.base_tick_calls = 0

    @property
    def is_active(self):
        return self._is_active

    def activate(self):
        self._is_active = True
        self.curr_layout = self.layout_name
        self.mdp = OvercookedGridworld.from_layout_name(self.layout_name)
        self.state = self.mdp.get_standard_start_state()
        self.start_time = guide_game._now()
        self.curr_tick = 0

    def deactivate(self):
        self._is_active = False

    def enqueue_action(self, player_id, action):
        self.base_enqueued.append((player_id, action))

    def apply_actions(self):
        t = {"cur_gameloop": self.curr_tick, "score": self.score,
             "time_elapsed": guide_game._now() - self.start_time}
        self.trajectory.append(t)

    def tick(self):
        self.base_tick_calls += 1
        self.curr_tick += 1
        self.apply_actions()
        return self.Status.ACTIVE

    def get_state(self):
        return {"time_left": max(self.max_time - (guide_game._now() - self.start_time), 0),
                "score": self.score}


class Game(GuideMixin, FakeBase):
    def __init__(self, guide="script", script_path=None, **kw):
        FakeBase.__init__(self, **kw)
        self._guide_init(guide, script_path)


def new_game(clock, guide="script", script=None, tmp_path=None, **kw):
    path = None
    if script is not None:
        path = tmp_path / "script.json"
        path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    g = Game(guide=guide, script_path=path, **kw)
    g.activate()
    return g


def mini_script(steps, **settings):
    base = {"ticks_per_sec": 10, "min_gap_sec": 0, "max_per_round": 99,
            "min_display_sec": 1.0, "llm_timeout_sec": 3.0}
    base.update(settings)
    return {"settings": base, "steps": steps}


def hold(game, name):
    """사람 손에 물건을 쥐여 준다(진짜 ObjectState)."""
    p = game.state.players[0]
    p.held_object = ObjectState(name, p.position) if name else None


def put_pot(game, onions=0, tomatoes=0, ready=False):
    pos = game.mdp.get_pot_locations()[0]
    if game.state.has_object(pos):
        game.state.remove_object(pos)
    if onions + tomatoes:
        soup = SoupState.get_soup(pos, num_onions=onions, num_tomatoes=tomatoes,
                                  cooking_tick=0 if ready else -1, finished=ready)
        game.state.add_object(soup, pos)


# ── 1. 대본 ──────────────────────────────────────────────────────────
def test_default_script_loads_and_is_valid():
    s = load_script(DEFAULT_SCRIPT_PATH)
    ids = [x["id"] for x in s["steps"]]
    assert len(ids) == len(set(ids)) >= 8
    assert all(x["pages"] for x in s["steps"])


@pytest.mark.parametrize("bad", [
    {"steps": [{"id": "a", "when": {"type": "nope"}, "text": "x"}]},
    {"steps": [{"id": "a", "when": {"type": "pot_full"}, "text": ""}]},
    {"steps": [{"id": "a", "when": {"type": "pot_full"}, "text": "x"},
               {"id": "a", "when": {"type": "pot_full"}, "text": "y"}]},
    {"steps": [{"id": "a", "when": {"type": "round_start"}, "text": "x", "once": "always"}]},
    {"steps": [{"id": "a", "when": {"type": "pot_full"}, "text": "x", "once": "weird"}]},
    {"nope": []},
])
def test_invalid_scripts_are_rejected(bad):
    with pytest.raises(ValueError):
        validate_script(bad)


# ── 2. 트리거(진짜 MDP 상태 사용) ─────────────────────────────────────
def test_round_start_fires_once_then_never_again(clock):
    s = mini_script([{"id": "intro", "when": {"type": "round_start"}, "text": "안녕"}])
    g = Game(guide="script", script_path=None)
    g._guide_script = validate_script(s)
    g._guide_engine = TriggerEngine(g._guide_script)
    g.activate()
    snap = make_snapshot(g, clock())
    assert snap["tick"] == 0 and snap["human"]["holding"] is None
    assert g._guide_engine.evaluate(snap)["id"] == "intro"
    g._guide_engine.mark_fired(g._guide_engine.steps[0], snap)
    assert g._guide_engine.evaluate(snap) is None   # 같은 상태에서 재평가해도 안 나온다


def test_detectors_pickup_putin_full_mismatch_ready_holdsoup_score(clock, tmp_path):
    steps = [
        {"id": "pick", "when": {"type": "human_pickup", "item": "ingredient"}, "text": "a"},
        {"id": "put", "when": {"type": "human_put_in_pot"}, "text": "b"},
        {"id": "full", "when": {"type": "pot_full"}, "text": "c"},
        {"id": "mis", "when": {"type": "pot_mismatch"}, "text": "d"},
        {"id": "rdy", "when": {"type": "soup_ready"}, "text": "e"},
        {"id": "hs", "when": {"type": "human_holds_soup"}, "text": "f"},
        {"id": "sc", "when": {"type": "first_delivery"}, "text": "g"},
        {"id": "ord", "when": {"type": "order_added"}, "text": "h"},
    ]
    g = Game(guide="script")
    g._guide_script = validate_script(mini_script(steps))
    g.activate()

    def fired(engine, game):
        snap = make_snapshot(game, clock())
        st = engine.evaluate(snap)
        return None if st is None else st["id"]

    def fresh_engine():
        e = TriggerEngine(g._guide_script)
        fired(e, g)          # 첫 평가는 prev 만 채운다
        return e

    # 집기
    e = fresh_engine(); hold(g, "onion"); assert fired(e, g) == "pick"
    # 냄비에 넣기: 들고 있던 재료가 사라지고 냄비 재료가 늘어남
    hold(g, "onion"); e = fresh_engine(); hold(g, None); put_pot(g, onions=1)
    assert fired(e, g) == "put"
    # 가득 참 (양파 3)
    e = fresh_engine(); put_pot(g, onions=3); assert fired(e, g) == "full"
    # 불일치: 주문은 [양파3] 하나뿐인데 냄비엔 토마토 2개
    g._open_orders = [Recipe(["onion", "onion", "onion"])]
    put_pot(g, tomatoes=2)
    e = TriggerEngine(g._guide_script); assert fired(e, g) == "mis"
    # 일치하면 안 나온다
    put_pot(g, onions=2); e = TriggerEngine(g._guide_script)
    assert fired(e, g) != "mis"
    # 다 익음
    g._open_orders = []; e = fresh_engine(); put_pot(g, onions=3, ready=True)
    assert fired(e, g) == "rdy"
    # 수프 들기
    put_pot(g, 0); e = fresh_engine(); hold(g, "soup"); assert fired(e, g) == "hs"
    # 점수
    hold(g, None); e = fresh_engine(); g.score = 20; assert fired(e, g) == "sc"
    # 주문 추가
    e = fresh_engine(); g._open_orders = [Recipe(["onion", "onion", "onion"])]
    assert fired(e, g) == "ord"


def test_idle_hold_priority_cooldown_cap_and_scopes(clock):
    steps = [
        {"id": "idle", "when": {"type": "human_idle", "seconds": 2}, "once": "round", "text": "i"},
        {"id": "hi", "when": {"type": "human_holds_soup"}, "priority": 5, "text": "hi"},
        {"id": "lo", "when": {"type": "human_holds_soup"}, "priority": 1, "text": "lo"},
    ]
    script = validate_script(mini_script(steps, min_gap_sec=3, max_per_round=2))
    g = Game(guide=None); g.activate()
    e = TriggerEngine(script)

    def tick(n=1):
        out = None
        for _ in range(n):
            g.curr_tick += 1
            out = e.evaluate(make_snapshot(g, clock()))
        return out

    tick(19); assert tick() is None            # 19틱 → 아직(1.9초)
    st = tick(); assert st and st["id"] == "idle"   # 20틱째 = 2초
    e.mark_fired(st, make_snapshot(g, clock()))
    hold(g, "soup")
    assert tick() is None                       # 쿨다운 3초 안
    st = tick(30)                               # 쿨다운 지남
    assert st and st["id"] == "hi"              # 우선순위 높은 것 먼저
    e.mark_fired(st, make_snapshot(g, clock()))
    assert e._round_count == 2
    assert tick(40) is None                     # 라운드당 최대 2회 → 3번째는 막힌다
    e.on_round_start()
    assert e._round_count == 0 and "hi" in e.fired_session
    st = tick(40)                               # 새 라운드: 한도 초기화, 안 나온 "lo" 는 나온다
    assert st and st["id"] == "lo"


# ── 3. 믹스인: 일시정지·재개·시계 ─────────────────────────────────────
INTRO = [{"id": "intro", "when": {"type": "round_start"}, "once": "session",
          "ignore_limits": True, "text": ["첫 쪽", "둘째 쪽"]}]


def test_pause_freezes_game_clock_and_resumes_with_enter(clock, tmp_path):
    g = new_game(clock, script=mini_script(INTRO), tmp_path=tmp_path)
    t0 = g.start_time
    assert g.tick() == "active"                        # 가이드 정지
    assert g.base_tick_calls == 0 and g.curr_tick == 0  # 원본 tick 을 부르지 않았다
    gs = g.get_state()
    assert gs["guide"]["status"] == "ready" and gs["guide"]["text"] == "첫 쪽"
    assert gs["guide"]["can_continue"] is False        # 최소 표시 시간 전
    assert gs["time_left"] == pytest.approx(60)

    clock.t += 30                                       # 30초 동안 멈춰 있음
    g.tick()
    assert g.base_tick_calls == 0
    assert g.get_state()["time_left"] == pytest.approx(60)   # 멈춘 동안 남은 시간 그대로
    assert g.get_state()["guide"]["can_continue"] is True

    g.enqueue_action("human_1", GUIDE_CONTINUE)         # 1쪽 → 2쪽
    gs = g.get_state()["guide"]
    assert gs["text"] == "둘째 쪽" and gs["page"] == 2 and gs["n_pages"] == 2
    assert gs["can_continue"] is False                  # 새 쪽도 최소 표시 시간 적용
    g.enqueue_action("human_1", GUIDE_CONTINUE)         # 너무 빨리 눌렀다 → 무시
    assert g.get_state()["guide"]["text"] == "둘째 쪽"

    clock.t += 2
    g.enqueue_action("human_1", GUIDE_CONTINUE)         # 재개
    assert g.get_state()["guide"] is None
    assert g.start_time == pytest.approx(t0 + 32)       # 멈춘 시간만큼 시작 시각이 밀렸다
    g.tick()
    assert g.base_tick_calls == 1 and g.curr_tick == 1  # 이제 게임이 진행
    # 재개 직후 첫 transition 에 로그가 붙는다
    ev = g.trajectory[-1]["guide_events"][0]
    assert ev["type"] == "guide_shown" and ev["id"] == "intro"
    assert ev["paused_sec"] == pytest.approx(32) and ev["source"] == "script"
    assert ev["pages"] == ["첫 쪽", "둘째 쪽"] and len(ev["continue_after_sec"]) == 2
    # 시간이 흐르지 않았으므로 transition 의 경과 시간은 0 에 가깝다
    assert g.trajectory[-1]["time_elapsed"] == pytest.approx(0)


def test_inputs_dropped_while_paused_and_not_replayed_after(clock, tmp_path):
    g = new_game(clock, script=mini_script(INTRO), tmp_path=tmp_path)
    g.tick()
    for a in ("UP", "SPACE", "KEY_DOWN_LEFT", "PING_OK", "STAY"):
        g.enqueue_action("human_1", a)
    assert g.base_enqueued == []                        # 멈춘 동안 조작·핑은 버려진다
    g.pending_actions[0].put("UP")                      # 이미 큐에 남아 있던 입력도
    clock.t += 5
    g.enqueue_action("human_1", GUIDE_CONTINUE); clock.t += 2
    g.enqueue_action("human_1", GUIDE_CONTINUE); clock.t += 2
    g.enqueue_action("human_1", GUIDE_CONTINUE)
    assert g.get_state().get("guide") is None
    assert g.pending_actions[0].empty()                 # 재개 때 비워진다
    g.enqueue_action("human_1", "UP")                   # 재개 뒤에는 정상 전달
    assert g.base_enqueued == [("human_1", "UP")]


def test_continue_ignored_when_not_paused_or_still_loading(clock, tmp_path):
    g = new_game(clock, script=mini_script(INTRO), tmp_path=tmp_path)
    g.enqueue_action("human_1", GUIDE_CONTINUE)         # 멈춘 적 없음
    assert g.base_enqueued == [] and g.get_state()["guide"] is None
    assert g.tick() == "active" and g._guide_status == "ready"


def test_guide_off_is_pure_passthrough(clock):
    g = Game(guide=None); g.activate()
    for _ in range(5):
        g.tick()
    assert g.base_tick_calls == 5 and "guide" not in g.get_state()
    g.enqueue_action("human_1", "UP")
    assert g.base_enqueued == [("human_1", "UP")]


def test_errors_disable_guide_but_never_stop_the_game(clock, tmp_path, monkeypatch):
    g = new_game(clock, script=mini_script(INTRO), tmp_path=tmp_path)

    def boom(*a, **k):
        raise RuntimeError("스냅샷 오류")
    monkeypatch.setattr(guide_game, "make_snapshot", boom)
    assert g.tick() == "active" and g.base_tick_calls == 1   # 예외가 나도 원본 tick 은 진행
    assert g._guide_enabled is False
    g.tick()
    assert g.base_tick_calls == 2
    assert g.trajectory[0]["guide_events"][0]["type"] == "guide_disabled"


def test_round_change_resets_state_and_session_flags_persist(clock, tmp_path):
    steps = INTRO + [{"id": "r", "when": {"type": "round_start"}, "once": "round",
                      "ignore_limits": True, "text": "라운드마다"}]
    g = new_game(clock, script=mini_script(steps), tmp_path=tmp_path)
    for _ in range(3):                                   # intro 두 쪽 + "r"
        g.tick()
        clock.t += 2
        for _ in range(3):
            g.enqueue_action("human_1", GUIDE_CONTINUE); clock.t += 2
        if g.get_state()["guide"] is None:
            break
    # 모두 소진될 때까지 진행
    for _ in range(10):
        g.tick()
        if g.base_tick_calls:
            break
        clock.t += 2
        for _ in range(3):
            g.enqueue_action("human_1", GUIDE_CONTINUE); clock.t += 2
    assert g.base_tick_calls >= 1
    shown_round1 = [e["id"] for t in g.trajectory for e in t.get("guide_events", [])]
    assert shown_round1 == ["intro", "r"]
    # 다음 라운드(reset → deactivate/activate): intro 는 세션 1회라 다시 안 나오고 r 만 다시 나온다
    g.deactivate(); g.activate()
    assert g._guide_round == 2 and g._guide_status == "idle"
    g.tick()
    assert g.get_state()["guide"]["id"] == "r"


# ── 4. 문장 제공자(대본 ↔ LLM) ────────────────────────────────────────
STEP = [{"id": "s", "when": {"type": "round_start"}, "ignore_limits": True,
         "text": "냄비에 재료를 3개 넣어요.", "key_points": ["냄비", "3개"],
         "must_include": ["냄비", "3개"], "max_chars": 40}]


def run_until_ready(g, clock, limit=50):
    g.tick()
    for _ in range(limit):
        if g._guide_status == "ready":
            return
        time_to_wait = 0.02
        _time.sleep(time_to_wait)
        g.tick()


def make_llm_game(clock, tmp_path, client, monkeypatch, **settings):
    monkeypatch.setattr(guide_game, "_LLM_CLIENT", client)
    return new_game(clock, guide="llm", script=mini_script(STEP, **settings), tmp_path=tmp_path)


def finish(g, clock):
    clock.t += 2
    g.enqueue_action("human_1", GUIDE_CONTINUE)
    g.tick()
    return g.trajectory[-1]["guide_events"][0]


def test_llm_success_uses_llm_text(clock, tmp_path, monkeypatch):
    g = make_llm_game(clock, tmp_path, lambda prompt, timeout: "냄비에 재료 3개를 넣어 주세요.", monkeypatch)
    run_until_ready(g, clock)
    assert g.get_state()["guide"]["text"] == "냄비에 재료 3개를 넣어 주세요."
    ev = finish(g, clock)
    assert ev["source"] == "llm" and ev["fallback"] is False and ev["requested_mode"] == "llm"


@pytest.mark.parametrize("bad,why", [
    (lambda p, t: (_ for _ in ()).throw(TimeoutError("느림")), "TimeoutError"),
    (lambda p, t: "재료를 넣어요.", "필수 단어"),            # 필수 단어 누락
    (lambda p, t: "냄비에 재료 3개를 넣어요. " * 10, "길이"),  # 길이 초과
    (lambda p, t: "냄비에 3개 😀", "이모지"),
    (lambda p, t: "", "빈 응답"),
])
def test_llm_failures_fall_back_to_script_text(clock, tmp_path, monkeypatch, bad, why):
    g = make_llm_game(clock, tmp_path, bad, monkeypatch)
    run_until_ready(g, clock)
    assert g.get_state()["guide"]["text"] == "냄비에 재료를 3개 넣어요."   # 대본 문장
    ev = finish(g, clock)
    assert ev["source"] == "script" and ev["fallback"] is True and why in ev["fallback_reason"]


def test_llm_hang_hits_deadline_then_falls_back(clock, tmp_path, monkeypatch):
    import threading
    release = threading.Event()

    def hang(prompt, timeout):
        release.wait(5)
        return "냄비 3개"
    g = make_llm_game(clock, tmp_path, hang, monkeypatch, llm_timeout_sec=1.0)
    g.tick()
    assert g._guide_status == "loading"
    gs = g.get_state()["guide"]
    assert gs["status"] == "loading" and gs["text"] is None   # 로딩 표시
    assert g.base_tick_calls == 0                              # LLM 을 기다리는 동안에도 게임은 멈춰 있다
    clock.t += 2                                               # 제한 시간(1초+여유)을 넘김
    g.tick()
    release.set()
    assert g._guide_status == "ready"
    assert g.get_state()["guide"]["text"] == "냄비에 재료를 3개 넣어요."
    assert finish(g, clock)["fallback_reason"] == "timeout"


def test_llm_mode_without_client_acts_as_script(clock, tmp_path, monkeypatch):
    monkeypatch.setattr(guide_game, "_LLM_CLIENT", None)
    g = new_game(clock, guide="llm", script=mini_script(STEP), tmp_path=tmp_path)
    g.tick()
    assert g.get_state()["guide"]["text"] == "냄비에 재료를 3개 넣어요."
    ev = finish(g, clock)
    assert ev["requested_mode"] == "llm" and ev["source"] == "script"


def test_llm_prompt_forbids_new_advice_and_carries_key_points():
    class Snap(dict):
        pass
    snap = {"layout": "cramped_room_mixed", "score": 0, "time_left": 50,
            "human": {"holding": "onion", "pos": (1, 1)}, "pots": [], "open_orders": []}
    step = validate_script(mini_script(STEP))["steps"][0]
    p = LLMProvider(lambda a, b: "x").build_prompt(step, snap, "냄비에 재료를 3개 넣어요.")
    assert "새로운 규칙이나 조언을 추가하지 말 것" in p and "냄비, 3개" in p and "기본 문장" in p


# ── 5. 클래스 조립 ────────────────────────────────────────────────────
def test_build_class_pops_guide_kwarg_and_validates_it():
    from experiment.server_ext.ping_game import build_ping_enabled_game_class

    class _Base:
        def __init__(self, *a, **k):
            assert "guide" not in k       # 원본 생성자는 guide 를 모른다
            self.players = []
            self.human_players = set()
            self.npc_policies = {}
            self.trajectory = []
            self.pending_actions = []
            self.write_data = False
    G = build_ping_enabled_game_class(_Base)
    assert G(guide="script")._guide_enabled is True
    assert G(guide="llm")._guide_requested_mode == "llm"
    for bad in (None, "none", "../../etc/passwd", 1, {"x": 1}):
        assert G(guide=bad)._guide_enabled is False
    assert G()._guide_enabled is False
    assert GuideMixin in G.__mro__ and G.__mro__.index(GuideMixin) < G.__mro__.index(_Base)
