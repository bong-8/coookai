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

# 3. (2026-10-04부로 더 이상 수동으로 할 필요 없음 — app.py가 기동할 때마다
#    graphics/overcooked_graphics_v2.2.js를 static/js/graphics.js로 자동으로
#    동기화한다. 아래 "그래픽 파일 자동 동기화" 섹션 참고. 예전엔 이 단계를
#    깜빡해서 "오더가 아이콘 수정했는데도 여전히 텍스트로 보인다"는 문제가
#    반복해서 발생했었다.)

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

## 실제 브라우저 1차 플레이 피드백 반영 (완료, 2026-10-03)

5단계 난이도로 실제 브라우저에서 처음 플레이해보고 나온 피드백 3가지 + 추가
요청 1가지를 반영:

1. **사람-봇 속도 차이**: 봇 계산 속도를 직접 재보니 1회 0.3ms로 병목이
   아니었다. 실제 원인으로 추정되는 건 방향키를 누르고 있을 때 브라우저/OS의
   "키 반복 지연"(처음은 즉시, 이후론 한참 있다 반복) 때문에 사람 캐릭터가
   끊기듯 움직이는 것 — 봇은 서버 틱마다 끊김 없이 움직여서 상대적으로 더
   빨라 보임. `predefined.js`의 `enable_key_listener()`를 "키 하나당 keydown
   1번 = 이동 1번"에서 "눌려있는 방향키를 서버 틱 주기(6fps)보다 살짝 빠른
   120ms 간격으로 계속 전송"하는 방식으로 바꿨다(SPACE는 반복 전송하면
   줍기/놓기가 의도치 않게 반복될 수 있어 `e.repeat`로 걸러 1회만 전송).
