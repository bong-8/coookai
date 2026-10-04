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
        // 2026-10-05 수정: "/"(원본 공개 데모의 기본 화면, Player1/Player2
        // 드롭다운이 있는 그 화면)로 보내던 걸 "/predefined"(이 실험 자체의
        // 시작 화면)로 바꿨다. "매칭 대기 중 Leave를 누르면 엉뚱한 화면으로
        // 간다"는 피드백의 원인이 이거였다 — 코드 버그라기보단 원본 공개
        // 데모를 그대로 베낀 흔적이 실험용 페이지에 남아있던 것.
        window.location.href = "/predefined"
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
    // 라운드(=난이도 하나)가 끝날 때마다 호출된다. game.get_data()는 반환하면서
    // 서버 쪽 trajectory를 비우므로, 이 라운드의 기록은 지금 이 data.data가
    // 유일한 기회다 — 누적해두지 않으면 "게임 기록 보기"에서 마지막 라운드
    // 말고는 볼 방법이 없다.
    recordRoundResult(data.data);
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
    // 마지막 라운드 기록도 reset_game과 똑같이 누적해둔다 — 세션 전체(5개
    // 난이도) 기록이 "게임 기록 보기"에 전부 모이게.
    recordRoundResult(data.data);

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
// 보내는 대신, "지금 눌려있는 키" 집합을 추적하다가 서버 틱 주기보다 살짝
// 빠른 주기로 계속 보내도록 바꿨다 — 키를 누르고 있는 동안은 봇처럼 매 틱
// 끊김 없이 움직인다. SPACE(상호작용)는 누르고 있다고 계속 반복하면
// 줍기/놓기가 의도치 않게 반복될 수 있어 그대로 keydown 1회당 1번만
// 보내되, OS 자동 반복(e.repeat)은 무시한다.
//
// 그런데 이것만으로는 "여전히 매끄럽지 않다"는 피드백이 또 나왔다 — 진짜
// 원인은 입력 쪽이 아니라 서버 시뮬레이션 자체가 초당 6번(약 167ms 간격)
// 만 상태를 갱신하고 있었기 때문이다(app.py의 play_game 루프). 입력을
// 아무리 자주 보내도 서버가 그만큼만 반영하니 한계가 있었다. 서버 틱
// 속도를 app.py에서 10fps(약 100ms 간격, config.json의 MAX_FPS)로
// 올렸으므로, 그 변화에 맞춰 이 간격도 같이 줄였다.
//
// 추가로 발견된 버그(2026-10-04, "유저와 봇의 속도를 일치시켜줘" 피드백):
// 이 간격(80ms)이 서버 틱(100ms)보다 빠르다는 것 자체는 의도한 설계였지만,
// 당시 app.py에서 human 플레이어의 pending_actions 큐가 무제한
// (buff_size=-1 기본값)이어서, 서버가 틱당 1개만 소비하는 동안 못 처리한
// 입력이 시간이 지날수록 한도 없이 쌓여 "사람 입력이 점점 더 늦게 반영되는"
// 누적 지연을 만들었다 — 매 라운드가 길어질수록 사람 쪽만 체감 속도가
// 떨어지고, 매 틱 즉석 결정이라 큐 자체가 없는 봇은 전혀 안 느려지니 둘의
// 속도 차이가 점점 벌어지는 것처럼 보였다. 이 JS 파일이 아니라 app.py의
// game.add_player(user_id, buff_size=1)로 고쳤다(큐 길이를 항상 최대
// 1로 제한 — NPC 봇이 원래부터 쓰던 것과 동일한 설정). 그 고침 덕분에 이
// 80ms 간격은 그대로 둬도 안전하다 — 큐가 꽉 차 있으면 enqueue_action()의
// Queue.put()이 서버가 다음 틱에 비울 때까지 잠깐만 대기할 뿐, 무한히
// 쌓이지는 않는다.
var MOVE_KEY_TO_ACTION = { 37: 'LEFT', 38: 'UP', 39: 'RIGHT', 40: 'DOWN' };
var PRESSED_MOVE_KEYS = [];  // 누른 순서 유지 (가장 최근 누른 키 우선)
var movementIntervalId = -1;
var MOVEMENT_SEND_INTERVAL_MS = 80;  // 서버 10fps(~100ms)보다 살짝 빠르게

