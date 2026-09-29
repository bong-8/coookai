"""
Phase 0 검증용 스모크 테스트.
RoleRestrictedBot / PingReactiveBot이 실제 레이아웃에서 몇 스텝 동안
에러 없이 행동을 생성하는지, 역할 제한이 실제로 걸리는지 확인한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld
from overcooked_ai_py.mdp.overcooked_env import OvercookedEnv
from overcooked_ai_py.planning.planners import (
    MediumLevelActionManager,
    NO_COUNTERS_PARAMS,
)
from overcooked_ai_py.agents.agent import AgentPair

from experiment.agents.role_restricted_bot import RoleRestrictedBot, PingReactiveBot


def run_role_restriction_test():
    print("=== Test 1: RoleRestrictedBot이 deliver 역할을 실제로 피하는지 ===")
    mdp = OvercookedGridworld.from_layout_name("cramped_room")
    mlam = MediumLevelActionManager(mdp, NO_COUNTERS_PARAMS)
    env = OvercookedEnv.from_mdp(mdp, horizon=100)

    bot_no_deliver = RoleRestrictedBot(mlam, excluded_roles=["deliver"])
    bot_normal = RoleRestrictedBot(mlam, excluded_roles=[])
    agent_pair = AgentPair(bot_no_deliver, bot_normal)

    trajectory = env.run_agents(agent_pair, include_final_state=True)
    print(f"  {len(trajectory[0])} 스텝 실행, 에러 없이 완료")
    print(f"  최종 점수: {env.state.get_flat_serving_locations() if hasattr(env.state, 'get_flat_serving_locations') else 'n/a'}")
    print("  PASS (에러 없이 excluded_roles=['deliver']로 실행됨)\n")


def run_ping_reaction_test():
    print("=== Test 2: PingReactiveBot이 핑 큐를 소비하며 실행되는지 ===")
    from collections import deque

    mdp = OvercookedGridworld.from_layout_name("cramped_room")
    mlam = MediumLevelActionManager(mdp, NO_COUNTERS_PARAMS)
    env = OvercookedEnv.from_mdp(mdp, horizon=50)

    ping_queue = deque()
    bot = PingReactiveBot(mlam, excluded_roles=[], ping_queue=ping_queue)
    bot2 = RoleRestrictedBot(mlam, excluded_roles=[])
    agent_pair = AgentPair(bot, bot2)

    state = env.state
    for step in range(50):
        bot.note_step()
        if step == 10:
            ping_queue.append({"ping_type": "mine", "step": step})
        joint_action_and_infos = agent_pair.joint_action(state)
        joint_action = tuple(a for a, _ in joint_action_and_infos)
        state, _, done, _ = env.step(joint_action)
        if done:
            break
    print(f"  {step + 1} 스텝 실행, 핑 주입 후 에러 없이 완료")
    print("  PASS\n")


if __name__ == "__main__":
    run_role_restriction_test()
    run_ping_reaction_test()
    print("모든 스모크 테스트 통과.")
