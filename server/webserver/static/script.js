let ws;
let progressChart = null;
let continuous = false;

// Authotification
function showRegister() {
    document.getElementById('login-form').style.display = 'none';
    document.getElementById('register-form').style.display = 'block';
}

function showLogin() {
    document.getElementById('register-form').style.display = 'none';
    document.getElementById('login-form').style.display = 'block';
}

function showDashboard() {
    document.getElementById('auth-section').style.display = 'none';
    document.getElementById('dashboard-section').style.display = 'block';
    connect();
    loadReadings();
}

function showAuth() {
    document.getElementById('dashboard-section').style.display = 'none';
    document.getElementById('auth-section').style.display = 'block';
    showLogin();
    if (ws) {
        ws.onclose = null;  
        ws.close();
        ws = null;
    }
}

async function login() {
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    const error = document.getElementById('login-error');

    const response = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
    });

    if (response.ok) {
        error.style.display = 'none';
        showDashboard();
    } else {
        error.textContent = 'Invalid username or password';
        error.style.display = 'block';
    }
}

async function register() {
    const username = document.getElementById('reg-username').value;
    const password = document.getElementById('reg-password').value;
    const error = document.getElementById('register-error');

    const response = await fetch('/api/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
    });

    if (response.ok) {
        error.style.display = 'none';
        showDashboard();
    } else {
        error.textContent = response.status === 409 ? 'Username already exists' : 'Registration failed';
        error.style.display = 'block';
    }
}

async function logout() {
     await fetch('/api/logout', { method: 'POST' });
    if (ws) ws.close();
    showAuth();
}



// -- Websocket

function renderHeatmap(pixels) {
    const canvas = document.getElementById('heatmap');
    const ctx = canvas.getContext('2d');
    const cellSize = canvas.width / 8;

    const minTemp = Math.min(...pixels);
    const maxTemp = Math.max(...pixels);
    const range = maxTemp - minTemp || 1;

    for (let row = 0; row < 8; row++) {
        for (let col = 0; col < 8; col++) {
            const temp = pixels[row * 8 + col];
            const norm = (temp - minTemp) / range;
            const r = Math.floor(255 * Math.min(1, norm * 2));
            const g = Math.floor(255 * Math.max(0, (norm - 0.5) * 2));
            const b = Math.floor(255 * (1 - norm));
            ctx.fillStyle = `rgb(${r},${g},${b})`;
            ctx.fillRect(col * cellSize, row * cellSize, cellSize, cellSize);
        }
    }
}

function connect() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws`);

    ws.onopen = () => {
        document.getElementById('status').textContent = 'Connected';
        document.getElementById('status').className = 'connected';
    };

    ws.onclose = () => {
        document.getElementById('status').textContent = 'Disconnected';
        document.getElementById('status').className = 'disconnected';
       setTimeout(connect, 1000);
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.event !== 'new_reading') return;

            renderHeatmap(data.pixels);

            document.getElementById('thermistor').textContent = data.thermistor != null ? data.thermistor.toFixed(1) : '--';
            document.getElementById('prediction').textContent = data.prediction || '--';
            document.getElementById('confidence').textContent = data.confidence != null ? (data.confidence * 100).toFixed(1) + '%' : '--';
            document.getElementById('mac-display').textContent = data.mac_address || '--';

            prependRow(data);
        } catch (e) {
            console.error('Failed to parse message:', e);
        }
    };
}


async function sendCommand(cmd) {
    try {
        const response = await fetch('/api/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: cmd })
        });
        showMessage(response.ok ? `Sent: ${cmd}` : `Error ${response.status}`);
    } catch (e) {
        showMessage('Network error');
    }
}

async function toggleContinuous() {
    continuous = !continuous;
    const btn = document.getElementById('btn-continuous');
    if (continuous) {
        btn.textContent = 'Stop Continuous';
        btn.classList.add('active');
        await sendCommand('start_continuous');
    } else {
        btn.textContent = 'Start Continuous';
        btn.classList.remove('active');
        await sendCommand('stop');
    }
}

// -- History table
async function loadReadings() {
    const mac = document.getElementById('mac-filter').value.trim();
    const url = mac ? `/api/readings?device_mac=${encodeURIComponent(mac)}` : '/api/readings';
    const rows = await (await fetch(url)).json();

    const tbody = document.getElementById('readings-tbody');
    tbody.innerHTML = '';
    document.getElementById('no-data').style.display = rows.length ? 'none' : 'block';
    rows.forEach(r => tbody.appendChild(buildRow(r)));
}

function buildRow(data) {
    const pred = (data.prediction || '').toUpperCase();
    const tr = document.createElement('tr');
    tr.id = `row-${data.id}`;
    tr.innerHTML = `
        <td>#${data.id}</td>
        <td>${data.mac_address}</td>
        <td>${data.thermistor_temp != null ? Number(data.thermistor_temp).toFixed(2) : data.thermistor != null ? Number(data.thermistor).toFixed(2) : '--'}</td>
        <td class="pred ${pred === 'PRESENT' ? 'present' : 'empty'}">${pred}</td>
        <td>${data.confidence != null ? (data.confidence * 100).toFixed(1) + '%' : '--'}</td>
        <td>${data.created_at ? data.created_at.slice(0, 19) : new Date().toISOString().slice(0, 19)}</td>
        <td><button class="del" onclick="deleteReading(${data.id})">✕</button></td>
    `;
    tr.addEventListener('click', (e) => {
        if (e.target.classList.contains('del')) return;
        if (data.pixels && data.pixels.length === 64) renderHeatmap(data.pixels);
    });
    return tr;
}

function prependRow(data) {
    const tbody = document.getElementById('readings-tbody');
    tbody.prepend(buildRow(data));
    document.getElementById('no-data').style.display = 'none';
    const tr = document.getElementById(`row-${data.id}`);
    if (tr) {
        tr.classList.add('flash');
        setTimeout(() => tr.classList.remove('flash'), 1500);
    }
}

async function deleteReading(id) {
    const r = await fetch(`/api/readings/${id}`, { method: 'DELETE' });
    if (r.ok) {
        document.getElementById(`row-${id}`)?.remove();
        const tbody = document.getElementById('readings-tbody');
        if (!tbody.children.length) document.getElementById('no-data').style.display = 'block';
    }
}

function clearFilter() {
    document.getElementById('mac-filter').value = '';
    loadReadings();
}

let msgTimer;
function showMessage(msg) {
    const el = document.getElementById('message');
    el.textContent = msg;
    clearTimeout(msgTimer);
    msgTimer = setTimeout(() => el.textContent = '', 3000);
}

document.addEventListener('DOMContentLoaded', async () => {
    const response = await fetch('/api/readings');
    if (response.ok) {
        showDashboard();  
    } else {
        showAuth();       
    }
});
