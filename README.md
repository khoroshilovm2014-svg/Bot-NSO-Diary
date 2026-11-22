---
title: NSO Diary API
emoji: 📓
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# NSO Diary API — плоская версия

Все файлы лежат в **одной папке**, без подкаталогов. Это сделано специально:
GitHub через веб-интерфейс не принимает папки, только отдельные файлы.
Теперь достаточно загрузить файлы по одному — и ничего не потеряется.

## Файлы

| Файл | Зачем |
|---|---|
| `main.py` | точка входа, читает настройки и запускает сервер |
| `server.py` | приложение FastAPI: API, webhook бота, мини-приложение |
| `api.py` | эндпоинты `/api/auth/*`, `/api/me`, `/api/marks` |
| `bot.py` | Telegram-бот на aiogram |
| `config.py` | чтение переменных окружения |
| `security.py` | проверка `initData`, JWT, коды, пароли |
| `db.py` | Supabase и хранилище в памяти |
| `miniapp.py` | HTML мини-приложения «Получить код» (внутри кода, без папки) |
| `requirements.txt` | зависимости |
| `render.yaml` | конфиг для Render |
| `Dockerfile` | для Hugging Face Spaces |

## Деплой на Render

1. Загрузить все файлы из этой папки в корень репозитория GitHub
   (Add file → Upload files → выбрать **все файлы сразу**).
2. Render → **New → Blueprint** → выбрать репозиторий → **Apply**.
3. В сервисе → **Environment** заполнить секреты:

```
BOT_TOKEN            = 8865586574:...
WEBHOOK_SECRET       = 17JaIx7MJAE_31uxJOK3h65UeSI_FRiU
JWT_SECRET           = wZgE5XCmIq07jhTJsPOM1fp4SYpZGUGhQCVb9tvi9HUaxyRVZt-yNpbIfJZgDH9I
SUPABASE_SERVICE_KEY = sb_secret_vzMjR1xpYWymgcLiqe92Xw_OExGdMRu
```

`SUPABASE_URL` и `BOT_USERNAME` уже прописаны в `render.yaml`.

4. После первого деплоя скопировать адрес сервиса
   (`https://имя.onrender.com`) и добавить переменную:

```
PUBLIC_BASE = https://имя.onrender.com
```

5. Проверить: `https://имя.onrender.com/api/health`

```json
{"ok": true, "db": "supabase", "bot": true, "demo": false}
```

6. В логах появится `[bot] webhook установлен: ...` — бот готов.
7. Открыть `t.me/nso_diary_bot` → Start → «Получить код».

## Деплой на Hugging Face Spaces

1. New → Space → SDK: **Docker** → Blank.
2. Files → Add file → Upload files → загрузить все файлы.
3. Settings → Variables and secrets → добавить секреты (те же, что выше).
4. Дождаться сборки, взять адрес `https://<логин>-<имя>.hf.space`
   и добавить его как `PUBLIC_BASE`, затем Restart.

## Локальный запуск

```bash
pip install -r requirements.txt
python main.py --demo
```

Открыть http://127.0.0.1:8000/docs — Swagger.
Ученик в демо-режиме: `demo` / `demo`.

## Что проверить при ошибках

| Симптом в логе | Причина |
|---|---|
| `ModuleNotFoundError: No module named 'app'` | загружена старая версия с папкой `app/` — нужны файлы из этой папки |
| `Could not open requirements file` | `requirements.txt` не в корне репозитория |
| `db: memory` в `/api/health` | не заданы `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` |
| `bot: false` | не задан `BOT_TOKEN` |
| `PUBLIC_BASE не начинается с https://` | не задан адрес сервиса |
