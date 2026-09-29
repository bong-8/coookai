# Overcooked-AI 실험 프로그램 — 개발 스캐폴드

이 폴더는 IRB 제출용 파이프라인 문서(Phase 0~8)의 Phase 0~1, Phase 3 일부를
실제로 구현·검증한 시작점입니다. 원본 `overcooked_ai` 레포는 건드리지 않고,
이 `experiment/` 폴더만 새로 추가하는 방식으로 작업했습니다.

## 사용법

```bash
# 원본 레포 클론
git clone https://github.com/HumanCompatibleAI/overcooked_ai.git
cd overcooked_ai

# 이 experiment/ 폴더를 그대로 복사해 넣기

# Python 3.10 가상환경 (원본이 3.10 전용으로 pin 되어 있음)
python3.10 -m venv .venv
source .venv/bin/activate
pip install -e .

# 스모크 테스트
python -m experiment.smoke_test
```

## 현재 상태 (2026-09-27 기준)

| 파일 | Phase | 상태 | 검증 |
| --- | --- | --- | --- |
| `agents/role_restricted_bot.py` : `RoleRestrictedBot` | 1 | 동작 확인 | `smoke_test.py` Test 1 통과 (excluded_roles=["deliver"]로 실제 레이아웃에서 101스텝 무오류 실행) |
| `server_ext/ping_game.py` : `PingMixin` | 2 | **구현 및 end-to-end 테스트 통과** | `server_ext/test_ping_logic.py` 8개 테스트 통과: 기존 이동 액션 회귀 없음, PING_* 라우팅, 알 수 없는 핑 무시, trajectory에 `pings` 필드 기록, 실제 `PingReactiveBot.ping_queue`로 전달, `note_step()` 매 tick 호출, **trajectory pickle → `compute_metrics.py`까지 end-to-end 연결(핑 2건 → `num_pings=2`, `comm_efficiency=10.0` 정상 계산)** |
| `agents/role_restricted_bot.py` : `PingReactiveBot` | 3 | 구현, 실제 핑으로 반응 동작 확인 | `smoke_test.py` Test 2(합성 핑) + `test_ping_logic.py` Test 5/6(실제 PingMixin이 넣어준 핑) 모두 통과. 단 `_PING_RESPONSE_MAP`, `_respond_to_help`의 실제 규칙 값은 TODO |
| `analysis/compute_metrics.py` | 4 | **end-to-end 검증 완료** | `test_ping_logic.py` Test 7에서 실제 pickle 포맷으로 저장 후 `compute_all_metrics()` 실행 확인. 유휴시간·핑 개수·소통 효율 계산 정상. 기능적 지연·핑-행동 일치는 TODO |
| 실험 플로우 (`OvercookedTutorial` 확장) | 5 | 미착수 | |

Phase 2는 원본 `overcooked_demo/server/game.py`(Flask/Socket.IO 서버 안,
`ray`/`human_aware_rl.rllib`의 구버전 `gym`+`ray.rllib.PPOTrainer`에 의존)를
직접 import하지 않고, 그 계약만 흉내 내는 `FakeOvercookedGame`으로 로직을
검증했다 (이유는 `test_ping_logic.py` 상단 주석 참고 — 우리가 이미 폐기
대상으로 확인한 DRL 학습 스택과 같은 무거운 의존성이라 경량 개발 venv에는
설치하지 않음). `PingMixin`은 원본 클래스에 의존하지 않는 순수 로직이라,
같은 클래스를 실제 서버(`build_ping_enabled_game_class()`)에도 그대로 쓴다.

## 다음 작업 (우선순위 순)

1. **Flask 서버에 실제로 연결**(남은 배선 작업) — `overcooked_demo/server/app.py`가
   `OvercookedGame`을 만드는 지점에서 `ping_game.build_ping_enabled_game_class()`가
   반환하는 클래스를 대신 쓰도록 1줄 교체. 이 작업 자체는 실제 서버 의존성
   (ray/human_aware_rl 구버전 스택)이 설치된 환경에서만 실행·검증 가능.
2. 클라이언트(JS) 쪽에서 핑 버튼 4개(`PING_HELP/LOOK/MINE/OK`)를 기존 액션
   전송 함수로 그대로 보내도록 연결 (서버 쪽은 이미 이 문자열들을 처리함).
3. `_PING_RESPONSE_MAP`과 `_respond_to_help()`의 실제 반응 규칙 확정 (지도교수 상담 필요 항목).
4. Phase 5 (실험 플로우) 착수.

## 알려진 이슈

- 원본 저장소가 `requires-python = ">=3.10,<3.11"`로 고정되어 있어 **반드시 Python 3.10**
  가상환경을 써야 합니다 (3.11에서는 `pip install -e .`가 즉시 실패함).
- `RoleRestrictedBot.ml_action()`은 `GreedyHumanModel.ml_action()`의 로직을 참고해
  카테고리별로 재작성한 것이라, 원본이 업데이트되면 이 파일도 함께 점검해야 합니다.
