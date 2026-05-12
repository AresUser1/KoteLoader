# modules/heroku_cfg.py
"""
<manifest>
version: 1.0.0
source: local
author: patch
</manifest>

Настройка конфигурации Heroku-модулей через инлайн-панель.

Команда .cfg [имя_модуля] — открывает панель настроек для Heroku-модулей,
у которых объявлен ModuleConfig. Только Heroku-модули (не системный cfg).
"""

import json
from core import register, callback_handler, inline_handler, watcher
from utils import database as db
from utils.message_builder import build_and_edit
from utils.security import check_permission
from telethon.tl.custom import Button


# ── Хранилище состояния ожидания ввода ──────────────────────────────────────
# { sender_id: {"mod": mod_name, "key": key_name, "msg_id": msg_id, "chat_id": chat_id} }
_awaiting_input: dict = {}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_heroku_modules_with_config(client):
    """Возвращает список (mod_key, mod_name, instance) для Heroku-модулей с конфигом."""
    result = []
    modules = getattr(client, "modules", {})
    for key, val in modules.items():
        if not key.startswith("heroku:"):
            continue
        instance = val.get("instance")
        if instance is None:
            continue
        try:
            from compat.loader import ModuleConfig
            cfg = getattr(instance, "config", None)
            if cfg is not None and isinstance(cfg, ModuleConfig) and cfg._meta:
                mod_name = key[len("heroku:"):]
                result.append((key, mod_name, instance))
        except Exception:
            continue
    return result


def _get_validator_type(validator) -> str:
    """Возвращает читаемый тип валидатора."""
    if validator is None:
        return "str"
    name = type(validator).__name__
    mapping = {
        "Boolean": "bool",
        "Integer": "int",
        "Float": "float",
        "String": "str",
        "Series": "list",
        "URL": "url",
        "Choice": "choice",
        "MultiChoice": "multichoice",
        "Hidden": "hidden",
        "Regex": "str",
    }
    return mapping.get(name, name.lower())


def _format_value(value, vtype: str) -> str:
    """Форматирует текущее значение для отображения."""
    if vtype == "bool":
        return "✅ Вкл" if value else "❌ Выкл"
    if vtype == "list":
        if isinstance(value, list):
            if not value:
                return "<пусто>"
            return ", ".join(str(v) for v in value)
    if vtype == "hidden":
        if value:
            return "●●●●●●"
        return "<не задано>"
    if value is None or value == "" or value == []:
        return "<не задано>"
    return str(value)


def _build_module_list_panel(client):
    """Строит текст и кнопки для списка модулей с конфигом."""
    mods = _get_heroku_modules_with_config(client)
    if not mods:
        text = (
            "⚙️ <b>Настройки Heroku-модулей</b>\n\n"
            "<i>Ни один загруженный Heroku-модуль не имеет настраиваемых полей.</i>"
        )
        buttons = [[Button.inline("❌ Закрыть", data="close_panel")]]
        return text, buttons

    text = (
        "⚙️ <b>Настройки Heroku-модулей</b>\n\n"
        f"Найдено модулей с конфигом: <b>{len(mods)}</b>\n"
        "Нажмите на модуль чтобы открыть его настройки:"
    )
    buttons = []
    for _key, mod_name, instance in mods:
        field_count = len(instance.config._meta)
        buttons.append([Button.inline(
            f"⚙️ {mod_name}  [{field_count} {'поле' if field_count == 1 else 'полей' if 2 <= field_count <= 4 else 'полей'}]",
            data=f"hcfg:mod:{mod_name}"
        )])
    buttons.append([Button.inline("❌ Закрыть", data="close_panel")])
    return text, buttons


