let ws;
let continuous = false;
let statsTotal = 0;
let statsPresent = 0;
let statsTempSum = 0;
let statsTempCount = 0;

// ── Thermal colormap (black → purple → red → orange → yellow → white) ──
const THERMAL_STOPS = [
    [0,   0,   0  ],  // 0.00 — black
    [100, 0,   130],  // 0.25 — dark purple
    [200, 0,   50 ],  // 0.50 — crimson
    [255, 100, 0  ],  // 0.75 — orange
    [255, 230, 0  ],  // 0.90 — yellow
    [255, 255, 255],  // 1.00 — white
];

const STOP_POSITIONS = [0, 0.25, 0.5, 0.75, 0.9, 1.0];

function thermalColor(norm) {
    for (let i = 1; i < STOP_POSITIONS.length; i++) {
        if (norm <= STOP_POSITIONS[i]) {
            const t = (norm - STOP_POSITIONS[i - 1]) / (STOP_POSITIONS[i] - STOP_POSITIONS[i - 1]);
            const a = THERMAL_STOPS[i - 1];
            const b = THERMAL_STOPS[i];
            return [
                Math.round(a[0] + t * (b[0] - a[0])),
                Math.round(a[1] + t * (b[1] - a[1])),
                Math.round(a[2] + t * (b[2] - a[2])),
            ];
        }
    }
    return THERMAL_STOPS[THERMAL_STOPS.length - 1];
}

// ── Heatmap rendering (smooth bilinear upscale) ──────────────────────
function renderHeatmap(pixels) {
    const canvas = document.getElementById('heatmap');
    const ctx = canvas.getContext('2d');
    const W = canvas.width;

    const minT = Math.min(...pixels);
    const maxT = Math.max(...pixels);
    const range = maxT - minT || 1;

    // Draw 8×8 to offscreen canvas
    const offscreen = document.createElement('canvas');
    offscreen.width = 8;
    offscreen.height = 8;
    const octx = offscreen.getContext('2d');
    const imageData = octx.createImageData(8, 8);

    for (let i = 0; i < 64; i++) {
        const norm = (pixels[i] - minT) / range;
        const [r, g, b] = thermalColor(norm);
        imageData.data[i * 4]     = r;
        imageData.data[i * 4 + 1] = g;
        imageData.data[i * 4 + 2] = b;
        imageData.data[i * 4 + 3] = 255;
    }
    octx.putImageData(imageData, 0, 0);

    // Upscale smoothly to main canvas
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.clearRect(0, 0, W, W);
    ctx.drawImage(offscreen, 0, 0, W, W);

    // Update temp scale labels
    document.getElementById('temp-max').textContent = maxT.toFixed(1) + '°';
    document.getElementById('temp-min').textContent = minT.toFixed(1) + '°';
}

// ── Authentication ───────────────────────────────────────────────────
function showRegister() {
    document.getElementById('login-form').style.display = 'none';
    document.getElementById('register-form').style.display = 'flex';
    document.getElementById('reg-username').focus();
}

function showLogin() {
    document.getElementById('register-form').style.display = 'none';
    document.getElementById('login-form').style.display = 'flex';
    document.getElementById('username').focus();
}

function showDashboard() {
    document.getElementById('auth-section').style.display = 'none';
    document.getElementById('dashboard-section').style.display = 'block';
    connect();
    loadReadings();
}

function showAuth() {
    document.getElementById('dashboard-section').style.display = 'none';
    document.getElementById('auth-section').style.display = 'flex';
    showLogin();
    if (ws) {
        ws.onclose = null;
        ws.close();
        ws = null;
    }
}

async function login() {
    const username = document.getElementById('username').value.trim();
    const password = document.getElementById('password').value;
    const error = document.getElementById('login-error');

    if (!username || !password) {
        showFormError(error, 'Please enter your username and password.');
        return;
    }

    const response = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
    });

    if (response.ok) {
        error.style.display = 'none';
        showDashboard();
    } else {
        showFormError(error, 'Invalid username or password.');
    }
}

