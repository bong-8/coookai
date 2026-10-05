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
      - 클라이언트가 "PING_HELP" / "PING_MOVE" / "PING_MINE" / "PING_OK" 같은
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

핑 타입 변경 이력(2026-10-04): "look"(이거 봐)은 행동 변화가 전혀 없어서
"핑을 보내도 아무 효과가 없다"는 피드백의 원인 중 하나였다. 그 자리를
"move"(비켜줘)로 교체했다 — 서로 길을 막았을 때 쓰는, 게임에 직접 영향을
주는 핑. 자세한 반응 로직은 role_restricted_bot.py의 PingReactiveBot
(_decide_move_aside_action 등) 참고. 교체일 뿐 종류 수는 그대로 4개.
"""
import time
from queue import Empty

PING_PREFIX = "PING_"
# help/mine은 예전 버튼(1,3번)이었고 지금 화면에서는 안 보내지만, 옛 로그/테스트와의
# 호환을 위해 서버는 계속 유효 처리한다. thanks/sorry는 사회적 핑(2026-10-05),
# welcome/fine은 봇이 되돌려 주는 답 말풍선 전용(클라이언트가 보내지는 않음).
VALID_PING_TYPES = {"help", "move", "mine", "ok", "thanks", "sorry"}
BOT_REPLY_BUBBLES = {"ok", "welcome", "fine"}

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
        # "봇/유저 속도 차이가 이전보다 더 벌어졌다"는 피드백(2026-10-04)으로
        # 발견한 버그 — 바로 전 수정(app.py의 buff_size=1)의 부작용이었다.
        #
        # buff_size=1만으로는 안 됐던 이유: game.py의 기본 enqueue_action은
        # queue.Queue.put(action)을 "블로킹"(기본값 block=True)으로 호출한다.
        # 클라이언트가 서버 틱(100ms)보다 빠르게(80ms) 보내므로, 큐가 이미
        # 차 있을 때 이 put()이 다음 틱이 비울 때까지 몇십 ms씩 그 요청을
        # 처리하던 소켓 이벤트 스레드(그린릿)를 그대로 블로킹한다. eventlet
        # 환경에서 한 커넥션의 이벤트는 보통 들어온 순서대로 처리되므로, 이
        # 핸들러가 블로킹된 동안 그 다음 입력들은 (파이썬 큐가 아니라)
        # 소켓/전송 계층 버퍼에 쌓인다 — 겉으로는 큐 길이를 1로 제한했지만
        # 실제로는 적체가 "한 칸 위(전송 계층)"로 옮겨간 것뿐이었고, 매 입력마다
        # 블로킹 자체가 추가 지연을 더해서 체감 속도 차이가 오히려 더
        # 나빠졌다.
        #
        # 고침: 블로킹 put() 대신, 큐가 이미 차 있으면 거기 든 "오래된"(아직
        # 처리 안 된) 액션을 먼저 버리고 새 액션을 넣는다 — "최신 입력이
        # 과거 입력보다 항상 더 중요하다"는 뜻이기도 하고(사람은 지금 누르고
        # 있는 방향키가 중요하지, 80ms 전에 누르고 있던 방향키가 아님), 이
        # put()은 그 즉시 성공하므로 블로킹이 전혀 없다. 큐 길이는 여전히
        # 항상 0~1로 유지된다(app.py의 buff_size=1과 같이 써야 동작 —
        # maxsize가 무제한(-1)이면 Queue.full()이 항상 False라 이 로직이
        # 무의미해진다).
        self._drop_stale_action_if_queue_full(player_id)
        super(PingMixin, self).enqueue_action(player_id, action)

    def _drop_stale_action_if_queue_full(self, player_id):
        if player_id not in self.players:
            return
        idx = self.players.index(player_id)
        q = self.pending_actions[idx]
        if q.full():
            try:
                q.get_nowait()
            except Empty:
                pass

    def _enqueue_ping(self, player_id, action):
        if not self.is_active or player_id not in self.players:
            return
        ping_type = action[len(PING_PREFIX):].lower()
        if ping_type not in VALID_PING_TYPES:
            # 알 수 없는 핑 타입은 조용히 무시 (원본처럼 KeyError로 게임을 죽이지 않음)
            return
        # "move"(비켜줘) 핑에 반응하려면 PingReactiveBot이 "보낸 사람이 지금
        # 어디 서 있는지"를 매 틱 state.players[sender_idx]로 조회해야 한다
        # (role_restricted_bot.py의 _decide_move_aside_action 참고). 그래서
        # player_id(소켓/플레이어 식별자) 말고 players 리스트에서의 정수
        # 인덱스도 같이 넘겨준다 — 봇 쪽에선 player_id 문자열이 무슨 뜻인지
        # 몰라도 되게.
        sender_idx = self.players.index(player_id)
        entry = {
            "player_id": player_id,
            "sender_idx": sender_idx,
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
        """PingReactiveBot(= ping_queue 속성을 가진 정책)에 방금 들어온 핑을 즉시 전달.

        "핑에 대해 반응이 전혀 없는 것 같다"는 피드백(2026-10-04) 원인은 사실
        두 가지가 섞여 있었다:
          1) _show_ping_on_screen()이 "보낸 사람" 머리 위에만 말풍선을 띄워서,
             받는 쪽(=상대 인간 참가자)은 자기 핑이 화면에 뜨는 건 보지만
             "봇이 그 핑을 들었다"는 걸 알 길이 전혀 없었다.
          2) 실제 행동 반응(role_restricted_bot.py의 _PING_RESPONSE_MAP)은
             REACTION_DELAY_STEPS만큼 지연 후에만 나타나므로, 반응이 있어도
             "봇이 지금 막 하던 동작을 계속하는 것"과 구분이 안 됐다.
        그래서 여기서 핑을 받는 "즉시"(지연 없이) 그 봇 캐릭터 머리 위에 OK
        말풍선을 띄운다 — 사람이 보낸 핑 하나당 "들었다"는 시각적 확인을
        먼저 주고, 실제 행동 변화(위 2번)는 기존 그대로 약간의 지연을 두고
        뒤따라온다. _enqueue_ping()의 full 파이프라인(= self._pending_pings에
        append해 trajectory로깅까지 가는 경로)을 타지 않고 _show_ping_on_screen()
        만 직접 호출하는 이유: 이건 실제로 "참가자가 보낸 핑"이 아니라 봇이
        합성해서 보여주는 수신확인 표시일 뿐이라, 핑 발생 횟수 등 사람 핑
        관련 연구 지표(trajectory의 "pings" 필드)에 끼어들면 안 되기 때문.
        """
        for bot_player_id, policy in getattr(self, "npc_policies", {}).items():
            if hasattr(policy, "ping_queue"):
                # (2026-10-05) 예전엔 여기서 즉시 OK 말풍선을 띄웠는데, "0.1초도
                # 안 돼서 OK가 뜨니 이해하고 반응한 게 아니라 OK부터 하고
                # 이해하는 느낌"이라는 피드백으로, 이제는 봇이 핑을 '이해'한
                # 시점(PING_ACK_DELAY_STEPS 뒤)에 tick()이 OK를 띄운다.
                policy.ping_queue.append(entry)

    def apply_actions(self):
        result = super(PingMixin, self).apply_actions()
        pings_this_tick, self._pending_pings = self._pending_pings, []
        if self.trajectory:
            self.trajectory[-1]["pings"] = pings_this_tick
        return result

    def tick(self):
        # PingReactiveBot의 반응 지연 계산용 스텝 카운터를 매 tick 갱신.
        for bot_player_id, policy in getattr(self, "npc_policies", {}).items():
            if hasattr(policy, "note_step"):
                policy.note_step()
            if hasattr(policy, "pop_ack") and policy.pop_ack():
                self._show_ping_on_screen(
                    bot_player_id, getattr(policy, "last_ack_kind", "ok"))
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

    # 주문 큐(order_queue.py)와 핑 채널(PingMixin)은 서로 독립적인 믹스인이다.
    # OrderQueueMixin을 앞에 둬서, activate() 때 원본 activate()가 mdp/state를
    # 새로 만든 "뒤에" 주문 목록을 초기화하고 mdp.deliver_soup을 감싼다.
    from experiment.server_ext.order_queue import OrderQueueMixin
    from experiment.server_ext.data_log import DataLogMixin
    from experiment.server_ext.human_input import HumanInputMixin

    class PingEnabledGame(HumanInputMixin, DataLogMixin, OrderQueueMixin, PingMixin, overcooked_game_cls):
        def __init__(self, *args, **kwargs):
            super(PingEnabledGame, self).__init__(*args, **kwargs)
            self._ping_init()
            self._orders_init()
            self._datalog_init()
            self._input_init()

    return PingEnabledGame
