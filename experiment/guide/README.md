# 자동 가이드 (조건 1: 규칙 봇 + 가이드)

- 대본: `script_ko.json` (초안 — 교수님 검토 후 수정). 트리거가 맞으면 게임이 멈추고 대본이 뜬다. Enter로 재개.
- 선택 화면 "학습 조건" → "AI 봇과 연습 + 자동 가이드".
- 음성: 브라우저 내장 TTS(ko-KR). 한국어 음성이 없으면 텍스트만 나온다.
- LLM: 지금은 대본 문장 그대로. `guide_game.set_llm_client(fn)` 로 `fn(prompt, timeout)->str` 을 꽂으면 문장만 다듬고, 실패·지연 시 대본으로 대체된다.
- 테스트: `python -m pytest experiment/server_ext/test_guide_logic.py -q`
