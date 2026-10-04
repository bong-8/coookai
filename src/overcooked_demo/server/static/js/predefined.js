// This is the javascript that defines transitions and interactions in the predefined.html page
// Persistent network connection that will be used to transmit real-time data
var socket = io();

var config;
var experimentParams = {
    layouts : ["cramped_room", "counter_circuit"],
    gameTime : 10,
    playerZero : "DummyAI"
};

var lobbyWaitTime = 300000;

/* * * * * * * * * * * * * 
 * Socket event handlers *
 * * * * * * * * * * * * */

window.intervalID = -1;
window.ellipses = -1;
window.lobbyTimeout = -1;

$(function() {
    $('#leave-btn').click(function () {
        socket.emit("leave",{});
        window.location.href = "/"
    });
});


socket.on('waiting', function(data) {
    // Show game lobby
    $('#game-over').hide();
    $("#overcooked").empty();
    $('#lobby').show();
    if (!data.in_game) {
        if (window.intervalID === -1) {
            // Occassionally ping server to try and join
            window.intervalID = setInterval(function() {
                socket.emit('join', {});
            }, 1000);
        }
    }
    if (window.lobbyTimeout === -1) {
        // Waiting animation
        window.ellipses = setInterval(function () {
            var e = $("#ellipses").text();
            $("#ellipses").text(".".repeat((e.length + 1) % 10));
        }, 500);
        // Timeout to leave lobby if no-one is found
        window.lobbyTimeout = setTimeout(function() {
            socket.emit('leave', {});
        }, config.lobbyWaitTime)
    }
});

socket.on('creation_failed', function(data) {
    // Tell user what went wrong
    let err = data['error']
    $("#overcooked").empty();
    $('#overcooked').append(`<h4>Sorry, game creation code failed with error: ${JSON.stringify(err)}</>`);
    $("error-exit").show();

    // Let parent window know error occurred
    window.top.postMessage({ name : "error"}, "*");
});

socket.on('start_game', function(data) {
    // Hide game-over and lobby, show game title header
    if (window.intervalID !== -1) {
        clearInterval(window.intervalID);
        window.intervalID = -1;
    }
    if (window.lobbyTimeout !== -1) {
        clearInterval(window.ellipses);
        clearTimeout(window.lobbyTimeout);
        window.lobbyTimeout = -1;
        window.ellipses = -1;
    }
    graphics_config = {
        container_id : "overcooked",
        start_info : data.start_info
    };
    $("#overcooked").empty();
    $('#game-over').hide();
    $('#lobby').hide();
    $('#reset-game').hide();
    $('#game-title').show();
    enable_key_listener();
    enable_ping_controls();
    graphics_start(graphics_config);
});

socket.on('reset_game', function(data) {
    graphics_end();
    disable_key_listener();
    disable_ping_controls();
    $("#overcooked").empty();
    $("#reset-game").show();
    setTimeout(function() {
        $("#reset-game").hide();
        graphics_config = {
            container_id : "overcooked",
            start_info : data.state
        };
        graphics_start(graphics_config);
        enable_key_listener();
        enable_ping_controls();

        // Propogate game stats to parent window
        window.top.postMessage({ name : "data", data : data.data, done : false}, "*");
    }, data.timeout);
});

socket.on('state_pong', function(data) {
    // Draw state update
    drawState(data['state']);
});

socket.on('end_game', function(data) {
    // Hide game data and display game-over html
    graphics_end();
    disable_key_listener();
    disable_ping_controls();
    $('#game-title').hide();
    $('#game-over').show();
    $("#overcooked").empty();

    // Game ended unexpectedly
    if (data.status === 'inactive') {
        $("#error").show();
        $("#error-exit").show();
    }

    // Propogate game stats to parent window
    window.top.postMessage({ name : "data", data : data.data, done : true }, "*");
});

socket.on('end_lobby', function() {
    // Display join game timeout text
    $("#finding_partner").text(
        "We were unable to find you a partner."
    );
    $("#error-exit").show();

    // Stop trying to join
    clearInterval(window.intervalID);
    clearInterval(window.ellipses);
    window.intervalID = -1;

    // Let parent window know what happened
    window.top.postMessage({ name : "timeout" }, "*");
})


