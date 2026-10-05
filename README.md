# Overcooked-AI 협업 학습 실험 플랫폼

> **AI 봇과의 협업 학습이 사람 간 협업 소통으로의 전이 및 학습 스트레스에 미치는 영향**을 보기 위한 실험용 Overcooked-AI 웹 플랫폼입니다.
> 개발 중 (파일럿 전 단계)

이 저장소는 [HumanCompatibleAI/overcooked_ai](https://github.com/HumanCompatibleAI/overcooked_ai)(MIT)를 기반으로 합니다.
원본 README는 [`README.upstream.md`](README.upstream.md), 개발 이력 상세는 [`experiment/README.md`](experiment/README.md)에 있습니다.

---

## 1. 연구 개요

| 구분 | 내용 |
|---|---|
| 독립변인 | 학습 단계 파트너 유형: **AI 봇 vs 사람** |
| RQ1 | AI 봇과 연습한 사용자가 학습 스트레스(NASA-TLX)가 더 낮은가? |
| RQ2 | 이후 제3자와의 협업 성과(점수·완료 시간)가 달라지는가? |
| RQ3 | 인간 간 협업의 질(협응·소통·공유 정신 모델)이 달라지는가? |
| 로그로 측정 | 점수, 유휴·기능적 지연(협응), 핑-행동 일치·소통 효율(소통) |
| 설문으로 측정 | NASA-TLX, 공유 정신 모델(SMM) — 로그 아님 |

## 2. 실험 플랫폼이 하는 일

- **5단계 난이도**를 순서대로 진행 (각 60초): `cramped_room_mixed → asymmetric_advantages_mixed → coordination_ring_mixed → forced_coordination_mixed → counter_circuit`
- **주문 목록**: 시작 시 1개, **8초마다 1개** 추가(기한 없음). 목록의 메뉴와 **재료 구성이 정확히 같은** 수프를 서빙하면 점수(대부분 20점)를 얻고 그 주문이 사라짐. 목록에 없는 수프는 0점. 메뉴는 양파/토마토 조합 4종이 고르게, 난이도별로 모든 참가자에게 같은 순서로 나옴.
- **AI 봇**(규칙 기반, 예측 가능한 파트너): 가장 먼저 들어온 미처리 주문부터 만들고, 사람보다 느리게(행동 빈도 1/3) 움직임. 서빙도 함께 함. 두 플레이어가 서로 다른 구역에서 일하는 `forced_coordination`에서는 재료·접시를 가운데 카운터에 공급하는 "공급자" 역할.
- **소통 핑**(숫자키 1~4 또는 버튼): `1 고마워` · `2 비켜줘` · `3 미안해` · `4 OK`. 봇은 비켜줘에는 잠깐 생각한 뒤(≈0.8초) OK 말풍선과 함께 비켜주고, 고마워/미안해에는 "천만에"/"괜찮아"로 답함.
- **조작**: 방향키 이동, 스페이스바 상호작용(줍기/놓기/요리 시작/서빙). 키 눌림·뗌을 이벤트로 처리해 한 번 누르면 정확히 1칸.
- **로그**: 닉네임(필수 입력)과 함께 라운드별로 저장.

## 3. 실행 방법 (Windows)

```bat
git clone <이 저장소>
cd overcooked_ai
python -m venv .venv
.venv\Scripts\activate
pip install -e .
pip install -r src\overcooked_demo\server\requirements.txt   (서버 의존성; 이미 동작하는 .venv가 있으면 생략)

cd src\overcooked_demo\server
set PORT=5001
set HOST=127.0.0.1
set FLASK_ENV=production
:: (선택) 로그 저장 폴더 지정. 기본값은 <저장소>\data\game_logs. OneDrive 밖을 권장
set OVERCOOKED_DATA_DIR=D:\logs
python app.py
```

브라우저에서 `http://127.0.0.1:5001/predefined` 접속 → 닉네임 입력 → 시작.
`set` 값은 그 cmd 창에서만 유지됩니다. 서버를 수정/재시작한 뒤에는 브라우저에서 **Ctrl+F5**로 새로고침하세요.
연구자용 설정(시작 화면): 학습 조건(AI 봇/사람), 시작 난이도(1개만 테스트) 선택 가능.

> 인간-인간 조건은 두 참가자가 각자 컴퓨터에서 같은 서버에 접속해 "시작하기"를 누르면 자동으로 한 게임에 연결됩니다.

## 4. 원본과의 관계 — "diff = 실험 개입"

원본 `game.py`, `overcooked_mdp.py`는 **수정하지 않고**, 실험에 필요한 동작은 모두 `experiment/`의 믹스인/서브클래스로 구현했습니다. 원본 서버 코드의 변경은 `app.py`(클래스 교체·닉네임 전달·난이도 순서)와 `predefined.*`(시작 화면·입력·핑 UI), `instructions.html`, `config.json`, 새 레이아웃 파일뿐입니다.

```
experiment/
  agents/role_restricted_bot.py   봇(역할 제한·핑 반응·혼합 주문·공급자)
  server_ext/
    ping_game.py                  게임 클래스 조립 + 핑 채널
    order_queue.py                주문 큐(8초 간격·추첨)
    human_input.py                키 눌림/뗌 입력 처리
    data_log.py                   닉네임·로그 저장(재시도·중도 종료)
    pickle_agent.py               봇을 서버용 agent.pickle로 저장
    test_ping_logic.py            단위/통합 테스트
  analysis/
    round_events.py               로그에서 사건 복원 + 지표 계산
    analyze_session.py            세션 지표표(CSV)
    summarize_log.py              한 라운드 요약
    compute_metrics.py            참가자 단위 CSV
    simulate_pair.py / simulate_orders.py   봇 점검용 시뮬레이션
src/overcooked_ai_py/data/layouts/*_mixed.layout   토마토를 추가한 실험 레이아웃
```

## 5. 주요 파라미터 (코드 상수)

| 항목 | 값 | 위치 |
|---|---|---|
| 라운드 시간 / 서버 틱 | 60초 / 10fps | `config.json` (`gameTime`, `MAX_FPS`) |
| 주문 간격 / 시작 주문 수 | 8초 / 1개 | `order_queue.py` |
| 봇 속도 | 사람의 1/3 (`BOT_SPEED_DIVISOR=3`) | `role_restricted_bot.py` |
| 봇 서빙 허용 | `BOT_MAY_DELIVER=True` | `role_restricted_bot.py` |
| 핑 이해 지연 / 효과 시간 | 0.8초 / 약 10초 | `PING_ACK_DELAY_STEPS`, `PING_EFFECT_STEPS` |

로그의 `meta`에 실험 당시 값(봇 속도, 주문 간격 등)이 같이 기록됩니다.

## 6. 로그와 분석

- 저장: `<로그폴더>/<시작시각>_<HH|HA>_<닉네임>/round<N>_<레이아웃>.pkl` + `index.csv` (라운드마다 한 줄)
- 분석: `python experiment\analysis\analyze_session.py <로그폴더> --csv out.csv`
- 지표: 점수·배달 수·0점 배달·주문 대기시간 / 유휴·막힌 이동·기능적 지연(수프 완성→수습)·인계 지연 / 핑 종류별 수·소통 효율·비켜줘 일치율
- 해석 주의: 봇의 STAY 비율은 속도 제한 때문에 원래 높음(`waiting_ratio` 사용), `counter_circuit` 점수는 원본 체계라 다른 판과 규모가 다름(배달 수로 비교).
- **개인정보**: 로그에는 참가자 닉네임이 들어 있어 `.gitignore`로 git에서 제외했습니다(`data/game_logs/`). 저장소에 올리지 마세요.

## 7. 테스트

```bat
set PYTHONPATH=.;src
python experiment\server_ext\test_ping_logic.py
python experiment\smoke_test.py
```

## 8. 현재 상태 / 남은 일

- 구현됨: 봇·핑·주문 큐·닉네임/로그·입력 개선·혼합 주문 레이아웃·분석 도구
- 파일럿 전 점검: 난이도 균형(8초 간격), 봇 정지/막힘 구간, 핑 구성, 인간-인간 조건 로그
- 개발 이력: [`experiment/README.md`](experiment/README.md)

## 라이선스 / 출처

MIT (원본 [LICENSE](LICENSE) 유지). 환경: Carroll et al. (2019), *On the Utility of Learning about Humans for Human-AI Coordination*, NeurIPS.
