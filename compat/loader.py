# compat/loader.py
"""
Слой совместимости с модулями Heroku / Hikka.

Эмулирует:
  - @loader.tds                     → регистрирует класс
  - @loader.command()               → маркирует метод как команду
  - @loader.watcher()               → маркирует метод как watcher
  - loader.ModuleConfig / ConfigValue / validators
  - Module.get() / .set() / .tg_id / .get_prefix() / .strings()
  - Module.inline.form() / .generate_markup() / .bot_username
  - utils.answer() / .escape_html() / .get_args_raw() / .get_chat_id()

Использование в модуле (без изменений кода модуля!):
  from .. import loader, utils        ← эти строки работают автоматически
  @loader.tds
  class MyMod(loader.Module): ...
"""

import re
import html
import logging
import asyncio
import inspect
from typing import Any, Callable, Optional

from . import validators as _validators

logger = logging.getLogger(__name__)

# ── Экспортируем validators как атрибут этого модуля ────────────────────────
validators = _validators


# ═══════════════════════════════════════════════════════════════════════════
# Заглушки для атрибутов которые используют Heroku-модули напрямую
# (loader.LoadError, loader.USER_INSTALL, loader.VALID_PIP_PACKAGES и т.д.)
# ═══════════════════════════════════════════════════════════════════════════

import re as _re

class LoadError(Exception): pass
class SelfUnload(Exception): pass
class SelfSuspend(Exception): pass
class StringLoader:
    """Загрузчик модуля из строки. Эмулирует Hikka StringLoader."""
    def __init__(self, data: str = "", origin: str = "<string>", *a, **kw):
        self.data = data        # исходный код модуля
        self.source = data      # алиас
        self.origin = origin

# Заглушки для pip/apt пакетов — никогда не матчат, чтобы не крашиться
VALID_PIP_PACKAGES = _re.compile(r"(?!x)x")   # never matches
VALID_APT_PACKAGES = _re.compile(r"(?!x)x")
USER_INSTALL = False


# ═══════════════════════════════════════════════════════════════════════════
# ConfigValue / ModuleConfig
# ═══════════════════════════════════════════════════════════════════════════

class ConfigValue:
    """Одно поле конфигурации модуля."""
    def __init__(self, name: str, default: Any = None, doc: str = "",
                 validator=None):
        self.name = name
        self.default = default
        self.doc = doc
        self.validator = validator
        self._value = default  # текущее значение

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        if self.validator is not None:
            try:
                v = self.validator.validate(v)
            except Exception:
                pass
        self._value = v

    def __repr__(self):
        return f"ConfigValue({self.name!r}, default={self.default!r})"


class ModuleConfig(dict):
    """
    Словарь конфигурации модуля.
    Принимает чередующиеся аргументы  name, default, doc  (старый стиль Hikka)
    или список ConfigValue (новый стиль).
    Поддерживает как cfg["key"] так и cfg.key.
    """

    def __init__(self, *args):
        super().__init__()
        self._meta: dict[str, ConfigValue] = {}

        # Новый стиль: ModuleConfig(ConfigValue(...), ConfigValue(...), ...)
        if args and isinstance(args[0], ConfigValue):
            for cv in args:
                self._meta[cv.name] = cv
                self[cv.name] = cv.default
            return

        # Старый стиль: ModuleConfig("key", default, "doc", "key2", ...)
        it = iter(args)
        try:
            while True:
                name = next(it)
                default = next(it)
                doc = next(it)
                # doc может быть callable (lambda) — вызываем
                if callable(doc):
                    try:
                        doc = doc()
                    except Exception:
                        doc = str(doc)
                cv = ConfigValue(name, default, doc)
                self._meta[name] = cv
                self[name] = default
        except StopIteration:
            pass

    def __getattr__(self, key: str):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def set_db_value(self, name: str, value: Any):
        """Устанавливает значение (вызывается при загрузке из БД)."""
        if name in self._meta:
            self._meta[name].value = value
            self[name] = self._meta[name].value
        else:
            self[name] = value

    def get_meta(self, name: str) -> Optional[ConfigValue]:
        return self._meta.get(name)


# ═══════════════════════════════════════════════════════════════════════════
# Декораторы
# ═══════════════════════════════════════════════════════════════════════════

def tds(cls):
    """
    @loader.tds  — регистрирует класс модуля.
    В оригинале это сокращение от «to-be-loaded».
    У нас просто маркируем класс.
    """
    cls._is_heroku_module = True
    return cls


def inline_handler(**kwargs):
    """
    @loader.inline_handler(...) — маркирует метод как Hikka inline-обработчик.
    Регистрирует функцию в INLINE_HANDLERS_REGISTRY с hikka_style=True,
    чтобы bot_callbacks.inline_query_handler передавал ей _HikkaQueryWrapper.
    Имя обработчика берётся из имени метода (убираем суффикс cmd/_cmd).
    """
    def decorator(func):
        import re as _re_ih
        raw = func.__name__
        cmd_name = _re_ih.sub(r"_?cmd$", "", raw, flags=_re_ih.IGNORECASE)

        func._is_inline_handler = True
        func._inline_kwargs = kwargs

        # Поля которые ждёт utils/loader INLINE_HANDLERS_REGISTRY
        func._inline_query_pattern = re.compile(
            r"^" + re.escape(cmd_name) + r"(?:\s+(.*))?$",
            re.IGNORECASE | re.DOTALL,
        )
        _doc = (kwargs.get("ru_doc") or kwargs.get("en_doc") or kwargs.get("doc")
                or next((v for k, v in kwargs.items() if k.endswith("_doc") and v), None)
                or func.__doc__ or "")
        func._inline_title = cmd_name
        func._inline_description = _doc.strip()
        # Флаг для bot_callbacks: передавать _HikkaQueryWrapper вместо event
        func._hikka_style = True
        func._inline_prefix = cmd_name + " "
        return func

    return decorator


def command(alias: str = None, **kwargs):
    """
    @loader.command()  — маркирует метод как команду.
    Имя команды берётся из имени метода: убираем суффиксы _cmd / cmd.
    Примеры: fheta_cmd → fheta, install_module_cmd → install_module, search → search
    Поддерживает ru_doc / en_doc / ua_doc / doc как описание команды.
    """
    def decorator(func):
        raw = func.__name__
        # Убираем суффикс _cmd или cmd (с подчёркиванием или без)
        cmd_name = alias or re.sub(r"_?cmd$", "", raw, flags=re.IGNORECASE)
        func._is_command = True
        func._command_name = cmd_name
        func._command_kwargs = {"outgoing": True, **kwargs}
        # Приоритет описания: ru_doc > en_doc > doc > любой *_doc > __doc__
        _doc = (kwargs.get("ru_doc")
                or kwargs.get("en_doc")
                or kwargs.get("doc")
                or next((v for k, v in kwargs.items() if k.endswith("_doc") and v), None)
                or func.__doc__
                or "")
        func._command_doc = _doc.strip()
        return func

    # Поддерживаем @loader.command без скобок
    if callable(alias):
        func, alias = alias, None
        return decorator(func)

    return decorator


def unrestricted(func):
    """
    @loader.unrestricted — помечает watcher/команду как доступную для всех.
    В Hikka ограничивает фильтрацию по владельцу. У нас — просто заглушка,
    возвращаем функцию как есть.
    """
    return func


def owner(func):
    """@loader.owner — только для владельца. Заглушка."""
    return func


def sudo(func):
    """@loader.sudo — для sudo-пользователей. Заглушка."""
    return func


def support(func):
    """@loader.support — для support-пользователей. Заглушка."""
    return func


def watcher(**kwargs):
    """@loader.watcher() — маркирует метод как watcher (обработчик всех сообщений)."""
    def decorator(func):
        func._is_watcher = True
        func._watcher_kwargs = kwargs
        return func

    if callable(kwargs.get("func")):
        # вызван как @loader.watcher без скобок
        f = kwargs.pop("func")
        f._is_watcher = True
        f._watcher_kwargs = {}
        return f

    return decorator


def callback_handler(**kwargs):
    """
    @loader.callback_handler() — маркирует метод как Hikka callback-обработчик.
    Обрабатывает нажатия inline-кнопок (UpdateBotCallbackQuery).

    Использование:
        @loader.callback_handler()
        async def my_handler(self, call):
            ...

        @loader.callback_handler(pattern=r"^prefix_")
        async def my_handler(self, call):
            ...

    Также поддерживается вызов без скобок:
        @loader.callback_handler
        async def my_handler(self, call): ...
    """
    import re as _re_cb

    def decorator(func):
        func._is_callback_handler = True
        pat_raw = kwargs.get("pattern", ".*")
        if isinstance(pat_raw, str):
            func._callback_pattern = _re_cb.compile(pat_raw)
        else:
            func._callback_pattern = pat_raw
        func._callback_kwargs = kwargs
        return func

    # Поддержка @loader.callback_handler без скобок (передаётся сама функция)
    if len(kwargs) == 1:
        only_val = list(kwargs.values())[0]
        if callable(only_val) and not isinstance(only_val, type):
            f = only_val
            f._is_callback_handler = True
            f._callback_pattern = _re_cb.compile(".*")
            f._callback_kwargs = {}
            return f

    return decorator


# ═══════════════════════════════════════════════════════════════════════════
# Inline-заглушка
# ═══════════════════════════════════════════════════════════════════════════

