/*
 * Kenney 스킨 (2026-10-21) — 그래픽 업그레이드 시안의 실제 구현. 기본은 꺼져 있다.
 *
 * 켜는 법: 접속 주소 뒤에 ?skin=kenney  (없으면 원본 그래픽 그대로 → 실험 기준선 불변)
 * 원본 graphics.js 의 OvercookedScene 을 상속해서 "그리는 부분"만 바꾼다. 서버·게임 로직·상태 형식은 그대로.
 *
 * 바뀐 점 (목적: 재료 종류/개수를 한눈에):
 *   - 냄비: 위에 3칸 — 들어간 재료가 그림으로(양파=노랑 테두리, 토마토=빨강 테두리), 빈 칸은 점선. 조리 링 진행, 완성 시 초록 링+✓
 *   - 수프 접시(바닥/손): 수프 색(양파=노랑, 토마토=빨강, 혼합=주황) + 재료 칩
 *   - 주문: 화면 아래 "주문서 카드"(재료 칸 + 글자), 같은 주문은 ×n 으로 묶음
 *   - 요리사: Kenney Mini Characters 로 만든 4방향 스프라이트(P1 파란 모자, P2 초록 모자)
 * 그림 파일: static/assets/kenney/*.png (CC0, 만든 방법은 experiment/graphics/README.md)
 *
 * 성능: 상태가 바뀔 때만(서버 틱 단위) 다시 그린다. 원본은 매 프레임 스프라이트를 지웠다 새로 만든다.
 */
