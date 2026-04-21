/**
 * OpenAkita WhatsApp Web Bridge — baileys 7.x
 *
 * Connects to WhatsApp via Baileys (WhatsApp Web multi-device protocol)
 * and exposes a simple HTTP API for the Python adapter.
 *
 * Migration notes (baileys 6 -> 7):
 *   - ESM only (this file is ES module via package.json "type": "module")
 *   - Requires Node.js >= 20
 *   - LID system: WhatsApp now exposes Local IDs to anonymize phone numbers in
 *     large groups. We do NOT try to "restore" the PN; we surface the LID JID
 *     and the optional PN alias (`participantPn` / `remoteJidAlt`) when WA
 *     provides it. Downstream just receives whichever identifier WA chose.
 *   - `useMultiFileAuthState` already persists the new keys (lid-mapping,
 *     device-list, tctoken) — no extra wiring needed.
 *   - We do NOT auto-send read ACKs; baileys 7 stopped doing it by default
 *     to reduce ban risk, and an AI bot has no reason to mark messages read.
 *
 * Environment variables:
 *   BRIDGE_PORT     - HTTP server port (default 9882)
 *   BRIDGE_DATA_DIR - Directory for auth/session data
 *   CALLBACK_URL    - URL to POST incoming messages to
 */

import express from 'express';
import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  Browsers,
} from '@whiskeysockets/baileys';
import pino from 'pino';

const PORT = parseInt(process.env.BRIDGE_PORT || '9882', 10);
const DATA_DIR = process.env.BRIDGE_DATA_DIR || './data';
const CALLBACK_URL = process.env.CALLBACK_URL || 'http://127.0.0.1:9881/whatsapp/webhook';

const logger = pino({ level: 'warn' });
const app = express();
app.use(express.json());

let sock = null;
let currentQR = null;
let connectionStatus = 'disconnected';

async function startSocket() {
  const { state, saveCreds } = await useMultiFileAuthState(DATA_DIR);

  sock = makeWASocket({
    auth: state,
    logger,
    printQRInTerminal: true,
    browser: Browsers.ubuntu('OpenAkita'),
    // Returning undefined is acceptable — WA will simply not retry decrypt of
    // messages we never stored. We don't keep a message store in this bridge.
    getMessage: async () => undefined,
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;
    if (qr) {
      currentQR = qr;
      connectionStatus = 'qr_ready';
      console.log('[bridge] QR code generated — scan to pair');
    }
    if (connection === 'open') {
      currentQR = null;
      connectionStatus = 'connected';
      console.log('[bridge] Connected to WhatsApp');
    }
    if (connection === 'close') {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      connectionStatus = 'disconnected';
      if (statusCode !== DisconnectReason.loggedOut) {
        console.log('[bridge] Connection closed, reconnecting...');
        setTimeout(startSocket, 3000);
      } else {
        console.log('[bridge] Logged out — restart bridge to re-pair');
      }
    }
  });

  sock.ev.on('messages.upsert', async ({ messages }) => {
    for (const msg of messages) {
      if (msg.key.fromMe) continue;
      try {
        const payload = formatMessage(msg);
        await fetch(CALLBACK_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      } catch (err) {
        console.error('[bridge] Callback failed:', err.message);
      }
    }
  });
}

/**
 * Resolve a sender identifier given baileys 7's dual LID/PN world.
 *
 * Priority:
 *   1. participant (the canonical sender JID inside a group)
 *   2. remoteJid (DM sender)
 *
 * For each, we prefer the PN-alias if WA exposed one (participantPn /
 * remoteJidAlt), otherwise we fall back to the LID/JID itself. The downstream
 * Python adapter receives whatever stable identifier WA chose; do NOT try to
 * "restore" the underlying phone number from a LID — see official migration
 * guide (https://whiskey.so/migrate-latest).
 */
function resolveSender(msg) {
  const key = msg.key || {};
  const jid = key.remoteJid || '';
  const isGroup = jid.endsWith('@g.us');

  if (isGroup) {
    // In groups, prefer PN alias if available (the LID is opaque)
    return key.participantPn || key.participant || '';
  }
  // DM: remoteJidAlt is the PN alias when remoteJid is a LID
  return key.remoteJidAlt || jid;
}

function formatMessage(msg) {
  const jid = msg.key.remoteJid || '';
  const isGroup = jid.endsWith('@g.us');
  const sender = resolveSender(msg);
  const text = msg.message?.conversation
    || msg.message?.extendedTextMessage?.text
    || '';

  return {
    entry: [{
      changes: [{
        value: {
          messages: [{
            id: msg.key.id,
            from: sender.replace(/@.*/, ''),
            type: 'text',
            text: { body: text },
            timestamp: String(msg.messageTimestamp || Math.floor(Date.now() / 1000)),
            context: isGroup ? { group_id: jid } : {},
          }],
          contacts: [{
            wa_id: sender.replace(/@.*/, ''),
            profile: { name: msg.pushName || '' },
          }],
        },
      }],
    }],
  };
}

// --- HTTP API ---

app.get('/qr', (req, res) => {
  res.json({ qr: currentQR, status: connectionStatus });
});

app.get('/status', (req, res) => {
  res.json({ status: connectionStatus });
});

app.post('/send', async (req, res) => {
  const { chat_id, text, reply_to, media_url, media_type } = req.body;
  if (!sock || connectionStatus !== 'connected') {
    return res.status(503).json({ error: 'Not connected' });
  }
  try {
    const jid = chat_id.includes('@') ? chat_id : `${chat_id}@s.whatsapp.net`;
    const opts = {};
    if (reply_to) {
      opts.quoted = { key: { id: reply_to, remoteJid: jid } };
    }
    let result;
    if (media_url && media_type === 'image') {
      result = await sock.sendMessage(jid, { image: { url: media_url }, caption: text || '' }, opts);
    } else {
      result = await sock.sendMessage(jid, { text: text || '' }, opts);
    }
    res.json({ message_id: result?.key?.id || '' });
  } catch (err) {
    console.error('[bridge] Send failed:', err.message);
    res.status(500).json({ error: err.message });
  }
});

app.listen(PORT, () => {
  console.log(`[bridge] HTTP API on port ${PORT}`);
  startSocket();
});
