# API управления zapret2

## Назначение

zapret2 развёртывается как обработчик только маршрутизируемого IPv4-трафика Wi‑Fi-клиентов `10.77.0.0/24`. Трафик Raspberry Pi, SSH, Ethernet LAN и любой входящий трафик в NFQUEUE не направляются. Версия upstream закреплена на commit `8afe88dea7c5f7374f302f947a9d938352c685a2` от 21 июня 2026 года.

## Профили и стратегии

Профиль — root-owned файл с допустимыми переопределениями upstream `config`. Клиент API выбирает только имя из установленного allowlist; произвольные аргументы `nfqws2` API не принимает.

| Профиль | Обработка | Назначение |
|---|---|---|
| `standard` | HTTP/80: `fake` + `multisplit`; TLS/443: `fake` + `multidisorder`; QUIC/443: повторный fake Initial | основной сбалансированный профиль upstream |
| `tcp-only` | те же HTTP и TLS стратегии, UDP/QUIC не перехватывается | совместимость и снижение нагрузки на Pi 3B |
| `tls-only` | только TLS/443: `fake` + `multisplit` | минимальная нагрузка и диагностика |

Стратегии не универсальны для всех провайдеров. Для подбора нового профиля администратор запускает `/opt/zapret2/blockcheck2.sh`, проверяет результат через реальное подключение `eth0`, затем добавляет статический проверенный файл при следующем deployment. Динамическое редактирование Lua-стратегии через API запрещено.

Все профили используют только исходящее направление: `NFQWS2_TCP_PKT_IN=0` и `NFQWS2_UDP_PKT_IN=0`. Это исключает reverse/prerouting hook, который иначе мог бы захватить ответы на соединения самой Raspberry Pi. Flow offload отключён.

## Механизм применения

Базовая конфигурация находится в `/opt/zapret2/config` и подключает `/etc/zapret-rpi/zapret2/active.conf`. Активный файл — символическая ссылка на один из файлов каталога `profiles/`.

Переключение выполняет `/usr/local/sbin/zapret-rpi-profile set NAME`:

1. проверяет имя и наличие обычного файла в allowlist-каталоге;
2. выполняет синтаксическую проверку базового файла и профиля;
3. атомарно заменяет ссылку `active.conf`;
4. перезапускает `zapret2.service`;
5. проверяет состояние службы и при ошибке возвращает предыдущую ссылку.

Команды локального управления:

```bash
sudo zapret-rpi-profile list
sudo zapret-rpi-profile get
sudo zapret-rpi-profile set tcp-only
```

Перезапуск кратковременно оставляет очередь без consumer, но правила создаются с `queue ... bypass`, поэтому модель отказа fail-open: Wi‑Fi остаётся в интернете без DPI-обработки.

## Изоляция трафика

Upstream hook `99-lan-filter` создаёт mark `0x10000000` только при одновременном выполнении условий:

- пакет проходит через `forward`, а не создан локально;
- входной интерфейс — `wlan0` (`IFACE_LAN=wlan0`);
- source входит в `10.77.0.0/24` (`FILTER_LAN_IP`);
- `FILTER_LAN_ALLOW_OUTPUT=0` не создаёт output hook.

Стандартные outgoing hooks zapret2 принимают только пакеты с `FILTER_MARK`. SSH на `eth0:22` не соответствует ни портам перехвата, ни Wi‑Fi mark. Ethernet LAN, локальные адреса и системный трафик Pi также не получают mark. IPv6 forwarding отключён и zapret2 запускается с `DISABLE_IPV6=1`.

Исключение существует только на время фоновой проверки доступности: root-only service создаёт network namespace с veth `zapret-mon`, добавляет точечный mark для source `192.0.2.2`, выполняет HTTPS-probe и удаляет namespace вместе с правилом. Это позволяет измерить именно forwarded path активного профиля, не направляя локальный output Raspberry Pi в NFQUEUE.

## Системные сервисы

| Unit | Ответственность |
|---|---|
| `zapret-rpi-nftables.service` | firewall, forwarding и NAT в таблице `inet zapret_rpi` |
| `zapret-rpi-hostapd.service` | Wi‑Fi AP на `wlan0` |
| `zapret-rpi-dnsmasq.service` | DHCP/DNS только для Wi‑Fi LAN |
| `zapret2.service` | upstream init wrapper, `nfqws2` и таблица `inet zapret2` |

`zapret2.service` запускается после base firewall и `network-online.target`. Он использует актуальный штатный механизм classic Linux: `/opt/zapret2/init.d/sysv/zapret2 start|stop`, который согласованно управляет daemon и firewall. Альтернативный upstream unit `nfqws2@.service` здесь не используется, поскольку он запускает только процесс и требует отдельного управления NFQUEUE-правилами.

## HTTP API управления

API реализован непривилегированным FastAPI backend на `10.77.0.1:8080`. Backend вызывает только фиксированный helper; нижеследующий контракт не разрешает shell-команды или пути к файлам. Маршруты Wi-Fi, состояния, аутентификации и логов описаны в `docs/ui.md`.

### `GET /api/v1/zapret/profiles`

Возвращает установленные профили и активный профиль.

```json
{
  "active": "standard",
  "profiles": [
    {"name": "standard", "description": "Upstream balanced HTTP, TLS and QUIC strategy"},
    {"name": "tcp-only", "description": "Conservative HTTP and TLS strategy; QUIC is not intercepted"},
    {"name": "tls-only", "description": "Minimal TLS-only profile for low CPU usage and diagnostics"}
  ]
}
```

