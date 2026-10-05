"""
사람 입력 지연/중복 이동 개선 (2026-10-05).

증상: "방향키를 눌러도 반응이 늦어서 다시 누르게 되고, 그러면 2칸 이동한다."
로그(coordination_ring)로 확인: 사람 이동 입력이 거의 전부 길이 1~2짜리 토막
(1틱 79번, 2틱 49번, 3틱 이상 거의 없음) — 키를 '한 번 톡' 눌러도 2틱 이동이
자주 나왔다는 뜻.

원인(구 방식): 클라이언트가 "눌려 있는 동안 80ms마다 방향 문자열을 계속 전송"
하고 서버는 틱(~108ms)마다 1개씩 소비했다.
  1) 첫 입력도 다음 80ms 타이머가 돌 때까지 기다렸다가 전송(최대 +80ms).
  2) 100ms 정도 톡 눌러도 전송이 2번 나가 서버가 2번 이동(=한 번 눌렀는데 2칸).
  3) 큐가 1칸이라 SPACE(상호작용)가 직후에 온 방향키 입력에 덮여 사라질 수 있음.

새 방식: 클라이언트는 "주기적 반복 전송"이 아니라 키의 눌림/뗌 '사건'을 보낸다.
  KEY_DOWN_<방향>  : 눌렀다 (즉시 전송)  → 서버가 '탭 1회' 이벤트 + '누르는 중' 등록
  KEY_HOLD_<방향>  : 누르는 중 하트비트(0.25초마다; 뗌 신호가 유실돼도 0.7초 뒤 자동 해제)
  KEY_UP_<방향>/KEY_UP_ALL : 뗐다 / 창 포커스 잃음
  SPACE            : 상호작용(1회 이벤트; 순서 보존)
서버는 매 틱 다음 순서로 그 틱의 행동 1개를 정한다:
  대기 중인 1회 이벤트(탭/SPACE)가 있으면 그것 → 없으면 누르고 있는 방향 → 없으면 STAY.
결과: 톡 누르면 정확히 1칸, 누르고 있으면 매 틱 이동, 떼면 즉시 멈춤(밀린 입력 없음),
SPACE는 방향키에 덮이지 않음. 서버 틱 속도/게임 속도는 그대로(난이도 불변).
기존 방식('UP','LEFT' 등 문자열)도 계속 동작한다(tutorial.js/index.js 호환).
"""
import time
from collections import deque

KEY_DOWN_PREFIX = "KEY_DOWN_"
KEY_HOLD_PREFIX = "KEY_HOLD_"
KEY_UP_PREFIX = "KEY_UP_"
HOLD_TIMEOUT_SEC = 0.7      # 하트비트가 이만큼 끊기면 키를 뗀 것으로 간주
MAX_PENDING_EVENTS = 3      # 서버가 못 따라갈 때 이벤트가 무한히 쌓이지 않게
_DIRS = {"UP", "DOWN", "LEFT", "RIGHT"}


class HumanInputMixin:
    def _input_init(self):
        self._hi_events = {}   # player_id -> deque(["LEFT", "SPACE", ...])
        self._hi_held = {}     # player_id -> {방향: 마지막 갱신 시각} (삽입 순서 = 누른 순서)

    def _hi_state(self, player_id):
        if not hasattr(self, "_hi_events"):
            self._input_init()
        ev = self._hi_events.setdefault(player_id, deque(maxlen=MAX_PENDING_EVENTS))
        held = self._hi_held.setdefault(player_id, {})
        return ev, held

    def enqueue_action(self, player_id, action):
        if isinstance(action, str):
            if action.startswith("KEY_"):
                self._handle_key_event(player_id, action)
                return
            if action == "SPACE" and player_id in getattr(self, "human_players", ()):
                if self.is_active and player_id in self.players:
                    ev, _ = self._hi_state(player_id)
                    ev.append("SPACE")
                return
        super(HumanInputMixin, self).enqueue_action(player_id, action)

    def _handle_key_event(self, player_id, action):
        if not self.is_active or player_id not in self.players:
            return
        ev, held = self._hi_state(player_id)
        now = time.time()
        if action == KEY_UP_PREFIX + "ALL":
            held.clear()
            return
        for prefix in (KEY_DOWN_PREFIX, KEY_HOLD_PREFIX, KEY_UP_PREFIX):
            if action.startswith(prefix):
                direction = action[len(prefix):]
                break
        else:
            return
        if direction not in _DIRS:
            return
        if prefix == KEY_UP_PREFIX:
            held.pop(direction, None)
        elif prefix == KEY_DOWN_PREFIX:
            if direction in held:       # 이미 눌린 상태의 중복 DOWN은 탭으로 안 침
                held[direction] = now
            else:
                held[direction] = now
                ev.append(direction)    # 탭 1회
        else:  # HOLD 하트비트(DOWN을 놓쳤어도 누르는 중으로 복구; 탭은 만들지 않음)
            held[direction] = now

    def _hi_pick_action(self, player_id):
        ev, held = self._hi_state(player_id)
        if ev:
            return ev.popleft()
        now = time.time()
        for d in [d for d, t in held.items() if now - t > HOLD_TIMEOUT_SEC]:
            del held[d]
        if held:
            return list(held.keys())[-1]   # 가장 최근에 누른 방향
        return None

    def apply_actions(self):
        # 이번 틱에 사람이 할 행동 1개를 정해 기존 pending_actions 큐에 넣는다.
        # (이후는 원본 apply_actions 그대로: 틱당 human 1개 소비, 없으면 STAY)
        for pid in list(getattr(self, "human_players", ())):
            if pid not in self.players:
                continue
            name = self._hi_pick_action(pid)
            if name is None:
                continue
            idx = self.players.index(pid)
            q = self.pending_actions[idx]
            try:  # 구 방식 입력이 남아 있으면 이번 틱 결정으로 교체
                while True:
                    q.get_nowait()
            except Exception:
                pass
            super(HumanInputMixin, self).enqueue_action(pid, name)
        return super(HumanInputMixin, self).apply_actions()

    def clear_pending_actions(self):
        for ev in getattr(self, "_hi_events", {}).values():
            ev.clear()
        parent = super(HumanInputMixin, self)
        if hasattr(parent, "clear_pending_actions"):
            parent.clear_pending_actions()
