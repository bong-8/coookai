"""가벼운 서버 실행기 — 새 PC(교수님 PC 등)에서 서버를 띄울 때 쓴다.

원본 서버(game.py)는 import 시점에 ray / human_aware_rl(rllib)을 요구하는데,
이 실험은 규칙 기반 봇(pickle)만 쓰므로 강화학습 스택이 필요 없다. 설치가
까다로운 ray/gym/tensorflow 대신, 없으면 빈 껍데기로 대체한 뒤 원본 app.py를
그대로 실행한다(저장소 코드는 수정하지 않음).

사용:  python experiment\\launch\\run_server.py
환경변수(선택): PORT(기본 5001), HOST(기본 0.0.0.0=다른 PC에서 접속 허용),
              OVERCOOKED_DATA_DIR(로그 저장 폴더)
"""
import os
import runpy
import socket
import sys
import types

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVER_DIR = os.path.join(ROOT, "src", "overcooked_demo", "server")
for p in (ROOT, os.path.join(ROOT, "src"), SERVER_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

# --- ray / rllib 대체 (설치돼 있으면 그대로 사용) ---
try:
    import ray  # noqa: F401
    from human_aware_rl.rllib.rllib import load_agent  # noqa: F401
except Exception:
    r = types.ModuleType("ray")
    r.is_initialized = lambda: False
    r.shutdown = lambda: None
    sys.modules["ray"] = r
    for name in ("human_aware_rl", "human_aware_rl.rllib", "human_aware_rl.rllib.rllib"):
        sys.modules[name] = types.ModuleType(name)

    def _no_rllib(*a, **k):
        raise RuntimeError("이 실행기에서는 RLlib 에이전트를 쓸 수 없습니다(규칙 기반 봇만 지원).")

    sys.modules["human_aware_rl.rllib.rllib"].load_agent = _no_rllib

os.environ.setdefault("PORT", "5001")
os.environ.setdefault("HOST", "0.0.0.0")
os.environ.setdefault("FLASK_ENV", "production")
port = os.environ["PORT"]


def lan_ips():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


def warm_up_planners():
    """실험 5개 난이도의 경로/행동 계획 캐시(data/planners/*.pkl)를 미리 만든다.

    이 캐시가 없으면 서버가 그 난이도에 처음 진입하는 순간(라운드 전환 시)
    계산을 시작하고, 계산이 끝날 때까지(난이도에 따라 수십 초~수 분) 서버
    전체가 멈춰 모든 접속자의 화면이 얼어붙는다. 새 PC에서는 캐시가 없으므로
    서버를 열기 전에 한 번 만들어 둔다(이후 실행에서는 즉시 끝남).
    """
    import json
    import time
    from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld
    from overcooked_ai_py.planning.planners import (
        MediumLevelActionManager, MotionPlanner, NO_COUNTERS_PARAMS)
    from experiment.agents.role_restricted_bot import build_mlam_params

    with open(os.path.join(SERVER_DIR, "config.json"), encoding="utf-8") as f:
        layouts = json.load(f)["predefined"]["experimentParams"]["layouts"]
    print("[준비] 난이도별 계산 캐시 확인 중 (새 PC는 처음 한 번 수 분 걸릴 수 있음)", flush=True)
    for name in layouts:
        t = time.time()
        mdp = OvercookedGridworld.from_layout_name(name)
        MotionPlanner.from_pickle_or_compute(mdp, counter_goals=NO_COUNTERS_PARAMS)
        MediumLevelActionManager.from_pickle_or_compute(mdp, build_mlam_params(mdp))
        print("  - %-28s 준비됨 (%.1f초)" % (name, time.time() - t), flush=True)


warm_up_planners()

print("=" * 60)
print(" 서버 시작. 이 PC에서:  http://127.0.0.1:%s/predefined" % port)
for ip in lan_ips():
    print(" 같은 와이파이의 다른 PC에서:  http://%s:%s/predefined" % (ip, port))
print(" (Windows 방화벽 창이 뜨면 '허용'. 종료는 Ctrl+C)")
print("=" * 60, flush=True)

os.chdir(SERVER_DIR)
runpy.run_path(os.path.join(SERVER_DIR, "app.py"), run_name="__main__")
