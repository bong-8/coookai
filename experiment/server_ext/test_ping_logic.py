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
import queue
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

from experiment.agents.role_restricted_bot import (
    PingReactiveBot,
    MOVE_ASIDE_GRACE_TICKS,
    _bfs_first_step_avoiding,
    _best_retreat_step,
)
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
        # 실제 game.py의 Game.add_player()와 동일하게: self.players 리스트에서의
        # "정수 인덱스"로 접근하는 queue.Queue 객체들(원본은 player_id 문자열이
        # 아니라 인덱스로 pending_actions를 관리함). maxsize=1은 2026-10-04에
        # app.py에서 human buff_size=1로 바꾼 것과 동일 — PingMixin.enqueue_action
        # 의 "큐가 차 있으면 오래된 걸 버리고 최신으로 교체" 로직(ping_game.py의
        # _drop_stale_action_if_queue_full)이 이 Queue.full()/get_nowait()에
        # 의존하므로, 예전의 "player_id로 바로 접근하는 plain list" 가짜 구조로는
        # 더 이상 검증할 수 없다.
        self.pending_actions = [queue.Queue(maxsize=1) for _ in players]
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
        idx = self.players.index(player_id)
        self.pending_actions[idx].put(action)

    def apply_actions(self):
        joint_action = {}
        for i, p in enumerate(self.players):
            try:
                joint_action[p] = self.pending_actions[i].get_nowait()
            except queue.Empty:
                joint_action[p] = "STAY"

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

    def activate(self):
        # 원본 OvercookedGame.activate()가 라운드 시작 시 self.layouts에서
        # 다음 레이아웃을 꺼내 self.mdp를 바꿔주는 부분만 흉내낸다.
        # PingMixin.activate()가 이 호출보다 "먼저" npc_policies에
        # update_for_layout을 전파하는지(순서가 핵심 — 실제 서버에서 레이스
        # 컨디션으로 발견된 버그) 검증하는 게 목적.
        self.activate_call_count = getattr(self, "activate_call_count", 0) + 1
        if getattr(self, "layouts", None):
            self.curr_layout = self.layouts.pop()
        self.mdp = getattr(self, "_next_mdp", "fake_mdp_for_test")


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
    assert list(game.pending_actions[0].queue) == ["UP"]
    assert game._pending_pings == []
    print("  PASS\n")


def test_ping_action_routed_not_keyerror():
    print("=== Test 3: PING_ 접두어 액션은 KeyError 없이 핑 큐로 라우팅됨 ===")
    game = TestGame(players=["p1", "p2"])
    for ping_type in VALID_PING_TYPES:
        game.enqueue_action("p1", "PING_" + ping_type.upper())
    assert len(game._pending_pings) == len(VALID_PING_TYPES)
    assert game.pending_actions[0].qsize() == 0  # 이동 큐에는 안 들어감
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


def test_bot_shows_ok_ack_bubble_immediately_on_ping():
    print("=== Test 5b: 사람이 핑을 보내면 '즉시'(지연 없이) 봇 머리 위에 OK 말풍선이 뜨는지 ===")
    # "핑에 대해 반응이 전혀 없는 것 같다"는 피드백(2026-10-04)의 수정 검증.
    # _show_ping_on_screen()이 보낸 사람(human_0, idx=0)뿐 아니라 봇
    # (bot_1, idx=1)에도 동시에 뜨는지, 그리고 그게 REACTION_DELAY_STEPS를
    # 기다리지 않고 "그 즉시" 뜨는지가 핵심이다 (실제 행동 반응은 지연되지만,
    # "들었다"는 시각적 확인은 지연되면 안 됨).
    bot = make_bot()
    game = TestGame(players=["human_0", "bot_1"], npc_policies={"bot_1": bot})
    assert game.get_state()["pings"] == {}

    game.enqueue_action("human_0", "PING_MINE")
    state = game.get_state()
    assert state["pings"] == {"0": "mine", "1": "ok"}, state
    print("  PASS (보낸 사람=idx0='mine' 그대로, 받는 봇=idx1='ok' 수신확인 동시 표시)\n")

    # 이 합성 ack는 실제 "사람이 보낸 핑"이 아니므로 trajectory 로깅(연구
    # 지표용 pings 필드)에는 섞여 들어가면 안 된다 — _enqueue_ping()의 전체
    # 파이프라인(= self._pending_pings.append)을 타지 않고 _show_ping_on_screen()
    # 만 직접 호출했는지를 이 assert로 확인한다.
    game.tick()
    assert len(game.trajectory[-1]["pings"]) == 1, (
        "합성 OK ack가 트라젝토리 로깅에 잘못 섞여 들어감: "
        f"{game.trajectory[-1]['pings']}"
    )
    assert game.trajectory[-1]["pings"][0]["player_id"] == "human_0"
    print("  PASS (합성 ack는 트라젝토리 로깅을 오염시키지 않음)\n")


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


