/*

Added state potential to HUD

*/



// How long a graphics update should take in milliseconds
//
// 원래 주석은 "서버가 30fps로 갱신한다"였지만 실제로는(2026-10-04 확인)
// app.py의 play_game 루프가 6fps(약 167ms 간격)로 하드코딩되어 있었다 —
// config.json의 MAX_FPS=30은 로드만 되고 어디에도 안 쓰이는 죽은 값이었다.
// 그 상태에서 ANIMATION_DURATION=50ms였으니, 한 틱(167ms)마다 캐릭터가
// 50ms 동안만 미끄러지듯 움직이고 나머지 117ms는 가만히 서 있는 것처럼
// 보였다 — "유저 조작이 매끄럽지 않다"는 피드백의 실제 원인 중 하나.
// app.py에서 틱 속도를 실제로 10fps(약 100ms 간격)로 올렸으므로, 그에
// 맞춰 애니메이션 길이도 늘려 한 틱의 대부분을 글라이드가 채우게 했다
// (다음 상태가 네트워크 지연으로 늦게 와도 트윈이 끊기지 않도록 약간의
// 여유만 남김).
var ANIMATION_DURATION = 85;

var DIRECTION_TO_NAME = {
    '0,-1': 'NORTH',
    '0,1': 'SOUTH',
    '1,0': 'EAST',
    '-1,0': 'WEST'
};

// Phase 2: 핑 소통 채널. 서버(experiment/server_ext/ping_game.py의 PingMixin)가
// get_state()에 얹어 보내는 "pings": {player_idx(문자열): ping_type} 을 캐릭터
// 머리 위 말풍선으로 그린다. 텍스트/이모지만 쓰고 별도 이미지 에셋은 없다.
// move(비켜줘)는 2026-10-04에 look(이거 봐) 자리를 교체했다 — 서버
// (experiment/server_ext/ping_game.py, VALID_PING_TYPES)와 반드시 맞춰야 함.
var PING_LABELS = {
    help: '🙋 도와줘',
    move: '🙅 비켜줘',
    mine: '🙋‍♂️ 내가 할게',
    ok: '👍 OK'
};

var scene_config = {
    player_colors : {0: 'blue', 1: 'green'},
    tileSize : 80,
    animation_duration : ANIMATION_DURATION,
    show_post_cook_time : false,
    cook_time : 20,
    assets_loc : "./static/assets/",
    hud_size : 150
};

var game_config = {
    type: Phaser.WEBGL,
    pixelArt: true,
    audio: {
        noAudio: true
    }
};

var graphics;

// Invoked at every state_pong event from server
function drawState(state) {
    // Try catch necessary because state pongs can arrive before graphics manager has finished initializing
    try {
        graphics.set_state(state);
    } catch {
        console.log("error updating state");
    }
};

// Invoked at 'start_game' event
function graphics_start(graphics_config) {
    graphics = new GraphicsManager(game_config, scene_config, graphics_config);
};

// Invoked at 'end_game' event
function graphics_end() {
    graphics.game.renderer.destroy();
    graphics.game.loop.stop();
    graphics.game.destroy();
}

class GraphicsManager {
    constructor(game_config, scene_config, graphics_config) {
        let start_info = graphics_config.start_info;
        scene_config.terrain = start_info.terrain;
        scene_config.start_state = start_info.state;
        game_config.scene = new OvercookedScene(scene_config);
        game_config.width = scene_config.tileSize*scene_config.terrain[0].length;
        game_config.height = scene_config.tileSize*scene_config.terrain.length  + scene_config.hud_size;
        game_config.parent = graphics_config.container_id;
        this.game = new Phaser.Game(game_config);
    }

    set_state(state) {
        this.game.scene.getScene('PlayGame').set_state(state);
    }
}

class OvercookedScene extends Phaser.Scene {
    constructor(config) {
        super({key: "PlayGame"});
        this.state = config.start_state.state;
        this.player_colors = config.player_colors;
        this.terrain = config.terrain;
        this.tileSize = config.tileSize;
        this.animation_duration = config.animation_duration;
        this.show_post_cook_time = config.show_post_cook_time;
        this.cook_time = config.cook_time;
        this.assets_loc = config.assets_loc;
        this.hud_size = config.hud_size
        this.hud_data = {
            potential : config.start_state.potential,
            score : config.start_state.score,
            time : config.start_state.time_left,
            bonus_orders : config.start_state.state.bonus_orders,
            all_orders : config.start_state.state.all_orders
        }
    }

