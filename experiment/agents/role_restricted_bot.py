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
from overcooked_ai_py.mdp.actions import Action, Direction
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


# 카운터(내려놓을 빈 자리)가 이 개수 이하로 남으면, 봇이 평소 제외된
# "deliver" 역할이어도 들고 있는 수프를 직접 서빙한다(2026-10-05 피드백:
# 봇이 못 서빙하고 카운터에 수프/접시만 쌓다가 자리가 없어지면 막힌다).
DELIVER_WHEN_FREE_COUNTERS_AT_MOST = 1


def _ingredient_names(recipe_like):
    """Recipe/SoupState 비슷한 객체에서 재료 이름 리스트를 뽑는다."""
    ings = recipe_like.ingredients
    return [getattr(i, "name", i) for i in ings]


# ── 레이아웃별 "봇이 실제로 할 수 있는 일" 분석 (2026-10-05) ─────────────
# 피드백: forced_coordination처럼 한 플레이어는 재료·접시 쪽에만, 다른 플레이어는
# 냄비·서빙 쪽에만 갈 수 있는 레이아웃에서, 봇이 "서빙 불가 / 접시를 가끔만
# 듦" 같은 일반 규칙을 그대로 따르면 게임 진행 자체가 막힌다(원본
# GreedyHumanModel 주석도 이 레이아웃에서는 동작 안 한다고 경고한다). 그래서
# 레이아웃 지형(걸어서 닿을 수 있는 칸)을 분석해, 봇이 냄비/서빙대에 아예
# 못 닿는 레이아웃에서는 "공급자(supplier)" 역할로 바꾼다 — 사람과 공유하는
# 카운터(handoff)에 접시와 양파를 계속 채워주는 일만 한다.
def _reachable_positions(mdp, start):
    walkable = set(mdp.get_valid_player_positions())
    seen = {start}
    stack = [start]
    while stack:
        pos = stack.pop()
        for d in Direction.ALL_DIRECTIONS:
            nxt = Action.move_in_direction(pos, d)
            if nxt in walkable and nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _adjacent_to(reach, pos):
    return any(
        Action.move_in_direction(pos, d) in reach for d in Direction.ALL_DIRECTIONS
    )