def test_activate_propagates_new_mdp_to_policies_with_update_hook():
    print("=== Test 10: activate()가 '다음' 레이아웃의 mdp를 super().activate() "
          "(=새 npc_policy_consumer 스레드 시작) 호출보다 먼저, update_for_layout "
          "훅이 있는 정책에만 전달하는지 (레이스 컨디션으로 발견된 버그의 회귀 방지) ===")

    class FakePolicyWithHook:
        def __init__(self):
            self.received = []
            # 업데이트가 호출된 시점에 아직 super().activate()(curr_layout pop)가
            # 실행되기 "전"이었는지 기록 — 순서가 거꾸로면 레이스 컨디션 버그 재발.
            self.called_before_super_activate = None

        def update_for_layout(self, mdp):
            self.received.append(mdp)

    class FakePolicyWithoutHook:
        pass  # ping_queue도 note_step도 update_for_layout도 없는 일반 정책 흉내

    hooked = FakePolicyWithHook()
    plain = FakePolicyWithoutHook()
    game = TestGame(
        players=["human_0", "bot_1", "bot_2"],
        npc_policies={"bot_1": hooked, "bot_2": plain},
    )
    game.layouts = ["cramped_room", "coordination_ring"]  # pop()은 뒤에서부터
    game.mdp_params = {}
    game.activate()

    # super().activate()(=FakeOvercookedGame.activate())가 호출되기 전에
    # 이미 update_for_layout이 불렸어야 하므로, FakeOvercookedGame.activate()
    # 쪽에서 pop한 curr_layout은 "coordination_ring"(다음 레이아웃)이고,
    # 정책이 받은 mdp도 바로 그 레이아웃이어야 한다 — 둘이 일치해야
    # "먼저 계산해서 넘겨준 것"이 실제로 맞게 계산됐다는 뜻.
    assert game.curr_layout == "coordination_ring", game.curr_layout
    assert len(hooked.received) == 1
    assert hooked.received[0].layout_name == "coordination_ring", hooked.received[0]
    assert game.layouts == ["cramped_room"], "다음 레이아웃을 미리 들여다보기만 하고 "\
        "실제 pop은 여전히 super().activate() 쪽에서 1번만 일어나야 함"
    # update_for_layout이 없는 정책은 그냥 조용히 건너뛰어야 함 (에러 없음)
    assert not hasattr(plain, "update_for_layout")
    print("  PASS (super().activate() 전에 다음 레이아웃 mdp를 정확히 계산해서 "
          "훅이 있는 정책에만 전달, pop은 한 번만 일어남)\n")


