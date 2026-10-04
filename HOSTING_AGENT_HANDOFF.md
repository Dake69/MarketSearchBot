# MarketSearchBot: контекст и задание для агента на хостинге

Дата подготовки: 2026-10-04  
Репозиторий: https://github.com/Dake69/MarketSearchBot  
Основная ветка: `main`  
Первый опубликованный коммит: `c411545` (`Initial commit`)

## Цель

Добиться стабильной работы MarketSearchBot на Linux-хостинге в Docker:

- контейнер постоянно работает и восстанавливается после перезагрузки хоста;
- DNS и исходящий HTTPS из контейнера стабильны;
- Facebook Marketplace возвращает объявления, а не HTTP 400, пустую выдачу или редирект на login;
- при необходимости используется сохранённая авторизованная Facebook-сессия;
- Telegram polling и отправка сообщений работают без DNS-ошибок;
- SQLite, настройки и browser profile сохраняются между пересозданиями контейнера;
- секреты не выводятся в консоль, логи, коммиты и ответы пользователю.

## Что уже сделано

- Приложение переведено на Docker Compose.
- Основной сервис называется `monitor`.
- Образ собирается из локального `Dockerfile`.
- Команда контейнера: `python -m app`.
- В Compose настроено `restart: unless-stopped`.
- Каталог `./data` примонтирован в `/app/data` с правом записи.
- `shm_size` установлен в `1gb` для Chromium.
- Переменные загружаются из `.env`; сам `.env` исключён из Git.
- SQLite и настройки хранятся в `data/`.
- Старая systemd-служба на хостинге, согласно предыдущей проверке, остановлена и отключена.
- Репозиторий инициализирован отдельно от родительского каталога и отправлен на GitHub.
- Локально прошли все тесты: `20 passed`.

Текущий `docker-compose.yml`:

```yaml
services:
  monitor:
    build: .
    restart: unless-stopped
    env_file:
      - path: .env
        required: false
    volumes:
      - ./data:/app/data
    shm_size: "1gb"
```

## Архитектура Marketplace fallback

При `MARKETPLACE_MODE=auto` приложение пробует провайдеры по очереди:

1. `anonymous_http` — обычный HTTP через `httpx`;
2. `anonymous_playwright` — headless Chromium с отдельным persistent profile;
3. `authenticated_playwright` — Chromium с авторизованным persistent profile, **но только если** в `BROWSER_PROFILE_PATH` уже существуют файлы.

Основные файлы:

- `app/marketplace/anonymous_http.py` — HTTP provider;
- `app/marketplace/playwright_client.py` — Chromium provider, login/checkpoint detection;
- `app/marketplace/auto.py` — выбор и фиксация provider;
- `app/marketplace/search.py` — сборка Marketplace URL;
- `app/worker.py` — циклы поиска и retry/backoff;
- `app/telegram.py`, `app/bot.py` — Telegram API и settings bot;
- `app/config.py` — загрузка конфигурации;
- `app/database.py`, `app/preferences.py` — SQLite и настройки.

Пути профилей:

- авторизованный: `data/browser-profile`;
- анонимный: `data/browser-profile-anonymous`.

Важно: наличие только `browser-profile-anonymous` не включает authenticated fallback. Метод `_profile_has_session()` проверяет именно `data/browser-profile` или путь из `BROWSER_PROFILE_PATH`.

## Известная конфигурация без секретов

В проверенной локальной конфигурации были значения:

```env
MARKETPLACE_MODE=auto
HEADLESS=true
BROWSER_LOCALE=en-US
HTTP_TIMEOUT_SECONDS=30
PAGE_TIMEOUT_SECONDS=45000
BROWSER_PROFILE_PATH=data/browser-profile
```

На момент проверки:

- `data/browser-profile` содержал `0` файлов;
- `data/browser-profile-anonymous` существовал и содержал Chromium profile;
- следовательно, приложение не имело рабочей авторизованной Facebook-сессии.

На хостинге нужно проверить фактические значения и каталоги заново, не печатая токены, cookies или содержимое базы.

