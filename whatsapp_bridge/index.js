const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const QRCode = require('qrcode');
const express = require('express');
const http = require('http');
const { Server } = require('socket.io');
const bodyParser = require('body-parser');
const path = require('path');
const fs = require('fs');

const app = express();
const server = http.createServer(app);
const io = new Server(server);
const port = process.env.PORT || 3000;

app.use(bodyParser.json());
app.use(express.static(path.join(__dirname, 'panel')));

// Estado global del bridge
let bridgeState = {
    status: 'disconnected', // connecting | authenticated | ready | disconnected
    phone: null,
    startTime: Date.now(),
    lastQR: null,
    chats: []
};

// ─── Utilidad de Log ────────────────────────────────────────────────────────
function bridgeLog(type, message) {
    const ts = new Date().toLocaleTimeString('es-MX', { hour12: false });
    const entry = { type, message, time: ts };
    console.log(`[${ts}] [${type.toUpperCase()}] ${message}`);
    io.emit('log', entry);
}

// ─── Cliente WhatsApp ───────────────────────────────────────────────────────
const client = new Client({
    authStrategy: new LocalAuth({ dataPath: './sessions' }),
    webVersionCache: {
        type: 'remote',
        remotePath: 'https://raw.githubusercontent.com/wppconnect-team/wa-version/main/html/2.3000.1018873523-alpha.html',
    },
    puppeteer: {
        args: ['--no-sandbox', '--disable-setuid-sandbox'],
        executablePath: process.env.CHROME_BIN || null,
        headless: 'new'
    }
});

client.on('qr', async (qr) => {
    bridgeState.status = 'connecting';
    bridgeState.lastQR = null;
    bridgeLog('info', 'Nuevo QR generado — escanea desde el panel web');

    // ASCII en consola (fallback)
    qrcode.generate(qr, { small: true });

    // PNG base64 para el panel
    try {
        const qrDataURL = await QRCode.toDataURL(qr, {
            width: 320,
            margin: 2,
            color: { dark: '#0f172a', light: '#f8fafc' }
        });
        bridgeState.lastQR = qrDataURL;
        io.emit('qr', { qr: qrDataURL });
        io.emit('status', { status: 'connecting', phone: null });
    } catch (err) {
        bridgeLog('error', 'Error generando QR PNG: ' + err.message);
    }
});

client.on('authenticated', () => {
    bridgeState.status = 'authenticated';
    bridgeState.lastQR = null;
    bridgeLog('success', 'Autenticado correctamente');
    io.emit('status', { status: 'authenticated', phone: bridgeState.phone });
});

client.on('auth_failure', (msg) => {
    bridgeState.status = 'disconnected';
    bridgeLog('error', 'Error de autenticación: ' + msg);
    io.emit('status', { status: 'disconnected', phone: null });
});

client.on('ready', async () => {
    bridgeState.status = 'ready';
    try {
        const info = client.info;
        bridgeState.phone = info?.wid?.user || 'Desconocido';
    } catch (_) {}
    bridgeLog('success', `Bridge LISTO | Teléfono: ${bridgeState.phone}`);
    io.emit('status', { status: 'ready', phone: bridgeState.phone });

    // Cargar chats al estar listo
    await refreshChats();
});

client.on('disconnected', (reason) => {
    bridgeState.status = 'disconnected';
    bridgeState.phone = null;
    bridgeState.chats = [];
    bridgeLog('warn', 'Bridge desconectado: ' + reason);
    io.emit('status', { status: 'disconnected', phone: null });
});

// ─── Helpers ────────────────────────────────────────────────────────────────
async function refreshChats() {
    try {
        const chats = await client.getChats();
        bridgeState.chats = chats.map(c => ({
            id: c.id._serialized,
            name: c.name || c.id.user,
            isGroup: c.isGroup,
            participants: c.isGroup ? (c.participants?.length ?? '?') : null,
            unreadCount: c.unreadCount
        }));
        bridgeLog('info', `${bridgeState.chats.length} chats cargados`);
        io.emit('chats', bridgeState.chats);
    } catch (err) {
        bridgeLog('error', 'Error cargando chats: ' + err.message);
    }
}

// ─── Socket.IO ──────────────────────────────────────────────────────────────
io.on('connection', (socket) => {
    bridgeLog('info', 'Cliente conectado al panel web');

    // Enviar estado actual al nuevo cliente
    socket.emit('status', { status: bridgeState.status, phone: bridgeState.phone });
    if (bridgeState.status === 'connecting' && bridgeState.lastQR) {
        socket.emit('qr', { qr: bridgeState.lastQR });
    }
    if (bridgeState.chats.length > 0) {
        socket.emit('chats', bridgeState.chats);
    }

    socket.on('refresh_chats', async () => {
        if (bridgeState.status === 'ready') await refreshChats();
    });
});

// ─── API REST ────────────────────────────────────────────────────────────────

// GET /api/status
app.get('/api/status', (req, res) => {
    res.json({
        status: bridgeState.status,
        phone: bridgeState.phone,
        uptime: Math.floor((Date.now() - bridgeState.startTime) / 1000),
        chatsLoaded: bridgeState.chats.length
    });
});

// GET /api/chats
app.get('/api/chats', async (req, res) => {
    if (bridgeState.status !== 'ready') {
        return res.status(503).json({ error: 'El bridge no está listo todavía' });
    }
    await refreshChats();
    res.json(bridgeState.chats);
});

// POST /api/logout
app.post('/api/logout', async (req, res) => {
    try {
        await client.logout();
        bridgeLog('warn', 'Sesión cerrada desde el panel web');
        res.json({ success: true });
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

// POST /send-alert  (backward-compat)
app.post('/send-alert', async (req, res) => {
    const { chatId, message, mediaUrl } = req.body;

    if (!chatId || !message) {
        return res.status(400).json({ error: 'Faltan parámetros: chatId o message' });
    }

    try {
        let result;
        if (mediaUrl) {
            bridgeLog('info', `Enviando imagen desde: ${mediaUrl}`);
            try {
                const media = await MessageMedia.fromUrl(mediaUrl, { unsafe: true });
                result = await client.sendMessage(chatId, media, { caption: message });
            } catch (mediaError) {
                bridgeLog('warn', 'Error cargando media, enviando solo texto: ' + mediaError.message);
                result = await client.sendMessage(chatId, message);
            }
        } else {
            result = await client.sendMessage(chatId, message);
        }
        bridgeLog('success', `Mensaje enviado a ${chatId}`);
        res.json({ success: true, messageId: result.id._serialized });
    } catch (error) {
        bridgeLog('error', 'Error enviando mensaje: ' + error.message);
        res.status(500).json({ error: 'No se pudo enviar el mensaje', details: error.message });
    }
});

// SPA fallback → panel
app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'panel', 'index.html'));
});

// ─── Arranque ────────────────────────────────────────────────────────────────
server.listen(port, () => {
    bridgeLog('info', `🚀 Panel web disponible en http://localhost:${port}`);
});

bridgeState.status = 'connecting';
client.initialize();