2. **핑에 반응이 없는 것처럼 보임**: `_respond_to_help()`가 "냄비에 재료가
   일부 들어있을 때만" 반응하게 짜여 있어서, 그 조건이 아니면(게임 막 시작/
   냄비 비어있음/꽉 참) 평소와 똑같이 행동해버려 반응이 없는 것처럼 보였다.
   4가지 핑의 반응 규칙을 명확히 재정의: **도와줘**=제한을 일시적으로 전부
   해제(배달 등 평소 피하던 일도 포함해 가장 효율적인 행동), **내가 할게**=
   배달 역할을 상대에게 양보(기존과 동일), **이거 봐**/**OK**=행동 변화 없음
   (로깅만). 다만 "도와줘"도 그 순간 "제한 때문에 못 하던 일"이 아예 없으면
   여전히 티가 안 날 수 있음 — 완전 보장은 아니라서 지도교수님과 "뭘 해야
   도움으로 느껴지는지" 확정은 여전히 TODO.
3. **오더 아이콘을 못 알아봄**: `overcooked_graphics_v2.2.js`의
   `_drawAllOrders`/`_drawBonusOrders`가 작은 수프 아이콘만 그리던 것을,
   "양파3 수프 x2"처럼 재료 구성 + 개수를 한국어 텍스트로 보여주도록 바꿨다
   (`_orderIngredientsLabel`/`_ordersToText` 추가).
4. **Player 1/2/Layout을 화면에서 고를 수 있게**: 원본 공식 데모(index.html)
   처럼 시작 화면에 드롭다운 3개를 추가했다 — 단, 이건 **연구자 테스트 편의용**
   이지 참가자가 쓰라고 만든 게 아니다. 기본값이 그대로 `config.json`의
   `predefined.experimentParams`라서, 아무것도 안 건드리고 "시작하기"만
   누르면 기존과 완전히 동일하게 동작한다(실험 통제 유지). Layout 드롭다운에서
   "기본값(난이도 전체)" 대신 특정 레이아웃 하나를 고르면 그 레이아웃 1개짜리
   단일 라운드로 빠르게 테스트해볼 수 있다. `app.py`의 `/predefined` 라우트가
   `get_agent_names()`/`LAYOUTS`를 템플릿에 추가로 넘겨주도록 했다.

실제 서버로 5레이아웃 연속 전환(라운드 전환 4회) + 에러 없음 재확인, 유닛
테스트 2개 추가(총 13개 통과: "도와줘" 핑이 실제로 제한을 전부 해제하는지,
기존 레이아웃 전환 테스트).

## 시작 화면을 실험 설계에 맞춰 재설계 (완료, 2026-10-04)

실제 브라우저 2차 플레이테스트에서 나온 피드백 세 가지에 대응.

1. **"Player 1/2 드롭다운 종류가 너무 많다"**: `static/assets/agents/`에는
   공식 공개 데모가 넣어둔 샘플 에이전트(`RandAI`, `StayAI`,
   `Rllib*BC/SP` 10종)가 본 연구의 실험 봇(`RuleBasedBot_*` 5종, 난이도별
   하나씩)과 함께 섞여 있었고, 기존 드롭다운은 이걸 전부 노출했다.
   중간 보고서 3.4절상 실험 봇은 "규칙 기반 플래너" 단 한 종류뿐이므로,
   `app.py`에 `LAYOUT_TO_EXPERIMENT_BOT` 매핑을 추가해 실험용 5종만
   내부적으로 사용하고 드롭다운에서 나머지는 아예 제거했다.
2. **"봇이 멍청한 것 같다"**: 위와 같은 원인일 가능성이 높다고 판단 —
   드롭다운에 `RandAI`(무작위 행동)·`StayAI`(정지)가 섞여 있어 테스트 중
   실수로 고를 수 있었다. 실험 봇 외의 선택지를 아예 없앴으므로 재현되면
   안 된다.
3. **"Player1/Player2 둘 다 사람이면 입력을 어떻게 받는가 / 조작키 설명이
   없다"**: `app.py`의 `on_join` 핸들러를 읽어 확인한 사실 — `playerZero`/
   `playerOne`이 둘 다 `"human"`이면 서버는 **사람 접속 2개**를 기다리며,
   각자 다른 브라우저(컴퓨터)가 같은 게임에 자동으로 합류한다(이때 먼저
   합류를 "생성"한 쪽의 파라미터만 실제로 적용되고 나중에 합류하는 쪽의
   선택은 무시됨 — `get_waiting_game()`이 파라미터를 다시 보지 않음).
   하나의 브라우저에서 P1·P2를 둘 다 고르는 방식 자체가 이 구조와 맞지
   않았으므로, 원본 공식 데모의 "Player1/Player2 임의 선택" UI를 그대로
   베끼는 대신 **독립변인(학습 단계 파트너 유형: AI 봇 vs 인간)에 맞춰
   "학습 조건" 하나만 고르는 방식**으로 교체했다:
   - `override-condition`: 기본값 / "AI 봇과 연습(AI 학습 집단)" /
     "다른 참가자와 연습(인간 학습 집단)".
   - "인간 학습 집단"을 고르면 "참가자 두 명이 각자 다른 컴퓨터에서
     접속해야 하며, 먼저 시작한 사람의 설정이 적용된다"는 경고 문구를
     화면에 표시(`#human-condition-note`).
   - 조작법 안내(방향키/스페이스바)를 더 눈에 띄게 강조.
   - `override-layout`도 실험에 쓰이지 않는 레이아웃(튜토리얼, marshmallow
     등)은 빼고 실제 5단계 난이도만 노출하도록 축소.

   변경 파일: `app.py`(`LAYOUT_TO_EXPERIMENT_BOT`,
   `get_experiment_agent_names`, `/predefined` 라우트),
   `static/templates/predefined.html`, `static/js/predefined.js`.
   Jinja 템플릿은 실제 Flask 앱 없이 `jinja2.Environment`로 직접 렌더링해
   구조(옵션 개수, id 존재 여부)를 검증함; JS는 `node --check`로 문법 검증.
   (이번 변경은 게임 로직 — `role_restricted_bot.py`/`ping_game.py` — 을
   건드리지 않아 기존 핑/레이아웃 전환 유닛테스트에는 영향이 없다.)

## 2차 플레이테스트 피드백 반영 (완료, 2026-10-04)

실제 플레이 중 발견된 세 가지 문제. 유닛테스트(Test 13 추가, 총 14개 전부
통과) + 멀티틱 시뮬레이션으로 직접 재현·검증했다.

1. **"봇이 접시를 들고 계속 움직이기만 하고 그대로 고장난다"**(사용자가
   "접시 제출은 플레이어만 하게 한 설정 때문 아니냐"고 정확히 진단한 버그).
   원인을 코드로 추적: `RoleRestrictedBot`/`PingReactiveBot`이 쓰는
   `NO_COUNTERS_PARAMS`는 `"counter_drop": []`라서(`planners.py` 정의),
   `MediumLevelActionManager.place_obj_on_counter_actions()`가 **항상** 빈
   리스트를 반환한다 — 즉 "카운터에 물건을 내려놓는다"는 행동 자체가
   플래너 레벨에서 완전히 막혀 있었다. `excluded_roles=["deliver"]`인 채로
   완성된 수프(dish+soup)를 들면: ml_action()에서 "deliver"가 제외돼
   motion_goals가 비고 → 예전 fallback `go_to_closest_feature_actions()`는
   냄비/디스펜서 위치만 알고 서빙 윈도·카운터는 전혀 모름(코드 확인) → 할
   수 있는 일이 없는 목표(가장 가까운 냄비 등)로 이동 → 도착해도 상태가 안
   바뀌니 같은 목표가 계속 나옴 → `GreedyHumanModel.action()`의
   `auto_unstuck`(제자리에 멈춘 걸 감지하면 무작위 행동을 주입하는 안전장치)
   이 계속 발동해 "접시를 든 채 꿈틀거리기만 하는" 것처럼 보였다.

   고침: (1) `role_restricted_bot.py`에 `build_mlam_params(mdp)`를 추가해
   `counter_drop`/`counter_goals`를 그 레이아웃의 전체 카운터 위치로 채운
   파라미터를 쓰도록 바꿨다(`update_for_layout()`과 `pickle_agent.py` 양쪽
   다). (2) `ml_action()`의 fallback을 "뭔가 들고 있는데 역할 제한 때문에
   더 할 수 있는 일이 없으면 `place_obj_on_counter_actions()`로 빈 카운터에
   내려놓기, 아무것도 안 들고 있을 때만 기존 `go_to_closest_feature_actions`"
   로 나눴다. 사람이 그 수프를 집어서 마저 배달하면 된다 — "일부 역할은
   의도적으로 사람의 몫으로 남긴다"는 3.4절 설계와 정확히 들어맞는 동작.
   40틱 시뮬레이션으로 "더 이상 안 멈추고 실제로 카운터에 내려놓는지"까지
   확인했다(Test 13).

   **주의(로컬에서 꼭 해야 하는 일)**: `MediumLevelActionManager`는 레이아웃별로
   계산 결과를 `src/overcooked_ai_py/data/planners/<layout>_am.pkl` 등으로
   디스크 캐시한다(이 폴더는 `.gitignore`되어 있어 델타 zip에 안 담김,
   각자 로컬에만 있음). 파라미터가 바뀌었으니 다음에 각 레이아웃을 처음
   플레이할 때 이 캐시가 자동으로 재계산되는데, 작은 레이아웃은 수 초지만
   **counter_circuit은 수 분(클라우드 환경에서 3분 이상) 걸릴 수 있다.**
   레이아웃 전환 시점에 이 재계산이 동기적으로 일어나므로(2026-10-03에
   고친 레이스 컨디션 수정과 같은 지점 — `update_for_layout()`이
   `super().activate()`보다 먼저 호출됨), 실제 참가자 세션 도중 이게
   처음 발동하면 "서버가 멈췄나?" 싶을 만큼 오래 멈춘 것처럼 보일 수 있다.
   **그래서 실제 파일럿/IRB 세션 전에, "AI 봇과 연습" 조건으로 5단계를
   한 번 전부(또는 적어도 counter_circuit 한 번) 미리 플레이해서 캐시를
   미리 데워두는 걸 강력히 권장한다.** `agent.pickle` 자체는 재생성할
   필요 없음 — 어차피 매 라운드 시작마다 `update_for_layout()`이 그 안의
   mlam을 새로 계산해서 덮어쓰므로, 저장된 pickle의 mlam은 사실상 쓰이지
   않는다(의미 있는 건 코드의 `build_mlam_params()`뿐). 다만 코드 일관성을
   위해 다음 5개 명령으로 `agent.pickle`도 다시 만들어두는 걸 권장
   (counter_circuit만 수 분 걸릴 수 있음, 나머지는 금방 끝남):
   ```
   python -m experiment.server_ext.pickle_agent --layout cramped_room --excluded-roles deliver --name RuleBasedBot_CrampedRoom --player-idx 1 --reactive
   python -m experiment.server_ext.pickle_agent --layout asymmetric_advantages --excluded-roles deliver --name RuleBasedBot_AsymmetricAdvantages --player-idx 1 --reactive
   python -m experiment.server_ext.pickle_agent --layout coordination_ring --excluded-roles deliver --name RuleBasedBot_CoordinationRing --player-idx 1 --reactive
   python -m experiment.server_ext.pickle_agent --layout forced_coordination --excluded-roles deliver --name RuleBasedBot_ForcedCoordination --player-idx 1 --reactive
   python -m experiment.server_ext.pickle_agent --layout counter_circuit --excluded-roles deliver --name RuleBasedBot_CounterCircuit --player-idx 1 --reactive
   ```

2. **"오더는 기존 아이콘 형태가 낫다"**: 2026-10-03에 한국어 텍스트
   ("양파 수프 x2")로 바꿨던 `_drawBonusOrders`/`_drawAllOrders`를 원본
   공식 데모의 아이콘 스프라이트 렌더링으로 되돌렸다
   (`_orderIngredientsLabel`/`_ordersToText` 헬퍼는 나중을 위해 남겨둠).

3. **"유저 조작이 아직도 매끄럽지 않다"**: 지난 수정(키 반복 폴링)은 입력
   빈도 문제였지만, 진짜 병목은 따로 있었다 — `app.py`의 게임 시뮬레이션
   루프(`play_game`)가 **초당 6번(약 167ms 간격)**으로 하드코딩되어 있었고,
   `config.json`의 `MAX_FPS`는 로드만 되고 어디에도 안 쓰이는 죽은 값이었다
   (실제로는 30이 적혀 있었지만 전혀 무관). 방향키를 아무리 자주 보내도
   서버가 초당 6번만 상태를 반영하니 한계가 있었고, 클라이언트 글라이드
   애니메이션(`ANIMATION_DURATION=50ms`)도 167ms 중 50ms만 움직이고
   나머지는 멈춰 보이는 구조였다. `MAX_FPS`를 실제로 `play_game()`에
   연결하고 10(약 100ms 간격)으로 올렸다 — 6→30처럼 과격하게 올리면 서버
   부하도 커지고 핑 반응 유효시간(스텝 단위라 틱 속도에 비례해 실제 시간이
   줄어듦)도 너무 짧아지므로 보수적으로 택함. `game.py`의 게임 제한시간은
   틱 수가 아니라 실제 시계(`time()`)로 재므로 gameTime=150초는 그대로
   유지됨(게임이 더 빨리 끝나는 부작용 없음). 같이 조정한 값:
   - `role_restricted_bot.py`의 `REACTION_DELAY_STEPS`: 3→5 (핑 유효 반응
     창을 약 2초로 유지 — 안 올리면 6fps 기준 설계값이 틱 속도만 빨라져서
     1.2초로 저절로 짧아짐).
   - `predefined.js`의 `MOVEMENT_SEND_INTERVAL_MS`: 120→80 (새 10fps 틱
     주기 100ms보다 살짝 빠르게).
   - `graphics.js`의 `ANIMATION_DURATION`: 50→85 (한 틱의 대부분을
     글라이드가 채우도록, 네트워크 지연을 흡수할 약간의 여유는 남김).

   변경 파일: `app.py`(`GAME_TICK_FPS`, 두 `play_game` 호출 지점),
   `config.json`(`MAX_FPS: 30→10`), `role_restricted_bot.py`,
   `static/js/predefined.js`, `graphics/overcooked_graphics_v2.2.js`.

## 3차 플레이테스트 피드백 반영 (완료, 2026-10-04)

위 2차 수정을 실제로 적용한 뒤에도 다섯 가지 문제가 재현됨. 유닛테스트(Test
5b 추가, 총 15개 전부 통과) + 독립 큐 시뮬레이션으로 직접 재현·검증했다.

1. **"order가 아직도 텍스트야"**: 2차 수정에서 소스 파일
   (`graphics/overcooked_graphics_v2.2.js`)은 분명히 아이콘 렌더링으로
   되돌렸는데, 실제로 서빙되는 `static/js/graphics.js`는 그대로 옛 텍스트
   버전이었다(diff로 직접 확인). Docker 빌드만 `COPY ./graphics/$GRAPHICS
   ./static/js/graphics.js` 단계를 자동으로 해주는데, 로컬에서는 그걸 매번
   수동으로 다시 복사해야 한다는 걸 깜빡하기 쉬운 구조였다(이미 README의
   "로컬 환경에서 실행하는 법"에 적어뒀었지만, 코드를 고칠 때마다 또
   깜빡하는 게 반복됨). **구조적으로 고침**: `app.py`가 기동할 때마다
   `graphics/overcooked_graphics_v2.2.js`와 `static/js/graphics.js`의
   내용을 비교해서 다르면 자동으로 덮어쓴다(`config.json`의
   `"GRAPHICS_SOURCE"`로 소스 파일명을 바꿀 수 있음, 기본값은 지금 파일).
   이제부터는 그래픽 소스를 고치고 서버를 (재)실행만 하면 항상 최신이
   서빙된다 — 수동 복사 단계가 아예 필요 없어짐.

2. **"유저와 봇의 속도를 일치시켜줘" (AI는 서버 딜레이를 안 받는 것 같다)**:
   2차 수정 자체(틱 속도 10fps 전환)는 맞는 방향이었지만, 그 수정의
   부작용으로 **새 버그**가 생겼었다. `predefined.js`가 `MOVEMENT_SEND_INTERVAL_MS
   =80ms`로 서버 틱(100ms)보다 일부러 빠르게 입력을 보내는데, `app.py`가
   human 플레이어를 `game.add_player(user_id)`로 등록할 때 `buff_size`를
   지정하지 않아 기본값 `-1`(무제한 큐)를 그대로 쓰고 있었다. `game.py`의
   `apply_actions()`는 사람 쪽에서 틱당 딱 1개(`get(block=False)`)만
   소비하므로, 서버보다 빠르게 쏟아지는 입력이 시간이 지날수록 **한도 없이
   큐에 쌓였다** — 라운드가 길어질수록 사람 입력이 점점 늦게 반영되는
   반면, 봇은 매 틱 즉석에서 결정하므로 큐 자체가 없어 전혀 안 느려지니
   "유저와 봇의 속도가 벌어진다"는 느낌으로 나타났다(사용자의 "서버 딜레이를
   안 받는 것 같다"는 직관이 현상 설명으로는 정확했다 — 다만 봇이 딜레이를
   "안 받는" 게 아니라, 사람 쪽에만 이 큐 적체가 생기는 구조였다). 독립
   큐 시뮬레이션으로 재현: 80ms 전송/100ms 소비를 5초 반복하면 무제한 큐는
   13개 적체, `buff_size=1`로 고치면 항상 0~1개로 유지됨을 확인했다. 고침:
   `app.py`의 두 `game.add_player(user_id)` 호출(최초 입장/대기 중인
   게임에 합류) 모두 `buff_size=1`을 명시 — NPC 봇이 원래부터 쓰던 설정과
   동일하게 맞췄다. `Queue.put()`이 기본적으로 블로킹이라 큐가 꽉 차 있으면
   다음 틱이 비울 때까지 짧게 대기할 뿐, 무한히 쌓이는 일은 이제 없다.

3. **"핑에 대해 반응이 전혀 없는 것 같다"**: `_show_ping_on_screen()`이
   "보낸 사람" 머리 위에만 말풍선을 띄워서, 상대(인간이든 봇이든)가 그
   핑을 "들었다"는 걸 보여줄 방법이 아예 없었다 — 실제 행동 반응은
   `REACTION_DELAY_STEPS`만큼 지연 후에만 나타나니 더더욱 "반응 없음"처럼
   보였다. 고침: `ping_game.py`의 `_route_ping_to_npc_bots()`가 핑을 받는
   **즉시**(지연 없이) 그 봇 머리 위에도 OK 말풍선을 띄우도록 추가했다
   (`.values()` → `.items()`로 바꿔 봇의 `player_id`를 확보). 이 합성
   ack는 `_enqueue_ping()`의 전체 로깅 파이프라인을 타지 않고
   `_show_ping_on_screen()`만 직접 호출해서, 사람 핑 횟수 같은 연구
   지표(trajectory의 `pings` 필드)를 오염시키지 않는다(Test 5b로 검증).

4. **"핑을 마우스로 클릭하는 것도 어렵다" → 숫자키 1~4 매핑**:
   `predefined.js`에 `PING_KEY_TO_TYPE = {1: help, 2: look, 3: mine, 4: ok}`
   를 추가해 `enable_key_listener()`의 keydown 핸들러에서 처리하도록 했다
   (OS 자동 반복은 무시, 방향키/스페이스와 동일한 핸들러 안에서 같이
   처리). 버튼 클릭과 숫자키가 같은 `send_ping()` 함수를 공유한다.
   `predefined.html`의 핑 버튼 라벨도 "도와줘 (1)"처럼 단축키를 같이
   보여주도록 바꿨다.

5. **"각 핑에 대한 봇의 반응을 같이 고민해보고 적용"**: `_PING_RESPONSE_MAP`
   (help→전체 해제, look→변화 없음, mine→deliver 양보, ok→변화 없음)을
   다시 검토한 결과, 맵 자체는 그대로 유지하기로 했다 — "반응이 없다"는
   느낌의 진짜 원인은 위 3번(시각적 수신확인 부재)이었고, look/ok까지
   행동을 바꾸게 하면 오히려 "그냥 알림용 핑인데 봇이 갑자기 하던 일을
   바꾼다"는 혼란을 만들 위험이 있다고 판단했다(`role_restricted_bot.py`에
   재검토 근거를 코드 주석으로 남겨뒀다). 즉: **"들었다"는 시각적 확인은
   모든 핑에 공통으로 주고, "행동이 바뀌는지"는 핑의 의미에 따라 다르게
   남겨두는** 현재 구조가 Phase 4 핑-행동 일치 지표 설계와도 맞는다고
   결론 내렸다. 사용자가 지도교수님과 "뭘 해야 도움으로 느껴지는지"를
   더 확정하고 싶다면(README에 이미 있던 TODO) 그때 다시 조정하면 된다.

   변경 파일: `app.py`(그래픽 자동 동기화, `buff_size=1`),
   `config.json`(`GRAPHICS_SOURCE` 키 추가), `ping_game.py`
   (`_route_ping_to_npc_bots`), `role_restricted_bot.py`(주석만, 맵 값은
   유지), `static/js/predefined.js`(`PING_KEY_TO_TYPE`, `send_ping()`),
   `static/templates/predefined.html`(버튼 라벨), `test_ping_logic.py`
   (Test 5b 추가).

## "비켜줘"(move) 핑 추가 — look 교체 (완료, 2026-10-04)

"핑이 직관적이지 않다"는 피드백 — help/mine/ok는 전부 "어떤 역할을 할지"를
바꾸는 사회적 신호였는데, 그중 어느 것도 "지금 당장 서로 길을 막고 있다"는
물리적 충돌 상황에는 직접 대응하지 않았다(특히 `look`은 애초에 행동 변화가
전혀 없었음). 그 자리를 "비켜줘"로 교체했다 — 서로 인접해 길을 막고 있을 때
쓰면, 봇이 실제로 비켜주는, 게임에 즉각적인 영향을 주는 핑. 종류 수는
그대로 4개(help/move/mine/ok), 키 매핑(1~4)도 그대로.

**설계 결정(실제 코드 작성 전에 먼저 확정한 것들)**:
- look 자리를 교체(5번째 핑 추가 대신) — 키 매핑을 안 건드리기 위해.
- 핑을 보낸 사람과 봇이 인접(거리 1)해 있을 때만 실제 반응 — 그 외엔 OK
  말풍선만 뜨고 행동은 안 바뀜("무관한데 갑자기 움직인다"는 혼란 방지).
- 대안 경로가 전혀 없는 외길에서는 "뒤로 한 칸" 같은 임시방편이 아니라,
  단계적으로 교착상태(둘 다 서로 기다리며 영원히 안 움직이는 상황)를
  구조적으로 막는 것을 목표로 함.

**기술적으로 왜 쉽지 않은가**: 기존 help/mine 반응은 전부 "어떤 일을 할지"
(역할, `excluded_roles`)만 바꾸고 길찾기는 항상 그대로 `mlam.motion_planner`
(레이아웃마다 미리 계산해 디스크에 캐시해둔 고정 그래프, 2차 수정에서 다룬
그 캐시)를 쓴다. "비켜줘"는 "지금 서 있는 자리를 비켜야 한다"는 문제라,
그 고정 그래프가 모르는 "지금 이 순간 상대가 서 있는 칸"이라는 동적
장애물을 반영해야 한다. 캐시된 플래너 자체를 매 틱 다시 계산하는 건
말이 안 되므로(다른 라운드/참가자에도 영향을 주는 공유 캐시이고, 큰
레이아웃은 재계산에 몇 분씩 걸림), "비켜주기" 전용으로 그 순간만 쓰는
가벼운 그리드 BFS를 따로 뒀다(`role_restricted_bot.py`의
`_bfs_first_step_avoiding`/`_best_retreat_step`) — 레이아웃이 커도 수십 칸
짜리 평범한 BFS라 매 틱 돌려도 비용이 거의 없다.

**교착상태 방지(3단계, 반드시 순서대로 시도)**:
1. **즉시 대안 경로 탐색**: 사람이 서 있는 칸 하나만 피해서 원래 목적지까지
   가는 경로를 그리드 BFS로 찾는다. 찾아지면 대기 없이 바로 그 경로로 이동.
2. **대안이 없으면 짧게만 대기**: 진짜 외길이면, `MOVE_ASIDE_GRACE_TICKS`
   (기본값 10틱 ≈ 1초, 10fps 기준. 처음엔 기존 핑 유효창과 맞춰 2초로
   뒀었는데 "너무 길게 느껴질 수 있다"는 판단으로 1초로 줄임)만큼만
   그 자리에서 대기 — 사람이 곧 비켜줄 걸 기대하는 짧은 유예. 서버 틱
   속도(`config.json`의 `MAX_FPS`)를 바꾸면 이 값도 같이 조정해야
   실제 대기 시간(초)이 유지된다.
3. **그래도 안 풀리면 봇이 무조건 후퇴**: 유예가 끝나도 막혀 있으면, 그
   순간부터는 "누가 먼저 움직일지"를 다시 저울질하지 않고 봇이 반드시
   (선택이 아니라 확정) 사람에게서 가장 멀어지는 칸으로 물러난다. "둘 다
   끝까지 기다리기만 하는" 대칭적 교착은 이 비대칭(항상 봇이 양보) 덕에
   수학적으로 생길 수 없다.

추가로 "되돌이표"(흔들림) 방지: 사람이 미세하게 움직일 때마다 매번 경로를
다시 계산해서 갈아타면 제자리서 떠는 것처럼 보일 수 있어, 이전에 쓰던
방향이 여전히 유효하면(벽도 아니고 사람이 서 있는 칸도 아니면) 그대로
유지한다. 동률인 선택지(예: 후퇴 방향이 여러 개로 거리가 같을 때)는 항상
같은 순서(`_MOVE_DIRECTION_PRIORITY` = 북→동→남→서)로 골라 매번 다른
선택으로 갈아타지 않게 했다.

**인접 여부는 어떻게 아는가**: `ping_game.py`의 `_enqueue_ping()`이 핑 보낸
사람의 `players` 리스트 인덱스(`sender_idx`)를 핑 항목에 같이 담아서
`PingReactiveBot.ping_queue`로 넘긴다. 봇은 매 틱 `state.players[sender_idx]
.position`으로 "지금" 그 사람이 어디 서 있는지 조회해서 거리를 재므로,
핑을 보낸 순간의 위치가 아니라 반응 창(≈2초) 동안 계속 최신 위치를
기준으로 판단한다(사람이 핑을 보낸 뒤 움직여도 따라 반응함).

**구조**: help/mine/ok는 기존처럼 `ml_action()`이 반환하는 motion_goals를
바꿔서 반응하지만(그 경로는 안 바꿈), "비켜줘"는 그 경로 선택 자체를
우회해야 하므로 더 위쪽인 `action()`을 오버라이드해서 처리한다(motion
goals/`mlam.motion_planner`를 아예 거치지 않고 그 틱의 저수준 이동 액션을
직접 반환). 두 채널(즉시성 핑 vs 비켜주기 모드)이 같은 `ping_queue`를
같이 쓰면서도 서로 간섭하지 않도록, 큐를 비우는 로직(`_drain_ping_queue`)을
따로 둬서 "move" 항목은 보이는 즉시 처리하고 나머지는 기존처럼 틱당
하나씩 순서대로 넘겨준다.

**검증**: `test_ping_logic.py`에 Test 14(BFS/후퇴 순수 로직 단위 테스트),
Test 15(열린 공간에서 즉시 우회 — 실제 cramped_room 레이아웃), Test
16(외길에서 유예 후 강제 후퇴 — 교착상태 방지 핵심 로직)을 추가, 전체
18개 테스트 전부 통과.

변경 파일: `ping_game.py`(`VALID_PING_TYPES`, `_enqueue_ping`의
`sender_idx`), `role_restricted_bot.py`(`MOVE_PING_TYPE`,
`_bfs_first_step_avoiding`, `_best_retreat_step`, `PingReactiveBot`의
`action()`/`_decide_move_aside_action`/`_drain_ping_queue` 등),
`static/js/predefined.js`(`PING_TYPES`, `PING_KEY_TO_TYPE`),
`static/templates/predefined.html`(`#ping-move` 버튼),
`graphics/overcooked_graphics_v2.2.js`(`PING_LABELS`),
`static/js/graphics.js`(자동 동기화로 반영), `test_ping_logic.py`
(Test 14/15/16 추가).

## 입력 큐 수정(buff_size=1)의 부작용 수정 — 블로킹 put() 제거 (완료, 2026-10-04)

위 "비켜줘" 핑을 실제로 적용해 플레이한 뒤 "봇과 유저의 움직임 속도 차이가
**이전보다 훨씬 커졌다**"는 피드백 — 즉 3차 수정에서 고쳤다고 생각한 그
문제가, 같은 수정 때문에 더 나빠진 역효과였다.

**원인**: 3차 수정에서 `app.py`의 `game.add_player(user_id, buff_size=1)`로
사람 입력 큐를 "무제한"에서 "최대 1개"로 제한했는데, 그 안에서 실제로
큐에 넣는 동작(`game.py`의 기본 `enqueue_action`)은 `queue.Queue.put(action)`
을 **블로킹**(기본값 `block=True`)으로 호출한다. `predefined.js`가
서버 틱(100ms)보다 빠르게(80ms) 계속 보내므로, 큐가 이미 차 있을 때마다
이 `put()`이 다음 틱이 비울 때까지 그 자리에서 기다린다 — 그런데 그
"기다림"이 그 입력을 처리하던 소켓 이벤트 핸들러(그린릿) 자체를 멈춰
세운다. eventlet 환경에서 한 커넥션의 이벤트는 보통 들어온 순서대로
처리되므로, 핸들러가 멈춰 있는 동안 그 다음 입력들은 **파이썬 큐가 아니라
전송 계층(소켓 버퍼)에** 쌓인다 — 겉보기엔 큐 길이를 1로 제한했지만,
적체 자체는 "한 칸 위"로 옮겨갔을 뿐이고, 매 입력마다 블로킹 시간이
추가로 더 쌓이니 체감 지연은 오히려 더 나빠졌다. 독립 시뮬레이션으로
확인: 블로킹 방식은 5초 동안 "멈춰 있던 시간"이 누적 1.9초(한 번에
최대 1초)까지 쌓였고, 아래 고친 방식은 누적 3ms 미만이었다.

**고침**: `ping_game.py`(`PingMixin.enqueue_action`)에서, 실제로 큐에
넣기 전에 큐가 이미 차 있으면 거기 든 "오래된"(아직 처리 안 된) 액션을
먼저 버리고 넘어가게 했다(`_drop_stale_action_if_queue_full`) — 그러면
뒤따르는 `super().enqueue_action()`의 `put()`은 항상 빈 자리에 넣는
것이라 블로킹이 전혀 없다. "최신 입력이 과거 입력보다 항상 더 중요하다"
는 뜻도 된다(사람은 지금 누르고 있는 방향키가 중요하지, 80ms 전에
누르고 있던 방향키가 아님). `game.py`(원본 파일) 자체는 건드리지 않고
`ping_game.py`(우리 개입 파일)에서만 처리.

**검증**: `test_ping_logic.py`에 Test 17 추가 — 큐가 찬 상태에서 연속으로
두 번 보내도 블로킹 없이, 오래된 액션이 아니라 최신 액션만 남고 큐 길이가
항상 1 이하로 유지되는지 확인. 전체 19개 테스트 통과.

변경 파일: `ping_game.py`(`PingMixin.enqueue_action`,
`_drop_stale_action_if_queue_full`), `test_ping_logic.py`
(`FakeOvercookedGame.pending_actions`를 실제 `queue.Queue` 기반으로 교체,
Test 2/3 어서션 갱신, Test 17 추가).

## 봇 속도 절반으로, 게임 시간 60초로 (완료, 2026-10-05)

위 블로킹 `put()` 수정까지 반영해 플레이한 뒤 피드백: "AI 봇이 너무 너무
빨라. 그리고 이 친구 제출도 안 해서(`excluded_roles=["deliver"]`라 의도된
동작) 수프가 쌓이고 있어. 이렇게 템포가 빠르면 유저는 정신 없어서 사고하기가
너무 어려워. **유저의 템포를 올리기보단 봇의 템포(속도)를 지금의 1/2정도로**
해도 될 것 같아. 그리고 시간은 60초로 줄이자."

**수프가 쌓이는 현상**: 별도 버그가 아니라 "배달은 의도적으로 사람의
역할"(3.4절 설계 철학) + 블로킹 `put()` 수정 이후 봇이 다시 제 속도로
움직이기 시작한 조합의 결과로 판단. 봇 속도를 줄이면 자연히 완화될 것으로
보고 별도 수정은 하지 않음(아래 속도 수정 이후 확인 필요).

**게임 시간 60초**: `config.json`의 `predefined.experimentParams.gameTime`을
150 → 60으로 단순 변경.

**봇 속도 1/2로 — 왜 `ticks_per_ai_action`을 안 썼는가**: `game.py`에 이미
딱 이 용도로 보이는 생성자 파라미터(`ticks_per_ai_action`)가 있다. 적용하기
전에 `game.py`의 `apply_actions()`를 읽어서 확인했는데, 그 파라미터를
1보다 크게 설정하면 **게임 전체(사람 포함)가 멈추는 버그**가 있다:

- NPC 쪽은 매 틱 `self.pending_actions[i].get(block=True)`로 무조건
  블로킹한다.
- 그런데 다음 행동을 계산하게 하는 state push(`npc_state_queues[...].put(...)`)는
  `self.curr_tick % self.ticks_per_ai_action == 0`인 틱에서만 일어난다.
- 즉 "건너뛰는" 틱에는 새 행동이 큐에 들어올 길이 전혀 없어서 그 틱의
  `get()`이 영원히 막히고, 메인 게임 루프 자체가 멈춘다(사람 쪽도 같은
  루프 안에 있으므로 같이 멈춤).

이건 원본 데모 코드 자체에 있던 버그로 보인다 — 트레이닝된 RL 정책처럼
매번 빠르게 응답하는 에이전트만 쓰는 전제라면 `ticks_per_ai_action>1`을
실제로 쓸 일이 없어서 지금까지 드러나지 않았을 뿐이다. "유저의 템포는
그대로 두고 봇만 느리게"라는 요구와도 정면으로 어긋나므로(건드리면 유저
쪽까지 같이 멈춤) 이 파라미터는 그대로 두고 쓰지 않기로 했다.

**실제로 한 것**: `role_restricted_bot.py`의 `RoleRestrictedBot`에
`action()`을 새로 오버라이드해서, 매 틱(`self._bot_tick_counter`로 직접
셈) 중 `BOT_SPEED_DIVISOR`(기본 2)번에 1번만 부모(`GreedyHumanModel`)의
실제 행동 계산(`ml_action` → 모션 플래너 → 비용 계산)을 돌리고, 나머지는
그 즉시 `STAY`를 반환한다. 서버/큐 쪽에서 보면 봇은 **매 틱 하나도 안
빠뜨리고 즉시 응답**하므로(블로킹 전혀 없음) `ticks_per_ai_action`의
멈춤 버그와 무관하고, 유저의 틱 속도·반응성도 전혀 안 바뀐다 — 봇만
체감상 절반 속도로 움직인다.

**발견 → 수정한 삽질 하나**: 처음엔 "건너뛰는 틱에도 `self.prev_state`를
직접 최신화해야 부모의 `auto_unstuck`(제자리에 멈춘 걸 감지하면 무작위
탈출 행동을 주입하는 안전장치)이 오작동하지 않는다"고 생각해서 그렇게
구현했는데, 실제로는 **정반대로 역효과**였다: 건너뛰는 틱은 항상 `STAY`라
위치가 안 바뀌므로, 그 상태를 `prev_state`로 저장해두면 바로 다음
"진짜" 턴에서 "직전 상태 대비 위치 변화 없음"으로 보여 거의 매번
`auto_unstuck`이 잘못 발동했다(기존 Test 13이 이걸로 실패하면서 발견).
`auto_unstuck`은 "연속된 두 번의 **진짜** 행동 계산 사이에 위치가 안
바뀌었는가"를 보려는 것이므로, 건너뛰는 틱에서는 `prev_state`를 아예
손대지 않는 것이 맞는 구현이었다(부모가 실제로 호출될 때만 자연스럽게
갱신되게 둠).

`PingReactiveBot`의 "비켜줘" 즉시 회피 반응은 이 속도 제한을 거치지
않는다(자체 `action()`에서 `super().action()`을 타지 않고 바로
`move_action`을 반환하는 경로) — 교착상태 회피는 느려지면 오히려
충돌/교착 위험이 커지므로 항상 즉시 나가야 한다는 기존 설계와 일관되게
그대로 둠.

**검증**: `test_ping_logic.py`에 Test 18 추가 — 20틱 동안 "진짜" 행동
계산이 정확히 10번(=20÷`BOT_SPEED_DIVISOR`)만 일어나는지, 건너뛰는 틱에
`prev_state`가 안 바뀌는지, "진짜" 턴에는 정상적으로 바뀌는지 확인. 기존
Test 13(수프를 카운터에 내려놓는 fallback)은 봇이 느려진 만큼 같은 거리를
걷는 데 필요한 틱 수가 늘어나므로 틱 예산을 `BOT_SPEED_DIVISOR`배로
늘려서 맞춤(로직 자체는 그대로). 전체 18개 테스트 통과.

변경 파일: `role_restricted_bot.py`(`BOT_SPEED_DIVISOR` 상수,
`RoleRestrictedBot.__init__`에 `_bot_tick_counter` 추가,
`RoleRestrictedBot.action()` 신규), `config.json`(`gameTime: 150→60`),
`test_ping_logic.py`(Test 18 추가, Test 13 틱 예산 조정).

BOT_SPEED_DIVISOR를 더 낮추고 싶으면(예: 1/3 속도) `role_restricted_bot.py`
상단의 상수 값만 바꾸면 된다.

### 위 수정 적용 후 실제로 발생한 치명적 버그: 게임 시작하자마자 멈춤 (완료, 2026-10-05)

위 속도 제한을 적용하고 실제로 플레이하자 "게임 시작하면 Time Left:
59.99...초에서 게임이 멈춰"라는 피드백 — 타이머까지 같이 멈추는, 틱 자체가
완전히 정지하는 증상.

**원인**: `experiment/server_ext/pickle_agent.py`로 미리 구워둔
`static/assets/agents/RuleBasedBot_*/agent.pickle` 파일들은 이번 속도 제한
변경 **이전의** `RoleRestrictedBot.__init__`으로 만들어진 것이다.
`pickle.load()`는 `__init__`을 다시 실행하지 않고 저장 당시의 `__dict__`만
그대로 복원하므로, 복원된 인스턴스에는 새로 추가한 `_bot_tick_counter`
속성이 아예 없었다. 그 상태에서 `RoleRestrictedBot.action()`의
`self._bot_tick_counter += 1`이 `AttributeError`를 던지는데, 이게 하필
`npc_policy_consumer`(게임 메인 루프가 아니라 NPC 전용 백그라운드 스레드)
안에서 조용히 터진다 — `reset()`의 `agent_index` 주석에 이미 적어둔 것과
**똑같은 종류의 함정**(pickle은 `__init__`을 안 돌리니, 새로 추가한 속성은
기존 pickle에 없다)이 또 발생한 것. 스레드가 죽으면 그 NPC의
`pending_actions` 큐에는 그 뒤로 아무것도 안 들어오고, `apply_actions()`의
`self.pending_actions[i].get(block=True)`가 영원히 안 풀려 **게임 전체(사람
포함, 타이머 포함)가 멈춘다** — 정확히 이 증상.

**고침 (두 가지, 둘 다 적용)**:
1. `RoleRestrictedBot.action()`에서 `self._bot_tick_counter += 1`을
   `self._bot_tick_counter = getattr(self, "_bot_tick_counter", 0) + 1`로
   방어적으로 바꿨다 — 앞으로 클래스에 새 인스턴스 속성을 추가해도, 기존에
   구워둔 pickle이 그 속성 없이도 안전하게 동작한다(이번처럼 또 pickle을
   전부 다시 구워야 하는 상황을 예방).
2. 그래도 **기존 5개 pickle 자체가 이미 낡은 상태**(이번 속도 제한보다도
   전, 심지어 일부는 "비켜줘" 기능보다도 전에 만들어졌을 수 있음)라, 근본
   해결을 위해 `pickle_agent.py`로 5개 레이아웃
   (cramped_room/asymmetric_advantages/coordination_ring/
   forced_coordination/counter_circuit) 전부 다시 구워서 교체했다. (실제
   게임에서는 1라운드가 시작될 때 로드되는 피클 하나만 쓰고 이후 레이아웃
   전환은 `update_for_layout` 훅으로 처리되지만, "시작 난이도(연구자 설정)"
   에서 특정 레이아웃 하나만 테스트로 고를 수도 있어서 5개 전부 최신화함.)

**검증**: `test_ping_logic.py`에 Test 19 추가 — `_bot_tick_counter`가 없는
`__dict__` 상태(=옛 pickle을 복원한 상황을 그대로 흉내)에서
`bot.action()`을 호출해도 `AttributeError` 없이 정상 동작하는지 확인.
전체 19개 테스트 통과. 또한 5개 pickle을 전부 직접 다시 로드해서 각자의
레이아웃으로 `action()` 몇 틱 돌려서 멈추지 않는지 직접 확인함.

**주의**: `agent.pickle`은 바이너리 파일이라 git diff로는 내용이 안 보임 —
덮어쓴 뒤 git에 올릴 때 "바이너리 파일이 변경됨"처럼 보이는 게 정상이다.
**앞으로 `role_restricted_bot.py`의 `PingReactiveBot`/`RoleRestrictedBot`에
새 인스턴스 속성을 추가할 때마다, 이번처럼 `getattr(..., 기본값)`으로
방어하는 걸 기본 습관으로 삼을 것** — 아니면 매번 5개 pickle을 다시 구워야
하고, 깜빡하면 또 "게임이 조용히 멈추는" 디버깅하기 어려운 버그로 돌아온다.

변경 파일: `role_restricted_bot.py`(`action()`의 `getattr` 방어),
`test_ping_logic.py`(Test 19 추가), `static/assets/agents/RuleBasedBot_*/agent.pickle`
(5개 전부 재생성 — 바이너리).

## 매칭 대기 중 Leave → 엉뚱한 화면, 게임기록/처음화면 버튼, 봇 속도 1/3로 (완료, 2026-10-05)

세 가지 피드백을 한 번에 반영.

**1) "사람 매칭 대기 중 Leave 누르면 이상한 화면으로 간다"**: 그 화면은
`/`(원본 공개 데모 기본 화면 — Player1/Player2 드롭다운이 있는 그 화면)이
맞다. `predefined.js`의 `leave-btn` 핸들러가 `window.location.href = "/"`로
박혀 있던 게 원인 — 원본 공개 데모 코드를 그대로 가져온 흔적이 실험용
페이지에 남아있던 것(버그라기보단 안 지운 것). `/predefined`(이 실험
자체의 시작 화면)로 보내게 고쳤다.

**2) 다른 PC에서 "인간 학습 집단"으로 매칭하는 법**: `app.py`는 이미
`host="0.0.0.0"`으로 떠 있어서(코드 안 건드려도 됨) 같은 네트워크 안의
다른 기기에서 접속하는 것 자체는 원래도 가능하다. 방법:

1. 서버를 켠 PC(지금 127.0.0.1로 접속하는 그 PC)에서 명령 프롬프트에
   `ipconfig` 입력 → "IPv4 주소"를 확인 (보통 `192.168.x.x` 형태).
2. 다른 PC는 **같은 공유기/네트워크**(같은 Wi-Fi 등)에 연결되어 있어야 함.
3. 다른 PC 브라우저에서 `http://<위 IPv4 주소>:<포트번호>/predefined` 접속
   (포트번호는 지금 쓰는 5001 등 동일하게, `127.0.0.1` 대신 저 IP를 씀).
