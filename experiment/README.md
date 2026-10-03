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
| `server_ext/ping_game.py` : `PingMixin` | 2 | **구현 및 end-to-end 테스트 통과** | `server_ext/test_ping_logic.py` 10개 테스트 통과: 기존 이동 액션 회귀 없음, PING_* 라우팅, 알 수 없는 핑 무시, trajectory에 `pings` 필드 기록, 실제 `PingReactiveBot.ping_queue`로 전달, `note_step()` 매 tick 호출, **화면 표시용 `get_state()["pings"]`가 즉시 나타났다가 `PING_DISPLAY_TICKS` 뒤 정확히 사라짐**, **trajectory pickle → `compute_metrics.py`까지 end-to-end 연결(핑 2건 → `num_pings=2`, `comm_efficiency=10.0` 정상 계산)** |
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

## 화면 표시 (완료) — 캐릭터 머리 위 텍스트/이모지 말풍선

핑을 보내면 버튼만 눌리고 끝나는 게 아니라, **캐릭터 머리 위에 말풍선이 뜬다**
(별도 이미지 에셋 없이 텍스트+이모지만 사용, 예: "🙋 도와줘"). 구현 위치:

- `ping_game.py`: `_enqueue_ping()`이 핑을 받으면 `_show_ping_on_screen()`으로
  `self._visible_pings[player_idx]`에 기록하고, `get_state()`를 오버라이드해서
  거기에 `"pings": {player_idx(문자열): ping_type}`을 얹는다. 이 `get_state()`
  결과가 `state_pong` 소켓 이벤트로 그대로 클라이언트에 나간다(서버 코드
  추가 수정 없음 — `app.py`의 `play_game` 루프가 이미 하던 걸 그대로 씀).
  `PING_DISPLAY_TICKS`(12틱 ≈ 2초, 6fps 기준) 지나면 자동으로 빠짐.
- `graphics/overcooked_graphics_v2.2.js`(기본 그래픽 버전, `docker-compose.yml`의
  `GRAPHICS` 기본값): `OvercookedScene.set_state()`가 `state.pings`를
  `this.pings`에 저장하고, `update()`에서 매 프레임 `_drawPings()`를 호출해
  해당 플레이어 캐릭터(`sprites['chefs'][pi]`) 바로 위에 Phaser 텍스트 객체를
  그리거나 갱신하거나(핑이 남아있으면) 지운다(핑이 사라지면).
  `PING_LABELS`에 4종 핑의 한국어+이모지 라벨 정의.
- trajectory 로깅(`_pending_pings` → transition의 `"pings"`)과는 완전히
  별개 경로다 — 화면 표시는 "최근 N틱", 로깅은 "핑이 발생한 사실 전부".

검증: 서버 쪽(`_visible_pings`/`get_state()` 만료 로직)은
`test_ping_logic.py`의 Test 8/9로 확인. 클라이언트 쪽은 `node --check`로
문법만 검증 — Phaser는 실제 브라우저(WebGL)가 있어야 렌더링을 확인할 수
있어서, 말풍선이 실제로 올바른 위치에 예쁘게 뜨는지는 Phase 3(아래, 실제
서버 기동 테스트)에서 브라우저로 직접 봐야 한다.

## 실제 Flask 서버 기동 검증 (완료, 2026-09-29)

`ray[rllib,tune]==2.2` + `Flask-SocketIO==4.3.0`(구버전 socket.io 클라이언트와
호환되는 버전)까지 실제로 설치해 `python app.py`로 서버를 직접 띄우고,
실제 `socketio.Client()`로 브라우저 클라이언트와 동일한 payload(join params =
`config.json`의 `predefined.experimentParams`)를 보내 게임을 처음부터 끝까지
(5초, `end_game: done`) 진행해봤다. 그 과정에서 실제 서버 환경에서만
재현되는 버그 2개를 발견·수정했다 (전부 `FakeOvercookedGame` 기반 단위
테스트로는 잡을 수 없었던 것들 — 원본 `game.py`를 실제로 타야만 재현됨):

1. **모듈 중복 임포트 버그**: `build_ping_enabled_game_class()`가 자체적으로
   `from overcooked_demo.server.game import OvercookedGame`을 하면, `app.py`가
   `import game`으로 부르는 것과 다른 모듈 인스턴스가 또 생겨서
   `game._configure(MAX_GAME_TIME, ...)`가 우리가 쓰는 인스턴스에는 반영이 안 되고
   `MAX_GAME_TIME=None`으로 남아 게임 생성이 매번
   `TypeError('<' not supported between NoneType and int)`로 실패했다.
   **수정**: `build_ping_enabled_game_class(overcooked_game_cls)`가 클래스를
   인자로 받도록 바꾸고, `app.py`가 자기가 이미 import한 `OvercookedGame`을
   그대로 넘긴다.
