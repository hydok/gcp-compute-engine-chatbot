'use strict';

const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

const PORT = Number(process.env.PORT || 3000);
const API_KEY = process.env.GEMINI_API_KEY || process.env.GOOGLE_API_KEY || '';
const API_BASE = 'https://generativelanguage.googleapis.com/v1beta';

// 화면에 노출할 모델 후보. 환경변수 GEMINI_MODELS 로 덮어쓸 수 있다.
const WANTED_MODELS = (process.env.GEMINI_MODELS || 'gemini-3.8-flash,gemini-3.7-flash')
  .split(',')
  .map((m) => m.trim())
  .filter(Boolean);

const PUBLIC_DIR = path.join(__dirname, 'public');
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
};

const json = (res, code, body) => {
  res.writeHead(code, { 'content-type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(body));
};

const readBody = (req) =>
  new Promise((resolve, reject) => {
    let raw = '';
    req.on('data', (c) => {
      raw += c;
      if (raw.length > 1e6) reject(new Error('payload too large'));
    });
    req.on('end', () => {
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch (e) {
        reject(e);
      }
    });
    req.on('error', reject);
  });

const prettyLabel = (id) =>
  id
    .replace(/^models\//, '')
    .replace(/-/g, ' ')
    .replace(/\bgemini\b/i, 'Gemini')
    .replace(/\bflash\b/i, 'Flash')
    .replace(/\bpro\b/i, 'Pro')
    .replace(/\blite\b/i, 'Lite')
    .replace(/\blatest\b/i, '(latest)');

// API 키로 실제 사용 가능한 flash 모델을 조회해서, 원하는 후보와 교차 확인한다.
async function listModels() {
  const fallback = WANTED_MODELS.map((id) => ({ id, label: prettyLabel(id), verified: false }));
  if (!API_KEY) return fallback;

  try {
    const r = await fetch(`${API_BASE}/models?pageSize=200&key=${API_KEY}`);
    if (!r.ok) return fallback;
    const data = await r.json();
    const live = (data.models || [])
      .filter((m) => (m.supportedGenerationMethods || []).includes('generateContent'))
      .map((m) => String(m.name).replace(/^models\//, ''));

    const picked = WANTED_MODELS.filter((id) => live.includes(id)).map((id) => ({
      id,
      label: prettyLabel(id),
      verified: true,
    }));
    if (picked.length) return picked;

    // 원하는 이름이 없으면 사용 가능한 flash 모델을 최신순으로 제공한다.
    const flash = live
      .filter((id) => id.includes('flash') && !id.includes('image') && !id.includes('tts') && !id.includes('live'))
      .sort((a, b) => b.localeCompare(a, 'en', { numeric: true }))
      .slice(0, 6)
      .map((id) => ({ id, label: prettyLabel(id), verified: true }));
    return flash.length ? flash : fallback;
  } catch {
    return fallback;
  }
}

async function streamChat(req, res, body) {
  const model = String(body.model || WANTED_MODELS[0]);
  if (!/^[\w.\-]+$/.test(model)) return json(res, 400, { error: '잘못된 모델 이름입니다.' });
  if (!API_KEY) return json(res, 500, { error: 'GEMINI_API_KEY 환경변수가 설정되지 않았습니다.' });

  const contents = (Array.isArray(body.messages) ? body.messages : [])
    .filter((m) => m && typeof m.text === 'string' && m.text.trim())
    .map((m) => ({ role: m.role === 'model' ? 'model' : 'user', parts: [{ text: m.text }] }));
  if (!contents.length) return json(res, 400, { error: '메시지가 비어 있습니다.' });

  // 검색 grounding 은 응답이 늦게 오거나 멈출 수 있어, 일정 시간 데이터가 없으면 중단한다.
  const IDLE_TIMEOUT_MS = 30_000;
  const controller = new AbortController();
  let idleTimer;
  const armIdleTimer = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(
      () => controller.abort(new Error(`Gemini 응답이 ${IDLE_TIMEOUT_MS / 1000}초 동안 없어 중단했습니다.`)),
      IDLE_TIMEOUT_MS
    );
  };
  armIdleTimer();

  let upstream;
  try {
    upstream = await fetch(
      `${API_BASE}/models/${encodeURIComponent(model)}:streamGenerateContent?alt=sse&key=${API_KEY}`,
      {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ contents, tools: [{ google_search: {} }] }),
        signal: controller.signal,
      }
    );
  } catch (e) {
    clearTimeout(idleTimer);
    return json(res, 504, { error: e.name === 'AbortError' ? e.message : `Gemini 요청 실패: ${e.message}` });
  }

  if (!upstream.ok || !upstream.body) {
    clearTimeout(idleTimer);
    const detail = await upstream.text().catch(() => '');
    let message = `Gemini API 오류 (${upstream.status})`;
    try {
      const parsed = JSON.parse(detail);
      if (parsed?.error?.message) message += `: ${parsed.error.message}`;
    } catch {
      if (detail) message += `: ${detail.slice(0, 300)}`;
    }
    return json(res, 502, { error: message });
  }

  res.writeHead(200, {
    'content-type': 'text/event-stream; charset=utf-8',
    'cache-control': 'no-cache',
    connection: 'keep-alive',
  });

  const send = (obj) => res.write(`data: ${JSON.stringify(obj)}\n\n`);
  const decoder = new TextDecoder();
  let buffer = '';
  let gotText = false;
  const sources = new Map();

  try {
    for await (const chunk of upstream.body) {
      armIdleTimer();
      buffer += decoder.decode(chunk, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        const payload = line.slice(5).trim();
        if (!payload || payload === '[DONE]') continue;
        try {
          const parsed = JSON.parse(payload);
          const blockReason = parsed?.promptFeedback?.blockReason;
          if (blockReason) {
            send({ error: `Gemini가 응답을 차단했습니다 (${blockReason}).` });
            continue;
          }

          const candidate = parsed?.candidates?.[0];
          const parts = candidate?.content?.parts || [];
          const text = parts.map((p) => p.text || '').join('');
          if (text) {
            gotText = true;
            send({ text });
          }

          const chunks = candidate?.groundingMetadata?.groundingChunks || [];
          for (const c of chunks) {
            if (c.web?.uri) sources.set(c.web.uri, c.web.title || c.web.uri);
          }

          if (candidate?.finishReason && candidate.finishReason !== 'STOP' && !gotText) {
            send({ error: `응답이 완료되지 못했습니다 (${candidate.finishReason}).` });
          }
        } catch {
          /* 부분 청크는 무시 */
        }
      }
    }
    clearTimeout(idleTimer);
    const sourceList = [...sources].map(([uri, title]) => ({ uri, title }));
    send({ done: true, sources: sourceList });
  } catch (e) {
    clearTimeout(idleTimer);
    send({ error: e.name === 'AbortError' ? e.message : `스트리밍 중단: ${e.message}` });
  }
  res.end();
}

function serveStatic(req, res) {
  const urlPath = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
  const rel = urlPath === '/' ? 'index.html' : urlPath.replace(/^\/+/, '');
  const file = path.join(PUBLIC_DIR, rel);
  if (!file.startsWith(PUBLIC_DIR)) return json(res, 403, { error: 'forbidden' });

  fs.readFile(file, (err, data) => {
    if (err) return json(res, 404, { error: 'not found' });
    res.writeHead(200, { 'content-type': MIME[path.extname(file)] || 'application/octet-stream' });
    res.end(data);
  });
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.method === 'GET' && req.url.startsWith('/api/models')) {
      return json(res, 200, { models: await listModels(), hasApiKey: Boolean(API_KEY) });
    }
    if (req.method === 'POST' && req.url.startsWith('/api/chat')) {
      return await streamChat(req, res, await readBody(req));
    }
    if (req.method === 'GET') return serveStatic(req, res);
    return json(res, 405, { error: 'method not allowed' });
  } catch (e) {
    if (!res.headersSent) json(res, 500, { error: e.message });
    else res.end();
  }
});

server.listen(PORT, () => {
  console.log(`\n  Gemini 챗봇  ->  http://localhost:${PORT}`);
  console.log(`  API 키: ${API_KEY ? '환경변수에서 로드됨' : '없음 (GEMINI_API_KEY 를 설정하세요)'}`);
  console.log(`  모델 후보: ${WANTED_MODELS.join(', ')}\n`);
});