4. 처음 접속 시 Windows 방화벽이 "이 앱이 네트워크 접근을 요청합니다"
   팝업을 띄우면 **허용**해야 한다. 안 뜨는데 접속이 안 되면, Windows
   Defender 방화벽 → 고급 설정 → 인바운드 규칙에서 그 포트(또는 python.exe)
   를 허용하는 규칙을 추가해야 할 수 있다.
5. 두 사람 다 "시작하기"를 누르면(먼저 누른 사람의 조건/난이도 설정이
   적용됨, `predefined.html`에 이미 안내돼 있는 그대로) 서버가 자동으로
   매칭한다.

코드 변경은 없음 — 순전히 네트워크/방화벽 설정 안내.

**3) 게임오버 화면에 "게임 기록 보기" / "처음 화면으로" 버튼 추가**:
`predefined.html`의 `#game-over`를 단순 `<h4>` 텍스트에서, 버튼 2개와
숨겨진 기록 표를 포함한 영역으로 바꿨다.

- "게임 기록 보기": 클릭하면 이번 세션에서 진행한 라운드(난이도)별로
  `최종 점수 / 소요 시간 / 핑 타입별(도와줘·비켜줘·내가할게·OK) 횟수`를
  표로 보여준다. 서버의 `game.get_data()`는 호출하면서 그 라운드의
  trajectory를 비우므로(game.py), `reset_game`(라운드 전환)과
  `end_game`(세션 종료) 이벤트가 올 때마다 그 즉시
  `recordRoundResult()`로 요약만 뽑아 `window.GAME_RECORD`에 누적해뒀다가
  버튼을 눌렀을 때 그려준다 — 전체 리플레이가 아니라 요약 표.
