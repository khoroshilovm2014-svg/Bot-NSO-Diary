"""Мини-приложение Telegram «Получить код».

HTML отдаётся прямо из кода, логотип встроен как base64 — папки и картинки
не нужны, проект остаётся плоским для загрузки в GitHub по одному файлу.

Плейсхолдер __SITE__ заменяется на адрес сайта при отдаче страницы.
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
  --bg:#F5F7FA;
  --card:#FFFFFF;
  --text:#1A1A1A;
  --muted:#8E8E93;
  --accent:#007AFF;
  --accent-light:#F0F4FF;
  --border:#E5E5EA;
  --danger:#D70015;
}
*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Roboto,Helvetica,Arial,sans-serif;-webkit-tap-highlight-color:transparent}
body{background:var(--bg);color:var(--text);min-height:100dvh;padding:calc(env(safe-area-inset-top) + 16px) 18px calc(env(safe-area-inset-bottom) + 20px)}
.app{max-width:420px;margin:0 auto;display:flex;flex-direction:column;gap:16px}
.brand{display:flex;align-items:center;gap:12px;padding:4px 2px}
.brand img{width:44px;height:44px;border-radius:12px;object-fit:cover;box-shadow:0 6px 16px rgba(0,122,255,.22)}
.brand b{display:block;font-size:16px;font-weight:800;letter-spacing:-.2px}
.brand span{font-size:13px;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--border);border-radius:26px;padding:22px 18px;box-shadow:0 10px 30px rgba(16,24,40,.04);display:flex;flex-direction:column;gap:14px}
h1{font-size:24px;font-weight:800;letter-spacing:-.6px;line-height:1.2}
.lead{font-size:14.5px;color:var(--muted);line-height:1.5}
.primary{width:100%;border:none;border-radius:18px;background:var(--accent);color:#fff;font-size:17px;font-weight:700;padding:16px;cursor:pointer;transition:transform .15s,opacity .15s;box-shadow:0 10px 24px rgba(0,122,255,.28)}
.primary:active{transform:scale(.98)}
.primary:disabled{opacity:.55;cursor:not-allowed;box-shadow:none}
.ghost{width:100%;border:1.5px solid var(--border);background:#fff;color:var(--text);border-radius:16px;font-size:15px;font-weight:600;padding:13px;cursor:pointer}
.ghost:active{opacity:.7}
.code-box{background:var(--accent-light);border:2px solid var(--accent);border-radius:20px;padding:18px 14px;text-align:center;animation:pop .35s ease}
@keyframes pop{from{opacity:0;transform:translateY(10px) scale(.98)}to{opacity:1;transform:none}}
.code-label{font-size:12px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:var(--accent)}
.code{font-family:"SF Mono",ui-monospace,"Cascadia Mono",Consolas,monospace;font-size:44px;font-weight:800;letter-spacing:8px;color:var(--accent);line-height:1.15;font-variant-numeric:tabular-nums;padding-left:8px;margin-top:4px}
.code-hint{font-size:14px;color:var(--muted);line-height:1.4;margin-top:6px}
.code-hint b{color:var(--text)}
.timer{text-align:center;font-size:13px;color:var(--muted)}
.timer b{color:var(--text);font-variant-numeric:tabular-nums}
.error{background:#FFF1F0;color:var(--danger);border-radius:16px;padding:13px 15px;font-size:14px;line-height:1.45}
.foot{font-size:12px;color:var(--muted);text-align:center;line-height:1.55;margin-top:4px}
[hidden]{display:none !important}
</style>
</head>
<body>
<div class="app">

  <header class="brand">
    <img src="__ICON__" alt="NSO Diary">
    <div><b>NSO Diary</b><span>Подтверждение входа</span></div>
  </header>

  <div class="card">
    <div>
      <h1>Код для входа</h1>
      <p class="lead" id="lead" style="margin-top:6px">Нажмите «Получить код» — он появится здесь, и его нужно будет ввести на сайте.</p>
    </div>

    <button class="primary" id="getCode">Получить код</button>

    <section id="codeCard" hidden>
      <div class="code-box">
        <div class="code-label">Ваш код</div>
        <div class="code" id="codeValue">000000</div>
        <div class="code-hint">Введите его на сайте в течение <b id="timer">5:00</b></div>
      </div>
      <div style="height:12px"></div>
      <button class="ghost" id="copyCode">Скопировать код</button>
    </section>

    <div class="timer" hidden id="timerRow">Код действует ещё <b id="timer2">5:00</b></div>

    <div class="error" id="errorBox" hidden></div>

    <section id="subCard" hidden>
      <a class="primary" id="subLink" href="https://t.me/nso_diary"
         target="_blank" rel="noopener"
         style="display:block;text-align:center;text-decoration:none">
        📣 Подписаться на канал
      </a>
      <div style="height:10px"></div>
      <button class="ghost" id="subCheck">Я подписался</button>
    </section>
  </div>

  <p class="foot">Код действует 5 минут и срабатывает один раз.<br>Если не успели — получите новый.</p>

</div>

<script>
const tg = window.Telegram ? window.Telegram.WebApp : null;
if (tg) { tg.ready(); tg.expand(); }

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
const timerRow = document.getElementById('timerRow');
const timer2 = document.getElementById('timer2');
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

const subCard = document.getElementById('subCard');
const subLink = document.getElementById('subLink');
const subCheck = document.getElementById('subCheck');

function showSubscribe(url) {
    if (!subCard) return;
    if (url) subLink.href = url;
    subCard.hidden = false;
}

function hideSubscribe() {
    if (subCard) subCard.hidden = true;
}
function clearError() { errorBox.hidden = true; }

function fmt(left) {
    const m = Math.floor(left / 60);
    const s = left % 60;
    return m + ':' + String(s).padStart(2, '0');
}

function startCountdown(seconds) {
    let left = seconds;
    if (tick) clearInterval(tick);
    const render = () => { timerEl.textContent = fmt(left); timer2.textContent = fmt(left); };
    timerRow.hidden = false;
    render();
    tick = setInterval(() => {
        left -= 1;
        if (left <= 0) {
            clearInterval(tick);
            timerEl.textContent = '0:00';
            timer2.textContent = '0:00';
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

            /* Нет подписки на канал — показываем кнопку подписки. */
            if (response.status === 403 && data.detail
                && data.detail.code === 'not_subscribed') {
                btn.textContent = label;
                btn.disabled = false;
                showError(data.detail.message || 'Сначала подпишитесь на канал.');
                showSubscribe(data.detail.subscribe_url);
                return;
            }

            throw new Error(humanError(data.detail, 'Не удалось получить код'));
        }
        hideSubscribe();
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

if (subCheck) {
    subCheck.addEventListener('click', () => {
        hideSubscribe();
        requestCode();
    });
}

if (!requestId) {
    btn.disabled = true;
    btn.textContent = 'Нужна ссылка с сайта';
    lead.textContent = 'Код выдаётся только после входа на сайте — так дневник защищён.';
    showError('Это окно открыто без данных входа. Откройте сайт дневника, введите логин и пароль, затем нажмите «Открыть Telegram» — бот пришлёт кнопку с кодом.');

    const siteBtn = document.createElement('a');
    siteBtn.href = '__SITE__';
    siteBtn.textContent = 'Открыть сайт дневника';
    siteBtn.style.cssText = 'display:block;text-align:center;background:#007AFF;color:#fff;'
        + 'text-decoration:none;border-radius:18px;padding:16px;font-size:17px;font-weight:700;'
        + 'box-shadow:0 10px 24px rgba(0,122,255,.28)';
    btn.parentNode.insertBefore(siteBtn, btn.nextSibling);
}
</script>
</body>
</html>
"""

# Логотип приложения (180x180 PNG) как data-URI — без отдельных файлов.
ICON_DATA_URI = "data:image/png;base64,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"


def render(site_origin: str) -> str:
    """Готовая страница мини-приложения с подставленным адресом сайта."""
    html = MINIAPP_HTML.replace("__SITE__", site_origin or "https://diary-nso.vercel.app")
    return html.replace("__ICON__", ICON_DATA_URI)
