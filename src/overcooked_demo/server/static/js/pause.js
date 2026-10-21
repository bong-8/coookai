/*
 * 일시정지 표시 (2026-10-21) — 서버(experiment/server_ext/pause_game.py)가 state_pong 에 "paused": true 를 보내는 동안
 * 게임 화면 위에 반투명 "PAUSE" 글씨를 띄운다. 클릭/키 입력은 막지 않는다(pointer-events: none).
 * 키는 predefined.js 가 ` (백틱) 를 'PAUSE_TOGGLE' 로 서버에 보낸다.
 */
(function () {
    var el = null;

    function build() {
        if (el) { return; }
        el = document.createElement('div');
        el.id = 'pause-overlay';
        el.style.cssText = 'display:none;position:fixed;z-index:9000;pointer-events:none;' +
            'background:rgba(0,0,0,0.28);align-items:center;justify-content:center;flex-direction:column;';
        var t = document.createElement('div');
        t.textContent = 'PAUSE';
        t.style.cssText = 'font:800 84px Arial,sans-serif;letter-spacing:8px;color:rgba(255,255,255,0.72);' +
            'text-shadow:0 2px 10px rgba(0,0,0,0.55);';
        var h = document.createElement('div');
        h.textContent = '` 키를 다시 누르면 계속돼요';
        h.style.cssText = 'margin-top:6px;font:600 18px Arial,sans-serif;color:rgba(255,255,255,0.7);' +
            'text-shadow:0 1px 6px rgba(0,0,0,0.6);';
        el.appendChild(t); el.appendChild(h);
        document.body.appendChild(el);
    }

    function place() {
        var box = document.getElementById('overcooked');
        var r = box ? box.getBoundingClientRect() : null;
        if (!r || r.width < 10) { r = { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight }; }
        el.style.left = r.left + 'px'; el.style.top = r.top + 'px';
        el.style.width = r.width + 'px'; el.style.height = r.height + 'px';
    }

    window.pauseOnState = function (state) {
        if (state && state.paused) {
            build(); place(); el.style.display = 'flex';
        } else if (el) {
            el.style.display = 'none';
        }
    };
    window.pauseHide = function () { if (el) { el.style.display = 'none'; } };
})();
