"""
Phase 2 마무리: RoleRestrictedBot / PingReactiveBot을 overcooked_demo 서버가
로드할 수 있는 형태(agent.pickle)로 저장한다.

서버의 game.py: get_policy(npc_id)는 npc_id가 "rllib"로 시작하지 않으면
    AGENT_DIR/<npc_id>/agent.pickle
을 그냥 pickle.load()해서 그 객체를 정책으로 쓴다 (Rllib 전용 로딩 경로를 타지
않음). 우리 봇은 overcooked_ai_py의 GreedyHumanModel만 상속하고 game.py나
human_aware_rl.rllib에 의존하지 않으므로, 이 무거운 서버 의존성 없이 이 경량
venv에서 바로 pickle을 만들 수 있다.

주의(README의 "Layout Compatibility"와 동일): mlam(MediumLevelActionManager)은
레이아웃마다 다르므로, 실제 파일럿에서 쓸 레이아웃 각각에 대해 별도로 만들어야
한다. 이름에 레이아웃을 포함시켜(RuleBasedBot_CrampedRoom 등) 실수로 다른
레이아웃에 쓰는 것을 방지한다.

사용법:
    python -m experiment.server_ext.pickle_agent \
        --layout cramped_room \
        --excluded-roles deliver \
        --name RuleBasedBot_CrampedRoom \
        --reactive   # PingReactiveBot으로 저장 (기본: RoleRestrictedBot)
"""
import argparse
import pickle
import sys
from collections import deque
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

DEFAULT_AGENT_DIR = (
    SRC_DIR / "overcooked_demo" / "server" / "static" / "assets" / "agents"
)


def build_and_save(layout, excluded_roles, name, reactive, agent_dir):
    from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld
    from overcooked_ai_py.planning.planners import (
        MediumLevelActionManager,
        NO_COUNTERS_PARAMS,
    )
    from experiment.agents.role_restricted_bot import (
        RoleRestrictedBot,
        PingReactiveBot,
    )

    mdp = OvercookedGridworld.from_layout_name(layout)
    mlam = MediumLevelActionManager(mdp, NO_COUNTERS_PARAMS)

    if reactive:
        agent = PingReactiveBot(
            mlam, excluded_roles=excluded_roles, ping_queue=deque()
        )
    else:
        agent = RoleRestrictedBot(mlam, excluded_roles=excluded_roles)

    out_dir = Path(agent_dir) / name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "agent.pickle"
    with open(out_path, "wb") as f:
        pickle.dump(agent, f)

    # 저장 직후 다시 불러와서 실제로 복원되는지 검증 (get_policy()가 하는 것과 동일)
    with open(out_path, "rb") as f:
        reloaded = pickle.load(f)
    assert isinstance(reloaded, RoleRestrictedBot)
    assert reloaded.excluded_roles == set(excluded_roles)

    print(f"저장 완료: {out_path}")
    print(f"  layout={layout}, excluded_roles={excluded_roles}, "
          f"class={type(agent).__name__}")
    print(f"  재로딩 검증 통과 (get_policy()와 동일한 방식으로 확인)")
    return out_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", required=True, help="예: cramped_room")
    parser.add_argument(
        "--excluded-roles", nargs="*", default=[],
        help="예: --excluded-roles deliver pickup_onion",
    )
    parser.add_argument(
        "--name", required=True,
        help="agents 폴더 하위 이름 (드롭다운에 표시될 이름). "
             "레이아웃을 이름에 포함시킬 것을 권장 (예: RuleBasedBot_CrampedRoom)",
    )
    parser.add_argument(
        "--reactive", action="store_true",
        help="PingReactiveBot(Phase 3)으로 저장. 생략 시 RoleRestrictedBot(Phase 1)만.",
    )
    parser.add_argument(
        "--agent-dir", default=str(DEFAULT_AGENT_DIR),
        help="기본값: 클론된 저장소의 overcooked_demo 서버 agents 폴더",
    )
    args = parser.parse_args()

    build_and_save(
        layout=args.layout,
        excluded_roles=args.excluded_roles,
        name=args.name,
        reactive=args.reactive,
        agent_dir=args.agent_dir,
    )


if __name__ == "__main__":
    main()