    set_state(state) {
        this.hud_data.potential = state.potential;
        this.hud_data.score = state.score;
        this.hud_data.time = Math.round(state.time_left);
        this.hud_data.bonus_orders = state.state.bonus_orders;
        this.hud_data.all_orders = state.state.all_orders;
        this.state = state.state;
        // Phase 2: {player_idx(문자열): ping_type}, 없으면 빈 객체
        this.pings = state.pings || {};
    }

    preload() {
        this.load.atlas("tiles",
            this.assets_loc + "terrain.png",
            this.assets_loc + "terrain.json");
        this.load.atlas("chefs",
            this.assets_loc + "chefs.png",
            this.assets_loc + "chefs.json");
        this.load.atlas("objects",
            this.assets_loc + "objects.png",
            this.assets_loc + "objects.json");
        this.load.multiatlas("soups",
            this.assets_loc + "soups.json",
            this.assets_loc)
    }

    create() {
        this.sprites = {};
        this.drawLevel();
        this._drawState(this.state, this.sprites);
    }

    update() {
        if (typeof(this.state) !== 'undefined') {
            this._drawState(this.state, this.sprites);
        }
        this._drawPings(this.pings, this.sprites);
        if (typeof(this.hud_data) !== 'undefined') {
            let { width, height } = this.game.canvas;
            let board_height = height - this.hud_size;
            this._drawHUD(this.hud_data, this.sprites, board_height);
        }
    }

