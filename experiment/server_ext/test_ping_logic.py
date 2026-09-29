"""
Phase 2 검증: 핑 채널 로직(PingMixin) + Phase 3(PingReactiveBot) + Phase 4
(compute_metrics.py) 를 실제 서버(overcooked_demo Flask) 없이 end-to-end로
연결해 검증한다. 이게 사용자가 요청한 "데이터로깅 연결 테스트"에 해당한다.

왜 실제 game.py를 직접 import하지 않는가:
    overcooked_demo/server/game.py 최상단이 무조건
        import ray
        from human_aware_rl.rllib.rllib import load_agent
    를 실행하는데, rllib.py는 다시 구버전 gym + ray.rllib.agents.ppo.PPOTrainer
    (현재 ray 버전에서 제거된 구식 API)를 요구한다. 이건 우리가 이미 "폐기 대상"
    으로 확인한 DRL 학습 스택과 동일한 의존성이며, 규칙 기반 봇만 쓰는 이
    실험에는 불필요하다. 실제 Flask 데모 서버를 띄울 때는
    overcooked_demo/server/requirements.txt + human_aware_rl이 요구하는
    구버전 ray[rllib]/gym이 설치된 별도 환경이 필요하다 (지금 이 경량
    .venv에는 의도적으로 설치하지 않음).

    그래서 이 테스트는 OvercookedGame의 "계약"(players, human_players,
    npc_policies, pending_actions, trajectory, curr_tick, apply_actions/tick의
    반환·부작용)만 그대로 흉내 내는 FakeOvercookedGame을 쓰고, 거기에
    ping_game.PingMixin을 실제로 믹스인해서 로직을 검증한다.
    PingMixin은 원본 클래스에 의존하지 않으므로, 이 테스트에서 검증된 로직은
    실제 서버의 PingEnabledGame(=PingMixin+OvercookedGame, ping_game.py의
    build_ping_enabled_game_class())에도 그대로 적용된다 — 다른 게 아니라
    "같은 믹스인 클래스"를 쓰기 때문.

    RoleRestrictedBot / PingReactiveBot(Phase 1, 3)은 실제 코드를 그대로
    쓴다 (overcooked_ai_py만 있으면 되므로 경량 venv에서 정상 동작).
"""
import json
import pickle
import sys
import tempfile
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcooked_ai_py.planning.planners import (
    MediumLevelActionManager,
    NO_COUNTERS_PARAMS,
)
from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld

from experiment.agents.role_restricted_bot import PingReactiveBot
from experiment.analysis.compute_metrics import compute_all_metrics
from experiment.server_ext.ping_game import (
    PingMixin,
    VALID_PING_TYPES,
    PING_DISPLAY_TICKS,
)


# ── OvercookedGame의 계약을 흉내 내는 가짜 베이스 클래스 ──────────────────
class FakeOvercookedGame:
    def __init__(self, players, npc_policies=None):
        self.players = list(players)
        self.human_players = {p for p in players if not p.startswith("bot_")}
        self.npc_policies = npc_policies or {}
        self.pending_actions = {p: [] for p in players}
        self._is_active = True
        self.trajectory = []
        self.curr_tick = 0
        self.score = 0

    @property
    def is_active(self):
        return self._is_active

    _VALID_MOVES = {"STAY", "UP", "DOWN", "LEFT", "RIGHT", "SPACE"}

    def enqueue_action(self, player_id, action):
        # 원본과 동일하게: 알 수 없는 액션 문자열은 KeyError (action_to_overcooked_action[action] 흉내)
        if action not in self._VALID_MOVES:
            raise KeyError(action)
        self.pending_actions[player_id].append(action)

    def apply_actions(self):
        joint_action = {}
        for p in self.players:
            queue = self.pending_actions[p]
            joint_action[p] = queue.pop(0) if queue else "STAY"

        # 위치를 흉내만 내서 compute_metrics의 idle_ratio 계산이 동작하게 함
        state = {
            "players": [
                {"position": [i, self.curr_tick % 3]} for i in range(len(self.players))
            ]
        }
        transition = {
            "state": json.dumps(state),
            "joint_action": json.dumps(joint_action),
            "score": self.score,
            "cur_gameloop": self.curr_tick,
        }
        self.trajectory.append(transition)
        return transition

    def tick(self):
        self.curr_tick += 1
        return self.apply_actions()

    def get_state(self):
        # 원본 OvercookedGame.get_state()가 만드는 {potential, state, score,
        # time_left} 딕셔너리를 최소한으로 흉내낸다. PingMixin.get_state()가
        # 여기에 "pings" 필드를 얹는 걸 검증하는 게 목적.
        return {"score": self.score, "time_left": 999}