## Подтверждённые симптомы

### 1. Anonymous HTTP получает HTTP 400

Пример:

```text
Marketplace provider unavailable: Facebook returned HTTP 400
provider=anonymous_http
```

Это не обязательно проблема Docker. Facebook Marketplace не предоставляет стабильный публичный API. Простой HTTP GET может отвергаться из-за отсутствия полноценного browser state, consent cookies, JavaScript-навигации или решения антибот-системы.

### 2. Anonymous Playwright иногда работает

В имеющихся логах были успешные циклы:

```text
Search completed: 24 listings, 2 new, 2 sent
provider=anonymous_playwright

Search completed: 24 listings, 24 new, 24 sent
provider=anonymous_playwright

Search completed: 24 listings, 14 new, 14 sent
provider=anonymous_playwright
```

Это подтверждает, что URL builder, Chromium, DOM parser, SQLite и Telegram pipeline в принципе способны работать.

### 3. Anonymous Playwright иногда получает пустую выдачу или login gate

Встречалось:

```text
Marketplace returned an empty result set
Search completed: 0 listings, 0 new, 0 sent
```

На хостинге также наблюдался редирект Chromium на Facebook login. Возможные причины:

- anonymous Marketplace недоступен для конкретного IP/региона;
- IP относится к дата-центру/VPS и получает более строгую проверку;
- новый или очищенный browser profile;
- география IP, Facebook location и `BROWSER_LOCALE` не согласованы;
- изменившееся поведение Meta, A/B-тест или risk scoring;
- rate limiting, temporary block, checkpoint или CAPTCHA;
- Meta больше не обязана гарантировать прежний logged-out Marketplace: 23 апреля 2025 года Marketplace был снят с соответствующего статуса gatekeeper по DMA.

Нельзя обходить CAPTCHA/checkpoint. При их появлении нужно остановиться, зафиксировать факт и использовать обычную ручную авторизацию или сменить разрешённый способ доступа.

### 4. Были отдельные массовые DNS-сбои внутри контейнера

Зафиксированы ошибки одновременно для Facebook и Telegram:

```text
Page.goto: net::ERR_NAME_NOT_RESOLVED at https://www.facebook.com/...
TelegramError: [Errno -2] Name or service not known
```

Это важный независимый дефект. Если одновременно не разрешаются `www.facebook.com` и `api.telegram.org`, причина почти наверняка в DNS/сети Docker или хоста, а не в Facebook login.

Возможные источники:

- некорректный `/etc/resolv.conf` на хосте;
- `systemd-resolved`/`dnsmasq` с loopback resolver, недоступным контейнеру;
- нестабильный DNS провайдера;
- VPN, firewall, proxy или IPv4/IPv6 mismatch;
- временный сетевой сбой Docker bridge/daemon.

Официальная документация Docker:

- https://docs.docker.com/engine/daemon/troubleshoot/#dns-resolver-issues
- https://docs.docker.com/engine/network/#dns-services

## Важное различие между локальной машиной и хостингом

В локальной среде на момент последней проверки контейнер имел состояние:

```text
Exited (0)
```

Это не противоречит предыдущему сообщению, что контейнер на хостинге работал без рестартов: речь может идти о разных Docker hosts или разных моментах времени. Агент на хостинге должен доверять только фактическим данным с целевого сервера.

Политика `unless-stopped` не поднимает контейнер, который был явно остановлен оператором, пока его снова не запустят. Также нужно проверить, что сам Docker daemon включён в автозагрузку.

## Приоритетный план диагностики на хостинге

### Шаг 1. Зафиксировать исходное состояние без изменений

Выполнить из каталога проекта:

```bash
docker compose ps -a
docker compose logs --timestamps --tail=300 monitor
docker inspect marketsearchbot-monitor-1 \
  --format '{{json .State}} {{json .HostConfig.RestartPolicy}} {{json .Mounts}}'
systemctl is-enabled docker
systemctl is-active docker
```

