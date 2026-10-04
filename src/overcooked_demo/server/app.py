import os
import sys

# Import and patch the production eventlet server if necessary
if os.getenv("FLASK_ENV", "production") == "production":
    import eventlet

    eventlet.monkey_patch()

import atexit
import json
import logging

# All other imports must come after patch to ensure eventlet compatibility
import pickle
import queue
from datetime import datetime
from threading import Lock

import game
from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from game import Game, OvercookedGame, OvercookedTutorial
from utils import ThreadSafeDict, ThreadSafeSet

# Phase 2 (핑 소통 채널, experiment/server_ext/ping_game.py): 원본 OvercookedGame
# 대신 핑 채널이 믹스인된 클래스를 쓰도록 GAME_NAME_TO_CLS에서만 교체한다.
# 원본 game.py/app.py의 나머지 로직은 그대로다 — 이 두 줄이 실험을 위한 개입의 전부.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from experiment.server_ext.ping_game import build_ping_enabled_game_class

# OvercookedGame(위에서 이미 import한 바로 그 클래스)을 넘긴다. ping_game.py의
# build_ping_enabled_game_class() 자체가 이 함수 안에서 game.py를 다시
# import하면 안 되는 이유(별도 모듈 인스턴스 → MAX_GAME_TIME=None 버그)를
# 설명해뒀다 — 실제로 서버를 띄워서 재현하고 고친 버그다.
PingEnabledGame = build_ping_enabled_game_class(OvercookedGame)

### Thoughts -- where I'll log potential issues/ideas as they come up
# Should make game driver code more error robust -- if overcooked randomlly errors we should catch it and report it to user
# Right now, if one user 'join's before other user's 'join' finishes, they won't end up in same game
# Could use a monitor on a conditional to block all global ops during calls to _ensure_consistent_state for debugging
# Could cap number of sinlge- and multi-player games separately since the latter has much higher RAM and CPU usage

###########
# Globals #
###########

# Read in global config
CONF_PATH = os.getenv("CONF_PATH", "config.json")
with open(CONF_PATH, "r") as f:
    CONFIG = json.load(f)

# Where errors will be logged
LOGFILE = CONFIG["logfile"]

# Available layout names
LAYOUTS = CONFIG["layouts"]

# Values that are standard across layouts
LAYOUT_GLOBALS = CONFIG["layout_globals"]

# Maximum allowable game length (in seconds)
MAX_GAME_LENGTH = CONFIG["MAX_GAME_LENGTH"]

# Path to where pre-trained agents will be stored on server
AGENT_DIR = CONFIG["AGENT_DIR"]

# Maximum number of games that can run concurrently. Contrained by available memory and CPU
MAX_GAMES = CONFIG["MAX_GAMES"]

# Frames per second cap for serving to client
MAX_FPS = CONFIG["MAX_FPS"]

# Default configuration for predefined experiment
PREDEFINED_CONFIG = json.dumps(CONFIG["predefined"])

# Default configuration for tutorial
TUTORIAL_CONFIG = json.dumps(CONFIG["tutorial"])

# Global queue of available IDs. This is how we synch game creation and keep track of how many games are in memory
FREE_IDS = queue.Queue(maxsize=MAX_GAMES)

# Bitmap that indicates whether ID is currently in use. Game with ID=i is "freed" by setting FREE_MAP[i] = True
FREE_MAP = ThreadSafeDict()

# Initialize our ID tracking data
for i in range(MAX_GAMES):
    FREE_IDS.put(i)
    FREE_MAP[i] = True

# Mapping of game-id to game objects
GAMES = ThreadSafeDict()

# Set of games IDs that are currently being played
ACTIVE_GAMES = ThreadSafeSet()

# Queue of games IDs that are waiting for additional players to join. Note that some of these IDs might
# be stale (i.e. if FREE_MAP[id] = True)
WAITING_GAMES = queue.Queue()

# Mapping of users to locks associated with the ID. Enforces user-level serialization
USERS = ThreadSafeDict()

# Mapping of user id's to the current game (room) they are in
USER_ROOMS = ThreadSafeDict()

# Mapping of string game names to corresponding classes
GAME_NAME_TO_CLS = {
    "overcooked": PingEnabledGame,  # Phase 2: 핑 채널 포함 (원본 OvercookedGame 상속)
    "tutorial": OvercookedTutorial,
}

game._configure(MAX_GAME_LENGTH, AGENT_DIR)


#######################
# Flask Configuration #
#######################

