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

중요(실제 서버 기동 테스트로 발견한 버그, 반드시 지켜야 함):
    game.py의 get_policy(npc_id, idx)는 Rllib 에이전트에는 agent_index=idx를
    넘겨주지만, 우리처럼 일반 pickle로 로드되는 에이전트에는 agent_index를
    전혀 설정해주지 않는다 (pickle.load()된 객체를 그대로 쓸 뿐). Agent 기본
    클래스의 agent_index는 생성 시 None이라, 설정 안 하면 실제 게임에서
    ml_action() 내부의 `state.players[self.agent_index]`가
    "TypeError: tuple indices must be integers or slices, not NoneType"로
    죽는다 — NPC 정책 스레드(npc_policy_consumer) 안에서 조용히 죽기 때문에
    브라우저에서는 그냥 봇이 멈춰있는 것처럼만 보인다. 그래서 이 스크립트가
    agent.set_agent_index(player_idx)를 pickle 저장 *전에* 반드시 호출한다.
    --player-idx는 config.json에서 이 에이전트를 playerZero로 쓸지(0)
    playerOne으로 쓸지(1)와 정확히 일치해야 한다 (기본값 1 = playerOne).

사용법:
    python -m experiment.server_ext.pickle_agent \
        --layout cramped_room \
        --excluded-roles deliver \
        --name RuleBasedBot_CrampedRoom \
        --player-idx 1 \
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


def build_and_save(layout, excluded_roles, name, reactive, agent_dir, player_idx):
    from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld
    from overcooked_ai_py.planning.planners import MediumLevelActionManager
    from experiment.agents.role_restricted_bot import (
        RoleRestrictedBot,
        PingReactiveBot,
        build_mlam_params,
    )

    mdp = OvercookedGridworld.from_layout_name(layout)
    # NO_COUNTERS_PARAMS를 그대로 쓰지 않는 이유는 role_restricted_bot.py의
    # build_mlam_params() 설명 참고 — counter_drop을 비워두면 봇이 "deliver"가
    # 제외된 채 완성된 수프를 들고 내려놓을 곳이 없어 멈춰버리는 버그가 있었다.
    mlam = MediumLevelActionManager(mdp, build_mlam_params(mdp))

    if reactive:
        agent = PingReactiveBot(
            mlam, excluded_roles=excluded_roles, ping_queue=deque()
        )
    else:
        agent = RoleRestrictedBot(mlam, excluded_roles=excluded_roles)

    # game.py의 get_policy()는 pickle로 로드되는 에이전트에는 agent_index를
    # 설정해주지 않는다 (Rllib 경로만 그렇게 함) — 여기서 미리 박아둬야 한다.
    # config.json에서 이 이름을 playerZero로 쓰면 0, playerOne으로 쓰면 1.
    agent.set_agent_index(player_idx)

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
    assert reloaded.agent_index == player_idx, (
        f"agent_index가 {reloaded.agent_index}로 저장됨 (기대값 {player_idx})"
    )

    print(f"저장 완료: {out_path}")
    print(f"  layout={layout}, excluded_roles={excluded_roles}, "
          f"class={type(agent).__name__}, agent_index={player_idx}")
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
    parser.add_argument(
        "--player-idx", type=int, default=1, choices=[0, 1],
        help="이 에이전트가 config.json에서 맡을 자리. playerZero=0, "
             "playerOne=1 (기본값 1 — 지금 config.json 설정과 일치).",
    )
    args = parser.parse_args()

    build_and_save(
        layout=args.layout,
        excluded_roles=args.excluded_roles,
        name=args.name,
        reactive=args.reactive,
        agent_dir=args.agent_dir,
        player_idx=args.player_idx,
    )


if __name__ == "__main__":
    main()