def analyze_layout_roles(mdp, agent_index):
    """봇이 이 레이아웃에서 어떤 역할을 맡아야 하는지.
    반환: {"role": "normal" | "supplier", "handoff": [사람과 공유하는 카운터 좌표]}"""
    starts = mdp.start_player_positions
    mine = _reachable_positions(mdp, starts[agent_index])
    other = _reachable_positions(mdp, starts[1 - agent_index])
    key_feats = list(mdp.get_pot_locations()) + list(mdp.get_serving_locations())
    can_cook_or_serve = any(_adjacent_to(mine, f) for f in key_feats)
    if can_cook_or_serve:
        return {"role": "normal", "handoff": []}
    handoff = [
        c for c in mdp.get_counter_locations()
        if _adjacent_to(mine, c) and _adjacent_to(other, c)
    ]
    return {"role": "supplier", "handoff": handoff}


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
        self._bot_tick_counter = 0

    def action(self, state):
        """
        속도 조절(2026-10-05, "AI 봇이 너무 너무 빨라. 유저의 템포를 올리기
        보단 봇의 템포(속도)를 지금의 1/2정도로" 피드백): 서버 틱 속도(=
        사람의 반응성, app.py의 GAME_TICK_FPS)는 그대로 두고 봇의 "실제
        행동 빈도"만 BOT_SPEED_DIVISOR분의 1로 줄인다.

        왜 game.py의 ticks_per_ai_action(이미 있는, 딱 이 용도로 보이는
        생성자 파라미터)을 안 쓰는가: 적용하기 전에 game.py의
        apply_actions()를 읽어보니, 그걸 1보다 크게 설정하면 실제로는 게임
        전체가 멈춰버리는 버그가 있다 — NPC 쪽은 매 틱
        `self.pending_actions[i].get(block=True)`로 무조건 블로킹하는데,
        다음 행동을 계산하게 하는 state push는
        `curr_tick % ticks_per_ai_action == 0`인 틱에서만 일어난다. 즉
        "건너뛰는" 틱에는 새 행동이 큐에 들어올 길이 전혀 없어서 그 틱의
        get()이 영원히 막히고, 사람 쪽까지 포함해 게임 전체가 멈춘다
        (원본 데모 코드 자체에 있던 버그로 보임 — 트레이닝된 RL 정책처럼
        매번 빠르게 응답하는 에이전트만 쓰는 전제라면 ticks_per_ai_action>1을
        실제로 쓸 일이 없어서 지금까지 드러나지 않았을 뿐).

        그래서 그 파라미터는 건드리지 않고, 여기서 매 틱 action()이 (큐를
        막지 않고) 즉시 반환하게 하면서 "건너뛰는 틱"에는 그냥 STAY를
        반환하는 방식을 쓴다 — 서버/큐 쪽에는 전혀 영향이 없고(매 틱 바로
        응답하니 블로킹 없음) 봇만 체감상 느려진다.

        self.prev_state를 건너뛰는 틱에 "안" 건드리는 이유(한 번 직접
        갱신했다가 실제로 역효과를 내서 되돌린 결정 — 2026-10-05): 처음엔
        건너뛰는 틱에도 prev_state를 매번 최신화했는데, 그렇게 하면 오히려
        auto_unstuck이 "진짜" 턴마다 거의 매번 잘못 발동했다. 건너뛰는 틱은
        항상 STAY라 위치가 안 바뀌고, 그 state를 prev_state로 저장해두면
        바로 다음 "진짜" 턴에서 "직전 상태 대비 위치 변화 없음"으로 보여
        (실제로는 그냥 한 틱 쉰 것뿐인데) 무작위 탈출 행동이 섞여 들어갔다
        (test_bot_drops_undeliverable_soup_on_counter_instead_of_stalling가
        이걸로 실패하면서 발견). auto_unstuck은 원래 "연속된 두 번의 '진짜'
        행동 계산 사이에 위치가 안 바뀌었는가"를 보려는 것이므로, prev_state는
        부모(GreedyHumanModel.action())가 실제로 호출될 때만(=이 메서드가
        super().action()으로 내려갈 때만) 갱신되게 그대로 둬야 맞다 — 건너뛰는
        틱에서는 prev_state를 아예 손대지 않는다.

        PingReactiveBot("비켜줘" 처리 중)은 이 메서드를 거치지 않고 자체
        action()에서 바로 move_action을 반환하는 경로가 있다 — 교착상태
        회피 반응은 이 속도 제한과 무관하게 항상 즉시 나가야 하므로 의도된
        동작이다(느려진 회피는 오히려 충돌/교착 위험을 키움).

        self._bot_tick_counter를 getattr(..., 0)으로 방어적으로 읽는 이유
        (실제 배포 환경에서 발견한 버그, 2026-10-05): pickle_agent.py로
        만들어 둔 기존 agent.pickle 파일들은 이 변경 *이전*의
        RoleRestrictedBot.__init__으로 만들어진 것이라, pickle.load()로
        복원된 인스턴스의 __dict__에는 _bot_tick_counter가 아예 없다
        (pickle은 __init__을 다시 실행하지 않고 저장 당시의 __dict__만
        그대로 복원하므로). 그 상태에서 `self._bot_tick_counter += 1`을
        그대로 쓰면 AttributeError가 나는데, 이게 하필
        npc_policy_consumer(게임 메인 루프가 아니라 NPC 전용 백그라운드
        스레드) 안에서 조용히 터진다 — 바로 위 reset()의 agent_index
        주석에 이미 적어둔 것과 똑같은 종류의 함정이다. 스레드가 죽으면
        그 뒤로 이 NPC의 pending_actions 큐에는 아무것도 안 들어오고,
        apply_actions()의 `self.pending_actions[i].get(block=True)`가
        영원히 풀리지 않아 **게임 전체(사람 포함)가 멈춘다** — 실제로 "게임
        시작하자마자 Time Left가 59.99...에서 멈춘다"는 형태로 재현됨
        (타이머도 매 틱 갱신되므로 같이 멈춤). agent.pickle을
        pickle_agent.py로 다시 만들면(최신 __init__이 다시 실행되므로)
        근본적으로 해결되지만, 기존에 이미 만들어 둔 pickle들까지 전부 다시
        구워야 하는 번거로움을 피하려고 여기서 getattr로 방어했다 — 새로
        만드는 pickle이든 기존 pickle이든 둘 다 안전하게 동작한다.
        """
        self._bot_tick_counter = getattr(self, "_bot_tick_counter", 0) + 1
        if (
            BOT_SPEED_DIVISOR > 1
            and self._bot_tick_counter % BOT_SPEED_DIVISOR != 0
        ):
            return Action.STAY, {
                "action_probs": self.a_probs_from_action(Action.STAY)
            }
        if self._role_info()["role"] == "supplier":
            return self._supplier_action(state)
        try:
            return super().action(state)
        except AssertionError as e:
            # 안전망(2026-10-05): "할 수 있는 일이 하나도 없는 상황"(예: 카운터가
            # 전부 차서 든 물건을 내려놓을 곳도 없음)에서 어설션이 터지면 NPC
            # 전용 스레드가 조용히 죽어 게임 전체가 멈춘다(위 getattr 주석의
            # 59.99초 멈춤과 같은 메커니즘). 멈춘 채 두기보다 이번 틱은 가만히
            # 있고 다음 틱에 다시 시도한다.
            msg = str(e)[:80]
            if getattr(self, "_last_warned", None) != msg:
                self._last_warned = msg
                print(f"[bot] 이번 틱에 할 일이 없어 대기합니다: {msg}")
            return Action.STAY, {"action_probs": self.a_probs_from_action(Action.STAY)}

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

    def _role_info(self):
        """레이아웃 분석 결과(캐시). getattr: 옛 pickle에는 속성이 없다."""
        key = (getattr(self.mlam.mdp, "layout_name", None), self.agent_index)
        cache = getattr(self, "_role_cache", None)
        if cache is None or cache[0] != key:
            cache = (key, analyze_layout_roles(self.mlam.mdp, self.agent_index))
            self._role_cache = cache
        return cache[1]

    def _supplier_goals(self, state):
        """공급자 역할: 사람과 공유하는 카운터(handoff)에 접시 1개 + 양파를
        계속 채워 둔다. 반환: 모션 목표 리스트, 또는 None(=지금은 기다림).

        규칙: 손이 비었을 때 handoff 빈 칸이 없으면 기다린다. 있으면 (접시가 하나도
        없고 냄비가 일하는 중이거나 양파가 이미 있으면) 접시를, 아니면 양파를
        집는다. 접시를 맨 마지막 칸에서 막지 않도록, 양파는 "빈 칸이 2개 이상이거나
        접시가 이미 있을 때만" 집는다. 든 물건은 빈 handoff 칸에 내려놓는다."""
        am = self.mlam
        mdp = am.mdp
        info = self._role_info()
        handoff = info["handoff"]
        player = state.players[self.agent_index]
        on_counter = {pos: state.objects[pos] for pos in handoff if pos in state.objects}
        free = [c for c in handoff if c not in on_counter]
        n_dish = sum(1 for o in on_counter.values() if o.name == "dish")
        n_onion = sum(1 for o in on_counter.values() if o.name == "onion")

        if player.has_object():
            if not free:
                return None
            return am._get_ml_actions_for_positions(free)

        if not free:
            return None
        pots = mdp.get_pot_states(state)
        pot_busy = any(pots[k] for k in pots if k != "empty")
        no_counter_items = defaultdict(list)
        if n_dish == 0 and (pot_busy or n_onion >= 1 or len(free) == 1):
            return am.pickup_dish_actions(no_counter_items, only_use_dispensers=True)
        if n_dish >= 1 or len(free) >= 2:
            return am.pickup_onion_actions(no_counter_items, only_use_dispensers=True)
        return am.pickup_dish_actions(no_counter_items, only_use_dispensers=True)

    def _supplier_action(self, state):
        goals = self._supplier_goals(state)
        player = state.players[self.agent_index]
        if goals:
            goals = [
                g for g in goals
                if self.mlam.motion_planner.is_valid_motion_start_goal_pair(
                    player.pos_and_or, g
                )
            ]
        if not goals:
            return Action.STAY, {"action_probs": self.a_probs_from_action(Action.STAY)}
        _, chosen_action, action_probs = self.choose_motion_goal(
            state.players_pos_and_or[self.agent_index], goals
        )
        return chosen_action, {"action_probs": action_probs}

    def _target_order(self, state):
        """봇이 만들 수프 = '아직 목록에서 삭제되지 않은 주문 중 가장 먼저 추가된 것'.
        게임(OrderQueueMixin)이 매 틱 self.open_orders를 갱신해 준다. 주문
        목록이 비어 있거나(모두 처리) 이 속성이 없으면(구형 pickle/단위 테스트)
        레이아웃의 허용 레시피 첫 번째로 대체한다. getattr: pickle.load는
        __init__을 다시 안 돌리므로 속성이 없을 수 있다."""
        orders = getattr(self, "open_orders", None)
        if orders:
            return orders[0]
        return list(state.all_orders)[0]

    def _needed_ingredient(self, state, target, pot_states_dict):
        """지금 들고 올 재료: 일부 채워진 냄비가 있으면 target과 비교해 모자란
        재료, 없으면 target의 첫 재료(양파 우선)."""
        want = list(_ingredient_names(target))
        for key, positions in pot_states_dict.items():
            if not key.endswith("_items") or key == "empty":
                continue
            n = int(key.split("_")[0])
            if n >= len(want):
                continue  # 이미 다 찬 냄비는 조리 시작 대상
            for pos in positions:
                have = _ingredient_names(state.get_object(pos))
                remaining = list(want)
                ok = True
                for h in have:
                    if h in remaining:
                        remaining.remove(h)
                    else:
                        ok = False
                        break
                if ok and remaining:
                    return "onion" if "onion" in remaining else remaining[0]
        return "onion" if "onion" in want else want[0]

    def _free_counter_count(self, state):
        empty = set(self.mlam.mdp.get_empty_counter_locations(state))
        return len([c for c in self.mlam.counter_drop if c in empty])

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
                next_order = self._target_order(state)
                key = "{}_items".format(len(next_order.ingredients))
                if pot_states_dict.get(key):
                    only = defaultdict(list)
                    only[key] = pot_states_dict[key]
                    motion_goals += am.start_cooking_actions(only)
            if "pickup_onion" not in self.excluded_roles:
                # 만들 수프(가장 오래된 열린 주문)에 필요한 재료만 집는다.
                need = self._needed_ingredient(
                    state, self._target_order(state), pot_states_dict
                )
                if need == "tomato":
                    motion_goals += am.pickup_tomato_actions(counter_objects)
                else:
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
            elif obj_name == "soup" and (
                "deliver" not in self.excluded_roles
                or self._free_counter_count(state)
                <= DELIVER_WHEN_FREE_COUNTERS_AT_MOST
            ):
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
                if not motion_goals:
                    # 내려놓을 빈 카운터도 없으면(가득 참) 예전 동작으로 대체해
                    # 어설션 실패로 봇 스레드가 죽는 일은 막는다.
                    motion_goals = am.go_to_closest_feature_actions(player)
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
#
# 재검토(2026-10-04, "각 핑에 대한 봇의 반응을 같이 고민해보고 적용" 피드백):
# "핑에 반응이 전혀 없는 것 같다"는 느낌의 진짜 원인은 이 표의 규칙 자체가
# 아니라, 봇이 핑을 "들었다"는 걸 화면에 전혀 보여주지 않았던 것이었다(핑
# 말풍선이 보낸 사람 머리 위에만 떴음 — 자세한 내용은 ping_game.py의
# _route_ping_to_npc_bots() 주석 참고). 그래서 그 함수에서 핑 수신 즉시
# (지연 없이) 봇 머리 위에 OK 말풍선을 띄우는 시각적 수신확인을 새로
# 추가했다. 이 표(행동 반응 자체)는 다시 검토한 결과 그대로 유지하기로
# 했다: help/mine처럼 "상대가 뭔가 해주길 바라는" 핑은 이미 역할 제한을
# 풀거나 양보하는 식으로 실제 행동이 바뀌고, look/ok처럼 "그냥 알림"에
# 해당하는 핑은 애초에 행동을 바꿀 이유가 없다(모든 핑에 행동을 바꾸게
# 하면 오히려 "OK라고만 했는데 봇이 갑자기 하던 일을 바꾼다"는 혼란을
# 만들 위험이 있음). 즉 "들었다는 확인"은 모든 핑 타입에 공통으로 주고,
# "행동이 실제로 바뀌는지"는 핑의 의미에 따라 다르게 남겨두는 것이 Phase 4
# 핑-행동 일치 지표 설계 의도와도 맞다.
# TODO: 파일럿에서 실제 핑 목록(3~5종)과 반응 규칙 최종 확정되면 갱신
_PING_RESPONSE_MAP = {
    "help": "ALL",
    "mine": "deliver",
    "ok": None,
}
# "move"(비켜줘, 2026-10-04에 "look" 자리를 교체)는 이 맵으로 표현할 수 있는
# "역할 제외"가 아니라 "지금 서 있는 자리를 비켜준다"는 전혀 다른 종류의
# 반응이라 여기 안 넣고 PingReactiveBot에 별도 로직(_decide_move_aside_action)
# 으로 분리했다 — 아래 "Phase 3b: 비켜줘(move) 핑" 섹션 참고.
MOVE_PING_TYPE = "move"

