# Автоматическое развертывание Raspberry Pi

## Публичная установка и обновление

На чистой Debian 13 arm64 установка запускается из SSH-сессии через Ethernet:

```bash
curl -fsSL https://raw.githubusercontent.com/larionovmike-collab/zapret-rpi/refs/heads/main/install.sh | sudo bash
```

Обновление установленной системы:

```bash
curl -fsSL https://raw.githubusercontent.com/larionovmike-collab/zapret-rpi/refs/heads/main/update.sh | sudo bash
```

Bootstrap-скрипты скачивают полный снимок репозитория. Установка сохраняет его в `/opt/zapret-rpi`, а обновление предварительно создаёт root-only snapshot в `/var/backups/zapret-rpi-updates/` и автоматически восстанавливает предыдущую версию при неудаче.

## Назначение

Сценарии в `scripts/` превращают Raspberry Pi 3B с Debian 13 arm64 в отдельный IPv4 Wi‑Fi-шлюз в соответствии с `docs/architecture.md`. Ethernet-интерфейс `eth0`, его DHCP-профиль, адрес, маршрут по умолчанию и SSH-служба не изменяются. zapret2 собирается из закреплённого upstream commit, запускается штатным init wrapper и обрабатывает только forwarded-трафик Wi‑Fi-клиентов.

Внутреннее развёртывание идемпотентно, но для уже установленной системы следует использовать публичный `update.sh`: он создаёт отдельный снимок, обновляет файлы проекта и заново проверяет конфигурацию. Исходные версии изменяемых файлов сохраняются один раз в `/var/lib/zapret-rpi/backup/original` и не перезаписываются обновлениями.

## Установленные компоненты

- `hostapd` — WPA2-PSK/CCMP точка доступа 2,4 ГГц на `wlan0`;
- `dnsmasq` — DHCP и кэширующий DNS, привязанный только к `10.77.0.1` на `wlan0`;
- `nftables` — фильтрация, forwarding и masquerade в отдельной таблице `inet zapret_rpi`;
- `systemd-networkd` — статический адрес только для `wlan0`;
- NetworkManager — продолжает управлять `eth0`, а `wlan0` помечается как unmanaged;
- `systemd` units `zapret-rpi-nftables`, `zapret-rpi-hostapd` и `zapret-rpi-dnsmasq`;
- вспомогательные пакеты `iw`, `rfkill`, `iproute2` и `network-manager`.
- локальный FastAPI + React web UI на `10.77.0.1:8080` для Wi‑Fi LAN и `http://<адрес-raspberry-pi>` для Ethernet LAN, запущенный от `zapret-web` через отдельный ограниченный helper.
- асинхронный root-only runner `zapret-rpi-autotune.service`, который запускает штатный `blockcheck2.sh` и сохраняет результаты в `/var/lib/zapret-rpi/autotune`.
- timer/oneshot-пара `zapret-rpi-autocheck.timer` и `zapret-rpi-autocheck.service` для фоновой проверки принятой доступности доменов через виртуальный forwarded-клиент.

Штатные units `hostapd.service` и `dnsmasq.service` отключаются, поскольку проект запускает эти программы с изолированными конфигурациями. Их предыдущее состояние сохраняется для отката. Пакеты при откате не удаляются.

## Сетевая схема

```text
Интернет
   |
существующий маршрутизатор (DHCP)
   |
eth0 — DHCP-адрес, default route, SSH :22
Raspberry Pi
   |  nftables forward + masquerade
wlan0 — 10.77.0.1/24, WPA2 AP
   |
клиенты — DHCP 10.77.0.50–10.77.0.200
```

DNS-клиентам выдаётся `10.77.0.1`; `dnsmasq` пересылает запросы на `1.1.1.1` и `9.9.9.9`. IPv4 forwarding включён, IPv6 forwarding отключён. Межклиентский трафик блокируется и в `hostapd` (`ap_isolate=1`), и в firewall.

В таблице `inet zapret_rpi` разрешены:

- SSH на `eth0:22` и HTTP web UI на `eth0:80` только из непосредственно подключённой Ethernet-подсети, определённой во время установки;
- DHCP, DNS, ICMP с ограничением частоты и web UI на TCP 8080 со стороны Wi‑Fi;
- новые соединения `wlan0 -> eth0` с source `10.77.0.0/24` и ответный established/related трафик;
- NAT masquerade для `10.77.0.0/24` при выходе через `eth0`.

Сценарий не выполняет глобальный `flush ruleset`. NFQUEUE-правила принадлежат отдельной upstream-таблице `inet zapret2` и используют fail-open `bypass`.

## Запуск развертывания

Требования к рабочей станции: PowerShell 7, OpenSSH (`ssh`, `scp`) и `tar`. SSH host key целевого устройства должен быть заранее добавлен в `known_hosts`. Рекомендуется вход по ключу пользователем `root`; для другого пользователя нужен рабочий passwordless `sudo`.

Можно указать адрес явно:

```powershell
./scripts/deploy.ps1 -HostName pi.local -User root -Ssid Zapret-RPi -Country RU -Channel 6
```

Либо создать локальный, исключённый из Git файл `.env`:

```text
PI_HOST=pi.local
PI_USER=root
```

После запуска сценарий безопасно запрашивает WPA2-пароль, проверяет Raspberry Pi через SSH, загружает временный архив и запускает `scripts/install.sh`. Web UI открывается сразу и не имеет отдельной авторизации. Допустимые SSID и пароль Wi-Fi состоят из латинских букв, цифр, `.`, `!`, `_`, `-`; пароль должен иметь длину 8–63 символа. Каналы ограничены значениями 1, 6 и 11.

Перед изменениями установка проверяет Debian/arm64, наличие `eth0` и `wlan0`, IPv4/default route на `eth0`, активный SSH и отсутствие конфликта с `10.77.0.0/24`. Тип текущего пользовательского соединения намеренно не определяется: пользователь обязан запускать установку через надёжный проводной канал управления. Конфигурации проходят `dnsmasq --test` и `nft -c`; hostapd проверяется фактическим запуском project-owned unit с автоматическим откатом при ошибке. У hostapd ключ `-t` включает timestamps и не является режимом проверки конфигурации.

## Изменённые файлы на Raspberry Pi

| Файл | Назначение |
|---|---|
| `/etc/zapret-rpi/hostapd.conf` | SSID, WPA2, страна и канал |
| `/etc/zapret-rpi/dnsmasq.conf` | DHCP/DNS только на `wlan0` |
| `/etc/zapret-rpi/zapret-rpi.nft` | firewall, forwarding и NAT |
| `/etc/NetworkManager/conf.d/10-zapret-rpi-wlan0.conf` | исключение `wlan0` из NetworkManager |
| `/etc/systemd/network/20-zapret-rpi-wlan0.network` | адрес `10.77.0.1/24` |
| `/etc/sysctl.d/90-zapret-rpi-router.conf` | IPv4/IPv6 forwarding |
| `/etc/systemd/system/zapret-rpi-*.service` | три project-owned units |
| `/usr/local/lib/zapret-rpi/apply-nft.sh` | атомарная проверка и замена project-owned таблицы |
| `/usr/local/sbin/zapret-rpi-{validate,smoke-test,rollback}` | эксплуатационные команды |
| `/usr/local/sbin/zapret-rpi-autotune` | очередь, выполнение, оценка и применение автоподбора |
| `/etc/systemd/system/zapret-rpi-autotune.service` | длительный oneshot-запуск вне web worker |
| `/etc/systemd/system/zapret-rpi-autocheck.{service,timer}` | фоновая проверка доступности и её планировщик |
| `/var/lib/zapret-rpi/backup/original/` | исходные файлы и состояния units |

`eth0` и его NetworkManager connection profile не изменяются. `/opt/zapret2` содержит закреплённый runtime, `/etc/zapret-rpi/zapret2` — профили, а `zapret2.service` управляет upstream init wrapper. Контракт профилей и API описан в `docs/api.md`.

## Проверка работоспособности

Общая автоматическая проверка:

```bash
sudo zapret-rpi-validate
sudo zapret-rpi-smoke-test
```

Точечная диагностика:

```bash
ip -br address show eth0
ip -br address show wlan0
ip -4 route
networkctl status wlan0
nmcli device status
systemctl status zapret-rpi-nftables zapret-rpi-hostapd zapret-rpi-dnsmasq
systemctl status zapret-rpi-hostapd.service
dnsmasq --test --conf-file=/etc/zapret-rpi/dnsmasq.conf
/usr/local/lib/zapret-rpi/apply-nft.sh --check
nft list table inet zapret_rpi
sysctl net.ipv4.ip_forward net.ipv6.conf.all.forwarding
ss -lntup
journalctl -u zapret-rpi-hostapd -u zapret-rpi-dnsmasq -u zapret-rpi-nftables -b
systemctl is-active zapret2.service; pgrep -af nfqws2
```

С Wi‑Fi-клиента следует проверить получение адреса из DHCP-пула, gateway/DNS `10.77.0.1`, DNS-разрешение и доступ в Интернет. С отдельной Ethernet-сессии следует подтвердить SSH на текущий DHCP-адрес Raspberry Pi.

## Откат

Полный автоматический откат файлов и состояний units:

```bash
sudo zapret-rpi-rollback
```

Откат останавливает и отключает project-owned units, удаляет таблицы `inet zapret_rpi` и `inet zapret2`, восстанавливает исходные файлы, состояния `hostapd.service`, `dnsmasq.service` и `systemd-networkd.service`, перечитывает NetworkManager и sysctl. Остальные nftables-таблицы не затрагиваются. Исходное дерево `/opt/zapret2` и установленные build-зависимости сохраняются, но его активный `config` и project-owned custom hook восстанавливаются по manifest.

Установленные Debian-пакеты намеренно сохраняются: их автоматическое удаление может затронуть зависимости и пользовательские настройки. Если они точно не использовались до развертывания, после отката их можно удалить вручную:

```bash
sudo apt remove hostapd dnsmasq nftables iw rfkill
sudo apt autoremove
```

Перед ручным удалением обязательно проверить `apt`-план и сохранить действующую Ethernet SSH-сессию.
