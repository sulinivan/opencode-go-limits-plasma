#!/usr/bin/env python3
"""Источник данных для плазмоида OpenCode Go Limits.

Скрипт делает четыре вещи:
  1. достаёт из базы установленного opencode токен входа и организацию;
  2. запрашивает у консоли OpenCode состояние лимитов Go (5 часов / неделя / месяц)
     и дневной расход Go с полуночи (норма дня = лимит месяца / 31);
  3. печатает одну строку JSON в stdout и кладёт её же в общий журнал usage.log,
     из которого читают все остальные экземпляры виджета (панель + рабочий стол);
  4. решает, кому отправлять уведомление о превышении порога — ровно одному
     экземпляру на окно сброса (см. --threshold).

Лимиты Go живут в консоли OpenCode (opencode.ai/console), а не в Zen-провайдере:
эндпоинт /zen/go/v1/usage больше не существует. Поэтому ключ подписки `opencode-go`
из auth.json для этого запроса не годится — нужен токен входа, который opencode
обновляет сам при каждом запуске.

Используется только стандартная библиотека Python 3 (никаких pip-пакетов).

Запуск вручную (для проверки входа и связи с консолью):

    python3 backend.py --test
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Не засоряем пакет виджета кэшем байт-кода.
sys.dont_write_bytecode = True

USER_AGENT = "opencode-go-limits-plasma/1.0"
TIMEOUT_SECONDS = 20

# Ключи окон лимитов: порядок совпадает с порядком полос в виджете.
WINDOWS = ("rolling", "weekly", "monthly")

# Порядок проверки уведомлений: те же окна плюс дневное. В WINDOWS дня нет,
# потому что normalize разбирает счётчики консоли, а день считается отдельно.
NOTIFY_ORDER = ("rolling", "daily", "weekly", "monthly")

# Как консоль называет каждое окно в ответе.
METERS = {"rolling": "fiveHour", "weekly": "week", "monthly": "month"}

# Дневная норма: месячный лимит, поделённый на 31 день. Потраченное берётся
# с сервера (/api/usage/models с полуночи, только провайдер opencode-go) —
# локального счёта дней нет, все ПК видят одно и то же число.
DAILY_DIVISOR = 31

# Общий журнал и маркер уведомлений лежат рядом со скриптом, внутри пакета
# виджета: так путь не зависит от $HOME и не требует переменных окружения.
STATE_DIR = Path(__file__).resolve().parent

MSG_NO_AUTH = "OpenCode не настроен. Установите opencode и войдите: opencode auth login"
MSG_NO_TOKEN = "opencode не вошёл в аккаунт: нет токена в opencode.db — запустите opencode"
MSG_NO_ORG = "у аккаунта opencode нет организации — проверьте workspace в консоли"
MSG_NO_GO = "Нужна подписка OpenCode Go: аккаунт без тарифа Go"
MSG_TOKEN_EXPIRED = "Токен входа opencode истёк — запустите opencode, чтобы он обновился"
MSG_BAD_RESPONSE = "неожиданный ответ консоли"


class UsageError(Exception):
    """Ошибка, текст которой показывается пользователю как есть."""


def database_path() -> Path:
    """База данных установленного opencode (там лежит токен входа)."""
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def database_file(path: Path | None = None) -> Path:
    source = path or database_path()
    if not source.is_file():
        raise UsageError(MSG_NO_AUTH)
    return source


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _amount(value: object) -> int:
    """Микроценты приходят строками; мусор считаем нулём."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def load_credentials(path: Path | None = None) -> tuple[str, str, str, int]:
    """Возвращает (токен, организация, база консоли, токен истекает в мс)."""
    try:
        connection = sqlite3.connect(str(database_file(path)))
    except sqlite3.Error as exc:
        raise UsageError(f"не удалось открыть opencode.db: {exc}") from exc

    try:
        account = connection.execute(
            "select url, access_token, token_expiry from account"
        ).fetchone()
        org = connection.execute(
            "select active_org_id from account_state"
        ).fetchone()
    except sqlite3.Error as exc:
        raise UsageError(f"не удалось прочитать opencode.db: {exc}") from exc
    finally:
        connection.close()

    if not account or not _text(account[1]):
        raise UsageError(MSG_NO_TOKEN)
    if not org or not _text(org[0]):
        raise UsageError(MSG_NO_ORG)

    return _text(account[1]), _text(org[0]), _text(account[0]).rstrip("/"), _amount(account[2])


