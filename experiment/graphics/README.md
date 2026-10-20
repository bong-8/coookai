# Kenney 스킨 (선택 그래픽)

- 켜기: 접속 주소 뒤에 `?skin=kenney`. 없으면 원본 그래픽(실험 기준선).
- 코드: `src/overcooked_demo/server/static/js/graphics_kenney.js` (원본 `graphics.js`의 장면을 상속, 그리는 부분만 교체)
- 그림: `src/overcooked_demo/server/static/assets/kenney/*.png`
- 출처: Kenney "Food Kit 2.0", "Mini Characters 1.0" (CC0, https://kenney.nl). 요리사 모자는 직접 모델링. 크레딧은 의무가 아니지만 `assets/kenney/CREDITS.txt`에 적어 둠.

## 그림 다시 만들기 (필요할 때만)
원본 zip 두 개를 풀어 `GLB_src/`(Mini Characters의 Models)와 `FOOD_src/`(Food Kit의 Models)로 두고
`npm i three@0.160.0`, `pip install playwright` 후 이 폴더에서:
1. `python render_chefs.py`  → out/p1_*.png, p2_*.png (요리사 4방향)
2. `python render_items.py`  → items/*.png (재료·접시·냄비)
3. `python build_assets.py <출력폴더>` → 게임용 PNG
(요리사: P1=character-male-b + 파란 모자, P2=character-female-e + 초록 모자)