def _build_module_config_panel(client, mod_name: str):
    """Строит панель конфига конкретного модуля."""
    modules = getattr(client, "modules", {})
    entry = modules.get(f"heroku:{mod_name}")
    if entry is None:
        return None, None

    instance = entry.get("instance")
    if instance is None:
        return None, None

    try:
        from compat.loader import ModuleConfig
        cfg = getattr(instance, "config", None)
        if cfg is None or not isinstance(cfg, ModuleConfig):
            return None, None
    except Exception:
        return None, None

    lines = [f"⚙️ <b>Настройки: {mod_name}</b>\n"]
    buttons = []

    for key, cv in cfg._meta.items():
        vtype = _get_validator_type(cv.validator)
        current = cfg.get(key, cv.default)
        displayed = _format_value(current, vtype)

        # Описание поля
        doc = cv.doc or ""
        if callable(doc):
            try:
                doc = doc()
            except Exception:
                doc = ""
        doc_short = (doc[:60] + "…") if len(doc) > 60 else doc
        lines.append(f"• <b>{key}</b> [{vtype}]: <code>{displayed}</code>")
        if doc_short:
            lines.append(f"  <i>{doc_short}</i>")

        # Кнопки для редактирования
        if vtype == "bool":
            # Для булевых — кнопка-переключатель прямо в панели
            label = f"{'🔴 Выкл' if current else '🟢 Вкл'} ← {key}"
            buttons.append([Button.inline(label, data=f"hcfg:toggle:{mod_name}:{key}")])
        else:
            buttons.append([Button.inline(
                f"✏️ Изменить {key}",
                data=f"hcfg:edit:{mod_name}:{key}"
            )])

    text = "\n".join(lines)
    buttons.append([
        Button.inline("🔙 Назад", data="hcfg:list"),
        Button.inline("❌ Закрыть", data="close_panel"),
    ])
    return text, buttons


# ── Команда .cfg ─────────────────────────────────────────────────────────────

@register("cfg", incoming=True)
async def cfg_command(event):
    """Панель настроек Heroku-модулей.

    Usage: {prefix}cfg [имя_модуля]

    Без аргумента — список всех Heroku-модулей с конфигом.
    С именем — сразу открывает настройки нужного модуля.
    """
    if not check_permission(event, min_level="TRUSTED"):
        return

    from handlers.user_commands import _call_inline_bot
    args = event.message.text.split(maxsplit=1)
    query = f"hcfg_panel:{args[1].strip()}" if len(args) > 1 else "hcfg_panel:"
    await build_and_edit(event, [{"text": "⚙️ Открываю настройки..."}])
    await _call_inline_bot(event, query)


# ── Inline-обработчик (бот присылает сообщение с панелью) ────────────────────

@inline_handler(
    r"^hcfg_panel:(.*)$",
    title="Настройки Heroku-модулей",
    description="Открыть панель конфигурации"
)
async def hcfg_inline(event):
    """Inline-обработчик для панели конфига."""
    client = event.client
    # event здесь — InlineQuery-like объект от нашего бота
    # raw_text приходит из inline_handler wrapper
    raw = getattr(event, "raw_text", "") or ""
    # Вытаскиваем имя модуля из запроса (после двоеточия)
    mod_name = ""
    if ":" in raw:
        mod_name = raw.split(":", 1)[1].strip()

    if mod_name:
        text, buttons = _build_module_config_panel(client.user_client, mod_name)
        if text is None:
            text = f"⚠️ Модуль <b>{mod_name}</b> не найден или не имеет конфига."
            buttons = [[Button.inline("❌ Закрыть", data="close_panel")]]
    else:
        text, buttons = _build_module_list_panel(client.user_client)

    return text, buttons


# ── Callback-обработчики (нажатия кнопок) ────────────────────────────────────

@callback_handler(r"^hcfg:list$")
async def hcfg_list_callback(event):
    """Возврат к списку модулей."""
    await event.answer()
    client = event.client
    user_client = getattr(client, "user_client", client)
    text, buttons = _build_module_list_panel(user_client)
    await event.edit(text, reply_markup=buttons, parse_mode="html")


@callback_handler(r"^hcfg:mod:(.+)$")
async def hcfg_mod_callback(event):
    """Открывает панель конфига конкретного модуля."""
    await event.answer()
    mod_name = event.pattern_match.group(1)
    client = event.client
    user_client = getattr(client, "user_client", client)
    text, buttons = _build_module_config_panel(user_client, mod_name)
    if text is None:
        await event.answer(f"⚠️ Модуль {mod_name} не найден.", show_alert=True)
        return
    await event.edit(text, reply_markup=buttons, parse_mode="html")