def test_real_bot_update_for_layout_rebuilds_mlam_for_new_layout():
    print("=== Test 11 (실제 봇): RoleRestrictedBot.update_for_layout()이 레이아웃이 "
          "바뀌면 실제로 그 레이아웃에 맞는 mlam으로 교체되는지 ===")
    from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld as _OG

    mdp_a = _OG.from_layout_name("cramped_room")
    mlam_a = MediumLevelActionManager(mdp_a, NO_COUNTERS_PARAMS)
    bot = PingReactiveBot(mlam_a, excluded_roles=["deliver"], ping_queue=deque())
    bot.set_agent_index(1)
    assert bot.mlam.mdp.layout_name == "cramped_room"

    mdp_b = _OG.from_layout_name("coordination_ring")
    bot.update_for_layout(mdp_b)

    assert bot.mlam.mdp.layout_name == "coordination_ring", (
        "레이아웃이 바뀐 뒤에도 이전 레이아웃(cramped_room) mlam을 그대로 쓰고 있음"
    )
    # agent_index처럼 유지돼야 하는 상태가 실수로 날아가지 않았는지도 확인
    assert bot.agent_index == 1
    assert bot.excluded_roles == {"deliver"}
    print("  PASS (mlam이 coordination_ring 전용으로 교체됨, agent_index/excluded_roles 유지)\n")


def test_help_ping_clears_all_exclusions():
    print("=== Test 12: '도와줘' 핑을 받으면 excluded_roles가 일시적으로 전부 "
          "해제되는지(실제 브라우저 플레이에서 '반응이 없다'는 피드백으로 단순화) ===")
    bot = make_bot()  # excluded_roles=[] — 구분을 위해 직접 세팅
    bot.excluded_roles = {"deliver"}
    bot.set_agent_index(1)
    bot.ping_queue.append({"ping_type": "help", "step": 0})
    bot._curr_step = 0

    calls = []

    def fake_super_ml_action(state):
        # 호출 시점의 excluded_roles 스냅샷을 기록 (실제 motion goal 계산은
        # 이 테스트의 관심사가 아님 — RoleRestrictedBot.ml_action을 그대로
        # 몽키패치해서 "무엇을 넘겨받는지"만 확인)
        calls.append(set(bot.excluded_roles))
        return ["dummy_goal"]

    import experiment.agents.role_restricted_bot as rrb
    original = rrb.RoleRestrictedBot.ml_action
    rrb.RoleRestrictedBot.ml_action = lambda self, state: fake_super_ml_action(state)
    try:
        # 2026-10-04 "비켜줘" 추가 이후: 큐를 비우고 "즉시성" 핑(help/mine/ok)
        # 을 꺼내는 일은 action()이 담당하도록 바뀌었다(move 핑과 채널을
        # 분리하려고). ml_action()은 action()이 채워준 _pending_instant_entry
        # 하나만 소비한다 — 그래서 ml_action()을 직접 테스트할 때도 그 계약을
        # 그대로 따라 _drain_ping_queue()를 먼저 호출해줘야 한다.
        bot._pending_instant_entry = bot._drain_ping_queue()
        bot.ml_action("fake_state")
    finally:
        rrb.RoleRestrictedBot.ml_action = original

    assert calls == [set()], (
        f"'help' 핑 처리 중에는 excluded_roles가 비어 있어야 하는데 {calls}"
    )
    # 핑 처리 끝난 뒤에는 원래 제외 목록(deliver)으로 복원돼야 함
    assert bot.excluded_roles == {"deliver"}, bot.excluded_roles
    print("  PASS (처리 중엔 제한 전부 해제, 끝나면 원래대로 복원)\n")


