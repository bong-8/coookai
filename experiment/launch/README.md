# 다른 PC에서 서버 실행하기 (교수님 PC·다른 노트북)

서버는 **PC 한 대에서만** 실행하고, 참가자는 같은 와이파이의 브라우저로 접속합니다.
이 폴더의 실행기는 설치가 까다로운 ray/tensorflow 없이도 서버가 돌도록 만든 것입니다(규칙 기반 봇만 사용).

## 준비물
- Windows PC, **Python 3.9~3.12** (python.org에서 설치. 설치 때 "Add Python to PATH" 체크)
- 이 저장소(`git clone` 또는 zip)

## 실행 (두 가지 중 하나)
1. 탐색기에서 `experiment\launch\start_server.bat` 더블클릭
2. 또는 cmd에서
   ```bat
   cd 저장소폴더
   python -m venv .venv
   .venv\Scripts\python -m pip install -r experiment\launch\requirements_light.txt
   .venv\Scripts\python experiment\launch\run_server.py
   ```

처음 한 번은 패키지 설치와 **계산 캐시 생성(약 5분, counter_circuit가 가장 오래 걸림)** 이 진행됩니다.
이후에는 몇 초 만에 시작합니다. 캐시를 미리 만들어 두는 이유: 없으면 라운드 전환 때 서버 전체가 수 분 멈춥니다.
(이태형 PC의 `src\overcooked_ai_py\data\planners\*.pkl`을 같은 폴더로 복사하면 이 단계를 건너뜁니다.
복사한 파일이 안 맞으면 자동으로 다시 계산합니다.)

실행이 끝나면 접속 주소가 표시됩니다.
```
이 PC에서:               http://127.0.0.1:5001/predefined
같은 와이파이의 다른 PC:  http://192.168.x.x:5001/predefined
```

## 접속이 안 될 때
- Windows 방화벽 창이 뜨면 "허용". 놓쳤다면 방화벽 설정에서 포트 5001(TCP) 인바운드를 허용.
- 학교·카페 와이파이는 기기 간 통신을 막는 경우가 많음 → 서버 PC에서 휴대폰 핫스팟을 켜고 모두 그 핫스팟에 접속.
- 서로 다른 장소: 서버 PC에서 `cloudflared tunnel --url http://localhost:5001` 로 임시 공개 주소를 만들어 공유(시연 후 종료).

## 사람-사람 조건 확인 방법
두 사람이 같은 주소로 접속 → 닉네임 입력 → "학습 조건"을 "다른 참가자와 연습"으로 선택 →
한 명이 먼저 "시작하기", 다른 한 명이 이어서 "시작하기". 서버 PC에서도 브라우저 창 하나를 참가자로 쓸 수 있습니다.

## 로그 위치
기본 `<저장소>\data\game_logs`. 바꾸려면 실행 전 `set OVERCOOKED_DATA_DIR=D:\logs`.