/* * * * * * * * * * * * * *
 * Game Key Event Listener *
 * * * * * * * * * * * * * */

// 키를 누르고 있을 때 브라우저/OS의 "키 반복" 지연(처음 누르면 바로, 그 다음
// 부터는 한참 있다 반복 입력이 옴) 때문에 사람 캐릭터가 봇보다 훨씬 끊기듯
// 움직이는 것처럼 보인다는 피드백으로 수정. 방향키는 keydown 하나당 한 번
// 보내는 대신, "지금 눌려있는 키" 집합을 추적하다가 서버 틱 주기(6fps =
// 약 167ms)보다 살짝 빠른 주기로 계속 보내도록 바꿨다 — 키를 누르고 있는
// 동안은 봇처럼 매 틱 끊김 없이 움직인다. SPACE(상호작용)는 누르고 있다고
// 계속 반복하면 줍기/놓기가 의도치 않게 반복될 수 있어 그대로 keydown 1회당
// 1번만 보내되, OS 자동 반복(e.repeat)은 무시한다.
var MOVE_KEY_TO_ACTION = { 37: 'LEFT', 38: 'UP', 39: 'RIGHT', 40: 'DOWN' };
var PRESSED_MOVE_KEYS = [];  // 누른 순서 유지 (가장 최근 누른 키 우선)
var movementIntervalId = -1;
var MOVEMENT_SEND_INTERVAL_MS = 120;  // 서버 6fps(~167ms)보다 살짝 빠르게

function enable_key_listener() {
    $(document).on('keydown', function(e) {
        if (MOVE_KEY_TO_ACTION.hasOwnProperty(e.which)) {
            e.preventDefault();
            if (PRESSED_MOVE_KEYS.indexOf(e.which) === -1) {
                PRESSED_MOVE_KEYS.push(e.which);
            }
        } else if (e.which === 32) { // space
            e.preventDefault();
            if (!e.repeat) {
                socket.emit('action', { 'action': 'SPACE' });
            }
        }
    });
    $(document).on('keyup', function(e) {
        if (MOVE_KEY_TO_ACTION.hasOwnProperty(e.which)) {
            let idx = PRESSED_MOVE_KEYS.indexOf(e.which);
            if (idx !== -1) { PRESSED_MOVE_KEYS.splice(idx, 1); }
        }
    });
    if (movementIntervalId === -1) {
        movementIntervalId = setInterval(function () {
            if (PRESSED_MOVE_KEYS.length === 0) { return; } // 아무 키도 안 눌렀으면 그냥 둠(서버가 STAY로 처리)
            let mostRecentKey = PRESSED_MOVE_KEYS[PRESSED_MOVE_KEYS.length - 1];
            socket.emit('action', { 'action': MOVE_KEY_TO_ACTION[mostRecentKey] });
        }, MOVEMENT_SEND_INTERVAL_MS);
    }
};

function disable_key_listener() {
    $(document).off('keydown');
    $(document).off('keyup');
    if (movementIntervalId !== -1) {
        clearInterval(movementIntervalId);
        movementIntervalId = -1;
    }
    PRESSED_MOVE_KEYS = [];
};


/* * * * * * * * * * * * * * * * * * * * *
 * Phase 2: 핑 소통 채널 버튼 핸들러      *
 * 새 이벤트를 만들지 않고, 기존 action  *
 * 이벤트로 PING_<TYPE> 문자열을 보낸다. *
 * * * * * * * * * * * * * * * * * * * * */

var PING_TYPES = ['help', 'look', 'mine', 'ok'];

$(function() {
    PING_TYPES.forEach(function (pingType) {
        $('#ping-' + pingType).click(function () {
            socket.emit('action', { 'action': 'PING_' + pingType.toUpperCase() });
        });
    });
});

function enable_ping_controls() {
    $('#ping-controls').show();
};

function disable_ping_controls() {
    $('#ping-controls').hide();
};


