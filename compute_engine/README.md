# gcp-compute-engine-chatbot

Google Cloud Compute Engine에 올려 운영하는 Gemini 기반 웹 챗봇.

## 기능

- Gemini 스트리밍 응답 (SSE)
- Google Search grounding — 모델이 필요하다고 판단하면 자동으로 웹 검색 후 답변에 반영, 답변 아래에 출처 링크 표시
- 좌측 대화 히스토리 패널 — 브라우저 `localStorage`에 저장, 클릭하면 이어서 채팅 가능
- 모델 드롭다운 — API 키로 실제 사용 가능한 모델을 조회해 노출

## 로컬 실행

```bash
# 1) API 키 등록 (둘 중 하나)
export GEMINI_API_KEY="발급받은_키"      # 현재 셸에만 적용
cp .env.example .env && vi .env          # .env 파일로 관리 (권장)

# 2) 서버 실행
python3 server.py    # http://localhost:3000
```

Python 3.8 이상이면 동작한다. 외부 의존성 없이 표준 라이브러리만 사용하므로 `pip install` 이 필요 없다.
키는 서버 프로세스의 환경변수(`GEMINI_API_KEY`, 없으면 `GOOGLE_API_KEY`)에서만 읽는다.
브라우저로는 절대 내려가지 않고, 모든 Gemini API 호출은 `server.py`가 프록시한다.

### 환경변수

| 변수 | 설명 | 기본값 |
| --- | --- | --- |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Gemini API 키 (필수) | - |
| `PORT` | 서버 포트 | `3000` |
| `GEMINI_MODELS` | 드롭다운에 노출할 모델 ID (쉼표 구분, 첫 번째가 기본값) | `gemini-3.8-flash,gemini-3.7-flash` |

```bash
export GEMINI_MODELS="gemini-2.5-flash,gemini-2.0-flash"
```

`/api/models`가 API 키로 `ListModels`를 호출해서 지정한 ID가 실제 사용 가능한지 확인하고,
없으면 키로 접근 가능한 flash 계열 모델을 최신순으로 대신 노출한다.

## 구성

| 파일 | 역할 |
| --- | --- |
| [server.py](server.py) | 정적 파일 서빙 + `/api/models` + `/api/chat` (SSE 스트리밍 프록시, Search grounding, idle timeout) |
| [public/index.html](public/index.html) | 화면 구조 (사이드바, 모델 선택, 입력창) |
| [public/styles.css](public/styles.css) | gemini.google.com/app 스타일 다크 테마 |
| [public/app.js](public/app.js) | 모델 선택, 히스토리 관리, 스트리밍 렌더링 |
| [compute_engine_example.ipynb](compute_engine_example.ipynb) | Compute Engine 인스턴스 생성/정리 예시 노트북 |

## 조작

- `Enter` 전송, `Shift+Enter` 줄바꿈
- 사이드바 "새 채팅": 새 대화 시작 (기존 대화는 목록에 남음)
- 히스토리 항목 클릭: 해당 대화를 불러와 이어서 채팅, 마우스 오버 시 삭제(x) 가능
- 답변에 검색 출처가 있으면 답변 아래에 "출처" 링크 목록 표시

## Compute Engine 배포

로컬과 동일한 `server.py` + `public/`을 그대로 인스턴스에 올려 systemd 서비스로 상시 운영하는 구성이다.

**아키텍처**

```
인터넷 ── HTTPS(443) ── Caddy (자동 TLS, Let's Encrypt) ── 127.0.0.1:3000 ── server.py (systemd)
                                                                                  │
                                                                    부팅/재시작 시 Secret Manager에서
                                                                    GEMINI_API_KEY 조회 (디스크 저장 안 함)
```

- **API 키**: GCE 인스턴스 디스크에 저장하지 않는다. 서비스 시작 스크립트가 인스턴스의 메타데이터 서버로 OAuth 토큰을 받아 Secret Manager REST API를 호출해 메모리로만 로드한다.
- **필요 IAM**: 인스턴스 서비스 계정에 대상 시크릿의 `roles/secretmanager.secretAccessor` 권한, 인스턴스 스코프에 `cloud-platform` 포함.
- **HTTPS**: 소유한 도메인이 없다면 `<외부IP>.sslip.io` 같은 무료 wildcard DNS를 호스트네임으로 써서 Caddy가 Let's Encrypt 인증서를 자동 발급/갱신하게 할 수 있다. 도메인이 있으면 그 도메인을 인스턴스 IP로 A 레코드 연결 후 `Caddyfile`의 호스트네임만 바꾸면 된다.
- **상시 운영**: `chatbot.service` (systemd, `enabled` + `Restart=on-failure`)로 등록해 재부팅/장애 시 자동 재시작.
- **방화벽**: 인바운드 `tcp:80,443`만 공개하고, 앱이 직접 듣는 `tcp:3000`은 외부에 열지 않는다(로컬호스트에서만 Caddy가 접근).

전체 배포 스크립트(`startup-script.sh`)와 실행 로그는 로컬 `deploy/` 디렉터리에 있으며(비공개, git에는 포함하지 않음),
값(프로젝트 ID·인스턴스명·시크릿 경로 등)만 채워 넣으면 새 프로젝트에도 그대로 재사용할 수 있다.

`compute_engine_example.ipynb`는 인스턴스 생성/삭제(과금 방지용 정리 포함)를 다루는 별도 실습 노트북이다.
