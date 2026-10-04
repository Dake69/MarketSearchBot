# Facebook Marketplace Monitor

Небольшой async-сервис, который ищет новые объявления Facebook Marketplace, хранит
состояние в SQLite и отправляет новые карточки в Telegram. Получение данных устроено
`anonymous-first`: обычный HTTP → анонимный Chromium → Chromium с профилем, в который
пользователь вошёл вручную.

> Facebook не предоставляет публичный поддерживаемый Marketplace API. URL-параметры и
> разметка могут измениться без предупреждения. Сервис делает редкие запросы, не обходит
> CAPTCHA/блокировки и при необходимости использует обычную пользовательскую сессию.

## Результат исследования (27 сентября 2026)

- **Anonymous HTTP:** в тестовой среде не работает — `facebook.com` вернул HTTP 400 и
  generic error page без ссылок `/marketplace/item/...` и без listing JSON.
- **Anonymous Playwright:** работает. Headless Chromium без cookies аккаунта получил 17
  реальных карточек для `Prague / macbook`, включая ID, title, price, location, URL и
  image. В ЕС logged-out Marketplace существует из-за DMA, однако фактическая
  доступность всё равно зависит от IP/региона, consent cookie и экспериментов Meta.
  Команда `--test-marketplace` повторяет проверку из среды, где будет работать сервис.
- **Authenticated Playwright:** в проведённом тесте не требуется; оставлен как fallback,
  если у конкретного хоста anonymous выдача закрыта или неполна.

Веб-интерфейс в настоящее время использует `sortBy=creation_time_descend`, `minPrice`,
`maxPrice`, `radius`, `daysSinceListed` и `category_id`. Это не документированный API;
поэтому URL строится только в `app/marketplace/search.py`. Location в URL надёжнее всего
задавать slug-ом или numeric location ID, который Marketplace показал после ручного
выбора города. `radius` у Facebook может быть мягким ограничением. Координаты задают
browser geolocation, но Meta всё равно может применить сохранённый Marketplace location.