    // Phase 2: 핑을 보낸 플레이어의 캐릭터 머리 위에 텍스트 말풍선을 그린다.
    // 서버가 더 이상 그 핑을 보내지 않으면(=PING_DISPLAY_TICKS 만료) 자동으로 지운다.
    _drawPings(pings, sprites) {
        pings = typeof(pings) === 'undefined' ? {} : pings;
        sprites['ping_bubbles'] =
            typeof(sprites['ping_bubbles']) === 'undefined' ? {} : sprites['ping_bubbles'];

        // 더 이상 활성화되지 않은 말풍선은 제거
        for (let pi in sprites['ping_bubbles']) {
            if (!sprites['ping_bubbles'].hasOwnProperty(pi)) { continue; }
            if (!pings.hasOwnProperty(pi)) {
                sprites['ping_bubbles'][pi].destroy();
                delete sprites['ping_bubbles'][pi];
            }
        }

        for (let pi in pings) {
            if (!pings.hasOwnProperty(pi)) { continue; }
            let chefSprites = sprites['chefs'] && sprites['chefs'][pi];
            if (typeof(chefSprites) === 'undefined') { continue; } // 아직 캐릭터가 안 그려졌으면 스킵

            let label = PING_LABELS[pings[pi]] || pings[pi];
            let x = chefSprites.chefsprite.x + this.tileSize / 2;
            let y = chefSprites.chefsprite.y - 10;

            if (typeof(sprites['ping_bubbles'][pi]) === 'undefined') {
                sprites['ping_bubbles'][pi] = this.add.text(x, y, label, {
                    font: "16px Arial",
                    fill: "#ffffff",
                    backgroundColor: "#000000cc",
                    padding: { x: 6, y: 3 }
                }).setOrigin(0.5, 1).setDepth(10);
            } else {
                let bubble = sprites['ping_bubbles'][pi];
                bubble.setText(label);
                bubble.setPosition(x, y);
            }
        }
    }
    drawLevel() {
        // Fill canvas with white
        this.cameras.main.setBackgroundColor('#e6b453')

        //draw tiles
        let terrain_to_img = {
            ' ': 'floor.png',
            'X': 'counter.png',
            'P': 'pot.png',
            'O': 'onions.png',
            'T': 'tomatoes.png',
            'D': 'dishes.png',
            'S': 'serve.png'
        };
        let pos_dict = this.terrain;
        for (let row in pos_dict) {
            if (!pos_dict.hasOwnProperty(row)) {continue}
            for (let col = 0; col < pos_dict[row].length; col++) {
                let [x, y] = [col, row]
                let ttype = pos_dict[row][col];
                let tile = this.add.sprite(
                    this.tileSize * x,
                    this.tileSize * y,
                    "tiles",
                    terrain_to_img[ttype]
                );
                tile.setDisplaySize(this.tileSize, this.tileSize);
                tile.setOrigin(0);
            }
        }
    }
    _drawState (state, sprites) {
        sprites = typeof(sprites) === 'undefined' ? {} : sprites;

        //draw chefs
        sprites['chefs'] =
            typeof(sprites['chefs']) === 'undefined' ? {} : sprites['chefs'];
        for (let pi = 0; pi < state.players.length; pi++) {
            let chef = state.players[pi];
            let [x, y] = chef.position;
            let dir = DIRECTION_TO_NAME[chef.orientation];
            let held_obj = chef.held_object;
            if (typeof(held_obj) !== 'undefined' && held_obj !== null) {
                if (held_obj.name === 'soup') {
                    let ingredients = held_obj._ingredients.map(x => x['name']);
                    if (ingredients.includes('onion')) {
                        held_obj = "-soup-onion";
                    } else {
                        held_obj = "-soup-tomato";
                    }
                    
                }
                else {
                    held_obj = "-"+held_obj.name;
                }
            }
            else {
                held_obj = "";
            }
            if (typeof(sprites['chefs'][pi]) === 'undefined') {
                let chefsprite = this.add.sprite(
                    this.tileSize*x,
                    this.tileSize*y,
                    "chefs",
                    `${dir}${held_obj}.png`
                );
                chefsprite.setDisplaySize(this.tileSize, this.tileSize);
                chefsprite.depth = 1;
                chefsprite.setOrigin(0);
                let hatsprite = this.add.sprite(
                    this.tileSize*x,
                    this.tileSize*y,
                    "chefs",
                    `${dir}-${this.player_colors[pi]}hat.png`
                );
                hatsprite.setDisplaySize(this.tileSize, this.tileSize);
                hatsprite.depth = 2;
                hatsprite.setOrigin(0);
                sprites['chefs'][pi] = {chefsprite, hatsprite};
            }
            else {
                let chefsprite = sprites['chefs'][pi]['chefsprite'];
                let hatsprite = sprites['chefs'][pi]['hatsprite'];
                chefsprite.setFrame(`${dir}${held_obj}.png`);
                hatsprite.setFrame(`${dir}-${this.player_colors[pi]}hat.png`);
                this.tweens.add({
                    targets: [chefsprite, hatsprite],
                    x: this.tileSize*x,
                    y: this.tileSize*y,
                    duration: this.animation_duration,
                    ease: 'Linear',
                    onComplete: (tween, target, player) => {
                        target[0].setPosition(this.tileSize*x, this.tileSize*y);
                        //this.animating = false;
                    }
                })
            }
        }

        //draw environment objects
        if (typeof(sprites['objects']) !== 'undefined') {
            for (let objpos in sprites.objects) {
                let {objsprite, timesprite} = sprites.objects[objpos];
                objsprite.destroy();
                if (typeof(timesprite) !== 'undefined') {
                    timesprite.destroy();
                }
            }
        }
        sprites['objects'] = {};

        for (let objpos in state.objects) {
            if (!state.objects.hasOwnProperty(objpos)) { continue }
            let obj = state.objects[objpos];
            let [x, y] = obj.position;
            let terrain_type = this.terrain[y][x];
            let spriteframe;
            let soup_status;
            if ((obj.name === 'soup') && (terrain_type === 'P')) {
                let ingredients = obj._ingredients.map(x => x['name']);

                // select pot sprite
                if (!obj.is_ready) {
                    soup_status = "idle";
                }
                else {
                    soup_status = "cooked";
                }
                spriteframe = this._ingredientsToSpriteFrame(ingredients, soup_status);
                let objsprite = this.add.sprite(
                    this.tileSize*x,
                    this.tileSize*y,
                    "soups",
                    spriteframe
                );
                objsprite.setDisplaySize(this.tileSize, this.tileSize);
                objsprite.depth = 1;
                objsprite.setOrigin(0);
                let objs_here = {objsprite};

                // show time accordingly
                let show_time = true;
                if (obj._cooking_tick > obj.cook_time && !this.show_post_cook_time || obj._cooking_tick == -1) {
                    show_time = false;
                }
                if (show_time) {
                    let timesprite =  this.add.text(
                        this.tileSize*(x+.5),
                        this.tileSize*(y+.6),
                        String(obj._cooking_tick),
                        {
                            font: "25px Arial",
                            fill: "red",
                            align: "center",
                        }
                    );
                    timesprite.depth = 2;
                    objs_here['timesprite'] = timesprite;
                }

                sprites['objects'][objpos] = objs_here
            }
            else if (obj.name === 'soup') {
                let ingredients = obj._ingredients.map(x => x['name']);
                let soup_status = "done";
                spriteframe = this._ingredientsToSpriteFrame(ingredients, soup_status);
                let objsprite = this.add.sprite(
                    this.tileSize*x,
                    this.tileSize*y,
                    "soups",
                    spriteframe
                );
                objsprite.setDisplaySize(this.tileSize, this.tileSize);
                objsprite.depth = 1;
                objsprite.setOrigin(0);
                sprites['objects'][objpos] = {objsprite};
            }
            else {
                if (obj.name === 'onion') {
                    spriteframe = "onion.png";
                }
                else if (obj.name === 'tomato') {
                    spriteframe = "tomato.png";
                }
                else if (obj.name === 'dish') {
                    spriteframe = "dish.png";
                }
                let objsprite = this.add.sprite(
                    this.tileSize*x,
                    this.tileSize*y,
                    "objects",
                    spriteframe
                );
                objsprite.setDisplaySize(this.tileSize, this.tileSize);
                objsprite.depth = 1;
                objsprite.setOrigin(0);
                sprites['objects'][objpos] = {objsprite};
            }
        }        
    }

