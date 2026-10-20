/*
 * 자동 가이드 화면 — 일시정지 오버레이 + 음성(TTS) + Enter 로 계속 (2026-10-21).
 *
 * 서버(experiment/server_ext/guide_game.py)가 state_pong 의 "guide" 필드로
 *   {id, status: "loading"|"ready", text, page, n_pages, seq, can_continue}
 * 를 보내면 이 파일이 오버레이를 띄운다. guide 가 null 이면 숨긴다.
 *  - 음성: 브라우저 내장 SpeechSynthesis(ko-KR). 키/외부 서비스 불필요. 한국어 음성이 없는
 *    브라우저에서는 조용히 텍스트만 보여준다. 같은 쪽(seq)은 한 번만 읽는다.
 *  - Enter: 서버가 can_continue=true 라고 알려 준 뒤에만 'GUIDE_CONTINUE' 전송.
 *  - 오버레이가 떠 있는 동안 keydown 은 여기서 가로챈다(게임 조작·핑 키가 안 먹게).
 *    keyup 은 그대로 통과시킨다 — 안 그러면 "눌린 키" 기록이 안 지워져 재개 후 캐릭터가 걷는다.
 * predefined.js 는 state_pong 에서 guideOnState(), reset_game/end_game 에서 guideHide() 만 부른다.
 */
(function () {
    var overlay = null, textEl = null, pageEl = null, hintEl = null, muteBtn = null;
    var lastSeq = -1;
    var current = null;           // 마지막으로 받은 guide 상태
    var ttsOn = true;
    var koVoice = null;

    function pickVoice() {
        if (!window.speechSynthesis) { return; }
        var voices = window.speechSynthesis.getVoices() || [];
        for (var i = 0; i < voices.length; i++) {
            if ((voices[i].lang || '').toLowerCase().indexOf('ko') === 0) { koVoice = voices[i]; return; }
        }
    }
    if (window.speechSynthesis) {
        pickVoice();
        window.speechSynthesis.onvoiceschanged = pickVoice;
    }

    function speak(text) {
        if (!ttsOn || !window.speechSynthesis || !text) { return; }
        try {
            window.speechSynthesis.cancel();
            var u = new SpeechSynthesisUtterance(text);
            u.lang = 'ko-KR';
            if (koVoice) { u.voice = koVoice; }
            u.rate = 1.0;
            window.speechSynthesis.speak(u);
        } catch (e) { /* 음성이 안 돼도 텍스트는 보인다 */ }
    }

    function stopSpeech() {
        if (window.speechSynthesis) { try { window.speechSynthesis.cancel(); } catch (e) {} }
    }

    function build() {
        if (overlay) { return; }
        overlay = document.createElement('div');
        overlay.id = 'guide-overlay';
        overlay.style.cssText = 'display:none;position:fixed;left:0;top:0;width:100%;height:100%;' +
            'background:rgba(0,0,0,0.55);z-index:9999;align-items:center;justify-content:center;';
        var card = document.createElement('div');
        card.style.cssText = 'background:#fff;border-radius:10px;padding:28px 32px;max-width:560px;width:90%;' +
            'box-shadow:0 8px 30px rgba(0,0,0,0.4);font-size:20px;line-height:1.6;color:#222;text-align:left;';
        pageEl = document.createElement('div');
        pageEl.style.cssText = 'font-size:13px;color:#888;margin-bottom:8px;';
        textEl = document.createElement('div');
        textEl.style.cssText = 'min-height:64px;';
        hintEl = document.createElement('div');
        hintEl.style.cssText = 'margin-top:18px;font-size:15px;color:#555;';
        muteBtn = document.createElement('button');
        muteBtn.type = 'button';
        muteBtn.style.cssText = 'float:right;font-size:12px;padding:2px 8px;';
        muteBtn.onclick = function (e) {
            e.preventDefault();
            ttsOn = !ttsOn;
            if (!ttsOn) { stopSpeech(); }
            muteBtn.textContent = ttsOn ? '음성 끄기' : '음성 켜기';
            muteBtn.blur();   // 포커스가 남으면 Enter 가 버튼 클릭으로 처리될 수 있다
        };
        muteBtn.textContent = '음성 끄기';
        card.appendChild(muteBtn);
        card.appendChild(pageEl);
        card.appendChild(textEl);
        card.appendChild(hintEl);
        overlay.appendChild(card);
        document.body.appendChild(overlay);
    }

    function render(g) {
        build();
        overlay.style.display = 'flex';
        if (g.status === 'loading') {
            pageEl.textContent = '';
            textEl.textContent = '안내를 준비하고 있어요…';
            hintEl.textContent = '';
            return;
        }
        pageEl.textContent = g.n_pages > 1 ? ('안내 ' + g.page + ' / ' + g.n_pages) : '안내';
        textEl.textContent = g.text || '';
        hintEl.textContent = g.can_continue
            ? 'Enter 키를 누르면 계속해요. (지금은 시간이 흐르지 않아요)'
            : '잠시만 기다려 주세요…';
        if (g.seq !== lastSeq) {
            lastSeq = g.seq;
            speak(g.text);
        }
    }

    window.guideOnState = function (state) {
        var g = state && state.guide;
        current = g || null;
        if (!g) { window.guideHide(); return; }
        render(g);
    };

    window.guideHide = function () {
        current = null;
        lastSeq = -1;
        stopSpeech();
        if (overlay) { overlay.style.display = 'none'; }
    };

    // 오버레이가 보이는 동안 키 입력을 가로챈다(캡처 단계: 게임의 jQuery 핸들러보다 먼저).
    window.addEventListener('keydown', function (e) {
        if (!current) { return; }
        e.preventDefault();
        e.stopImmediatePropagation();
        if (e.repeat) { return; }
        if (e.key === 'Enter' && current.status === 'ready' && current.can_continue) {
            if (typeof socket !== 'undefined') {
                socket.emit('action', { 'action': 'GUIDE_CONTINUE' });
            }
        }
    }, true);
})();