async function register() {
    const username = document.getElementById('reg-username').value.trim();
    const password = document.getElementById('reg-password').value;
    const error = document.getElementById('register-error');

    if (!username || !password) {
        showFormError(error, 'Please fill in all fields.');
        return;
    }

    const response = await fetch('/api/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
    });

    if (response.ok) {
        error.style.display = 'none';
        showDashboard();
    } else {
        showFormError(error, response.status === 409 ? 'Username already taken.' : 'Registration failed.');
    }
}

async function logout() {
    await fetch('/api/logout', { method: 'POST' });
    if (ws) ws.close();
    showAuth();
}

function showFormError(el, msg) {
    el.textContent = msg;
    el.style.display = 'block';
}

// ── Enter key for auth forms ─────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('password').addEventListener('keydown', e => {
        if (e.key === 'Enter') login();
    });
    document.getElementById('username').addEventListener('keydown', e => {
        if (e.key === 'Enter') document.getElementById('password').focus();
    });
    document.getElementById('reg-password').addEventListener('keydown', e => {
        if (e.key === 'Enter') register();
    });
    document.getElementById('mac-filter').addEventListener('keydown', e => {
        if (e.key === 'Enter') loadReadings();
    });
});

// ── WebSocket ────────────────────────────────────────────────────────
function setStatus(connected) {
    const el = document.getElementById('status');
    const txt = document.getElementById('status-text');
    el.className = connected ? 'connected' : 'disconnected';
    txt.textContent = connected ? 'Connected' : 'Disconnected';
}