Источники исследования: [решение Еврокомиссии о logged-out Marketplace](https://ec.europa.eu/competition/antitrust/cases1/202513/AT_40684_10582539_13405_4.pdf),
[пример актуальных URL-параметров](https://github.com/realbcole/marketplace-lens).

## 1. Telegram bot

1. Откройте `@BotFather` в Telegram.
2. Выполните `/newbot`, задайте имя и username.
3. Скопируйте токен в `TELEGRAM_BOT_TOKEN`.
4. Напишите новому боту любое сообщение.
5. Откройте в браузере
   `https://api.telegram.org/bot<TOKEN>/getUpdates` и скопируйте
   `result[...].message.chat.id` в `TELEGRAM_CHAT_ID`.

Для группы добавьте бота в группу, напишите сообщение и возьмите отрицательный `chat.id`
из того же `getUpdates`.

## 2. Установка

```bash
git clone <repository-url>
cd facebook-marketplace-monitor
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
playwright install chromium
```

На Linux зависимости Chromium можно установить командой:

```bash
playwright install --with-deps chromium
```

Заполните `.env`. Минимальный пример:

```env
FB_MARKETPLACE_LOCATION=Prague
FB_MARKETPLACE_RADIUS_KM=50
SEARCH_QUERY=
MIN_PRICE=5000
MAX_PRICE=50000
POLL_INTERVAL_SECONDS=300
LISTING_RETENTION_DAYS=3
SEND_EXISTING_ON_FIRST_RUN=false
TELEGRAM_BOT_TOKEN=<token-from-BotFather>
TELEGRAM_CHAT_ID=123456789
```

Если `searches.yaml` отсутствует, используется один запрос из `.env`. Для нескольких:

```bash
cp searches.example.yaml searches.yaml
```

`location` может быть городом (`Prague`), готовым slug-ом или numeric location ID из URL
Marketplace. `lat`/`lon` можно указать в YAML; они передаются Chromium как geolocation.

## 3. Проверка anonymous mode

```bash
python -m app --test-marketplace
```

Успешный результат содержит выбранный provider и несколько реальных карточек:

```text
Marketplace mode: anonymous_playwright
Listings found: 24
```

Если оба анонимных режима недоступны, команда явно предложит login. Принудительный режим
можно задать через `MARKETPLACE_MODE=anonymous_http`, `anonymous_playwright` или
`authenticated_playwright`; для production рекомендуется `auto`.

## 4. Facebook login (только если требуется)

```bash
python -m app.login
```

Откроется Chromium. Введите логин/пароль самостоятельно, дождитесь карточек Marketplace,
вернитесь в терминал и нажмите Enter. Программа не видит и не сохраняет пароль в коде;
Chromium сохраняет обычный persistent profile в `data/browser-profile`.

## 5. Запуск

```bash
python -m app --once
python -m app --dry-run --once
python -m app
```

### Настройка через Telegram

После запуска отправьте боту команду `/settings` или `/start`. Inline-меню позволяет
менять location, радиус, минимальную/максимальную цену, список поисковых запросов и
выбирать несколько категорий. По умолчанию бот проверяет все 17 категорий. Если выбраны
категории, он проверяет только их. Несколько queries и categories разворачиваются в
независимые комбинации `query × category`. Настройки сохраняются в SQLite и применяются
сразу, без перезапуска контейнера. Значения из `.env` используются только как первоначальные
defaults.

Кнопка **«Все объявления»** очищает query/category и переключает provider на
обход всех top-level категорий с
`sortBy=creation_time_descend`. Корневая Marketplace-страница для этого не используется:
она показывает `Today's picks` и игнорирует newest-sort. По умолчанию за один цикл
проверяются все 17 категорий (`ALL_CATEGORIES_PER_CYCLE=17`), после чего до следующего
цикла выдерживается интервал 5 минут (`POLL_INTERVAL_SECONDS=300`).

Для произвольной географии отправьте боту numeric location ID, canonical Marketplace slug
или полную ссылку Marketplace. Для Brno уже есть отдельная кнопка.

- `--once` — один цикл;
- `--dry-run` — вывод карточек без изменений SQLite и без Telegram;
- `--test-telegram` — тестовое сообщение;
- `--reset-baseline` — очистить историю и заново создать baseline на следующем цикле.

При `SEND_EXISTING_ON_FIRST_RUN=false` первая успешная выдача каждого именованного
поиска записывается как baseline и не отправляется. Дальше ID дедуплицируются глобально.
Неуспешная Telegram-доставка остаётся pending и повторяется в следующем цикле. База и
профиль переживают рестарты.

## 6. Docker

```bash
docker compose up -d --build
docker compose logs -f
```

`./data:/app/data` сохраняет SQLite и профили. По умолчанию Docker использует один запрос
из `.env`. Для YAML добавьте в `docker-compose.yml` volume:

```yaml
- ./searches.yaml:/app/searches.yaml:ro
```

Первичный interactive login внутри headless-контейнера неудобен. Выполните
`python -m app.login` локально с тем же каталогом `data/`, затем запустите контейнер.
Профиль Chromium не всегда переносим между ОС/версиями; самый надёжный вариант — провести
login на Linux-хосте контейнера через доступный display/VNC или запустить сервис локально.

## 7. Heroku

Проект разворачивается как container-stack с одним фоновым процессом `worker`. PostgreSQL
обязателен для постоянного хранения: файловая система dyno временная, поэтому SQLite и
browser profile исчезают при рестарте. При наличии `DATABASE_URL` приложение автоматически
использует PostgreSQL; локально без неё продолжает использовать SQLite.

Команды ниже создают платные ресурсы Heroku: worker dyno и PostgreSQL `essential-0`.
Перед запуском проверьте актуальные тарифы в панели Heroku.

```bash
heroku login
heroku create <app-name>
heroku stack:set container -a <app-name>
heroku addons:create heroku-postgresql:essential-0 -a <app-name>
heroku config:set \
  FB_MARKETPLACE_LOCATION=Brno \
  FB_MARKETPLACE_RADIUS_KM=50 \
  POLL_INTERVAL_SECONDS=300 \
  SEND_EXISTING_ON_FIRST_RUN=false \
  MARKETPLACE_MODE=auto \
  TELEGRAM_BOT_TOKEN='<token>' \
  TELEGRAM_CHAT_ID='<chat-id>' \
  -a <app-name>
heroku container:login
heroku container:push worker -a <app-name>
heroku container:release worker -a <app-name>
heroku ps:scale worker=1 -a <app-name>
heroku logs --tail -a <app-name>
```

Не добавляйте `DATABASE_URL` вручную: add-on создаёт и обновляет её сам. Схема таблиц
создаётся автоматически при первом запуске. Если каталог проекта является корнем
отдельного Git-репозитория, вместо Container Registry можно использовать `heroku.yml`:
`git push heroku HEAD:main`.

Для Heroku рекомендуется anonymous-режим. `authenticated_playwright` зависит от browser
profile на диске, а он не переживает рестарты dyno. Запускайте ровно один worker: несколько
экземпляров одновременно будут дублировать polling и Telegram long polling.

## Надёжность и границы

- Один persistent browser context переиспользуется между циклами и восстанавливается
  после crash; запросы идут последовательно с jitter.
- HTTP 429 и временные ошибки вызывают backoff. CAPTCHA и checkpoints не обходятся.
- DOM извлекается по семантическим ссылкам `/marketplace/item/<id>`; параллельно читаются
  доступные Marketplace/GraphQL JSON responses и embedded JSON.
- `listing_id` — основной ключ; fallback — SHA-256 от URL/title/price/location.
- Записи, которые не появлялись в выдаче `LISTING_RETENTION_DAYS` дней, автоматически
  удаляются из SQLite; по умолчанию срок хранения — 3 дня.
- `POLL_INTERVAL_SECONDS` не может быть меньше 60; значение по умолчанию — 300 секунд.
- Сортировка и фильтры Facebook недокументированы. Даже newest-first иногда содержит
  promoted/нерелевантные карточки, а radius не всегда является строгой границей.
- Seller/category/exact creation time часто отсутствуют в search cards и тогда остаются
  пустыми; сервис не открывает каждую карточку, чтобы не умножать запросы.

## Тесты

```bash
pytest
ruff check .
```

Тесты покрывают SQLite/dedup, baseline, fingerprint, listing ID/HTML+JSON parser,
Telegram HTML escaping и конфигурацию.
