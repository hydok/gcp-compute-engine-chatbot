#!/usr/bin/env python3
import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def load_dotenv_if_exists(path='.env'):
    if not os.path.isfile(path):
        return
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv_if_exists()

PORT = int(os.environ.get('PORT', '8080'))
LOCATION = os.environ.get('LOCATION', 'us-central1')
IDLE_TIMEOUT = 30  # 검색 grounding 은 응답이 늦게 오거나 멈출 수 있어, 일정 시간 데이터가 없으면 중단한다.

# 화면에 노출할 모델 후보. 환경변수 GEMINI_MODELS 로 덮어쓸 수 있다.
# 주의: Vertex AI의 모델 ID는 Generative Language API(cloud_run/)와 이름이 다르다.
# 예) gemini-3.8-flash(AI Studio) 대응 없음 -> gemini-2.5-flash(Vertex AI)로 확인됨.
WANTED_MODELS = [m.strip() for m in os.environ.get('GEMINI_MODELS', 'gemini-2.5-flash,gemini-2.5-pro').split(',') if m.strip()]

METADATA_HEADERS = {'Metadata-Flavor': 'Google'}
METADATA_BASE = 'http://metadata.google.internal/computeMetadata/v1'

# API 키/Secret Manager 없이 ADC(Application Default Credentials)로 인증한다.
# Cloud Run 등 GCP 환경: 인스턴스 메타데이터 서버에서 서비스 계정 토큰을 바로 받는다.
# 로컬 개발 환경: 메타데이터 서버가 없으므로 `gcloud auth application-default login`으로
# 로그인해둔 사용자 인증 정보를 gcloud CLI를 통해 조회한다.
_token_cache = {'value': None, 'exp': 0}
_project_id_cache = {'value': None}


def get_access_token():
    now = time.time()
    if _token_cache['value'] and now < _token_cache['exp'] - 60:
        return _token_cache['value']

    try:
        req = urllib.request.Request(f'{METADATA_BASE}/instance/service-accounts/default/token', headers=METADATA_HEADERS)
        with urllib.request.urlopen(req, timeout=2) as r:
            data = json.loads(r.read().decode('utf-8'))
        token, ttl = data['access_token'], data['expires_in']
    except Exception:
        result = subprocess.run(
            ['gcloud', 'auth', 'application-default', 'print-access-token'],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f'ADC 토큰 발급 실패: {result.stderr.strip()}')
        token, ttl = result.stdout.strip(), 3600

    _token_cache['value'] = token
    _token_cache['exp'] = now + ttl
    return token


def get_project_id():
    if _project_id_cache['value']:
        return _project_id_cache['value']

    project_id = os.environ.get('PROJECT_ID')
    if not project_id:
        try:
            req = urllib.request.Request(f'{METADATA_BASE}/project/project-id', headers=METADATA_HEADERS)
            with urllib.request.urlopen(req, timeout=2) as r:
                project_id = r.read().decode('utf-8')
        except Exception:
            result = subprocess.run(['gcloud', 'config', 'get-value', 'project'], capture_output=True, text=True, timeout=10)
            project_id = result.stdout.strip() if result.returncode == 0 else ''

    _project_id_cache['value'] = project_id
    return project_id


def vertex_models_url(project_id, suffix=''):
    return f'https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{project_id}/locations/{LOCATION}/publishers/google/models{suffix}'


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.realpath(os.path.join(BASE_DIR, 'public'))
MIME = {
    '.html': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
}


def send_json(handler, code, obj):
    body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    handler.send_response(code)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_body(handler):
    length = int(handler.headers.get('Content-Length') or 0)
    if length > 1_000_000:
        raise ValueError('payload too large')
    raw = handler.rfile.read(length) if length else b''
    return json.loads(raw.decode('utf-8')) if raw else {}


def pretty_label(model_id):
    s = re.sub(r'^publishers/google/models/', '', model_id)
    s = s.replace('-', ' ')
    s = re.sub(r'\bgemini\b', 'Gemini', s, count=1, flags=re.IGNORECASE)
    s = re.sub(r'\bflash\b', 'Flash', s, count=1, flags=re.IGNORECASE)
    s = re.sub(r'\bpro\b', 'Pro', s, count=1, flags=re.IGNORECASE)
    s = re.sub(r'\blite\b', 'Lite', s, count=1, flags=re.IGNORECASE)
    s = re.sub(r'\blatest\b', '(latest)', s, count=1, flags=re.IGNORECASE)
    return s


# Vertex AI는 API 키 기반 ListModels 같은 손쉬운 후보 검증 엔드포인트가 없어
# (Generative Language API의 cloud_run/server.py 방식과 달리) 여기서는 후보를 검증 없이 그대로 노출한다.
# 실제로 존재하지 않는 모델 ID를 넣으면 /api/chat 호출 시점에 에러로 드러난다.
def list_models():
    return [{'id': m, 'label': pretty_label(m), 'verified': False} for m in WANTED_MODELS]