BOT_SPEED_DIVISOR = 3  # 1=평소 속도, 2=절반 속도, 3=1/3 속도...
# "AI 봇이 너무 너무 빨라서 쌓인 수프를 사람이 못 따라간다, 유저의 템포를
# 올리기보단 봇의 템포를 1/2로 낮추자"는 피드백(2026-10-05)으로 2(절반)로
# 처음 추가. 그걸 실제로 플레이해보고 "그래도 봇이 좀 빠른 것 같다"는
# 추가 피드백(같은 날)으로 3(1/3 속도)으로 한 단계 더 낮췄다.
# RoleRestrictedBot.action()의 속도 조절 로직 참고(바로 위 클래스 정의).
# 더 느리게/빠르게 하고 싶으면 이 값만 바꾸면 된다 — 인스턴스 속성이 아니라
# 모듈 상수라 매 action() 호출 때마다 새로 읽으므로, 이미 구워둔
# agent.pickle을 다시 만들 필요도 없다(값만 bot_tick_counter와 비교하는
# 용도일 뿐, pickle에 안 박혀 있음).

# 핑을 "이해"하는 데 걸리는 시간(2026-10-05 피드백: "0.1초도 안 돼서 바로 OK가
# 뜨는 건 이해하고 반응한 게 아니라 OK부터 하고 이해하는 느낌"). 봇은 핑을 받고
# 이 시간이 지난 뒤에야 (1) OK 말풍선을 띄우고 (2) 그 핑에 맞게 행동하기
# 시작한다 — 즉 "이해(지연) → OK → 행동" 순서. 10fps 기준 8틱 ≈ 0.8초.
PING_ACK_DELAY_STEPS = 8
# 도와줘/내가 할게 효과가 지속되는 시간: 100틱 ≈ 10초(다음 핑이 오면 교체).
# 예전엔 핑 하나가 "의사결정 딱 한 번"에만 반영됐고, 속도 제한(BOT_SPEED_DIVISOR)
# 때문에 3틱 중 2틱에서는 그마저도 버려져서 핑이 거의 티가 안 났다.
PING_EFFECT_STEPS = 100