function connect() {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${window.location.host}/ws`);

    ws.onopen  = () => setStatus(true);
    ws.onclose = () => {
        setStatus(false);
        setTimeout(connect, 1500);
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.event !== 'new_reading') return;

            renderHeatmap(data.pixels);

            const predEl = document.getElementById('prediction');
            const pred = (data.prediction || '').toUpperCase();
            predEl.textContent = pred || '--';
            predEl.className = 'info-value' + (pred === 'PRESENT' ? ' present' : pred === 'EMPTY' ? ' empty' : '');

            document.getElementById('thermistor').textContent =
                data.thermistor != null ? data.thermistor.toFixed(1) : '--';
            document.getElementById('confidence').textContent =
                data.confidence != null ? (data.confidence * 100).toFixed(1) + '%' : '--';
            document.getElementById('mac-display').textContent = data.mac_address || '--';

            updateStats(data);
            prependRow(data);
        } catch (e) {
            console.error('WS parse error:', e);
        }
    };
}

// ── Stats ────────────────────────────────────────────────────────────
function updateStats(data) {
    statsTotal++;
    if ((data.prediction || '').toUpperCase() === 'PRESENT') statsPresent++;
    if (data.thermistor != null) {
        statsTempSum += data.thermistor;
        statsTempCount++;
    }
    document.getElementById('stat-total').textContent = statsTotal;
    document.getElementById('stat-present').textContent = statsPresent;
    document.getElementById('stat-rate').textContent =
        statsTotal > 0 ? ((statsPresent / statsTotal) * 100).toFixed(0) + '%' : '--%';
    document.getElementById('stat-avg').textContent =
        statsTempCount > 0 ? (statsTempSum / statsTempCount).toFixed(1) + ' °C' : '-- °C';
}

function resetStats(rows) {
    statsTotal = rows.length;
    statsPresent = rows.filter(r => (r.prediction || '').toUpperCase() === 'PRESENT').length;
    statsTempSum = rows.reduce((s, r) => s + (r.thermistor_temp || r.thermistor || 0), 0);
    statsTempCount = rows.filter(r => r.thermistor_temp != null || r.thermistor != null).length;

    document.getElementById('stat-total').textContent = statsTotal;
    document.getElementById('stat-present').textContent = statsPresent;
    document.getElementById('stat-rate').textContent =
        statsTotal > 0 ? ((statsPresent / statsTotal) * 100).toFixed(0) + '%' : '--%';
    document.getElementById('stat-avg').textContent =
        statsTempCount > 0 ? (statsTempSum / statsTempCount).toFixed(1) + ' °C' : '-- °C';
}

// ── Commands ─────────────────────────────────────────────────────────
async function sendCommand(cmd) {
    try {
        const response = await fetch('/api/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: cmd })
        });
        if (response.ok) {
            showMessage(`Command sent: ${cmd}`);
        } else {
            showToast(`Error ${response.status}`, 'error');
        }
    } catch {
        showToast('Network error — command not sent', 'error');
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

// ── History table ────────────────────────────────────────────────────
async function loadReadings() {
    const mac = document.getElementById('mac-filter').value.trim();
    const url = mac ? `/api/readings?device_mac=${encodeURIComponent(mac)}` : '/api/readings';

    try {
        const res = await fetch(url);
        if (!res.ok) return;
        const rows = await res.json();

        const tbody = document.getElementById('readings-tbody');
        tbody.innerHTML = '';
        document.getElementById('no-data').style.display = rows.length ? 'none' : 'block';
        rows.forEach(r => tbody.appendChild(buildRow(r)));
        document.getElementById('row-count').textContent = rows.length;
        resetStats(rows);
    } catch {
        showToast('Failed to load readings', 'error');
    }
}

function buildRow(data) {
    const pred = (data.prediction || '').toUpperCase();
    const temp = data.thermistor_temp != null
        ? Number(data.thermistor_temp).toFixed(1)
        : data.thermistor != null
            ? Number(data.thermistor).toFixed(1)
            : '--';
    const conf = data.confidence != null
        ? (data.confidence * 100).toFixed(1) + '%'
        : '--';
    const time = data.created_at
        ? formatTime(data.created_at)
        : formatTime(new Date().toISOString());

    const tr = document.createElement('tr');
    tr.id = `row-${data.id}`;
    tr.innerHTML = `
        <td style="color:var(--text-muted)">#${data.id}</td>
        <td class="mono-sm">${data.mac_address || '--'}</td>
        <td>${temp}</td>
        <td class="pred ${pred === 'PRESENT' ? 'present' : 'empty'}">${pred || '--'}</td>
        <td>${conf}</td>
        <td style="color:var(--text-muted);font-size:0.78rem;">${time}</td>
        <td><button class="del" title="Delete" onclick="deleteReading(${data.id})">✕</button></td>
    `;
    tr.addEventListener('click', (e) => {
        if (e.target.classList.contains('del')) return;
        if (data.pixels && data.pixels.length === 64) renderHeatmap(data.pixels);
    });
    return tr;
}

function prependRow(data) {
    const tbody = document.getElementById('readings-tbody');
    const tr = buildRow(data);
    tbody.prepend(tr);
    document.getElementById('no-data').style.display = 'none';

    const count = tbody.children.length;
    document.getElementById('row-count').textContent = count;

    // Flash new row
    tr.classList.add('flash');

    // Limit table to 100 rows to keep it manageable
    if (count > 100) tbody.removeChild(tbody.lastChild);
}

async function deleteReading(id) {
    if (!confirm('Delete this reading?')) return;
    const r = await fetch(`/api/readings/${id}`, { method: 'DELETE' });
    if (r.ok) {
        document.getElementById(`row-${id}`)?.remove();
        const tbody = document.getElementById('readings-tbody');
        const count = tbody.children.length;
        document.getElementById('row-count').textContent = count;
        if (!count) document.getElementById('no-data').style.display = 'block';
        showToast('Reading deleted', 'success');
    } else {
        showToast('Failed to delete reading', 'error');
    }
}

function clearFilter() {
    document.getElementById('mac-filter').value = '';
    loadReadings();
}

// ── Helpers ──────────────────────────────────────────────────────────
let msgTimer;
function showMessage(msg) {
    const el = document.getElementById('message');
    el.textContent = msg;
    clearTimeout(msgTimer);
    msgTimer = setTimeout(() => el.textContent = '', 3000);
}

function formatTime(isoStr) {
    const d = new Date(isoStr);
    if (isNaN(d)) return isoStr.slice(0, 19);
    const now = new Date();
    const diff = (now - d) / 1000;
    if (diff < 60)  return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// ── Toast Notifications ───────────────────────────────────────────────
function showToast(msg, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = msg;
    container.appendChild(toast);
    setTimeout(() => {
        toast.classList.add('fade-out');
        toast.addEventListener('animationend', () => toast.remove());
    }, 3000);
}

// ── Init ─────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
    const response = await fetch('/api/readings');
    if (response.ok) {
        showDashboard();
    } else {
        showAuth();
    }
});