class _InlineManager:
    """
    Эмулирует self.inline из Heroku/Hikka.
    Реализует form() через обычные кнопки Telethon,
    generate_markup() конвертирует формат кнопок.
    """

    def __init__(self, client, bot_username: str = "", bot_client=None):
        self._client = client
        self.bot_username = bot_username
        self._bot = bot_client  # TelegramClient бота, если запущен

    def _generate_telethon_markup(self, buttons) -> list:
        """Внутренний метод: кнопки в формат Telethon Button (для form/send_message)."""
        from telethon.tl.custom import Button as TgButton

        result = []
        for row in buttons:
            tg_row = []
            for btn in (row if isinstance(row, list) else [row]):
                if not isinstance(btn, dict):
                    continue
                text = btn.get("text", "?")

                if "url" in btn:
                    tg_row.append(TgButton.url(text, btn["url"]))
                elif "callback" in btn:
                    # Сохраняем callback в реестр, передаём data как индекс
                    cb_func = btn["callback"]
                    cb_args = btn.get("args", ())
                    # Нормализуем args: list → tuple, scalar → (scalar,)
                    if isinstance(cb_args, (list, tuple)):
                        cb_args = tuple(cb_args)
                    else:
                        cb_args = (cb_args,)
                    data = _CallbackRegistry.register(cb_func, cb_args)
                    tg_row.append(TgButton.inline(text, data=data.encode("utf-8")))
                elif "copy" in btn:
                    # copy-кнопка — просто текст (Telegram не поддерживает нативно)
                    tg_row.append(TgButton.inline(text, data=f"copy:{btn['copy'][:50]}"))
                else:
                    tg_row.append(TgButton.inline(text, data="noop"))

            if tg_row:
                result.append(tg_row)
        return result

    def generate_markup(self, buttons):
        """Конвертирует кнопки Heroku/Hikka в aiogram InlineKeyboardMarkup.
        Hikka-модули (FHeta и др.) передают результат прямо в reply_markup
        InlineQueryResultArticle — aiogram требует InlineKeyboardMarkup."""
        return self.generate_aiogram_markup(buttons)

    def generate_aiogram_markup(self, buttons):
        """Конвертирует список кнопок Heroku в aiogram InlineKeyboardMarkup.
        Используется для InlineQueryResultArticle и других aiogram-объектов."""
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        kb_rows = []
        for row in buttons:
            kb_row = []
            for btn in (row if isinstance(row, list) else [row]):
                if not isinstance(btn, dict):
                    continue
                text = btn.get("text", "?")

                if "url" in btn:
                    kb_row.append(InlineKeyboardButton(text=text, url=btn["url"]))
                elif "callback" in btn:
                    cb_func = btn["callback"]
                    cb_args = btn.get("args", ())
                    if isinstance(cb_args, (list, tuple)):
                        cb_args = tuple(cb_args)
                    else:
                        cb_args = (cb_args,)
                    data = _CallbackRegistry.register(cb_func, cb_args)
                    kb_row.append(InlineKeyboardButton(text=text, callback_data=data))
                elif "copy" in btn:
                    data = _CallbackRegistry.register(lambda *a: None, ())
                    kb_row.append(InlineKeyboardButton(text=text, callback_data=f"copy:{btn['copy'][:50]}"))
                elif "switch_inline" in btn:
                    kb_row.append(InlineKeyboardButton(
                        text=text,
                        switch_inline_query_current_chat=btn["switch_inline"]
                    ))
                else:
                    kb_row.append(InlineKeyboardButton(text=text, callback_data="noop"))

            if kb_row:
                kb_rows.append(kb_row)

        return InlineKeyboardMarkup(inline_keyboard=kb_rows)

    async def form(self, text: str, message, reply_markup=None, **kwargs):
        """
        self.inline.form() — отправляет сообщение с кнопками via @bot.

        Стратегия (та же что у FHeta):
          1. Регистрируем временный inline-обработчик в INLINE_HANDLERS_REGISTRY.
          2. Userbot вызывает self._client.inline_query(bot, token) → _inline_query_patch
             находит обработчик и возвращает _FakeInlineResult с текстом и кнопками.
          3. result.click(chat_id) → отправляет via @bot через SendInlineBotResultRequest.
          Callback-кнопки работают через _CallbackRegistry / hc_NNN data.
          Fallback: бот шлёт send_message (если участник чата), затем userbot без кнопок.
        """
        import uuid as _uuid
        from telethon.tl.custom import Button as TgButton

        # Конвертируем кнопки в Telethon-формат (для fallback)
        tg_buttons = None
        if reply_markup:
            tg_buttons = self._generate_telethon_markup(reply_markup)

        # Определяем chat_id и reply_to
        try:
            chat_id = message.chat_id
        except Exception:
            chat_id = getattr(message, "id", None)
        reply_to = getattr(message, "id", None)

        # ── Метод 1: via @bot через _inline_query_patch (как FHeta) ─────
        if self._client is not None and self.bot_username and chat_id:
            try:
                import re as _re_form
                from utils.loader import INLINE_HANDLERS_REGISTRY as _IHR

                _token = f"__form_{_uuid.uuid4().hex[:12]}"

                # Кнопки для inline-результата — Telethon-формат
                _form_buttons = tg_buttons
                _form_text    = text

                async def _form_inline_handler(tl_event):
                    """Raw Telethon InlineQuery handler — строит article с кнопками."""
                    _btns = _form_buttons  # Telethon Button list
                    result = tl_event.builder.article(
                        title="●",
                        description=(_form_text[:50] if _form_text else ""),
                        text=_form_text,
                        parse_mode="html",
                        buttons=_btns,
                        link_preview=False,
                    )
                    await tl_event.answer([result], cache_time=0, private=True)

                _pat = _re_form.compile(rf"^{_re_form.escape(_token)}$")
                _IHR[_pat] = {
                    "func": _form_inline_handler,
                    "title": "form",
                    "description": "",
                    "hikka_style": False,
                    "prefix": _token,
                    "_one_shot": True,
                    "_raw_handler": True,
                }

                # Используем _inline_query_patch — он вызовет _form_inline_handler
                # и вернёт _FakeInlineResult который умеет click() via @bot
                results = await self._client.inline_query(self.bot_username, _token)
                logger.debug(f"[compat] inline.form: inline_query returned {len(results) if results else 0} results")

                if results:
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    await results[0].click(chat_id, reply_to=reply_to)
                    logger.info("[compat] inline.form: sent via native inline OK")
                    return
                else:
                    logger.warning("[compat] inline.form: inline_query вернул пустой список")
                    _IHR.pop(_pat, None)

            except Exception as _e_form:
                logger.warning(f"[compat] inline.form via inline_query failed: {_e_form}")
                import traceback as _tb_form
                logger.debug(_tb_form.format_exc())

        # ── Метод 2: бот шлёт напрямую send_message (если участник чата) ─
        if self._bot is not None and chat_id:
            _bot_in_chat = True
            try:
                await self._bot.get_input_entity(chat_id)
            except Exception:
                _bot_in_chat = False
            if _bot_in_chat:
                try:
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    await self._bot.send_message(
                        chat_id, text,
                        buttons=tg_buttons, parse_mode="html",
                        link_preview=False,
                    )
                    logger.info("[compat] inline.form: sent via bot.send_message OK")
                    return
                except Exception as _e2:
                    logger.warning(f"[compat] inline.form bot.send_message failed: {_e2}")

        # ── Метод 3: userbot edit/send (без callback-кнопок) ─────────────
        async def _send(txt, btns):
            try:
                await message.edit(txt, buttons=btns, parse_mode="html", link_preview=False)
                return True
            except Exception:
                pass
            try:
                await self._client.send_message(
                    chat_id, txt, buttons=btns, parse_mode="html", link_preview=False)
                return True
            except Exception:
                return False

        if await _send(text, tg_buttons):
            return

        # Последний fallback: просто текст с перечислением кнопок
        hint_parts = []
        if reply_markup:
            for row in reply_markup:
                for btn in (row if isinstance(row, list) else [row]):
                    if isinstance(btn, dict) and "text" in btn:
                        hint_parts.append(btn["text"])
        hint = ""
        if hint_parts:
            hint = "\n\n<b>Действия:</b> " + " | ".join(
                f"<code>{h}</code>" for h in hint_parts
            )
        if not await _send(text + hint, None):
            logger.warning("[compat] inline.form: все методы отправки не удались")

    @property
    def bot(self):
        """Эмулирует self.inline.bot. Передаём bot_client для редактирования inline-сообщений."""
        return _BotStub(self._client, bot_client=self._bot)