REACTION_DELAY_STEPS = 5  # 약 0.5~1초 상당(스텝 길이에 따라 조정) 지연 후 반응
# 핑 유효 반응 창("핑을 보낸 뒤 몇 스텝 안에 반응해야 반응으로 인정하는가")은
# REACTION_DELAY_STEPS*4 스텝으로 아래에서 계산된다. 이 값은 서버 틱 속도에
# 비례한 "스텝" 단위라, 2026-10-04에 틱 속도를 6fps->10fps로 올리면서
# (app.py의 GAME_TICK_FPS) 그대로 뒀다면 유효 창이 약 2초→1.2초로 저절로
# 짧아져 버렸을 것이다(12스텝 ÷ 6fps=2초 vs 12스텝 ÷ 10fps=1.2초). 설계
# 의도(약 2초)를 유지하려고 3→5로 같이 올렸다(20스텝 ÷ 10fps=2초).


# ── Phase 3b: 비켜줘(move) 핑 ───────────────────────────────────────
# 설계 배경(2026-10-04, "핑이 직관적이지 않다"는 피드백): help/mine/ok는
# 전부 "어떤 역할을 할지"를 바꾸는 사회적 신호였는데, 그중 어느 것도
# "지금 당장 서로 길을 막고 있다"는 물리적 충돌 상황에는 직접 대응하지
# 않았다. "비켜줘"는 그 자리를 메우는, 게임에 즉각적인 영향을 주는 핑이다.
#
# 왜 mlam.motion_planner를 그대로 못 쓰는가: 그 플래너는 레이아웃마다
# 미리 계산해 디스크에 캐시해둔 "고정" 그래프라(2차 수정 때 다룬 그 캐시),
# "지금 상대가 서 있는 칸"처럼 매 틱 바뀌는 장애물을 반영하지 못한다.
# 그래서 "비켜주기" 전용으로, 그 순간만 쓰는 가벼운 그리드 BFS를 따로 둔다
# (레이아웃이 커도 수십 칸짜리 평범한 BFS라 매 틱 돌려도 비용이 거의 없음).
#
# 교착상태(둘 다 서로 기다리며 영원히 안 움직이는 상황) 방지 설계: 셋 중
# 하나도 아니라 "항상 셋 다, 순서대로" 시도한다 — (1) 사람이 서 있는 칸만
# 피해서 원래 목적지까지 가는 대안 경로가 있으면 그걸로 이동. (2) 대안이
# 전혀 없으면(진짜 외길) MOVE_ASIDE_GRACE_TICKS만큼만 그 자리에서 기다림
# (사람이 먼저 비켜줄 짧은 유예). (3) 그래도 안 풀리면 봇이 무조건
# 뒤로(사람에게서 가장 멀어지는 칸으로) 물러난다 — "누가 먼저 움직일지"를
# 매번 다시 저울질하게 하면 둘 다 기다리며 영원히 멈출 수 있어서, 이 마지막
# 수는 선택이 아니라 확정 동작으로 둔다. 이 세 단계 덕에 "둘 다 끝까지 안
# 움직이는" 상황은 수학적으로 생길 수 없다(유예가 끝나면 반드시 (1) 아니면
# (3)이 실행됨).
MOVE_ASIDE_GRACE_TICKS = 10  # ≈1초 (app.py의 GAME_TICK_FPS=10fps 기준).
# 처음엔 기존 핑 유효창(REACTION_DELAY_STEPS*4 ≈2초)과 맞췄었는데, 실제
# 플레이 전 "유예가 너무 길게 느껴질 수 있다"는 판단으로 1초로 줄였다
# (2026-10-04). 서버 틱 속도(config.json의 MAX_FPS)를 바꾸면 "틱 수"의
# 실제 시간도 같이 바뀌므로, 1초를 유지하려면 이 값도 같이 조정해야 한다
# (예: 6fps로 되돌리면 MOVE_ASIDE_GRACE_TICKS=6).

