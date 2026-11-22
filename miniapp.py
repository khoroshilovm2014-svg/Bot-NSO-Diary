"""Мини-приложение Telegram «Получить код».

HTML отдаётся прямо из кода: отдельная папка webapp/ не нужна,
поэтому проект можно загрузить в репозиторий отдельными файлами.
Логотип — встроенный SVG, картинок нет.
"""

from __future__ import annotations

MINIAPP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<title>Код для входа — NSO Diary</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{
  --bg:var(--tg-theme-bg-color,#FFFFFF);
  --card:var(--tg-theme-secondary-bg-color,#F2F2F7);
  --text:var(--tg-theme-text-color,#1A1A1A);
  --muted:var(--tg-theme-hint-color,#8E8E93);
  --accent:var(--tg-theme-button-color,#007AFF);
  --accent-text:var(--tg-theme-button-text-color,#FFFFFF);
  --border:rgba(128,128,128,.18);
}
*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Roboto,Helvetica,Arial,sans-serif;-webkit-tap-highlight-color:transparent}
body{background:var(--bg);color:var(--text);min-height:100dvh;padding:calc(env(safe-area-inset-top) + 18px) 18px calc(env(safe-area-inset-bottom) + 18px)}
.app{max-width:420px;margin:0 auto;display:flex;flex-direction:column;gap:18px}
.brand{display:flex;align-items:center;gap:12px}
.brand svg{width:44px;height:44px;border-radius:12px;box-shadow:0 6px 16px rgba(0,122,255,.22)}
.brand b{display:block;font-size:16px;font-weight:800;letter-spacing:-.2px}
.brand span{font-size:13px;color:var(--muted)}
h1{font-size:26px;font-weight:800;letter-spacing:-.7px;line-height:1.2}
.lead{font-size:15px;color:var(--muted);line-height:1.45}
.primary{width:100%;border:none;border-radius:16px;background:var(--accent);color:var(--accent-text);font-size:17px;font-weight:700;padding:17px;cursor:pointer;transition:transform .15s,opacity .15s}
.primary:active{transform:scale(.98)}
.primary:disabled{opacity:.55;cursor:not-allowed}
.ghost{width:100%;border:1px solid var(--border);background:transparent;color:var(--text);border-radius:14px;font-size:15px;font-weight:600;padding:13px;cursor:pointer}
.code-card{background:var(--card);border:1px solid var(--border);border-radius:22px;padding:22px 18px;text-align:center;display:flex;flex-direction:column;gap:12px;animation:pop .35s ease}
@keyframes pop{from{opacity:0;transform:translateY(10px) scale(.98)}to{opacity:1;transform:none}}
.code-label{font-size:12px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:var(--muted)}
.code{font-family:"SF Mono",ui-monospace,Consolas,monospace;font-size:46px;font-weight:800;letter-spacing:9px;color:var(--accent);line-height:1.1;font-variant-numeric:tabular-nums;padding-left:9px}
.code-hint{font-size:14px;color:var(--muted);line-height:1.4}
.code-hint b{color:var(--text)}
.error{background:rgba(255,59,48,.1);color:#D70015;border-radius:16px;padding:14px 16px;font-size:14px;line-height:1.45}
.foot{font-size:12px;color:var(--muted);text-align:center;line-height:1.5}
[hidden]{display:none !important}
</style>
</head>
<body>
<div class="app">
  <header class="brand">
    <svg viewBox="0 0 180 180" aria-hidden="true">
      <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#0071FF"/><stop offset="1" stop-color="#01ADFE"/>
      </linearGradient></defs>
      <rect width="180" height="180" rx="40" fill="url(#g)"/>
      <rect x="52" y="42" width="76" height="96" rx="14" fill="#FFFFFF" opacity=".95"/>
      <rect x="66" y="62" width="48" height="8" rx="4" fill="#007AFF" opacity=".8"/>
      <rect x="66" y="80" width="34" height="8" rx="4" fill="#007AFF" opacity=".55"/>
      <rect x="66" y="98" width="42" height="8" rx="4" fill="#007AFF" opacity=".4"/>
    </svg>
    <div><b>NSO Diary</b><span>Подтверждение входа</span></div>
  </header>

  <div>
    <h1>Код для входа</h1>
    <p class="lead" id="lead">Нажмите «Получить код» — он появится здесь, и его нужно будет ввести на сайте.</p>
  </div>

  <button class="primary" id="getCode">Получить код</button>

  <section class="code-card" id="codeCard" hidden>
    <div class="code-label">Ваш код</div>
    <div class="code" id="codeValue">000000</div>
    <div class="code-hint">Введите его на сайте в течение <b id="timer">5:00</b></div>
    <button class="ghost" id="copyCode">Скопировать код</button>
  </section>

  <div class="error" id="errorBox" hidden></div>

  <p class="foot">Код действует 5 минут и срабатывает один раз.<br>Если не успели — получите новый.</p>
</div>

<script>
const tg = window.Telegram ? window.Telegram.WebApp : null;
if (tg) { tg.ready(); tg.expand(); if (tg.setHeaderColor) tg.setHeaderColor('secondary_bg_color'); }

const params = new URLSearchParams(location.search);
const startParam = tg && tg.initDataUnsafe ? (tg.initDataUnsafe.start_param || '') : '';
const requestId = params.get('req')
    || (startParam.startsWith('auth_') ? startParam.slice(5) : '')
    || sessionStorage.getItem('nso_req')
    || '';

const btn = document.getElementById('getCode');
const card = document.getElementById('codeCard');
const codeValue = document.getElementById('codeValue');
const timerEl = document.getElementById('timer');
const errorBox = document.getElementById('errorBox');
const lead = document.getElementById('lead');
const copyBtn = document.getElementById('copyCode');

let tick = null;
let currentCode = '';

function humanError(detail, fallback) {
    if (!detail) return fallback;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
        const first = detail[0];
        if (first && typeof first === 'object' && typeof first.msg === 'string') return first.msg;
        return fallback;
    }
    if (typeof detail === 'object' && typeof detail.message === 'string') return detail.message;
    return fallback;
}

function showError(message) { errorBox.textContent = message; errorBox.hidden = false; }
function clearError() { errorBox.hidden = true; }

function startCountdown(seconds) {
    let left = seconds;
    if (tick) clearInterval(tick);
    const render = () => {
        const m = Math.floor(left / 60);
        const s = left % 60;
        timerEl.textContent = m + ':' + String(s).padStart(2, '0');
    };
    render();
    tick = setInterval(() => {
        left -= 1;
        if (left <= 0) {
            clearInterval(tick);
            timerEl.textContent = '0:00';
            showError('Код истёк. Нажмите «Получить код», чтобы создать новый.');
            return;
        }
        render();
    }, 1000);
}

function showCode(code, seconds) {
    currentCode = code;
    codeValue.textContent = code;
    card.hidden = false;
    lead.textContent = 'Код готов. Вернитесь на сайт и введите его.';
    btn.textContent = 'Получить новый код';
    startCountdown(seconds);
    if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
}

async function requestCode() {
    clearError();
    if (!requestId) {
        showError('Нет запроса на вход. Вернитесь на сайт и начните вход заново.');
        return;
    }
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = 'Отправляем…';
    try {
        const response = await fetch('/api/auth/confirm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ request_id: requestId, init_data: tg ? tg.initData : '' })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            if (response.status === 422) throw new Error('Ссылка для входа повреждена. Начните заново.');
            if (response.status === 410) throw new Error('Срок действия ссылки истёк. Начните заново.');
            throw new Error(humanError(data.detail, 'Не удалось получить код'));
        }
        showCode(data.code, data.expires_in || 300);
    } catch (e) {
        btn.textContent = label;
        showError(e instanceof TypeError ? 'Нет связи с сервером. Проверьте интернет.' : (e.message || 'Ошибка'));
    } finally {
        btn.disabled = false;
    }
}

copyBtn.addEventListener('click', async () => {
    try {
        await navigator.clipboard.writeText(currentCode);
        copyBtn.textContent = 'Скопировано ✓';
        setTimeout(() => (copyBtn.textContent = 'Скопировать код'), 1600);
    } catch (e) { showError('Не удалось скопировать. Запишите код вручную.'); }
});

btn.addEventListener('click', requestCode);

if (!requestId) {
    btn.disabled = true;
    btn.textContent = 'Нет активного входа';
    showError('Приложение открыто без ссылки на вход. Вернитесь на сайт, нажмите «Открыть Telegram» — бот пришлёт кнопку.');
}
</script>
</body>
</html>
"""
