"""
자동 가이드 믹스인 — 일시정지 + 대본 출력 + Enter 재개 (2026-10-21).

원본 game.py 는 건드리지 않는다(이 파일이 "실험 개입"의 일부).

흐름:
  1) 매 틱 시작 때 트리거 판정(guide_engine.TriggerEngine).
  2) 트리거되면 게임을 멈춘다 — tick() 이 원본 tick 을 부르지 않고 바로 ACTIVE 를 돌려준다.
     (게임 틱·남은 시간·봇이 멈춘다. 서버가 봇 행동을 기다리다 얼어붙는 문제도 없다:
      apply_actions 자체가 안 불리므로 큐를 기다리는 일이 없다.)
  3) 문장 제공자(ScriptProvider/LLMProvider)가 문장을 만든다. LLM 이 느려도 게임은
     이미 멈춰 있으니 문제가 없다. 제한 시간이 넘거나 검사에 실패하면 대본 문장으로 대체.
  4) 클라이언트가 state_pong 의 "guide" 필드로 오버레이+TTS 를 띄운다.
  5) 참가자가 Enter → 'GUIDE_CONTINUE' → (여러 쪽이면 다음 쪽) → 재개.
     재개할 때 start_time 을 멈춘 시간만큼 밀어, 남은 시간이 멈춘 동안 줄지 않게 한다
     (원본은 time() - start_time 으로 남은 시간을 계산한다).

안전장치: 가이드 코드에서 예외가 나면 그 게임의 가이드만 끄고 게임은 계속한다
(tick 에서 예외가 나면 play_game 루프가 죽어 게임이 멈추기 때문).

로그: 재개 직후 첫 transition 에 "guide_events" 필드로 붙는다(핑의 "pings" 와 같은 방식).
"""
import threading
import time

from experiment.server_ext.guide_engine import (
    ScriptProvider,
    LLMProvider,
    TriggerEngine,
    load_script,
    make_snapshot,
)

GUIDE_CONTINUE = "GUIDE_CONTINUE"
_BLOCKED_WHILE_PAUSED_PREFIXES = ("KEY_", "PING_")
_BLOCKED_WHILE_PAUSED_EXACT = {"SPACE", "UP", "DOWN", "LEFT", "RIGHT", "STAY"}

# LLM 문장 생성 클라이언트: callable(prompt, timeout) -> str. 실제 API 연결은 이 함수로 주입한다.
# 주입 안 하면 "llm" 모드도 대본 문장으로 동작한다(로그에 requested_mode="llm", fallback 기록).
_LLM_CLIENT = None


def set_llm_client(client):
    global _LLM_CLIENT
    _LLM_CLIENT = client


def _now():
    return time.time()


