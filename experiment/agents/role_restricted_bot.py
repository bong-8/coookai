"""
Phase 1 + Phase 3 스텁: 역할 제한 + 핑 반응 규칙 기반 AI 봇.

원본 GreedyHumanModel(overcooked_ai_py/agents/agent.py)을 건드리지 않고
새 파일에서 상속·확장한다. 원본과의 차이는 이 파일의 diff로 IRB 서류에
그대로 첨부 가능하다.

TODO(파일럿 전 확정 필요):
- excluded_roles 의 실제 값 (예: ["deliver"])
- ping_type -> 반응 규칙 매핑 (_PING_RESPONSE_MAP)
- 반응 지연(REACTION_DELAY_STEPS) 튜닝
"""
import time
from collections import deque, defaultdict

from overcooked_ai_py.agents.agent import GreedyHumanModel
from overcooked_ai_py.planning.planners import (
    MediumLevelActionManager,
    NO_COUNTERS_PARAMS,
)


def build_mlam_params(mdp):
    """
    이 실험 봇 전용 MediumLevelActionManager 파라미터.

    실제 플레이테스트에서 발견한 버그: NO_COUNTERS_PARAMS를 그대로 쓰면
    "counter_drop": [] 라서(planners.py 정의 참고) place_obj_on_counter_actions()가
    *항상* 빈 리스트를 반환한다 — 즉 봇이 카운터에 물건을 내려놓는 게 애초에
    플래너 레벨에서 완전히 불가능한 상태였다. excluded_roles=["deliver"]인
    채로 완성된 수프를 들고 있으면(ml_action()의 방어 로직 참고) 내려놓을
    곳이 하나도 없어 제자리에서 멈춰버리는 버그로 이어졌다.

    그래서 NO_COUNTERS_PARAMS를 베이스로 하되 counter_drop/counter_goals만
    이 레이아웃의 모든 카운터 위치로 채운 파라미터를 쓴다 — "카운터에 뭔가를
    내려놓을 수 있다"는 능력만 켜고, wait_allowed/counter_pickup 등 나머지
    제약(=예측 가능성을 위해 의도적으로 좁힌 행동 공간, 3.4절 설계 철학)은
    그대로 유지한다. MediumLevelActionManager.from_pickle_or_compute()는
    params 내용이 바뀌면 디스크 캐시를 자동으로 무효화하고 다시 계산하므로
    (planners.py의 `mlam.params != mlam_params` 체크), 기존에 캐시된
    (counter_drop 없는) *_am.pkl이 남아 있어도 안전하게 새로 계산된다.
    """
    params = dict(NO_COUNTERS_PARAMS)
    all_counters = mdp.get_counter_locations()
    params["counter_drop"] = all_counters
    params["counter_goals"] = all_counters
    return params