# Create and configure flask app
app = Flask(__name__, template_folder=os.path.join("static", "templates"))
app.config["DEBUG"] = os.getenv("FLASK_ENV", "production") == "development"
socketio = SocketIO(app, cors_allowed_origins="*", logger=app.config["DEBUG"])


# Attach handler for logging errors to file
handler = logging.FileHandler(LOGFILE)
handler.setLevel(logging.ERROR)
app.logger.addHandler(handler)


#################################
# Global Coordination Functions #
#################################


def try_create_game(game_name, **kwargs):
    """
    Tries to create a brand new Game object based on parameters in `kwargs`

    Returns (Game, Error) that represent a pointer to a game object, and error that occured
    during creation, if any. In case of error, `Game` returned in None. In case of sucess,
    `Error` returned is None

    Possible Errors:
        - Runtime error if server is at max game capacity
        - Propogate any error that occured in game __init__ function
    """
    try:
        curr_id = FREE_IDS.get(block=False)
        assert FREE_MAP[curr_id], "Current id is already in use"
        game_cls = GAME_NAME_TO_CLS.get(game_name, OvercookedGame)
        game = game_cls(id=curr_id, **kwargs)
    except queue.Empty:
        err = RuntimeError("Server at max capacity")
        return None, err
    except Exception as e:
        return None, e
    else:
        GAMES[game.id] = game
        FREE_MAP[game.id] = False
        return game, None


def cleanup_game(game: OvercookedGame):
    if FREE_MAP[game.id]:
        raise ValueError("Double free on a game")

    # User tracking
    for user_id in game.players:
        leave_curr_room(user_id)

    # Socketio tracking
    socketio.close_room(game.id)
    # Game tracking
    FREE_MAP[game.id] = True
    FREE_IDS.put(game.id)
    del GAMES[game.id]

    if game.id in ACTIVE_GAMES:
        ACTIVE_GAMES.remove(game.id)


def get_game(game_id):
    return GAMES.get(game_id, None)


def get_curr_game(user_id):
    return get_game(get_curr_room(user_id))


def get_curr_room(user_id):
    return USER_ROOMS.get(user_id, None)


def set_curr_room(user_id, room_id):
    USER_ROOMS[user_id] = room_id


def leave_curr_room(user_id):
    del USER_ROOMS[user_id]


def get_waiting_game():
    """
    Return a pointer to a waiting game, if one exists

    Note: The use of a queue ensures that no two threads will ever receive the same pointer, unless
    the waiting game's ID is re-added to the WAITING_GAMES queue
    """
    try:
        waiting_id = WAITING_GAMES.get(block=False)
        while FREE_MAP[waiting_id]:
            waiting_id = WAITING_GAMES.get(block=False)
    except queue.Empty:
        return None
    else:
        return get_game(waiting_id)


##########################
# Socket Handler Helpers #
##########################


def _leave_game(user_id):
    """
    Removes `user_id` from it's current game, if it exists. Rebroadcast updated game state to all
    other users in the relevant game.

    Leaving an active game force-ends the game for all other users, if they exist

    Leaving a waiting game causes the garbage collection of game memory, if no other users are in the
    game after `user_id` is removed
    """
    # Get pointer to current game if it exists
    game = get_curr_game(user_id)

    if not game:
        # Cannot leave a game if not currently in one
        return False

    # Acquire this game's lock to ensure all global state updates are atomic
    with game.lock:
        # Update socket state maintained by socketio
        leave_room(game.id)

        # Update user data maintained by this app
        leave_curr_room(user_id)

        # Update game state maintained by game object
        if user_id in game.players:
            game.remove_player(user_id)
        else:
            game.remove_spectator(user_id)

        # Whether the game was active before the user left
        was_active = game.id in ACTIVE_GAMES

        # Rebroadcast data and handle cleanup based on the transition caused by leaving
        if was_active and game.is_empty():
            # Active -> Empty
            game.deactivate()
        elif game.is_empty():
            # Waiting -> Empty
            cleanup_game(game)
        elif not was_active:
            # Waiting -> Waiting
            emit("waiting", {"in_game": True}, room=game.id)
        elif was_active and game.is_ready():
            # Active -> Active
            pass
        elif was_active and not game.is_empty():
            # Active -> Waiting
            game.deactivate()

    return was_active


