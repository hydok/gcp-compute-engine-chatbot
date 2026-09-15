#!/usr/bin/env python3
import json
import os
import re
import socket
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

PORT = int(os.environ.get('PORT', '3000'))
API_KEY = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY') or ''
API_BASE = 'https://generativelanguage.googleapis.com/v1beta'
IDLE_TIMEOUT = 30  # 검색 grounding 은 응답이 늦게 오거나 멈출 수 있어, 일정 시간 데이터가 없으면 중단한다.

# 화면에 노출할 모델 후보. 환경변수 GEMINI_MODELS 로 덮어쓸 수 있다.
WANTED_MODELS = [m.strip() for m in os.environ.get('GEMINI_MODELS', 'gemini-3.8-flash,gemini-3.7-flash').split(',') if m.strip()]

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
    s = re.sub(r'^models/', '', model_id)
    s = s.replace('-', ' ')
    s = re.sub(r'\bgemini\b', 'Gemini', s, count=1, flags=re.IGNORECASE)
    s = re.sub(r'\bflash\b', 'Flash', s, count=1, flags=re.IGNORECASE)
    s = re.sub(r'\bpro\b', 'Pro', s, count=1, flags=re.IGNORECASE)
    s = re.sub(r'\blite\b', 'Lite', s, count=1, flags=re.IGNORECASE)
    s = re.sub(r'\blatest\b', '(latest)', s, count=1, flags=re.IGNORECASE)
    return s


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


# API 키로 실제 사용 가능한 flash 모델을 조회해서, 원하는 후보와 교차 확인한다.
def list_models():
    fallback = [{'id': m, 'label': pretty_label(m), 'verified': False} for m in WANTED_MODELS]
    if not API_KEY:
        return fallback

    try:
        url = f"{API_BASE}/models?pageSize=200&key={urllib.parse.quote(API_KEY)}"
        with urllib.request.urlopen(url, timeout=10) as r:
            if r.status != 200:
                return fallback
            data = json.loads(r.read().decode('utf-8'))

        live = [
            re.sub(r'^models/', '', str(m.get('name', '')))
            for m in data.get('models', [])
            if 'generateContent' in (m.get('supportedGenerationMethods') or [])
        ]

        picked = [{'id': m, 'label': pretty_label(m), 'verified': True} for m in WANTED_MODELS if m in live]
        if picked:
            return picked

        # 원하는 이름이 없으면 사용 가능한 flash 모델을 최신순으로 제공한다.
        flash_ids = sorted(
            (m for m in live if 'flash' in m and 'image' not in m and 'tts' not in m and 'live' not in m),
            key=natural_key,
            reverse=True,
        )[:6]
        flash = [{'id': m, 'label': pretty_label(m), 'verified': True} for m in flash_ids]
        return flash or fallback
    except Exception:
        return fallback


def stream_chat(handler, body):
    model = str(body.get('model') or (WANTED_MODELS[0] if WANTED_MODELS else ''))
    if not re.fullmatch(r'[\w.\-]+', model):
        return send_json(handler, 400, {'error': '잘못된 모델 이름입니다.'})
    if not API_KEY:
        return send_json(handler, 500, {'error': 'GEMINI_API_KEY 환경변수가 설정되지 않았습니다.'})

    messages = body.get('messages')
    contents = [
        {'role': 'model' if m.get('role') == 'model' else 'user', 'parts': [{'text': m['text']}]}
        for m in (messages if isinstance(messages, list) else [])
        if isinstance(m, dict) and isinstance(m.get('text'), str) and m['text'].strip()
    ]
    if not contents:
        return send_json(handler, 400, {'error': '메시지가 비어 있습니다.'})

    url = f"{API_BASE}/models/{urllib.parse.quote(model, safe='')}:streamGenerateContent?alt=sse&key={urllib.parse.quote(API_KEY)}"
    payload = json.dumps({'contents': contents, 'tools': [{'google_search': {}}]}).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'}, method='POST')

    try:
        upstream = urllib.request.urlopen(req, timeout=IDLE_TIMEOUT)
    except socket.timeout:
        return send_json(handler, 504, {'error': f'Gemini 응답이 {IDLE_TIMEOUT}초 동안 없어 중단했습니다.'})
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'ignore')
        message = f'Gemini API 오류 ({e.code})'
        try:
            parsed = json.loads(detail)
            if parsed.get('error', {}).get('message'):
                message += f": {parsed['error']['message']}"
        except Exception:
            if detail:
                message += f": {detail[:300]}"
        return send_json(handler, 502, {'error': message})
    except urllib.error.URLError as e:
        return send_json(handler, 504, {'error': f'Gemini 요청 실패: {e.reason}'})

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
                return send_json(self, 200, {'models': list_models(), 'hasApiKey': bool(API_KEY)})
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
    print(f"\n  Gemini 챗봇  ->  http://localhost:{PORT}")
    print(f"  API 키: {'환경변수에서 로드됨' if API_KEY else '없음 (GEMINI_API_KEY 를 설정하세요)'}")
    print(f"  모델 후보: {', '.join(WANTED_MODELS)}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