class TestGame(PingMixin, FakeOvercookedGame):
    def __init__(self, *args, **kwargs):
        super(TestGame, self).__init__(*args, **kwargs)
        self._ping_init()


def make_bot():
    mdp = OvercookedGridworld.from_layout_name("cramped_room")
    mlam = MediumLevelActionManager(mdp, NO_COUNTERS_PARAMS)
    return PingReactiveBot(mlam, excluded_roles=[], ping_queue=deque())


def test_unknown_action_still_raises_keyerror():
    print("=== Test 1: PING_ 접두어가 아닌 알 수 없는 액션은 여전히 KeyError (회귀 방지) ===")
    game = TestGame(players=["p1", "p2"])
    try:
        game.enqueue_action("p1", "NOT_A_REAL_ACTION")
    except KeyError:
        print("  PASS (원본과 동일하게 KeyError 발생)\n")
        return
    raise AssertionError("KeyError가 발생해야 하는데 발생하지 않음")


def test_normal_move_unaffected():
    print("=== Test 2: 일반 이동 액션(STAY 등)은 기존과 동일하게 pending_actions로 감 ===")
    game = TestGame(players=["p1", "p2"])
    game.enqueue_action("p1", "UP")
    assert game.pending_actions["p1"] == ["UP"]
    assert game._pending_pings == []
    print("  PASS\n")


def test_ping_action_routed_not_keyerror():
    print("=== Test 3: PING_ 접두어 액션은 KeyError 없이 핑 큐로 라우팅됨 ===")
    game = TestGame(players=["p1", "p2"])
    for ping_type in VALID_PING_TYPES:
        game.enqueue_action("p1", "PING_" + ping_type.upper())
    assert len(game._pending_pings) == len(VALID_PING_TYPES)
    assert game.pending_actions["p1"] == []  # 이동 큐에는 안 들어감
    for entry in game._pending_pings:
        assert entry["player_id"] == "p1"
        assert entry["ping_type"] in VALID_PING_TYPES
        assert "timestamp" in entry
    print(f"  PASS ({len(VALID_PING_TYPES)}종 핑 모두 정상 라우팅)\n")


def test_unknown_ping_type_ignored():
    print("=== Test 3b: 알 수 없는 핑 타입(PING_FOO)은 조용히 무시 (게임을 죽이지 않음) ===")
    game = TestGame(players=["p1", "p2"])
    game.enqueue_action("p1", "PING_FOO")
    assert game._pending_pings == []
    print("  PASS\n")


def test_pings_land_in_trajectory():
    print("=== Test 4: tick() 이후 trajectory[-1]['pings']에 핑이 실제로 기록되는지 ===")
    game = TestGame(players=["p1", "p2"])
    game.enqueue_action("p1", "UP")
    game.enqueue_action("p1", "PING_HELP")
    game.tick()
    assert len(game.trajectory) == 1
    assert game.trajectory[-1]["pings"][0]["ping_type"] == "help"
    assert game._pending_pings == []  # 다음 tick을 위해 비워졌는지
    game.tick()  # 핑 없는 tick
    assert game.trajectory[-1]["pings"] == []
    print("  PASS\n")


def test_ping_routes_to_real_reactive_bot():
    print("=== Test 5: 사람이 보낸 핑이 실제 PingReactiveBot.ping_queue로 즉시 전달되는지 ===")
    bot = make_bot()
    game = TestGame(players=["human_0", "bot_1"], npc_policies={"bot_1": bot})
    assert len(bot.ping_queue) == 0
    game.enqueue_action("human_0", "PING_MINE")
    assert len(bot.ping_queue) == 1
    assert bot.ping_queue[0]["ping_type"] == "mine"
    print("  PASS (봇의 ping_queue에 실제로 들어감)\n")


