"""전문가용 일시정지(pause_game.py) 검증 — 실제 서버 없이 가짜 게임 베이스로."""
import queue
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import pytest

from experiment.server_ext import pause_game
from experiment.server_ext.pause_game import ExpertPauseMixin, PAUSE_TOGGLE


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(pause_game, "_now", c)
    return c


class FakeBase:
    class Status:
        ACTIVE = "active"
        DONE = "done"

    def __init__(self, max_time=60):
        self.players = ["p1", "p2"]
        self.human_players = {"p1", "p2"}
        self.pending_actions = [queue.Queue(), queue.Queue()]
        self.trajectory = []
        self.curr_tick = 0
        self.max_time = max_time
        self.start_time = None
        self._active = False
        self.curr_layout = "cramped_room_mixed"
        self.base_enqueued = []
        self.base_ticks = 0

    @property
    def is_active(self):
        return self._active

    def activate(self):
        self._active = True
        self.start_time = pause_game._now()

    def deactivate(self):
        self._active = False

    def enqueue_action(self, player_id, action):
        self.base_enqueued.append((player_id, action))

    def apply_actions(self):
        self.trajectory.append({"tick": self.curr_tick})

    def tick(self):
        self.base_ticks += 1
        self.curr_tick += 1
        self.apply_actions()
        return self.Status.ACTIVE

    def get_state(self):
        return {"time_left": max(self.max_time - (pause_game._now() - self.start_time), 0)}


class Game(ExpertPauseMixin, FakeBase):
    def __init__(self, enabled=True):
        FakeBase.__init__(self)
        self._pause_init(enabled)


def new_game(enabled=True):
    g = Game(enabled)
    g.activate()
    return g


def test_toggle_pauses_freezes_clock_and_resumes(clock):
    g = new_game(); t0 = g.start_time
    g.tick(); assert g.base_ticks == 1
    clock.t += 10
    g.enqueue_action("p1", PAUSE_TOGGLE)
    assert g.get_state()["paused"] is True
    assert g.tick() == "active" and g.base_ticks == 1          # 멈춘 동안 원본 tick 안 불림
    clock.t += 30
    assert g.get_state()["time_left"] == pytest.approx(50)      # 멈춘 시각 기준으로 고정
    g.tick(); assert g.base_ticks == 1
    g.enqueue_action("p1", PAUSE_TOGGLE)
    assert g.get_state()["paused"] is False
    assert g.start_time == pytest.approx(t0 + 30)               # 멈춘 시간만큼 밀림
    assert g.get_state()["time_left"] == pytest.approx(50)
    g.tick(); assert g.base_ticks == 2
    ev = g.trajectory[-1]["pause_events"][0]
    assert ev["type"] == "expert_pause" and ev["paused_sec"] == pytest.approx(30)
    assert ev["paused_by"] == "p1" and ev["resumed_by"] == "p1" and ev["tick"] == 1


def test_either_player_can_pause_and_resume(clock):
    g = new_game()
    g.enqueue_action("p2", PAUSE_TOGGLE); assert g.get_state()["paused"] is True
    clock.t += 5
    g.enqueue_action("p1", PAUSE_TOGGLE); assert g.get_state()["paused"] is False
    g.tick()
    ev = g.trajectory[-1]["pause_events"][0]
    assert ev["paused_by"] == "p2" and ev["resumed_by"] == "p1"


def test_inputs_dropped_while_paused_then_pass_through(clock):
    g = new_game()
    g.enqueue_action("p1", PAUSE_TOGGLE)
    for a in ("UP", "SPACE", "KEY_DOWN_LEFT", "PING_OK", "STAY"):
        g.enqueue_action("p1", a)
    assert g.base_enqueued == []
    g.pending_actions[0].put("UP")
    g.enqueue_action("p2", PAUSE_TOGGLE)
    assert g.pending_actions[0].empty()                          # 풀 때 남은 입력 비움
    g.enqueue_action("p1", "UP"); assert g.base_enqueued == [("p1", "UP")]


def test_disabled_game_ignores_toggle(clock):
    g = new_game(enabled=False)
    g.enqueue_action("p1", PAUSE_TOGGLE)
    assert g.base_enqueued == []                                 # 원본으로도 안 넘김
    g.tick(); assert g.base_ticks == 1
    assert "paused" not in g.get_state()


def test_non_player_and_inactive_game_cannot_toggle(clock):
    g = new_game()
    g.enqueue_action("spectator", PAUSE_TOGGLE); assert g.get_state()["paused"] is False
    g.deactivate()
    g.enqueue_action("p1", PAUSE_TOGGLE); assert g._paused is False


def test_round_change_clears_pause(clock):
    g = new_game()
    g.enqueue_action("p1", PAUSE_TOGGLE); assert g._paused
    g.deactivate(); g.activate()
    assert g._paused is False and g.get_state()["paused"] is False


def test_build_class_expert_pause_only_for_true():
    from experiment.server_ext.ping_game import build_ping_enabled_game_class

    class _Base:
        def __init__(self, *a, **k):
            assert "expert_pause" not in k and "guide" not in k
            self.players = []; self.human_players = set(); self.npc_policies = {}
            self.trajectory = []; self.pending_actions = []; self.write_data = False
    G = build_ping_enabled_game_class(_Base)
    assert G(expert_pause=True)._pause_enabled is True
    for bad in (False, None, "yes", 1, "true"):
        assert G(expert_pause=bad)._pause_enabled is False
    assert G()._pause_enabled is False
    assert G.__mro__.index(ExpertPauseMixin) < G.__mro__.index(_Base)