def test_bot_drops_undeliverable_soup_on_counter_instead_of_stalling():
    print("=== Test 13: excluded_roles=['deliver']인 채로 완성된 수프를 들면, "
          "배달 대신 카운터에 내려놓고 멈추지 않는지 (실제 플레이테스트에서 "
          "'접시를 든 채 그대로 고장난다'는 피드백으로 발견된 버그의 회귀 방지) ===")
    from overcooked_ai_py.mdp.overcooked_mdp import (
        OvercookedGridworld as _OG,
        SoupState as _SoupState,
    )
    from experiment.agents.role_restricted_bot import build_mlam_params

    mdp = _OG.from_layout_name("cramped_room")
    # force_compute=True: 디스크에 예전(counter_drop=[] 였던 시절) 캐시가 남아
    # 있어도 이 테스트는 항상 지금 build_mlam_params()로 새로 계산해서 검증한다.
    mlam = MediumLevelActionManager.from_pickle_or_compute(
        mdp, build_mlam_params(mdp), force_compute=True
    )

    state = mdp.get_standard_start_state()
    p0 = state.players[0]
    # 플레이어가 이미 "완성된 수프"(배달 직전 상태)를 들고 있는 상황을 만든다.
    p0.set_object(
        _SoupState.get_soup(p0.position, num_onions=3, num_tomatoes=0, finished=True)
    )

    bot = PingReactiveBot(mlam, excluded_roles=["deliver"], ping_queue=deque())
    bot.set_agent_index(0)

    # 실제 게임 루프처럼 여러 틱 굴려서, 봇이 정말로 내려놓는지(제자리에서
    # 계속 들고만 있지 않는지) 끝까지 시뮬레이션한다. 상대(플레이어 1)는
    # 가만히 둔다 — 이 테스트의 관심사가 아니므로.
    from overcooked_ai_py.mdp.actions import Action

    dropped = False
    for _ in range(40):
        action0, _ = bot.action(state)
        state, _ = mdp.get_state_transition(state, (action0, Action.STAY))
        if not state.players[0].has_object():
            dropped = True
            break

    assert dropped, (
        "40틱이 지나도록 봇이 수프를 계속 들고만 있음 — "
        "place_obj_on_counter_actions()로 내려놓는 fallback이 동작하지 않음"
    )

    # 그냥 사라진 게 아니라 실제로 카운터 위에 놓였는지(= 사람이 집어서 마저
    # 배달할 수 있는 상태인지)까지 확인한다.
    objs_on_counters = mdp.get_counter_objects_dict(
        state, mdp.get_counter_locations()
    )
    assert len(objs_on_counters.get("soup", [])) == 1, (
        f"수프가 카운터 위에서 발견되지 않음: {objs_on_counters}"
    )
    print("  PASS (봇이 배달 대신 카운터에 수프를 내려놓고 멈추지 않음)\n")


def test_move_aside_bfs_helpers():
    print("=== Test 14: '비켜줘' 핑의 핵심 로직(그리드 BFS 우회/후퇴) 단위 테스트 ===")
    from overcooked_ai_py.mdp.actions import Direction

    # 3x3 완전 개방 격자: (1,1)에서 (1,1)인 blocked를 피해 (1,-1)에서 (1,1)로
    # 가는 대안이 존재해야 함 (돌아갈 길이 있는 경우).
    open_grid = {(x, y) for x in range(-1, 2) for y in range(-1, 2)}
    step = _bfs_first_step_avoiding(open_grid, (-1, 0), (1, 0), blocked=(0, 0))
    assert step is not None, "열린 격자인데 우회로를 못 찾음"
    # 첫 걸음이 blocked 칸으로 바로 들어가면 안 됨
    from overcooked_ai_py.mdp.actions import Action
    assert Action.move_in_direction((-1, 0), step) != (0, 0)
    print(f"  PASS (열린 격자: blocked=(0,0) 피해서 첫 걸음={step})")

    # 1칸짜리 외길: (0,0)-(1,0)-(2,0) 뿐이고 (1,0)이 막히면 우회로가 전혀 없어야 함.
    corridor = {(0, 0), (1, 0), (2, 0)}
    step = _bfs_first_step_avoiding(corridor, (0, 0), (2, 0), blocked=(1, 0))
    assert step is None, f"외길인데 우회로가 있다고 나옴: {step}"
    print("  PASS (진짜 외길에서는 우회로 없음 -> None)")

    # 후퇴 방향: (1,0)에서 blocked=(2,0)이면 반대쪽인 (0,0) 방향(WEST)으로
    # 물러나야 함 (해당 칸으로 이어지는 방향이 west뿐인 외길 기준).
    retreat = _best_retreat_step(corridor, (1, 0), blocked=(2, 0))
    assert retreat == Direction.WEST, f"blocked에서 먼 쪽(WEST)이 아니라 {retreat}로 후퇴"
    print("  PASS (후퇴는 항상 막힌 칸에서 더 멀어지는 방향으로)\n")


