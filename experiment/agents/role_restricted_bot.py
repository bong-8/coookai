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
            mdp, NO_COUNTERS_PARAMS
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
        # 방어 로직으로 가장 가까운 feature로 이동하게 함 (원본 fallback과 동일)
        if len(motion_goals) == 0:
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
# ping_type -> 반응 시 우선적으로 배제할 역할(=상대에게 양보) 매핑.
# TODO: 파일럿에서 실제 핑 목록(3~5종) 확정되면 갱신
_PING_RESPONSE_MAP = {
    "help": None,       # 도와줘: 상대 근처 병목 작업으로 이동 (아래 _respond_to_help)
    "look": None,       # 이거 봐: 별도 행동 변화 없음 (주의 환기용, 로깅만)
    "mine": "deliver",  # 내가 할게: 배달 역할을 상대에게 양보
    "ok": None,          # OK: 행동 변화 없음
}

REACTION_DELAY_STEPS = 3  # 약 0.5~1초 상당(스텝 길이에 따라 조정) 지연 후 반응


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
            role_to_yield = _PING_RESPONSE_MAP.get(ping_type)
            if role_to_yield:
                # 지정된 역할을 일시적으로 제외 목록에 추가 (상대에게 양보)
                original = set(self.excluded_roles)
                self.excluded_roles = original | {role_to_yield}
                try:
                    return super().ml_action(state)
                finally:
                    self.excluded_roles = original
            if ping_type == "help":
                return self._respond_to_help(state)
        return super().ml_action(state)

    def _respond_to_help(self, state):
        """
        '도와줘' 핑: 상대 근처의 병목 작업(냄비 채우기 등)으로 목표를 옮긴다.
        TODO: 파일럿에서 "병목 작업"의 정의(어떤 pot 상태를 우선할지) 확정
        """
        pot_states_dict = self.mlam.mdp.get_pot_states(state)
        partially_full = self.mlam.mdp.get_partially_full_pots(pot_states_dict)
        if partially_full:
            goals = self.mlam.put_onion_in_pot_actions(pot_states_dict)
            player = state.players[self.agent_index]
            goals = [
                mg for mg in goals
                if self.mlam.motion_planner.is_valid_motion_start_goal_pair(
                    player.pos_and_or, mg
                )
            ]
            if goals:
                return goals
        return super().ml_action(state)