def _create_game(user_id, game_name, params={}):
    game, err = try_create_game(game_name, **params)
    if not game:
        emit("creation_failed", {"error": err.__repr__()})
        return
    spectating = True
    with game.lock:
        if not game.is_full():
            spectating = False
            game.add_player(user_id)
        else:
            spectating = True
            game.add_spectator(user_id)
        join_room(game.id)
        set_curr_room(user_id, game.id)
        if game.is_ready():
            game.activate()
            ACTIVE_GAMES.add(game.id)
            emit(
                "start_game",
                {"spectating": spectating, "start_info": game.to_json()},
                room=game.id,
            )
            socketio.start_background_task(play_game, game, fps=6)
        else:
            WAITING_GAMES.put(game.id)
            emit("waiting", {"in_game": True}, room=game.id)


#####################
# Debugging Helpers #
#####################


def _ensure_consistent_state():
    """
    Simple sanity checks of invariants on global state data

    Let ACTIVE be the set of all active game IDs, GAMES be the set of all existing
    game IDs, and WAITING be the set of all waiting (non-stale) game IDs. Note that
    a game could be in the WAITING_GAMES queue but no longer exist (indicated by
    the FREE_MAP)

    - Intersection of WAITING and ACTIVE games must be empty set
    - Union of WAITING and ACTIVE must be equal to GAMES
    - id \in FREE_IDS => FREE_MAP[id]
    - id \in ACTIVE_GAMES => Game in active state
    - id \in WAITING_GAMES => Game in inactive state
    """
    waiting_games = set()
    active_games = set()
    all_games = set(GAMES)

    for game_id in list(FREE_IDS.queue):
        assert FREE_MAP[game_id], "Freemap in inconsistent state"

    for game_id in list(WAITING_GAMES.queue):
        if not FREE_MAP[game_id]:
            waiting_games.add(game_id)

    for game_id in ACTIVE_GAMES:
        active_games.add(game_id)

    assert (
        waiting_games.union(active_games) == all_games
    ), "WAITING union ACTIVE != ALL"

    assert not waiting_games.intersection(
        active_games
    ), "WAITING intersect ACTIVE != EMPTY"

    assert all(
        [get_game(g_id)._is_active for g_id in active_games]
    ), "Active ID in waiting state"
    assert all(
        [not get_game(g_id)._id_active for g_id in waiting_games]
    ), "Waiting ID in active state"


def get_agent_names():
    return [
        d
        for d in os.listdir(AGENT_DIR)
        if os.path.isdir(os.path.join(AGENT_DIR, d))
    ]


# 실험 설계(experiment/README.md 중간 보고서 3.4절)상 본 실험의 AI 봇은
# "규칙 기반 플래너" 단 한 종류뿐이다. static/assets/agents/ 폴더에는 원본
# 공개 데모가 넣어둔 샘플 에이전트(RandAI, StayAI, Rllib*BC/SP 10종 등)도
# 같이 들어있는데, 연구자용 시작 화면 드롭다운에 이게 전부 노출되면
# (1) 실험과 무관한 에이전트를 실수로 고를 위험이 있고 (2) 실제로 그런
# 실수가 "봇이 멍청하다"는 피드백의 원인이었을 가능성이 높다(StayAI는
# 가만히 있고 RandAI는 무작위로 움직인다 — 둘 다 실험 봇이 아님).
# 그래서 /predefined 화면에는 실험용 RuleBasedBot_* 5종만 노출한다.
#
# 난이도(layout, snake_case) -> 그 난이도에서 쓸 실험 봇 이름 매핑.
# experiment/server_ext/ping_game.py의 update_for_layout 훅이 라운드마다
# mlam을 새 레이아웃에 맞게 다시 계산해주므로, 1라운드 시작 시점에 로드되는
# 피클이 "그 레이아웃의" 봇이기만 하면(=mlam이 처음부터 맞으면) 이후
# 레이아웃 전환은 전부 자동으로 따라간다. 즉 이 매핑은 "1라운드째 어떤
# 피클로 시작할지"를 정확히 고르기 위한 것이다.
LAYOUT_TO_EXPERIMENT_BOT = {
    "cramped_room": "RuleBasedBot_CrampedRoom",
    "asymmetric_advantages": "RuleBasedBot_AsymmetricAdvantages",
    "coordination_ring": "RuleBasedBot_CoordinationRing",
    "forced_coordination": "RuleBasedBot_ForcedCoordination",
    "counter_circuit": "RuleBasedBot_CounterCircuit",
}


def get_experiment_agent_names():
    """실험용 봇만 걸러낸 목록(실제로 agent.pickle이 존재하는 것만)."""
    all_names = set(get_agent_names())
    return sorted(
        name
        for name in LAYOUT_TO_EXPERIMENT_BOT.values()
        if name in all_names
    )


######################
# Application routes #
######################

# Hitting each of these endpoints creates a brand new socket that is closed
# at after the server response is received. Standard HTTP protocol