def test_move_ping_reroutes_around_blocker_in_open_area():
    print("=== Test 15: '비켜줘' 핑 + 인접 상태 -> 열린 공간에서는 그 즉시 "
          "대안 경로로 우회(대기 없이)하는지 (실제 cramped_room 레이아웃) ===")
    # cramped_room은 (1,1)~(3,2) 2x3 열린 블록이라 항상 돌아갈 길이 있다
    # (교착 방지 2단계/3단계까지 안 가고 1단계에서 바로 풀려야 하는 경우).
    mdp = OvercookedGridworld.from_layout_name("cramped_room")
    mlam = MediumLevelActionManager(mdp, NO_COUNTERS_PARAMS)
    bot = PingReactiveBot(mlam, excluded_roles=[], ping_queue=deque())
    bot.set_agent_index(0)

    state = mdp.get_standard_start_state()
    # 사람(플레이어 1)을 봇(플레이어 0) 바로 옆(인접)에 세워 "막힌" 상황을 만든다.
    bot_pos = state.players[0].position  # (1, 2)
    human_pos = (bot_pos[0] + 1, bot_pos[1])  # (2, 2) — 바로 옆 칸
    state.players[1].position = human_pos

    bot.ping_queue.append({"ping_type": "move", "step": 0, "sender_idx": 1})
    bot._curr_step = 0

    action0, _ = bot.action(state)
    assert action0 != "interact", action0
    from overcooked_ai_py.mdp.actions import Action as _Action
    new_pos = _Action.move_in_direction(bot_pos, action0) if action0 in (
        (0, -1), (0, 1), (1, 0), (-1, 0)
    ) else bot_pos
    assert new_pos != human_pos, (
        f"봇이 사람이 서 있는 칸({human_pos})으로 그대로 이동하려 함: action={action0}"
    )
    assert new_pos != bot_pos, (
        "열린 공간(대안 경로가 분명히 있음)인데 봇이 STAY만 하고 있음 — "
        "1단계(대안 경로 탐색)가 즉시 작동해야 하는 상황"
    )
    print(f"  PASS (인접 즉시, 대기 없이 대안 경로로 이동: action={action0}, "
          f"{bot_pos} -> {new_pos})\n")


def test_move_ping_waits_then_forces_retreat_in_dead_end():
    print("=== Test 16: '비켜줘' 핑인데 대안 경로가 전혀 없으면(외길) "
          "유예(MOVE_ASIDE_GRACE_TICKS) 동안 대기 후 반드시 후퇴하는지 "
          "(교착상태 방지 핵심 로직 회귀 방지) ===")
    mdp = OvercookedGridworld.from_layout_name("cramped_room")
    mlam = MediumLevelActionManager(mdp, NO_COUNTERS_PARAMS)
    bot = PingReactiveBot(mlam, excluded_roles=[], ping_queue=deque())
    bot.set_agent_index(0)

    # 실제 레이아웃은 열려 있어서 "외길"을 자연스럽게 재현하기 어려우니,
    # _decide_move_aside_action이 참조하는 walkable 집합 자체를 1칸짜리
    # 외길로 좁혀서(테스트 전용) 교착 방지 로직만 떼어내 검증한다. 목적지
    # 조회(_current_goal_position)는 몽키패치로 "도달 불가능한 먼 칸"을
    # 돌려주게 해서, 1단계(대안 경로)가 항상 실패하도록 강제한다.
    bot._walkable_positions = {(0, 0), (1, 0), (2, 0)}
    bot._current_goal_position = lambda state: (99, 99)  # 외길 밖, 절대 도달 불가

    bot.ping_queue.append({"ping_type": "move", "step": 0, "sender_idx": 1})
    bot._curr_step = 0

    my_pos = (1, 0)
    sender_pos = (2, 0)  # 1칸 외길에서 바로 옆 칸을 막고 있음, 우회 불가능

    actions_seen = []
    for tick in range(MOVE_ASIDE_GRACE_TICKS + 3):
        bot._curr_step = tick
        action = bot._decide_move_aside_action(
            type("FakeState", (), {})(), my_pos, sender_pos
        )
        actions_seen.append(action)

    from overcooked_ai_py.mdp.actions import Action as _Action
    # 유예 기간 동안은(마지막 틱 전까지) 전부 STAY여야 함
    assert all(a == _Action.STAY for a in actions_seen[:MOVE_ASIDE_GRACE_TICKS]), (
        f"유예 기간인데 STAY가 아닌 행동이 나옴: {actions_seen[:MOVE_ASIDE_GRACE_TICKS]}"
    )
    # 유예가 끝난 뒤에는 반드시(선택이 아니라 확정) 후퇴해야 함 -> STAY가 아님
    forced = actions_seen[MOVE_ASIDE_GRACE_TICKS]
    assert forced != _Action.STAY, (
        f"유예가 끝났는데도 여전히 STAY임 — 교착상태 방지(강제 후퇴)가 작동 안 함: {forced}"
    )
    # 후퇴 방향이 실제로 sender_pos(2,0)에서 멀어지는 쪽(WEST=(0,0) 방향)인지
    new_pos = _Action.move_in_direction(my_pos, forced)
    assert abs(new_pos[0] - sender_pos[0]) > abs(my_pos[0] - sender_pos[0]), (
        f"'후퇴'인데 오히려 사람 쪽으로 가까워짐: {my_pos} -> {new_pos} (sender={sender_pos})"
    )
    print(f"  PASS (유예 {MOVE_ASIDE_GRACE_TICKS}틱 동안 대기 -> 그 다음 틱에 "
          f"반드시 후퇴: {forced})\n")


