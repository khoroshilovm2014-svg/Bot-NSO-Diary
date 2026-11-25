/* NSO Diary — настройки фронтенда.
   Единственное место, где заданы адреса. Меняйте здесь. */

(function () {
    'use strict';

    /* Адрес API на Render.
       ВАЖНО: Render меняет адрес, если пересоздать сервис.
       Актуальный адрес виден в логе деплоя: "Available at your primary URL ..." */
    var DEPLOY_API = 'https://nso-diary-api-t3yd.onrender.com';

    var BOT_USERNAME = 'nso_diary_bot';

    var host = location.hostname;
    var isLocal = host === 'localhost' || host === '127.0.0.1' || location.protocol === 'file:';

    var BASE = isLocal ? 'http://127.0.0.1:8000' : DEPLOY_API;

    /* ---------------------------------------------------------------
       Почему нужны повторы
       ---------------------------------------------------------------
       Render на бесплатном тарифе «засыпает» после 15 минут без запросов.
       Первый запрос после сна висит 30–90 секунд, пока сервис поднимается.
       Без повторов браузер обрывает соединение и показывает
       «нет связи с сервером» — хотя сервер просто просыпается.

       Поэтому: несколько попыток с паузой и общий лимит ожидания.
       --------------------------------------------------------------- */

    var WAKE_TOTAL_MS = 150000;   /* сколько всего ждём пробуждения */
    var ATTEMPT_TIMEOUT = 25000;  /* сколько ждём один запрос */
    var RETRY_PAUSE = 2500;       /* пауза между попытками */

    var activeBase = BASE;
    var probed = false;
    var waking = false;

    /* Колбэк для интерфейса: сообщает «сервер просыпается». */
    var wakeListener = null;

    function notifyWaking(seconds) {
        if (typeof wakeListener === 'function') {
            try {
                wakeListener(seconds);
            } catch (e) { /* не критично */ }
        }
    }

    function sleep(ms) {
        return new Promise(function (resolve) { setTimeout(resolve, ms); });
    }

    /* fetch с таймаутом: без него зависший запрос висит бесконечно. */
    function fetchWithTimeout(url, options, timeoutMs) {
        var controller = new AbortController();
        var timer = setTimeout(function () { controller.abort(); }, timeoutMs);

        var opts = Object.assign({}, options || {}, { signal: controller.signal });

        return fetch(url, opts).then(function (response) {
            clearTimeout(timer);
            return response;
        }).catch(function (error) {
            clearTimeout(timer);
            throw error;
        });
    }

    /* Ждёт, пока сервер проснётся и ответит на /api/health. */
    async function wake() {
        if (probed) return true;

        var started = Date.now();
        var attempt = 0;

        while (Date.now() - started < WAKE_TOTAL_MS) {
            attempt += 1;

            try {
                var response = await fetchWithTimeout(
                    activeBase + '/api/health',
                    { method: 'GET', cache: 'no-store' },
                    ATTEMPT_TIMEOUT
                );

                if (response.ok) {
                    probed = true;
                    if (waking) {
                        waking = false;
                        notifyWaking(0);
                    }
                    return true;
                }
            } catch (e) {
                /* сервер ещё не поднялся — ждём дальше */
            }

            if (!waking) {
                waking = true;
            }
            notifyWaking(Math.round((Date.now() - started) / 1000));

            await sleep(RETRY_PAUSE);
        }

        /* Не дождались — пусть интерфейс покажет ошибку. */
        return false;
    }

    /* Совместимость со старым кодом. */
    async function pickBase() {
        await wake();
        return activeBase;
    }

    /* fetch к API: сам ждёт пробуждения сервера и повторяет при обрыве. */
    async function apiFetch(path, options) {
        await wake();

        var url = path.indexOf('http') === 0 ? path : activeBase + path;
        var started = Date.now();
        var lastError = null;

        while (Date.now() - started < WAKE_TOTAL_MS) {
            try {
                return await fetchWithTimeout(url, options, ATTEMPT_TIMEOUT);
            } catch (error) {
                lastError = error;

                /* Обрыв сети во время пробуждения — повторяем. */
                notifyWaking(Math.round((Date.now() - started) / 1000));
                await sleep(RETRY_PAUSE);
            }
        }

        throw lastError || new TypeError('Сервер не отвечает');
    }

    /* Проверка состояния сервера без ожидания: для индикатора. */
    function ping() {
        return fetchWithTimeout(
            activeBase + '/api/health',
            { method: 'GET', cache: 'no-store' },
            6000
        ).then(function (response) {
            return response.ok;
        }).catch(function () {
            return false;
        });
    }

    window.NSO = {
        API_BASE: activeBase,
        BOT_USERNAME: BOT_USERNAME,
        BOT_URL: 'https://t.me/' + BOT_USERNAME,
        TOKEN_KEY: 'nso_token',
        REQ_KEY: 'nso_req',
        pickBase: pickBase,
        apiFetch: apiFetch,
        wake: wake,
        ping: ping,
        onWaking: function (callback) { wakeListener = callback; },
        isLocal: isLocal
    };
})();
