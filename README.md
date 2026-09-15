# gcp-compute-engine-chatbot

GoogleCloud의 Compute Engine을 활용하여 ChatBot 구현.

## 로컬 웹 챗봇 (1단계)

브라우저에서 동작하는 Gemini 기반 챗봇. 외부 의존성 없이 Node 내장 모듈만 사용한다.

### 실행

```bash
# 1) API 키 등록 (둘 중 하나)   
export GEMINI_API_KEY="발급받은_키"      # 현재 셸에만 적용
cp .env.example .env && vi .env          # .env 파일로 관리 (권장)

# 2) 서버 실행
npm start            # http://localhost:3000
```

키는 서버 프로세스의 환경변수(`GEMINI_API_KEY`, 없으면 `GOOGLE_API_KEY`)에서만 읽는다.
브라우저로는 절대 내려가지 않고, 모든 호출은 `server.js`가 프록시한다.

### 구성

| 파일 | 역할 |
| --- | --- |
| [server.js](server.js) | 정적 파일 서빙 + `/api/models` + `/api/chat` (SSE 스트리밍 프록시) |
| [public/index.html](public/index.html) | 화면 구조 (상단 모델 선택, 인사말, 입력창) |
| [public/styles.css](public/styles.css) | gemini.google.com/app 스타일 다크 테마 입력창 |
| [public/app.js](public/app.js) | 모델 선택, 자동 높이 입력창, 스트리밍 렌더링 |

### 모델 선택

기본 후보는 `gemini-3.8-flash`, `gemini-3.7-flash` 이다.
`/api/models`가 API 키로 `ListModels`를 호출해서 실제 사용 가능한지 확인하고,
해당 ID가 없으면 키로 접근 가능한 flash 계열 모델을 최신순으로 대신 노출한다.

원하는 모델을 직접 지정하려면:

```bash
export GEMINI_MODELS="gemini-2.5-flash,gemini-2.0-flash"   # 첫 번째가 기본값
```

### 조작

- `Enter` 전송, `Shift+Enter` 줄바꿈
- 우측 상단 아이콘: 새 채팅 (대화 기록 초기화)
- `+`, `도구`, `마이크` 버튼은 레이아웃 재현용 자리표시자 (동작 없음)
