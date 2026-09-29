"""
Phase 4 스텁: overcooked_demo 서버가 저장한 trajectory pickle을
분석용 CSV(결과·과정·소통 지표)로 변환한다.

입력: server.get_data()가 저장하는 {"uid": str, "trajectory": [transition, ...]}
      각 transition은 game.py의 apply_actions()가 만드는 dict.
      (score, time_elapsed, state, joint_action, player_0_id, player_1_id, ...
       + Phase 2에서 추가하는 "pings": [{"player_id","ping_type","timestamp"}])

출력: participant_id 기준의 지표 CSV (결과 지표, 과정 지표, 소통 지표)

TODO(파일럿 전 확정 필요):
- 유휴(idle) 판정 기준: 방향만 바뀐 스텝을 유휴로 볼지 여부 (현재: 제외)
- 기능적 지연 계산의 "인계" 정의 (counter에 놓기 vs 직접 전달)
- 핑-행동 일치 판정 윈도의 초 단위 길이
"""
import argparse
import pickle
from pathlib import Path

import pandas as pd

# 핑 타입 -> 그 핑 이후 "일치하는" 것으로 볼 행동 이벤트 이름
# TODO: Phase 2/3에서 확정된 핑 종류로 갱신
PING_MATCH_WINDOW_STEPS = 20  # 핑 이후 몇 스텝 내 행동을 "일치"로 볼지


def load_trajectory(pickle_path: str) -> list:
    with open(pickle_path, "rb") as f:
        data = pickle.load(f)
    return data["trajectory"]


def compute_score_metric(trajectory: list) -> dict:
    """결과 지표: 최종 누적 점수."""
    if not trajectory:
        return {"final_score": 0}
    return {"final_score": trajectory[-1]["score"]}


def compute_process_metrics(trajectory: list) -> dict:
    """
    과정 지표: 유휴 시간 비율, 기능적 지연(평균 스텝).
    trajectory 각 원소의 "state"(json 문자열)를 파싱해 위치 변화를 추적한다.
    """
    import json

    idle_steps = {0: 0, 1: 0}
    total_steps = len(trajectory)

    prev_pos = {0: None, 1: None}
    for t in trajectory:
        state = json.loads(t["state"])
        players = state.get("players", [])
        for idx, p in enumerate(players):
            pos = tuple(p.get("position", []))
            if prev_pos[idx] is not None and pos == prev_pos[idx]:
                idle_steps[idx] += 1
            prev_pos[idx] = pos

    idle_ratio = {
        f"idle_ratio_p{idx}": (idle_steps[idx] / total_steps if total_steps else 0)
        for idx in (0, 1)
    }
    # 기능적 지연: TODO — 인계 이벤트를 state diff에서 탐지하는 로직 구현 필요.
    # 지금은 자리만 잡아둔다.
    idle_ratio["functional_delay_mean"] = None
    return idle_ratio


def compute_communication_metrics(trajectory: list) -> dict:
    """소통 지표: 핑-행동 일치율, 소통 효율(점수/핑 수)."""
    all_pings = []
    for t in trajectory:
        all_pings.extend(t.get("pings", []))

    final_score = trajectory[-1]["score"] if trajectory else 0
    num_pings = len(all_pings)
    comm_efficiency = (final_score / num_pings) if num_pings else None

    # 핑-행동 일치는 ping_type별 반응 규칙(_PING_RESPONSE_MAP, Phase 3)과
    # 대조해야 하므로 여기서는 개수만 집계하고 세부 일치 로직은 TODO.
    return {
        "num_pings": num_pings,
        "comm_efficiency": comm_efficiency,
        "ping_action_match_rate": None,  # TODO
    }


def compute_all_metrics(pickle_path: str, participant_id: str, condition: str) -> dict:
    trajectory = load_trajectory(pickle_path)
    row = {"participant_id": participant_id, "condition": condition}
    row.update(compute_score_metric(trajectory))
    row.update(compute_process_metrics(trajectory))
    row.update(compute_communication_metrics(trajectory))
    return row


def main():
    parser = argparse.ArgumentParser(
        description="trajectory pickle들을 모아 분석용 CSV로 변환"
    )
    parser.add_argument("data_dir", help="pickle 파일들이 있는 디렉토리")
    parser.add_argument("out_csv", help="출력 CSV 경로")
    args = parser.parse_args()

    rows = []
    for pkl_path in Path(args.data_dir).glob("*.pkl"):
        # 파일명 규칙: {participant_id}_{condition}.pkl 가정 (Phase 5에서 확정)
        stem = pkl_path.stem
        parts = stem.split("_")
        participant_id = parts[0] if parts else stem
        condition = parts[1] if len(parts) > 1 else "unknown"
        rows.append(compute_all_metrics(str(pkl_path), participant_id, condition))

    df = pd.DataFrame(rows)
    df.to_csv(args.out_csv, index=False)
    print(f"{len(rows)}건을 {args.out_csv}에 저장했습니다.")


if __name__ == "__main__":
    main()