### `GET /api/v1/zapret/profile`

Возвращает активный profile и runtime state: `active`, `degraded` или `failed`.

```json
{"profile":"standard","state":"active","revision":"8afe88dea7c5f7374f302f947a9d938352c685a2"}
```

### `PUT /api/v1/zapret/profile`

Тело: `{"profile":"tcp-only"}`. При успехе возвращает `200` и фактически активный профиль. Операция идемпотентна.

Ошибки: `400` — неверный JSON/имя; `404` — профиль отсутствует; `409` — идёт применение другой конфигурации; `500` — activation и rollback оба неуспешны; `503` — zapret2 после rollback неработоспособен.

### `POST /api/v1/zapret/restart`

Перезапускает только `zapret2.service`, не меняя профиль. Возвращает `204` или `503`.

Изменяющие методы доступны из разрешённых локальных подсетей и сериализуются backend. В журнал пишутся имя профиля, результат и request ID, но не полное содержимое стратегии.

## API автоматического подбора

| Метод | Назначение |
|---|---|
| `POST /api/v1/autotune/runs` | Поставить один запуск в очередь; возвращает `202` и объект запуска |
| `GET /api/v1/autotune/runs/current` | Текущий/последний запуск или `null`, если запусков ещё не было |
| `GET /api/v1/autotune/runs/{id}` | Прогресс и результаты конкретного запуска |
| `POST /api/v1/autotune/runs/{id}/cancel` | Остановить активный запуск и восстановить zapret2 |
| `POST /api/v1/autotune/runs/{id}/apply` | Атомарно применить сгенерированный профиль через `zapret-rpi-profile` |
| `GET /api/v1/autotune/monitor` | Получить настройки, статус и краткую baseline-карту |
| `PUT /api/v1/autotune/monitor` | Включить/выключить монитор и сохранить интервал/параметры подбора |

Тело запуска:

```json
{
  "domains": ["rutracker.org"],
  "protocols": ["http", "https", "quic"],
  "repeats": 2,
  "scan_level": "quick",
  "test_set": "standard"
}
```

Допускается 1–30 доменов, 1–5 повторов и уровни `quick`, `standard`, `force`. `test_set=auto` выбирает ограниченный `zapret-rpi-quick` для quick и upstream `standard` для остальных уровней. Ответ активного запуска содержит `current_test`, `expected_tests` и `candidates` с `suitability`, `coverage`, числом попыток и успешными доменами. Одновременно выполняется только один запуск. Статусы: `queued`, `running`, `completed`, `failed`; фазы: `queued`, `preparing`, `testing`, `evaluating`, `completed`, `failed`.

Тело применения позволяет вручную оставить одну успешную стратегию на протокол:

```json
{
  "selections": [
    {"protocol": "https", "strategy": "--payload=tls_client_hello --lua-desync=multidisorder:pos=1,midsld"},
    {"protocol": "quic", "strategy": "--payload=quic_initial --lua-desync=fake:blob=fake_default_quic:repeats=6"}
  ]
}
```

Каждая пара должна точно присутствовать среди успешных `candidates` выбранного запуска. Повтор одного протокола отклоняется.

Тело настройки монитора использует те же `domains`, `protocols`, `repeats`, `scan_level` и `test_set`, а также `enabled` и `interval_minutes` в диапазоне 15–1440. API не возвращает адреса DNS или полную внутреннюю baseline-карту; UI получает только число доступных целей, время последней проверки, список подтверждённо ухудшившихся доменов и состояние `disabled`, `pending`, `checking`, `healthy`, `waiting`, `retuning`, `cooldown`, `paused` или `error`.

## Структура конфигурации

```text
/opt/zapret2/
├── config                         # базовые ограничения и source active.conf
├── nfq2/nfqws2                    # собранный arm64 binary
└── init.d/sysv/custom.d/99-lan-filter # mark только Wi-Fi forward traffic

/etc/zapret-rpi/zapret2/
├── active.conf -> profiles/standard.conf
└── profiles/
    ├── standard.conf
    ├── tcp-only.conf
    └── tls-only.conf
```

Базовый файл задаёт интерфейсы, mark, packet limits, отсутствие incoming hooks и IPv6. Профиль переопределяет только ports, packet limits и `NFQWS2_OPT`. Права файлов — `0600`, владелец `root`; каталог не доступен на запись web backend.

## Проверка

```bash
sudo zapret-rpi-validate
sudo zapret-rpi-smoke-test
sudo nft list table inet zapret2
systemctl status zapret2.service
```

NFQUEUE counters должны увеличиваться при HTTP/TLS/QUIC-трафике клиента `10.77.0.x` и не изменяться при таком же трафике, созданном самой Pi. Отдельно проверяются SSH через `eth0`, доступ к Ethernet LAN и fail-open после остановки `nfqws2`.

Источники механизма запуска и параметров: [официальный config.default](https://github.com/bol-van/zapret2/blob/8afe88dea7c5f7374f302f947a9d938352c685a2/config.default), [официальное руководство](https://github.com/bol-van/zapret2/blob/8afe88dea7c5f7374f302f947a9d938352c685a2/docs/manual.md), [systemd unit upstream](https://github.com/bol-van/zapret2/blob/8afe88dea7c5f7374f302f947a9d938352c685a2/init.d/systemd/zapret2.service), [LAN filter upstream](https://github.com/bol-van/zapret2/blob/8afe88dea7c5f7374f302f947a9d938352c685a2/init.d/custom.d.examples.linux/99-lan-filter).