def message_for_http_status(code: int) -> str:
    """Человеческое объяснение кода ответа консоли."""
    if code == 401:
        return "токен входа отклонён (401) — запустите opencode, чтобы он обновился"
    if code == 404:
        return "консоль не знает эндпоинт лимитов (404) — возможно, изменился API"
    if code == 400:
        return "консоль отклонила запрос (400) — проверьте организацию в opencode"
    return f"консоль ответила ошибкой {code}"


def console_get(
    path: str,
    token: str,
    org: str,
    base: str,
    timeout: int = TIMEOUT_SECONDS,
) -> dict:
    """GET к консоли: авторизация, разбор JSON и тексты ошибок — в одном месте."""
    request = urllib.request.Request(
        base + path,
        headers={
            "Authorization": "Bearer " + token,
            "x-org-id": org,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise UsageError(message_for_http_status(exc.code)) from exc
    except urllib.error.URLError as exc:
        raise UsageError(f"нет связи с консолью: {exc.reason}") from exc
    except TimeoutError as exc:
        raise UsageError("таймаут запроса к консоли") from exc
    except ValueError as exc:
        raise UsageError(MSG_BAD_RESPONSE) from exc


def fetch_status(
    token: str,
    org: str,
    base: str = "https://opencode.ai/console",
    timeout: int = TIMEOUT_SECONDS,
) -> dict:
    """Окна лимитов Go: GET <console>/api/go/status."""
    return console_get("/api/go/status", token, org, base, timeout)


def normalize(payload: object) -> dict:
    """Оставляет только нужные поля и приводит типы к удобным для QML."""
    access = payload.get("access") if isinstance(payload, dict) else None
    meters = access.get("meters") if isinstance(access, dict) else None
    if not isinstance(meters, dict):
        if isinstance(payload, dict) and payload.get("product") not in ("go", "go-plus"):
            raise UsageError(MSG_NO_GO)
        raise UsageError(MSG_BAD_RESPONSE)

    result: dict[str, dict] = {}
    for name in WINDOWS:
        meter = meters.get(METERS[name])
        if not isinstance(meter, dict):
            continue
        used = _amount(meter.get("usedMicroCents"))
        limit = _amount(meter.get("limitMicroCents"))
        percent = used / limit * 100 if limit > 0 else 0.0
        result[name] = {
            "percent": percent,
            "status": "exceeded" if percent >= 100 else "active",
            "resetsAt": _text(meter.get("resetsAt")),
        }
    return result


def day_bounds() -> tuple[str, str]:
    """Границы суток: сброс окна в локальном ISO и начало дня в UTC ISO для консоли."""
    today = datetime.datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        (today + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S"),
        today.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def fetch_daily_spend(
    token: str,
    org: str,
    base: str,
    month_limit: int,
    timeout: int = TIMEOUT_SECONDS,
) -> dict:
    """Дневной расход Go с сервера: сумма затрат моделей opencode-go с полуночи.

    Никакого локального счёта дней — все ПК видят одно и то же число.
    Процент от дневной нормы (лимит месяца / 31) не ограничен сверху:
    перерасход показывается как есть (137%, 200%, ...).
    """
    reset_at, since = day_bounds()
    query = urllib.parse.urlencode(
        {"since": since, "pageSize": 100}  # моделей за день — единицы
    )
    payload = console_get("/api/usage/models?" + query, token, org, base, timeout)

    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise UsageError(MSG_BAD_RESPONSE)
    spent = sum(
        _amount(item.get("totalCostMicroCents"))
        for item in items
        if isinstance(item, dict) and item.get("provider") == "opencode-go"
    )
    allowance = month_limit / DAILY_DIVISOR if month_limit > 0 else 0
    percent = spent / allowance * 100 if allowance > 0 else 0.0
    return {
        "percent": percent,
        "status": "exceeded" if percent >= 100 else "active",
        "resetsAt": reset_at,
    }


def collect() -> dict:
    """Полный цикл: вход -> запрос -> нормализация."""
    token, org, base, expiry = load_credentials()
    # ponytail: токен не обновляем сами — opencode обновляет его при каждом
    # запуске, а писать в его базу из виджета опасно. Протухший токен даёт
    # 401; если понадобится свой refresh через /console/auth/device/token,
    # это +15 строк в load_credentials.
    if expiry and expiry <= time.time() * 1000:
        raise UsageError(MSG_TOKEN_EXPIRED)
    payload = fetch_status(token, org, base)
    if isinstance(payload, dict) and payload.get("product") not in ("go", "go-plus"):
        raise UsageError(MSG_NO_GO)
    usage = normalize(payload)
    month_meter = payload.get("access", {}).get("meters", {}).get("month", {})
    try:
        usage["daily"] = fetch_daily_spend(
            token, org, base, _amount(month_meter.get("limitMicroCents"))
        )
    except UsageError:
        pass  # дневное окно необязательно: остальные три покажем без него
    return usage


def publish(document: dict) -> None:
    """Атомарно кладёт результат в общий журнал, который читают все виджеты."""
    line = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    temporary = STATE_DIR / "usage.tmp"
    try:
        temporary.write_text(line + "\n", encoding="utf-8")
        os.replace(temporary, STATE_DIR / "usage.log")
    except OSError:
        pass  # журнал — необязательный канал: виджет всё равно получит ответ


def window_key(resets_at: str, threshold: int) -> str:
    """Ключ окна сброса для уведомления.

    Консоль отдаёт resetsAt с дрейфом в долях секунды (10:56:48.051, 10:56:48.845),
    поэтому сравниваем только до минут: иначе уведомление повторялось бы при
    каждом опросе.
    """
    return resets_at[:16] + "|" + str(threshold)


def claim(key: str) -> bool:
    """True, если уведомление об этом окне сброса ещё никто не отправлял."""
    marker = STATE_DIR / "notified"
    try:
        with open(marker, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            if handle.read().strip() == key:
                return False
            handle.seek(0)
            handle.truncate()
            handle.write(key)
            handle.flush()
            return True
    except OSError:
        return True  # не смогли проверить — лучше показать уведомление


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Лимиты OpenCode Go для плазмоида")
    parser.add_argument("--test", action="store_true", help="проверить вход и показать лимиты в читаемом виде")
    parser.add_argument(
        "--threshold",
        type=int,
        default=None,
        metavar="N",
        help="порог уведомления, %%: один раз на окно сброса, для всех виджетов вместе",
    )
    parser.add_argument(
        "--notify-windows",
        default="rolling",
        metavar="LIST",
        help="окна для уведомлений через запятую (rolling,daily,weekly,monthly)",
    )
    args = parser.parse_args(argv)

    if args.test:
        try:
            usage = collect()
        except UsageError as exc:
            print("FAIL: " + str(exc))
            return 1
        for name in NOTIFY_ORDER:
            item = usage.get(name)
            if item:
                print(f"{name:<8} {item['percent']:>5.0f}%  status={item['status']:<8} resets {item['resetsAt'] or '—'}")
        return 0

    document: dict = {"time": time.strftime("%H:%M:%S")}
    try:
        usage = collect()
    except UsageError as exc:
        document.update(ok=False, error=str(exc))
    except Exception as exc:  # последняя страховка, чтобы виджет не остался без ответа
        document.update(ok=False, error=f"внутренняя ошибка: {exc}")
    else:
        document.update(ok=True, usage=usage)
        if args.threshold is not None:
            wanted = {
                name.strip()
                for name in (args.notify_windows or "").split(",")
                if name.strip() in NOTIFY_ORDER
            }
            for name in NOTIFY_ORDER:
                window = usage.get(name)
                if name in wanted and window and window["percent"] >= args.threshold:
                    # Имя окна вместо bool: виджет покажет, какое окно сработало.
                    # False сохраняем для совместимости со старыми виджетами.
                    if claim(window_key(window["resetsAt"], args.threshold)):
                        document["notify"] = name
                    else:
                        document["notify"] = False
                    break

    publish(document)
    print(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
