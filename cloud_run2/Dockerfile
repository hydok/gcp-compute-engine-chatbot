# 표준 라이브러리만 쓰는 앱이라 pip install 레이어가 없음 → slim 이미지로 충분
FROM python:3.12-slim

# 이후 명령이 실행될 컨테이너 내부 작업 디렉토리
WORKDIR /app

# 서버 코드와 정적 파일만 복사 (server.py가 상대경로로 public/을 찾음)
COPY server.py .
COPY public/ public/

# stdout 버퍼링 끔 - 안 하면 print() 로그가 지연/유실되어 Cloud Run 로그에 안 찍힐 수 있음
ENV PYTHONUNBUFFERED=1
# Cloud Run이 배포 시 자동 주입하는 값과 동일하게 로컬 docker run 기본값도 맞춤
ENV PORT=8080
# 문서화 목적 선언 (실제 포트 개방은 docker run -p 또는 Cloud Run이 처리)
EXPOSE 8080

# root 대신 전용 사용자로 실행 (보안 모범사례)
RUN useradd -m appuser
USER appuser

CMD ["python3", "server.py"]