    _drawHUD(hud_data, sprites, board_height) {
        if (typeof(hud_data.all_orders) !== 'undefined') {
            this._drawAllOrders(hud_data.all_orders, sprites, board_height);
        }
        if (typeof(hud_data.bonus_orders) !== 'undefined') {
            this._drawBonusOrders(hud_data.bonus_orders, sprites, board_height);
        }
        if (typeof(hud_data.time) !== 'undefined') {
            this._drawTimeLeft(hud_data.time, sprites, board_height);
        }
        if (typeof(hud_data.score) !== 'undefined') {
            this._drawScore(hud_data.score, sprites, board_height);
        }
        if (typeof(hud_data.potential) !== 'undefined' && hud_data.potential !== null) {
            console.log(hud_data.potential)
            this._drawPotential(hud_data.potential, sprites, board_height);
        }
    }

    // 오더 표시: "양파 수프 x2" 텍스트로 바꿔봤다가 실제 플레이테스트에서
    // "기존 아이콘 형태가 낫다"는 피드백을 받아 원본(공식 공개 데모) 그대로의
    // 아이콘 렌더링으로 되돌렸다(2026-10-04). _orderIngredientsLabel/
    // _ordersToText 두 헬퍼는 더 안 쓰이지만, 나중에 "아이콘 + 텍스트 라벨"
    // 조합처럼 다시 쓸 수도 있어 남겨둔다.
    _orderIngredientsLabel(ingredients) {
        let names = ingredients.map(x => x['name']);
        let numOnion = names.filter(n => n === 'onion').length;
        let numTomato = names.filter(n => n === 'tomato').length;
        let parts = [];
        if (numOnion > 0) { parts.push(`양파${numOnion}`); }
        if (numTomato > 0) { parts.push(`토마토${numTomato}`); }
        if (parts.length === 0) { return "수프"; }
        return parts.join('+') + " 수프";
    }

    _ordersToText(orders) {
        let counts = {};
        let order = [];
        for (let i = 0; i < orders.length; i++) {
            let label = this._orderIngredientsLabel(orders[i]['ingredients']);
            if (!counts.hasOwnProperty(label)) {
                counts[label] = 0;
                order.push(label);
            }
            counts[label] += 1;
        }
        return order.map(label => counts[label] > 1 ? `${label} x${counts[label]}` : label).join(', ');
    }