- "처음 화면으로": `/predefined`로 이동(위 1번과 같은 목적지).

서버(Python) 쪽은 전혀 안 건드림 — 순수 클라이언트(JS/HTML)만 수정.

**4) 봇 속도 1/2로도 아직 빠르다는 피드백 → 1/3로**:
`role_restricted_bot.py`의 `BOT_SPEED_DIVISOR`를 2 → 3으로. 이 값은
인스턴스 속성이 아니라 모듈 상수라(`RoleRestrictedBot.action()`이 매 틱
그 자리에서 읽음) **pickle 재생성이 필요 없다** — 바로 위 섹션에서 겪은
"속성 추가하면 pickle 다시 구워야 한다" 함정과 다른 케이스. 체감이 여전히
안 맞으면 이 상수만 더 조정하면 된다(4, 5, ...).

**검증**: `test_ping_logic.py` 전체(19개) 재통과 확인 — Test 18/13은
`rrb.BOT_SPEED_DIVISOR`를 동적으로 참조하므로 값이 2→3으로 바뀌어도 코드
수정 없이 그대로 통과함. `predefined.js`는 `node --check`로 문법 검증.
JS 쪽 로직(핑 집계/표 렌더링)은 자동화 테스트가 없음 — **실제 브라우저로
한 세션 끝까지 플레이해서 기록 표가 정확히 뜨는지 직접 확인 필요**
(아래 "다음 작업" 1번에 추가).

