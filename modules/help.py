# modules/help.py
"""
<manifest>
version: 1.4.0
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/help.py
author: Kote
</manifest>

Модуль справки.
Отображает список доступных модулей и детальную информацию о командах.
Если список не влезает в 4096 символов — показывается постранично через бота.
"""

import re
from collections import defaultdict
from telethon.tl.types import (
    MessageEntityBlockquote, MessageEntityCustomEmoji,
    MessageEntityBold, MessageEntityItalic, MessageEntityCode,
)
from core import register
from utils.loader import COMMANDS_REGISTRY, PREFIX, callback_handler
from utils.message_builder import build_and_edit, utf16len
from utils import database as db
from utils.security import check_permission

PAW_EMOJI_ID           = 5084923566848213749
SQUARE_EMOJI_ID_SYSTEM = 4974681956907221809
SQUARE_EMOJI_ID_USER   = 4974508259839836856
INFO_EMOJI_ID          = 5879813604068298387
USAGE_EMOJI_ID         = 5197195523794157505

SYSTEM_MODULES = [
    "admin", "help", "install", "modules", "updater", "ping", "profile",
    "config", "hider", "power", "git_manager", "core_updater", "about",
    "aliases", "twins", "private", "heroku_cfg"
]

MAX_LEN = 4096

# Хранилище страниц: {user_id: {"pages": [...], "page": N}}
_help_sessions: dict = {}


# ── Навигационные кнопки ──────────────────────────────────────────────────────

def _nav_buttons(page: int, total: int, user_id: int):
    row = []
    if page > 0:
        row.append({"text": "◀️", "data": f"help_nav:{user_id}:{page - 1}"})
    row.append({"text": f"{page + 1} / {total}", "data": f"help_nav:{user_id}:{page}"})
    if page < total - 1:
        row.append({"text": "▶️", "data": f"help_nav:{user_id}:{page + 1}"})
    return [row]


# ── Сборка текста одной страницы ─────────────────────────────────────────────

def _build_page(header_parts, sec_title, emoji_id, entries):
    text_parts = []
    ents = []
    offset = 0

    def ap(text, et=None, **kw):
        nonlocal offset
        text_parts.append(text)
        if et:
            l = utf16len(text)
            if l > 0:
                ents.append(et(offset=offset, length=l, **kw))
        offset += utf16len(text)

    for t, et, kw in header_parts:
        ap(t, et, **kw)

    ap(f"{sec_title}\n", MessageEntityBold)
    q_start = offset
    for display_name, cmds in entries:
        ap("▪️", MessageEntityCustomEmoji, document_id=emoji_id)
        ap(f" {display_name}: ( ", MessageEntityBold)
        ap(" | ".join(cmds))
        ap(" )\n")
    q_end = offset
    q_len = q_end - q_start - utf16len("\n")
    if q_len > 0:
        ents.append(MessageEntityBlockquote(offset=q_start, length=q_len, collapsed=True))

    return "".join(text_parts).strip(), ents


def _paginate(header_parts, sec_title, emoji_id, entries):
    """Нарезает секцию на страницы <= MAX_LEN. Модуль целиком переносится на следующую страницу."""
    pages = []
    current = []
    for entry in entries:
        candidate = current + [entry]
        text, ents = _build_page(header_parts, sec_title, emoji_id, candidate)
        if len(text) > MAX_LEN and current:
            pages.append(_build_page(header_parts, sec_title, emoji_id, current))
            current = [entry]
        else:
            current = candidate
    if current:
        pages.append(_build_page(header_parts, sec_title, emoji_id, current))
    return pages


# ── Callback кнопок ───────────────────────────────────────────────────────────