# ── Phase 1: 역할 제한 ──────────────────────────────────────────────
class RoleRestrictedBot(GreedyHumanModel):
    """
    특정 역할(motion_goal 카테고리)을 의도적으로 봇의 선택지에서 제외한다.
    "예측 가능한 협력 파트너"이지 "최적 수행 파트너"가 아니어야 한다는
    3.4절 설계 철학을 코드로 강제하는 지점.

    excluded_roles 값과 ml_action() 내부 메서드명의 대응:
        "pickup_onion" -> pickup_onion_actions
        "pickup_dish"  -> pickup_dish_actions
        "put_in_pot"   -> put_onion_in_pot_actions / put_tomato_in_pot_actions
        "deliver"      -> deliver_soup_actions
        "start_cooking"-> start_cooking_actions
    """

    def __init__(self, mlam, excluded_roles=None, **kwargs):
        super().__init__(mlam, **kwargs)
        self.excluded_roles = set(excluded_roles or [])

    def reset(self):
        """
        실제 서버(overcooked_demo/server/game.py)에서 발견한 버그의 고정:
        원본 Agent.reset()(부모의 부모)은 self.agent_index = None으로 되돌린다
        ("트라젝토리 롤아웃 사이에 항상 reset해야 한다"는 원본 주석대로, 매
        롤아웃마다 actions()가 set_agent_index()를 다시 호출해주는 사용 패턴을
        전제로 한 설계). 그런데 이 데모 서버는 OvercookedGame.activate()에서
        게임을 시작할 때마다 무조건 npc_policy.reset()을 호출하고, 그 이후로는
        다시 set_agent_index()를 불러주지 않는다 (pickle로 로드된 에이전트는
        그 시점의 agent_index를 계속 갖고 있을 거라 가정함 — game.py의
        get_policy()가 Rllib 에이전트에는 agent_index=idx를 넘겨주는 것도
        그 전제 때문). 실제 Rllib용 RlLibAgent(human_aware_rl/rllib/rllib.py)를
        보면 reset()을 완전히 새로 정의해서 super().reset()을 아예 호출하지
        않는 방식으로 이 문제를 피해간다.

        우리는 GreedyHumanModel.reset()이 하는 다른 일(prev_state 초기화 등)은
        그대로 두고 싶으므로, agent_index만 저장했다가 복원하는 방식을 쓴다.
        이 버그는 FakeOvercookedGame 기반 단위 테스트(test_ping_logic.py)로는
        절대 잡을 수 없었다 — 실제 game.py의 activate()를 타야만 재현되는데,
        실제 Flask 서버를 처음 띄워서 브라우저 대신 socketio 클라이언트로
        게임을 시작해보다가(Phase 3 기동 검증) 발견했다.
        """
        saved_agent_index = getattr(self, "agent_index", None)
        super().reset()
        if saved_agent_index is not None:
            self.agent_index = saved_agent_index

    def update_for_layout(self, mdp):
        """
        레이아웃이 여러 개(config.json의 "layouts" 배열)인 세션에서, 서버가
        한 라운드가 끝나고 다음 레이아웃으로 넘어갈 때 호출해줘야 하는 훅.

        이 봇의 ml_action()은 전부 self.mlam(MediumLevelActionManager)을
        거쳐 동작하는데, mlam은 생성 시점의 레이아웃 지형(카운터 위치, 냄비
        위치, 이동 가능 경로 등)에 맞춰 미리 계산되는 객체라 다른 레이아웃에는
        그대로 못 쓴다(README의 "Layout Compatibility" 경고와 같은 이유).
        pickle로 저장할 때 baked-in된 mlam은 그 레이아웃 전용이므로, 세션
        중간에 레이아웃이 바뀌면 이 메서드로 새 mdp에 맞는 mlam으로 교체해야
        한다. 계산 비용을 줄이기 위해 MediumLevelActionManager.from_pickle_or_compute
        를 써서, 같은 레이아웃이면 디스크 캐시를 재사용한다(pickle_agent.py가
        쓰는 방식과 동일).

        호출 지점: experiment/server_ext/ping_game.py의 PingMixin.activate()
        오버라이드 — 원본 OvercookedGame.activate()가 라운드마다 self.mdp를
        새로 설정한 직후, ping_queue 속성 확인과 같은 방식(hasattr 덕 타이핑)
        으로 이 메서드가 있는 정책에만 호출해준다.
        """
        self.mlam = MediumLevelActionManager.from_pickle_or_compute(
            mdp, build_mlam_params(mdp)
        )
        self.mdp = mdp

    def ml_action(self, state):
        # 부모 클래스가 어떤 액션 카테고리에서 목표를 만들었는지 알 수 없으므로,
        # 카테고리별로 직접 재계산 후 제외 목록을 뺀 나머지만 합쳐서 반환한다.
        # (GreedyHumanModel.ml_action의 로직을 참고해 카테고리별로 나눔)
        player = state.players[self.agent_index]
        am = self.mlam
        counter_objects = self.mlam.mdp.get_counter_objects_dict(
            state, list(self.mlam.mdp.terrain_pos_dict["X"])
        )
        pot_states_dict = self.mlam.mdp.get_pot_states(state)

        motion_goals = []

        if not player.has_object():
            if "pickup_dish" not in self.excluded_roles:
                ready = pot_states_dict["ready"]
                cooking = pot_states_dict["cooking"]
                if ready or cooking:
                    motion_goals += am.pickup_dish_actions(counter_objects)
            if "start_cooking" not in self.excluded_roles:
                next_order = list(state.all_orders)[0]
                key = "{}_items".format(len(next_order.ingredients))
                if pot_states_dict.get(key):
                    only = defaultdict(list)
                    only[key] = pot_states_dict[key]
                    motion_goals += am.start_cooking_actions(only)
            if "pickup_onion" not in self.excluded_roles:
                motion_goals += am.pickup_onion_actions(counter_objects)
        else:
            obj_name = player.get_object().name
            if obj_name == "onion" and "put_in_pot" not in self.excluded_roles:
                motion_goals += am.put_onion_in_pot_actions(pot_states_dict)
            elif obj_name == "tomato" and "put_in_pot" not in self.excluded_roles:
                motion_goals += am.put_tomato_in_pot_actions(pot_states_dict)
            elif obj_name == "dish" and "pickup_soup" not in self.excluded_roles:
                motion_goals += am.pickup_soup_with_dish_actions(
                    pot_states_dict, only_nearly_ready=True
                )
            elif obj_name == "soup" and "deliver" not in self.excluded_roles:
                motion_goals += am.deliver_soup_actions()

        motion_goals = [
            mg for mg in motion_goals
            if self.mlam.motion_planner.is_valid_motion_start_goal_pair(
                player.pos_and_or, mg
            )
        ]

        # 제외 로직 때문에 목표가 하나도 안 남으면(봇이 멈추면) 안 되므로
        # 방어 로직이 필요하다.
        #
        # 실제 플레이테스트에서 발견된 버그: excluded_roles=["deliver"]인 채로
        # 봇이 완성된 수프(dish+soup)를 들고 있으면, 위 분기에서
        # "deliver"가 제외되어 motion_goals가 비고, 예전에는 여기서
        # go_to_closest_feature_actions(player)로 떨어졌다. 그런데 이 함수는
        # 양파/토마토 디스펜서·냄비·접시 디스펜서 위치만 알고 서빙 윈도/카운터는
        # 전혀 모른다(planners.py 정의 참고) — 즉 수프를 들고 있는 봇한테
        # "가장 가까운 냄비로 가라"는, 가서 할 수 있는 일이 없는 목표를 준
        # 것이다. 봇이 그 목표 지점에 도착하면 상태가 더 안 바뀌니 같은
        # ml_action이 계속 나오고, 결국 GreedyHumanModel.action()의
        # auto_unstuck(제자리에 멈춘 두 턴을 감지하면 무작위 행동을 주입하는
        # 안전장치)이 계속 발동해 "접시를 든 채 제자리에서 계속 꿈틀거리기만
        # 하고 아무것도 안 하는" 것처럼 보였다 — 네가 추측한 "접시를 못
        # 내려놓아서"가 정확한 원인이었다.
        #
        # 고침: 뭔가를 들고 있는데(=player.has_object()) 그걸로 할 수 있는
        # 다음 행동이 역할 제한 때문에 전부 막혀 있다면, "아무 데나 걷기"가
        # 아니라 place_obj_on_counter_actions()로 빈 카운터 위에 내려놓게
        # 한다. 그러면 사람 참가자가 그걸 집어서 마저 처리(예: 배달)할 수
        # 있다 — "일부 역할은 의도적으로 사람의 몫으로 남긴다"는 3.4절 설계
        # 철학과 정확히 들어맞는 동작이다. 아무것도 안 들고 있을 때의 기존
        # fallback(go_to_closest_feature_actions)은 그대로 둔다.
        if len(motion_goals) == 0:
            if player.has_object():
                motion_goals = am.place_obj_on_counter_actions(state)
            else:
                motion_goals = am.go_to_closest_feature_actions(player)
            motion_goals = [
                mg for mg in motion_goals
                if self.mlam.motion_planner.is_valid_motion_start_goal_pair(
                    player.pos_and_or, mg
                )
            ]

        assert len(motion_goals) != 0, (
            "RoleRestrictedBot: excluded_roles={} 로 인해 유효한 행동이 "
            "전혀 남지 않았습니다. excluded_roles 조합을 확인하세요."
            .format(self.excluded_roles)
        )
        return motion_goals


