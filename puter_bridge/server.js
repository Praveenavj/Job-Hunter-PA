/**
 * Puter Bridge Server v2.0
 * ========================
 * Sits between your Python FastAPI backend and Puter's FREE Claude API.
 * Runs on localhost:3456. Python calls this; this calls Puter; Puter calls Claude.
 *
 * WHY THIS EXISTS:
 *   Puter gives you FREE access to Claude (claude-sonnet-4-5) via their
 *   "user-pays" model. You sign up at puter.com (free), get a session token,
 *   and Puter's generous free credits cover ~50-200 LLM calls/day easily.
 *   This bridge translates Python's HTTP calls into Puter API calls.
 *
 * SETUP (one-time, 5 minutes):
 *   1. Go to https://puter.com → Sign up (free)
 *   2. Open browser DevTools (F12) → Console tab
 *   3. Paste: puter.auth.getToken().then(t => console.log(t))
 *   4. Copy the long token string
 *   5. Add to your .env file:  PUTER_AUTH_TOKEN=your_token_here
 *   6. Run: node puter_bridge/server.js
 *
 * ENDPOINTS:
 *   GET  /health   → {"status":"ok","token_set":true,"model":"..."}
 *   POST /complete → {"system":"...", "user":"...", "max_tokens":2048}
 *                  ← {"text":"Claude's response"}
 *
 * START COMMAND:
 *   node puter_bridge/server.js
 *   (or via start_all.sh which starts everything together)
 */

require('dotenv').config({ path: require('path').join(__dirname, '..', '.env') });

const http  = require('http');
const https = require('https');

const PORT = parseInt(process.env.PORT || process.env.PUTER_BRIDGE_PORT || '3456');
const PUTER_TOKEN = process.env.PUTER_AUTH_TOKEN || '';
const MODEL       = process.env.PUTER_MODEL || 'claude-sonnet-4-5';

// ── Core: call Puter AI API ───────────────────────────────────────────────────

function callPuterAI(system, user, maxTokens) {
  return new Promise((resolve, reject) => {

    const messages = [
      { role: 'user', content: `${system}\n\n${user}` }
    ];

    const bodyData = JSON.stringify({
      model: MODEL,
      max_tokens: maxTokens || 2048,
      messages: messages,
    });

    const options = {
      hostname: 'api.puter.com',
      path:     '/drivers/call',
      method:   'POST',
      headers:  {
        'Content-Type':   'application/json',
        'Authorization':  `Bearer ${PUTER_TOKEN}`,
        'Content-Length': Buffer.byteLength(bodyData),
      },
      timeout: 90000,
    };

    const req = https.request(options, (res) => {
      let raw = '';
      res.on('data', chunk => { raw += chunk; });
      res.on('end', () => {
        try {
          const parsed = JSON.parse(raw);

          // Puter drivers/call response shape
          if (parsed.success && parsed.result) {
            const result = parsed.result;
            // Claude response is in content[0].text
            if (result.content && result.content[0] && result.content[0].text) {
              return resolve(result.content[0].text);
            }
            // Sometimes it's a plain string
            if (typeof result === 'string') {
              return resolve(result);
            }
          }

          // OpenAI-compatible shape (fallback)
          if (parsed.choices && parsed.choices[0]) {
            const msg = parsed.choices[0].message;
            if (msg && msg.content) return resolve(msg.content);
          }

          // Error from Puter
          if (parsed.error) {
            return reject(new Error(`Puter API error: ${JSON.stringify(parsed.error)}`));
          }

          return reject(new Error(`Unexpected Puter response: ${raw.slice(0, 200)}`));

        } catch (e) {
          reject(new Error(`JSON parse failed: ${e.message} | raw: ${raw.slice(0, 200)}`));
        }
      });
    });

    req.on('error',   e => reject(new Error(`Network error: ${e.message}`)));
    req.on('timeout', () => { req.destroy(); reject(new Error('Request timed out (90s)')); });

    req.write(bodyData);
    req.end();
  });
}

// ── Alternative endpoint: try OpenAI-compatible path ─────────────────────────