Не включать в отчёт содержимое `.env`, cookies, Telegram token, chat ID или SQLite rows с пользовательскими данными.

Проверить, нет ли параллельно запущенной старой копии через systemd, cron, supervisor или отдельный Docker container. Два экземпляра Telegram polling могут конфликтовать.

### Шаг 2. Отделить DNS/сеть от поведения Facebook

Проверить DNS несколько раз, а не один раз:

```bash
docker compose exec monitor getent hosts www.facebook.com
docker compose exec monitor getent hosts api.telegram.org
docker compose exec monitor python - <<'PY'
import socket
for host in ("www.facebook.com", "api.telegram.org"):
    print(host, socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
PY
```

Также проверить:

```bash
cat /etc/resolv.conf
docker compose exec monitor cat /etc/resolv.conf
docker network inspect marketsearchbot_default
```

Если DNS внутри контейнера нестабилен, сначала исправить его. Не считать Marketplace диагностированным, пока контейнер стабильно разрешает оба домена.

Допустимое решение — задать надёжные DNS-серверы на уровне Docker daemon или Compose, но выбирать их с учётом инфраструктуры и политики хостинга. После изменения daemon config проверить валидность JSON, перезапустить Docker и повторить тесты. Не делать это вслепую, если хост использует внутренний DNS.

### Шаг 3. Проверить Marketplace из самого контейнера

После стабилизации DNS:

```bash
docker compose run --rm monitor python -m app --test-marketplace
```

Зафиксировать только:

- выбранный provider;
- HTTP status;
- финальный URL;
- количество объявлений;
- факт login/checkpoint/CAPTCHA;
- время ответа.

Не печатать cookies и request headers с авторизацией.

Если текущего логирования недостаточно, можно добавить безопасную диагностику: начальный URL, финальный URL, HTTP status, заголовок страницы и классификацию gate. Не логировать HTML целиком, cookies, localStorage или токены.

### Шаг 4. Проверить постоянный browser profile

Проверить только структуру и количество файлов:

```bash
find data/browser-profile -type f 2>/dev/null | wc -l
find data/browser-profile-anonymous -type f 2>/dev/null | wc -l
docker compose run --rm monitor sh -lc \
  'find /app/data/browser-profile -type f 2>/dev/null | wc -l'
```

Если авторизованный профиль пуст, `auto` никогда не попробует `authenticated_playwright`.

Надёжнее создать Facebook-сессию непосредственно на целевом Linux-хосте с тем же каталогом `data/` и совместимой версией Chromium. Chromium profile, перенесённый между macOS и Linux или между сильно разными версиями Chromium, может не работать.

Для интерактивного входа потребуется разрешённый display/VNC/X forwarding или другой безопасный ручной способ. Пользователь должен вводить логин, пароль и 2FA самостоятельно. Агент не должен запрашивать, читать или сохранять пароль.

После ручной авторизации проверить, что:

- файлы появились в `data/browser-profile` на хосте;
- они видны в `/app/data/browser-profile` внутри контейнера;
- владелец и permissions позволяют пользователю контейнера читать и писать профиль;
- профиль сохраняется после `docker compose up -d --build`;
- `MARKETPLACE_MODE=authenticated_playwright` проходит тест или `auto` действительно выбирает этот provider после отказа anonymous providers.

### Шаг 5. Проверить IP и географию

Сравнить исходящий публичный IP хоста и контейнера, не публикуя его в открытом отчёте. Обычно Docker использует IP хоста, но VPS migration меняет IP/ASN.

Проверить согласованность:

- страна IP;
- `FB_MARKETPLACE_LOCATION` или numeric location ID;
- `BROWSER_LOCALE`;
- timezone/geolocation browser context, если используются координаты.

Не использовать обходные техники, residential proxy без разрешения, fingerprint spoofing или CAPTCHA-solving. Если Meta ограничивает дата-центровый IP, сообщить об этом как о внешнем ограничении.

### Шаг 6. Telegram и единственный polling process

После исправления DNS:

```bash
docker compose run --rm monitor python -m app --test-telegram
```

Проверить, что старый systemd unit действительно не активен и нет второго процесса с тем же bot token. Не выводить token в команды или отчёт.

### Шаг 7. Проверить persistence и restart behavior

Проверить безопасным пересозданием контейнера:

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 monitor
```

Убедиться, что:

- bind mount указывает на ожидаемый абсолютный каталог проекта;
- SQLite и browser profile остались на месте;
- контейнер не находится в restart loop;
- `RestartCount` остаётся ожидаемым;
- после перезапуска Docker daemon сервис поднимается автоматически;
- ручной `docker stop` корректно трактуется как явная остановка и не используется для теста политики `unless-stopped`.

## Возможные изменения в коде после диагностики

Вносить только если подтверждены соответствующие причины:

1. Улучшить structured logs для Playwright:
   - navigation status;
   - final URL;
   - gate type: `login`, `checkpoint`, `captcha`, `temporary_block`, `empty`;
   - provider и search ID;
   - без cookies/HTML/секретов.
2. Не выбирать anonymous provider навсегда после единственного случайного пустого результата, если это маскирует последующий login gate.
3. Различать `DNS/network failure`, `HTTP rejection`, `login required`, `rate limited` и реальную пустую выдачу.
4. Рассмотреть controlled fallback с повторным выбором provider после серии ошибок выбранного provider.
5. Добавить healthcheck, который проверяет жизнеспособность процесса и локальное состояние, но не делает частые запросы к Facebook.
6. Добавить regression tests для provider reselection и классификации redirect/gate.

Не превращать healthcheck в Marketplace scraper: это удвоит запросы и повысит риск rate limiting.

## Критерии готовности

Работа считается завершённой, когда на целевом хостинге подтверждено следующее:

- `docker compose ps` показывает `monitor` в состоянии `Up` без restart loop;
- Docker daemon включён и активен;
- старая systemd-служба и другие дубли процесса отключены;
- DNS внутри контейнера стабильно разрешает Facebook и Telegram в серии проверок;
- `--test-telegram` успешно отправляет тестовое сообщение;
- `--test-marketplace` возвращает реальные объявления или чётко подтверждает внешнее ограничение Meta;
- если нужен вход, используется persistent authenticated profile из `/app/data/browser-profile`;
- после пересборки/перезапуска SQLite, настройки и browser profile сохраняются;
- минимум один обычный рабочий цикл успешно получает объявления и корректно обрабатывает dedup;
- после следующего интервала polling проходит ещё один цикл без DNS/login/restart ошибок;
- в Git, логах и отчёте отсутствуют секреты.

## Что сообщить пользователю по итогам

Короткий отчёт должен содержать:

- корневую причину или несколько подтверждённых причин;
- какие конфигурационные/кодовые изменения сделаны;
- текущий provider Marketplace;
- результат DNS, Marketplace и Telegram tests;
- состояние контейнера и restart count;
- подтверждение persistence;
- оставшиеся внешние ограничения, если Facebook продолжает требовать login/checkpoint;
- ссылки на созданные коммиты.

Не писать, что проблема исправлена, только потому что контейнер `Up`: нужен успешный Marketplace cycle и повторная проверка после интервала polling.

## Дополнительные замечания

- HTTP 400 от `anonymous_http` и redirect Chromium на login — разные классы проблем.
- Ошибка `ERR_NAME_NOT_RESOLVED` — это DNS, а не Facebook block.
- Пустая выдача может быть настоящей, но также может означать изменившуюся страницу; классифицировать её следует по final URL, title/body markers и наличию Marketplace links.
- Facebook URL parameters и DOM не документированы и могут измениться без предупреждения.
- Приложение намеренно не обходит CAPTCHA и checkpoints.
- Не удалять `data/`, не выполнять `docker compose down -v` и не сбрасывать SQLite без отдельного разрешения пользователя.
- Перед редактированием проверить `git status`; не перезаписывать чужие незакоммиченные изменения.