@app.route("/")
def index():
    agent_names = get_agent_names()
    return render_template(
        "index.html", agent_names=agent_names, layouts=LAYOUTS
    )


@app.route("/predefined")
def predefined():
    uid = request.args.get("UID")
    num_layouts = len(CONFIG["predefined"]["experimentParams"]["layouts"])

    # 시작 화면의 "학습 조건"/"난이도" 선택은 연구자 테스트 편의용 —
    # 참가자는 이 화면을 건드릴 필요 없이 기본값 그대로 "시작하기"만 누르면 됨.
    # 기본값은 여전히 config.json의 predefined.experimentParams를 그대로 씀.
    #
    # 독립변인(학습 단계 파트너 유형: AI 봇 vs 인간)과 직접 대응하도록,
    # 공개 데모처럼 "Player1=임의 에이전트, Player2=임의 에이전트"를 고르게
    # 하는 대신 "학습 조건"(AI 봇 / 인간) 하나만 고르게 하고, 내부적으로
    # LAYOUT_TO_EXPERIMENT_BOT을 통해 현재 난이도에 맞는 봇을 자동 매칭한다.
    learning_layouts = CONFIG["predefined"]["experimentParams"]["layouts"]

    return render_template(
        "predefined.html",
        uid=uid,
        config=PREDEFINED_CONFIG,
        num_layouts=num_layouts,
        learning_layouts=learning_layouts,
        layout_to_bot=LAYOUT_TO_EXPERIMENT_BOT,
    )


@app.route("/instructions")
def instructions():
    return render_template("instructions.html", layout_conf=LAYOUT_GLOBALS)


@app.route("/tutorial")
def tutorial():
    return render_template("tutorial.html", config=TUTORIAL_CONFIG)


@app.route("/debug")
def debug():
    resp = {}
    games = []
    active_games = []
    waiting_games = []
    users = []
    free_ids = []
    free_map = {}
    for game_id in ACTIVE_GAMES:
        game = get_game(game_id)
        active_games.append({"id": game_id, "state": game.to_json()})

    for game_id in list(WAITING_GAMES.queue):
        game = get_game(game_id)
        game_state = None if FREE_MAP[game_id] else game.to_json()
        waiting_games.append({"id": game_id, "state": game_state})

    for game_id in GAMES:
        games.append(game_id)

    for user_id in USER_ROOMS:
        users.append({user_id: get_curr_room(user_id)})

    for game_id in list(FREE_IDS.queue):
        free_ids.append(game_id)

    for game_id in FREE_MAP:
        free_map[game_id] = FREE_MAP[game_id]

    resp["active_games"] = active_games
    resp["waiting_games"] = waiting_games
    resp["all_games"] = games
    resp["users"] = users
    resp["free_ids"] = free_ids
    resp["free_map"] = free_map
    return jsonify(resp)


#########################
# Socket Event Handlers #
#########################

# Asynchronous handling of client-side socket events. Note that the socket persists even after the
# event has been handled. This allows for more rapid data communication, as a handshake only has to
# happen once at the beginning. Thus, socket events are used for all game updates, where more rapid
# communication is needed


def creation_params(params):
    """
    This function extracts the dataCollection and oldDynamics settings from the input and
    process them before sending them to game creation
    """
    # this params file should be a dictionary that can have these keys:
    # playerZero: human/Rllib*agent
    # playerOne: human/Rllib*agent
    # layout: one of the layouts in the config file, I don't think this one is used
    # gameTime: time in seconds
    # oldDynamics: on/off
    # dataCollection: on/off
    # layouts: [layout in the config file], this one determines which layout to use, and if there is more than one layout, a series of game is run back to back
    #

    use_old = False
    if "oldDynamics" in params and params["oldDynamics"] == "on":
        params["mdp_params"] = {"old_dynamics": True}
        use_old = True

    if "dataCollection" in params and params["dataCollection"] == "on":
        # config the necessary setting to properly save data
        params["dataCollection"] = True
        mapping = {"human": "H"}
        # gameType is either HH, HA, AH, AA depending on the config
        gameType = "{}{}".format(
            mapping.get(params["playerZero"], "A"),
            mapping.get(params["playerOne"], "A"),
        )
        params["collection_config"] = {
            "time": datetime.today().strftime("%Y-%m-%d_%H-%M-%S"),
            "type": gameType,
        }
        if use_old:
            params["collection_config"]["old_dynamics"] = "Old"
        else:
            params["collection_config"]["old_dynamics"] = "New"

    else:
        params["dataCollection"] = False