@callback_handler(r"help_nav:\d+:\d+")
async def _help_nav_cb(event):
    data = event.data.decode() if isinstance(event.data, bytes) else event.data
    m = re.match(r"help_nav:(\d+):(\d+)", data)
    if not m:
        return

    user_id = int(m.group(1))
    page    = int(m.group(2))
    session = _help_sessions.get(user_id)

    if not session:
        await event.answer("Сессия истекла, вызови .help снова.", alert=True)
        return

    pages = session["pages"]
    if page < 0 or page >= len(pages):
        await event.answer()
        return

    session["page"] = page
    text, ents = pages[page]
    total = len(pages)
    markup = _nav_buttons(page, total, user_id)

    try:
        # event._client — бот, user_client — юзербот
        bot_client  = event._client
        user_client = getattr(bot_client, "user_client", None)
        if user_client is None:
            await event.answer("Ошибка: нет user_client", alert=True)
            return

        raw     = getattr(event, "_event", event)
        msg_id  = getattr(raw, "message_id", None) or getattr(raw, "msg_id", None)
        chat_id = getattr(raw, "chat_id", None)
        if not chat_id:
            peer = getattr(raw, "peer_id", None)
            if peer:
                if getattr(peer, "channel_id", None):
                    chat_id = -peer.channel_id
                elif getattr(peer, "chat_id", None):
                    chat_id = -peer.chat_id
                elif getattr(peer, "user_id", None):
                    chat_id = peer.user_id

        from telethon.tl.custom import Button as _TgButton
        tg_buttons = []
        for row in markup:
            tg_row = []
            for btn in row:
                raw_data = btn["data"].encode("utf-8")
                tg_row.append(_TgButton.inline(btn["text"], data=raw_data))
            tg_buttons.append(tg_row)

        if msg_id and chat_id:
            # Юзербот редактирует — он в чате и поддерживает custom emoji
            await user_client.edit_message(
                chat_id, msg_id,
                text,
                formatting_entities=ents,
                buttons=tg_buttons,
                link_preview=False,
            )
        await event.answer()
    except Exception as e:
        await event.answer(f"Ошибка: {e}", alert=True)


# ── Команда .help ─────────────────────────────────────────────────────────────