@callback_handler(r"^hcfg:toggle:(.+):(.+)$")
async def hcfg_toggle_callback(event):
    """Переключает булевое значение."""
    mod_name = event.pattern_match.group(1)
    key = event.pattern_match.group(2)

    client = event.client
    user_client = getattr(client, "user_client", client)

    modules = getattr(user_client, "modules", {})
    entry = modules.get(f"heroku:{mod_name}")
    if not entry:
        await event.answer("⚠️ Модуль не найден.", show_alert=True)
        return

    instance = entry.get("instance")
    cfg = getattr(instance, "config", None)
    if cfg is None or key not in cfg._meta:
        await event.answer("⚠️ Поле не найдено.", show_alert=True)
        return

    current = cfg.get(key, False)
    new_val = not bool(current)

    # Сохраняем в БД и применяем
    db.set_module_config(mod_name, key, new_val)
    cfg.set_db_value(key, new_val)

    status = "✅ Вкл" if new_val else "❌ Выкл"
    await event.answer(f"{key}: {status}", show_alert=False)

    # Перерисовываем панель
    text, buttons = _build_module_config_panel(user_client, mod_name)
    if text:
        await event.edit(text, reply_markup=buttons, parse_mode="html")


@callback_handler(r"^hcfg:edit:(.+):(.+)$")
async def hcfg_edit_callback(event):
    """Запрашивает новое значение поля через текстовый ввод в чате."""
    mod_name = event.pattern_match.group(1)
    key = event.pattern_match.group(2)

    client = event.client
    user_client = getattr(client, "user_client", client)

    modules = getattr(user_client, "modules", {})
    entry = modules.get(f"heroku:{mod_name}")
    if not entry:
        await event.answer("⚠️ Модуль не найден.", show_alert=True)
        return

    instance = entry.get("instance")
    cfg = getattr(instance, "config", None)
    if cfg is None or key not in cfg._meta:
        await event.answer("⚠️ Поле не найдено.", show_alert=True)
        return

    cv = cfg._meta[key]
    vtype = _get_validator_type(cv.validator)
    current = cfg.get(key, cv.default)
    displayed = _format_value(current, vtype)
    doc = cv.doc or ""
    if callable(doc):
        try:
            doc = doc()
        except Exception:
            doc = ""

    # Запоминаем, что ждём ввода от этого пользователя
    sender_id = event.sender_id
    msg_id = None
    chat_id = None
    try:
        msg_id = event.message.message_id
        chat_id = event.message.chat.id
    except Exception:
        pass

    _awaiting_input[sender_id] = {
        "mod": mod_name,
        "key": key,
        "vtype": vtype,
        "msg_id": msg_id,
        "chat_id": chat_id,
    }

    hint_by_type = {
        "int": "целое число (например: 42)",
        "float": "число (например: 1.5)",
        "list": "значения через запятую (например: one, two, три)",
        "url": "URL (https://...)",
        "bool": "true или false",
        "str": "произвольный текст",
    }
    hint = hint_by_type.get(vtype, "значение")

    tip = (
        f"✏️ <b>Редактирование: {mod_name} → {key}</b>\n\n"
        f"Тип: <code>{vtype}</code>\n"
        f"Текущее значение: <code>{displayed}</code>\n"
    )
    if doc:
        tip += f"Описание: <i>{doc}</i>\n"
    tip += f"\nОтправьте <b>{hint}</b> следующим сообщением.\nДля отмены отправьте /cancel"

    await event.answer()
    await event.edit(tip, reply_markup=[
        [Button.inline("🚫 Отмена", data=f"hcfg:cancel:{mod_name}:{key}")]
    ], parse_mode="html")


@callback_handler(r"^hcfg:cancel:(.+):(.+)$")
async def hcfg_cancel_callback(event):
    """Отмена ввода — возврат к панели модуля."""
    mod_name = event.pattern_match.group(1)
    sender_id = event.sender_id
    _awaiting_input.pop(sender_id, None)

    await event.answer("Отменено.")
    client = event.client
    user_client = getattr(client, "user_client", client)
    text, buttons = _build_module_config_panel(user_client, mod_name)
    if text:
        await event.edit(text, reply_markup=buttons, parse_mode="html")


