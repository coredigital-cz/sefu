/**
 * Hybrid King — WhatsApp HTTP Bridge v4
 * ======================================
 * Production-grade WhatsApp API server.
 *
 * Endpoints:
 * GET  /status       -> connection state
 * POST /send         -> send message (with retry + error classification + media)
 * POST /sendTyping   -> ACK only (no getChatById — prevents Frame crash)
 * GET  /health       -> uptime + stats
 *
 * Anti-ban features:
 * - Message queue (serial sends, never concurrent)
 * - Random 100-500ms jitter before each send
 * - Auto-restart on Puppeteer crash
 * - 503 response during restart (Python waits gracefully)
 *
 * NEVER uses getChatById — it corrupts Puppeteer internal state
 * and causes "detached Frame" / "Cannot read properties" crashes.
 */

'use strict';

const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const http = require('http');

// ═══════════════════════════════════════════════════════════
// State tracking
// ═══════════════════════════════════════════════════════════

let clientReady = false;
let isRestarting = false;
let restartTimer = null;
let messageQueue = Promise.resolve();
const stats = {
    startedAt: new Date().toISOString(),
    messagesSent: 0,
    messagesFailed: 0,
    restarts: 0,
    lastSentAt: null,
    lastError: null,
};

// ═══════════════════════════════════════════════════════════
// Error classification
// ═══════════════════════════════════════════════════════════

function isNotOnWhatsApp(msg) {
    const m = msg.toLowerCase();
    return (
        m.includes('no lid') ||
        m.includes('not registered') ||
        m.includes('invalid wid') ||
        m.includes('checknumberstatus') ||
        m.includes('could not get phone') ||
        m.includes('invalid number') ||
        m.includes('not a valid') ||
        m.includes('phone number shared')
    );
}

function isCrashError(msg) {
    const m = msg.toLowerCase();
    return (
        m.includes('detached frame') ||
        m.includes('execution context') ||
        m.includes('target closed') ||
        m.includes('protocol error') ||
        m.includes('session closed') ||
        m.includes('cannot read properties') ||
        m.includes('page.evaluate') ||
        m.includes('cannot find context') ||
        m.includes('frame was detached')
    );
}

// ═══════════════════════════════════════════════════════════
// Client factory + auto-restart
// ═══════════════════════════════════════════════════════════

function createClient() {
    return new Client({
        authStrategy: new LocalAuth({
            dataPath: '/home/root/waha-simple/.wwebjs_auth'
        }),
        puppeteer: {
            args: [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--no-first-run',
                '--no-zygote',
                '--disable-extensions',
                '--disable-background-networking',
                '--disable-default-apps',
                '--mute-audio',
                '--single-process',
                '--memory-pressure-off',
                '--js-flags=--max-old-space-size=512'
            ],
            headless: true,
            timeout: 60000
        },
        restartOnAuthFail: true,
        takeoverOnConflict: true,
        takeoverTimeoutMs: 10000,
    });
}

let client = createClient();

function scheduleRestart(reason) {
    if (isRestarting) return;
    isRestarting = true;
    clientReady = false;
    stats.restarts++;
    stats.lastError = reason;

    const wait = 10;
    console.log(`[RESTART] In ${wait}s — reason: ${reason}`);

    if (restartTimer) clearTimeout(restartTimer);
    restartTimer = setTimeout(async () => {
        console.log('[RESTART] Destroying old client...');
        try { await client.destroy(); } catch (e) { /* ignore */ }

        client = createClient();
        attachEvents();
        console.log('[RESTART] Reinitializing...');
        try {
            await client.initialize();
        } catch (e) {
            console.error('[RESTART] Init failed:', e.message);
            isRestarting = false;
            scheduleRestart('init_failed: ' + e.message.slice(0, 60));
        }
    }, wait * 1000);
}