@register("help", incoming=True)
async def help_cmd(event):
    """Показывает справку по командам.

    Usage: {prefix}help [команда]
    """
    if not check_permission(event, min_level="TRUSTED"):
        return

    args = event.pattern_match.group(1)
    hidden_modules = db.get_hidden_modules()

    # ── Помощь по одной команде ───────────────────────────────────────────────
    async def show_command_help(command_name):
        prefix = db.get_setting("prefix", default=".")

        cmd_module = ""
        cmd_info_list = COMMANDS_REGISTRY.get(command_name)
        if cmd_info_list:
            cmd_module = cmd_info_list[0].get("module").lower()

        user_id = event.sender_id
        level   = db.get_user_level(user_id)
        if level == "TRUSTED" and cmd_module not in ["help", "about"]:
            allowed = db.get_setting(f"allowed_mods_{user_id}") or \
                      db.get_setting("allowed_mods_TRUSTED", default="wisp")
            if allowed.lower() != "all":
                al = [m.strip().lower() for m in allowed.split(",")]
                if cmd_module not in al:
                    cmd_info_list = None

        if not cmd_info_list or cmd_module in hidden_modules:
            return await build_and_edit(event, [
                {"text": "❌ "},
                {"text": "Команда ",     "entity": MessageEntityBold},
                {"text": command_name,   "entity": MessageEntityCode},
                {"text": " не найдена или ее модуль скрыт.", "entity": MessageEntityBold},
            ])

        doc = (cmd_info_list[0].get("doc") or "Без описания").strip().replace("{prefix}", prefix)
        module_name = cmd_module.capitalize()

        if "\nUsage:" in doc:
            description = doc.split("\nUsage:")[0].strip()
            usage_text  = doc.split("\nUsage:")[1].strip()
        else:
            description = doc
            usage_text  = ""

        parts = [
            {"text": "🐾", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": PAW_EMOJI_ID}},
            {"text": f" {module_name}", "entity": MessageEntityBold},
            {"text": "\n\n"},
            {"text": "ℹ️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": INFO_EMOJI_ID}},
            {"text": f" {description}", "entity": MessageEntityItalic},
            {"text": "\n\n"},
            {"text": "▫️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": USAGE_EMOJI_ID}},
            {"text": " Использование: ", "entity": MessageEntityBold},
        ]
        if usage_text:
            usage_display = usage_text if ("{prefix}" in usage_text or usage_text.startswith(prefix)) \
                            else f"{prefix}{command_name} {usage_text}"
            parts.append({"text": usage_display, "entity": MessageEntityCode})
        else:
            parts.append({"text": f"{prefix}{command_name}", "entity": MessageEntityCode})

        await build_and_edit(event, parts)

    # ── Список всех команд ────────────────────────────────────────────────────
    async def show_all_commands():
        user_id = event.sender_id
        level   = db.get_user_level(user_id)

        allowed_list = []
        if level == "TRUSTED":
            allowed = db.get_setting(f"allowed_mods_{user_id}") or \
                      db.get_setting("allowed_mods_TRUSTED", default="wisp")
            if allowed.lower() != "all":
                allowed_list = [m.strip().lower() for m in allowed.split(",")]
                allowed_list.extend(["help", "about"])

        visible_modules: dict[str, list] = defaultdict(list)
        for command, cmd_info_list in sorted(COMMANDS_REGISTRY.items()):
            mod_name = cmd_info_list[0]["module"].lower()
            if level == "TRUSTED" and allowed_list and mod_name not in allowed_list:
                continue
            if mod_name not in hidden_modules:
                visible_modules[mod_name].append(command)

        # Шапка (одинакова на всех страницах)
        header_parts = [
            ("🐾", MessageEntityCustomEmoji, {"document_id": PAW_EMOJI_ID}),
            (f" {len(visible_modules)} модулей доступно", MessageEntityBold, {}),
        ]
        if hidden_modules:
            header_parts.append((f", {len(hidden_modules)} скрыто", MessageEntityBold, {}))
        header_parts.append(("\n\n", None, {}))

        sys_entries  = [
            (n.replace("heroku:", "").capitalize(), sorted(visible_modules[n]))
            for n in sorted(visible_modules) if n.lower() in SYSTEM_MODULES
        ]
        user_entries = [
            (n.replace("heroku:", "").capitalize(), sorted(visible_modules[n]))
            for n in sorted(visible_modules) if n.lower() not in SYSTEM_MODULES
        ]

        # Пробуем одно сообщение (как раньше)
        def build_full():
            tp, ents, off = [], [], 0
            def ap(text, et=None, **kw):
                nonlocal off
                tp.append(text)
                if et:
                    l = utf16len(text)
                    if l:
                        ents.append(et(offset=off, length=l, **kw))
                off += utf16len(text)
            for t, et, kw in header_parts:
                ap(t, et, **kw)
            def add_sec(title, emoji_id, entries):
                nonlocal off
                if not entries:
                    return
                ap(f"{title}\n", MessageEntityBold)
                qs = off
                for dn, cmds in entries:
                    ap("▪️", MessageEntityCustomEmoji, document_id=emoji_id)
                    ap(f" {dn}: ( ", MessageEntityBold)
                    ap(" | ".join(cmds))
                    ap(" )\n")
                ql = off - qs - utf16len("\n")
                if ql > 0:
                    ents.append(MessageEntityBlockquote(offset=qs, length=ql, collapsed=True))
                ap("\n")
            add_sec("Системные",        SQUARE_EMOJI_ID_SYSTEM, sys_entries)
            add_sec("Пользовательские", SQUARE_EMOJI_ID_USER,   user_entries)
            return "".join(tp).strip(), ents

        full_text, full_ents = build_full()

        # Влезает — отправляем как обычно
        # Telegram считает длину в UTF-16 единицах, а не в символах Python
        full_utf16_len = len(full_text.encode("utf-16-le")) // 2
        if full_utf16_len <= MAX_LEN:
            if event.out:
                await event.edit(full_text, formatting_entities=full_ents, link_preview=False)
            else:
                await event.respond(full_text, formatting_entities=full_ents, link_preview=False)
            return

        # Не влезает — пагинируем
        pages = []
        pages += _paginate(header_parts, "Системные",        SQUARE_EMOJI_ID_SYSTEM, sys_entries)
        pages += _paginate(header_parts, "Пользовательские", SQUARE_EMOJI_ID_USER,   user_entries)

        if not pages:
            await event.edit("Нет доступных модулей.")
            return

        _help_sessions[user_id] = {"pages": pages, "page": 0}
        text0, ents0 = pages[0]
        total  = len(pages)
        markup = _nav_buttons(0, total, user_id)

        bot_client = getattr(event._client, "bot_client", None)
        if bot_client is None:
            # Бота нет — шлём первую страницу без кнопок
            if event.out:
                await event.edit(text0, formatting_entities=ents0, link_preview=False)
            else:
                await event.respond(text0, formatting_entities=ents0, link_preview=False)
            return

        chat_id  = event.chat_id
        reply_to = event.id

        from telethon.tl.custom import Button as _TgButton
        tg_buttons = []
        for row in markup:
            tg_row = []
            for btn in row:
                raw_data = btn["data"].encode("utf-8")
                tg_row.append(_TgButton.inline(btn["text"], data=raw_data))
            tg_buttons.append(tg_row)

        # Юзербот отправляет с кнопками напрямую — Telethon позволяет юзерботу
        # отправлять inline-кнопки если у него есть бот (через bot_client в сессии).
        # Удаляем оригинальное сообщение команды чтобы не было дублей.
        try:
            if event.out:
                await event.delete()
        except Exception:
            pass

        await event._client.send_message(
            chat_id,
            text0,
            formatting_entities=ents0,
            buttons=tg_buttons,
            reply_to=reply_to,
            link_preview=False,
        )

    if args:
        await show_command_help(args)
    else:
        await show_all_commands()