@callback_handler(r"^hcfg:reset:(.+):(.+)$")
async def hcfg_reset_callback(event):
    """Сбрасывает поле до дефолтного значения."""
    mod_name = event.pattern_match.group(1)
    key = event.pattern_match.group(2)

    client = event.client
    user_client = getattr(client, "user_client", client)

    modules = getattr(user_client, "modules", {})
    entry = modules.get(f"heroku:{mod_name}")
    if not entry:
        await event.answer("⚠️ Модуль не найден.", show_alert=True)
        return

    instance = entry.get("instance")
    cfg = getattr(instance, "config", None)
    if cfg is None or key not in cfg._meta:
        await event.answer("⚠️ Поле не найдено.", show_alert=True)
        return

    default = cfg._meta[key].default
    db.set_module_config(mod_name, key, default)
    cfg.set_db_value(key, default)

    await event.answer(f"↩️ {key} сброшен до дефолта.", show_alert=False)

    text, buttons = _build_module_config_panel(user_client, mod_name)
    if text:
        await event.edit(text, reply_markup=buttons, parse_mode="html")


# ── Watcher: ловит текстовый ввод пользователя ───────────────────────────────


@watcher(outgoing=True)
async def hcfg_input_watcher(event):
    """
    Ловит входящие сообщения от владельца когда активен режим ожидания ввода
    для редактирования поля конфига Heroku-модуля.
    """
    if not event.out:
        return

    # Для исходящих сообщений sender_id — это мы сами
    try:
        me = await event.client.get_me()
        sender_id = me.id
    except Exception:
        return
    state = _awaiting_input.get(sender_id)
    if state is None:
        return

    text = event.message.text or ""

    # Отмена
    if text.strip().lower() in ("/cancel", ".cancel", "отмена"):
        _awaiting_input.pop(sender_id, None)
        await event.edit("🚫 Редактирование отменено.")
        return

    mod_name = state["mod"]
    key = state["key"]
    vtype = state["vtype"]

    # Находим модуль
    user_client = event.client
    modules = getattr(user_client, "modules", {})
    entry = modules.get(f"heroku:{mod_name}")
    if not entry:
        _awaiting_input.pop(sender_id, None)
        return

    instance = entry.get("instance")
    cfg = getattr(instance, "config", None)
    if cfg is None or key not in cfg._meta:
        _awaiting_input.pop(sender_id, None)
        return

    cv = cfg._meta[key]
    raw = text.strip()

    # Парсим значение по типу
    try:
        if vtype == "bool":
            new_val = raw.lower() in ("true", "1", "yes", "да", "вкл", "on")
        elif vtype == "int":
            new_val = int(raw)
        elif vtype == "float":
            new_val = float(raw)
        elif vtype == "list":
            # Пытаемся распарсить как JSON-список или через запятую
            if raw.startswith("["):
                try:
                    new_val = json.loads(raw)
                except Exception:
                    new_val = [v.strip() for v in raw.split(",") if v.strip()]
            else:
                new_val = [v.strip() for v in raw.split(",") if v.strip()]
            # Применяем валидатор Series к каждому элементу если есть inner
            if cv.validator is not None:
                try:
                    new_val = cv.validator.validate(new_val)
                except Exception:
                    pass
        else:
            new_val = raw
            if cv.validator is not None:
                try:
                    new_val = cv.validator.validate(new_val)
                except Exception as ve:
                    await event.edit(f"❌ Ошибка валидации: {ve}")
                    return
    except (ValueError, TypeError) as e:
        await event.edit(f"❌ Неверный формат для типа {vtype}: {e}")
        return

    # Сохраняем
    db.set_module_config(mod_name, key, new_val)
    cfg.set_db_value(key, new_val)
    _awaiting_input.pop(sender_id, None)

    displayed = _format_value(new_val, vtype)
    await event.edit(f"✅ <b>{mod_name} → {key}</b> = <code>{displayed}</code>",
                     parse_mode="html")
