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

## Flask 서버 연결 (완료)

`src/overcooked_demo/server/app.py`의 `GAME_NAME_TO_CLS["overcooked"]`가
`OvercookedGame` 대신 `ping_game.build_ping_enabled_game_class()`가 반환하는
클래스를 쓰도록 바뀌었다 (2줄 import + 1줄 교체가 개입의 전부, git diff로 확인
가능). `config.json`을 안 건드려도 기본 게임 생성 경로가 자동으로 핑 채널을
포함하게 된다.

## NPC 봇 배포 (완료)

`experiment/server_ext/pickle_agent.py`로 `RoleRestrictedBot`/`PingReactiveBot`을
서버가 읽는 `agent.pickle` 포맷으로 저장한다:

```bash
python -m experiment.server_ext.pickle_agent \
    --layout cramped_room --excluded-roles deliver \
    --name RuleBasedBot_CrampedRoom --reactive
```

`cramped_room` 레이아웃으로 실제 생성·검증 완료 — **별도의 새 파이썬 프로세스에서
pickle을 다시 불러와(서버의 `get_policy()`와 동일한 방식) 61스텝 무오류 실행**까지
확인했다. 생성된 파일은
`src/overcooked_demo/server/static/assets/agents/RuleBasedBot_CrampedRoom/agent.pickle`.

## config.json 기본 실험 설정 (완료)

`predefined.experimentParams`를 사람 vs 규칙기반봇으로 바꿨다:
`playerOne`을 `"human"` → `"RuleBasedBot_CrampedRoom"`. 동시에
`layouts`를 `["counter_circuit", "cramped_room"]` → `["cramped_room"]`으로
줄였다 — mlam(따라서 우리 봇)은 레이아웃별로 다시 계산해야 하는데
지금은 `cramped_room`용만 만들어놨기 때문에, 원본 README의 "Layout
Compatibility" 경고(다른 레이아웃에 쓰면 봇이 그냥 조용히 멈춰버림)에 걸리지
않도록 실험 레이아웃을 봇이 있는 것 하나로 제한했다.

## 클라이언트 핑 버튼 (완료)

`static/templates/predefined.html`에 버튼 4개(`#ping-help/look/mine/ok`)를
추가하고, `static/js/predefined.js`에 클릭 시 `socket.emit('action', {action:
'PING_' + 타입})`을 보내는 핸들러를 달았다. 새 UI 요소가 아니라 **기존
`enable_key_listener()`/`disable_key_listener()`와 나란히** `enable_ping_
controls()`/`disable_ping_controls()`를 호출하도록 넣어서, 게임 시작/종료/재시작
시점에 버튼이 자동으로 보이고 숨겨진다 (게임 중이 아닐 때 핑을 보낼 수 없게).
`node --check`로 JS 문법, 태그 존재로 HTML 검증 완료. `index.js`/`tutorial.js`
(자유 플레이/튜토리얼 페이지)는 이번 파일럿 경로가 아니라서 안 건드림.

이걸로 Phase 2(핑 채널)가 서버·봇·클라이언트·로깅·지표계산까지 전부 연결됐다.
남은 건 실제 무거운 의존성 환경에서 브라우저로 직접 플레이해보는 것뿐.

## 다음 작업 (우선순위 순)

1. 실제 Flask 서버 기동 검증 — ray/human_aware_rl 구버전 스택이 설치된 환경에서
   `python app.py`로 띄워 브라우저로 직접 플레이해보는 것. 지금까지의 검증은
   전부 그 무거운 스택 없이 로직만 확인한 것이므로, 이 마지막 단계는 아직 안 됨.
2. `_PING_RESPONSE_MAP`과 `_respond_to_help()`의 실제 반응 규칙 확정 (지도교수 상담 필요 항목).
3. 파일럿에서 실제 쓸 레이아웃을 추가로 정하면, 그 레이아웃마다
   `pickle_agent.py`를 한 번씩 더 돌려서 에이전트를 만들고
   `config.json`의 `layouts`에도 추가.
4. Phase 5 (실험 플로우) 착수.

## 알려진 이슈

- 원본 저장소가 `requires-python = ">=3.10,<3.11"`로 고정되어 있어 **반드시 Python 3.10**
  가상환경을 써야 합니다 (3.11에서는 `pip install -e .`가 즉시 실패함).
- `RoleRestrictedBot.ml_action()`은 `GreedyHumanModel.ml_action()`의 로직을 참고해
  카테고리별로 재작성한 것이라, 원본이 업데이트되면 이 파일도 함께 점검해야 합니다.