/* * * * * * * * * * * *
 * Game Initialization *
 * * * * * * * * * * * */

// 난이도(layout, snake_case) -> 그 난이도용 실험 봇 이름. app.py의
// LAYOUT_TO_EXPERIMENT_BOT과 동일한 매핑(실험 봇은 RuleBasedBot_* 5종뿐 —
// 공개 데모 샘플 에이전트는 애초에 참가자 화면에 노출하지 않는다).
var LAYOUT_TO_BOT = {
    'cramped_room': 'RuleBasedBot_CrampedRoom',
    'asymmetric_advantages': 'RuleBasedBot_AsymmetricAdvantages',
    'coordination_ring': 'RuleBasedBot_CoordinationRing',
    'forced_coordination': 'RuleBasedBot_ForcedCoordination',
    'counter_circuit': 'RuleBasedBot_CounterCircuit'
};

socket.on("connect", function() {
    // set configuration variables
    set_config();
    // "학습 조건"/"난이도" 드롭다운은 항상 "기본값"에서 시작한다 — 아무것도
    // 안 건드리고 "시작하기"만 누르면 config.json의 predefined.experimentParams
    // 그대로(=참가자가 실제로 겪는 기본 흐름) 동작함.
});

$(function() {
    // "인간 학습 집단" 조건을 고르면, 두 대의 컴퓨터가 필요하다는 안내를 보여준다.
    $('#override-condition').change(function () {
        $('#human-condition-note').toggle($(this).val() === 'human');
    });
});

// 접속 직후 바로 게임이 시작되지 않도록, "시작하기" 버튼을 눌러야
// join을 보낸다 (안내 화면 참고: static/templates/predefined.html의 #start-screen).
$(function() {
    $('#start-btn').click(function () {
        $('#start-screen').hide();
        $('#overcooked-container').show();

        let params = JSON.parse(JSON.stringify(config.experimentParams));

        // 연구자용 난이도(Layout) 오버라이드 — 기본값이면 config.json의
        // 5단계 순서(experimentParams.layouts) 그대로 둔다.
        let layoutOverride = $('#override-layout').val();
        if (layoutOverride !== '__default__') {
            params.layouts = [layoutOverride];
        }
        let firstLayout = params.layouts[0];

        // "학습 조건"은 본 실험의 독립변인(학습 단계 파트너 유형)과 1:1로
        // 대응한다. AI 조건을 고르면 1라운드째 난이도에 맞는 실험 봇을
        // 자동으로 매칭한다 — 사람이 봇 이름을 직접 고르다 엉뚱한 봇(혹은
        // 공개 데모 샘플 에이전트)을 고르는 실수를 원천 차단한다.
        let condition = $('#override-condition').val();
        if (condition === 'ai') {
            params.playerZero = 'human';
            params.playerOne = LAYOUT_TO_BOT[firstLayout] || config.experimentParams.playerOne;
        } else if (condition === 'human') {
            // 사람-사람 조건: 두 번째 참가자는 자신의 컴퓨터에서 이 화면에
            // 접속해 "시작하기"만 누르면 된다 (서버가 대기 중인 게임에
            // 자동으로 합류시킨다 — on_join의 get_waiting_game()). 이때
            // 두 번째 참가자가 고른 조건/난이도는 서버에서 무시되고, 먼저
            // "시작하기"를 누른 사람의 설정이 게임 전체에 적용된다.
            params.playerZero = 'human';
            params.playerOne = 'human';
        }
        // condition === 'default' 이면 config.json 값을 그대로 둔다.

        let data = {
            "params" : params,
            "game_name" : "overcooked"
        };

        // create (or join if it exists) new game
        socket.emit("join", data);
    });
});


/* * * * * * * * * * *
 * Utility Functions *
 * * * * * * * * * * */

var arrToJSON = function(arr) {
    let retval = {}
    for (let i = 0; i < arr.length; i++) {
        elem = arr[i];
        key = elem['name'];
        value = elem['value'];
        retval[key] = value;
    }
    return retval;
};

var set_config = function() {
    config = JSON.parse($("#config").text());
}
