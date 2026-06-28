# zapret-rpi

`zapret-rpi` превращает Raspberry Pi с проводным подключением в отдельную Wi‑Fi-точку доступа с локальным DPI bypass на базе [bol-van/zapret2](https://github.com/bol-van/zapret2).

Трафик клиентов обрабатывается непосредственно на Raspberry Pi. Внешний VPN или VPS не требуется. Проводной интерфейс `eth0`, основной маршрут Raspberry Pi и управляющий SSH остаются без изменений.

```text
Телефон / ноутбук
  → Wi‑Fi wlan0 (hostapd + dnsmasq)
  → Raspberry Pi
  → nftables + NFQUEUE
  → nfqws2
  → домашний роутер через eth0
  → Интернет
```

## Возможности

- отдельная WPA2 Wi‑Fi-точка доступа;
- IPv4 forwarding и NAT через проводной `eth0`;
- обработка HTTP, HTTPS/TLS и QUIC с помощью `nfqws2`;
- автоподбор стратегий через штатный `blockcheck2.sh`;
- единый профиль `Autotune`, который обновляется выбранными стратегиями;
- готовые наборы доменов YouTube, X, Instagram, Discord, Facebook, Signal и LinkedIn;
- локальная web-панель без логина по адресу `http://<LAN-IP-Raspberry>`;
- доступ к панели только из непосредственно подключённой Ethernet-подсети;
- резервная копия исходной конфигурации и автоматический откат при ошибке;
- транзакционное обновление проекта и закреплённой версии zapret2.

## Проверенная платформа

Проект разработан и проверен на:

- Raspberry Pi 3 Model B Rev 1.2;
- Debian GNU/Linux 13 (Trixie);
- архитектуре `arm64`;
- стандартном ядре Raspberry Pi;
- встроенном Wi‑Fi `wlan0`;
- проводном интерфейсе `eth0`.

Другие модели Raspberry Pi и Debian-подобные системы могут работать, но автоматически не считаются проверенными.

## Требования

Перед установкой:

1. Установите чистую Debian 13 arm64 или Raspberry Pi OS на базе Debian 13.
2. Включите SSH.
3. Подключите Raspberry Pi к домашнему роутеру кабелем через `eth0`.
4. Подключитесь к Raspberry Pi по SSH.
5. Убедитесь, что Интернет и default route работают через `eth0`.
6. Не настраивайте `wlan0` как отдельную точку доступа вручную.

Если в минимальном образе нет `curl`, установите только bootstrap-зависимости:

```bash
sudo apt update
sudo apt install -y curl ca-certificates
```

Установщик:

- запускается только от `root` или через `sudo`;
- требует активную SSH-сессию через `eth0`;
- отказывается работать, если отсутствуют `eth0` или `wlan0`;
- проверяет Debian-совместимую arm64-систему;
- не изменяет IP-адрес, DHCP-профиль или default route интерфейса `eth0`;
- использует для Wi‑Fi клиентов подсеть `10.77.0.0/24`.

## Быстрая установка

```bash
curl -fsSL https://raw.githubusercontent.com/larionovmike-collab/zapret-rpi/refs/heads/main/install.sh | sudo bash
```

Запускайте эту команду прямо из обычной SSH-сессии. Предварительный `sudo su -` не требуется: `sudo bash` уже выдаёт установщику необходимые права. Начиная с версии `1.0.2`, предусмотрено и восстановление адреса SSH-клиента после `sudo su -` через дерево процессов `sshd`.

Скрипт запросит:

1. имя Wi‑Fi-точки;
2. WPA2-пароль;
3. код страны, по умолчанию `RU`;
4. канал Wi‑Fi: `1`, `6` или `11`;
5. подтверждение установки.

Далее он автоматически:

1. скачает полный снимок репозитория;
2. установит системные зависимости;
3. загрузит и соберёт закреплённый commit zapret2;
4. создаст резервную копию изменяемых файлов;
5. настроит `hostapd`, `dnsmasq`, nftables и systemd;
6. установит backend и готовую сборку web-интерфейса;
7. запустит полную системную проверку;
8. сохранит установленный снимок проекта в `/opt/zapret-rpi`.

После завершения панель будет доступна без порта:

```text
http://<LAN-IP-Raspberry>
```

Например:

```text
http://192.168.1.112
```

Клиенты созданной Wi‑Fi-сети также могут открыть панель по адресу:

```text
http://10.77.0.1:8080
```

## Обновление

Интерактивное обновление до версии из ветки `main`:

```bash
curl -fsSL https://raw.githubusercontent.com/larionovmike-collab/zapret-rpi/refs/heads/main/update.sh | sudo bash
```

Обновление без запроса подтверждения:

```bash
curl -fsSL https://raw.githubusercontent.com/larionovmike-collab/zapret-rpi/refs/heads/main/update.sh | sudo bash -s -- --yes
```

Повторная установка той же версии:

```bash
curl -fsSL https://raw.githubusercontent.com/larionovmike-collab/zapret-rpi/refs/heads/main/update.sh | sudo bash -s -- --force
```

Обновление не выполняет слепой `git pull` внутри работающей системы. Оно:

1. скачивает новый снимок репозитория во временный каталог;
2. сравнивает `VERSION` и `UPSTREAM_COMMIT`;
3. сохраняет текущую систему в `/var/backups/zapret-rpi-updates/`;
4. повторно использует действующие SSID, Wi‑Fi-пароль, страну и канал;
5. собирает закреплённую новую ревизию zapret2;
6. развёртывает файлы проекта и запускает полную проверку;
7. заменяет `/opt/zapret-rpi` только после успешной проверки;
8. автоматически восстанавливает предыдущий снимок при любой ошибке.

Набранный автоподбором профиль `Autotune` и история заданий сохраняются при штатном обновлении.

Первый запуск `update.sh` также умеет принять под управление существующую установку, созданную до появления `/opt/zapret-rpi`: она распознаётся по original backup, установленному валидатору и Git-дереву `/opt/zapret2`.

Снимок обновления содержит конфигурацию Wi‑Fi, включая WPA2-пароль. Каталог и архив создаются с доступом только для `root`; не копируйте их в web-каталог или публичный репозиторий.

### Обновление upstream zapret2

Версия upstream хранится в одном файле:

```text
UPSTREAM_COMMIT
```

Чтобы выпустить обновление zapret2:

1. изучите изменения в официальном репозитории;
2. запишите проверенный 40-символьный commit в `UPSTREAM_COMMIT`;
3. увеличьте версию в `VERSION`;
4. пересоберите frontend и запустите тесты;
5. отправьте изменения в `main`;
6. выполните обычную команду `update.sh` на Raspberry Pi.

Установленные устройства получат новую upstream-ревизию вместе с обновлением проекта и смогут автоматически вернуться к предыдущей сборке при неудаче.

## Проверка

Состояние системы:

```bash
sudo zapret-rpi-validate
```

Основные службы:

```bash
systemctl status zapret2
systemctl status zapret-rpi-hostapd
systemctl status zapret-rpi-dnsmasq
systemctl status zapret-rpi-nftables
systemctl status zapret-rpi-web
systemctl status zapret-rpi-web-lan
```

Активный профиль:

```bash
sudo zapret-rpi-profile get
```

Текущие версии:

```bash
cat /etc/zapret-rpi/release.env
git -C /opt/zapret2 rev-parse HEAD
cat /opt/zapret-rpi/VERSION
```

## Удаление и восстановление исходной конфигурации

```bash
curl -fsSL https://raw.githubusercontent.com/larionovmike-collab/zapret-rpi/refs/heads/main/rollback.sh | sudo bash
```

Без запроса подтверждения:

```bash
curl -fsSL https://raw.githubusercontent.com/larionovmike-collab/zapret-rpi/refs/heads/main/rollback.sh | sudo bash -s -- --yes
```

Откат:

- останавливает службы проекта;
- удаляет только принадлежащие проекту nftables-таблицы и файлы;
- восстанавливает сетевые файлы и состояния служб до установки;
- сохраняет копию исходного installer backup в `/var/backups/zapret-rpi-removed-*`;
- не удаляет установленные Debian-пакеты и исходное дерево `/opt/zapret2`.

Архив исходной конфигурации может содержать прежние сетевые пароли и доступен только `root`.

## Важные ограничения

- Проект обрабатывает только маршрутизируемый IPv4-трафик клиентов `wlan0`.
- IPv6 для Wi‑Fi клиентов отключён.
- Web-панель использует обычный HTTP и предназначена только для доверенной домашней сети.
- Firewall разрешает панель на TCP 80 только из непосредственно подключённой Ethernet-подсети.
- Качество обхода зависит от провайдера, региона и актуальности стратегий DPI.
- Обновлять zapret2 непосредственно командой `git pull` в `/opt/zapret2` не рекомендуется.
- Перед обновлением upstream следует проверять изменения `blockcheck2.sh`, `config.default`, Lua-стратегий и nftables-интеграции.

## Структура репозитория

```text
install.sh                 публичный bootstrap-установщик
update.sh                  публичный bootstrap-обновлятор
rollback.sh                публичный откат установки
VERSION                    версия zapret-rpi
UPSTREAM_COMMIT            закреплённая ревизия bol-van/zapret2

configs/                   hostapd, dnsmasq, nftables и zapret2
systemd/                   службы проекта
scripts/install.sh         внутреннее развёртывание
scripts/update-system.sh   snapshot, обновление и автооткат
scripts/validate.sh        системная проверка
scripts/autotune.py        автоподбор и профиль Autotune
web/                       FastAPI backend и React frontend
docs/                      подробная архитектура и API
tests/                     тесты парсера и генерации профилей
```

## Подготовка GitHub-репозитория

Рекомендуемое имя:

```text
larionovmike-collab/zapret-rpi
```

Перед первой публикацией:

1. перенесите содержимое этой папки в корень нового репозитория;
2. не добавляйте `.env`;
3. не добавляйте `codex-state`, `node_modules` и `__pycache__`;
4. обязательно добавьте `web/frontend/dist` — чистая Raspberry не должна собирать frontend через Node.js;
5. убедитесь, что ветка по умолчанию называется `main`;
6. после загрузки проверьте GitHub Actions;
7. откройте raw-ссылку `install.sh` и только затем запускайте установку на чистой системе.

Для локальной проверки перед публикацией:

```bash
find . -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/*.py web/backend/zapret_ui/*.py
npm --prefix web/frontend run build
```

`node_modules` требуется только для разработки frontend и не используется установщиком Raspberry Pi.

## Документация

- [Архитектура](docs/architecture.md)
- [Установка и эксплуатация](docs/deployment.md)
- [Web-интерфейс](docs/ui.md)
- [HTTP API и профили](docs/api.md)
- [Итоговая конфигурация](docs/final-system.md)

## Upstream

- [bol-van/zapret2](https://github.com/bol-van/zapret2)
- [Документация zapret2](https://github.com/bol-van/zapret2/blob/master/docs/manual.md)

`zapret-rpi` не является официальной частью проекта bol-van/zapret2.
