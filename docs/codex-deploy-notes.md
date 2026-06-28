# Codex deploy notes for zapret-rpi

Эта памятка нужна самому Codex при работе с этим проектом и живой Raspberry Pi.

## Главное правило

Не выполнять длинные SSH/PowerShell-команды одной строкой.

Для любых нетривиальных действий:

1. создать локальный временный `.ps1` или короткий `.sh`;
2. загрузить shell-скрипт на Raspberry;
3. нормализовать CRLF;
4. запустить файл-скрипт;
5. удалить временный локальный скрипт из репозитория перед финальным ответом.

Так меньше ошибок с кавычками, `$`, пайпами, heredoc и PowerShell parsing.

## Доступ к Raspberry

Данные брать из `.env`:

- `PI_HOST`
- `PI_USER`
- `PI_PASSWORD`

Не печатать пароль в финальном ответе и не сохранять его в файлы репозитория.

Для сетевых команд и SSH/SCP обычно нужен escalated запуск, потому что network access ограничен sandbox-ом.

## PowerShell: чего избегать

Не использовать Unix-разделители в PowerShell:

```powershell
cmd1 && cmd2
cmd1 || cmd2
```

В Windows PowerShell 5 это ломается. Использовать отдельные команды или `.ps1`.

Не писать большие inline-команды вида:

```powershell
powershell.exe -Command "много ssh, heredoc, sed, json, кавычек..."
```

Вместо этого создавать файл:

```powershell
powershell.exe -ExecutionPolicy Bypass -File codex-state\some-task.ps1
```

## SCP/SSH через plink/pscp

Для этого проекта надёжный вариант:

```powershell
plink.exe -batch -pw $password $target "command"
pscp.exe -batch -pw $password localfile "${target}:/remote/path"
```

Если remote script создаётся через stdin:

```powershell
$remote | plink.exe -batch -pw $password $target "tee /home/$user/task.sh >/dev/null"
plink.exe -batch -pw $password $target "sed -i 's/\r$//' /home/$user/task.sh"
plink.exe -batch -pw $password $target "echo '$password' | sudo -S -p '' bash /home/$user/task.sh"
```

Не писать во временные имена в `/tmp`, если раньше они могли быть созданы root-ом. Лучше использовать `/home/$PI_USER/...`.

## CRLF на Linux

Любой `.sh`, отправленный с Windows, перед запуском обязательно чистить:

```sh
sed -i 's/\r$//' /path/to/script.sh
```

Иначе возможны странные ошибки вроде:

```text
systemctl is-active --quiet $'zapret-rpi-web-lan.service\r'
```

## stdin у helper-скриптов

`zapret-rpi-web-helper` теперь допускает пустой stdin, но при запуске из shell-файла всё равно лучше явно закрывать stdin:

```sh
/usr/local/sbin/zapret-rpi-web-helper status </dev/null
```

Иначе helper может прочитать остаток текущего `.sh` как stdin.

Web backend обычно передаёт `{}` или JSON body сам, поэтому в API это не проблема.

## Deploy backend/helper/autotune

Типовой порядок:

1. Локально проверить синтаксис:

   ```powershell
   python -m py_compile scripts/autotune.py scripts/web-helper.py web/backend/zapret_ui/main.py
   ```

2. Скопировать на Pi:

   ```sh
   install -m 0755 /tmp/zapret-rpi-autotune /usr/local/sbin/zapret-rpi-autotune
   install -m 0755 /tmp/zapret-rpi-web-helper /usr/local/sbin/zapret-rpi-web-helper
   sed -i 's/\r$//' /usr/local/sbin/zapret-rpi-autotune
   sed -i 's/\r$//' /usr/local/sbin/zapret-rpi-web-helper
   python3 -m py_compile /usr/local/sbin/zapret-rpi-autotune /usr/local/sbin/zapret-rpi-web-helper
   ```

3. Перезапустить нужные сервисы:

   ```sh
   systemctl restart zapret-rpi-web.service zapret-rpi-web-lan.service
   systemctl is-active --quiet zapret-rpi-web.service
   systemctl is-active --quiet zapret-rpi-web-lan.service
   ```

## Deploy frontend

Если `node_modules` отсутствует, ставить зависимости короткой командой:

```powershell
npm.cmd ci
```

Если PowerShell блокирует `npm.ps1`, использовать именно `npm.cmd`.

Если sandbox/Windows не даёт писать `node_modules` или `dist`, запускать соответствующий короткий npm-шаг с escalation и понятным justification.

Собрать:

```powershell
npm.cmd run build
```

После сборки удалить локальный `node_modules`, если он был создан только временно:

```powershell
Remove-Item -LiteralPath 'D:\Go\zapret-rpi\web\frontend\node_modules' -Recurse -Force
```

На Pi копировать содержимое `web/frontend/dist` в:

```text
/opt/zapret-rpi/web/frontend/dist
```

При обновлении dist:

```sh
mkdir -p /opt/zapret-rpi/web/frontend/dist
rm -rf /opt/zapret-rpi/web/frontend/dist/assets
cp -a /tmp/zapret-rpi-dist/. /opt/zapret-rpi/web/frontend/dist/
chown -R root:root /opt/zapret-rpi/web/frontend/dist
systemctl restart zapret-rpi-web.service zapret-rpi-web-lan.service
```

## Проверки после deploy

Минимум:

```sh
systemctl is-active zapret-rpi-web.service
systemctl is-active zapret-rpi-web-lan.service
systemctl is-active zapret-rpi-hostapd.service
systemctl is-active zapret-rpi-dnsmasq.service
/usr/local/sbin/zapret-rpi-web-helper status </dev/null
```

С машины разработчика:

```powershell
curl.exe -s -o NUL -w "%{http_code}" http://$PI_HOST/
```

Ожидаемый ответ для `GET /` — `200`.

`HEAD /` может вернуть `405`, это не означает, что UI недоступен.

## Autotune smoke-test

Короткий тест:

```json
{"domains":["example.com"],"protocols":["http"],"repeats":1,"scan_level":"quick","test_set":"standard"}
```

Ожидаемо возможен результат:

```json
{
  "status": "completed",
  "progress": 100,
  "best_profile": null,
  "note": "blockcheck2 не подобрал стратегию, потому что проверяемые цели доступны без DPI-bypass."
}
```

Это нормально: `example.com` доступен без обхода, стратегия не нужна.

Аварией считать только:

- ненулевой exit code `blockcheck2`;
- отсутствие prerequisites;
- невозможность создать job/profile;
- падение systemd-сервиса autotune.

## Частые ошибки и как их не повторять

- Не считать DHCP lease реальным Wi-Fi клиентом. Для UI клиентов использовать реальные associated stations из `hostapd_cli all_sta` / `iw station dump`, а DHCP только для IP/hostname.
- Не считать “strategy not found” ошибкой, если `blockcheck2` завершился успешно и цель доступна без bypass.
- Не забывать `dnsutils`: `blockcheck2` требует `nslookup` или `host`.
- Не забывать `ReadWritePaths` в systemd units, если сервис с `ProtectSystem=strict` должен писать в `/var/lib/zapret-rpi` или `/etc/zapret-rpi`.
- Не включать `NoNewPrivileges=true` для web unit, если sudo-helper должен повышать привилегии.
- Не оставлять временные deploy/debug scripts в `codex-state`.
- Не оставлять `web/frontend/node_modules` после временной локальной сборки.