변경 파일: `static/js/predefined.js`(leave-btn 목적지, `recordRoundResult`/
`renderGameRecord`, 새 버튼 핸들러), `static/templates/predefined.html`
(`#game-over` 영역 재구성), `role_restricted_bot.py`(`BOT_SPEED_DIVISOR`
2→3).

## 다음 작업 (우선순위 순)

1. **봇 속도 1/3(`BOT_SPEED_DIVISOR=3`) + 게임 시간 60초 + 게임기록/처음화면
   버튼을 실제 브라우저로 플레이해서 확인.** 특히: (a) 봇 체감 속도가 이제
   적당한지(여전히 빠르면 `BOT_SPEED_DIVISOR`를 더 올리기, 반대로 너무
   느려졌으면 내리기), (b) 수프가 쌓이는 현상이 완화됐는지, (c) 60초가 각
   난이도(레이아웃)를 경험하기에 너무 짧지는 않은지, (d) 게임오버 화면에서
   "게임 기록 보기"를 눌렀을 때 라운드별 점수/시간/핑 횟수가 정확히
   뜨는지, "처음 화면으로"가 제대로 시작 화면으로 돌아가는지, (e) 다른
   PC에서 IP로 접속해 "인간 학습 집단" 매칭이 실제로 되는지(위 "매칭 대기
   중 Leave" 섹션의 접속 방법 참고).
2. 실제 브라우저로 위 세 가지 수정(봇이 카운터에 내려놓는지, 오더 아이콘,
   조작 매끄러움) 다시 플레이해서 확인. **특히 처음 counter_circuit으로
   전환될 때 캐시 재계산으로 오래 멈출 수 있다는 점을 염두에 두고, 위
   "주의" 항목대로 미리 한 번 전체 플레이해서 캐시를 데워둘 것.**
3. "도와줘" 핑이 여전히 티가 안 나는 경우가 있는지, 있다면 어떤 상황인지
   관찰 → 지도교수 상담해서 "도움"의 정의를 더 구체적으로 확정.
4. **"비켜줘" 핑을 실제 브라우저로 플레이하며 확인.** 특히: (a) 좁은 레이아웃
   (cramped_corridor, forced_coordination 등)에서 일부러 서로 막아보고
   유예 시간(≈1초) 후 봇이 실제로 후퇴하는지, (b) 열린 레이아웃에서 즉시
   우회하는지, (c) 사람이 핑을 보낸 뒤 계속 움직일 때도 봇이 "그 순간"
   위치를 따라 반응하는지. 유예 시간(`MOVE_ASIDE_GRACE_TICKS`)이 실제
   플레이에서 너무 길게/짧게 느껴지면 조정.
5. "Deterministic?" 같은 원본 공식 데모의 다른 옵션들, "Replay Trajectories"
   기능 도입 여부 검토 (우선순위 낮음, 지금 당장 필수는 아님).
6. Phase 5 (실험 플로우) 착수.

## 알려진 이슈

- 원본 저장소가 `requires-python = ">=3.10,<3.11"`로 고정되어 있어 **반드시 Python 3.10**
  가상환경을 써야 합니다 (3.11에서는 `pip install -e .`가 즉시 실패함).
- `RoleRestrictedBot.ml_action()`은 `GreedyHumanModel.ml_action()`의 로직을 참고해
  카테고리별로 재작성한 것이라, 원본이 업데이트되면 이 파일도 함께 점검해야 합니다.
- `src/overcooked_ai_py/data/planners/`의 `*_am.pkl`/`*_mp.pkl` 캐시는
  `.gitignore`되어 있어 팀원 간에 공유되지 않습니다. mlam 파라미터를 바꿀
  때마다(2026-10-04의 `counter_drop` 변경처럼) 각자 로컬에서 자동
  재계산되며, 레이아웃이 클수록(counter_circuit) 오래 걸릴 수 있습니다.

## 2026-10-05 (3) 주문 목록 방식 / 한글 게임 방법 / 닉네임 + 로그 저장 경로

### 주문 목록 (`server_ext/order_queue.py`, `OrderQueueMixin`)
- 시작 시 주문 1개, 이후 10초마다 1개 추가(만료 없음). 목록에 있는 수프를 서빙하면 점수 + 그 주문 1개 삭제, 목록에 없으면 0점.
- 상수: `ORDER_ARRIVAL_INTERVAL_SEC=10`, `INITIAL_ORDER_COUNT=1`. 주문 순서는 레이아웃 이름으로 시드 → 같은 난이도에선 모든 참가자가 동일한 주문열.
- 원본 `OvercookedState.all_orders`는 건드리지 않음(목록이 비면 "전부 허용"으로 폴백되는 문제). 열린 주문은 게임 객체가 보관하고 `mdp.deliver_soup`를 감싸 보상 계산, `get_state()`로 HUD("주문 목록:")에 전달.
- 로그: trajectory 각 스텝에 `open_orders`, `order_events`(initial/added/delivered/rejected).
- 알려진 한계: 봇의 요리 선택은 정적 `state.all_orders[0]` 기준이라 counter_circuit에선 열린 주문과 어긋날 수 있음.

### 게임 방법 페이지
- `templates/instructions.html`을 한글로 전면 재작성(구현 기준: 방향키/스페이스, 주문 규칙, 핑 4종과 봇 반응, 봇은 서빙 안 함·1/3 속도, 60초). 시작 화면 문구의 "이거 봐"→"비켜줘" 정정.

### 닉네임 + 로그 (`server_ext/data_log.py`, `DataLogMixin`)
- 시작 화면에서 닉네임 필수(최대 20자). join 이벤트의 최상위 `nickname`으로 전달 → `game.set_nickname(sid, nick)`.
- 원본 `get_data()`를 오버라이드(반환 계약 동일). 저장 위치: 환경변수 `OVERCOOKED_DATA_DIR`, 없으면 `<저장소>/data/game_logs` (서버 시작 시 콘솔에 출력, `.gitignore` 등록).
- 구조: `<폴더>/<시작시각>_<HH|HA>_<닉네임>/round<N>_<레이아웃>.pkl` + `<폴더>/index.csv`. pkl = `{uid, trajectory, nicknames{0,1}, meta{...}}`.
- Windows에서 경로 변경: `set OVERCOOKED_DATA_DIR=D:\logs` 후 같은 창에서 `python app.py`.
- 다음 작업: `compute_metrics.py`를 이 폴더 구조/닉네임에 맞게 연결.

## 2026-10-05 (4) 봇 서빙 허용(자리 부족) / 봇이 만들 수프 = 가장 오래된 열린 주문
- `DELIVER_WHEN_FREE_COUNTERS_AT_MOST = 1` (`role_restricted_bot.py`): 내려놓을 빈 카운터가 1개 이하이면 `deliver`가 제외된 봇도 들고 있는 수프를 직접 서빙. 카운터가 가득 차 봇이 어설션으로 죽던 경우도 방어(내려놓을 곳 없으면 예전 fallback).
- `RoleRestrictedBot._target_order()`: 게임(`OrderQueueMixin._sync_orders_to_bots`)이 매 틱 NPC에 `open_orders`를 넣어 주고, 봇은 목록의 첫 번째(=가장 먼저 추가된 미처리 주문)를 만든다. 재료도 그 주문 기준으로 양파/토마토를 고름(냄비에 일부 들어 있으면 모자란 재료). 주문이 비면 레이아웃 허용 목록 첫 번째로 대체. agent.pickle 재생성 불필요(속성은 getattr).
- Test 22 추가.
- `data/game_logs` 폴더는 수정본을 적용하고 서버를 한 번 띄워야(시작 시 자동 생성) 생김.

## 2026-10-05 (5) 핑 재설계 / forced 계열 공급자 봇 / 로그 저장 재시도 / 안내 페이지 축소

### 핑 (role_restricted_bot.py `PingReactiveBot`, ping_game.py)
- 순서 변경: 이전엔 핑 수신 즉시 봇 머리 위에 OK → 나중에 행동. 이제 **이해 지연(`PING_ACK_DELAY_STEPS=8`≈0.8초) → OK 말풍선 → 행동**. OK는 서버가 즉시 띄우지 않고 봇이 핑을 활성화할 때 `pop_ack()`로 알리면 `PingMixin.tick()`이 띄움.
- 도와줘: 역할 제한 해제 + **보낸 사람과 가까운 목표 우선**. 내가 할게: 서빙 제외 + **보낸 사람과 먼 목표 우선**. 효과는 `PING_EFFECT_STEPS=100`(≈10초) 유지. (거리 = 목표 칸과 보낸 사람 위치의 맨해튼 거리, 동률이면 봇의 이동 비용 낮은 쪽)
- **발견한 버그**: 예전 구현은 핑 하나가 "의사결정 한 번"에만 반영됐고, 봇 속도 제한(3틱 중 2틱은 STAY) 때문에 그마저도 대부분 버려졌다 → "핑이 쓸모없다"는 체감의 실제 원인 중 하나. 이제 효과가 기간 동안 유지.
- 핑 처리는 `note_step()`(게임 틱)에서 수행. 시간 기준을 봇 자체 카운터로 통일(게임 curr_tick과 봇 카운터를 섞으면 2라운드부터 핑이 만료 처리될 위험이 있었음).
- 옛 agent.pickle과 호환(새 속성은 `_ensure_ping_state`/getattr). 5개 pickle 로드·동작 확인, **재생성 불필요**.

### forced_coordination 등: 공급자(supplier) 봇
- `analyze_layout_roles()`: 시작 위치에서 걸어 닿는 칸으로 봇이 냄비·서빙대에 닿는지 판단. 못 닿으면 `supplier`.
  forced_coordination(봇=재료·접시 구역)이 해당. 나머지 4개 레이아웃은 기존 역할.
- 공급자는 사람과 공유하는 카운터(handoff, forced에서는 (2,1),(2,2),(2,3))에 **접시 1개 + 양파**를 채움. 빈 칸이 없으면 대기.
- 이전 봇은 이 레이아웃에서 `AssertionError`로 NPC 스레드가 죽을 수 있었음(시뮬레이션으로 재현) → 게임 정지.
- 안전망: `RoleRestrictedBot.action()`이 어설션 시 이번 틱 STAY(스레드 사망 방지).
- 도구 `analysis/simulate_pair.py`: 사람 대역(범용 / forced 전용 규칙 기반)+봇으로 600스텝 시뮬레이션. forced: 공급자 봇 + 규칙 기반 사람 = 10개 서빙/60초(주문 목록 상한 7개 이내로 충분).
  ※ 범용 사람 대역은 좁은 주방에서 서로 막히는 등 현실의 사람보다 훨씬 못하므로 절대 점수 해석 금지 — "봇이 진행을 막지 않는가" 확인용.

### 로그 저장 PermissionError
- Windows(OneDrive/백신)가 방금 만든 임시 파일을 잡아 `os.replace`가 실패 → 최종 경로에 직접 쓰고 최대 6회 재시도, 그래도 안 되면 `_retryNNN` 이름으로 저장. index.csv가 엑셀로 열려 있어도 pkl은 저장.
- 권장: 로그 폴더를 OneDrive 밖(`OVERCOOKED_DATA_DIR`)으로 지정하면 잠금 문제가 더 줄어든다.

### 게임 방법 페이지
- 약 240단어로 축소(조작, 수프 만들기, 주문/점수, 소통 버튼 표, AI 파트너 특징). 새 동역학상 요리 시작은 "빈손으로 냄비에 스페이스바"임을 정확히 반영.

### 알려진 한계
- counter_circuit: 좁은 통로에서 사람/봇이 서로 막히면 봇 쪽 회피는 "비켜줘" 핑에 의존.
- 봇의 요리 선택은 열린 주문 첫 번째 기준(주문 순서는 레이아웃별 고정 시드).