    _drawBonusOrders(orders, sprites, board_height) {
        if (typeof(orders) !== 'undefined' && orders !== null) {
            let orders_str = "보너스: ";
            if (typeof(sprites['bonus_orders']) !== 'undefined') {
                // Clear existing orders
                sprites['bonus_orders']['orders'].forEach(element => {
                    element.destroy();
                });
                sprites['bonus_orders']['orders'] = [];

                // Update with new orders
                for (let i = 0; i < orders.length; i++) {
                    let spriteFrame = this._ingredientsToSpriteFrame(orders[i]['ingredients'], "done");
                    let orderSprite = this.add.sprite(
                        130 + 40 * i,
                        board_height + 40,
                        "soups",
                        spriteFrame
                    );
                    sprites['bonus_orders']['orders'].push(orderSprite);
                    orderSprite.setDisplaySize(60, 60);
                    orderSprite.setOrigin(0);
                    orderSprite.depth = 1;
                }
            }
            else {
                sprites['bonus_orders'] = {};
                sprites['bonus_orders']['str'] = this.add.text(
                    5, board_height + 60, orders_str,
                    {
                        font: "20px Arial",
                        fill: "red",
                        align: "left"
                    }
                )
                sprites['bonus_orders']['orders'] = []
            }
        }
    }

    _drawAllOrders(orders, sprites, board_height) {
        if (typeof(orders) !== 'undefined' && orders !== null) {
            let orders_str = "주문 목록: ";  // 2026-10-05: 서버가 "지금 열려 있는 주문"만 내려줌(order_queue.py)
            if (typeof(sprites['all_orders']) !== 'undefined') {
                // Clear existing orders
                sprites['all_orders']['orders'].forEach(element => {
                    element.destroy();
                });
                sprites['all_orders']['orders'] = [];

                // Update with new orders
                for (let i = 0; i < orders.length; i++) {
                    let spriteFrame = this._ingredientsToSpriteFrame(orders[i]['ingredients'], "done");
                    let orderSprite = this.add.sprite(
                        90 + 40 * i,
                        board_height - 4,
                        "soups",
                        spriteFrame
                    );
                    sprites['all_orders']['orders'].push(orderSprite);
                    orderSprite.setDisplaySize(60, 60);
                    orderSprite.setOrigin(0);
                    orderSprite.depth = 1;
                }
            }
            else {
                sprites['all_orders'] = {};
                sprites['all_orders']['str'] = this.add.text(
                    5, board_height + 15, orders_str,
                    {
                        font: "20px Arial",
                        fill: "red",
                        align: "left"
                    }
                )
                sprites['all_orders']['orders'] = []
            }
        }
    }

    _drawScore(score, sprites, board_height) {
        score = "Score: "+score;
        if (typeof(sprites['score']) !== 'undefined') {
            sprites['score'].setText(score);
        }
        else {
            sprites['score'] = this.add.text(
                5, board_height + 90, score,
                {
                    font: "20px Arial",
                    fill: "red",
                    align: "left"
                }
            )
        }
    }

    _drawPotential(potential, sprites, board_height) {
        potential = "Potential: "+potential;
        if (typeof(sprites['potential']) !== 'undefined') {
            sprites['potential'].setText(potential);
        }
        else {
            sprites['potential'] = this.add.text(
                100, board_height + 90, potential,
                {
                    font: "20px Arial",
                    fill: "red",
                    align: "left"
                }
            )
        }
    }

    _drawTimeLeft(time_left, sprites, board_height) {
        time_left = "Time Left: "+time_left;
        if (typeof(sprites['time_left']) !== 'undefined') {
            sprites['time_left'].setText(time_left);
        }
        else {
            sprites['time_left'] = this.add.text(
                5, board_height + 115, time_left,
                {
                    font: "20px Arial",
                    fill: "red",
                    align: "left"
                }
            )
        }
    }

    _ingredientsToSpriteFrame(ingredients, status) {
        let num_tomatoes = ingredients.filter(x => x === 'tomato').length;
        let num_onions = ingredients.filter(x => x === 'onion').length;
        return `soup_${status}_tomato_${num_tomatoes}_onion_${num_onions}.png`
    }
}