@socketio.on("create")
def on_create(data):
    user_id = request.sid
    with USERS[user_id]:
        # Retrieve current game if one exists
        curr_game = get_curr_game(user_id)
        if curr_game:
            # Cannot create if currently in a game
            return

        params = data.get("params", {})

        creation_params(params)

        game_name = data.get("game_name", "overcooked")
        _create_game(user_id, game_name, params)


@socketio.on("join")
def on_join(data):
    user_id = request.sid
    with USERS[user_id]:
        create_if_not_found = data.get("create_if_not_found", True)

        # Retrieve current game if one exists
        curr_game = get_curr_game(user_id)
        if curr_game:
            # Cannot join if currently in a game
            return

        # Retrieve a currently open game if one exists
        game = get_waiting_game()

        if not game and create_if_not_found:
            # No available game was found so create a game
            params = data.get("params", {})
            creation_params(params)
            game_name = data.get("game_name", "overcooked")
            _create_game(user_id, game_name, params)
            return

        elif not game:
            # No available game was found so start waiting to join one
            emit("waiting", {"in_game": False})
        else:
            # Game was found so join it
            with game.lock:
                join_room(game.id)
                set_curr_room(user_id, game.id)
                game.add_player(user_id)

                if game.is_ready():
                    # Game is ready to begin play
                    game.activate()
                    ACTIVE_GAMES.add(game.id)
                    emit(
                        "start_game",
                        {"spectating": False, "start_info": game.to_json()},
                        room=game.id,
                    )
                    socketio.start_background_task(play_game, game)
                else:
                    # Still need to keep waiting for players
                    WAITING_GAMES.put(game.id)
                    emit("waiting", {"in_game": True}, room=game.id)


@socketio.on("leave")
def on_leave(data):
    user_id = request.sid
    with USERS[user_id]:
        was_active = _leave_game(user_id)

        if was_active:
            emit("end_game", {"status": Game.Status.DONE, "data": {}})
        else:
            emit("end_lobby")


@socketio.on("action")
def on_action(data):
    user_id = request.sid
    action = data["action"]

    game = get_curr_game(user_id)
    if not game:
        return

    game.enqueue_action(user_id, action)


@socketio.on("connect")
def on_connect():
    user_id = request.sid

    if user_id in USERS:
        return

    USERS[user_id] = Lock()


@socketio.on("disconnect")
def on_disconnect():
    print("disonnect triggered", file=sys.stderr)
    # Ensure game data is properly cleaned-up in case of unexpected disconnect
    user_id = request.sid
    if user_id not in USERS:
        return
    with USERS[user_id]:
        _leave_game(user_id)

    del USERS[user_id]


# Exit handler for server
def on_exit():
    # Force-terminate all games on server termination
    for game_id in GAMES:
        socketio.emit(
            "end_game",
            {
                "status": Game.Status.INACTIVE,
                "data": get_game(game_id).get_data(),
            },
            room=game_id,
        )


#############
# Game Loop #
#############


def play_game(game: OvercookedGame, fps=6):
    """
    Asynchronously apply real-time game updates and broadcast state to all clients currently active
    in the game. Note that this loop must be initiated by a parallel thread for each active game

    game (Game object):     Stores relevant game state. Note that the game id is the same as to socketio
                            room id for all clients connected to this game
    fps (int):              Number of game ticks that should happen every second
    """
    status = Game.Status.ACTIVE
    while status != Game.Status.DONE and status != Game.Status.INACTIVE:
        with game.lock:
            status = game.tick()
        if status == Game.Status.RESET:
            with game.lock:
                data = game.get_data()
            socketio.emit(
                "reset_game",
                {
                    "state": game.to_json(),
                    "timeout": game.reset_timeout,
                    "data": data,
                },
                room=game.id,
            )
            socketio.sleep(game.reset_timeout / 1000)
        else:
            socketio.emit(
                "state_pong", {"state": game.get_state()}, room=game.id
            )
        socketio.sleep(1 / fps)

    with game.lock:
        data = game.get_data()
        socketio.emit(
            "end_game", {"status": status, "data": data}, room=game.id
        )

        if status != Game.Status.INACTIVE:
            game.deactivate()
        cleanup_game(game)


if __name__ == "__main__":
    # Dynamically parse host and port from environment variables (set by docker build)
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 80))

    # Attach exit handler to ensure graceful shutdown
    atexit.register(on_exit)

    # https://localhost:80 is external facing address regardless of build environment
    socketio.run(app, host=host, port=port, log_output=app.config["DEBUG"])