# ── Phase 3: 핑 반응 ────────────────────────────────────────────────
# ping_type별 반응 규칙. "ALL"은 excluded_roles를 일시적으로 전부 해제(=봇이
# 평소엔 피하던 일(배달 등)까지 포함해 그 순간 가장 효율적인 행동을 함),
# 역할 이름(예: "deliver")은 그 역할을 일시적으로 상대에게 "양보"(제외 목록에
# 추가), None은 행동 변화 없음(로깅만).
#
# 실제 브라우저로 플레이해보며 확정한 규칙(2026-10-03):
#   - help(도와줘): 제한 전부 해제 → 가장 급한 일(배달 등)을 봇이 직접 처리.
#     예전엔 "냄비에 재료가 일부 들어있을 때만" 반응하게 짜여 있어서 그 조건이
#     안 맞으면 평소랑 똑같이 행동해버려 "반응 안 하는 것처럼" 보이는 문제가
#     있었다 — 조건 없이 항상 반응하도록 단순화했다. 다만 이 순간 "제한 때문에
#     못 하고 있던 일"이 아예 없으면(예: 배달할 수프도 없고 할 일이 전부 허용
#     범위 안이면) 그래도 티가 안 날 수 있음 — 완전히 보장되는 건 아니라서
#     파일럿 전에 지도교수님과 "뭘 해야 '도움'으로 느껴지는지" 확정 필요.
#   - look(이거 봐): 행동 변화 없음(주의 환기용, 로깅만) — 그대로 유지.
#   - mine(내가 할게): "deliver" 역할을 일시적으로 상대에게 양보(기존 그대로).
#   - ok(OK): 행동 변화 없음 — 그대로 유지.
# TODO: 파일럿에서 실제 핑 목록(3~5종)과 반응 규칙 최종 확정되면 갱신
_PING_RESPONSE_MAP = {
    "help": "ALL",
    "look": None,
    "mine": "deliver",
    "ok": None,
}