2. **`agent_index`가 게임 시작 시점에 `None`으로 되돌아가는 버그**: 원본
   `Agent.reset()`(`overcooked_ai_py/agents/agent.py`)은
   `self.agent_index = None`으로 되돌린다 — "트라젝토리 롤아웃마다 `actions()`가
   `set_agent_index()`를 다시 불러줄 것"을 전제한 설계다. 그런데 실제 데모
   서버(`game.py`의 `OvercookedGame.activate()`)는 게임을 시작할 때마다
   무조건 `npc_policy.reset()`을 부르고 그 이후로는 다시 `set_agent_index()`를
   불러주지 않는다 (pickle 저장 시점의 `agent_index`가 계속 유지된다고 가정).
   Rllib 에이전트(`RlLibAgent`)는 `reset()`을 완전히 새로 정의해서
   이 문제를 원천적으로 피해가지만, 우리 `RoleRestrictedBot`(`GreedyHumanModel`
   상속)은 그 함정에 그대로 걸려서 `ml_action()`의
   `state.players[self.agent_index]`가
   `TypeError: tuple indices must be integers or slices, not NoneType`로
   매 게임 시작마다(NPC 정책 스레드 안에서 조용히) 죽었다. **수정**:
   `RoleRestrictedBot.reset()`을 오버라이드해서 `agent_index`만 저장했다가
   `super().reset()` 이후 복원한다 (다른 리셋 동작은 그대로 둠). 관련해서
   `pickle_agent.py`에도 `--player-idx`를 추가해 pickle 저장 *전에*
   `set_agent_index()`를 호출해두도록 했다 (이건 최초 1회 로드 시점의
   방어 코드로 남겨둠 — 근본 수정은 `reset()` 오버라이드).

수정 후 재검증: `socketio.Client()`로 5초짜리 게임을 끝까지 실행 — 서버 로그에
에러 없음, `state_pong` 31회 수신, 핑을 보내면 `state["pings"]`에
`{'0': 'help'}`처럼 정확히 반영, `end_game: done`으로 정상 종료까지 확인.
기존 유닛/스모크 테스트(`test_ping_logic.py` 10개, `smoke_test.py` 2개) 전부
회귀 없이 통과.

**남은 건 진짜 브라우저(WebGL)로 직접 플레이해서 말풍선이 화면에 예쁘게 뜨는지
눈으로 확인하는 것뿐** — 서버·봇·로깅·화면표시 데이터 파이프라인 자체는
이제 실제 서버로 end-to-end 검증됨.

## 로컬(비 Docker) 환경에서 실행하는 법 (2026-09-30 추가)

`python app.py`로 Docker 없이 직접 실행할 때만 필요한 추가 단계:

```bash
# 1. 프로젝트 설치 (human_aware_rl이 src/ 밑에 있는 걸 파이썬이 찾게 해줌)
pip install -e .

# 2. 무거운 RL 스택(실제로 학습은 안 하지만 game.py가 import는 함) + 구버전 socket.io 스택
pip install "ray[rllib,tune]==2.2" gym
pip install Flask-SocketIO==4.3.0 python-socketio==4.6.0 python-engineio==3.13.0 \
    Werkzeug==2.0.3 Jinja2==3.1.0 click==8.0.0 itsdangerous==2.0.0 MarkupSafe==2.0.0 \
    Flask==2.1.3 eventlet==0.41.2 "setuptools==79.0.1"
# (setuptools는 최신 버전을 깔면 pkg_resources가 빠져있어 ray가 깨짐 — 꼭 이 버전으로)

# 3. Docker 빌드 때만 자동으로 복사되는 그래픽 파일을 수동으로 복사
#    (이거 안 하면 소켓/게임 로직은 다 되는데 Phaser 캔버스가 안 뜸 —
#    실제 브라우저로 처음 테스트할 때 발견)
cp graphics/overcooked_graphics_v2.2.js static/js/graphics.js   # src/overcooked_demo/server/ 안에서 실행

# 4. 서버 실행
cd src/overcooked_demo/server
PORT=5001 HOST=127.0.0.1 FLASK_ENV=production python app.py
```

`http://127.0.0.1:5001/predefined` 접속 → "시작하기" 버튼을 눌러야 게임이 시작됨
(접속 즉시 자동 시작되던 것을 안내 화면 추가로 수정, 아래 참고).

## 접속 즉시 게임이 시작되던 문제 수정 (완료, 2026-10-03)

실제 브라우저로 처음 플레이해보니, `predefined.html`에 접속하자마자
(`socket.on("connect", ...)`에서 바로 `join`을 보내서) 참가자가 준비할 틈도 없이
게임이 시작돼버렸다. `#start-screen`(안내 문구 + "시작하기" 버튼 + 게임 방법
링크)을 추가하고, 버튼을 눌러야 `join`을 보내도록 `predefined.js`를 수정했다.

## 난이도(레이아웃) 5단계 + 레이아웃 전환 버그 수정 (완료, 2026-10-03)

