# gcp-cloud-run-chatbot-adc

[cloud_run/](../cloud_run/)과 같은 챗봇이지만 인증 방식이 다르다: `GEMINI_API_KEY` / Secret Manager 대신
**ADC(Application Default Credentials)**로 인증하고, Gemini Developer API 대신 **Vertex AI의 Gemini 모델**을 호출한다.

## cloud_run과의 차이

| 항목 | cloud_run | cloud_run2 (이 폴더) |
| --- | --- | --- |
| 인증 | `GEMINI_API_KEY` (Secret Manager로 주입) | ADC — 서비스 계정 신원으로 자동 인증, 키 발급/보관 불필요 |
| 호출 API | Generative Language API (`generativelanguage.googleapis.com`) | Vertex AI (`{location}-aiplatform.googleapis.com`) |
| 필요 설정 | Secret Manager 등록 + IAM `secretAccessor` | Cloud Run 서비스 계정에 IAM `roles/aiplatform.user` |
| 키 유출 위험 | 있음 (탈취되면 누구나 사용 가능) | 없음 (토큰은 짧은 수명 + 서비스 계정에 귀속) |

## ADC 동작 방식

`server.py`가 API 키를 아예 갖고 있지 않다. 대신 요청마다:

1. **Cloud Run/GCE 등 GCP 환경**: 인스턴스 메타데이터 서버(`http://metadata.google.internal/...`)에 붙어있는 서비스 계정 토큰을 즉시 조회 (관리 작업 없음, 자동으로 항상 됨)
2. **로컬 개발 환경**: 메타데이터 서버가 없으므로 `gcloud auth application-default print-access-token`을 대신 호출해서 로그인된 사용자 인증 정보로 토큰 발급

두 경로 모두 짧은 수명(약 1시간)의 OAuth 액세스 토큰을 받아 `Authorization: Bearer` 헤더로 Vertex AI를 호출한다. 코드에 저장되는 비밀값은 전혀 없다.

## 로컬 실행

```bash
# 1) 최초 1회 - 로컬 ADC 로그인
gcloud auth application-default login

# 2) 사용할 프로젝트 지정 (gcloud 기본 프로젝트로 자동 감지되지만, 다르면 명시)
gcloud config set project <프로젝트ID>

# 3) 서버 실행
python3 server.py    # http://localhost:8080
```

### 환경변수

| 변수 | 설명 | 기본값 |
| --- | --- | --- |
| `PROJECT_ID` | GCP 프로젝트 ID (보통 자동 감지, 실패 시에만 지정) | 메타데이터 서버 → `gcloud config get-value project` 순으로 자동 감지 |
| `LOCATION` | Vertex AI 리전 | `us-central1` |
| `PORT` | 서버 포트 | `8080` |
| `GEMINI_MODELS` | 드롭다운에 노출할 모델 ID (쉼표 구분) | `gemini-3.8-flash,gemini-3.7-flash` |

## Cloud Run 배포

```bash
# 1) Vertex AI API 활성화 (최초 1회)
gcloud services enable aiplatform.googleapis.com --project=<프로젝트ID>

# 2) 빌드 + 배포 (API 키/Secret 설정 필요 없음 — --set-secrets 플래그 자체가 없다)
gcloud run deploy chatbot-adc \
  --source . \
  --region asia-northeast3 \
  --allow-unauthenticated
```

Cloud Run이 사용하는 서비스 계정(기본값은 `<프로젝트번호>-compute@developer.gserviceaccount.com`)에
Vertex AI 호출 권한이 없으면 `/api/chat` 호출 시 403이 반환된다. 필요하면 부여:

```bash
gcloud projects add-iam-policy-binding <프로젝트ID> \
  --member="serviceAccount:<프로젝트번호>-compute@developer.gserviceaccount.com" \
  --role="roles/aiplatform.user"
```

(이 프로젝트는 기본 서비스 계정에 이미 `roles/editor`가 있어 별도 부여 없이도 바로 동작했다 — 프로젝트마다 기본 SA 권한이 다를 수 있으니 403이 나면 그때 위 명령 실행.)

## 주의

- **모델 ID가 cloud_run/과 다르다.** Generative Language API(`gemini-3.8-flash` 등)와 Vertex AI의 모델 카탈로그는 별개라, 실제 배포해서 확인한 결과 `gemini-3.8-flash`/`gemini-3.7-flash`는 Vertex AI(`us-central1`)에 없고 `gemini-2.5-flash` / `gemini-2.5-pro` / `gemini-2.5-flash-lite`는 확인됨 (기본값도 이걸로 맞춰둠). `LOCATION`을 바꾸면 사용 가능한 모델도 달라질 수 있다.
- Vertex AI는 API 키 방식의 손쉬운 ListModels 엔드포인트가 없어 `list_models()`는 후보를 검증 없이 그대로 노출한다. 존재하지 않는 모델 ID를 넣으면 `/api/chat` 호출 시점에 에러 메시지로 드러난다.