class _BotStub:
    """
    Эмулирует self.inline.bot (aiogram Bot) для Hikka/Heroku модулей.

    Поддерживает:
      - edit_message_text(chat_id, message_id, text, ...)        -- обычное сообщение
      - edit_message_text(inline_message_id=..., text=..., ...)  -- inline-режим
      - send_message(chat_id, text, ...)
      - answer_callback_query(query_id, text, ...)
      - reply_markup конвертируется из aiogram InlineKeyboardMarkup / Telethon Button list
    """
    def __init__(self, client, bot_client=None):
        self._client = client
        self._bot = bot_client  # TelegramClient бота — для EditInlineBotMessageRequest

    @staticmethod
    def _convert_markup(reply_markup):
        """
        Принимает reply_markup в любом формате и возвращает Telethon-совместимый список.
        - None                         -> None
        - [[TgButton, ...], ...]       -> оставляем как есть
        - aiogram InlineKeyboardMarkup -> конвертируем в [[TgButton, ...], ...]
        """
        if reply_markup is None:
            return None
        if hasattr(reply_markup, "inline_keyboard"):
            from telethon.tl.custom import Button as TgButton
            rows = []
            for row in reply_markup.inline_keyboard:
                tg_row = []
                for btn in row:
                    text = getattr(btn, "text", "?")
                    url = getattr(btn, "url", None)
                    cb  = getattr(btn, "callback_data", None)
                    if url:
                        tg_row.append(TgButton.url(text, url))
                    elif cb:
                        data = cb.encode("utf-8")[:64]
                        tg_row.append(TgButton.inline(text, data=data))
                    else:
                        tg_row.append(TgButton.inline(text, data=b"noop"))
                if tg_row:
                    rows.append(tg_row)
            return rows or None
        # Уже Telethon-формат
        return reply_markup

    @staticmethod
    def _lo_to_bool(lo):
        """Конвертирует aiogram LinkPreviewOptions -> link_preview bool для Telethon."""
        if lo is None:
            return False
        if hasattr(lo, "is_disabled"):
            return not lo.is_disabled
        return False

    async def edit_message_text(self, chat_id=None, message_id=None, text="",
                                 inline_message_id=None, reply_markup=None,
                                 parse_mode="HTML", link_preview_options=None, **kwargs):
        import traceback as _tb
        buttons = self._convert_markup(reply_markup)
        lp = self._lo_to_bool(link_preview_options)
        try:
            if inline_message_id:
                # EditInlineBotMessageRequest работает ТОЛЬКО от аккаунта бота.
                # Если self._bot is None — нельзя редактировать inline-сообщение вообще.
                if self._bot is None:
                    logger.warning("[compat] edit_message_text: bot_client is None — cannot edit inline message. "
                                   "Убедись что бот запущен и bot_client передан в _InlineManager.")
                    return
                editor = self._bot
                logger.info(f"[compat] edit_message_text: inline mode via bot, "
                             f"iid_type={type(inline_message_id).__name__}, iid={inline_message_id!r}")
                try:
                    from telethon import functions as tl_functions
                    from telethon.tl.types import InputBotInlineMessageID, InputBotInlineMessageID64
                    import struct

                    iid = inline_message_id

                    # Уже готовый TL-объект (InputBotInlineMessageID / InputBotInlineMessageID64)
                    # — приходит из UpdateInlineBotCallbackQuery.msg_id напрямую
                    if hasattr(iid, "CONSTRUCTOR_ID"):
                        pass  # уже TL-объект, используем как есть
                    elif isinstance(iid, bytes):
                        if len(iid) == 20:
                            dc_id, msg_id, access_hash = struct.unpack("<iiq", iid)
                            iid = InputBotInlineMessageID(dc_id=dc_id, id=msg_id, access_hash=access_hash)
                        elif len(iid) == 24:
                            dc_id, msg_id, owner_id, access_hash = struct.unpack("<iiiq", iid)
                            iid = InputBotInlineMessageID64(dc_id=dc_id, id=msg_id, owner_id=owner_id, access_hash=access_hash)
                        else:
                            logger.warning(f"[compat] edit_message_text: unknown bytes iid len={len(iid)}")
                            return
                    elif isinstance(iid, str):
                        import base64
                        try:
                            raw = base64.urlsafe_b64decode(iid + "==")
                            if len(raw) == 20:
                                dc_id, msg_id, access_hash = struct.unpack("<iiq", raw)
                                iid = InputBotInlineMessageID(dc_id=dc_id, id=msg_id, access_hash=access_hash)
                            elif len(raw) == 24:
                                dc_id, msg_id, owner_id, access_hash = struct.unpack("<iiiq", raw)
                                iid = InputBotInlineMessageID64(dc_id=dc_id, id=msg_id, owner_id=owner_id, access_hash=access_hash)
                            else:
                                logger.warning(f"[compat] edit_message_text: unknown base64 iid len={len(raw)}")
                                return
                        except Exception as _pe:
                            logger.warning(f"[compat] edit_message_text: base64 parse failed: {_pe}")
                            return
                    else:
                        logger.warning(f"[compat] edit_message_text: unknown iid type {type(iid)}")
                        return

                    logger.info(f"[compat] edit_message_text: parsed iid={iid!r}")

                    # Собираем reply_markup
                    tl_markup = None
                    if buttons:
                        try:
                            tl_markup = editor.build_reply_markup(buttons)
                        except Exception as _mke:
                            logger.warning(f"[compat] edit_message_text: build_reply_markup failed: {_mke}")

                    # Парсим HTML/MD -> entities
                    from telethon.extensions import html as tl_html, markdown as tl_md
                    if parse_mode and parse_mode.lower() in ("html", "htm"):
                        msg_text, entities = tl_html.parse(text)
                    else:
                        msg_text, entities = tl_md.parse(text)

                    await editor(tl_functions.messages.EditInlineBotMessageRequest(
                        id=iid,
                        message=msg_text,
                        entities=entities,
                        reply_markup=tl_markup,
                        no_webpage=not lp,
                    ))
                    logger.info("[compat] edit_message_text: EditInlineBotMessageRequest OK")
                except Exception as _e:
                    logger.warning(f"[compat] bot.edit_message_text (inline) FAILED: {_e}\n{_tb.format_exc()}")
            elif message_id and chat_id is not None and chat_id != 0:
                # Бот отправил сообщение от своего имени (bot.send_message в click()),
                # значит только бот может его редактировать.
                # chat_id может совпадать с user_id (Saved Messages) — нужен правильный peer.
                if self._bot is None:
                    logger.warning("[compat] edit_message_text: bot_client is None, cannot edit")
                    return
                try:
                    # Для Saved Messages chat_id == sender user_id.
                    # Бот не может писать в Saved Messages юзера напрямую по user_id —
                    # нужно открыть диалог с юзером. Используем entity из bot_client.
                    from telethon.tl.types import InputPeerUser, InputPeerChannel, InputPeerChat
                    try:
                        peer_entity = await self._bot.get_input_entity(int(chat_id))
                    except Exception:
                        peer_entity = int(chat_id)

                    logger.info(f"[compat] edit_message_text: bot editing msg_id={message_id} in peer={peer_entity!r}")
                    await self._bot.edit_message(
                        peer_entity,
                        message_id,
                        text,
                        parse_mode="html",
                        buttons=buttons,
                        link_preview=lp,
                    )
                    logger.info("[compat] edit_message_text: bot.edit_message OK")
                except Exception as _bot_edit_err:
                    logger.warning(f"[compat] edit_message_text via bot failed: {_bot_edit_err}\n{_tb.format_exc()}")
            else:
                logger.warning("[compat] bot.edit_message_text: нет chat_id+message_id и нет inline_message_id")
        except Exception as e:
            logger.warning(f"[compat] bot.edit_message_text failed: {e}\n{_tb.format_exc()}")

    async def send_message(self, chat_id, text, reply_markup=None,
                           parse_mode="HTML", link_preview_options=None, **kwargs):
        buttons = self._convert_markup(reply_markup)
        lp = self._lo_to_bool(link_preview_options)
        try:
            await self._client.send_message(
                chat_id, text,
                parse_mode="html",
                buttons=buttons,
                link_preview=lp,
            )
        except Exception as e:
            logger.warning(f"[compat] bot.send_message failed: {e}")

    async def answer_callback_query(self, query_id, text="", show_alert=False, **kwargs):
        # userbot не может answer() на чужой callback_query — логируем и идём дальше
        logger.debug(f"[compat] bot.answer_callback_query query_id={query_id} text={text!r}")


# ═══════════════════════════════════════════════════════════════════════════
# FakeInlineResult — эмуляция results от self._client.inline_query()
# ═══════════════════════════════════════════════════════════════════════════

