"""
게임 로그 저장 — 닉네임 기록 + 저장 경로 설정 (2026-10-05).

원본 game.py의 OvercookedGame.get_data()는 라운드(난이도)가 끝날 때마다
trajectory를 {"uid", "trajectory"} 딕셔너리로 만들어 반환하면서(그 즉시
self.trajectory를 비움) dataCollection이 켜져 있으면
    /app/data/<레이아웃>/<New|Old>/<HH|HA|..>/<시작시각>/result.pkl
에 pickle로 저장한다. 두 가지가 실험에 맞지 않았다:
  1) 경로의 "/app/data"가 Docker 볼륨 전용으로 하드코딩돼 있다(utils.py의
     DOCKER_VOLUME). 로컬(Windows)에서 돌리면 프로젝트 폴더가 아니라 현재
     드라이브 루트(C:\\app\\data 등)에 쌓인다.
  2) 참가자를 식별할 정보가 경로에도 내용에도 없다.

원본 파일은 수정하지 않고(=이 폴더가 "실험 개입 전체"라는 원칙 유지) 이
믹스인이 get_data()를 오버라이드한다 — 저장 로직만 새로 쓰고 반환값의 형태는
원본과 같다(+ nicknames/meta 필드 추가라 기존 소비자(app.py의 end_game/
reset_game 이벤트, predefined.js의 recordRoundResult)는 그대로 동작).

저장 위치(우선순위):
  1) 환경변수 OVERCOOKED_DATA_DIR
  2) <저장소 루트>/data/game_logs   (기본값; .gitignore에 등록돼 git에 안 올라감)

저장 구조:
  <DATA_DIR>/<세션시작시각>_<HH|HA..>_<닉네임[+닉네임2]>/round<N>_<레이아웃>.pkl
  <DATA_DIR>/index.csv   (라운드마다 한 줄: 어디에 뭐가 저장됐는지 한눈에)
각 pkl의 내용: {"uid", "trajectory", "nicknames": {"0": ..., "1": ...}, "meta": {...}}
"""
import csv
import os
import pickle
import re
import time
from datetime import datetime
from pathlib import Path

MAX_NICKNAME_LEN = 20
ENV_DATA_DIR = "OVERCOOKED_DATA_DIR"
_REPO_ROOT = Path(__file__).resolve().parents[2]


def resolve_log_dir():
    """호출 시점마다 다시 읽는다(테스트/환경변수 변경을 바로 반영)."""
    env = os.environ.get(ENV_DATA_DIR)
    if env:
        return str(Path(env).expanduser())
    return str(_REPO_ROOT / "data" / "game_logs")


def sanitize_nickname(raw):
    """사용자가 입력한 닉네임 정리: 앞뒤 공백 제거, 제어문자 제거, 길이 제한.
    비어 있으면 None. (표시/로그용 원본 — 파일명용은 safe_filename_part 참고)"""
    if raw is None:
        return None
    nick = re.sub(r"[\x00-\x1f\x7f]", "", str(raw)).strip()
    nick = nick[:MAX_NICKNAME_LEN]
    return nick or None


def safe_filename_part(text):
    """파일/폴더 이름에 써도 안전한 형태로(한글/영문/숫자/_-.만 유지)."""
    cleaned = re.sub(r"[^\w\-.]+", "_", str(text), flags=re.UNICODE).strip("._")
    return cleaned or "anonymous"


def _dump_with_retry(data, path, attempts=6, wait=0.5):
    path = Path(path)
    last = None
    candidates = [path, path.with_name(path.stem + f"_retry{int(time.time())}" + path.suffix)]
    for target in candidates:
        for _ in range(attempts):
            try:
                with open(target, "wb") as f:
                    pickle.dump(data, f)
                return target
            except PermissionError as e:
                last = e
                time.sleep(wait)
    raise last