// 핑을 마우스로 클릭하기 어렵다는 피드백(2026-10-04)으로 추가한 숫자키
// 단축키 — 게임 중에는 양손이 방향키/스페이스에 가 있으니, 숫자키 1~4를
// 키보드 맨 위 줄에 그대로 둬 손을 크게 옮기지 않고도 누를 수 있게 했다.
// PING_TYPES 배열(아래)과 같은 순서: 1=help(도와줘), 2=move(비켜줘),
// 3=mine(내가 할게), 4=ok. e.which: 1='1'=49, 2='2'=50, 3='3'=51, 4='4'=52.
// move(비켜줘)는 2026-10-04에 "look"(이거 봐) 자리를 교체했다 — look은
// 행동 변화가 전혀 없어서 "핑을 눌러도 아무 효과가 없다"는 피드백의
// 원인이었다. 자세한 반응 로직은 experiment/agents/role_restricted_bot.py의
// PingReactiveBot._decide_move_aside_action 참고.
var PING_KEY_TO_TYPE = { 49: 'help', 50: 'move', 51: 'mine', 52: 'ok' };

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
        } else if (PING_KEY_TO_TYPE.hasOwnProperty(e.which)) {
            e.preventDefault();
            if (!e.repeat) { // 키를 누르고 있어도 반복 전송되지 않게
                send_ping(PING_KEY_TO_TYPE[e.which]);
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
 * 게임 기록(라운드별 요약) — 2026-10-05  *
 * * * * * * * * * * * * * * * * * * * * *
 * "게임 끝나고 기록을 볼 수 있는 버튼" 요청으로 추가. 서버의
 * game.get_data()는 반환과 동시에 그 라운드의 trajectory를 비우므로
 * (game.py 참고), reset_game/end_game 이벤트가 올 때마다 그 즉시 요약만
 * 뽑아서 클라이언트 쪽(window.GAME_RECORD)에 쌓아둔다 — 서버에 새 엔드포인트를
 * 만들거나 전체 trajectory를 계속 들고 있을 필요 없이, 각 라운드 trajectory의
 * 마지막 엔트리(최종 점수/소요시간)와 "pings" 필드(핑 타입별 집계)만 뽑아
 * 가벼운 요약으로 변환한다. 전체 리플레이가 아니라 "이번 세션에 난이도별로
 * 몇 점이었고 핑을 몇 번 주고받았는지"를 보여주는 용도. */
window.GAME_RECORD = [];

function recordRoundResult(data) {
    if (!data || !data.trajectory || data.trajectory.length === 0) {
        return;
    }
    var trajectory = data.trajectory;
    var lastStep = trajectory[trajectory.length - 1];
    var pingCounts = { help: 0, move: 0, mine: 0, ok: 0 };
    trajectory.forEach(function (step) {
        (step.pings || []).forEach(function (ping) {
            if (pingCounts.hasOwnProperty(ping.ping_type)) {
                pingCounts[ping.ping_type] += 1;
            }
        });
    });
    window.GAME_RECORD.push({
        layout: lastStep.layout_name || "?",
        score: lastStep.score,
        timeElapsedSec: Math.round(lastStep.time_elapsed),
        pingCounts: pingCounts
    });
}

function renderGameRecord() {
    var $body = $("#game-record-body");
    $body.empty();
    if (window.GAME_RECORD.length === 0) {
        $body.append("<tr><td colspan='7'>기록된 라운드가 없습니다.</td></tr>");
        return;
    }
    window.GAME_RECORD.forEach(function (round) {
        var minutes = Math.floor(round.timeElapsedSec / 60);
        var seconds = round.timeElapsedSec % 60;
        var timeStr = minutes + "분 " + seconds + "초";
        $body.append(
            "<tr>" +
            "<td>" + round.layout + "</td>" +
            "<td>" + round.score + "</td>" +
            "<td>" + timeStr + "</td>" +
            "<td>" + round.pingCounts.help + "</td>" +
            "<td>" + round.pingCounts.move + "</td>" +
            "<td>" + round.pingCounts.mine + "</td>" +
            "<td>" + round.pingCounts.ok + "</td>" +
            "</tr>"
        );
    });
}

$(function () {
    $('#view-record-btn').click(function () {
        var $panel = $('#game-record');
        if ($panel.is(':visible')) {
            $panel.hide();
            $(this).text('게임 기록 보기');
        } else {
            renderGameRecord();
            $panel.show();
            $(this).text('게임 기록 숨기기');
        }
    });
    $('#back-to-start-btn').click(function () {
        // leave-btn과 동일하게 /predefined(이 실험의 시작 화면)로 돌아간다.
        // 이미 end_game까지 받은 뒤라 서버 쪽 게임은 정리된 상태이므로
        // 별도로 leave를 emit할 필요는 없다.
        window.location.href = "/predefined";
    });
});


/* * * * * * * * * * * * * * * * * * * * *
 * Phase 2: 핑 소통 채널 버튼 핸들러      *
 * 새 이벤트를 만들지 않고, 기존 action  *
 * 이벤트로 PING_<TYPE> 문자열을 보낸다. *
 * * * * * * * * * * * * * * * * * * * * */

var PING_TYPES = ['help', 'move', 'mine', 'ok'];

// 버튼 클릭과 숫자키(1/2/3/4) 단축키가 똑같은 경로를 타도록 공용 함수로 뺐다.
function send_ping(pingType) {
    socket.emit('action', { 'action': 'PING_' + pingType.toUpperCase() });
}

$(function() {
    PING_TYPES.forEach(function (pingType) {
        $('#ping-' + pingType).click(function () {
            send_ping(pingType);
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
