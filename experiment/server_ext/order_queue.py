"""
주문 큐(order queue) — 2026-10-05.

원본 Overcooked-AI의 주문 방식은 "레이아웃 파일에 허용 레시피 목록이 고정돼
있고, 거기 해당하는 수프를 배달하면 점수를 받는" 정적 구조다(주문이 소모되지도
새로 들어오지도 않음). 실험에서 쓰려는 방식은 이렇다:

  - 게임 시작 시 주문 INITIAL_ORDER_COUNT개가 목록에 있다.
  - ORDER_ARRIVAL_INTERVAL_SEC(8초; 2026-10-05에 10→8)마다 허용 레시피 중 하나가 목록에 추가된다.
    만료는 없다(추가된 주문은 배달될 때까지 목록에 남는다).
  - 목록에 있는 레시피의 수프를 배달하면 점수를 받고 그 주문이 목록에서
    삭제된다. 목록에 없는 레시피를 배달하면 0점(수프는 사라짐 — 원본의
    "허용 레시피가 아니면 0점"과 동일한 동작).

왜 state(OvercookedState)의 all_orders를 안 건드리고 게임 객체에 따로 두는가:
  1) OvercookedState.all_orders는 목록이 비면 "모든 레시피 허용"(Recipe.
     ALL_RECIPES)으로 폴백한다. 즉 모든 주문을 처리해 목록이 비는 순간 오히려
     아무 수프나 점수를 받게 되어, "목록에 없으면 0점" 규칙을 상태 쪽에서는
     표현할 수 없다.
  2) 상태는 매 스텝 deepcopy되는데(deepcopy가 all_orders를 dict로 변환했다가
     다시 만듦) 가변 주문 목록을 거기 싣고 다니면 얻는 게 없다.
  3) 봇(GreedyHumanModel)은 state.all_orders를 "이 레이아웃이 허용하는 레시피"로
     읽는다(`list(state.all_orders)[0]`) — 그 의미를 그대로 유지하는 편이 안전.
  그래서 "지금 열려 있는 주문"은 이 믹스인이 self._open_orders로 따로 들고,
  (a) 배달 보상 계산은 mdp.deliver_soup 인스턴스를 얇게 감싸서(원본 파일 수정
  없음), (b) 화면 표시는 get_state()가 내보내는 딕셔너리의 state.all_orders를
  이 목록으로 바꿔치기해서(클라이언트 HUD는 그걸 그대로 아이콘으로 그림),
  (c) 연구 로그는 trajectory의 각 스텝에 open_orders/order_events를 얹어서 처리한다.

주문 순서는 레이아웃 이름으로 시드한 난수로 뽑는다 — 같은 난이도에서는 모든
참가자가 항상 똑같은 주문 순서를 보게 해서(무작위 순서의 차이가 점수 차이로
섞이지 않도록) 집단 간 비교의 통제를 높인다. 허용 레시피가 1종뿐인
레이아웃(원본 cramped_room 등)에서는 항상 같은 주문이라 너무 쉬워서,
실험용 *_mixed 레이아웃(양파+토마토, 4종)을 쓴다(2026-10-05).
"""
import random
import time

ORDER_ARRIVAL_INTERVAL_SEC = 8
INITIAL_ORDER_COUNT = 1


def _recipe_label(recipe):
    """로그/이벤트에 남길 사람이 읽기 쉬운 레시피 표현. 예: ["onion", "onion", "onion"]"""
    return list(recipe.to_dict()["ingredients"])


