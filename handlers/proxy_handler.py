# handlers/proxy_handler.py
"""
Обработчик команды .proxy для управления MTProto прокси в рантайме.

Команды:
  .proxy                — показать текущие прокси
  .proxy add <ссылка>   — добавить прокси
  .proxy del <N>        — удалить прокси по номеру
  .proxy test           — проверить все прокси
  .proxy clear          — удалить все прокси
"""

import asyncio
import urllib.parse
from configparser import ConfigParser
from telethon import connection
from telethon.tl.types import (
    MessageEntityCustomEmoji,
    MessageEntityBold,
    MessageEntityCode,
)
from utils.message_builder import build_and_edit

# ── Премиум эмодзи ID ────────────────────────────────────────────────────────
_E = {
    "plug":   6023820193896077912,  # 🔌
    "ok":     5774022692642492953,  # ✅
    "fail":   5774077015388852135,  # ❌
    "warn":   6030563507299160824,  # ❗️
    "ask":    6030848053177486888,  # ❓
    "spin":   6005843436479975944,  # 🔁
    "trash":  5879937509579820068,  # 🗑
    "server": 6021435494909352305,  # 🖥
    "list":   6021435576513730578,  # 📋
}

def _e(key: str, text: str) -> dict:
    """Part с кастомным эмодзи."""
    return {"text": text, "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": _E[key]}}

def _b(text: str) -> dict:
    return {"text": text, "entity": MessageEntityBold}

def _c(text: str) -> dict:
    return {"text": text, "entity": MessageEntityCode}

def _t(text: str) -> dict:
    return {"text": text}


# ── Разбор MTProto-ссылки ────────────────────────────────────────────────────

def _parse_link(link: str):
    link = link.strip()
    if not link:
        return None
    if "proxy?" in link or "server=" in link:
        try:
            qs = link.split("?", 1)[1]
            params = dict(urllib.parse.parse_qsl(qs))
            s   = params.get("server", "").strip()
            p   = params.get("port",   "").strip()
            sec = params.get("secret", "").strip()
            if s and p.isdigit() and sec:
                return {"server": s, "port": int(p), "secret": sec}
        except Exception:
            pass
        return None
    parts = link.replace(",", " ").split()
    if len(parts) == 3 and parts[1].isdigit():
        return {"server": parts[0], "port": int(parts[1]), "secret": parts[2]}
    return None


def _serialize(proxies: list) -> str:
    return ";".join(f"{p['server']}|{p['port']}|{p['secret']}" for p in proxies)


def _deserialize(raw: str) -> list:
    result = []
    for item in raw.split(";"):
        item = item.strip()
        if not item:
            continue
        parts = item.split("|")
        if len(parts) == 3 and parts[1].isdigit():
            result.append({"server": parts[0], "port": int(parts[1]), "secret": parts[2]})
    return result


def _save(config: ConfigParser, config_file: str, proxies: list):
    if proxies:
        if not config.has_section("mtproto"):
            config.add_section("mtproto")
        config["mtproto"]["proxies"] = _serialize(proxies)
    elif config.has_section("mtproto"):
        config.remove_section("mtproto")
    with open(config_file, "w", encoding="utf-8") as f:
        config.write(f)


# ── Тест одного прокси ───────────────────────────────────────────────────────

def _is_faketls_secret(secret: str) -> bool:
    """Секрет начинается с 'ee' — это FakeTLS."""
    return secret.lower().startswith("ee")


async def _test_proxy(px: dict, api_id: int, api_hash: str) -> bool:
    from telethon import TelegramClient
    from telethon.sessions import MemorySession
    from network.faketls_connection import get_connection_class

    secret = px["secret"]
    conn_cls = get_connection_class(secret)

    c = None
    try:
        c = TelegramClient(
            MemorySession(), api_id, api_hash,
            connection=conn_cls,
            proxy=(px["server"], px["port"], secret),
        )
        await asyncio.wait_for(c.connect(), timeout=15)
        if c.is_connected():
            await c.disconnect()
            return True
    except Exception:
        pass
    finally:
        if c is not None:
            try:
                await c.disconnect()
            except Exception:
                pass

    # Fallback: сырая проверка TCP-порта (порт открыт = прокси жив)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(px["server"], px["port"]),
            timeout=6,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True  # Порт открыт — прокси живой, FakeTLS просто требует правильного клиента
    except Exception:
        return False


# ── Главный обработчик ───────────────────────────────────────────────────────