`config.json`의 `predefined.experimentParams`를 원본 논문/공식 데모가 쓰는
쉬움→어려움 5단계로 확장:
`cramped_room → asymmetric_advantages → coordination_ring → forced_coordination → counter_circuit`,
레이아웃당 `gameTime: 150`초 (`randomized: false`로 순서 고정, `MAX_GAME_LENGTH`도
150보다 작게 걸려있던 것을 200으로 올림). `game.py`의 `layouts` 배열은 한 세션
안에서 라운드를 순서대로 이어서 진행해주는 원본 기능을 그대로 쓴 것 — 서버 쪽
코드는 안 건드림.

다만 여기서 **진짜 버그 하나를 더 발견·수정**했다 (역시 실제 서버로 직접
재현하기 전까지는 안 보이던 것): `playerOne`(우리 봇)은 세션 전체에서 **에이전트
객체 하나만** 계속 쓰이는데, 그 봇의 의사결정(`ml_action()`)은 특정 레이아웃
지형에 맞춰 미리 계산된 `mlam`(MediumLevelActionManager)에 의존한다. 레이아웃이
바뀌어도 원본 서버는 이 mlam을 안 바꿔주므로, 2번째 레이아웃부터는 봇이 "이전"
레이아웃 지형 기준으로 판단해버린다.

1. **1차 수정**: `RoleRestrictedBot.update_for_layout(mdp)` 추가(새 레이아웃에
   맞는 mlam을 `MediumLevelActionManager.from_pickle_or_compute`로 재계산 —
   디스크 캐시를 쓰므로 같은 레이아웃이면 두 번째부터는 빠름), `PingMixin.activate()`
   에서 이 훅이 있는 정책에 새 mdp를 전달하도록 함.
2. **레이스 컨디션 발견**: 1차 수정을 실제 서버로 5레이아웃 연속 테스트해보니
   여전히 레이아웃 전환 직후 `AssertionError: Node 1 cc: [] / Node 2 cc: [0]`로
   NPC 정책 스레드가 죽었다. 원인은 순서였다 — `OvercookedGame.activate()`는
   `self.mdp`를 새로 설정하자마자 그 안에서 바로 새 `npc_policy_consumer`
   스레드를 띄우고 시작 상태를 큐에 넣는데, 당시 `update_for_layout()` 호출은
   `super().activate()` 이후였다. 그 새 스레드가 (갱신되기 전) 옛 mlam으로
   새 상태를 처리해버리는 레이스가 실제로 발생.
   **최종 수정**: `super().activate()`를 부르기 **전에** `self.layouts[-1]`
   (아직 pop 안 한, 다음에 쓸 레이아웃)을 미리 들여다보고 그 mdp를 계산해서
   정책들에 먼저 넘긴 뒤에 `super().activate()`를 호출하도록 순서를 바꿨다.
   실제 서버로 5레이아웃을 끝까지(`reset_game` 4회, 레이아웃 전환마다 1회)
   에러 없이 통과하는 것까지 확인.

`test_ping_logic.py`에 이 순서 보장을 검증하는 테스트(Test 10)와, 실제
`RoleRestrictedBot.update_for_layout()`이 레이아웃이 바뀌면 진짜로 mlam을
교체하는지 확인하는 테스트(Test 11)를 추가 — 총 12개 테스트 통과.
`asymmetric_advantages`/`coordination_ring`/`forced_coordination`/
`counter_circuit`용 봇 pickle도 미리 만들어뒀지만(`RuleBasedBot_*`),
구조상 `playerOne` 봇 하나(`RuleBasedBot_CrampedRoom`)가 `update_for_layout()`
덕분에 5개 레이아웃을 전부 커버하므로 지금 당장은 안 써도 됨 — 나중에
레이아웃마다 다른 봇 설정(예: 제외 역할을 다르게)을 쓰고 싶을 때를 위해 남겨둠.

## 다음 작업 (우선순위 순)

1. 실제 브라우저로 5개 레이아웃을 끝까지 직접 플레이해서 말풍선 표시, 난이도
   전환, 150초 타이머가 전부 의도대로 보이는지 육안 확인.
2. `_PING_RESPONSE_MAP`과 `_respond_to_help()`의 실제 반응 규칙 확정 (지도교수 상담 필요 항목).
3. "Deterministic?" 같은 원본 공식 데모의 다른 옵션들, "Replay Trajectories"
   기능 도입 여부 검토 (우선순위 낮음, 지금 당장 필수는 아님).
4. Phase 5 (실험 플로우) 착수.

## 알려진 이슈

- 원본 저장소가 `requires-python = ">=3.10,<3.11"`로 고정되어 있어 **반드시 Python 3.10**
  가상환경을 써야 합니다 (3.11에서는 `pip install -e .`가 즉시 실패함).
- `RoleRestrictedBot.ml_action()`은 `GreedyHumanModel.ml_action()`의 로직을 참고해
  카테고리별로 재작성한 것이라, 원본이 업데이트되면 이 파일도 함께 점검해야 합니다.