class OrderQueueMixin:
    """
    OvercookedGame(혹은 그 계약을 만족하는 클래스)에 믹스인한다. PingMixin과
    독립적이며, MRO상 PingMixin보다 앞에 두든 뒤에 두든 동작한다.
    필요한 계약: self.state(.all_orders/.bonus_orders), self.mdp(.order_bonus),
    self.start_time, self.curr_layout, self.trajectory, apply_actions()/
    activate()/get_state().
    """

    def _orders_init(self):
        self._open_orders = []
        self._allowed_orders = []
        self._next_order_at_sec = ORDER_ARRIVAL_INTERVAL_SEC
        self._order_events = []
        self._order_rng = random.Random(0)
        self._order_bag = []

    def _draw_recipe(self):
        """허용 레시피를 "섞은 가방(bag)"에서 하나씩 뽑는다 — 가방이 비면 다시
        섞어 채움. 그냥 무작위로 뽑으면 같은 메뉴가 연달아 나올 수 있는데
        (2026-10-05 피드백: 주문이 한 종류뿐이라 너무 쉬움), 가방 방식은 모든
        종류가 고르게 나오고 시드(레이아웃 이름)가 같으면 순서도 항상 같다."""
        if not self._order_bag:
            self._order_bag = list(self._allowed_orders)
            self._order_rng.shuffle(self._order_bag)
        return self._order_bag.pop()

    # ── 시간 ────────────────────────────────────────────────────────
    def _order_elapsed_sec(self):
        # 원본 get_state()의 time_left와 같은 기준(start_time)을 쓴다 —
        # 라운드 전환 때 reset()이 start_time을 reset_timeout만큼 미뤄주므로
        # 그 대기 시간은 자동으로 제외된다.
        return time.time() - self.start_time

    # ── 라운드 시작 ─────────────────────────────────────────────────
    def activate(self):
        super(OrderQueueMixin, self).activate()
        self._reset_orders()
        self._install_order_aware_delivery()
        self._sync_orders_to_bots()

    def _reset_orders(self):
        # 이 레이아웃이 허용하는 레시피들. 이 값은 state를 안 건드리므로
        # (위 설명 1번) 항상 레이아웃 정의 그대로다.
        self._allowed_orders = list(self.state.all_orders)
        layout = getattr(self, "curr_layout", "")
        self._order_rng = random.Random(str(layout))
        self._order_bag = []
        self._open_orders = [
            self._draw_recipe() for _ in range(INITIAL_ORDER_COUNT)
        ]
        self._next_order_at_sec = ORDER_ARRIVAL_INTERVAL_SEC
        self._order_events = [
            {"type": "initial", "recipe": _recipe_label(r), "elapsed": 0.0}
            for r in self._open_orders
        ]

    def _sync_orders_to_bots(self):
        """NPC 봇이 '가장 먼저 추가된 미처리 주문'을 만들도록 열린 주문 목록을
        넘겨준다(RoleRestrictedBot._target_order). 복사본을 넘겨 스레드 간
        동시 수정을 피한다."""
        for policy in getattr(self, "npc_policies", {}).values():
            policy.open_orders = list(self._open_orders)

    def _install_order_aware_delivery(self):
        # 라운드마다 mdp가 새로 만들어지므로(원본 activate) 라운드마다 다시
        # 감싼다. 원본 deliver_soup과 동일한 사전 검사/처리 뒤, 보상만
        # "열린 주문 목록" 기준으로 계산한다.
        def deliver_soup(state, player, soup):
            assert soup.name == "soup", "Tried to deliver something that wasn't soup"
            assert soup.is_ready, "Tried to deliever soup that isn't ready"
            player.remove_object()
            return self._resolve_delivery(state, soup.recipe)

        self.mdp.deliver_soup = deliver_soup

    def _resolve_delivery(self, state, recipe):
        elapsed = round(self._order_elapsed_sec(), 3)
        if recipe in self._open_orders:
            self._open_orders.remove(recipe)  # 같은 레시피 중 첫 번째 하나만 삭제
            reward = recipe.value
            if recipe in state.bonus_orders:
                reward = self.mdp.order_bonus * recipe.value
            self._order_events.append(
                {"type": "delivered", "recipe": _recipe_label(recipe),
                 "reward": reward, "elapsed": elapsed}
            )
            return reward
        self._order_events.append(
            {"type": "rejected", "recipe": _recipe_label(recipe),
             "reward": 0, "elapsed": elapsed}
        )
        return 0

    # ── 매 틱 ───────────────────────────────────────────────────────
    def apply_actions(self):
        result = super(OrderQueueMixin, self).apply_actions()
        self._add_due_orders()
        self._sync_orders_to_bots()
        events, self._order_events = self._order_events, []
        if self.trajectory:
            # 로그 해석용: 이번 스텝 직후 "열려 있는 주문" 목록과 이번 스텝의
            # 주문 이벤트(추가/배달/거절). state 필드의 all_orders는 레이아웃의
            # 허용 목록(정적)이라 이 값과 다르다.
            self.trajectory[-1]["open_orders"] = [
                _recipe_label(r) for r in self._open_orders
            ]
            self.trajectory[-1]["order_events"] = events
        return result

    def _add_due_orders(self):
        elapsed = self._order_elapsed_sec()
        # 서버가 잠깐 멈췄다 돌아와도 놓친 주문을 몰아서 보충하도록 while 사용.
        while self._allowed_orders and elapsed >= self._next_order_at_sec:
            recipe = self._draw_recipe()
            self._open_orders.append(recipe)
            self._order_events.append(
                {"type": "added", "recipe": _recipe_label(recipe),
                 "elapsed": round(self._next_order_at_sec, 3)}
            )
            self._next_order_at_sec += ORDER_ARRIVAL_INTERVAL_SEC

    # ── 화면 ────────────────────────────────────────────────────────
    def get_state(self):
        state_dict = super(OrderQueueMixin, self).get_state()
        inner = state_dict.get("state")
        if isinstance(inner, dict):
            inner["all_orders"] = [r.to_dict() for r in list(self._open_orders)]
        return state_dict