function attachEvents() {
    client.removeAllListeners();

    client.on('qr', qr => {
        console.log('\n=== SCAN QR CODE WITH YOUR PHONE ===');
        qrcode.generate(qr, { small: true });
        console.log('=== YOU HAVE 60 SECONDS ===\n');
    });

    client.on('authenticated', () => {
        console.log('[OK] Authenticated');
    });

    client.on('ready', () => {
        clientReady = true;
        isRestarting = false;
        console.log('');
        console.log('╔════════════════════════════════════════╗');
        console.log('║  WhatsApp CONNECTED — Status: WORKING  ║');
        console.log('║  HTTP API: http://localhost:3000        ║');
        console.log('╚════════════════════════════════════════╝');
        console.log('');
    });

    client.on('auth_failure', msg => {
        console.error('[ERROR] Auth failed:', msg);
        clientReady = false;
        scheduleRestart('auth_failure');
    });

    client.on('disconnected', reason => {
        console.log('[WARN] Disconnected:', reason);
        clientReady = false;
        if (reason !== 'LOGOUT') {
            scheduleRestart('disconnected: ' + reason);
        }
    });

    client.on('message', msg => {
        const from = msg.from.replace('@c.us', '').replace('@s.whatsapp.net', '');
        const body = (msg.body || '').substring(0, 100);
        console.log(`[REPLY] +${from}: ${body}`);
    });
}

// ═══════════════════════════════════════════════════════════
// Safe send — serialized queue + jitter
// Acum suportă conținut diversificat (media/text) și opțiuni
// ═══════════════════════════════════════════════════════════

function sendSafe(chatId, content, options = {}) {
    const result = messageQueue.then(async () => {
        if (!clientReady) throw new Error('CLIENT_NOT_READY');

        // Human-like random jitter 200-800ms
        const jitter = Math.floor(Math.random() * 600) + 200;
        await new Promise(r => setTimeout(r, jitter));

        return client.sendMessage(chatId, content, options);
    });
    messageQueue = result.catch(() => { });
    return result;
}

// ═══════════════════════════════════════════════════════════
// HTTP Server
// ═══════════════════════════════════════════════════════════

