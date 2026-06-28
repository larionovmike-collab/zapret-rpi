# Локальный веб-интерфейс

## Назначение и доступ

Интерфейс доступен из Wi‑Fi LAN по адресу `http://10.77.0.1:8080` и из Ethernet LAN по адресу `http://<адрес-raspberry-pi>` без указания порта. Панель не имеет логина, пароля администратора, сессий или CSRF: главная страница и API сразу доступны из разрешённых firewall локальных подсетей.

Backend реализован на FastAPI и работает как `zapret-web`. Frontend — React SPA. Привилегированные операции выполняет только фиксированный `/usr/local/sbin/zapret-rpi-web-helper`; произвольные команды, пути и systemd units через API передать нельзя.

## Маршруты API

| Метод и маршрут | Назначение |
|---|---|
| `GET /api/v1/status` | CPU, память, активные Wi‑Fi-станции, профиль и состояние zapret2 |
| `GET /api/v1/wifi` | SSID, канал и признак наличия WPA2-пароля |
| `PUT /api/v1/wifi` | Применение SSID, опционального WPA2-пароля и канала |
| `GET /api/v1/zapret/profiles` | Allowlist профилей, активный профиль и его правила |
| `GET /api/v1/zapret/profile` | Активный профиль и runtime state |
| `PUT /api/v1/zapret/profile` | Выбор профиля из allowlist |
| `PUT /api/v1/zapret/enabled` | Включение или выключение zapret2 |
| `POST /api/v1/zapret/restart` | Перезапуск zapret2 |
| `GET /api/v1/zapret/logs?lines=100` | Последние строки журнала |
| `POST /api/v1/autotune/runs` | Запуск автоподбора |
| `GET /api/v1/autotune/runs/current` | Текущий или последний запуск |
| `GET /api/v1/autotune/runs/{id}` | Выбранный запуск |
| `POST /api/v1/autotune/runs/{id}/cancel` | Остановка запуска и восстановление zapret2 |
| `POST /api/v1/autotune/runs/{id}/apply` | Генерация и применение профиля из отмеченных стратегий |

Изменяющие операции сериализуются backend. Параллельное изменение возвращает `409`. Ошибка helper или активации возвращает `503`.

## Разделы

1. **Обзор** — CPU, память, активная стратегия и состояние zapret2.
2. **zapret2** — включение, выбор профиля, точные активные правила и журнал.
3. **Wi‑Fi** — SSID, WPA2-пароль и канал.
4. **Клиенты Wi‑Fi** — реально ассоциированные станции `iw` с IP/hostname из DHCP.
5. **Автоподбор** — домены, повторы, глубина, текущая стратегия, прогресс и рейтинг.

Быстрый режим проверяет максимум 20 отобранных стратегий на домен. Кнопки пресетов добавляют наборы endpoint-ов YouTube, X, Instagram, Discord, Facebook, Signal и LinkedIn. Домены объединяются без дублей; максимум — 30.

После завершения автоматика отмечает одну лучшую стратегию каждого найденного протокола. Пользователь может снять отметку либо выбрать другой успешный кандидат того же протокола. `apply` принимает 1–3 отмеченных стратегии, повторно проверяет их по сохранённому allowlist кандидатов и обновляет единственный профиль `autotune.conf`, отображаемый в панели как `Autotune`.

## Защита и границы

- TCP 8080 доступен только из `wlan0`/`10.77.0.0/24`.
- TCP 80 доступен только из непосредственно подключённой Ethernet-подсети.
- Web units используют `ProtectSystem=strict`, `ProtectHome` и ограниченный набор address families.
- Sudoers разрешает `zapret-web` запускать только project-owned helper.
- Helper повторно валидирует имена профилей, стратегии, диапазоны и JSON.
- Wi‑Fi PSK не возвращается API и не записывается в journal.

## Хранение

| Путь | Содержимое |
|---|---|
| `/etc/zapret-rpi/hostapd.conf` | SSID, WPA2 PSK, страна и канал |
| `/etc/zapret-rpi/zapret2/profiles/*.conf` | статические и автоматически созданные профили |
| `/etc/zapret-rpi/zapret2/active.conf` | symlink активного профиля |
| `/var/lib/zapret-rpi/autotune/` | задания, рейтинг кандидатов и raw logs |

## Проверка

```bash
sudo zapret-rpi-validate
curl http://10.77.0.1:8080/api/v1/status
curl http://<адрес-raspberry-pi>/api/v1/status
```