class DataLogMixin:
    """OvercookedGame(혹은 계약을 만족하는 클래스)에 믹스인. 필요한 속성:
    players, human_players, trajectory, write_data, write_config(옵션),
    curr_layout, max_time(옵션)."""

    def _datalog_init(self):
        self._nicknames = {}  # player_id(소켓 sid) -> 닉네임
        self._session_started = None
        self._round_idx = 0

    def set_nickname(self, player_id, nickname):
        nick = sanitize_nickname(nickname)
        if nick:
            self._nicknames[player_id] = nick

    def activate(self):
        super(DataLogMixin, self).activate()
        if self._session_started is None:
            self._session_started = datetime.now().strftime("%Y%m%d_%H%M%S")

    def deactivate(self):
        # 참가자가 중간에 나가거나 탭을 닫으면(원본은 이때 get_data를 안 불러
        # 진행하던 라운드 기록이 통째로 사라졌다) 남아 있는 trajectory를 "중도
        # 종료(partial)" 표시로 저장한다. 정상 종료 때는 이미 get_data가
        # trajectory를 비운 뒤라 중복 저장되지 않는다.
        if getattr(self, "trajectory", None):
            self._partial_round = True
            try:
                self.get_data()
            except Exception as e:
                print(f"[data_log] 중도 종료 라운드 저장 실패: {e!r}")
            finally:
                self._partial_round = False
        super(DataLogMixin, self).deactivate()

    def _nicknames_by_index(self):
        out = {}
        for idx, pid in enumerate(self.players):
            if pid in self._nicknames:
                out[str(idx)] = self._nicknames[pid]
            elif pid in getattr(self, "human_players", set()):
                out[str(idx)] = "anonymous"
            else:
                out[str(idx)] = "AI_BOT"
        return out

    def _log_meta(self, layout):
        meta = {
            "layout": layout,
            "round": self._round_idx,
            "session_started": self._session_started,
            "players": list(self.players),
            "game_time_sec": getattr(self, "max_time", None),
            "partial": bool(getattr(self, "_partial_round", False)),
        }
        cfg = getattr(self, "write_config", None) or {}
        meta["game_type"] = cfg.get("type")
        # 재현에 필요한 실험 파라미터(코드에 박혀 있는 값들)를 같이 남긴다.
        try:
            from experiment.agents.role_restricted_bot import BOT_SPEED_DIVISOR
            meta["bot_speed_divisor"] = BOT_SPEED_DIVISOR
        except Exception:
            pass
        try:
            from experiment.server_ext.order_queue import (
                ORDER_ARRIVAL_INTERVAL_SEC, INITIAL_ORDER_COUNT,
            )
            meta["order_arrival_interval_sec"] = ORDER_ARRIVAL_INTERVAL_SEC
            meta["initial_order_count"] = INITIAL_ORDER_COUNT
        except Exception:
            pass
        return meta

    def get_data(self):
        """원본과 같은 계약: 누적된 trajectory를 반환하고 비운다."""
        trajectory = self.trajectory
        self.trajectory = []
        data = {"uid": str(time.time()), "trajectory": trajectory}
        if not trajectory:
            return data

        self._round_idx += 1
        layout = trajectory[-1].get("layout_name") or getattr(self, "curr_layout", "?")
        data["nicknames"] = self._nicknames_by_index()
        data["meta"] = self._log_meta(layout)

        if getattr(self, "write_data", False):
            try:
                self._write_round(data, layout)
            except Exception as e:  # 로그 저장 실패가 게임을 죽이면 안 된다
                print(f"[data_log] 저장 실패(게임은 계속 진행): {e!r}")
        return data

    def _write_round(self, data, layout):
        base = Path(resolve_log_dir())
        nick_part = "+".join(
            safe_filename_part(n) for n in data["nicknames"].values() if n != "AI_BOT"
        ) or "anonymous"
        game_type = data["meta"].get("game_type") or "NA"
        session_dir = base / f"{self._session_started}_{game_type}_{nick_part}"
        session_dir.mkdir(parents=True, exist_ok=True)
        path = session_dir / f"round{self._round_idx}_{safe_filename_part(layout)}.pkl"

        # (2026-10-05) 처음엔 "임시 파일에 쓰고 os.replace로 교체"였는데, Windows에서
        # 문서 폴더(OneDrive 동기화/백신 실시간 검사)가 방금 만든 파일을 잠깐
        # 붙잡아 os.replace가 PermissionError(13, "다른 프로세스가 파일을 사용
        # 중")로 실패했고 빈 .tmp만 남았다. 그래서 최종 경로에 바로 쓰고, 잠겨
        # 있으면 잠깐 기다렸다 재시도한다. 끝까지 안 되면 같은 폴더에 다른
        # 이름으로 한 번 더 시도(데이터를 잃는 것보다 파일명이 달라지는 게 낫다).
        _dump_with_retry(data, path)

        self._append_index(base, data, layout, path)

    def _append_index(self, base, data, layout, path):
        index_path = base / "index.csv"
        new_file = not index_path.exists()
        last = data["trajectory"][-1]
        for _ in range(6):
            try:
                f = open(index_path, "a", newline="", encoding="utf-8-sig")
                break
            except PermissionError:  # 엑셀로 열어둔 경우 등
                time.sleep(0.5)
        else:
            print("[data_log] index.csv가 다른 프로그램에서 열려 있어 이번 줄은 기록하지 못했습니다(pkl은 저장됨).")
            return
        with f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["saved_at", "session_started", "game_type", "round",
                            "layout", "nicknames", "final_score", "steps", "partial", "file"])
            w.writerow([
                datetime.now().isoformat(timespec="seconds"),
                self._session_started,
                data["meta"].get("game_type"),
                self._round_idx,
                layout,
                "|".join(data["nicknames"].values()),
                last.get("score"),
                len(data["trajectory"]),
                int(data["meta"].get("partial", False)),
                str(path),
            ])
