"""
Phase 2: 핑 소통 채널.

원본 overcooked_demo/server/game.py 의 OvercookedGame 은 건드리지 않는다.
대신 이 파일이 OvercookedGame 을 상속해, 원본 그대로 두고도 동작이 바뀌는
지점만 오버라이드한다. 이 파일과 원본의 diff 자체가 IRB 서류에 첨부 가능한
"실험 개입(intervention)의 전체"가 된다.

설계 (파이프라인 문서 Phase 2 "단축 옵션"):
    새 Socket.IO 이벤트(on_ping/broadcast_ping)를 따로 만들지 않고,
    기존 "action" 이벤트 파이프를 그대로 재사용한다.
      - 원본 OvercookedGame.enqueue_action(player_id, action) 은
        action_to_overcooked_action[action] 에서 STAY/UP/DOWN/LEFT/RIGHT/SPACE
        가 아니면 KeyError.
      - 클라이언트가 "PING_HELP" / "PING_LOOK" / "PING_MINE" / "PING_OK" 같은
        문자열을 그대로 그 action 이벤트로 보내면, 여기서 "PING_" 접두어를
        감지해 이동 큐가 아니라 별도의 핑 큐로 라우팅한다.
      - apply_actions()가 만드는 transition dict에 "pings" 필드를 추가해
        self.trajectory에 그대로 쌓이게 한다 (원본 로깅 파이프를 재사용).
      - 상대 참가자가 보낸 핑은, npc_policies 중 ping_queue 속성을 가진 정책
        (=PingReactiveBot, Phase 3)에도 즉시 전달한다.
      - 화면 표시(캐릭터 머리 위 말풍선)용으로, get_state()가 서버 브로드캐스트
        (state_pong 이벤트, play_game 루프가 몇 fps마다 보냄)에 "pings" 필드를
        추가로 얹는다: {player_idx(문자열): ping_type}, PING_DISPLAY_TICKS
        틱 동안만 유지되다가 자동으로 사라진다. 이건 trajectory 로깅과는 별개
        (로깅은 "핑이 발생한 사실", 이건 "지금 화면에 보여줄 핑")의 용도라
        따로 관리한다.

이 로직은 PingMixin 에 원본 클래스와 무관하게 분리되어 있다. 실제 배포용
클래스는 PingEnabledGame = PingMixin + OvercookedGame 조합이고, 단위 테스트는
PingMixin 을 경량 가짜(fake) 베이스 클래스와 조합해 원본의 무거운 의존성
(ray / human_aware_rl.rllib.rllib, 구버전 gym) 없이 로직만 검증한다.
자세한 이유와 실제 배포 시 필요한 의존성은 test_ping_logic.py 상단 주석 참고.

TODO(파일럿 전 확정 필요):
- 핑 타입 목록(VALID_PING_TYPES)이 실제 UI 버튼과 정확히 일치하는지
- _route_ping_to_npc_bots가 "사람이 보낸 핑만" NPC에 전달하는지, AI끼리도
  핑을 주고받게 할지 (현재: 모든 플레이어의 핑을 모든 NPC에 전달)
"""
import time

PING_PREFIX = "PING_"
VALID_PING_TYPES = {"help", "look", "mine", "ok"}

# state_pong은 play_game 루프에서 초당 몇 프레임(fps, 기본 6)마다 브로드캐스트된다.
# 12틱 ≈ 2초(6fps 기준) 동안 말풍선을 화면에 유지한다.
PING_DISPLAY_TICKS = 12