def test_enqueue_action_overwrites_stale_instead_of_blocking():
    print("=== Test 17: 큐가 이미 차 있어도 블로킹/적체 없이 '최신' 액션으로 "
          "덮어쓰는지 ('봇/유저 속도 차이가 이전보다 더 벌어졌다'는 피드백으로 "
          "발견된, buff_size=1 수정 자체의 부작용 회귀 방지) ===")
    game = TestGame(players=["p1", "p2"])
    # 틱이 한 번도 안 돌아 큐를 아직 아무도 안 비운 상태에서, 클라이언트가
    # 서버보다 빠르게(80ms vs 100ms) 두 번 연속 보낸 것과 같은 상황을 흉내냄.
    # 이전(blocking put()) 방식이었다면 두 번째 호출이 여기서 블로킹되거나
    # (이 테스트처럼 단일 스레드면) 영원히 멈췄을 상황.
    game.enqueue_action("p1", "UP")
    game.enqueue_action("p1", "RIGHT")
    assert game.pending_actions[0].qsize() == 1, (
        f"큐 길이가 1이 아님(적체 발생): {game.pending_actions[0].qsize()}"
    )
    assert list(game.pending_actions[0].queue) == ["RIGHT"], (
        "오래된 액션(UP)이 아니라 최신 액션(RIGHT)만 남아있어야 함"
    )
    print("  PASS (오래된 액션은 버려지고 최신 액션만 남음, 큐 길이 항상 최대 1)\n")


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
    test_bot_shows_ok_ack_bubble_immediately_on_ping()
    test_tick_calls_note_step_on_bot()
    test_ping_appears_in_get_state()
    test_ping_disappears_after_display_window()
    test_activate_propagates_new_mdp_to_policies_with_update_hook()
    test_real_bot_update_for_layout_rebuilds_mlam_for_new_layout()
    test_help_ping_clears_all_exclusions()
    test_bot_drops_undeliverable_soup_on_counter_instead_of_stalling()
    test_move_aside_bfs_helpers()
    test_move_ping_reroutes_around_blocker_in_open_area()
    test_move_ping_waits_then_forces_retreat_in_dead_end()
    test_enqueue_action_overwrites_stale_instead_of_blocking()
    test_end_to_end_logging_and_metrics()
    print("Phase 2 전체(핑 채널 + 봇 반응 + 로깅 + 지표 계산) 테스트 통과.")
