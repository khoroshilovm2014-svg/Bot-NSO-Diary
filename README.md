---
title: NSO Diary API
emoji: 📓
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# NSO Diary — API авторизации и Telegram-бот

Отдельный сервис: FastAPI + aiogram + Supabase. Сайт (`../index.html`, `../login.html`,
`../auth.html`) — статика, этот сервис отвечает за вход.

---

## Как устроен вход

```
Сайт                      API                        Telegram
────                      ───                        ────────
логин + пароль  ───────▶  проверка students
                          создаёт auth_requests
             ◀──────────  request_id + deeplink
                          (10 минут)

«Открыть Telegram» ─────────────────────────────▶  /start auth_<id>
                                                   кнопка «Получить код»
                                                   ↓ мини-приложение
                          POST /api/auth/confirm ◀─ initData + request_id
             ◀──────────  6-значный код (5 минут)
                                                   
ввод кода на сайте ─────▶  POST /api/auth/verify
             ◀──────────  JWT-сессия (12 часов) ──▶ marks.html
```

Два независимых подтверждения:

1. **что-то, что знает ученик** — пароль или Госуслуги;
2. **что-то, что есть только у него** — Telegram-аккаунт.

Даже если пароль утёк, без доступа к его Telegram войти нельзя. И наоборот:
код из бота бесполезен без первичного логина.

---

## Структура

```
bot/
├── run.py                  запуск: python run.py
├── requirements.txt
├── .env.example            шаблон настроек
├── app/
│   ├── config.py           чтение .env и типов
│   ├── security.py         initData, JWT, коды, пароли (только stdlib)
│   ├── db.py               Supabase (PostgREST) и in-memory хранилище
│   ├── api.py              эндпоинты /api/*
│   ├── bot.py              хендлеры aiogram
│   └── main.py             FastAPI: CORS, webhook, статика мини-аппа
├── webapp/
│   ├── index.html          мини-приложение «Получить код»
│   └── icon.png
├── sql/
│   ├── 01_schema.sql       таблицы, индексы, RLS
│   └── 02_seed.sql         демо-ученик, оценки, расписание
├── scripts/
│   └── hash_password.py    генерация хеша пароля
└── tests/
    └── test_core.py        12 тестов: crypto + весь сценарий входа
```

---

## Быстрый старт (локально, без Supabase и без бота)

```bash
cd bot
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python run.py --demo
```

`--demo` игнорирует `.env` (база в памяти, бот выключен) и создаёт ученика demo/demo —
удобно, пока не применена SQL-схема и нет VPN для Telegram.

Открыть:

- `http://127.0.0.1:8000/docs` — Swagger;
- `http://127.0.0.1:8000/tg-app/?req=<uuid>` — мини-приложение (без Telegram покажет ошибку — это нормально);
- сайт — `login.html` (логин **demo**, пароль **demo**).

В демо-режиме `init_data` не требуется, поэтому полный цикл можно пройти
прямо из Swagger: `login` → `confirm` → `verify` → `me`.

---

## Подключение Supabase

1. **Ключ.** Supabase → Project Settings → API → `service_role` (secret).
   Он даёт полный доступ к базе, поэтому живёт только на сервере.
   В браузер его класть нельзя ни при каких условиях.

2. **Схема.** SQL Editor → New query → вставить `sql/01_schema.sql` → Run.
   Затем `sql/02_seed.sql`.

   > В схеме есть `DROP TABLE` — старые `marks`, `schedule`, `auth_codes`
   > созданы вручную и не совпадают по типам. Сейчас они пустые (0 строк),
   > так что данные не теряются. Если данные появились — сначала бэкап,
   > потом закомментируйте блок `DROP`.

3. **Настройки.** В `.env`:

   ```
   SUPABASE_URL=https://surbkjmviyyvvrajhjkp.supabase.co
   SUPABASE_SERVICE_KEY=<service_role ключ>
   DEMO_MODE=0
   ```

4. **Пароли учеников.** Хеш считается скриптом:

   ```bash
   python scripts/hash_password.py "пароль"
   # pbkdf2_sha256$200000$...$...
   ```

   Вставить в `students.password_hash`. Пустое поле = вход только через Госуслуги.

5. **Проверка:** `GET /api/health` покажет `"db": "supabase"`.

---

## Telegram

1. `@BotFather` → `/newbot` → получить токен → `BOT_TOKEN`.
2. Имя бота → `BOT_USERNAME` (без `@`), из него строится `t.me/<username>?start=auth_…`.
3. **HTTPS обязателен** — мини-приложения и webhook работают только по нему.
   Домен вида `api.nso-diary.ru` + nginx + certbot.
4. `PUBLIC_BASE=https://api.nso-diary.ru`, `WEBHOOK_SECRET=<случайная строка>`.
5. При старте сервис сам вызывает `setWebhook` (если `PUBLIC_BASE` начинается с `https://`)
   и ставит кнопку меню «Дневник» — она открывает мини-приложение.
6. Кнопка «Получить код» внутри бота ведёт на `WEBAPP_URL?req=<id>`.

Проверить, что webhook встал:

```bash
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

---

## Куда деплоить (и куда нельзя)

Полный разбор с пошаговыми инструкциями — в отдельном файле
**[DEPLOY.md](DEPLOY.md)**. Коротко:

| Хостинг | HTTPS | Годится? |
|---|---|---|
| **Render** (onrender.com) | выдаётся сам | **да**, рекомендую |
| **Hugging Face Spaces** (hf.space) | выдаётся сам | **да** |
| NomadHost / Pterodactyl + cloudflared | только туннель | **нет** — блокируют аккаунт |
| PythonAnywhere (free) | есть | **нет** — исходящие только по белому списку, Telegram недоступен |
| VPS + домен | настраивается | да, если есть сервер |

Готовые файлы в проекте: Dockerfile (для HF Spaces и любого Docker),

ender.yaml (для Render), .env.example (шаблон настроек).

> **Важно:** .env с токеном и ключами **не загружайте** в репозиторий.
> На Render и HF Spaces секреты вводятся в панели, а не в файле.

---

## Деплой на VPS

`/etc/systemd/system/nso-diary.service`:

```ini
[Unit]
Description=NSO Diary API
After=network.target

[Service]
User=www-data
WorkingDirectory=/srv/nso-diary/bot
EnvironmentFile=/srv/nso-diary/bot/.env
ExecStart=/srv/nso-diary/bot/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

`nginx`:

```nginx
server {
    listen 443 ssl http2;
    server_name api.nso-diary.ru;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
systemctl daemon-reload && systemctl enable --now nso-diary
```

---

## Связка с сайтом

В корне проекта лежит `config.js`:

```js
window.NSO = {
    API_BASE: 'https://api.nso-diary.ru',
    BOT_URL: 'https://t.me/NSODiaryBot',
    ...
};
```

Локально `API_BASE` автоматически становится `http://127.0.0.1:8000`.

Не забудьте добавить домен сайта в `CORS_ORIGINS` — иначе браузер заблокирует запросы.

---

## Безопасность

| Что | Как защищено |
|---|---|
| Пароли | PBKDF2-SHA256, 200 000 итераций, соль на пользователя |
| `initData` | HMAC-SHA256 с ключом из `BOT_TOKEN`, проверка `auth_date` (сутки) |
| Код | 6 цифр, живёт 5 минут, одноразовый, лимит попыток 6 |
| Сессия | JWT HS256, 12 часов, подпись `JWT_SECRET` |
| База | RLS включён, политик нет → anon-ключ не читает ничего |
| Ключи | `service_role` только в `.env` сервера |

Чего пока нет и что стоит добавить перед реальным запуском:

- rate limit на `/api/auth/login` (сейчас пароль можно перебирать);
- лог неудачных входов и уведомление ученику в бота;
- нормальная интеграция ЕСИА вместо заглушки `esia_id`.

---

## Что делать с базой данных

**Ключи.** В Supabase два ключа: `anon` (публичный) и `service_role` (секретный).
У нас `anon` не используется вообще — весь доступ идёт через API. Поэтому RLS
включён со «злой» настройкой: политик нет, значит для `anon` и `authenticated`
все таблицы недоступны, а `service_role` обходит RLS по определению.

**Таблицы и связи.**

```
students ──1:N──▶ marks
    │
    └────1:N──▶ auth_requests ──1:N──▶ auth_codes
schedule (независимая, по class_name)
```

**Импорт учеников.** Проще всего через CSV: Supabase → Table Editor → `students`
→ Import data from CSV. Колонки: `login, full_name, class_name, password_hash`.
Либо SQL:

```sql
insert into public.students (login, full_name, class_name, password_hash)
values ('ivanov', 'Иванов Иван', '9 «А»', 'pbkdf2_sha256$...');
```

**Оценки и расписание.** Пока их неоткуда брать автоматически: нужен либо
доступ к API НСО, либо выгрузка. Когда появится источник — писать в `marks`
и `schedule` тем же `service_role` ключом из отдельного скрипта-синхронизатора
(не из браузера). Поля в `marks` оставлены текстом, потому что даты приходят
как `12.11.2026`.

**Гигиена.**

- индексы уже созданы (`marks(student_id, mark_date)`, `schedule(class_name, day_of_week, lesson)`);
- чистить старые записи: `delete from auth_requests where created_at < now() - interval '30 days';` (можно повесить на cron);
- бэкапы у Supabase ежедневные на платном плане, на бесплатном — делать `pg_dump` самому;
- следить за лимитом размера: бесплатный проект ≈ 500 МБ.

**Быстрая проверка после настройки:**

```sql
select count(*) from students;
select count(*) from marks;
select * from auth_requests order by created_at desc limit 5;
```

---

## Тесты

```bash
cd bot
python tests/test_core.py
```

12 проверок: пароли, JWT (включая подделку и истечение), коды, подпись `initData`
(валидная, подделанная, просроченная) и полный сценарий входа через API.

---

## Ограничения

- Госуслуги — заготовка: эндпоинт ищет `students.esia_id`. Для настоящего входа
  нужен client_id/secret ЕСИА и редирект с кодом.
- Пароль НСО проверяется по таблице `students`, а не на серверах НСО.
- Нет синхронизации оценок — только хранение.