def stream_chat(handler, body):
    model = str(body.get('model') or (WANTED_MODELS[0] if WANTED_MODELS else ''))
    if not re.fullmatch(r'[\w.\-]+', model):
        return send_json(handler, 400, {'error': '잘못된 모델 이름입니다.'})

    try:
        token = get_access_token()
        project_id = get_project_id()
    except Exception as e:
        return send_json(handler, 500, {'error': f'ADC 인증 실패: {e}'})
    if not project_id:
        return send_json(handler, 500, {'error': 'GCP 프로젝트 ID를 확인할 수 없습니다 (PROJECT_ID 환경변수를 설정하세요).'})

    messages = body.get('messages')
    contents = [
        {'role': 'model' if m.get('role') == 'model' else 'user', 'parts': [{'text': m['text']}]}
        for m in (messages if isinstance(messages, list) else [])
        if isinstance(m, dict) and isinstance(m.get('text'), str) and m['text'].strip()
    ]
    if not contents:
        return send_json(handler, 400, {'error': '메시지가 비어 있습니다.'})

    url = vertex_models_url(project_id, f'/{urllib.parse.quote(model, safe="")}:streamGenerateContent') + '?alt=sse'
    payload = json.dumps({'contents': contents, 'tools': [{'google_search': {}}]}).encode('utf-8')
    req = urllib.request.Request(
        url, data=payload,
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'},
        method='POST',
    )

    try:
        upstream = urllib.request.urlopen(req, timeout=IDLE_TIMEOUT)
    except socket.timeout:
        return send_json(handler, 504, {'error': f'Gemini 응답이 {IDLE_TIMEOUT}초 동안 없어 중단했습니다.'})
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'ignore')
        message = f'Vertex AI 오류 ({e.code})'
        try:
            parsed = json.loads(detail)
            if parsed.get('error', {}).get('message'):
                message += f": {parsed['error']['message']}"
        except Exception:
            if detail:
                message += f": {detail[:300]}"
        return send_json(handler, 502, {'error': message})
    except urllib.error.URLError as e:
        return send_json(handler, 504, {'error': f'Vertex AI 요청 실패: {e.reason}'})

    handler.send_response(200)
    handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
    handler.send_header('Cache-Control', 'no-cache')
    handler.end_headers()

    def send(obj):
        handler.wfile.write(f'data: {json.dumps(obj, ensure_ascii=False)}\n\n'.encode('utf-8'))
        handler.wfile.flush()

    got_text = False
    sources = {}
    try:
        for raw_line in upstream:
            line = raw_line.decode('utf-8', 'ignore').rstrip('\r\n')
            if not line.startswith('data:'):
                continue
            payload_str = line[5:].strip()
            if not payload_str or payload_str == '[DONE]':
                continue
            try:
                parsed = json.loads(payload_str)
            except Exception:
                continue

            block_reason = (parsed.get('promptFeedback') or {}).get('blockReason')
            if block_reason:
                send({'error': f'Gemini가 응답을 차단했습니다 ({block_reason}).'})
                continue

            candidates = parsed.get('candidates') or []
            candidate = candidates[0] if candidates else {}
            parts = (candidate.get('content') or {}).get('parts') or []
            text = ''.join(p.get('text', '') for p in parts)
            if text:
                got_text = True
                send({'text': text})

            for c in (candidate.get('groundingMetadata') or {}).get('groundingChunks') or []:
                web = c.get('web') or {}
                if web.get('uri'):
                    sources[web['uri']] = web.get('title') or web['uri']

            finish_reason = candidate.get('finishReason')
            if finish_reason and finish_reason != 'STOP' and not got_text:
                send({'error': f'응답이 완료되지 못했습니다 ({finish_reason}).'})

        source_list = [{'uri': uri, 'title': title} for uri, title in sources.items()]
        send({'done': True, 'sources': source_list})
    except socket.timeout:
        send({'error': f'Gemini 응답이 {IDLE_TIMEOUT}초 동안 없어 중단했습니다.'})
    except Exception as e:
        send({'error': f'스트리밍 중단: {e}'})
    finally:
        upstream.close()


def serve_static(handler, url_path):
    rel = 'index.html' if url_path == '/' else urllib.parse.unquote(url_path).lstrip('/')
    file_path = os.path.realpath(os.path.join(PUBLIC_DIR, rel))
    if file_path != PUBLIC_DIR and not file_path.startswith(PUBLIC_DIR + os.sep):
        return send_json(handler, 403, {'error': 'forbidden'})
    if not os.path.isfile(file_path):
        return send_json(handler, 404, {'error': 'not found'})

    with open(file_path, 'rb') as f:
        data = f.read()
    handler.send_response(200)
    handler.send_header('Content-Type', MIME.get(os.path.splitext(file_path)[1], 'application/octet-stream'))
    handler.send_header('Content-Length', str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def do_GET(self):
        try:
            path = urllib.parse.urlparse(self.path).path
            if path.startswith('/api/models'):
                return send_json(self, 200, {'models': list_models(), 'hasCredentials': bool(get_project_id())})
            return serve_static(self, path)
        except Exception as e:
            send_json(self, 500, {'error': str(e)})

    def do_POST(self):
        try:
            path = urllib.parse.urlparse(self.path).path
            if path.startswith('/api/chat'):
                return stream_chat(self, read_body(self))
            return send_json(self, 405, {'error': 'method not allowed'})
        except Exception as e:
            send_json(self, 500, {'error': str(e)})


if __name__ == '__main__':
    httpd = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    print(f"\n  Gemini 챗봇 (ADC 인증, Vertex AI)  ->  http://localhost:{PORT}")
    print(f"  프로젝트: {get_project_id() or '확인 불가 (PROJECT_ID 환경변수 확인)'}")
    print(f"  Vertex AI 리전: {LOCATION}")
    print(f"  모델 후보: {', '.join(WANTED_MODELS)}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