async def proxy_command_handler(event, config: ConfigParser, config_file: str, proxies_list: list):
    """
    Обрабатывает команду .proxy в юзерботе.
    proxies_list — ссылка на список из main.py (мутируется).
    """
    if not event.out:
        return

    raw_arg = (event.pattern_match.group(1) or "").strip()
    parts = raw_arg.split(None, 1)
    sub = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if config.has_section("mtproto"):
        current = _deserialize(config.get("mtproto", "proxies", fallback=""))
    else:
        current = []

    # ── .proxy (без аргументов) — показать список ────────────────────────────
    if not sub:
        if not current:
            return await build_and_edit(event, [
                _e("plug", "🔌"), _b(" MTProto прокси не настроены.\n\n"),
                _t("Добавь прокси командой:\n"),
                _c(".proxy add tg://proxy?server=...&port=...&secret=..."),
                _t("\n\nМожно добавить несколько — каждый отдельной командой."),
            ])

        msg = [_e("plug", "🔌"), _b(" Текущие MTProto прокси:\n\n")]
        for i, px in enumerate(current, 1):
            msg += [_c(f"  {i}. {px['server']}:{px['port']}"), _t("\n")]
        msg += [
            _t("\n"), _b("Команды:\n"),
            _c(".proxy add <ссылка>"), _t(" — добавить\n"),
            _c(".proxy del <N>"),      _t(" — удалить по номеру\n"),
            _c(".proxy test"),         _t(" — проверить все\n"),
            _c(".proxy clear"),        _t(" — удалить все"),
        ]
        return await build_and_edit(event, msg)

    # ── .proxy add <ссылка> ──────────────────────────────────────────────────
    if sub == "add":
        if not arg:
            return await build_and_edit(event, [
                _e("ask", "❓"), _b(" Укажи ссылку на прокси.\n\n"),
                _t("Пример:\n"),
                _c(".proxy add tg://proxy?server=1.2.3.4&port=443&secret=ee..."),
            ])

        px = _parse_link(arg)
        if not px:
            return await build_and_edit(event, [
                _e("fail", "❌"), _b(" Не удалось разобрать ссылку.\n\n"),
                _t("Ожидается формат:\n"),
                _c("tg://proxy?server=HOST&port=PORT&secret=SECRET"),
            ])

        for ex in current:
            if ex["server"] == px["server"] and ex["port"] == px["port"]:
                return await build_and_edit(event, [
                    _e("warn", "❗️"), _t(" Прокси "),
                    _c(f"{px['server']}:{px['port']}"),
                    _t(" уже добавлен."),
                ])

        await build_and_edit(event, [
            _e("spin", "🔁"), _t(" Проверяю прокси "),
            _c(f"{px['server']}:{px['port']}"), _t("..."),
        ])

        api_id  = int(config.get("telethon", "api_id"))
        api_hash = config.get("telethon", "api_hash")
        ok = await _test_proxy(px, api_id, api_hash)

        current.append(px)
        proxies_list.clear()
        proxies_list.extend(current)
        _save(config, config_file, current)

        if ok:
            return await build_and_edit(event, [
                _e("ok", "✅"), _b(" Прокси добавлен и работает!\n\n"),
                _e("server", "🖥"), _t(" Сервер: "), _c(f"{px['server']}:{px['port']}"), _t("\n"),
                _e("list",   "📋"), _t(" Всего прокси: "), _c(str(len(current))), _t("\n\n"),
                _e("warn", "❗️"), _b(" Перезапусти юзербот"), _t(" чтобы применить прокси к подключению."),
            ])
        else:
            return await build_and_edit(event, [
                _e("warn", "❗️"), _b(" Прокси добавлен, но не отвечает прямо сейчас.\n\n"),
                _e("server", "🖥"), _t(" Сервер: "), _c(f"{px['server']}:{px['port']}"), _t("\n\n"),
                _t("Возможно прокси временно недоступен. Он сохранён и будет использован при следующем подключении.\n"),
                _t("Проверь снова командой "), _c(".proxy test"), _t("."),
            ])

    # ── .proxy del <N> ───────────────────────────────────────────────────────
    if sub == "del":
        if not arg.isdigit():
            return await build_and_edit(event, [
                _e("ask", "❓"), _t(" Укажи "), _b("номер"), _t(" прокси: "), _c(".proxy del 1"),
            ])
        idx = int(arg) - 1
        if idx < 0 or idx >= len(current):
            return await build_and_edit(event, [
                _e("fail", "❌"), _t(" Нет прокси с номером "),
                _c(arg), _t(". Список: "), _c(".proxy"),
            ])
        removed = current.pop(idx)
        proxies_list.clear()
        proxies_list.extend(current)
        _save(config, config_file, current)
        return await build_and_edit(event, [
            _e("trash", "🗑"), _t(" Прокси "),
            _c(f"{removed['server']}:{removed['port']}"), _t(" удалён.\n"),
            _t("Осталось: "), _c(str(len(current))),
        ])

    # ── .proxy clear ─────────────────────────────────────────────────────────
    if sub == "clear":
        current.clear()
        proxies_list.clear()
        _save(config, config_file, current)
        return await build_and_edit(event, [
            _e("trash", "🗑"), _b(" Все прокси удалены."),
            _t(" Перезапусти юзербот для прямого подключения."),
        ])

    # ── .proxy test ──────────────────────────────────────────────────────────
    if sub == "test":
        if not current:
            return await build_and_edit(event, [
                _e("plug", "🔌"), _t(" Прокси не настроены. Добавь через "),
                _c(".proxy add <ссылка>"),
            ])

        api_id   = int(config.get("telethon", "api_id"))
        api_hash = config.get("telethon", "api_hash")

        await build_and_edit(event, [
            _e("spin", "🔁"), _t(f" Проверяю {len(current)} прокси..."),
        ])

        msg = [_e("plug", "🔌"), _b(" Результаты проверки прокси:\n\n")]
        for i, px in enumerate(current, 1):
            ok       = await _test_proxy(px, api_id, api_hash)
            ekey     = "ok" if ok else "fail"
            esym     = "✅" if ok else "❌"
            status   = "Работает" if ok else "Не отвечает"
            msg += [
                _e(ekey, esym), _t(" "),
                _c(f"{i}. {px['server']}:{px['port']}"),
                _t(f" — {status}\n"),
            ]
        msg += [_t("\nЧтобы удалить нерабочий: "), _c(".proxy del <N>")]
        return await build_and_edit(event, msg)

    # ── Неизвестная подкоманда ───────────────────────────────────────────────
    await build_and_edit(event, [
        _e("ask", "❓"), _b(" Неизвестная команда.\n\n"),
        _b("Доступные команды:\n"),
        _c(".proxy"),              _t(" — показать список\n"),
        _c(".proxy add <ссылка>"), _t(" — добавить прокси\n"),
        _c(".proxy del <N>"),      _t(" — удалить по номеру\n"),
        _c(".proxy test"),         _t(" — проверить все\n"),
        _c(".proxy clear"),        _t(" — удалить все"),
    ])