class PingMixin:
    """
    OvercookedGame(혹은 그 계약을 만족하는 아무 클래스)에 믹스인되는
    핑 채널 로직. 원본 클래스의 존재를 가정하지 않으므로 단독으로도
    테스트 가능하다.
    """

    def _ping_init(self):
        # 이번 tick 동안 들어온 핑들. apply_actions()에서 transition에 붙이고 비운다.
        self._pending_pings = []
        # 화면에 지금 보여줄 핑: {player_idx: {"ping_type": str, "expires_tick": int}}
        self._visible_pings = {}

    def enqueue_action(self, player_id, action):
        if isinstance(action, str) and action.startswith(PING_PREFIX):
            self._enqueue_ping(player_id, action)
            return
        super(PingMixin, self).enqueue_action(player_id, action)

    def _enqueue_ping(self, player_id, action):
        if not self.is_active or player_id not in self.players:
            return
        ping_type = action[len(PING_PREFIX):].lower()
        if ping_type not in VALID_PING_TYPES:
            # 알 수 없는 핑 타입은 조용히 무시 (원본처럼 KeyError로 게임을 죽이지 않음)
            return
        entry = {
            "player_id": player_id,
            "ping_type": ping_type,
            "timestamp": time.time(),
            "step": getattr(self, "curr_tick", None),
        }
        self._pending_pings.append(entry)
        self._route_ping_to_npc_bots(entry)
        self._show_ping_on_screen(player_id, ping_type)

    def _show_ping_on_screen(self, player_id, ping_type):
        """다음 몇 번의 state_pong 브로드캐스트 동안 이 플레이어 머리 위에
        말풍선을 띄우도록 표시한다. players 리스트에서의 인덱스가 클라이언트
        Phaser 코드의 state.players[pi]와 대응하는 인덱스다."""
        if player_id not in self.players:
            return
        idx = self.players.index(player_id)
        curr_tick = getattr(self, "curr_tick", 0)
        self._visible_pings[idx] = {
            "ping_type": ping_type,
            "expires_tick": curr_tick + PING_DISPLAY_TICKS,
        }

    def _route_ping_to_npc_bots(self, entry):
        """PingReactiveBot(= ping_queue 속성을 가진 정책)에 방금 들어온 핑을 즉시 전달."""
        for policy in getattr(self, "npc_policies", {}).values():
            if hasattr(policy, "ping_queue"):
                policy.ping_queue.append(entry)

    def apply_actions(self):
        result = super(PingMixin, self).apply_actions()
        pings_this_tick, self._pending_pings = self._pending_pings, []
        if self.trajectory:
            self.trajectory[-1]["pings"] = pings_this_tick
        return result

    def tick(self):
        # PingReactiveBot의 반응 지연 계산용 스텝 카운터를 매 tick 갱신.
        for policy in getattr(self, "npc_policies", {}).values():
            if hasattr(policy, "note_step"):
                policy.note_step()
        return super(PingMixin, self).tick()

    def activate(self):
        # 여러 레이아웃(config.json의 "layouts" 배열)이 한 세션 안에서
        # 순서대로 진행될 때, 원본 OvercookedGame.activate()는 매 라운드
        # self.mdp를 새 레이아웃 것으로 바꿔주지만 npc_policies(우리 봇)는
        # 건드리지 않는다. RoleRestrictedBot.ml_action()은 self.mlam에
        # 의존하는데 mlam은 특정 레이아웃 지형에 맞춰 미리 계산된 거라,
        # 안 바꿔주면 레이아웃이 바뀌는 순간부터 봇이 이전 레이아웃 지형
        # 기준으로 동작해버린다(실제 서버로 5개 레이아웃 순환을 테스트하다
        # 발견).
        #
        # 중요: 이 업데이트는 반드시 super().activate()보다 **먼저** 일어나야
        # 한다. 처음에는 super() 호출 "이후"에 self.mdp를 읽어서 넘겨줬는데,
        # 실제 서버로 재현해보니 레이스 컨디션이 있었다 — OvercookedGame.
        # activate()는 self.mdp를 새로 설정하자마자 그 안에서 바로
        # npc_policy_consumer 스레드를 새로 띄우고 시작 상태를 큐에 넣는다.
        # 그 스레드가 (아직 우리가 update_for_layout을 호출하기 전에) 바로
        # 그 상태를 꺼내 policy.action()을 불러버리면, 봇은 여전히 "이전"
        # 레이아웃의 mlam으로 "새" 레이아웃의 상태를 해석하게 되고, 그 결과
        # 좌표가 mlam의 이동 그래프에 아예 없어서
        # `AssertionError: Node 1 cc: [] / Node 2 cc: [0]`로 스레드가 죽는
        # 걸 실제 5레이아웃 연속 테스트에서 확인했다. 그래서 여기서는
        # super().activate()가 self.layouts.pop()으로 꺼낼 "다음" 레이아웃을
        # 먼저(pop 하지 않고) 들여다보고, 그 레이아웃의 mdp를 미리 계산해
        # 정책들에 넘긴 "다음" super().activate()를 부른다 — 새 스레드가
        # 시작될 때는 이미 정책이 새 레이아웃 기준으로 준비돼있다.
        layouts = getattr(self, "layouts", None)
        if layouts:
            next_layout_name = layouts[-1]  # self.layouts.pop()과 동일한 다음 원소
            from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld

            next_mdp = OvercookedGridworld.from_layout_name(
                next_layout_name, **(getattr(self, "mdp_params", None) or {})
            )
            for policy in getattr(self, "npc_policies", {}).values():
                if hasattr(policy, "update_for_layout"):
                    policy.update_for_layout(next_mdp)
        super(PingMixin, self).activate()

    def get_state(self):
        # 원본 OvercookedGame.get_state()가 만드는 딕셔너리(potential/state/
        # score/time_left)에 "pings" 필드만 얹는다. 이게 state_pong 이벤트로
        # 그대로 클라이언트에 나가서 화면에 말풍선을 그리는 데 쓰인다.
        state_dict = super(PingMixin, self).get_state()
        curr_tick = getattr(self, "curr_tick", 0)
        self._visible_pings = {
            idx: info
            for idx, info in self._visible_pings.items()
            if info["expires_tick"] >= curr_tick
        }
        state_dict["pings"] = {
            str(idx): info["ping_type"]
            for idx, info in self._visible_pings.items()
        }
        return state_dict