class GuideMixin:
    # ── 초기화 ────────────────────────────────────────────────────
    def _guide_init(self, guide=None, script_path=None):
        """guide: None/""/"none" → 끔, "script" → 대본, "llm" → LLM 문장(없으면 대본)."""
        mode = guide if isinstance(guide, str) else None
        self._guide_enabled = mode in ("script", "llm")
        self._guide_requested_mode = mode
        self._guide_round = 0
        self._guide_status = "idle"   # idle | loading | ready
        self._guide_events_pending = []
        self._guide_lock = threading.Lock()
        self._guide_cur = None
        if not self._guide_enabled:
            return
        import os
        script = load_script(script_path or os.environ.get("OVERCOOKED_GUIDE_SCRIPT"))
        self._guide_script = script
        self._guide_engine = TriggerEngine(script)
        self._guide_script_provider = ScriptProvider()
        if mode == "llm" and _LLM_CLIENT is not None:
            self._guide_provider = LLMProvider(
                _LLM_CLIENT, timeout=script["settings"]["llm_timeout_sec"])
        else:
            self._guide_provider = self._guide_script_provider

    # ── 입력 ──────────────────────────────────────────────────────
    def enqueue_action(self, player_id, action):
        if getattr(self, "_guide_enabled", False) and isinstance(action, str):
            if action == GUIDE_CONTINUE:
                self._guide_on_continue()
                return
            if self._guide_status != "idle":
                # 멈춰 있는 동안의 조작·핑은 버린다(재개 뒤 한꺼번에 반영되지 않게).
                if (action.startswith(_BLOCKED_WHILE_PAUSED_PREFIXES)
                        or action in _BLOCKED_WHILE_PAUSED_EXACT):
                    return
        super(GuideMixin, self).enqueue_action(player_id, action)

    # ── 틱 ────────────────────────────────────────────────────────
    def tick(self):
        if not getattr(self, "_guide_enabled", False) or not self.is_active:
            return super(GuideMixin, self).tick()
        try:
            if self._guide_status != "idle":
                self._guide_poll()
                return self.Status.ACTIVE
            if self._guide_check_triggers():
                return self.Status.ACTIVE
        except Exception as e:  # 가이드 때문에 게임이 멈추면 안 된다
            self._guide_disable(e)
        return super(GuideMixin, self).tick()

    def _guide_disable(self, err):
        print(f"[guide] 오류로 이 게임의 가이드를 끕니다: {err!r}")
        with self._guide_lock:
            self._guide_enabled = False
            was_paused = self._guide_status != "idle"
            self._guide_status = "idle"
            self._guide_cur = None
        if was_paused and getattr(self, "_guide_pause_t0", None) is not None:
            self._guide_shift_clock()
        self._guide_events_pending.append(
            {"type": "guide_disabled", "error": repr(err), "tick": getattr(self, "curr_tick", None)})

    # ── 트리거 → 정지 ────────────────────────────────────────────
    def _guide_check_triggers(self):
        now = _now()
        snap = make_snapshot(self, now)
        if snap is None:
            return False
        step = self._guide_engine.evaluate(snap)
        if step is None:
            return False
        self._guide_engine.mark_fired(step, snap)
        self._guide_begin(step, snap, now)
        return True

    def _guide_begin(self, step, snap, now):
        self._guide_clear_input()
        self._guide_pause_t0 = now
        self._guide_cur = {
            "step": step, "snap": snap, "page": 0, "pages": None, "seq": 0,
            "ready_t": None, "source": None, "fallback": False, "fallback_reason": None,
            "render_started": now, "latency": None, "result": None, "deadline": None,
            "continue_times": [],
        }
        self._guide_status = "loading"
        provider = self._guide_provider
        if getattr(provider, "is_instant", False):
            self._guide_finish_render(provider.render(step, snap), now)
            return
        timeout = self._guide_script["settings"]["llm_timeout_sec"]
        holder = self._guide_cur
        holder["deadline"] = now + timeout + 0.5

        def work():
            try:
                res = provider.render(step, snap)
            except Exception as e:
                res = {"error": repr(e)}
            holder["result"] = res

        threading.Thread(target=work, daemon=True).start()

    def _guide_poll(self):
        """loading 상태에서 LLM 결과가 왔는지/시간이 넘었는지 본다."""
        cur = self._guide_cur
        if self._guide_status != "loading" or cur is None:
            return
        now = _now()
        res = cur.get("result")
        if res is None:
            if cur["deadline"] is not None and now > cur["deadline"]:
                res = {"error": "timeout"}
            else:
                return
        if "error" in res:
            res = self._guide_script_provider.fallback(cur["step"], cur["snap"], res["error"])
        self._guide_finish_render(res, now)

    def _guide_finish_render(self, res, now):
        cur = self._guide_cur
        with self._guide_lock:
            cur["pages"] = res["pages"]
            cur["source"] = res.get("source", "script")
            cur["fallback"] = bool(res.get("fallback"))
            cur["fallback_reason"] = res.get("fallback_reason")
            cur["latency"] = round(now - cur["render_started"], 3)
            cur["ready_t"] = now
            cur["seq"] += 1
            self._guide_status = "ready"

    # ── Enter → 다음 쪽 / 재개 ────────────────────────────────────
    def _guide_on_continue(self):
        with self._guide_lock:
            cur = self._guide_cur
            if self._guide_status != "ready" or cur is None:
                return
            now = _now()
            if now - cur["ready_t"] < self._guide_script["settings"]["min_display_sec"]:
                return
            cur["continue_times"].append(round(now - cur["ready_t"], 3))
            if cur["page"] + 1 < len(cur["pages"]):
                cur["page"] += 1
                cur["ready_t"] = now
                cur["seq"] += 1
                return
        self._guide_resume(now)

    def _guide_shift_clock(self, now=None):
        """멈춘 시간만큼 start_time 을 민다 → 남은 시간이 멈춘 동안 줄지 않는다."""
        if getattr(self, "start_time", None) is not None:
            self.start_time += (_now() if now is None else now) - self._guide_pause_t0

    def _guide_resume(self, now):
        cur = self._guide_cur
        paused = now - self._guide_pause_t0
        self._guide_shift_clock(now)
        self._guide_clear_input()
        self._guide_events_pending.append({
            "type": "guide_shown",
            "id": cur["step"]["id"],
            "round": cur["snap"]["round"],
            "layout": cur["snap"]["layout"],
            "tick": cur["snap"]["tick"],
            "requested_mode": self._guide_requested_mode,
            "source": cur["source"],
            "fallback": cur["fallback"],
            "fallback_reason": cur["fallback_reason"],
            "render_latency_sec": cur["latency"],
            "paused_sec": round(paused, 3),
            "pages": cur["pages"],
            "continue_after_sec": cur["continue_times"],
        })
        with self._guide_lock:
            self._guide_status = "idle"
            self._guide_cur = None

    def _guide_clear_input(self):
        """멈추고/재개할 때 쌓인 사람 입력을 비운다(눌린 키가 재개 직후 튀지 않게)."""
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

    # ── 라운드 전환/종료 ───────────────────────────────────────────
    def activate(self):
        super(GuideMixin, self).activate()
        if getattr(self, "_guide_enabled", False):
            self._guide_round += 1
            self._guide_status = "idle"
            self._guide_cur = None
            self._guide_engine.on_round_start()

    def deactivate(self):
        if getattr(self, "_guide_enabled", False):
            self._guide_status = "idle"
            self._guide_cur = None
        super(GuideMixin, self).deactivate()

    # ── 화면/로그 ─────────────────────────────────────────────────
    def get_state(self):
        state_dict = super(GuideMixin, self).get_state()
        if not getattr(self, "_guide_enabled", False):
            return state_dict
        cur = self._guide_cur
        if self._guide_status == "idle" or cur is None:
            state_dict["guide"] = None
            return state_dict
        # 멈춰 있는 동안 남은 시간이 줄어 보이지 않게 고정
        if "time_left" in state_dict and getattr(self, "start_time", None) is not None:
            state_dict["time_left"] = max(
                self.max_time - (self._guide_pause_t0 - self.start_time), 0)
        ready = self._guide_status == "ready"
        min_disp = self._guide_script["settings"]["min_display_sec"]
        state_dict["guide"] = {
            "id": cur["step"]["id"],
            "status": self._guide_status,
            "text": cur["pages"][cur["page"]] if ready else None,
            "page": cur["page"] + 1,
            "n_pages": len(cur["pages"]) if ready else len(cur["step"]["pages"]),
            "seq": cur["seq"],
            "can_continue": bool(ready and _now() - cur["ready_t"] >= min_disp),
        }
        return state_dict

    def apply_actions(self):
        result = super(GuideMixin, self).apply_actions()
        pending = getattr(self, "_guide_events_pending", None)
        if pending and getattr(self, "trajectory", None):
            self.trajectory[-1]["guide_events"] = pending
            self._guide_events_pending = []
        return result