class _FakeInlineResult:
    """
    Эмулирует один результат из self._client.inline_query() для Hikka-модулей.

    Hikka после inline_query делает:
      results = await self._client.inline_query(bot, query)
      results[0].title            -- проверяет заголовок
      await results[0].click(chat_id, reply_to=...)  -- отправляет в чат

    click() отправляет message через userbot с кнопками из buttons.
    """
    def __init__(self, title: str, message: str, buttons, client=None, bot_client=None,
                 inline_query: str = "", _raw_tl_result=None):
        self.title = title
        self._message = message
        self._buttons = buttons
        self._client = client           # userbot (telethon)
        self._bot = bot_client          # TelegramClient бота
        self._inline_query = inline_query  # оригинальный текст inline-запроса для native inline
        self._raw_tl_result = _raw_tl_result  # готовый TL результат GetInlineBotResultsRequest

    def _to_telethon_buttons(self, buttons):
        if buttons is None:
            return None
        if isinstance(buttons, list):
            return buttons
        try:
            # Уже готовые Telethon кнопки — возвращаем как есть
            if isinstance(buttons, list):
                return buttons
            from aiogram.types import InlineKeyboardMarkup as AioMarkup
            from telethon.tl.custom import Button as TgButton
            if isinstance(buttons, AioMarkup):
                result = []
                for row in buttons.inline_keyboard:
                    tg_row = []
                    for btn in row:
                        if btn.url:
                            tg_row.append(TgButton.url(btn.text, btn.url))
                        elif btn.callback_data:
                            tg_row.append(TgButton.inline(btn.text, data=btn.callback_data))
                        elif btn.switch_inline_query_current_chat is not None:
                            tg_row.append(TgButton.switch_inline(btn.text, same_peer=True))
                        else:
                            tg_row.append(TgButton.inline(btn.text, data="noop"))
                    if tg_row:
                        result.append(tg_row)
                return result or None
        except Exception as e:
            logger.warning(f"[compat] _to_telethon_buttons failed: {e}")
        return None

    async def click(self, chat_id, reply_to=None, **kwargs):
        """Отправляет inline-результат в чат.

        Стратегия:
        - Если есть _raw_tl_result — используем его напрямую (для inline.form).
        - Saved Messages: userbot шлёт сам с кнопками.
        - Группа где бот участник: bot.send_message.
        - Группа где бот НЕ участник: нативный inline via @bot.
        """
        # ── Быстрый путь: уже есть готовый TL результат (inline.form) ───
        if self._raw_tl_result is not None:
            try:
                from telethon import functions as _tl_fn, types as _tl_types
                _iq = self._raw_tl_result
                chosen = _iq.results[0]
                peer = await self._client.get_input_entity(chat_id)
                reply_to_hdr = None
                if reply_to:
                    try:
                        reply_to_hdr = _tl_types.InputReplyToMessage(reply_to_msg_id=reply_to)
                    except Exception:
                        pass
                await self._client(_tl_fn.messages.SendInlineBotResultRequest(
                    peer=peer,
                    query_id=_iq.query_id,
                    id=chosen.id,
                    reply_to=reply_to_hdr,
                ))
                logger.info("[compat] _FakeInlineResult.click: sent via _raw_tl_result OK")
                return
            except Exception as _rte:
                logger.warning(f"[compat] _FakeInlineResult.click _raw_tl_result failed: {_rte}")
                # fallback ниже
        if self._client is None and self._bot is None:
            logger.warning("[compat] _FakeInlineResult.click: нет клиента")
            return

        # Определяем свой user_id чтобы понять Saved Messages
        my_id = None
        try:
            me = await self._client.get_me()
            my_id = me.id
        except Exception:
            pass

        is_saved = (my_id is not None and int(chat_id) == my_id)

        # Для Saved Messages — userbot шлёт напрямую с кнопками (через userbot, не бот)
        if is_saved:
            try:
                tg_buttons = self._to_telethon_buttons(self._buttons)
                await self._client.send_message(
                    "me",
                    self._message,
                    parse_mode="html",
                    buttons=tg_buttons,
                    reply_to=reply_to,
                    link_preview=False,
                )
                logger.info("[compat] click: sent to Saved Messages via userbot OK")
                return
            except Exception as e:
                logger.warning(f"[compat] click: saved messages userbot failed: {e}")

        # Для групп/каналов — сначала пробуем bot.send_message (если бот в чате)
        if self._bot is not None and not is_saved:
            try:
                tg_buttons = self._to_telethon_buttons(self._buttons)
                sent = await self._bot.send_message(
                    chat_id,
                    self._message,
                    parse_mode="html",
                    buttons=tg_buttons,
                    reply_to=reply_to,
                    link_preview=False,
                )
                logger.info(f"[compat] click: sent via bot.send_message OK msg_id={getattr(sent, 'id', None)}")
                return
            except Exception as e:
                logger.warning(f"[compat] click: bot.send_message failed ({e}), trying native inline")

        # Нативный inline — userbot шлёт via @bot (работает в группах без бота)
        # При нажатии кнопки inline_message_id будет в callback для групп
        if self._bot is not None and self._client is not None:
            try:
                from telethon import functions, types as tl_types
                bot_me = await self._bot.get_me()
                bot_input = await self._client.get_input_entity(bot_me.username)
                iq_result = await self._client(functions.messages.GetInlineBotResultsRequest(
                    bot=bot_input,
                    peer=bot_input,
                    query=self._inline_query or "",
                    offset="",
                ))
                if iq_result and iq_result.results:
                    chosen = iq_result.results[0]
                    for r in iq_result.results:
                        if getattr(r, "title", "") == self.title:
                            chosen = r
                            break
                    peer = await self._client.get_input_entity(chat_id)
                    reply_to_header = None
                    if reply_to:
                        reply_to_header = tl_types.InputReplyToMessage(reply_to_msg_id=reply_to)
                    await self._client(functions.messages.SendInlineBotResultRequest(
                        peer=peer,
                        query_id=iq_result.query_id,
                        id=chosen.id,
                        reply_to=reply_to_header,
                    ))
                    logger.info("[compat] click: sent via native inline OK")
                    return
            except Exception as e:
                logger.warning(f"[compat] click: native inline failed: {e}")

        # Последний fallback: userbot без кнопок
        if self._client is not None:
            try:
                await self._client.send_message(
                    chat_id,
                    self._message,
                    parse_mode="html",
                    reply_to=reply_to,
                    link_preview=False,
                )
                logger.info("[compat] click: sent via userbot (no buttons) OK")
            except Exception as e:
                logger.warning(f"[compat] _FakeInlineResult.click all fallbacks failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# Callback Registry (для кнопок)
# ═══════════════════════════════════════════════════════════════════════════

class _CallbackRegistry:
    _store: dict[str, tuple] = {}
    _counter = 0

    @classmethod
    def register(cls, func: Callable, args: tuple) -> str:
        cls._counter += 1
        key = f"hc_{cls._counter}"
        cls._store[key] = (func, args)
        return key

    @classmethod
    def get(cls, key: str):
        return cls._store.get(key)


# ═══════════════════════════════════════════════════════════════════════════
# Базовый класс Module (расширенный)
# ═══════════════════════════════════════════════════════════════════════════

class _CallableStrings(dict):
    """
    Словарь который можно вызвать как функцию.
    Модули Hikka/Heroku используют strings и как dict (strings["key"])
    и как callable (self.strings("key", **kwargs)).
    Этот класс поддерживает оба варианта одновременно.
    """
    def __call__(self, key: str, **kwargs) -> str:
        value = self.get(key, key)
        if kwargs:
            try:
                value = value.format(**kwargs)
            except Exception:
                pass
        return value


class Module:
    """
    Базовый класс для Heroku/Hikka-совместимых модулей.
    Расширяет минимальный Module из KoteLoader.
    """
    strings: _CallableStrings = _CallableStrings({"name": "Unknown"})

    def __init__(self):
        self.client = None
        self._client = None  # алиас для совместимости с Hikka (self._client == self.client)
        self.db = None
        self.config: ModuleConfig = ModuleConfig()
        self._tg_id: Optional[int] = None
        self.inline: _InlineManager = _InlineManager(None)

    # ── Данные модуля (self.get / self.set) ──────────────────────────────
    def get(self, key: str, default: Any = None) -> Any:
        """self.get("key") — читает данные модуля из БД."""
        if self.db is None:
            return default
        mod_name = self._get_module_name()
        return self.db.get_module_data(mod_name, key, default=default)

    def set(self, key: str, value: Any):
        """self.set("key", value) — сохраняет данные модуля в БД."""
        if self.db is None:
            return
        mod_name = self._get_module_name()
        self.db.set_module_data(mod_name, key, value)

    def _get_module_name(self) -> str:
        cls_strings = getattr(self, "strings", {})
        if isinstance(cls_strings, dict):
            return cls_strings.get("name", self.__class__.__name__)
        return self.__class__.__name__

    # ── tg_id ────────────────────────────────────────────────────────────
    @property
    def tg_id(self) -> Optional[int]:
        return self._tg_id

    # ── Префикс ──────────────────────────────────────────────────────────
    def get_prefix(self) -> str:
        if self.db is not None:
            return self.db.get_setting("prefix", default=".")
        return "."

    # ── strings() как callable ───────────────────────────────────────────
    # Проблема: дочерние модули объявляют strings = {"name": ...} как атрибут
    # класса, что перекрывает этот метод. Решение — сделать strings объектом
    # который одновременно является словарём И callable.

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Если дочерний класс объявил strings как обычный dict — оборачиваем
        if "strings" in cls.__dict__ and isinstance(cls.__dict__["strings"], dict):
            cls.strings = _CallableStrings(cls.__dict__["strings"])

    # ── client_ready ─────────────────────────────────────────────────────
    def lookup(self, module_name: str):
        """
        self.lookup("loader") — Hikka API для поиска модуля по имени.
        Возвращаем заглушку загрузчика с allmodules.register_module().
        """
        if module_name.lower() == "loader":
            return _LoaderModuleStub(self.client)
        # Для других модулей — ищем в client.modules
        if self.client:
            mods = getattr(self.client, "modules", {})
            for k, v in mods.items():
                if k.lower().replace("heroku:", "") == module_name.lower():
                    instance = v.get("instance")
                    if instance:
                        return instance
        return None

    async def client_ready(self, client, db):
        """Вызывается при загрузке модуля."""
        pass



# ═══════════════════════════════════════════════════════════════════════════
# Hikka loader API — реальные реализации для self.lookup("loader")
# ═══════════════════════════════════════════════════════════════════════════


class _StorageFetcher:
    """
    Эмулирует lm._storage из Hikka.
    Реально скачивает исходный код модуля по URL через aiohttp.
    """

    async def fetch(self, url: str, auth=None) -> str:
        import aiohttp
        logger.info(f"[compat] StorageFetcher.fetch: downloading {url!r}")
        headers = {}
        if auth:
            import base64
            encoded = base64.b64encode(str(auth).encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    resp.raise_for_status()
                    text = await resp.text()
                    logger.info(f"[compat] StorageFetcher.fetch: OK, {len(text)} chars")
                    return text
        except Exception as _fe:
            logger.warning(f"[compat] StorageFetcher.fetch FAILED: {_fe}")
            raise


class _LoaderConfig:
    """
    Эмулирует lm.config из Hikka — простой dict-like объект.
    """

    def __init__(self):
        self._data: dict = {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value


class _AllModules:
    """
    Реализует Hikka allmodules API для self.lookup("loader").allmodules.

    Методы:
      register_module(spec, module_name, origin, save_fs)
        — загружает модуль из исходника (ModuleSpec со StringLoader) в память,
          регистрирует его команды/обработчики в KoteLoader и возвращает instance.
      unload_module(class_name)
        — выгружает ранее зарегистрированный модуль.
      send_config_one(instance)
        — применяет конфиг из БД к instance.config (уже применён при load,
          но вызывается повторно — безвредно).
      send_ready_one(instance, **kwargs)
        — вызывает client_ready на instance (уже вызван, повторный вызов — noop).
      modules (list)
        — список живых инстансов для .remove() при ошибке.
    """

    def __init__(self, client):
        self._client = client
        self.fully_loaded = True
        # Список активных инстансов, зарегистрированных через register_module.
        # FHeta делает lm.allmodules.modules.remove(instance) при ошибке.
        self.modules: list = []

    async def register_module(self, spec, module_name: str,
                              origin: str = "", save_fs: bool = False,
                              **kwargs):
        """
        Загружает модуль из ModuleSpec (spec.loader — StringLoader с исходником).
        Регистрирует обработчики в KoteLoader и возвращает живой instance.
        """
        import sys, types, importlib.util, inspect
        from telethon import events
        from utils.loader import COMMANDS_REGISTRY, CALLBACK_REGISTRY, WATCHERS_REGISTRY, PREFIX
        from compat.loader import Module as HerokuModule, ModuleConfig, _InlineManager, _CallbackRegistry
        from utils import database as db_module

        # ── 1. Получаем исходный код из spec ────────────────────────────
        source = None
        if hasattr(spec, "loader"):
            for attr in ("data", "source"):
                v = getattr(spec.loader, attr, None)
                if v is not None:
                    source = v
                    break
        if source is None:
            raise ValueError("register_module: не удалось получить исходник из spec.loader")

        # ── 2. Определяем имя пакета/модуля ────────────────────────────
        # module_name может быть "heroku.modules.GoyPulseMod"
        # Берём последнюю часть как имя класса/файла
        uid = module_name.split(".")[-1]
        mod_full_name = f"heroku_compat_pkg.modules.{uid}"

        # ── 2b. save_fs=True (или всегда): сохраняем исходник на диск ───
        # ── 2b. Всегда сохраняем исходник на диск ──────────────────────
        # FHeta передаёт save_fs=False, но файл нужен для перезапуска.
        # Приоритет имени файла:
        #   1. Имя из origin URL (напр. "yt.py" из "…/yt.py")
        #   2. strings["name"] → snake_case первого слова ("YTMusic" → "yt_music.py")
        #   3. Имя класса → snake_case ("YTMusicMod" → "yt_music_mod.py")
        #   4. uid как fallback
        # НЕ ищем совпадение с существующими файлами — это вызывало перезапись
        # чужих файлов (Banners.py при установке YTMusic).
        import ast as _ast_fs, re as _re_fs
        from pathlib import Path as _Path
        _modules_dir = _Path(__file__).parent.parent / "modules"

        # Шаг A: имя из origin URL
        _origin_str = origin or ""
        _from_url = None
        _url_basename = _origin_str.split("/")[-1].split("?")[0]
        if _url_basename.endswith(".py") and not _url_basename.startswith("_"):
            _from_url = _url_basename[:-3]  # "yt.py" → "yt"

        # Шаг B: strings["name"] из исходника
        _mod_string_name = None
        _class_name = None
        try:
            _tree_s = _ast_fs.parse(source)
            for _n_s in _tree_s.body:
                if isinstance(_n_s, _ast_fs.ClassDef) and _class_name is None:
                    _class_name = _n_s.name
        except Exception:
            pass
        try:
            _ms = _re_fs.search(r'["\'`]name["\'`]\s*:\s*["\'`]([^"\'`]+)["\'`]', source)
            if _ms:
                _mod_string_name = _ms.group(1)
        except Exception:
            pass

        # Определяем имя файла
        if _from_url:
            _save_stem = _from_url
        elif _mod_string_name:
            # "YTMusic" → "ytmusic", "GoyPulse V9" → "goypulse"
            _save_stem = _re_fs.sub(r'\s+v?\d+[\d.]*$', '', _mod_string_name.lower())
            _save_stem = _re_fs.sub(r'[^a-z0-9]+', '_', _save_stem).strip('_')
        elif _class_name:
            _save_stem = _re_fs.sub(r'(?<!^)(?=[A-Z])', '_', _class_name).lower().strip('_')
        else:
            _save_stem = uid.lower()

        _target_path = _modules_dir / f"{_save_stem}.py"
        _target_path.write_text(source, encoding="utf-8")
        _registered_file_name = _save_stem  # передаём вниз для file_name в client.modules
        logger.info(f"[compat] register_module save_fs: сохранён {_target_path} "
                        f"(class={_class_name}, strings_name={_mod_string_name})")

        # ── 2c. Выгружаем старый инстанс этого модуля (если есть) ────────
        # Без этого старые обработчики остаются активными параллельно с новыми.
        # uid может быть "__extmod_uuid" — поэтому ищем по _class_name и
        # _mod_string_name которые мы уже вытащили выше (при save_fs).
        # Если save_fs=False — вытащим их здесь.
        if not save_fs:
            import ast as _ast_fs2, re as _re_fs2
            _class_name = None
            _mod_string_name = None
            try:
                _tree2 = _ast_fs2.parse(source)
                for _n2 in _tree2.body:
                    if isinstance(_n2, _ast_fs2.ClassDef):
                        _class_name = _n2.name
                        break
            except Exception:
                pass
            try:
                _m2 = _re_fs2.search(r'["\']name["\']\s*:\s*["\']([^"\']+)["\']', source)
                if _m2:
                    _mod_string_name = _m2.group(1)
            except Exception:
                pass

        _mods = getattr(self._client, "modules", {}) if self._client else {}
        _old_key = None
        _uid_is_real = not uid.startswith("__extmod_")
        for _k, _v in list(_mods.items()):
            _inst = _v.get("instance")
            if _inst is None:
                continue
            _cls = _inst.__class__.__name__
            _mname = _inst._get_module_name() if hasattr(_inst, "_get_module_name") else _cls
            if _class_name and _cls.lower() == _class_name.lower():
                _old_key = _k
                break
            if _mod_string_name and _mname.lower() == _mod_string_name.lower():
                _old_key = _k
                break
            if _uid_is_real and (
                    _cls == uid or _mname.lower() == uid.lower() or _cls.lower() == uid.lower()):
                _old_key = _k
                break

        if _old_key and self._client:
            _old_data = _mods.pop(_old_key)
            _old_inst = _old_data.get("instance")
            # Снимаем обработчики старого инстанса
            for _func, _handler in _old_data.get("handlers", []):
                try:
                    self._client.remove_event_handler(_func, _handler)
                except Exception:
                    pass
            # Убираем из реестра команд
            from utils.loader import COMMANDS_REGISTRY as _CR
            for _cmd in list(_CR):
                _CR[_cmd] = [c for c in _CR[_cmd] if c.get("module") != _old_key]
                if not _CR[_cmd]:
                    del _CR[_cmd]
            # Убираем из allmodules.modules
            if _old_inst and _old_inst in self.modules:
                self.modules.remove(_old_inst)
            # Чистим sys.modules от старого динамического модуля
            import sys as _sys_u
            for _sname in list(_sys_u.modules):
                if uid in _sname:
                    del _sys_u.modules[_sname]
            logger.info(f"[compat] register_module: выгружен старый инстанс {_old_key}")

        logger.info(f"[compat] register_module step 3: compiling {uid}")
        # ── 3. Компилируем и выполняем исходник ─────────────────────────
        from compat.heroku_loader import _patch_herokutl, _create_fake_package, FAKE_PACKAGE
        _patch_herokutl()
        _create_fake_package(FAKE_PACKAGE)

        # Убеждаемся что промежуточный пакет существует
        sub_pkg = f"{FAKE_PACKAGE}.modules"
        if sub_pkg not in sys.modules:
            pkg = types.ModuleType(sub_pkg)
            pkg.__path__ = []
            pkg.__package__ = sub_pkg
            sys.modules[sub_pkg] = pkg

        mod = types.ModuleType(mod_full_name)
        mod.__package__ = sub_pkg
        mod.__name__ = mod_full_name
        mod.__file__ = origin or f"<dynamic {uid}>"
        sys.modules[mod_full_name] = mod

        try:
            code = compile(source, mod.__file__, "exec")
            logger.info(f"[compat] register_module step 3b: executing {uid}")
            # Автоустановка зависимостей при ModuleNotFoundError
            for _attempt in range(5):
                try:
                    exec(code, mod.__dict__)
                    logger.info(f"[compat] register_module step 3c: exec done {uid}")
                    break
                except ModuleNotFoundError as _mne:
                    _pkg_raw = _mne.name or str(_mne)
                    # Маппинг import_name -> pip_package_name
                    _PKG_MAP = {
                        "markdown_it": "markdown-it-py",
                        "google": "google-genai",
                        "google.genai": "google-genai",
                        "google.generativeai": "google-generativeai",
                        "genai": "google-genai",
                        "PIL": "Pillow",
                        "bs4": "beautifulsoup4",
                        "yaml": "PyYAML",
                        "cv2": "opencv-python",
                        "sklearn": "scikit-learn",
                        "aiofiles": "aiofiles",
                        "magic": "python-magic",
                        "pytz": "pytz",
                        "aiohttp": "aiohttp",
                        "requests": "requests",
                        "gtts": "gTTS",
                        "pydub": "pydub",
                        "qrcode": "qrcode",
                        "barcode": "python-barcode",
                        "toml": "toml",
                        "tomllib": "tomli",
                        "mutagen": "mutagen",
                        "ffmpeg": "ffmpeg-python",
                        "cryptography": "cryptography",
                    }
                    _pkg = _PKG_MAP.get(_pkg_raw, _pkg_raw)
                    logger.info(f"[compat] register_module: missing {_pkg_raw!r}, installing as {_pkg!r}...")
                    import asyncio as _aio, sys as _sys2
                    _proc = await _aio.create_subprocess_exec(
                        _sys2.executable, "-m", "pip", "install", "-q",
                        "--disable-pip-version-check", _pkg,
                        stdout=_aio.subprocess.PIPE, stderr=_aio.subprocess.PIPE
                    )
                    _rc = await _proc.wait()
                    if _rc != 0:
                        _err = (await _proc.stderr.read()).decode()
                        logger.warning(f"[compat] pip install {_pkg} failed: {_err}")
                        del sys.modules[mod_full_name]
                        raise ImportError(f"Не удалось установить {_pkg}: {_err}") from _mne
                    import importlib as _il
                    _il.invalidate_caches()
                    logger.info(f"[compat] register_module: installed {_pkg}, retrying exec")
            else:
                del sys.modules[mod_full_name]
                raise ImportError(f"Не удалось загрузить {uid} после 5 попыток")
        except ImportError:
            raise
        except Exception as e:
            import traceback as _tb3
            logger.warning(f"[compat] register_module exec FAILED for {uid}: {e}\n{_tb3.format_exc()}")
            del sys.modules[mod_full_name]
            raise ImportError(f"Ошибка компиляции {uid}: {e}") from e

        logger.info(f"[compat] register_module step 4: finding class in {uid}")
        # ── 4. Ищем класс модуля ────────────────────────────────────────
        instance = None
        for name, obj in inspect.getmembers(mod, inspect.isclass):
            if obj is HerokuModule:
                continue
            if issubclass(obj, HerokuModule) or getattr(obj, "_is_heroku_module", False):
                instance = obj()
                break

        if instance is None:
            del sys.modules[mod_full_name]
            raise ImportError(f"register_module: класс модуля не найден в {uid}")

        logger.info(f"[compat] register_module step 5: injecting deps for {uid}")
        # ── 5. Инжектируем зависимости ──────────────────────────────────
        instance.client = self._client
        instance._client = self._client   # алиас: Hikka-модули используют self._client
        from compat.heroku_loader import _DbAdapter
        instance.db = _DbAdapter(db_module)
        try:
            me = await self._client.get_me()
            instance._tg_id = me.id
        except Exception:
            pass

        # bot_username — username именно бота, не юзербота
        bot_client = getattr(self._client, "bot_client", None)
        bot_username = getattr(self._client, "bot_username", "") or ""
        if not bot_username and bot_client is not None:
            try:
                _bme = await bot_client.get_me()
                bot_username = getattr(_bme, "username", "") or ""
                # Кэшируем для следующих вызовов
                self._client.bot_username = bot_username
            except Exception:
                bot_username = ""
        instance.inline = _InlineManager(self._client, bot_username, bot_client=bot_client)

        # ── Патчим client.inline_query ───────────────────────────────────
        # Hikka-модули вызывают self._client.inline_query(bot_username, query)
        # и ожидают список объектов с .title и .click(chat_id).
        # Telethon ИМЕЕТ метод inline_query, но он возвращает сырые TL-объекты
        # без .click() и требует реального бота в inline-режиме.
        # Решение: всегда заменяем метод на инстанс-уровне объекта клиента
        # (instance attribute перекрывает class method).
        # Наша реализация находит inline_handler модуля в INLINE_HANDLERS_REGISTRY,
        # вызывает его напрямую и возвращает _FakeInlineResult с рабочим .click().
        _iq_client_ref = self._client
        _iq_bot_ref = bot_client  # TelegramClient бота — нужен для отправки кнопок

        async def _inline_query_patch(bot_username_arg: str, query_text: str):
            from utils.loader import INLINE_HANDLERS_REGISTRY as _IHR
            results_holder = []

            # ── Сначала ищем _raw_handler (для inline.form) ──────────────
            # Такие обработчики ждут реального Telethon InlineQuery event.
            # Мы делаем GetInlineBotResultsRequest к боту — бот получит
            # настоящий InlineQuery и вызовет _raw_handler через bot_callbacks.
            # Потом используем результат через SendInlineBotResultRequest в click().
            for _rpat, _rentry in list(_IHR.items()):
                if not _rentry.get("_raw_handler"):
                    continue
                _rm = _rpat.match(query_text)
                if not _rm:
                    continue
                # Делаем реальный запрос к боту — бот ответит через bot_callbacks
                try:
                    from telethon import functions as _tl_fn
                    _iq_res = None
                    for _peer in ["me"]:
                        try:
                            _iq_res = await _iq_client_ref(
                                _tl_fn.messages.GetInlineBotResultsRequest(
                                    bot=bot_username_arg or _iq_client_ref.bot_username,
                                    peer=_peer,
                                    query=query_text,
                                    offset="",
                                )
                            )
                            break
                        except Exception as _iqe:
                            logger.debug(f"[compat] _raw_handler GetInlineBotResults peer={_peer!r}: {_iqe}")
                    if _iq_res and _iq_res.results:
                        return [_FakeInlineResult(
                            title=getattr(_iq_res.results[0], "title", ""),
                            message="",  # текст уже в TL результате
                            buttons=None,
                            client=_iq_client_ref,
                            bot_client=_iq_bot_ref,
                            inline_query=query_text,
                            _raw_tl_result=_iq_res,  # передаём сырой результат для click()
                        )]
                except Exception as _raw_e:
                    logger.warning(f"[compat] _raw_handler inline_query failed: {_raw_e}")
                return []

            # ── Ищем hikka_style обработчик (FHeta и др.) ───────────────
            # query_text пример: "fheta __cmd__ gemini"
            # паттерн FHeta: ^fheta(?:\s+(.*))?$
            matched_func = None
            matched_args = None
            for pat, entry in _IHR.items():
                if not entry.get("hikka_style"):
                    continue
                m = pat.match(query_text)
                if m:
                    prefix = entry.get("prefix", "")  # например "fheta "
                    if prefix and query_text.startswith(prefix):
                        matched_args = query_text[len(prefix):]
                    elif m.lastindex and m.group(1) is not None:
                        matched_args = m.group(1)
                    else:
                        matched_args = query_text
                    matched_func = entry["func"]
                    break

            if matched_func is None:
                # ── Ищем обычный @inline_handler из utils/loader.py (не hikka, не raw) ──
                # Например namelist из name_manager: возвращает (text, buttons)
                for _npat, _nentry in _IHR.items():
                    if _nentry.get("hikka_style") or _nentry.get("_raw_handler"):
                        continue
                    _nm = _npat.match(query_text)
                    if _nm:
                        try:
                            class _FakeNativeEvent:
                                pass
                            _nev = _FakeNativeEvent()
                            _nev.pattern_match = _nm
                            _nev.text = query_text
                            _nresult = await _nentry["func"](_nev)
                            if isinstance(_nresult, tuple) and len(_nresult) == 2:
                                _ntext, _nbuttons = _nresult
                                return [_FakeInlineResult(
                                    title=_nentry.get("title", query_text),
                                    message=_ntext,
                                    buttons=_nbuttons,
                                    client=_iq_client_ref,
                                    bot_client=_iq_bot_ref,
                                    inline_query=query_text,
                                )]
                        except Exception as _ne:
                            logger.warning(f"[compat] inline_query native handler error: {_ne}")
                            import traceback; traceback.print_exc()
                        return []

                logger.warning(f"[compat] inline_query: нет обработчика для {query_text!r}")
                return []

            # Fake answer-коллектор — handler вызывает query.inline_query.answer([...])
            class _FakeAnswerCollector:
                async def answer(self, res, cache_time=0, **kw):
                    results_holder.extend(res)

            class _FakeWrapper:
                def __init__(self, args):
                    self.args = args
                    self.inline_query = _FakeAnswerCollector()

            wrapper = _FakeWrapper(matched_args)

            try:
                ret = await matched_func(wrapper)
            except Exception as _eq:
                logger.warning(f"[compat] inline_query handler error: {_eq}")
                import traceback; traceback.print_exc()
                return []

            # Handler вернул dict (ошибка/пусто) — один fake результат
            if isinstance(ret, dict):
                return [_FakeInlineResult(
                    title=ret.get("title", ""),
                    message=ret.get("message", ret.get("title", "")),
                    buttons=None,
                    client=_iq_client_ref,
                    bot_client=_iq_bot_ref,
                )]

            # Handler вызвал answer([InlineQueryResultArticle, ...])
            # Конвертируем aiogram Articles -> _FakeInlineResult
            fake = []
            for r in results_holder:
                if hasattr(r, "input_message_content"):
                    imc  = r.input_message_content
                    text = getattr(imc, "message_text", "") if imc else ""
                    # r.reply_markup — aiogram InlineKeyboardMarkup (generate_markup возвращает aiogram)
                    fake.append(_FakeInlineResult(
                        title=r.title or "",
                        message=text,
                        buttons=r.reply_markup,
                        client=_iq_client_ref,
                        bot_client=_iq_bot_ref,
                    ))
            return fake

        # Вешаем как instance-атрибут — перекрывает Telethon class method
        self._client.inline_query = _inline_query_patch

        # Загружаем конфиг из БД
        mod_name = instance._get_module_name()
        if isinstance(instance.config, ModuleConfig):
            for key in instance.config._meta:
                saved = db_module.get_module_config(mod_name, key)
                if saved is not None:
                    instance.config.set_db_value(key, saved)

        logger.info(f"[compat] register_module step 6: client_ready for {mod_name}")
        # ── 6. Вызываем client_ready ────────────────────────────────────
        try:
            cr = instance.client_ready
            sig = inspect.signature(cr)
            params = list(sig.parameters.keys())
            if len(params) <= 1:
                await cr()
            else:
                await cr(self._client, instance.db)
        except Exception as e:
            logger.warning(f"[compat] register_module client_ready failed for {mod_name}: {e}")

        logger.info(f"[compat] register_module step 7: registering handlers for {mod_name}")
        # ── 7. Регистрируем обработчики ──────────────────────────────────
        import re as _re
        registered_commands = []

        for attr_name in dir(instance):
            try:
                func = getattr(instance, attr_name)
            except Exception:
                continue
            if not callable(func):
                continue

            # Автодетект старого стиля: метод называется <cmd>cmd
            if (not getattr(func, "_is_command", False)
                    and attr_name.endswith("cmd")
                    and not attr_name.startswith("_")):
                cmd_name = attr_name[:-3]
                if cmd_name:
                    _f = getattr(func, "__func__", func)
                    _f._is_command = True
                    _f._command_name = cmd_name
                    _f._command_kwargs = {"outgoing": True}
                    _f._command_doc = func.__doc__ or ""

            if getattr(func, "_is_command", False):
                cmd = func._command_name
                pattern = _re.compile(
                    _re.escape(PREFIX) + _re.escape(cmd) + r"(?:\s+(.*))?\\s*$",
                    _re.IGNORECASE | _re.DOTALL,
                )
                _VALID_NM_KWARGS = {"chats", "blacklist_chats", "incoming", "outgoing",
                                    "from_users", "forwards", "pattern", "func"}
                hkw = {k: v for k, v in func._command_kwargs.items() if k in _VALID_NM_KWARGS}
                hkw["pattern"] = pattern
                self._client.add_event_handler(func, events.NewMessage(**hkw))
                if cmd not in COMMANDS_REGISTRY:
                    COMMANDS_REGISTRY[cmd] = []
                _doc_str = getattr(func, '_command_doc', '') or ''
                if not _doc_str:
                    _ckw = getattr(func, '_command_kwargs', {})
                    _doc_str = (_ckw.get('ru_doc') or _ckw.get('en_doc') or _ckw.get('doc')
                                or next((v for k, v in _ckw.items()
                                         if k.endswith('_doc') and v), '') or '')
                COMMANDS_REGISTRY[cmd].append({
                    "module": f"heroku:{mod_name}",
                    "doc": _doc_str.strip() or "Нет описания",
                })
                registered_commands.append(cmd)

            if getattr(func, "_is_watcher", False):
                wkw = func._watcher_kwargs.copy()
                ev = wkw.pop("event", None)
                if ev is None:
                    if "chat_id" in wkw:
                        wkw["chats"] = wkw.pop("chat_id")
                    # Удаляем kwargs которые Telethon NewMessage не поддерживает
                    _VALID_NM = {"chats", "blacklist_chats", "incoming", "outgoing",
                                 "from_users", "forwards", "pattern", "func"}
                    _unknown = set(wkw) - _VALID_NM
                    if _unknown:
                        logger.debug(f"[compat] watcher {mod_name}: dropping unknown kwargs {_unknown}")
                        for _k in _unknown:
                            wkw.pop(_k)
                    # only_incoming → incoming=True
                    if "only_incoming" in wkw:
                        wkw["incoming"] = wkw.pop("only_incoming")
                    try:
                        ev = events.NewMessage(**wkw)
                    except Exception as _we:
                        logger.warning(f"[compat] register_module watcher error {mod_name}: {_we}")
                        continue
                self._client.add_event_handler(func, ev)

            if getattr(func, "_is_callback_handler", False):
                import re as _re2
                pat = getattr(func, "_callback_pattern", _re2.compile(".*"))
                CALLBACK_REGISTRY[pat] = func

            if getattr(func, "_is_inline_handler", False):
                # Регистрируем в глобальном INLINE_HANDLERS_REGISTRY из utils/loader
                # чтобы bot_callbacks.inline_query_handler нашёл обработчик
                from utils.loader import INLINE_HANDLERS_REGISTRY as _IHR
                _pat = getattr(func, "_inline_query_pattern", None)
                if _pat is None:
                    import re as _re3
                    _cmd = re.sub(r"_?cmd$", "", func.__name__, flags=re.IGNORECASE)
                    _pat = _re3.compile(r"^" + _re3.escape(_cmd) + r"(?:\s+(.*))?$",
                                        _re3.IGNORECASE | _re3.DOTALL)
                _prefix = getattr(func, "_inline_prefix", "")
                _IHR[_pat] = {
                    "func": func,
                    "title": getattr(func, "_inline_title", func.__name__),
                    "description": getattr(func, "_inline_description", ""),
                    "hikka_style": True,
                    "prefix": _prefix,
                }

        # ── 8. Сохраняем в реестре клиента ──────────────────────────────
        if not hasattr(self._client, "modules"):
            self._client.modules = {}

        # file_name — stem файла на диске для нормализованного матчинга в modules.py
        # _registered_file_name устанавливается в блоке save_fs выше.
        _file_name = ""
        try:
            _file_name = _registered_file_name  # "yt", "goypulse", etc.
        except NameError:
            pass  # save_fs не выполнился
        if _file_name:
            pass  # уже есть
        else:
            # Модуль не был сохранён (save_fs не выполнился) — ищем файл в modules/
            import re as _re_fn
            from pathlib import Path as _Path2
            _mdir2 = _Path2(__file__).parent.parent / "modules"
            _cn2 = type(instance).__name__ if instance else ""
            _sn2 = getattr(instance, "strings", {}) if instance else {}
            _sn2 = _sn2.get("name", "") if isinstance(_sn2, dict) else ""
            # Ищем файл по имени класса или strings["name"]
            for _pyf in _mdir2.glob("*.py"):
                _st = _pyf.stem.lower()
                _cn2l = _re_fn.sub(r'(?<!^)(?=[A-Z])', '_', _cn2).lower().strip('_') if _cn2 else ""
                _sn2l = _sn2.lower().split()[0] if _sn2 else ""
                if _st and (_st == _cn2l or _st == _sn2l or
                            (_sn2l and _sn2l.startswith(_st)) or
                            (_st and _sn2l and _st in _sn2l)):
                    _file_name = _st
                    break
            if not _file_name and _cn2:
                _file_name = _re_fn.sub(r'(?<!^)(?=[A-Z])', '_', _cn2).lower().strip('_')

        self._client.modules[f"heroku:{mod_name}"] = {
            "module": mod,
            "instance": instance,
            "handlers": [],
            "heroku_compat": True,
            "file_name": _file_name,
        }

        # Добавляем в список allmodules.modules
        self.modules.append(instance)

        logger.info(f"[compat] register_module: загружен {mod_name}, команды: {registered_commands}")
        return instance

    async def unload_module(self, class_name: str):
        """
        Выгружает модуль по имени класса или имени модуля.
        Ищет в client.modules по heroku: ключу.
        """
        import sys
        from utils.loader import COMMANDS_REGISTRY, CALLBACK_REGISTRY

        mods = getattr(self._client, "modules", {})
        target_key = None
        for k, v in mods.items():
            inst = v.get("instance")
            if inst is None:
                continue
            cls_name = inst.__class__.__name__
            mod_name = inst._get_module_name() if hasattr(inst, "_get_module_name") else cls_name
            if cls_name == class_name or mod_name == class_name:
                target_key = k
                break

        if target_key is None:
            logger.debug(f"[compat] unload_module: {class_name} не найден")
            return

        mod_data = mods.pop(target_key)
        instance = mod_data.get("instance")

        # Удаляем обработчики
        for func, handler in mod_data.get("handlers", []):
            try:
                self._client.remove_event_handler(func, handler)
            except Exception:
                pass

        # Чистим реестры команд
        mod_label = target_key  # "heroku:ModName"
        for cmd in list(COMMANDS_REGISTRY):
            COMMANDS_REGISTRY[cmd] = [c for c in COMMANDS_REGISTRY[cmd]
                                      if c.get("module") != mod_label]
            if not COMMANDS_REGISTRY[cmd]:
                del COMMANDS_REGISTRY[cmd]

        # Чистим inline handlers принадлежащие этому модулю
        from utils.loader import INLINE_HANDLERS_REGISTRY as _IHR
        mod_file = class_name  # имя класса == имя модуля
        for pat in list(_IHR):
            entry = _IHR[pat]
            fn = entry.get("func")
            if fn is None:
                continue
            fn_module = getattr(fn, "__module__", "") or ""
            if fn_module.endswith(mod_file) or f".{mod_file}" in fn_module:
                del _IHR[pat]

        # Чистим sys.modules
        for name in list(sys.modules):
            if f".{class_name}" in name or name.endswith(class_name):
                del sys.modules[name]

        # Удаляем из self.modules
        if instance and instance in self.modules:
            self.modules.remove(instance)

        logger.info(f"[compat] unload_module: выгружен {class_name}")

    def send_config_one(self, instance):
        """Применяет конфиг из БД к instance — конфиг уже применён при загрузке."""
        if instance is None:
            return
        try:
            from utils import database as db_module
            from compat.loader import ModuleConfig
            mod_name = instance._get_module_name() if hasattr(instance, "_get_module_name") else ""
            if mod_name and isinstance(getattr(instance, "config", None), ModuleConfig):
                for key in instance.config._meta:
                    saved = db_module.get_module_config(mod_name, key)
                    if saved is not None:
                        instance.config.set_db_value(key, saved)
        except Exception as e:
            logger.debug(f"[compat] send_config_one: {e}")

    async def send_ready_one(self, instance, **kwargs):
        """
        Повторный вызов client_ready после register_module.
        Обычно уже вызван — делаем noop, но вызовем если instance новый.
        """
        if instance is None:
            return
        try:
            import inspect
            cr = instance.client_ready
            sig = inspect.signature(cr)
            params = list(sig.parameters.keys())
            if len(params) <= 1:
                await cr()
            else:
                from compat.heroku_loader import _DbAdapter
                from utils import database as db_module
                await cr(self._client, _DbAdapter(db_module))
        except Exception as e:
            logger.debug(f"[compat] send_ready_one: {e}")

    def update_modules_in_db(self):
        """Синхронизирует список активных модулей в БД KoteLoader."""
        try:
            from utils import database as db_module
            mods = getattr(self._client, "modules", {})
            for key in mods:
                if key.startswith("heroku:"):
                    mod_name = key[len("heroku:"):]
                    # Сохраняем факт что модуль активен (если есть такой API)
                    if hasattr(db_module, "set_module_active"):
                        db_module.set_module_active(mod_name, True)
        except Exception as e:
            logger.debug(f"[compat] update_modules_in_db: {e}")


class _LoaderModuleStub:
    """
    Реализует объект возвращаемый self.lookup("loader") в Hikka.

    Используется FHeta и другими Hikka-совместимыми модулями:
      lm = self.lookup("loader")
      r = await lm._storage.fetch(url, auth=lm.config.get("basic_auth"))
      result = await lm.allmodules.register_module(spec, name, origin)
      lm.update_modules_in_db()
      await lm.install_packages(packages)
    """

    def __init__(self, client):
        self._client = client
        self.allmodules = _AllModules(client)
        self.fully_loaded = True
        self._storage = _StorageFetcher()
        self.config = _LoaderConfig()

    def update_modules_in_db(self):
        """Обновляет список модулей в БД."""
        self.allmodules.update_modules_in_db()

    async def load_module(self, url_or_source: str = "", **kwargs):
        """Загружает модуль по URL или исходнику."""
        if url_or_source.startswith("http"):
            try:
                source = await self._storage.fetch(url_or_source)
            except Exception as e:
                logger.warning(f"[compat] load_module fetch failed: {e}")
                return None
        else:
            source = url_or_source

        if not source:
            return None

        import uuid
        from importlib.machinery import ModuleSpec
        from compat.loader import StringLoader

        uid = f"__extmod_{uuid.uuid4()}"
        module_name = f"heroku.modules.{uid}"
        spec = ModuleSpec(module_name, StringLoader(source, f"<dynamic {uid}>"),
                          origin=f"<dynamic {uid}>")
        try:
            return await self.allmodules.register_module(spec, module_name,
                                                         origin=url_or_source)
        except Exception as e:
            logger.warning(f"[compat] load_module register failed: {e}")
            return None

    async def unload_module(self, class_name: str, **kwargs):
        """Выгружает модуль по имени класса."""
        await self.allmodules.unload_module(class_name)

    async def install_packages(self, packages: list) -> bool:
        """
        Устанавливает apt-пакеты (если apt доступен) или pip-пакеты.
        Возвращает True при успехе, False при ошибке.
        """
        import asyncio, sys
        if not packages:
            return True
        logger.info(f"[compat] install_packages: {packages}")
        # Пробуем pip как запасной вариант для apt-пакетов
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "--upgrade", "-q",
            "--disable-pip-version-check", "--no-warn-script-location",
            *packages,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        rc = await proc.wait()
        if rc != 0:
            stderr = (await proc.stderr.read()).decode()
            logger.warning(f"[compat] install_packages pip failed: {stderr}")
            return False
        return True

    def __getattr__(self, name: str):
        """Любой неизвестный атрибут — возвращаем безопасную заглушку."""
        async def _async_noop(*a, **kw):
            return None
        def _noop(*a, **kw):
            return None
        # Эвристика: если имя выглядит как async-метод — возвращаем корутину
        _async_names = {"fetch", "load", "install", "unload", "send", "register", "ready"}
        if any(name.startswith(n) or name.endswith(n) for n in _async_names):
            return _async_noop
        return _noop


# ═══════════════════════════════════════════════════════════════════════════
# utils — эмуляция heroku/hikka utils
# ═══════════════════════════════════════════════════════════════════════════

class _Utils:
    """Эмуляция utils из Heroku/Hikka."""

    @staticmethod
    def escape_html(text: Any) -> str:
        return html.escape(str(text))

    @staticmethod
    def get_args_raw(message) -> str:
        """Возвращает аргументы команды как строку."""
        try:
            match = getattr(message, "pattern_match", None)
            if match:
                g = match.group(1)
                return (g or "").strip()
        except Exception:
            pass
        try:
            text = message.raw_text or ""
            parts = text.split(maxsplit=1)
            return parts[1] if len(parts) > 1 else ""
        except Exception:
            return ""

    @staticmethod
    def get_args(message) -> list:
        raw = _Utils.get_args_raw(message)
        return raw.split() if raw else []

    @staticmethod
    def get_chat_id(message) -> int:
        try:
            return message.chat_id
        except Exception:
            return 0

    @staticmethod
    async def answer(message, text: str, reply_markup=None, **kwargs) -> Any:
        """
        utils.answer(message, text) — редактирует/отправляет сообщение.
        Аналог answer() из Hikka. Поддерживает premium emoji через
        <emoji document_id=...> теги.
        """
        # Конвертируем reply_markup если передан в формате Heroku
        buttons = None
        if reply_markup:
            if isinstance(reply_markup, list):
                try:
                    tmp = _InlineManager(message.client)
                    buttons = tmp._generate_telethon_markup(reply_markup)
                except Exception:
                    pass

        # Проверяем наличие premium emoji тегов
        try:
            from compat.heroku_loader import _has_emoji_tags, _parse_emoji_html
            has_emoji = _has_emoji_tags(text)
        except Exception:
            has_emoji = False

        if has_emoji:
            # Парсим emoji и отправляем через formatting_entities
            try:
                parsed_text, all_entities = _parse_emoji_html(text)
                kw = {k: v for k, v in kwargs.items() if k != "parse_mode"}
                try:
                    await message.edit(
                        parsed_text,
                        formatting_entities=all_entities,
                        link_preview=False,
                        buttons=buttons,
                        **kw,
                    )
                    return message
                except Exception:
                    sent = await message.client.send_message(
                        message.chat_id,
                        parsed_text,
                        formatting_entities=all_entities,
                        link_preview=False,
                        buttons=buttons,
                        reply_to=message.id,
                        **kw,
                    )
                    return sent
            except Exception:
                pass  # fallback ниже

        parse_mode = kwargs.get("parse_mode", "html")
        try:
            await message.edit(
                text,
                parse_mode=parse_mode,
                link_preview=False,
                buttons=buttons,
            )
            return message
        except Exception:
            try:
                sent = await message.client.send_message(
                    message.chat_id,
                    text,
                    parse_mode=parse_mode,
                    link_preview=False,
                    buttons=buttons,
                    reply_to=message.id,
                )
                return sent
            except Exception as e:
                logger.warning(f"[compat] utils.answer failed: {e}")
                return message

    @staticmethod
    def get_display_name(entity) -> str:
        try:
            from telethon.utils import get_display_name as tg_name
            return tg_name(entity)
        except Exception:
            return str(entity)

    @staticmethod
    async def run_sync(func, *args, **kwargs):
        """utils.run_sync(func, *args) — запускает синхронную функцию в executor."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    @staticmethod
    def dnd(*args, **kwargs):
        """Заглушка utils.dnd — архивация чата (не критично если не работает)."""
        import asyncio
        async def _noop(): pass
        return _noop()

    @staticmethod
    def rand(length: int = 16) -> str:
        """Генерирует случайную строку из букв и цифр заданной длины.
        Аналог utils.rand() из Hikka — используется как ID для inline-результатов."""
        import random
        import string
        chars = string.ascii_letters + string.digits
        return "".join(random.choice(chars) for _ in range(length))


# Синглтон utils для импорта
utils = _Utils()

# Самоссылка: loader.loader.StringLoader — Hikka использует такой путь
import sys as _sys
_self = _sys.modules[__name__]
_self.loader = _self
_self.callback_handler = callback_handler