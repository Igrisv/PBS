const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const express = require('express');
const bodyParser = require('body-parser');

const app = express();
const port = process.env.PORT || 3000;

app.use(bodyParser.json());

// Inicializar cliente de WhatsApp con persistencia de sesión
const client = new Client({
    authStrategy: new LocalAuth({
        dataPath: './sessions'
    }),
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

client.on('qr', (qr) => {
    console.log('--- ESCANEA ESTE CÓDIGO QR CON TU WHATSAPP ---');
    qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
    console.log('✅ WhatsApp Bridge está LISTO!');
    
    // Opcional: Listar grupos para ayudar al usuario a encontrar el ID
    client.getChats().then(chats => {
        const groups = chats.filter(chat => chat.isGroup);
        console.log('\n--- GRUPOS DETECTADOS ---');
        groups.forEach(group => {
            console.log(`Nombre: ${group.name} | ID: ${group.id._serialized}`);
        });
        console.log('-------------------------\n');
    });
});

client.on('authenticated', () => {
    console.log('✅ Autenticado correctamente');
});

client.on('auth_failure', msg => {
    console.error('❌ Error de autenticación:', msg);
});

// Endpoint para enviar alertas
app.post('/send-alert', async (req, res) => {
    const { chatId, message, mediaUrl } = req.body;

    if (!chatId || !message) {
        return res.status(400).json({ error: 'Faltan parámetros: chatId o message' });
    }

    try {
        let result;
        if (mediaUrl) {
            console.log(`📸 Intentando enviar imagen desde: ${mediaUrl}`);
            try {
                const media = await MessageMedia.fromUrl(mediaUrl, { unsafe: true });
                result = await client.sendMessage(chatId, media, { caption: message });
            } catch (mediaError) {
                console.error('❌ Error cargando media, enviando solo texto:', mediaError.message);
                result = await client.sendMessage(chatId, message);
            }
        } else {
            result = await client.sendMessage(chatId, message);
        }
        
        console.log(`✅ Mensaje enviado a ${chatId}`);
        res.json({ success: true, messageId: result.id._serialized });
    } catch (error) {
        console.error('❌ Error enviando mensaje:', error);
        res.status(500).json({ error: 'No se pudo enviar el mensaje', details: error.message });
    }
});

// Iniciar servidor API
app.listen(port, () => {
    console.log(`🚀 API del Bridge escuchando en http://localhost:${port}`);
});

// Iniciar cliente de WhatsApp
client.initialize();
