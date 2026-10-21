"""
전문가용 일시정지 믹스인 (2026-10-21) — 사람-사람 훈련 조건에서 "설명하려고 멈추는" 기능.

  - 두 사람 중 누구든 ` (백틱) 키를 누르면 게임이 멈추고, 다시 누르면 풀린다.
  - 멈춰 있는 동안: 게임 틱·남은 시간·봇이 모두 멈춘다(guide_game.py 와 같은 방식 — tick() 이 원본 tick 을 안 부른다).
    조작/핑 입력은 버린다. 풀 때 start_time 을 멈춘 시간만큼 밀어 남은 시간이 줄지 않게 한다.
  - 화면: get_state() 의 "paused": true 를 보고 클라이언트(pause.js)가 반투명 PAUSE 글씨를 띄운다.
  - 로그: 풀린 직후 첫 transition 에 "pause_events" 로 {누가, 몇 틱에, 몇 초 멈췄는지}가 붙는다.
  - 켜기: game 생성 인자 expert_pause=True (predefined.js 가 "다른 참가자와 연습" 조건에서 보낸다).
    꺼져 있는 게임에서는 키 입력이 와도 조용히 무시한다.

원본 game.py 는 건드리지 않는다.
"""
import time

PAUSE_TOGGLE = "PAUSE_TOGGLE"
_BLOCKED_PREFIXES = ("KEY_", "PING_")
_BLOCKED_EXACT = {"SPACE", "UP", "DOWN", "LEFT", "RIGHT", "STAY"}


def _now():
    return time.time()


class ExpertPauseMixin:
    def _pause_init(self, enabled=False):
        self._pause_enabled = bool(enabled)
        self._paused = False
        self._pause_t0 = None
        self._pause_by = None
        self._pause_events_pending = []

    # ── 입력 ──────────────────────────────────────────────────────
    def enqueue_action(self, player_id, action):
        if isinstance(action, str):
            if action == PAUSE_TOGGLE:
                if getattr(self, "_pause_enabled", False) and self.is_active \
                        and player_id in getattr(self, "human_players", ()):
                    self._pause_toggle(player_id)
                return  # 켜져 있든 아니든 이 신호는 원본으로 넘기지 않는다
            if getattr(self, "_paused", False):
                if action.startswith(_BLOCKED_PREFIXES) or action in _BLOCKED_EXACT:
                    return
        super(ExpertPauseMixin, self).enqueue_action(player_id, action)

    def _pause_toggle(self, player_id):
        now = _now()
        if not self._paused:
            self._paused = True
            self._pause_t0 = now
            self._pause_by = player_id
            self._pause_clear_input()
        else:
            paused_sec = now - self._pause_t0
            if getattr(self, "start_time", None) is not None:
                self.start_time += paused_sec
            self._pause_clear_input()
            self._pause_events_pending.append({
                "type": "expert_pause",
                "paused_by": self._pause_by,
                "resumed_by": player_id,
                "tick": getattr(self, "curr_tick", None),
                "layout": getattr(self, "curr_layout", None),
                "paused_sec": round(paused_sec, 3),
            })
            self._paused = False
            self._pause_t0 = None
            self._pause_by = None

    def _pause_clear_input(self):
        """멈추고/풀 때 쌓인 입력을 비운다(눌린 키가 풀린 직후 튀지 않게)."""
        for d in (getattr(self, "_hi_events", None), getattr(self, "_hi_held", None)):
            if d:
                for v in d.values():
                    v.clear()
        for pid in getattr(self, "human_players", ()):
            if pid in self.players:
                q = self.pending_actions[self.players.index(pid)]
                try:
                    while True:
                        q.get_nowait()
                except Exception:
                    pass

    # ── 틱 ────────────────────────────────────────────────────────
    def tick(self):
        if getattr(self, "_paused", False) and self.is_active:
            return self.Status.ACTIVE      # 원본 tick 을 부르지 않는다 = 게임 시간/틱/봇 정지
        return super(ExpertPauseMixin, self).tick()

    # ── 라운드 전환/종료 ───────────────────────────────────────────
    def activate(self):
        super(ExpertPauseMixin, self).activate()
        self._paused = False
        self._pause_t0 = None
        self._pause_by = None

    def deactivate(self):
        self._paused = False
        self._pause_t0 = None
        super(ExpertPauseMixin, self).deactivate()

    # ── 화면/로그 ─────────────────────────────────────────────────
    def get_state(self):
        state = super(ExpertPauseMixin, self).get_state()
        if not getattr(self, "_pause_enabled", False):
            return state
        state["paused"] = bool(self._paused)
        if self._paused and "time_left" in state and getattr(self, "start_time", None) is not None \
                and self._pause_t0 is not None:
            state["time_left"] = max(self.max_time - (self._pause_t0 - self.start_time), 0)
        return state

    def apply_actions(self):
        result = super(ExpertPauseMixin, self).apply_actions()
        pending = getattr(self, "_pause_events_pending", None)
        if pending and getattr(self, "trajectory", None):
            self.trajectory[-1]["pause_events"] = pending
            self._pause_events_pending = []
        return result
