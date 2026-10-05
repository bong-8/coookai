"""혼합 주문(*_mixed 레이아웃) 시뮬레이션 (2026-10-05).

실제 게임과 같은 주문 큐 규칙(처음 1개, 10초마다 +1, 배달 시 삭제, 목록에 없으면 0점,
레이아웃 이름으로 시드한 '섞은 가방' 순서)을 틱 기준으로 돌리면서, 봇이 여러 종류의
주문(양파/토마토 조합)을 막힘 없이 처리하는지 본다.
  python experiment/analysis/simulate_orders.py [layout ...]
열: 사람대용+봇 / 봇 혼자(사람은 가만히) / 사람대용+사람대용.  각 항목 = (점수, 배달한 메뉴들)
※ 사람 대용은 단순 플래너라 현실의 사람보다 못하다. "봇이 주문을 처리하는가"의 확인용.
"""
import sys
from experiment.analysis.simulate_pair import (
    FullSpeedPlanner, PotSideScriptedHuman, IdlePartner, make_mlam,
)
from experiment.agents.role_restricted_bot import PingReactiveBot
from experiment.server_ext.order_queue import (
    OrderQueueMixin, ORDER_ARRIVAL_INTERVAL_SEC, _recipe_label,
)
from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld

MIXED = ["cramped_room_mixed", "asymmetric_advantages_mixed",
         "coordination_ring_mixed", "forced_coordination_mixed", "counter_circuit"]
STEPS = 600


class _Orders(OrderQueueMixin):
    def __init__(self, mdp, layout):
        self.mdp = mdp
        self.state = mdp.get_standard_start_state()
        self.curr_layout = layout
        self.tick = 0
        self.npc_policies = {}
        self._orders_init()
        self._reset_orders()
        self._install_order_aware_delivery()

    def _order_elapsed_sec(self):
        return self.tick / 10.0


def run(layout, kind, steps=STEPS):
    mdp = OvercookedGridworld.from_layout_name(layout)
    mlam = make_mlam(mdp)
    orders = _Orders(mdp, layout)
    human_cls = PotSideScriptedHuman if layout.startswith("forced_coordination") else FullSpeedPlanner
    if kind == "bot_alone":
        human = IdlePartner()
    else:
        human = human_cls(mlam, excluded_roles=[])
    human.set_agent_index(0)
    partner = (PingReactiveBot(mlam, excluded_roles=["deliver"]) if kind != "proxy_pair"
               else FullSpeedPlanner(mlam, excluded_roles=[]))
    partner.set_agent_index(1)
    state = mdp.get_standard_start_state()
    orders.state = state
    score, delivered = 0, []
    for t in range(steps):
        orders.tick = t
        orders._add_due_orders()
        for ag in (human, partner):
            ag.open_orders = list(orders._open_orders)
        a0, _ = human.action(state)
        a1, _ = partner.action(state)
        if hasattr(partner, "note_step"):
            partner.note_step()
        state, info = mdp.get_state_transition(state, (a0, a1))
        orders.state = state
        r = sum(info["sparse_reward_by_agent"])
        score += r
    for e in orders._order_events:
        if e["type"] == "delivered":
            delivered.append("".join(i[0] for i in e["recipe"]))
        elif e["type"] == "rejected":
            delivered.append("x:" + "".join(i[0] for i in e["recipe"]))
    return score, delivered


if __name__ == "__main__":
    layouts = sys.argv[1:] or MIXED
    for lay in layouts:
        print(f"== {lay}")
        for kind in ("bot", "bot_alone", "proxy_pair"):
            try:
                sc, d = run(lay, kind)
                print(f"  {kind:11s} {sc:4d}점 {d}")
            except Exception as e:
                import traceback
                print(f"  {kind:11s} ERR {type(e).__name__}: {e}")