REACTION_DELAY_STEPS = 5  # 약 0.5~1초 상당(스텝 길이에 따라 조정) 지연 후 반응
# 핑 유효 반응 창("핑을 보낸 뒤 몇 스텝 안에 반응해야 반응으로 인정하는가")은
# REACTION_DELAY_STEPS*4 스텝으로 아래에서 계산된다. 이 값은 서버 틱 속도에
# 비례한 "스텝" 단위라, 2026-10-04에 틱 속도를 6fps->10fps로 올리면서
# (app.py의 GAME_TICK_FPS) 그대로 뒀다면 유효 창이 약 2초→1.2초로 저절로
# 짧아져 버렸을 것이다(12스텝 ÷ 6fps=2초 vs 12스텝 ÷ 10fps=1.2초). 설계
# 의도(약 2초)를 유지하려고 3→5로 같이 올렸다(20스텝 ÷ 10fps=2초).


class PingReactiveBot(RoleRestrictedBot):
    """
    핑 큐를 참조해 일관되게 반응하는 봇. 핑-행동 일치 지표(Phase 4)가
    이 클래스의 일관성에 의존하므로, 반응 규칙은 확정 후 함부로 바꾸지 않는다.
    """

    def __init__(self, mlam, excluded_roles=None, ping_queue=None, **kwargs):
        super().__init__(mlam, excluded_roles=excluded_roles, **kwargs)
        # ping_queue: 외부(서버)에서 주입되는, 상대가 보낸 핑을 담는 deque
        # 각 원소: {"ping_type": str, "step": int}
        self.ping_queue = ping_queue if ping_queue is not None else deque()
        self._pending_role_override = None
        self._override_expires_at = None
        self._curr_step = 0

    def note_step(self):
        """서버 tick마다 호출해 내부 스텝 카운터를 올린다 (지연 계산용)."""
        self._curr_step += 1

    def _latest_unconsumed_ping(self):
        while self.ping_queue:
            entry = self.ping_queue.popleft()
            if self._curr_step - entry["step"] <= REACTION_DELAY_STEPS * 4:
                return entry
        return None

    def ml_action(self, state):
        entry = self._latest_unconsumed_ping()
        if entry is not None:
            ping_type = entry["ping_type"]
            rule = _PING_RESPONSE_MAP.get(ping_type)
            if rule == "ALL":
                # 도와줘: 제한을 전부 일시 해제하고 그 순간 가장 효율적인
                # 행동(배달 등 평소 제한된 역할 포함)을 하도록 함.
                original = set(self.excluded_roles)
                self.excluded_roles = set()
                try:
                    return super().ml_action(state)
                finally:
                    self.excluded_roles = original
            elif rule:
                # 지정된 역할을 일시적으로 제외 목록에 추가 (상대에게 양보)
                original = set(self.excluded_roles)
                self.excluded_roles = original | {rule}
                try:
                    return super().ml_action(state)
                finally:
                    self.excluded_roles = original
            # rule이 None(look/ok)이면 행동 변화 없이 아래로 그대로 진행
        return super().ml_action(state)