const server = http.createServer(async (req, res) => {
    res.setHeader('Content-Type', 'application/json');

    // ── GET /status ──
    if (req.method === 'GET' && req.url === '/status') {
        const st = clientReady ? 'WORKING' :
            isRestarting ? 'RESTARTING' : 'DISCONNECTED';
        res.writeHead(200);
        res.end(JSON.stringify({ status: st }));
        return;
    }

    // ── GET /health ──
    if (req.method === 'GET' && req.url === '/health') {
        res.writeHead(200);
        res.end(JSON.stringify({
            uptime_seconds: Math.floor(process.uptime()),
            ...stats,
            client_ready: clientReady,
        }));
        return;
    }

    if (req.method !== 'POST') {
        res.writeHead(404);
        res.end(JSON.stringify({ error: 'Not found' }));
        return;
    }

    // ── Parse body ──
    let body = '';
    req.on('data', c => { body += c; });
    req.on('end', async () => {
        let data;
        try { data = JSON.parse(body); }
        catch {
            res.writeHead(400);
            res.end(JSON.stringify({ error: 'Invalid JSON' }));
            return;
        }

        // ── POST /sendTyping ──
        // Returns 200 immediately — NO getChatById (prevents crash)
        // Python sleeps for the duration, creating the typing illusion
        if (req.url === '/sendTyping') {
            if (!data.phone) {
                res.writeHead(400);
                res.end(JSON.stringify({ error: 'Missing phone' }));
                return;
            }
            const d = data.duration || 7000;
            console.log(`[TYPE] ACK ${data.phone} ${d}ms`);
            res.writeHead(200);
            res.end(JSON.stringify({ success: true, duration: d }));
            return;
        }

        // ── POST /send ──
        if (req.url === '/send') {
            if (!data.phone || !data.message) {
                res.writeHead(400);
                res.end(JSON.stringify({ error: 'Missing phone or message' }));
                return;
            }

            if (!clientReady || isRestarting) {
                res.writeHead(503);
                res.end(JSON.stringify({
                    error: 'client_restarting',
                    retry_after: 30
                }));
                return;
            }

            const chatId = data.phone
                .replace(/\+/g, '')
                .replace(/[\s\-]/g, '') + '@c.us';

            // ── Încărcare Media (dacă există) ──
            let mediaContent = null;
            if (data.mediaUrl) {
                try {
                    mediaContent = await MessageMedia.fromUrl(data.mediaUrl, { unsafeMime: true });
                } catch (e) {
                    console.error(`[MEDIA FAIL] Nu s-a putut încărca imaginea de la URL: ${data.mediaUrl}`, e.message);
                    // Dacă pică media, continuăm pentru a trimite măcar textul
                }
            }

            let lastErr = null;
            for (let attempt = 1; attempt <= 2; attempt++) {
                try {
                    // Dacă avem mediaContent, îl trimitem cu mesajul sub formă de 'caption'
                    if (mediaContent) {
                        await sendSafe(chatId, mediaContent, { caption: data.message });
                    } else {
                        // Trimitere standard de text
                        await sendSafe(chatId, data.message);
                    }

                    stats.messagesSent++;
                    stats.lastSentAt = new Date().toISOString();
                    console.log(`[SEND] OK -> ${data.phone} (attempt ${attempt})`);
                    res.writeHead(200);
                    res.end(JSON.stringify({ success: true, phone: data.phone }));
                    return;

                } catch (err) {
                    lastErr = (err && err.message) || String(err);

                    if (lastErr === 'CLIENT_NOT_READY') {
                        res.writeHead(503);
                        res.end(JSON.stringify({ error: 'client_not_ready', retry_after: 15 }));
                        return;
                    }

                    if (isNotOnWhatsApp(lastErr)) {
                        console.log(`[SKIP] Not on WhatsApp: ${data.phone}`);
                        res.writeHead(200);
                        res.end(JSON.stringify({
                            success: false,
                            reason: 'not_on_whatsapp',
                            phone: data.phone
                        }));
                        return;
                    }

                    if (isCrashError(lastErr)) {
                        console.error(`[CRASH] ${data.phone}: ${lastErr.slice(0, 80)}`);
                        stats.messagesFailed++;
                        stats.lastError = lastErr.slice(0, 100);
                        scheduleRestart('crash: ' + lastErr.slice(0, 40));
                        res.writeHead(503);
                        res.end(JSON.stringify({
                            error: 'client_restarting',
                            retry_after: 30
                        }));
                        return;
                    }

                    if (attempt === 1) {
                        console.log(`[RETRY] ${data.phone} in 3s...`);
                        await new Promise(r => setTimeout(r, 3000));
                    }
                }
            }

            stats.messagesFailed++;
            stats.lastError = lastErr.slice(0, 100);
            console.error(`[FAIL] ${data.phone}: ${lastErr.slice(0, 100)}`);
            res.writeHead(500);
            res.end(JSON.stringify({ error: lastErr }));
            return;
        }

        res.writeHead(404);
        res.end(JSON.stringify({ error: 'Route not found' }));
    });
});

server.listen(3000, '127.0.0.1', () => {
    console.log('[OK] HTTP server on http://127.0.0.1:3000');
});

server.on('error', err => {
    if (err.code === 'EADDRINUSE') {
        console.error('[ERROR] Port 3000 in use. Run: fuser -k 3000/tcp');
        process.exit(1);
    }
    console.error('[ERROR]', err.message);
});

// ═══════════════════════════════════════════════════════════
// Start
// ═══════════════════════════════════════════════════════════
attachEvents();
console.log('[...] Starting WhatsApp — waiting for connection...');
client.initialize();