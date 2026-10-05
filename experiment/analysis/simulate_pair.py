"""
봇 실효성 시뮬레이션 (2026-10-05).

"봇이 레이아웃 진행을 방해하는가?"(forced_coordination 등)를 사람 없이 숫자로
확인하기 위한 도구. 사람 역할은 같은 플래너 기반 에이전트(역할 제한 없음, 매 틱
행동 = 사람의 최대 속도)로 대신하고, 봇은 실험용 PingReactiveBot(역할 제한 +
속도 1/BOT_SPEED_DIVISOR)을 그대로 쓴다. 핑은 쓰지 않는다.

비교 기준 3가지:
  A. 사람대용 + 사람대용 (두 사람 모두 최대 속도: 인간-인간 조건의 이론적 상한 근처)
  B. 사람대용 + 실험 봇   (AI 학습 집단의 실제 조건)
  C. 사람대용 + 가만히 있는 파트너 (파트너 기여 0의 하한)
(forced_coordination의 사람 대역은 그 레이아웃 전용 규칙 기반 PotSideScriptedHuman.)
주의: 범용 대역(FullSpeedPlanner)끼리는 좁은 주방에서 서로 길을 막거나 가정
위반으로 멈추는 경우가 있어 "사람+사람대용" 열은 참고용일 뿐이다. 이 도구의 주
용도는 "봇이 게임 진행을 막지 않는가(에러/정지/0점)"를 확인하는 것이다.

실행: PYTHONPATH=. python experiment/analysis/simulate_pair.py [layout ...]
주의: 점수는 주문 목록 규칙(order_queue)을 적용하지 않은 "양파 3개 수프 = 20점" 단순
계산이라, 실제 게임보다 약간 높게 나올 수 있다(주문이 안 들어온 시간대의 서빙이
0점이 되는 효과 제외). 레이아웃별 상대 비교용이다.
"""
import sys
from collections import defaultdict
from overcooked_ai_py.mdp.actions import Action
from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld
from overcooked_ai_py.agents.agent import GreedyHumanModel
from overcooked_ai_py.planning.planners import MediumLevelActionManager
from experiment.agents.role_restricted_bot import (
    PingReactiveBot, RoleRestrictedBot, build_mlam_params,
)

LAYOUTS = ["cramped_room", "asymmetric_advantages", "coordination_ring",
           "forced_coordination", "counter_circuit"]
STEPS = 600  # 60초 * 10틱/초


class FullSpeedPlanner(RoleRestrictedBot):
    """사람 대용: 역할 제한 없음, 속도 제한 없음."""

    def action(self, state):
        self._idle_wait = False
        result = GreedyHumanModel.action(self, state)
        if self._idle_wait:
            return Action.STAY, {}
        return result


class PotSideScriptedHuman(RoleRestrictedBot):
    """forced_coordination의 '냄비·서빙 쪽 사람'을 흉내내는 간단한 규칙 기반 대역.
    (범용 대역 FullSpeedPlanner는 이 레이아웃에서 가정 위반으로 멈춘다.)
    규칙: 수프 들면 서빙 / 접시 들면 익는 냄비에서 수프 뜨기 / 양파 들면 냄비에 넣기 /
    빈손이면 가득 찬 냄비 조리 시작 → 접시(공유 카운터) → 양파(공유 카운터) 순."""

    def action(self, state):
        am = self.mlam
        mdp = am.mdp
        p = state.players[self.agent_index]
        pots = mdp.get_pot_states(state)
        co = mdp.get_counter_objects_dict(state, list(mdp.terrain_pos_dict["X"]))
        goals = []
        if p.has_object():
            n = p.get_object().name
            if n == "soup":
                goals = am.deliver_soup_actions()
            elif n == "dish" and (pots["ready"] or pots["cooking"]):
                goals = am.pickup_soup_with_dish_actions(pots, only_nearly_ready=False)
            elif n == "onion":
                goals = am.put_onion_in_pot_actions(self._compatible_pots(state, pots, "onion"))
            elif n == "tomato":
                goals = am.put_tomato_in_pot_actions(self._compatible_pots(state, pots, "tomato"))
        else:
            ready_pots = self._pots_ready_to_cook(state, pots)
            if ready_pots:
                goals = am.start_cooking_actions(ready_pots)
            elif (pots["ready"] or pots["cooking"]) and co["dish"]:
                goals = am.pickup_dish_actions(co)
            else:
                need = self._needed_ingredient(state, self._target_order(state), pots)
                if need == "tomato" and co["tomato"]:
                    goals = am.pickup_tomato_actions(co)
                elif need == "onion" and co["onion"]:
                    goals = am.pickup_onion_actions(co)
        goals = [
            g for g in goals
            if am.motion_planner.is_valid_motion_start_goal_pair(p.pos_and_or, g)
        ]
        if not goals:
            return Action.STAY, {}
        _, a, _ = self.choose_motion_goal(state.players_pos_and_or[self.agent_index], goals)
        return a, {}


class IdlePartner:
    def action(self, state):
        return Action.STAY, {}

    def set_agent_index(self, i):
        self.agent_index = i

    def reset(self):
        pass


def make_mlam(mdp):
    return MediumLevelActionManager.from_pickle_or_compute(mdp, build_mlam_params(mdp))


def run(layout, partner_kind, steps=STEPS):
    mdp = OvercookedGridworld.from_layout_name(layout)
    mlam = make_mlam(mdp)
    human_cls = PotSideScriptedHuman if layout.startswith("forced_coordination") else FullSpeedPlanner
    human = human_cls(mlam, excluded_roles=[])
    human.set_agent_index(0)
    if partner_kind == "human_proxy":
        partner = FullSpeedPlanner(mlam, excluded_roles=[])
    elif partner_kind == "bot":
        partner = PingReactiveBot(mlam, excluded_roles=["deliver"])
    else:
        partner = IdlePartner()
    partner.set_agent_index(1)
    state = mdp.get_standard_start_state()
    score = 0
    deliveries = []
    for t in range(steps):
        a0, _ = human.action(state)
        a1, _ = partner.action(state)
        if hasattr(partner, "note_step"):
            partner.note_step()
        state, info = mdp.get_state_transition(state, (a0, a1))
        r = sum(info["sparse_reward_by_agent"])
        if r:
            score += r
            deliveries.append(t)
    return score, deliveries


if __name__ == "__main__":
    layouts = sys.argv[1:] or LAYOUTS
    print(f"{'layout':24s} {'사람+사람대용':>14s} {'사람+봇':>10s} {'사람+가만히':>12s}   (점수, {STEPS}스텝)")
    for lay in layouts:
        row = []
        for kind in ("human_proxy", "bot", "idle"):
            try:
                row.append(run(lay, kind)[0])
            except Exception as e:  # 시뮬레이션 자체의 한계(플래너 가정 위반 등)도 결과로 보여줌
                row.append(f"ERR:{type(e).__name__}")
        print(f"{lay:24s} {str(row[0]):>14s} {str(row[1]):>10s} {str(row[2]):>12s}")
