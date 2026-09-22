# gcp-compute-engine-chatbot

Gemini 기반 웹 챗봇을 GCP의 서로 다른 두 가지 방식(Compute Engine / Cloud Run)으로 배포해보는 프로젝트.
프런트엔드와 서버 로직은 동일하고, 배포·운영 방식만 다르다.

## 기능 (공통)

- Gemini 스트리밍 응답 (SSE)
- Google Search grounding — 모델이 필요하다고 판단하면 자동으로 웹 검색 후 답변에 반영, 출처 링크 표시
- 좌측 대화 히스토리 패널 — 브라우저 `localStorage`에 저장
- 모델 드롭다운 — API 키로 실제 사용 가능한 모델을 조회해 노출
- 서버는 외부 의존성 없이 Python 표준 라이브러리만 사용 (`pip install` 불필요)

## 구성

| 디렉터리 | 배포 방식 | 문서 |
| --- | --- | --- |
| [compute_engine/](compute_engine/) | GCE VM에 systemd 서비스로 상시 운영, Caddy로 HTTPS(Let's Encrypt) 처리 | [compute_engine/README.md](compute_engine/README.md) |
| [cloud_run/](cloud_run/) | 컨테이너 이미지를 Artifact Registry에 올리고 Cloud Run(서버리스)에 배포, HTTPS 자동 관리 | [cloud_run/README.md](cloud_run/README.md) |

두 디렉터리 모두 `server.py`(정적 파일 서빙 + `/api/models` + `/api/chat` 프록시)와 `public/`(프런트엔드)으로 구성되며, 세부 배포 절차는 각 디렉터리 README 참고.

## 방식 비교

| 항목 | Compute Engine | Cloud Run |
| --- | --- | --- |
| 과금 | 인스턴스 상시 가동 (켜져 있는 동안 계속 과금) | 요청 처리한 시간만 과금, 트래픽 없으면 0 |
| HTTPS | Caddy + Let's Encrypt 직접 구성 | Google이 `*.run.app` 인증서 자동 발급/갱신 |
| API 키 관리 | 부팅 스크립트가 메타데이터 서버 OAuth 토큰으로 Secret Manager 조회 | `--set-secrets`로 Secret Manager 값을 런타임 환경변수 주입 |
| 배포 단위 | VM 디스크에 코드 직접 배치 | 컨테이너 이미지 (Artifact Registry) |
| 스케일링 | 수동 (인스턴스 사양 변경) | 자동 (0 ~ N 인스턴스) |

## 공통 환경변수

| 변수 | 설명 | 기본값 |
| --- | --- | --- |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Gemini API 키 (필수) | - |
| `PORT` | 서버 포트 | compute_engine: `3000` / cloud_run: `8080` |
| `GEMINI_MODELS` | 드롭다운에 노출할 모델 ID (쉼표 구분) | `gemini-3.8-flash,gemini-3.7-flash` |

로컬 실행이나 배포 상세 절차는 각 디렉터리의 README를 참고.
