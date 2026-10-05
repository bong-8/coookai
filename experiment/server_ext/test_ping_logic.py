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
    PING_ACK_DELAY_STEPS,
    PING_EFFECT_STEPS,
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


def test_bot_shows_ok_ack_only_after_understanding_delay():
    print("=== Test 5b: 핑을 보내도 봇의 OK 말풍선은 '즉시' 뜨지 않고, 이해 지연"
          "(PING_ACK_DELAY_STEPS) 뒤에야 뜨는지 (2026-10-05 피드백: OK부터 하고 "
          "이해하는 느낌이라 순서를 바꿔달라) ===")
    bot = make_bot()
    game = TestGame(players=["human_0", "bot_1"], npc_policies={"bot_1": bot})
    assert game.get_state()["pings"] == {}

    game.enqueue_action("human_0", "PING_MINE")
    state = game.get_state()
    assert state["pings"] == {"0": "mine"}, f"즉시에는 보낸 사람 말풍선만 있어야 함: {state}"

    seen_ok_at = None
    for i in range(1, PING_ACK_DELAY_STEPS + 3):
        game.tick()
        if game.get_state()["pings"].get("1") == "ok":
            seen_ok_at = i
            break
    assert seen_ok_at == PING_ACK_DELAY_STEPS, (
        f"OK가 {PING_ACK_DELAY_STEPS}틱째에 떠야 하는데 {seen_ok_at}틱째"
    )
    print(f"  PASS (즉시는 보낸 사람만, 봇 OK는 {PING_ACK_DELAY_STEPS}틱 뒤)\n")

    # 합성 OK는 연구 지표용 trajectory 'pings'에 섞이면 안 된다.
    for t in game.trajectory:
        for p in t.get("pings", []):
            assert p["player_id"] == "human_0", p
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


def test_help_and_mine_pings_change_goal_choice_for_a_while():
    print("=== Test 12: 도와줘=역할 제한 해제+나와 가까운 일, 내가 할게=서빙 제외+나와 "
          "먼 일. 효과는 한 번이 아니라 PING_EFFECT_STEPS 동안 유지(속도 제한으로 "
          "핑이 버려지던 버그 회귀 방지) ===")
    import experiment.agents.role_restricted_bot as rrb
    seen = []
    orig = rrb.RoleRestrictedBot.action

    def spy(self, state):
        seen.append((set(self.excluded_roles), getattr(self, "_goal_bias", None)))
        return ("STAY", {})
    rrb.RoleRestrictedBot.action = spy
    try:
        bot = make_bot()
        bot.excluded_roles = {"deliver"}
        bot.set_agent_index(0)
        mdp = OvercookedGridworld.from_layout_name("cramped_room")
        state = mdp.get_standard_start_state()
        human_pos = state.players[1].position

        # 핑 전: 평소 상태
        bot.action(state)
        assert seen[-1] == ({"deliver"}, None), seen[-1]

        # help 핑 -> 지연 전에는 아무 변화 없음
        bot.ping_queue.append({"ping_type": "help", "step": 0, "sender_idx": 1})
        for _ in range(PING_ACK_DELAY_STEPS - 1):
            bot.note_step()
        bot.action(state)
        assert seen[-1] == ({"deliver"}, None), "이해 지연 전인데 반응함"
        bot.note_step()  # 지연 경과
        assert bot.pop_ack() is True and bot.pop_ack() is False
        # 효과는 여러 번의 결정에 걸쳐 유지
        for _ in range(5):
            bot.action(state)
            assert seen[-1] == (set(), ("near", human_pos)), seen[-1]
            bot.note_step()
        assert bot.excluded_roles == {"deliver"}, "끝나면 원래 제한으로 복원돼야 함"

        # mine 핑 -> 서빙 제외 + 먼 곳 우선
        bot.ping_queue.append({"ping_type": "mine", "step": 0, "sender_idx": 1})
        for _ in range(PING_ACK_DELAY_STEPS):
            bot.note_step()
        bot.action(state)
        assert seen[-1] == ({"deliver"}, ("far", human_pos)), seen[-1]

        # 효과 만료 후 원상복귀
        for _ in range(PING_EFFECT_STEPS + 2):
            bot.note_step()
        bot.action(state)
        assert seen[-1] == ({"deliver"}, None), seen[-1]
    finally:
        rrb.RoleRestrictedBot.action = orig
    print("  PASS (지연 후 활성, 지속, 복원, 만료)\n")