(function () {
    if (typeof OvercookedScene === 'undefined') { return; }

    var K_ASSETS = [
        'floor_a', 'floor_b', 'counter', 'tile_onions', 'tile_tomatoes', 'tile_dishes', 'tile_serve', 'serve_arrows', 'tile_pot',
        'item_onion', 'item_tomato', 'item_dish', 'item_soup_o', 'item_soup_t', 'item_soup_m',
        'chip_o', 'chip_t', 'chip_empty',
        'chef_p1_SOUTH', 'chef_p1_EAST', 'chef_p1_NORTH', 'chef_p1_WEST',
        'chef_p2_SOUTH', 'chef_p2_EAST', 'chef_p2_NORTH', 'chef_p2_WEST'
    ];
    var K_TILE = { 'X': 'counter', 'P': 'tile_pot', 'O': 'tile_onions', 'T': 'tile_tomatoes', 'D': 'tile_dishes', 'S': 'tile_serve' };
    var OPEN_Y = 40;            // 냄비 입구(타원) 중심의 타일 내 y
    var SOUP_COLOR = { o: 0xf4d678, t: 0xe0503f, m: 0xeb8c50 };
    var HELD_POS = { SOUTH: [40, 60], EAST: [60, 54], WEST: [20, 54], NORTH: [40, 48] };

    // ── 효과음 (Kenney Casino Audio, CC0): 재료 넣기 / 조리 완료 / 서빙 성공. 소리 켜짐 여부는 브라우저에 저장 ──
    var _sndCache = {};
    function soundMuted() { try { return window.localStorage.getItem('game_sound_muted') === '1'; } catch (e) { return false; } }
    function setSoundMuted(m) { try { window.localStorage.setItem('game_sound_muted', m ? '1' : '0'); } catch (e) { /* 저장 불가여도 동작 */ } }
    window.gameSoundMuted = soundMuted;
    window.gamePlaySound = function (name) {
        if (soundMuted()) { return; }
        try {
            var base = _sndCache[name];
            if (!base) { base = new Audio('static/assets/sounds/' + name + '.mp3'); base.preload = 'auto'; _sndCache[name] = base; }
            var a = base.cloneNode(); a.volume = 0.5;
            var pr = a.play(); if (pr && pr.catch) { pr.catch(function () { }); }
        } catch (e) { /* 소리가 안 나도 게임은 계속 */ }
    };

    function names(ings) { return (ings || []).map(function (x) { return typeof x === 'string' ? x : x.name; }); }
    function soupKind(ings) {
        var o = ings.filter(function (n) { return n === 'onion'; }).length;
        var t = ings.filter(function (n) { return n === 'tomato'; }).length;
        return t === 0 ? 'o' : (o === 0 ? 't' : 'm');
    }

    class KenneyScene extends OvercookedScene {
        preload() {
            for (var i = 0; i < K_ASSETS.length; i++) {
                this.load.image('k_' + K_ASSETS[i], this.assets_loc + 'kenney/' + K_ASSETS[i] + '.png');
            }
        }

        create() {
            for (var i = 0; i < K_ASSETS.length; i++) {   // 축소해도 매끈하게(게임 설정은 pixelArt=true)
                this.textures.get('k_' + K_ASSETS[i]).setFilter(Phaser.Textures.FilterMode.LINEAR);
            }
            this._kObjs = [];
            this._kHud = [];
            this._kHudSig = null;
            this._kLast = null;
            this._kObjSig = null;
            this._kHeld = {};
            this._kFx = {};
            this._kPrev = null;
            ['put', 'ready', 'serve'].forEach(function (n) { try { var a = new Audio('static/assets/sounds/' + n + '.mp3'); a.preload = 'auto'; _sndCache[n] = a; } catch (e) { } });
            this.sprites = {};
            this.drawLevel();
            this._drawState(this.state, this.sprites);
        }

        // ── 칸(재료 칩) ─────────────────────────────────────────────
        _kChips(cx, y, items, size, depth) {
            var n = Math.max(3, items.length), gap = Math.max(2, Math.round(size / 8));
            var w = n * size + (n - 1) * gap;
            var c = this.add.container(cx, y);
            for (var i = 0; i < n; i++) {
                var key = i < items.length ? (items[i] === 'onion' ? 'k_chip_o' : 'k_chip_t') : 'k_chip_empty';
                var img = this.add.image(-w / 2 + i * (size + gap) + size / 2, size / 2, key);
                img.setDisplaySize(size, size);
                c.add(img);
            }
            c.setDepth(depth);
            return c;
        }

        // ── 바닥/타일 ───────────────────────────────────────────────
        drawLevel() {
            this.cameras.main.setBackgroundColor('#33333b');
            var T = this.tileSize;
            for (var row in this.terrain) {
                if (!this.terrain.hasOwnProperty(row)) { continue; }
                for (var col = 0; col < this.terrain[row].length; col++) {
                    var ch = this.terrain[row][col];
                    var key = K_TILE[ch] || ((Number(row) + col) % 2 === 0 ? 'floor_a' : 'floor_b');
                    var tile = this.add.image(T * col, T * Number(row), 'k_' + key);
                    tile.setOrigin(0);
                    tile.setDisplaySize(T, T);
                    if (ch === 'S') { this._kServeArrows(col, Number(row)); }
                }
            }
        }

        // 서빙 칸: 접시가 "나가는" 방향을 화살표로 강조. 바닥(' ')이 있는 쪽에서 칸 안쪽으로 향하고, 계속 깜박이며 움직인다.
        _kServeArrows(col, row) {
            var T = this.tileSize, t = this.terrain;
            var cand = [[0, -1], [-1, 0], [1, 0], [0, 1]];   // 위, 왼, 오른, 아래 이웃 확인
            var dir = [0, 1];                                 // 기본: 아래로
            for (var i = 0; i < cand.length; i++) {
                var nr = row + cand[i][1], nc = col + cand[i][0];
                if (nr >= 0 && nr < t.length && nc >= 0 && nc < t[nr].length && t[nr][nc] === ' ') {
                    dir = [-cand[i][0], -cand[i][1]];          // 이웃에서 이 칸으로 들어가는 방향
                    break;
                }
            }
            var angle = (dir[0] === 0 && dir[1] === 1) ? 0 : (dir[0] === 0 ? 180 : (dir[0] === -1 ? 90 : -90));
            var cx = T * col + T / 2, cy = T * row + T * 0.4;
            var img = this.add.image(cx, cy, 'k_serve_arrows');
            img.setDisplaySize(T * 0.86, T * 0.86).setAngle(angle).setDepth(0.5);
            this.tweens.add({ targets: img, x: cx + dir[0] * 4, y: cy + dir[1] * 4, alpha: 0.7,
                duration: 650, yoyo: true, repeat: -1, ease: 'Sine.easeInOut' });
        }

        // ── 상태(요리사/냄비/수프) ───────────────────────────────────
        _drawState(state, sprites) {
            if (typeof (state) === 'undefined' || state === null || state === this._kLast) { return; }
            this._kLast = state;
            sprites = typeof (sprites) === 'undefined' ? this.sprites : sprites;
            this._kDrawChefs(state, sprites);
            this._kDrawObjects(state);
            this._kSounds(state);
        }

        _kDrawChefs(state, sprites) {
            var T = this.tileSize;
            sprites['chefs'] = typeof (sprites['chefs']) === 'undefined' ? {} : sprites['chefs'];
            for (var pi = 0; pi < state.players.length; pi++) {
                var chef = state.players[pi];
                var x = chef.position[0], y = chef.position[1];
                var dir = DIRECTION_TO_NAME[chef.orientation];
                var key = 'k_chef_p' + (pi + 1) + '_' + dir;
                var held = chef.held_object, heldKey = null, heldChips = [], heldSize = 30;
                if (held) {
                    if (held.name === 'soup') {
                        heldChips = names(held._ingredients);
                        heldKey = 'k_item_soup_' + soupKind(heldChips); heldSize = 38;
                    } else if (held.name === 'onion') { heldKey = 'k_item_onion'; }
                    else if (held.name === 'tomato') { heldKey = 'k_item_tomato'; }
                    else if (held.name === 'dish') { heldKey = 'k_item_dish'; heldSize = 36; }
                }
                var hp = HELD_POS[dir] || HELD_POS.SOUTH;
                var chipY = y > 0 ? T * y - 22 : T * y + 60;
                var s = sprites['chefs'][pi];
                if (typeof (s) === 'undefined') {
                    var chefsprite = this.add.image(T * x - 8, T * y - 8, key);
                    chefsprite.setOrigin(0); chefsprite.setDisplaySize(96, 96); chefsprite.setDepth(1);
                    s = { chefsprite: chefsprite, heldsprite: null, chips: null, chipSig: '' };
                    sprites['chefs'][pi] = s;
                    s.firstPlaced = true;
                } else {
                    s.chefsprite.setTexture(key);
                }
                // 들고 있는 물건
                if (heldKey) {
                    if (!s.heldsprite) { s.heldsprite = this.add.image(T * x + hp[0], T * y + hp[1], heldKey); }
                    s.heldsprite.setTexture(heldKey);
                    s.heldsprite.setDisplaySize(heldSize, heldSize);
                    s.heldsprite.setDepth(dir === 'NORTH' ? 0.9 : 2);
                    s.heldsprite.setVisible(true);
                } else if (s.heldsprite) { s.heldsprite.setVisible(false); }
                // 수프를 들고 있으면 재료 칩 표시
                var sig = heldChips.join(',');
                if (s.chipSig !== sig) {
                    if (s.chips) { s.chips.destroy(); s.chips = null; }
                    if (heldChips.length) { s.chips = this._kChips(T * x + T / 2, chipY, heldChips, 22, 4); }
                    s.chipSig = sig;
                }
                // 위치(부드럽게 이동)
                var targets = [s.chefsprite];
                if (s.heldsprite) { targets.push(s.heldsprite); }
                if (s.chips) { targets.push(s.chips); }
                var goal = function (t, sp) {
                    if (t === sp.chefsprite) { return [T * x - 8, T * y - 8]; }
                    if (t === sp.heldsprite) { return [T * x + hp[0], T * y + hp[1]]; }
                    return [T * x + T / 2, chipY];
                };
                if (s.firstPlaced) {
                    s.firstPlaced = false;
                    for (var i = 0; i < targets.length; i++) { var g0 = goal(targets[i], s); targets[i].setPosition(g0[0], g0[1]); }
                } else {
                    for (var j = 0; j < targets.length; j++) {
                        var g = goal(targets[j], s);
                        if (targets[j] === s.heldsprite) { targets[j].setOrigin(0.5); }
                        this.tweens.add({ targets: targets[j], x: g[0], y: g[1], duration: this.animation_duration, ease: 'Linear' });
                    }
                }
            }
        }

        _kDrawObjects(state) {
            var sig = JSON.stringify(state.objects);
            if (sig === this._kObjSig) { return; }
            this._kObjSig = sig;
            for (var i = 0; i < this._kObjs.length; i++) { this._kObjs[i].destroy(); }
            this._kObjs = [];
            var T = this.tileSize, self = this;
            var add = function (o) { self._kObjs.push(o); return o; };
            var potsWithSoup = {};
            var fxWant = {};
            for (var p in state.objects) {
                if (!state.objects.hasOwnProperty(p)) { continue; }
                var obj = state.objects[p];
                var x = obj.position[0], y = obj.position[1];
                var cx = T * x + T / 2;
                if (obj.name === 'soup' && this.terrain[y][x] === 'P') {
                    potsWithSoup[x + ',' + y] = true;
                    var ings = names(obj._ingredients);
                    add(this._kChips(cx, T * y + 3, ings, 20, 3));
                    var cook = obj.cook_time || this.cook_time;
                    var ox = cx, oy = T * y + OPEN_Y, kind = soupKind(ings);
                    var cookingNow = !obj.is_ready && obj._cooking_tick >= 0 && ings.length >= 3;
                    // 냄비 속: 재료만 들어 있으면 물 + 재료 그림, 조리 중/완성이면 수프 색
                    if (ings.length) {
                        if (cookingNow || obj.is_ready) {
                            add(this.add.ellipse(ox, oy, 34, 19, SOUP_COLOR[kind], 1).setDepth(2));
                            add(this.add.ellipse(ox - 5, oy - 3, 12, 5, 0xffffff, obj.is_ready ? 0.35 : 0.2).setDepth(2));
                        } else {
                            add(this.add.ellipse(ox, oy, 34, 19, 0x8ea4b8, 1).setDepth(2));
                            var spots = [[-9, -1], [9, -1], [0, 4]];
                            for (var q = 0; q < ings.length && q < 3; q++) {
                                add(this.add.image(ox + spots[q][0], oy + spots[q][1], ings[q] === 'onion' ? 'k_item_onion' : 'k_item_tomato').setDisplaySize(16, 16).setDepth(2));
                            }
                        }
                    }
                    if (obj.is_ready) { fxWant[x + ',' + y] = { mode: 'steam', x: ox, y: oy }; }
                    else if (cookingNow) { fxWant[x + ',' + y] = { mode: 'bubble', x: ox, y: oy, color: SOUP_COLOR[kind] }; }
                    var g = add(this.add.graphics()); g.setDepth(3);
                    var cy = T * y + T * 0.62;
                    if (obj.is_ready) {
                        g.lineStyle(5, 0x3cc35a, 1); g.beginPath(); g.arc(cx, cy, 27, 0, Math.PI * 2, false); g.strokePath();
                        add(this.add.text(cx + 22, cy - 26, '✓', { font: 'bold 18px Arial', fill: '#ffffff', backgroundColor: '#2e9e4a', padding: { x: 4, y: 0 } }).setDepth(4).setOrigin(0.5));
                    } else if (obj._cooking_tick >= 0 && ings.length >= 3) {
                        var frac = Math.min(Math.max(obj._cooking_tick / cook, 0.02), 1);
                        g.lineStyle(5, 0xffc84a, 1); g.beginPath();
                        g.arc(cx, cy, 27, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * frac, false); g.strokePath();
                    }
                } else if (obj.name === 'soup') {
                    var ings2 = names(obj._ingredients);
                    add(this.add.image(cx, T * y + 36, 'k_item_soup_' + soupKind(ings2)).setDisplaySize(46, 46).setDepth(1));
                    add(this._kChips(cx, T * y + 3, ings2, 18, 3));
                } else {
                    var k = obj.name === 'onion' ? 'k_item_onion' : (obj.name === 'tomato' ? 'k_item_tomato' : 'k_item_dish');
                    add(this.add.image(cx, T * y + 34, k).setDisplaySize(36, 36).setDepth(1));
                }
            }
            this._kReconcileFx(fxWant);
            // 재료가 없는 냄비도 빈 칸 3개를 보여 준다
            for (var r in this.terrain) {
                if (!this.terrain.hasOwnProperty(r)) { continue; }
                for (var c = 0; c < this.terrain[r].length; c++) {
                    if (this.terrain[r][c] === 'P' && !potsWithSoup[c + ',' + r]) {
                        add(this._kChips(T * c + T / 2, T * Number(r) + 3, [], 20, 3));
                    }
                }
            }
        }

        // 상태 변화를 보고 소리를 낸다(서버 변경 없음): 점수↑=서빙, 익은 냄비↑=조리 완료, 냄비 재료↑=재료 넣기
        _kSounds(state) {
            var ing = 0, readyN = 0;
            for (var p in state.objects) {
                if (!state.objects.hasOwnProperty(p)) { continue; }
                var o = state.objects[p];
                if (o.name === 'soup' && this.terrain[o.position[1]][o.position[0]] === 'P') {
                    ing += (o._ingredients || []).length;
                    if (o.is_ready) { readyN++; }
                }
            }
            var cur = { ing: ing, readyN: readyN, score: Number(this.hud_data.score) || 0 };
            var prev = this._kPrev; this._kPrev = cur;
            if (!prev) { return; }
            if (cur.score > prev.score) { window.gamePlaySound('serve'); }
            else {
                if (cur.readyN > prev.readyN) { window.gamePlaySound('ready'); }
                if (cur.ing > prev.ing) { window.gamePlaySound('put'); }
            }
        }

        // ── 효과: 조리 중 거품 / 완성 증기 (냄비마다 하나, 상태가 바뀔 때만 교체) ──
        _kReconcileFx(want) {
            for (var key in this._kFx) {
                if (!this._kFx.hasOwnProperty(key)) { continue; }
                if (!want[key] || want[key].mode !== this._kFx[key].mode) {
                    this._kFx[key].timer.remove(false);
                    delete this._kFx[key];
                }
            }
            var self = this;
            for (var k in want) {
                if (!want.hasOwnProperty(k) || this._kFx[k]) { continue; }
                (function (w) {
                    var timer = self.time.addEvent({
                        delay: w.mode === 'steam' ? 240 : 330, loop: true,
                        callback: function () { self._kPuff(w); }
                    });
                    self._kFx[k] = { mode: w.mode, timer: timer };
                })(want[k]);
            }
        }

        _kPuff(w) {
            var jitter = Phaser.Math.Between(-9, 9);
            if (w.mode === 'steam') {
                var r = Phaser.Math.Between(8, 12);
                var c = this.add.circle(w.x + jitter, w.y - 4, r, 0xffffff, 0.75).setDepth(8);
                this.tweens.add({ targets: c, y: w.y - 44 - Phaser.Math.Between(0, 12), x: c.x + Phaser.Math.Between(-6, 6),
                    alpha: 0, scaleX: 1.9, scaleY: 1.9, duration: 1100, ease: 'Sine.easeOut',
                    onComplete: function () { c.destroy(); } });
            } else {
                var b = this.add.circle(w.x + jitter, w.y + Phaser.Math.Between(-3, 3), Phaser.Math.Between(2, 3), 0xffffff, 0.75).setDepth(8);
                this.tweens.add({ targets: b, y: b.y - 7, alpha: 0, scaleX: 1.5, scaleY: 1.5, duration: 450,
                    onComplete: function () { b.destroy(); } });
            }
        }

        // ── HUD: 주문서 카드 + 점수/시간 ──────────────────────────────
        _drawHUD(hud, sprites, board_height) {
            var sig = JSON.stringify([hud.all_orders, hud.time, hud.score, soundMuted()]);
            if (sig === this._kHudSig) { return; }
            this._kHudSig = sig;
            for (var i = 0; i < this._kHud.length; i++) { this._kHud[i].destroy(); }
            this._kHud = [];
            var self = this, W = this.game.canvas.width;
            var add = function (o) { self._kHud.push(o); return o; };
            // 같은 주문 묶기(처음 나온 순서 유지)
            var groups = [], idx = {};
            var orders = hud.all_orders || [];
            for (var o = 0; o < orders.length; o++) {
                var ings = names(orders[o]['ingredients']);
                var kk = ings.slice().sort().join(',');
                if (!idx.hasOwnProperty(kk)) { idx[kk] = groups.length; groups.push({ ings: ings.slice().sort().reverse(), n: 0 }); }
                groups[idx[kk]].n++;
            }
            var cw = 92, ch = 78, gap = 6, y0 = board_height + 8;
            var fit = Math.max(1, Math.floor((W - 4 + gap) / (cw + gap)));
            var shown = groups.length > fit ? fit - 1 : groups.length;
            for (var gI = 0; gI < shown; gI++) {
                var grp = groups[gI], x0 = 4 + gI * (cw + gap);
                var card = add(this.add.graphics()); card.setDepth(5);
                card.fillStyle(0xfaf6e8, 1); card.fillRoundedRect(x0, y0, cw, ch, 9);
                card.lineStyle(2, 0xbeb4a0, 1); card.strokeRoundedRect(x0, y0, cw, ch, 9);
                var chips = this._kChips(x0 + cw / 2, y0 + 7, grp.ings, 24, 6); add(chips);
                var no = grp.ings.filter(function (n) { return n === 'onion'; }).length;
                var nt = grp.ings.filter(function (n) { return n === 'tomato'; }).length;
                var lines = [];
                if (no) { lines.push('양파 ' + no); }
                if (nt) { lines.push('토마토 ' + nt); }
                for (var l = 0; l < lines.length; l++) {
                    add(this.add.text(x0 + cw / 2, y0 + 40 + l * 17, lines[l], { font: 'bold 14px Arial', fill: '#2a2a2a' }).setOrigin(0.5, 0).setDepth(6));
                }
                if (grp.n > 1) {
                    add(this.add.text(x0 + cw - 4, y0 - 2, '×' + grp.n, { font: 'bold 13px Arial', fill: '#ffffff', backgroundColor: '#d93c32', padding: { x: 5, y: 1 } }).setOrigin(1, 0.2).setDepth(7));
                }
            }
            if (shown < groups.length) {
                var extra = 0; for (var e = shown; e < groups.length; e++) { extra += groups[e].n; }
                add(this.add.text(4 + shown * (cw + gap) + 2, y0 + ch / 2, '+' + extra, { font: 'bold 20px Arial', fill: '#ffffff' }).setOrigin(0, 0.5).setDepth(6));
            }
            if (!orders.length) {
                add(this.add.text(8, y0 + 26, '주문 대기 중…', { font: '16px Arial', fill: '#aaaab5' }).setDepth(6));
            }
            var txt = '점수 ' + (hud.score === undefined ? 0 : hud.score) + '      남은 시간 ' + (hud.time === undefined ? '' : hud.time) + '초';
            var muted = soundMuted();
            var btn = add(this.add.text(W - 6, board_height + 94, muted ? '🔇 소리 꺼짐' : '🔊 소리 켜짐',
                { font: 'bold 14px Arial', fill: '#ffffff', backgroundColor: muted ? '#7a3b3b' : '#4a5a6a', padding: { x: 8, y: 4 } }
            ).setOrigin(1, 0).setDepth(6).setInteractive({ useHandCursor: true }));
            btn.on('pointerdown', function () { setSoundMuted(!soundMuted()); self._kHudSig = null; });
            add(this.add.text(6, board_height + 96, txt, { font: 'bold 20px Arial', fill: '#ffffff' }).setDepth(6));
        }
    }

    window.KenneyScene = KenneyScene;
})();