# 흔들림(flip-flop) 방지용 고정 우선순위 — 동률인 선택지 중 늘 같은 방향을
# 고르게 해서, 사람이 미세하게 움직일 때마다 봇이 다른 칸으로 갈아타며
# 제자리서 떠는 것처럼 보이는 걸 막는다.
_MOVE_DIRECTION_PRIORITY = [
    Direction.NORTH,
    Direction.EAST,
    Direction.SOUTH,
    Direction.WEST,
]


def _manhattan_distance(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _bfs_first_step_avoiding(walkable, start, goal, blocked):
    """start에서 goal까지, blocked 칸 하나(핑을 보낸 사람이 지금 서 있는 칸)를
    피해서 가는 최단 경로의 "첫 걸음"(Direction)을 반환한다. start==goal이거나
    그런 경로가 전혀 없으면 None.

    mlam.motion_planner와 달리 이건 그 자리에서 즉석으로 도는 가벼운 BFS라
    "지금 이 순간 상대가 서 있는 칸"처럼 매 틱 바뀌는 장애물을 반영할 수
    있다 — 대신 레이아웃 전체를 쓰는 고정 그래프가 아니라 walkable 칸들에
    대한 단순 격자 탐색이라, 방향 전환 비용 같은 모션 플래너 특유의 비용
    계산은 하지 않는다(그 정밀도는 지금 "비켜주기" 용도에는 필요 없음).
    """
    if start == goal:
        return None
    visited = {start}
    queue = deque([(start, None)])
    while queue:
        pos, first_step = queue.popleft()
        for d in _MOVE_DIRECTION_PRIORITY:
            nxt = Action.move_in_direction(pos, d)
            if nxt in visited or nxt not in walkable or nxt == blocked:
                continue
            step = first_step if first_step is not None else d
            if nxt == goal:
                return step
            visited.add(nxt)
            queue.append((nxt, step))
    return None


def _best_retreat_step(walkable, start, blocked):
    """대안 경로가 전혀 없을 때(진짜 외길) 쓰는 마지막 수단: blocked(사람)로
    부터 가장 멀어지는 방향으로 한 칸 물러날 Direction을 고른다. 동률이면
    _MOVE_DIRECTION_PRIORITY 순서로 고정해서 매번 같은 선택을 하게 한다.
    물러날 칸이 전혀 없으면(사방이 벽/카운터) None.
    """
    candidates = []
    for d in _MOVE_DIRECTION_PRIORITY:
        nxt = Action.move_in_direction(start, d)
        if nxt not in walkable or nxt == blocked:
            continue
        candidates.append((_manhattan_distance(nxt, blocked), d))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], _MOVE_DIRECTION_PRIORITY.index(item[1])))
    return candidates[0][1]