def test_tick_calls_note_step_on_bot():
    print("=== Test 6: tick()마다 봇의 note_step()이 호출되어 반응 지연 계산이 진행되는지 ===")
    bot = make_bot()
    game = TestGame(players=["human_0", "bot_1"], npc_policies={"bot_1": bot})
    assert bot._curr_step == 0
    for _ in range(5):
        game.tick()
    assert bot._curr_step == 5
    print("  PASS\n")


def test_ping_appears_in_get_state():
    print("=== Test 8: 핑을 보내면 get_state()의 'pings' 필드에 즉시 나타나는지 (화면 표시용) ===")
    game = TestGame(players=["p1", "p2"])
    assert game.get_state()["pings"] == {}
    game.enqueue_action("p1", "PING_HELP")
    state = game.get_state()
    assert state["pings"] == {"0": "help"}, state  # p1은 players[0]
    print("  PASS (인덱스 0에 'help'로 표시됨)\n")


def test_ping_disappears_after_display_window():
    print("=== Test 9: 화면 표시용 핑이 PING_DISPLAY_TICKS 이후 자동으로 사라지는지 ===")
    game = TestGame(players=["p1", "p2"])
    game.enqueue_action("p1", "PING_OK")
    assert game.get_state()["pings"] == {"0": "ok"}

    for _ in range(PING_DISPLAY_TICKS):
        game.tick()
    # 만료 시점 직전까지는 아직 보여야 함
    assert game.get_state()["pings"] == {"0": "ok"}, "표시 기간 내인데 사라짐"

    game.tick()
    assert game.get_state()["pings"] == {}, "표시 기간이 지났는데 안 사라짐"
    print("  PASS (표시 기간 동안 유지되다가 정확히 만료됨)\n")


def test_end_to_end_logging_and_metrics():
    print("=== Test 7 (End-to-End): 핑 채널 -> trajectory 로깅 -> compute_metrics.py 연결 테스트 ===")
    bot = make_bot()
    game = TestGame(players=["human_0", "bot_1"], npc_policies={"bot_1": bot})

    # 15틱 동안, 3틱째와 9틱째에 사람이 핑을 보낸 것처럼 시뮬레이션.
    # 실제 서버에서는 apply_actions()가 매 tick 배달 발생 시 self.score를 갱신하지만,
    # 여기서는 그 부분을 흉내만 내는 대신 12틱째부터 배달 2회가 있었다고 가정해
    # score가 로깅에 반영되는지(comm_efficiency 계산까지)를 함께 확인한다.
    for step in range(15):
        game.enqueue_action("human_0", "UP")
        if step == 3:
            game.enqueue_action("human_0", "PING_HELP")
        if step == 9:
            game.enqueue_action("human_0", "PING_MINE")
        if step == 12:
            game.score = 20
        game.tick()

    with tempfile.TemporaryDirectory() as tmpdir:
        pkl_path = Path(tmpdir) / "P01_ruleAI.pkl"  # {participant_id}_{condition}.pkl 규칙
        with open(pkl_path, "wb") as f:
            pickle.dump({"uid": "test", "trajectory": game.trajectory}, f)

        metrics = compute_all_metrics(str(pkl_path), "P01", "ruleAI")

    assert metrics["num_pings"] == 2, metrics
    assert metrics["final_score"] == 20, metrics
    assert 0 <= metrics["idle_ratio_p0"] <= 1
    assert metrics["comm_efficiency"] == 10.0, metrics  # 20점 / 핑 2건
    print(f"  결과: {metrics}")
    print("  PASS (핑 2건이 trajectory pickle -> compute_metrics.py까지 그대로 연결됨)\n")


if __name__ == "__main__":
    test_unknown_action_still_raises_keyerror()
    test_normal_move_unaffected()
    test_ping_action_routed_not_keyerror()
    test_unknown_ping_type_ignored()
    test_pings_land_in_trajectory()
    test_ping_routes_to_real_reactive_bot()
    test_tick_calls_note_step_on_bot()
    test_ping_appears_in_get_state()
    test_ping_disappears_after_display_window()
    test_end_to_end_logging_and_metrics()
    print("Phase 2 전체(핑 채널 + 봇 반응 + 로깅 + 지표 계산) 테스트 통과.")