function callPuterOpenAI(system, user, maxTokens) {
  return new Promise((resolve, reject) => {

    const messages = [
      { role: 'system', content: system },
      { role: 'user',   content: user   },
    ];

    const bodyData = JSON.stringify({
      model: `anthropic/${MODEL}`,
      max_tokens: maxTokens || 2048,
      messages,
    });

    const options = {
      hostname: 'api.puter.com',
      path:     '/puterai/openai/v1/chat/completions',
      method:   'POST',
      headers:  {
        'Content-Type':   'application/json',
        'Authorization':  `Bearer ${PUTER_TOKEN}`,
        'Content-Length': Buffer.byteLength(bodyData),
      },
      timeout: 90000,
    };

    const req = https.request(options, (res) => {
      let raw = '';
      res.on('data', chunk => { raw += chunk; });
      res.on('end', () => {
        try {
          const parsed = JSON.parse(raw);
          if (parsed.choices && parsed.choices[0] && parsed.choices[0].message) {
            return resolve(parsed.choices[0].message.content);
          }
          if (parsed.error) {
            return reject(new Error(`Puter OpenAI error: ${JSON.stringify(parsed.error)}`));
          }
          reject(new Error(`Unexpected response: ${raw.slice(0, 200)}`));
        } catch (e) {
          reject(new Error(`Parse error: ${e.message}`));
        }
      });
    });

    req.on('error',   e => reject(new Error(`Network: ${e.message}`)));
    req.on('timeout', () => { req.destroy(); reject(new Error('Timeout')); });
    req.write(bodyData);
    req.end();
  });
}

// ── Try both endpoints, use whichever works ───────────────────────────────────

async function callWithFallback(system, user, maxTokens) {
  // Try drivers/call first (newer Puter API)
  try {
    return await callPuterAI(system, user, maxTokens);
  } catch (e1) {
    console.warn(`[bridge] drivers/call failed: ${e1.message} — trying OpenAI endpoint`);
    // Try OpenAI-compatible endpoint
    try {
      return await callPuterOpenAI(system, user, maxTokens);
    } catch (e2) {
      throw new Error(`Both Puter endpoints failed.\n  Path 1: ${e1.message}\n  Path 2: ${e2.message}`);
    }
  }
}

// ── HTTP Server ───────────────────────────────────────────────────────────────

const server = http.createServer(async (req, res) => {
  res.setHeader('Content-Type', 'application/json');
  res.setHeader('Access-Control-Allow-Origin', '*');

  // Health check
  if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200);
    res.end(JSON.stringify({
      status:    'ok',
      token_set: !!PUTER_TOKEN,
      model:     MODEL,
      port:      PORT,
    }));
    return;
  }

  // Guard: no token
  if (req.method === 'POST' && req.url === '/complete' && !PUTER_TOKEN) {
    res.writeHead(401);
    res.end(JSON.stringify({
      error: 'PUTER_AUTH_TOKEN not set. See puter_bridge/server.js for setup instructions.',
    }));
    return;
  }

  // Main completion endpoint
  if (req.method === 'POST' && req.url === '/complete') {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', async () => {
      try {
        const { system, user, max_tokens } = JSON.parse(body);
        if (!system || !user) {
          res.writeHead(400);
          res.end(JSON.stringify({ error: 'Missing "system" or "user" in request body' }));
          return;
        }
        console.log(`[bridge] Request: system=${system.slice(0,40)}... user=${user.slice(0,40)}...`);
        const text = await callWithFallback(system, user, max_tokens || 2048);
        console.log(`[bridge] Response: ${text.slice(0, 60)}...`);
        res.writeHead(200);
        res.end(JSON.stringify({ text }));
      } catch (e) {
        console.error(`[bridge] Error: ${e.message}`);
        res.writeHead(500);
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  res.writeHead(404);
  res.end(JSON.stringify({ error: 'Not found. Use POST /complete or GET /health' }));
});

server.listen(PORT, () => {
  console.log('');
  console.log('═══════════════════════════════════════════════════');
  console.log('  Puter Bridge Server v2.0');
  console.log(`  Listening : http://localhost:${PORT}`);
  console.log(`  Model     : ${MODEL}`);
  console.log(`  Token     : ${PUTER_TOKEN ? '✅ Set' : '❌ NOT SET – see setup below'}`);
  console.log('═══════════════════════════════════════════════════');
  if (!PUTER_TOKEN) {
    console.log('');
    console.log('  HOW TO GET YOUR FREE PUTER TOKEN:');
    console.log('  1. Go to https://puter.com and sign up (free)');
    console.log('  2. Open browser DevTools → Console tab');
    console.log('  3. Run: puter.auth.getToken().then(t => console.log(t))');
    console.log('  4. Copy the token, add to .env:');
    console.log('     PUTER_AUTH_TOKEN=your_token_here');
    console.log('  5. Restart: node puter_bridge/server.js');
    console.log('');
  }
});

server.on('error', e => {
  if (e.code === 'EADDRINUSE') {
    console.error(`\n❌ Port ${PORT} is already in use.`);
    console.error(`   Kill it: lsof -ti:${PORT} | xargs kill -9`);
  } else {
    console.error(`\n❌ Server error: ${e.message}`);
  }
  process.exit(1);
});
