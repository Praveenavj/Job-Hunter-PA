/**
 * Puter Bridge Server v1.0
 * ========================
 * Acts as a local middleware between Python (FastAPI backend) and
 * Puter's free Claude API. Runs on localhost:3456.
 *
 * How it works:
 *   1. You get a free Puter auth token (sign up at puter.com - free)
 *   2. Python calls http://localhost:3456/complete with system+user prompts
 *   3. This server forwards to Puter's OpenAI-compatible API
 *   4. Returns Claude's response to Python
 *
 * Puter "User-Pays" model: YOUR Puter account pays the tiny AI cost.
 * Puter gives generous free credits. For a personal bot, it's effectively free.
 *
 * Start: node puter_bridge/server.js
 * Or:    PUTER_AUTH_TOKEN=your_token node puter_bridge/server.js
 */


// ── LOAD .env FROM PROJECT ROOT (CRITICAL) ──
const path = require('path');
const fs = require('fs');

// Explicitly load .env from project root
const envPath = path.resolve(__dirname, '..', '.env');
console.log('🔍 Loading .env from:', envPath);

if (fs.existsSync(envPath)) {
  require('dotenv').config({ path: envPath, override: true });
  console.log('✅ .env loaded');
} else {
  console.error('❌ .env NOT FOUND at:', envPath);
}

// Debug: show token status BEFORE server starts
const token = process.env.PUTER_AUTH_TOKEN?.trim();
console.log('🔑 PUTER_AUTH_TOKEN length:', token ? token.length : 'UNSET');
console.log('🔑 First 40 chars:', token ? token.substring(0,40) + '...' : 'N/A');
console.log('');
// ── END LOAD .env ──

const http  = require('http');
const https = require('https');

const PORT         = parseInt(process.env.PUTER_BRIDGE_PORT || '3456');
const PUTER_TOKEN  = process.env.PUTER_AUTH_TOKEN?.trim() || '';
const MODEL        = process.env.PUTER_MODEL || 'anthropic/claude-sonnet-4-5';

// ── Core Puter API call ────────────────────────────────────────────────────

function callPuter(messages, maxTokens) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ model: MODEL, messages, max_tokens: maxTokens });

    const options = {
      hostname: 'api.puter.com',
      path:     '/puterai/openai/v1/chat/completions',
      method:   'POST',
      headers:  {
        'Content-Type':   'application/json',
        'Authorization':  `Bearer ${PUTER_TOKEN}`,
        'Content-Length': Buffer.byteLength(body),
      },
    };

    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', (c) => { data += c; });
      res.on('end', () => {
        try {
          const r = JSON.parse(data);
          if (r.choices?.[0]?.message?.content) {
            resolve(r.choices[0].message.content);
          } else if (r.error) {
            reject(new Error(r.error.message || JSON.stringify(r.error)));
          } else {
            reject(new Error('Unexpected Puter response: ' + data.slice(0, 200)));
          }
        } catch (e) {
          reject(new Error('Parse error: ' + e.message + ' raw: ' + data.slice(0, 100)));
        }
      });
    });

    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

// ── HTTP Server ────────────────────────────────────────────────────────────

const server = http.createServer(async (req, res) => {
  res.setHeader('Content-Type', 'application/json');
  res.setHeader('Access-Control-Allow-Origin', '*');

  // Health check
  if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200);
    res.end(JSON.stringify({
      status:    'ok',
      provider:  'puter',
      model:     MODEL,
      token_set: !!PUTER_TOKEN,
    }));
    return;
  }

  // Main completion endpoint - matches what llm_client.py expects
  if (req.method === 'POST' && req.url === '/complete') {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', async () => {
      try {
        const { system, user, max_tokens } = JSON.parse(body);
        const messages = [];
        if (system) messages.push({ role: 'system',    content: system });
        if (user)   messages.push({ role: 'user',      content: user });

        const text = await callPuter(messages, max_tokens || 2048);
        res.writeHead(200);
        res.end(JSON.stringify({ text }));
      } catch (e) {
        console.error('Puter error:', e.message);
        res.writeHead(500);
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  res.writeHead(404);
  res.end(JSON.stringify({ error: 'Not found' }));
});

server.listen(PORT, '127.0.0.1', () => {
  console.log('');
  console.log('═══════════════════════════════════════════');
  console.log('  Puter Bridge Server – Free Claude AI');
  console.log(`  Port  : http://localhost:${PORT}`);
  console.log(`  Model : ${MODEL}`);
  console.log(`  Token : ${PUTER_TOKEN ? '✅ Set' : '❌ NOT SET – see setup below'}`);
  console.log('═══════════════════════════════════════════');
  if (!PUTER_TOKEN) {
    console.log('');
    console.log('  HOW TO GET YOUR FREE PUTER TOKEN:');
    console.log('  1. Go to https://puter.com and create a free account');
    console.log('  2. Open browser DevTools → Console');
    console.log('  3. Run: puter.auth.getToken().then(t => console.log(t))');
    console.log('  4. Copy the token and add to .env:');
    console.log('     PUTER_AUTH_TOKEN=your_token_here');
    console.log('  5. Restart: node puter_bridge/server.js');
    console.log('');
  }
});