class PingReactiveBot(RoleRestrictedBot):
    """
    핑 큐를 참조해 일관되게 반응하는 봇. 핑-행동 일치 지표(Phase 4)가
    이 클래스의 일관성에 의존하므로, 반응 규칙은 확정 후 함부로 바꾸지 않는다.
    """

    def __init__(self, mlam, excluded_roles=None, ping_queue=None, **kwargs):
        super().__init__(mlam, excluded_roles=excluded_roles, **kwargs)
        # ping_queue: 외부(서버)에서 주입되는, 상대가 보낸 핑을 담는 deque
        # 각 원소: {"ping_type": str, "step": int, "sender_idx": int}
        self.ping_queue = ping_queue if ping_queue is not None else deque()
        self._pending_role_override = None
        self._override_expires_at = None
        self._curr_step = 0
        # ml_action()이 쓸, 이번 틱에 반응할 "즉시성" 핑(help/mine/ok) 하나.
        # action()의 드레인 단계에서 채워지고 ml_action()에서 한 번만 소비된다.
        self._pending_instant_entry = None
        self._incoming = []
        self._active_ping = None
        self._ack_pending = False
        self._goal_bias = None
        # "비켜줘(move)" 모드 상태 — help/mine처럼 ml_action()의 motion_goals
        # 선택이 아니라 action() 레벨에서 직접 다루므로 별도로 관리한다.
        self._move_ping_active_until_step = None
        self._move_ping_sender_idx = None
        self._move_block_since_step = None
        self._last_move_action = None
        self._walkable_positions = set(mlam.mdp.get_valid_player_positions())

    def note_step(self):
        """서버 tick마다 호출해 내부 스텝 카운터를 올리고, 핑 대기열을 처리한다.
        핑 처리를 NPC 스레드의 action()이 아니라 이 게임 틱 호출에 두는 이유:
        (1) 지연 시간이 항상 "게임 틱 수"로 정확하고, (2) 핑 큐/대기열을 한
        스레드만 만져서 경합이 없다."""
        self._curr_step += 1
        self._drain_ping_queue()

    def update_for_layout(self, mdp):
        super().update_for_layout(mdp)
        self._walkable_positions = set(mdp.get_valid_player_positions())
        # 레이아웃이 바뀌면 이전 레이아웃 기준으로 쌓인 "비켜주기" 상태를
        # 새 레이아웃까지 들고 가면 안 된다(엉뚱한 칸을 기준으로 판단하게 됨).
        self._clear_move_aside_mode()

    def _clear_move_aside_mode(self):
        self._move_ping_active_until_step = None
        self._move_ping_sender_idx = None
        self._move_block_since_step = None
        self._last_move_action = None

    def _ensure_ping_state(self):
        # 옛 agent.pickle에는 이 속성들이 없다(pickle은 __init__을 안 돌림).
        if not hasattr(self, "_incoming"):
            self._incoming = []
            self._active_ping = None
            self._ack_pending = False

    def pop_ack(self):
        """서버(PingMixin.tick)가 매 틱 호출: 봇이 방금 핑을 '이해'했으면 True를
        한 번 돌려준다 — 그때 OK 말풍선을 띄운다."""
        self._ensure_ping_state()
        if self._ack_pending:
            self._ack_pending = False
            return True
        return False

    def _drain_ping_queue(self):
        """새로 들어온 핑을 '이해 대기열'에 넣고, PING_ACK_DELAY_STEPS가 지난
        핑을 활성화한다. 활성화 순간 OK 표시 요청(_ack_pending)을 켠다.

        시간 기준은 핑이 담고 온 step(게임의 curr_tick)이 아니라 봇이 그 핑을
        처음 본 시점의 자기 카운터(_curr_step)다 — 두 카운터는 라운드가 바뀌면
        어긋날 수 있어서(게임은 라운드마다 0부터, 봇은 누적) 서로 섞어 비교하면
        두 번째 라운드부터 핑이 전부 '이미 만료'로 처리될 위험이 있다."""
        self._ensure_ping_state()
        while self.ping_queue:
            entry = self.ping_queue.popleft()
            self._incoming.append(
                # -1: 이 핑은 보낸 틱 "다음" note_step에서 처음 보이므로 그 한 틱을
                # 빼야 정확히 PING_ACK_DELAY_STEPS틱 뒤에 활성화된다.
                {"entry": entry, "due": self._curr_step + PING_ACK_DELAY_STEPS - 1}
            )
        waiting = []
        for item in self._incoming:
            if item["due"] > self._curr_step:
                waiting.append(item)
            else:
                self._activate_ping(item["entry"])
        self._incoming = waiting

    def _activate_ping(self, entry):
        ping_type = entry["ping_type"]
        self._ack_pending = True
        if ping_type == MOVE_PING_TYPE:
            self._move_ping_active_until_step = (
                self._curr_step + REACTION_DELAY_STEPS * 4
            )
            self._move_ping_sender_idx = entry.get("sender_idx")
            self._move_block_since_step = None
            self._last_move_action = None
        elif ping_type in ("help", "mine"):
            self._active_ping = {
                "type": ping_type,
                "sender_idx": entry.get("sender_idx"),
                "until": self._curr_step + PING_EFFECT_STEPS,
            }

    def _active_ping_effect(self, state):
        """지금 유효한 도와줘/내가 할게 효과 -> (type, 보낸 사람 위치) 또는 None."""
        ap = getattr(self, "_active_ping", None)
        if not ap:
            return None
        if self._curr_step > ap["until"]:
            self._active_ping = None
            return None
        idx = ap.get("sender_idx")
        if idx is None or idx == self.agent_index or not (0 <= idx < len(state.players)):
            return None
        return ap["type"], state.players[idx].position

    def _current_goal_position(self, state):
        """역할 제한이 반영된, 지금 봇이 원래 가려던 목적지 칸.

        PingReactiveBot.ml_action이 아니라 그 부모(RoleRestrictedBot)의
        ml_action을 직접 호출한다 — help/mine 핑 반응(역할 일시 해제/양보)이
        여기 끼어들면 "비켜주기"가 엉뚱한 목적지를 기준으로 경로를 찾게
        되므로, "지금 핑과 무관하게 평소 하려던 일"만 순수하게 알고 싶을 때
        쓰는 조회용 호출이다.
        """
        start_pos_and_or = state.players_pos_and_or[self.agent_index]
        motion_goals = RoleRestrictedBot.ml_action(self, state)
        if not motion_goals:
            return None
        goal, _ = self.get_lowest_cost_action_and_goal(
            start_pos_and_or, motion_goals
        )
        return goal[0] if goal is not None else None

    def _decide_move_aside_action(self, state, my_pos, sender_pos):
        """"비켜줘" 핑에 대한 실제 반응. 세 단계를 순서대로 시도한다 —
        위 MOVE_ASIDE_GRACE_TICKS 주석에 적은 교착상태 방지 설계 참고."""
        walkable = self._walkable_positions

        # 흔들림 방지: 이전에 쓰던 방향이 지금도 유효하면(벽/카운터가 아니고,
        # 사람이 서 있는 칸도 아니면) 매번 다시 계산하지 않고 그대로 유지한다.
        if self._last_move_action is not None:
            nxt = Action.move_in_direction(my_pos, self._last_move_action)
            if nxt in walkable and nxt != sender_pos:
                return self._last_move_action

        # 1단계: 원래 목적지까지, 사람이 서 있는 칸만 피해서 가는 대안 경로.
        goal_pos = self._current_goal_position(state)
        step = None
        if goal_pos is not None:
            step = _bfs_first_step_avoiding(walkable, my_pos, goal_pos, sender_pos)

        if step is not None:
            self._last_move_action = step
            self._move_block_since_step = None  # 진전이 있었으니 유예 타이머 리셋
            return step

        # 2단계: 대안이 전혀 없음(진짜 외길) -> 유예 기간 동안은 일단 대기.
        if self._move_block_since_step is None:
            self._move_block_since_step = self._curr_step
        if self._curr_step - self._move_block_since_step < MOVE_ASIDE_GRACE_TICKS:
            self._last_move_action = None
            return Action.STAY

        # 3단계: 유예가 끝났는데도 여전히 막혀 있음 -> 봇이 무조건 후퇴.
        # ("누가 먼저 움직일지"를 매번 다시 저울질하면 둘 다 영원히 기다릴 수
        # 있어서, 이 시점부터는 선택이 아니라 확정 동작으로 둔다.)
        retreat = _best_retreat_step(walkable, my_pos, sender_pos)
        self._move_block_since_step = self._curr_step  # 후퇴 후 유예 타이머 재시작
        self._last_move_action = retreat
        return retreat if retreat is not None else Action.STAY

    def action(self, state):

        if self._move_ping_active_until_step is not None:
            if self._curr_step > self._move_ping_active_until_step:
                self._clear_move_aside_mode()
            else:
                sender_idx = self._move_ping_sender_idx
                if (
                    sender_idx is not None
                    and sender_idx != self.agent_index
                    and 0 <= sender_idx < len(state.players)
                ):
                    my_pos = state.players[self.agent_index].position
                    sender_pos = state.players[sender_idx].position
                    if _manhattan_distance(my_pos, sender_pos) == 1:
                        move_action = self._decide_move_aside_action(
                            state, my_pos, sender_pos
                        )
                        return move_action, {
                            "action_probs": self.a_probs_from_action(move_action)
                        }
                        # 인접하지 않으면(= 실제로 막고 있지 않으면) 아무 것도
                        # 안 바꾸고 아래 super().action()으로 그대로 진행한다
                        # — "인접할 때만 실제 반응"하기로 한 설계 결정.

        effect = self._active_ping_effect(state)
        if effect is None:
            return super().action(state)
        ping_type, sender_pos = effect
        # 도와줘: 역할 제한을 풀고 "나(보낸 사람)와 가까운" 일부터 도와준다.
        # 내가 할게: 서빙은 사람 몫으로 두고 "나와 먼" 일부터 맡는다.
        saved_roles = set(self.excluded_roles)
        if ping_type == "help":
            self.excluded_roles = set()
            self._goal_bias = ("near", sender_pos)
        else:
            self.excluded_roles = saved_roles | {"deliver"}
            self._goal_bias = ("far", sender_pos)
        try:
            return super().action(state)
        finally:
            self.excluded_roles = saved_roles
            self._goal_bias = None

    def choose_motion_goal(self, start_pos_and_or, motion_goals):
        """도와줘/내가 할게 효과 중이면, 봇 자신과의 거리 대신 '핑을 보낸 사람과의
        거리'로 목표를 고른다(가까운 곳 / 먼 곳). 거리가 같으면 평소처럼 봇이
        가기 쉬운(이동 비용이 적은) 쪽."""
        bias = getattr(self, "_goal_bias", None)
        if not bias or not motion_goals or self.hl_boltzmann_rational:
            return super().choose_motion_goal(start_pos_and_or, motion_goals)
        kind, ref_pos = bias
        scored = []
        for goal in motion_goals:
            plan, _, cost = self.mlam.motion_planner.get_plan(start_pos_and_or, goal)
            dist = _manhattan_distance(goal[0], ref_pos)
            scored.append((dist if kind == "near" else -dist, cost, goal, plan[0]))
        _, _, goal, first_action = min(scored, key=lambda x: (x[0], x[1]))
        return goal, first_action, self.a_probs_from_action(first_action)
