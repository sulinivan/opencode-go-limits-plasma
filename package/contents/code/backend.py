#!/usr/bin/env python3
"""Источник данных для плазмоида OpenCode Go Limits.

Скрипт делает четыре вещи:
  1. находит API-ключ `opencode-go` в auth.json установленного opencode;
  2. запрашивает у сервиса статистику лимитов (5 часов / неделя / месяц);
  3. печатает одну строку JSON в stdout и кладёт её же в общий журнал usage.log,
     из которого читают все остальные экземпляры виджета (панель + рабочий стол);
  4. решает, кому отправлять уведомление о превышении порога — ровно одному
     экземпляру на окно сброса (см. --threshold).

Используется только стандартная библиотека Python 3 (никаких pip-пакетов).

Запуск вручную (для проверки ключа и связи с сервисом):

    python3 backend.py --test
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://opencode.ai/zen/go/v1/usage"
USER_AGENT = "opencode-go-limits-plasma/1.0"
TIMEOUT_SECONDS = 20

# Ключи окон лимитов: порядок совпадает с порядком полос в виджете.
WINDOWS = ("rolling", "weekly", "monthly")

# Общий журнал и маркер уведомлений лежат рядом со скриптом, внутри пакета
# виджета: так путь не зависит от $HOME и не требует переменных окружения.
STATE_DIR = Path(__file__).resolve().parent

MSG_NO_AUTH = "OpenCode не настроен. Установите opencode и войдите: opencode auth login"
MSG_NO_KEY = "Нужна подписка OpenCode Go: ключ opencode-go не найден в auth.json"
MSG_ZEN_ONLY = "Нужна подписка OpenCode Go: найден только ключ Zen, а лимиты доступны на тарифе Go"
MSG_BAD_RESPONSE = "неожиданный ответ API"


class UsageError(Exception):
    """Ошибка, текст которой показывается пользователю как есть."""


def auth_file() -> Path:
    """Путь к auth.json пользователя (тот же, что использует opencode)."""
    return Path.home() / ".local" / "share" / "opencode" / "auth.json"


def load_api_key(path: Path | None = None) -> str:
    """Достаёт ключ подписки Go из auth.json."""
    source = path or auth_file()
    if not source.is_file():
        raise UsageError(MSG_NO_AUTH)

    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UsageError(f"не удалось прочитать auth.json: {exc}") from exc

    if not isinstance(document, dict):
        raise UsageError(MSG_NO_KEY)

    entry = document.get("opencode-go")
    if isinstance(entry, dict) and entry.get("key"):
        return str(entry["key"])

    zen = document.get("opencode")
    if isinstance(zen, dict) and zen.get("key"):
        raise UsageError(MSG_ZEN_ONLY)

    raise UsageError(MSG_NO_KEY)


def message_for_http_status(code: int) -> str:
    """Человеческое объяснение кода ответа сервиса."""
    if code == 401:
        return "ключ отклонён (401) — проверьте auth.json"
    if code == 404:
        return "сервис лимитов недоступен (404) — возможно, изменился API"
    return f"сервис лимитов ответил ошибкой {code}"


def fetch_usage(key: str, url: str = API_URL, timeout: int = TIMEOUT_SECONDS) -> dict:
    """GET /zen/go/v1/usage с ключом в заголовке Authorization."""
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + key,
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
        raise UsageError(f"нет связи с сервисом лимитов: {exc.reason}") from exc
    except TimeoutError as exc:
        raise UsageError("таймаут запроса к сервису лимитов") from exc
    except ValueError as exc:
        raise UsageError(MSG_BAD_RESPONSE) from exc


def normalize(payload: object) -> dict:
    """Оставляет только нужные поля и приводит типы к удобным для QML."""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        raise UsageError(MSG_BAD_RESPONSE)

    result: dict[str, dict] = {}
    for name in WINDOWS:
        item = usage.get(name)
        if not isinstance(item, dict):
            continue
        try:
            percent = float(item.get("percent") or 0)
        except (TypeError, ValueError):
            percent = 0.0
        result[name] = {
            "percent": percent,
            "status": str(item.get("status") or ""),
            "resetsAt": str(item.get("resetsAt") or ""),
        }
    return result


def collect() -> dict:
    """Полный цикл: ключ -> запрос -> нормализация."""
    return normalize(fetch_usage(load_api_key()))


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

    Сервис отдаёт resetsAt с дрейфом в долях секунды (10:56:48.051, 10:56:48.845),
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
    parser.add_argument("--test", action="store_true", help="проверить ключ и показать лимиты в читаемом виде")
    parser.add_argument(
        "--threshold",
        type=int,
        default=None,
        metavar="N",
        help="порог уведомления, %%: один раз на окно сброса, для всех виджетов вместе",
    )
    args = parser.parse_args(argv)

    if args.test:
        try:
            usage = collect()
        except UsageError as exc:
            print("FAIL: " + str(exc))
            return 1
        for name in WINDOWS:
            item = usage.get(name)
            if item:
                print(f"{name:<8} {item['percent']:>5.0f}%  status={item['status']:<8} resets {item['resetsAt']}")
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
        rolling = usage.get("rolling")
        if args.threshold is not None and rolling and rolling["percent"] >= args.threshold:
            document["notify"] = claim(window_key(rolling["resetsAt"], args.threshold))

    publish(document)
    print(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