def test_goal_choice_by_distance_from_sender():
    print("=== Test 12b: 도와줘면 보낸 사람에게 가까운 목표를, 내가 할게면 먼 목표를 고르는지 ===")
    bot = make_bot()
    mdp = OvercookedGridworld.from_layout_name("cramped_room")
    bot.set_agent_index(0)
    state = mdp.get_standard_start_state()
    start = state.players_pos_and_or[0]
    # 양파 보급대 두 곳(좌/우)을 목표 후보로
    goals = bot.mlam.pickup_onion_actions({"onion": []}) if False else None
    from collections import defaultdict
    goals = bot.mlam.pickup_onion_actions(defaultdict(list))
    assert len(goals) >= 2, goals
    far_ref = max(goals, key=lambda g: g[0][0])[0]  # 어느 한 쪽 끝을 '보낸 사람 위치'로
    bot._goal_bias = ("near", far_ref)
    near_goal, _, _ = bot.choose_motion_goal(start, goals)
    bot._goal_bias = ("far", far_ref)
    far_goal, _, _ = bot.choose_motion_goal(start, goals)
    d_near = abs(near_goal[0][0] - far_ref[0]) + abs(near_goal[0][1] - far_ref[1])
    d_far = abs(far_goal[0][0] - far_ref[0]) + abs(far_goal[0][1] - far_ref[1])
    assert d_near < d_far, (near_goal, far_goal)
    print("  PASS (near는 가까운 쪽, far는 먼 쪽)\n")


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
    #
    # 틱 예산에 BOT_SPEED_DIVISOR를 곱하는 이유(2026-10-05 추가): 이제
    # RoleRestrictedBot.action()이 틱의 절반(기본값)은 즉시 STAY를 반환하고
    # "진짜" 행동 계산은 나머지 절반에서만 일어나므로, 같은 거리를 걷는 데
    # 필요한 틱 수 자체가 그만큼 늘어난다. 이건 이 테스트가 검증하려는
    # "결국 내려놓는가"와는 무관한, 속도 제한 기능의 당연한 부작용이라
    # 틱 예산만 맞춰주고 나머지 로직은 그대로 둔다.
    from overcooked_ai_py.mdp.actions import Action
    from experiment.agents.role_restricted_bot import BOT_SPEED_DIVISOR

    max_ticks = 40 * BOT_SPEED_DIVISOR
    dropped = False
    for _ in range(max_ticks):
        action0, _ = bot.action(state)
        state, _ = mdp.get_state_transition(state, (action0, Action.STAY))
        if not state.players[0].has_object():
            dropped = True
            break

    assert dropped, (
        f"{max_ticks}틱이 지나도록 봇이 수프를 계속 들고만 있음 — "
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
    for _ in range(PING_ACK_DELAY_STEPS):  # 이해 지연 뒤에 반응이 시작된다
        bot.note_step()

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


def test_bot_speed_throttle_halves_action_frequency():
    print("=== Test 18: 봇 속도 제한(BOT_SPEED_DIVISOR)이 '진짜' 행동 계산(=부모 "
          "GreedyHumanModel.action() 호출) 빈도를 정확히 1/N로 줄이고, 매 틱 "
          "블로킹 없이 즉시 반환하며, 건너뛰는 틱에는 prev_state를 건드리지 "
          "않아 auto_unstuck이 오작동하지 않는지 확인 — 'AI 봇이 너무 빨라서 "
          "유저가 못 쫓아간다. 유저 템포는 그대로 두고 봇 템포만 1/2로' "
          "피드백으로 2026-10-05에 추가. game.py의 ticks_per_ai_action 대신 "
          "이 방식을 쓴 이유는 role_restricted_bot.py의 RoleRestrictedBot.action() "
          "docstring 참고(그 파라미터를 그대로 쓰면 게임 전체가 멈추는 버그가 "
          "있음). prev_state를 건너뛰는 틱에도 직접 갱신하는 첫 구현은 오히려 "
          "test_bot_drops_undeliverable_soup_on_counter_instead_of_stalling(Test "
          "13)을 실패시켰다 — 그 회귀를 다시 일으키지 않는지도 이 테스트가 "
          "간접적으로 지킨다(아래 prev_state 단계별 검증 참고) ===")
    import experiment.agents.role_restricted_bot as rrb
    from overcooked_ai_py.agents.agent import GreedyHumanModel
    from overcooked_ai_py.mdp.actions import Action

    bot = make_bot()
    bot.set_agent_index(0)

    real_action_calls = []
    original_action = GreedyHumanModel.action

    def counting_action(self, state):
        real_action_calls.append(self._bot_tick_counter)
        return original_action(self, state)

    GreedyHumanModel.action = counting_action
    try:
        mdp = OvercookedGridworld.from_layout_name("cramped_room")
        state = mdp.get_standard_start_state()

        n_ticks = 20
        for i in range(n_ticks):
            is_skip_tick = (i + 1) % rrb.BOT_SPEED_DIVISOR != 0
            prev_state_before = bot.prev_state
            bot.action(state)
            if is_skip_tick:
                # 건너뛰는 틱: prev_state를 아예 손대지 않아야 한다(손대면
                # 다음 '진짜' 턴에서 auto_unstuck이 오작동 — Test 13 회귀).
                assert bot.prev_state is prev_state_before, (
                    "건너뛰는 틱에 prev_state가 바뀌면 안 되는데 바뀜 "
                    "(auto_unstuck 오작동 위험, Test 13 회귀 원인)"
                )
            else:
                # '진짜' 턴: 부모 GreedyHumanModel.action()이 평소처럼
                # prev_state를 이번 state로 갱신했어야 한다.
                assert bot.prev_state is state, (
                    "'진짜' 턴인데도 prev_state가 갱신되지 않음"
                )
    finally:
        GreedyHumanModel.action = original_action

    expected_real_calls = n_ticks // rrb.BOT_SPEED_DIVISOR
    assert len(real_action_calls) == expected_real_calls, (
        f"실제 행동 계산(super().action()) 호출 횟수가 {n_ticks}틱 중 "
        f"{expected_real_calls}번이어야 하는데 {len(real_action_calls)}번 "
        f"호출됨: {real_action_calls}"
    )
    print(f"  결과: {n_ticks}틱 중 실제 행동 계산 {len(real_action_calls)}번 "
          f"(나머지 {n_ticks - len(real_action_calls)}번은 즉시 STAY로 건너뜀) "
          f"— BOT_SPEED_DIVISOR={rrb.BOT_SPEED_DIVISOR}")
    print("  PASS (봇 속도가 1/N로, 블로킹이나 prev_state 오작동 없이 줄어듦)\n")


def test_speed_throttle_survives_pickle_without_tick_counter():
    print("=== Test 19: 이번 속도 제한 변경 '이전'에 만들어둔 기존 agent.pickle "
          "(__dict__에 _bot_tick_counter가 아예 없는 상태)을 복원해도 "
          "AttributeError 없이 동작하는지 — 실제로 '게임 시작하자마자 "
          "Time Left 59.99초에서 멈춘다'는 형태로 재현된, npc_policy_consumer "
          "스레드가 조용히 죽어 게임 전체가 멈추는 버그의 회귀 방지 "
          "(2026-10-05 발견). reset()의 agent_index 주석과 같은 종류의 "
          "pickle 호환성 함정 ===")
    bot = make_bot()
    # pickle.load()로 '이번 변경 이전'에 저장된 agent.pickle을 복원한 상황을
    # 정확히 흉내낸다: __init__이 다시 안 돌아가므로 _bot_tick_counter가
    # 아예 없는 __dict__ 상태.
    assert "_bot_tick_counter" in bot.__dict__
    del bot.__dict__["_bot_tick_counter"]
    assert "_bot_tick_counter" not in bot.__dict__

    bot.set_agent_index(0)
    mdp = OvercookedGridworld.from_layout_name("cramped_room")
    state = mdp.get_standard_start_state()

    # 예전엔 바로 이 호출에서 AttributeError가 났다 (npc_policy_consumer
    # 스레드 안이라 브라우저에서는 그냥 멈춘 것처럼만 보였음).
    action, info = bot.action(state)
    assert action is not None and "action_probs" in info
    assert bot.__dict__["_bot_tick_counter"] == 1
    print("  PASS (_bot_tick_counter가 없는 옛날 pickle도 AttributeError 없이 "
          "동작, getattr로 0부터 다시 셈)\n")


def test_order_queue_add_deliver_reject():
    print("=== Test 20: 주문 큐 — 시작 시 1개, 10초마다 1개 추가, 배달하면 점수+삭제, "
          "목록에 없으면 0점(목록이 비어도 '아무거나 점수'로 폴백하지 않음), "
          "화면 출력/로그 반영 (2026-10-05) ===")
    import time as _time
    from overcooked_ai_py.mdp.overcooked_mdp import SoupState
    from experiment.server_ext.order_queue import (
        OrderQueueMixin, ORDER_ARRIVAL_INTERVAL_SEC, INITIAL_ORDER_COUNT,
    )

    class _Base:
        def __init__(self):
            self.trajectory = []
            self.layouts = ["cramped_room"]

        def activate(self):
            self.curr_layout = self.layouts.pop()
            self.mdp = OvercookedGridworld.from_layout_name(self.curr_layout)
            self.state = self.mdp.get_standard_start_state()
            self.start_time = _time.time()

        def apply_actions(self):
            self.trajectory.append({})

        def get_state(self):
            return {"state": self.state.to_dict(), "score": 0}

    class _Game(OrderQueueMixin, _Base):
        def __init__(self):
            super().__init__()
            self._orders_init()

    g = _Game()
    g.activate()
    assert len(g._open_orders) == INITIAL_ORDER_COUNT == 1
    assert len(g.get_state()["state"]["all_orders"]) == 1, "화면에는 열린 주문만 나가야 함"

    def deliver():
        st = g.state.deepcopy()
        p = st.players[0]
        soup = SoupState.get_soup(p.position, num_onions=3, num_tomatoes=0, finished=True)
        p.set_object(soup)
        return g.mdp.deliver_soup(st, p, soup), p

    r, player = deliver()
    assert r == 20 and not player.has_object(), (r, player.has_object())
    assert len(g._open_orders) == 0, "배달하면 그 주문이 목록에서 삭제돼야 함"
    assert g.get_state()["state"]["all_orders"] == []

    r, _ = deliver()
    assert r == 0, "목록이 비었을 때 배달하면 0점이어야 함(ALL_RECIPES 폴백 금지)"

    # 10초마다 1개씩 추가(만료 없음): 25초 경과 -> 2개 추가
    g.start_time -= 25
    g.apply_actions()
    assert len(g._open_orders) == 2, len(g._open_orders)
    g.start_time -= 10  # 총 35초 경과 -> 3개째
    g.apply_actions()
    assert len(g._open_orders) == 3
    last = g.trajectory[-1]
    assert last["open_orders"] == [["onion"] * 3] * 3, last["open_orders"]
    kinds = [e["type"] for e in g.trajectory[-2]["order_events"]]
    assert "added" in kinds and "rejected" in kinds and "delivered" in kinds, kinds

    # 새 라운드(activate)가 오면 주문 목록이 다시 초기 상태로
    g.layouts.append("cramped_room")
    g.activate()
    assert len(g._open_orders) == 1 and g._next_order_at_sec == ORDER_ARRIVAL_INTERVAL_SEC
    print("  PASS (시작 1개 -> 10초마다 +1, 배달 시 점수+삭제, 빈 목록이면 0점, "
          "화면/로그 반영, 라운드 시작 시 초기화)\n")


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


def test_datalog_nickname_and_files():
    """Test 21: 닉네임이 pkl/index.csv에 남고, 저장 경로는 환경변수로 바뀐다."""
    import csv, os, pickle, tempfile, glob
    from experiment.server_ext.data_log import (
        DataLogMixin, sanitize_nickname, safe_filename_part, ENV_DATA_DIR)

    assert sanitize_nickname("  홍길동\n ") == "홍길동"
    assert sanitize_nickname("   ") is None and sanitize_nickname(None) is None
    assert len(sanitize_nickname("a" * 50)) == 20
    assert "/" not in safe_filename_part("a/b\\c:d")

    class _Base:
        def __init__(self):
            self.players = ["sidA", "bot"]
            self.human_players = {"sidA"}
            self.trajectory = [{"layout_name": "cramped_room", "score": 20}]
            self.write_data = True
            self.write_config = {"type": "HA"}
            self.max_time = 60
        def activate(self):
            pass
    class _G(DataLogMixin, _Base):
        pass

    tmp = tempfile.mkdtemp()
    old = os.environ.get(ENV_DATA_DIR)
    os.environ[ENV_DATA_DIR] = tmp
    try:
        g = _G(); g._datalog_init(); g.activate()
        g.set_nickname("sidA", "홍길동")
        d = g.get_data()
        assert d["nicknames"] == {"0": "홍길동", "1": "AI_BOT"}, d["nicknames"]
        assert g.trajectory == []  # 원본 계약: 비움
        files = glob.glob(os.path.join(tmp, "*", "round1_cramped_room.pkl"))
        assert len(files) == 1 and "홍길동" in files[0], files
        saved = pickle.load(open(files[0], "rb"))
        assert saved["nicknames"]["0"] == "홍길동" and saved["meta"]["game_type"] == "HA"
        rows = list(csv.reader(open(os.path.join(tmp, "index.csv"), encoding="utf-8-sig")))
        assert rows[1][5] == "홍길동|AI_BOT" and rows[1][6] == "20", rows
        assert g.get_data()["trajectory"] == []  # 빈 호출은 파일 안 만듦
        assert len(glob.glob(os.path.join(tmp, "*", "*.pkl"))) == 1
    finally:
        if old is None: os.environ.pop(ENV_DATA_DIR, None)
        else: os.environ[ENV_DATA_DIR] = old
    print("PASS test_datalog_nickname_and_files")



def test_bot_delivers_when_counters_full_and_follows_oldest_order():
    """Test 22: (a) 빈 카운터 <=1이면 deliver 제외 봇도 서빙, (b) 가장 오래된
    열린 주문의 재료를 집는다, (c) 게임이 NPC에 open_orders를 동기화한다."""
    from collections import defaultdict
    from overcooked_ai_py.mdp.overcooked_mdp import (
        OvercookedGridworld, SoupState, ObjectState, Recipe)
    from overcooked_ai_py.planning.planners import MediumLevelActionManager
    from experiment.agents.role_restricted_bot import RoleRestrictedBot, build_mlam_params

    mdp = OvercookedGridworld.from_layout_name("cramped_room")
    mlam = MediumLevelActionManager.from_pickle_or_compute(mdp, build_mlam_params(mdp))
    bot = RoleRestrictedBot(mlam, excluded_roles=["deliver"]); bot.set_agent_index(0)
    st = mdp.get_standard_start_state()
    st.players[0].set_object(SoupState.get_soup(st.players[0].position, num_onions=3, finished=True))
    serve = set(mlam.deliver_soup_actions())
    assert not any(g in serve for g in bot.ml_action(st)), "자리 여유가 있으면 서빙 안 함"
    empt = [c for c in mlam.counter_drop if c in set(mdp.get_empty_counter_locations(st))]
    for c in empt[:-1]:
        st.add_object(ObjectState("dish", c))
    assert bot._free_counter_count(st) == 1
    assert all(g in serve for g in bot.ml_action(st)), "빈 자리 1개면 서빙"

    mdp2 = OvercookedGridworld.from_layout_name("counter_circuit")
    mlam2 = MediumLevelActionManager.from_pickle_or_compute(mdp2, build_mlam_params(mdp2))
    b2 = RoleRestrictedBot(mlam2, excluded_roles=["deliver"]); b2.set_agent_index(0)
    s2 = mdp2.get_standard_start_state()
    b2.open_orders = [Recipe(["onion", "tomato", "tomato"])]
    pot = mdp2.get_pot_locations()[0]
    td = set(mlam2.pickup_tomato_actions(defaultdict(list)))
    od = set(mlam2.pickup_onion_actions(defaultdict(list)))
    assert all(g in od for g in b2.ml_action(s2)), "빈 냄비: 첫 재료(양파)"
    s2.objects[pot] = SoupState.get_soup(pot, num_onions=1, num_tomatoes=0)
    assert all(g in td for g in b2.ml_action(s2)), "양파 들어감: 토마토 필요"

    class _P: pass
    from experiment.server_ext.order_queue import OrderQueueMixin
    class _Q(OrderQueueMixin):
        pass
    q = _Q(); q._orders_init(); q.npc_policies = {"b": _P()}
    q._open_orders = [Recipe(["onion"] * 3)]
    q._sync_orders_to_bots()
    assert q.npc_policies["b"].open_orders == q._open_orders
    print("PASS test_bot_delivers_when_counters_full_and_follows_oldest_order")


def test_build_class_instantiates_with_all_mixins():
    """Test 23: build_ping_enabled_game_class()가 만든 클래스를 실제로 만들고
    인스턴스를 생성했을 때 믹스인 초기화(_ping/_orders/_datalog)가 모두 되는지.
    (이전엔 이 함수를 아무 테스트도 호출하지 않아, 들여쓰기 실수로 app.py
    import 시점에 NameError가 나는 걸 놓쳤다 — 2026-10-05 사용자 보고)"""
    from experiment.server_ext.ping_game import build_ping_enabled_game_class

    class _Base:
        def __init__(self, *a, **k):
            self.players = []
            self.human_players = set()
            self.npc_policies = {}
            self.trajectory = []
            self.pending_actions = []
            self.write_data = False
    G = build_ping_enabled_game_class(_Base)
    g = G()
    for attr in ("_nicknames", "_open_orders", "_session_started"):
        assert hasattr(g, attr), attr
    g.set_nickname("x", "닉")
    assert g._nicknames == {"x": "닉"}
    print("PASS test_build_class_instantiates_with_all_mixins")


def test_forced_layout_supplier_role_and_safety_net():
    """Test 24: forced_coordination처럼 봇이 냄비/서빙대에 못 닿는 레이아웃에서
    (a) 공급자 역할로 분석되고, (b) 접시와 양파를 번갈아 공유 카운터에 채워서
    사람이 실제로 수프를 서빙할 수 있고, (c) 다른 레이아웃은 기존 역할 유지,
    (d) 할 일이 없는 상황에서 어설션으로 죽지 않으며(NPC 스레드 사망=게임 정지 방지),
    (e) 새 속성이 없는 옛 pickle도 핑 처리에서 안 죽는다."""
    from experiment.agents.role_restricted_bot import (
        PingReactiveBot, analyze_layout_roles, build_mlam_params)
    from experiment.analysis.simulate_pair import (
        PotSideScriptedHuman, make_mlam, STEPS)
    from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld, ObjectState

    for lay, role in [("forced_coordination", "supplier"), ("cramped_room", "normal"),
                      ("asymmetric_advantages", "normal"), ("coordination_ring", "normal")]:
        r = analyze_layout_roles(OvercookedGridworld.from_layout_name(lay), 1)
        assert r["role"] == role, (lay, r)
    assert analyze_layout_roles(
        OvercookedGridworld.from_layout_name("forced_coordination"), 1
    )["handoff"] == [(2, 1), (2, 2), (2, 3)]

    mdp = OvercookedGridworld.from_layout_name("forced_coordination")
    mlam = make_mlam(mdp)
    human = PotSideScriptedHuman(mlam, excluded_roles=[]); human.set_agent_index(0)
    bot = PingReactiveBot(mlam, excluded_roles=["deliver"]); bot.set_agent_index(1)
    st = mdp.get_standard_start_state()
    score = 0
    for _ in range(STEPS):
        a0, _i = human.action(st); a1, _i = bot.action(st); bot.note_step()
        st, info = mdp.get_state_transition(st, (a0, a1))
        score += sum(info["sparse_reward_by_agent"])
    assert score >= 100, f"공급자 봇 + 규칙 기반 사람으로 60초 동안 수프 5개 미만: {score}"

    # (d) 안전망: 봇이 물건을 들었는데 카운터가 전부 차 있어 할 일이 없는 상태
    mdp2 = OvercookedGridworld.from_layout_name("cramped_room")
    mlam2 = make_mlam(mdp2)
    b2 = PingReactiveBot(mlam2, excluded_roles=["deliver"]); b2.set_agent_index(0)
    s2 = mdp2.get_standard_start_state()
    from overcooked_ai_py.mdp.overcooked_mdp import SoupState
    s2.players[0].set_object(SoupState.get_soup(s2.players[0].position, num_onions=3, finished=True))
    for c in mlam2.counter_drop:
        if c not in s2.objects:
            s2.add_object(ObjectState("dish", c))
    b2._bot_tick_counter = 10**6 - 1  # 속도 제한이 걸리지 않는 틱
    import experiment.agents.role_restricted_bot as rrb
    old_div = rrb.BOT_SPEED_DIVISOR
    rrb.BOT_SPEED_DIVISOR = 1
    try:
        act, _i = b2.action(s2)  # 예외 없이 무언가를 돌려줘야 한다
    finally:
        rrb.BOT_SPEED_DIVISOR = old_div

    # (e) 옛 pickle 시뮬레이션: 핑 관련 새 속성 제거 후에도 동작
    for attr in ("_incoming", "_active_ping", "_ack_pending", "_goal_bias", "_role_cache"):
        b2.__dict__.pop(attr, None)
    b2.ping_queue.append({"ping_type": "help", "step": 0, "sender_idx": 1})
    for _ in range(PING_ACK_DELAY_STEPS):
        b2.note_step()
    assert b2.pop_ack() is True
    print("PASS test_forced_layout_supplier_role_and_safety_net")


def test_datalog_write_retries_on_permission_error():
    """Test 25: Windows에서 파일이 잠겨 PermissionError가 나도 재시도로 저장된다."""
    import builtins, os, pickle, tempfile
    from experiment.server_ext import data_log as dl
    tmp = tempfile.mkdtemp()
    target = os.path.join(tmp, "x.pkl")
    real_open = builtins.open
    calls = {"n": 0}

    def flaky_open(path, mode="r", *a, **k):
        if str(path) == target and "w" in mode:
            calls["n"] += 1
            if calls["n"] <= 2:
                raise PermissionError(13, "locked")
        return real_open(path, mode, *a, **k)
    builtins.open = flaky_open
    old_sleep = dl.time.sleep
    dl.time.sleep = lambda s: None
    try:
        out = dl._dump_with_retry({"a": 1}, target)
    finally:
        builtins.open = real_open
        dl.time.sleep = old_sleep
    assert calls["n"] == 3 and str(out) == target
    assert pickle.load(real_open(target, "rb")) == {"a": 1}
    print("PASS test_datalog_write_retries_on_permission_error")


if __name__ == "__main__":
    test_unknown_action_still_raises_keyerror()
    test_normal_move_unaffected()
    test_ping_action_routed_not_keyerror()
    test_unknown_ping_type_ignored()
    test_pings_land_in_trajectory()
    test_ping_routes_to_real_reactive_bot()
    test_bot_shows_ok_ack_only_after_understanding_delay()
    test_tick_calls_note_step_on_bot()
    test_ping_appears_in_get_state()
    test_ping_disappears_after_display_window()
    test_activate_propagates_new_mdp_to_policies_with_update_hook()
    test_real_bot_update_for_layout_rebuilds_mlam_for_new_layout()
    test_help_and_mine_pings_change_goal_choice_for_a_while()
    test_goal_choice_by_distance_from_sender()
    test_bot_drops_undeliverable_soup_on_counter_instead_of_stalling()
    test_move_aside_bfs_helpers()
    test_move_ping_reroutes_around_blocker_in_open_area()
    test_move_ping_waits_then_forces_retreat_in_dead_end()
    test_enqueue_action_overwrites_stale_instead_of_blocking()
    test_bot_speed_throttle_halves_action_frequency()
    test_speed_throttle_survives_pickle_without_tick_counter()
    test_order_queue_add_deliver_reject()
    test_datalog_nickname_and_files()
    test_bot_delivers_when_counters_full_and_follows_oldest_order()
    test_build_class_instantiates_with_all_mixins()
    test_forced_layout_supplier_role_and_safety_net()
    test_datalog_write_retries_on_permission_error()
    test_end_to_end_logging_and_metrics()
    print("Phase 2 전체(핑 채널 + 봇 반응 + 로깅 + 지표 계산) 테스트 통과.")