def build_ping_enabled_game_class(overcooked_game_cls):
    """
    app.py에서 OvercookedGame 대신 이 함수가 반환하는 클래스를 인스턴스화하면 됨:

        import game  # app.py가 이미 하고 있는 그 import
        from experiment.server_ext.ping_game import build_ping_enabled_game_class
        PingEnabledGame = build_ping_enabled_game_class(game.OvercookedGame)
        g = PingEnabledGame(...)  # 원본 OvercookedGame과 생성자 동일

    반드시 app.py가 **자기 자신이 import한 그 OvercookedGame 클래스**를 넘겨야
    한다 (직접 `from overcooked_demo.server.game import ...`처럼 이 함수 안에서
    따로 import하면 안 됨). 이유: app.py는 서버 디렉터리에서 `import game`으로
    불러오는데, 그 경로가 아닌 다른 경로(`overcooked_demo.server.game` 같은
    패키지 경로)로 같은 파일을 다시 import하면 파이썬이 완전히 별개의 모듈
    인스턴스를 또 만든다. game.py는 `game._configure(MAX_GAME_TIME, AGENT_DIR)`
    로 모듈 전역 변수를 채우는데, 그 설정이 app.py가 쓰는 모듈 인스턴스에만
    적용되고 우리가 새로 import한 인스턴스는 여전히 MAX_GAME_TIME=None인 채로
    남는다 — 실제로 이 버그로 게임 생성이 매번
    "TypeError('<' not supported between NoneType and int)"로 실패하는 걸
    실제 서버를 띄워서 확인했다(OvercookedGame.__init__의
    `min(int(gameTime), MAX_GAME_TIME)`). 그래서 이 함수는 이제 클래스를
    인자로 받아서, app.py가 쓰는 것과 항상 같은 모듈 인스턴스를 base로 쓴다.
    """

    class PingEnabledGame(PingMixin, overcooked_game_cls):
        def __init__(self, *args, **kwargs):
            super(PingEnabledGame, self).__init__(*args, **kwargs)
            self._ping_init()

    return PingEnabledGame
