# gcp-cloud-run-chatbot

`compute_engine/`와 동일한 Gemini 챗봇을 Cloud Run(서버리스 컨테이너)에서 운영하는 구성.

## 로컬 실행

```bash
export GEMINI_API_KEY="발급받은_키"
python3 server.py    # http://localhost:8080
```

Python 3.8 이상, 표준 라이브러리만 사용 (`pip install` 불필요).

### 환경변수

| 변수 | 설명 | 기본값 |
| --- | --- | --- |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Gemini API 키 (필수) | - |
| `PORT` | 서버 포트 (Cloud Run이 자동 주입) | `8080` |
| `GEMINI_MODELS` | 드롭다운에 노출할 모델 ID (쉼표 구분) | `gemini-3.8-flash,gemini-3.7-flash` |

## 구성

| 파일 | 역할 |
| --- | --- |
| [server.py](server.py) | `compute_engine/server.py`와 동일한 로직 (기본 `PORT`만 `8080`) |
| [public/](public/) | 정적 프런트엔드 (동일) |
| [Dockerfile](Dockerfile) | Cloud Run 배포용 컨테이너 이미지 정의 |

## Cloud Run 배포

**아키텍처**

```
인터넷 ── HTTPS(자동 발급/관리) ── Cloud Run 서비스 (컨테이너, 0~N 인스턴스 자동 스케일)
                                          │
                                    시크릿 참조로 GEMINI_API_KEY 주입
                                    (Secret Manager, 이미지/디스크에 저장 안 함)
```

Compute Engine 구성과 달리 Caddy, systemd, 방화벽 설정이 필요 없다 — Cloud Run이 HTTPS와 프로세스 상시성을 관리한다.

```bash
# 1) 시크릿 등록 (최초 1회)
echo -n "발급받은_키" | gcloud secrets create GEMINI_API_KEY --data-file=-

# 2) 소스에서 바로 빌드 + 배포
gcloud run deploy chatbot \
  --source . \
  --region asia-northeast3 \
  --set-secrets GEMINI_API_KEY=GEMINI_API_KEY:latest \
  --allow-unauthenticated
```

- **API 키**: 컨테이너 이미지나 환경변수 파일에 넣지 않고, `--set-secrets`로 Secret Manager 값을 런타임에 환경변수로 주입한다.
- **필요 IAM**: Cloud Run 서비스 계정(기본값 또는 지정한 서비스 계정)에 대상 시크릿의 `roles/secretmanager.secretAccessor` 권한.
- **HTTPS**: Cloud Run이 `*.run.app` 도메인에 인증서를 자동 발급/갱신한다. 커스텀 도메인을 쓰려면 `gcloud run domain-mappings create`로 매핑만 추가하면 된다.
- **인증**: 누구나 접근 가능하게 하려면 `--allow-unauthenticated`, 특정 사용자/서비스만 허용하려면 생략 후 `roles/run.invoker`를 부여한다.
- **재배포**: 코드/의존성 변경 후 위 `gcloud run deploy` 명령을 다시 실행하면 새 리비전이 배포되고 트래픽이 전환된다.
