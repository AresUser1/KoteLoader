# handlers/bot_callbacks.py

import traceback
import asyncio
import sys
import importlib
import re
from pathlib import Path

from telethon import events
from telethon.errors.rpcerrorlist import MessageNotModifiedError
from telethon.tl.custom import Button
from telethon.tl.types import InputBotInlineResult

from utils import database as db
from utils.loader import (
    INLINE_HANDLERS_REGISTRY, CALLBACK_REGISTRY,
    load_module, unload_module, reload_module, check_module_dependencies
)
from panels.main_panel import build_main_panel
from panels.module_menu import build_module_menu
from panels.global_menu import build_global_menu
from panels.updates_panel import build_updates_panel
from services.state_manager import update_state_file
from modules.updater import check_for_updates


class _HtmlCallProxy:
    """
    Прокси над CallbackQuery event для Heroku/Hikka-совместимых модулей.

    Эмулирует Hikka InlineCall:
      - call.edit(text, ...)              — редактирует сообщение с parse_mode=html
      - call.answer(text, ...)            — показывает всплывающее уведомление
      - call.respond(text, ...)           — отправляет новое сообщение
      - call.inline_message_id           — id для редактирования inline-сообщений
      - call.message.chat.id             — id чата (Hikka-стиль)
      - call.message.message_id          — id сообщения (Hikka-стиль)
      - call.from_user.id / call.data    — прокси на event
    """
    def __init__(self, event):
        self._event = event

    # ── Редактирование сообщения ─────────────────────────────────────────
    async def edit(self, text, reply_markup=None, parse_mode="html",
                   link_preview=False, **kwargs):
        from compat.loader import _BotStub as _BS
        import logging as _log
        _logger = _log.getLogger("compat.loader")
        buttons = None
        if reply_markup is not None:
            buttons = _BS._convert_markup(reply_markup)
        iid = getattr(self._event, "inline_message_id", None)
        _logger.info(f"[compat] HtmlCallProxy.edit: inline_message_id={iid!r} has_buttons={buttons is not None}")

        # Inline callback (via @bot inline) — нужен EditInlineBotMessageRequest
        if iid:
            try:
                from telethon import functions as _tl_f
                from telethon.tl.types import InputBotInlineMessageID, InputBotInlineMessageID64

                # iid — уже готовый TL объект из event.query.msg_id (Telethon ставит его напрямую)
                # base64 декодирование НЕ нужно
                tl_iid = iid

                # Конвертируем reply_markup из Hikka формата в Telethon кнопки
                tl_markup = None
                if reply_markup is not None:
                    try:
                        from compat.loader import _InlineManager as _IM
                        _im = _IM(None)
                        tl_buttons = _im._generate_telethon_markup(reply_markup)
                        tl_markup = self._event.client.build_reply_markup(tl_buttons)
                    except Exception as _me:
                        _logger.warning(f"[compat] HtmlCallProxy.edit markup convert failed: {_me}")

                # Парсим текст с поддержкой premium emoji
                try:
                    from compat.heroku_loader import _has_emoji_tags, _parse_emoji_html
                    if _has_emoji_tags(text):
                        msg_text, entities = _parse_emoji_html(text)
                        # Если парсинг emoji вернул пустой текст — fallback на strip-версию
                        if not msg_text or not msg_text.strip():
                            _logger.warning("[compat] _parse_emoji_html вернул пустой текст, fallback на HTML без emoji")
                            from telethon.extensions import html as _tl_html
                            _clean = _parse_emoji_html.__globals__["_EMOJI_TAG_RE"].sub(lambda m: m.group(2), text)
                            msg_text, entities = _tl_html.parse(_clean)
                    else:
                        from telethon.extensions import html as _tl_html
                        msg_text, entities = _tl_html.parse(text)
                except Exception as _pe:
                    _logger.warning(f"[compat] parse failed: {_pe}, fallback to strip")
                    try:
                        from telethon.extensions import html as _tl_html
                        msg_text, entities = _tl_html.parse(text)
                    except Exception:
                        msg_text = text
                        entities = []

                # Telegram не принимает пустой текст — ставим неразрывный пробел
                if not msg_text or not msg_text.strip():
                    _logger.warning(f"[compat] HtmlCallProxy.edit: пустой msg_text после парсинга, text={text[:80]!r}")
                    msg_text = " "  # неразрывный пробел — Telegram принимает в inline edit
                    entities = []

                _logger.info(f"[compat] HtmlCallProxy.edit (inline): msg_text_len={len(msg_text)} entities={len(entities)}")
                await self._event.client(_tl_f.messages.EditInlineBotMessageRequest(
                    id=tl_iid,
                    message=msg_text,
                    entities=entities,
                    reply_markup=tl_markup,
                    no_webpage=not link_preview,
                ))
                _logger.info("[compat] HtmlCallProxy.edit (inline): OK")
                return
            except Exception as _e_inline:
                _logger.warning(f"[compat] HtmlCallProxy.edit (inline) failed: {_e_inline}")
                import traceback as _tbi
                _logger.debug(_tbi.format_exc())
                return

        # Обычный callback — используем стандартный event.edit() через userbot
        try:
            from compat.heroku_loader import _has_emoji_tags, _parse_emoji_html
            _has_emj = isinstance(text, str) and _has_emoji_tags(text)
        except Exception:
            _has_emj = False

        try:
            if _has_emj:
                # Premium emoji — парсим и передаём formatting_entities (userbot с премиумом)
                _ep_text, _ep_ents = _parse_emoji_html(text)
                if _ep_text and _ep_text.strip():
                    await self._event.edit(
                        _ep_text, formatting_entities=_ep_ents,
                        buttons=buttons, link_preview=False,
                    )
                    _logger.info("[compat] HtmlCallProxy.edit (emoji): OK")
                    return
            await self._event.edit(
                text, buttons=buttons,
                parse_mode="html", link_preview=False,
            )
            _logger.info("[compat] HtmlCallProxy.edit: OK")
        except Exception as _e:
            _logger.warning(f"[compat] HtmlCallProxy.edit failed: {_e}")
            try:
                await self._event.respond(text, parse_mode="html", link_preview=False)
            except Exception as _e2:
                _logger.warning(f"[compat] HtmlCallProxy.respond fallback failed: {_e2}")

    # ── Всплывающее уведомление ──────────────────────────────────────────
    async def answer(self, text="", show_alert=False, alert=None, **kwargs):
        # Принимаем оба варианта: show_alert (наш стиль) и alert (Telethon/aiogram стиль)
        if alert is not None:
            show_alert = alert
        import logging as _alog
        _alog.getLogger("bot_callbacks").info(f"[answer] text={text!r} show_alert={show_alert} is_inline={type(getattr(self._event, 'original_update', None)).__name__!r}")
        import logging as _log2
        _logger2 = _log2.getLogger("compat.loader")
        raw = getattr(self._event, "original_update", None)
        is_inline_cb = (raw is not None and
                        type(raw).__name__ == "UpdateInlineBotCallbackQuery")
        if is_inline_cb:
            # Для inline via @bot используем SetBotCallbackAnswerRequest через bot_client
            try:
                from telethon import functions as _tl_f2
                query_id = getattr(raw, "query_id", None)
                _logger2.info(f"[compat] call.answer inline: query_id={query_id!r} text={text!r} alert={show_alert}")
                if query_id is not None:
                    result = await self._event.client(
                        _tl_f2.messages.SetBotCallbackAnswerRequest(
                            query_id=query_id,
                            cache_time=0,
                            message=str(text) if text else None,
                            alert=show_alert,
                        )
                    )
                    _logger2.info(f"[compat] call.answer (inline): OK result={result!r}")
            except Exception as _ae:
                _err_str = str(_ae)
                _err_name = type(_ae).__name__
                if "QueryIdInvalid" in _err_name or "QUERY_ID_INVALID" in _err_str:
                    _logger2.warning(f"[compat] call.answer (inline) query expired (>15s), ignoring: {_ae}")
                else:
                    _logger2.error(f"[compat] call.answer (inline) FAILED: {_ae}", exc_info=True)
            return
        try:
            await self._event.answer(text, alert=show_alert)
        except Exception:
            pass

    # ── Новое сообщение в чат ────────────────────────────────────────────
    async def respond(self, text, **kwargs):
        kwargs.setdefault("parse_mode", "html")
        kwargs.setdefault("link_preview", False)
        await self._event.respond(text, **kwargs)

    # ── inline_message_id (для inline-режима) ────────────────────────────
    @property
    def inline_message_id(self):
        # Telethon CallbackQuery.inline_message_id
        v = getattr(self._event, "inline_message_id", None)
        if v is not None:
            return v
        # UpdateInlineBotCallbackQuery: msg_id лежит прямо на original_update,
        # а НЕ в event.query — Telethon не проксирует его как inline_message_id
        raw = getattr(self._event, "original_update", None)
        if raw is not None and type(raw).__name__ == "UpdateInlineBotCallbackQuery":
            msg_id = getattr(raw, "msg_id", None)
            if msg_id is not None:
                return msg_id
        # Fallback: старый путь через event.query.msg_id
        query = getattr(self._event, "query", None)
        if query is not None:
            msg_id = getattr(query, "msg_id", None)
            if msg_id is not None:
                return msg_id
        return None

    # ── call.message.chat.id / call.message.message_id  (Hikka-стиль) ───
    @property
    def message(self):
        return _CallMessageProxy(self._event)

    # ── call.from_user.id ────────────────────────────────────────────────
    @property
    def from_user(self):
        return _FromUserProxy(self._event)

    # ── call.data — строка, как в оригинальном Hikka/Heroku ──────────────
    # HikariChat и другие Hikka-модули ожидают call.data как str:
    #   re.match(r"pattern", call.data)  и  call.data.split("/")
    # Telethon event.data — bytes, декодируем здесь один раз.
    @property
    def data(self):
        d = getattr(self._event, "data", b"")
        if isinstance(d, bytes):
            return d.decode("utf-8", errors="replace")
        return d if d is not None else ""

    # ── Удаление сообщения (call.delete()) ─────────────────────────────
    async def delete(self):
        """Удаляет сообщение с кнопками (аналог Hikka InlineCall.delete)."""
        import logging as _log
        _logger = _log.getLogger("compat.loader")
        raw = getattr(self._event, "original_update", None)
        is_inline_cb = (raw is not None and
                        type(raw).__name__ == "UpdateInlineBotCallbackQuery")

        if is_inline_cb:
            # Inline via @bot: удаляем через EditInlineBotMessageRequest
            # Сначала отвечаем на callback чтобы убрать крутилку
            try:
                from telethon import functions as _tl_f3
                query_id = getattr(raw, "query_id", None)
                if query_id is not None:
                    try:
                        await self._event.client(
                            _tl_f3.messages.SetBotCallbackAnswerRequest(
                                query_id=query_id,
                                message=None,
                                alert=False,
                            )
                        )
                    except Exception:
                        pass
                # Редактируем сообщение — убираем текст и кнопки
                msg_id_raw = getattr(raw, "msg_id", None)
                if msg_id_raw is not None:
                    await self._event.client(_tl_f3.messages.EditInlineBotMessageRequest(
                        id=msg_id_raw,
                        message="✖️",
                        entities=[],
                        reply_markup=None,
                        no_webpage=True,
                    ))
                    _logger.info("[compat] call.delete (inline): OK")
            except Exception as _de:
                _logger.warning(f"[compat] call.delete (inline) failed: {_de}")
            return

        try:
            # Для обычного callback — удаляем сообщение через event
            msg = getattr(self._event, "message", None)
            if msg is not None:
                await msg.delete()
                return
            # Fallback: пробуем через client
            chat_id = getattr(self._event, "chat_id", None)
            msg_id = getattr(self._event, "message_id", None)
            if chat_id and msg_id:
                await self._event.client.delete_messages(chat_id, [msg_id])
        except Exception as _e:
            _logger.warning(f"[compat] HtmlCallProxy.delete failed: {_e}")

    # ── Всё остальное — прямо с event ───────────────────────────────────
    def __getattr__(self, name):
        return getattr(self._event, name)


class _CallMessageProxy:
    """Эмулирует call.message для Hikka (call.message.chat.id, call.message.message_id)."""
    def __init__(self, event):
        self._event = event

    @property
    def chat(self):
        return _ChatProxy(self._event)

    @property
    def message_id(self):
        # Telethon CallbackQuery: .message_id (обычный) или None (inline)
        v = getattr(self._event, "message_id", None)
        if v is not None:
            return v
        m = getattr(self._event, "message", None)
        if m is not None:
            return getattr(m, "id", None)
        return None

    def __getattr__(self, name):
        return getattr(self._event, name)


class _ChatProxy:
    """Эмулирует call.message.chat для Hikka (call.message.chat.id)."""
    def __init__(self, event):
        self._event = event

    @property
    def id(self):
        # Telethon CallbackQuery: chat_id может быть 0 для групп/каналов
        v = getattr(self._event, "chat_id", None)
        if v:  # не None и не 0
            return v
        # Для групп и каналов берём из peer_id с правильным знаком
        peer = getattr(self._event, "peer_id", None)
        if peer is not None:
            cid = getattr(peer, "channel_id", None)
            if cid:
                return -1000000000000 - cid  # супергруппа/канал
            cid = getattr(peer, "chat_id", None)
            if cid:
                return -cid  # обычная группа
            cid = getattr(peer, "user_id", None)
            if cid:
                return cid  # личный чат
        return None

    def __getattr__(self, name):
        return getattr(self._event, name)


class _FromUserProxy:
    """Эмулирует call.from_user для Hikka (call.from_user.id)."""
    def __init__(self, event):
        self._event = event

    @property
    def id(self):
        return getattr(self._event, "sender_id", None)

    def __getattr__(self, name):
        return getattr(self._event, name)


async def updates_callback_handler(event):
    """
    Обрабатывает нажатия на кнопки обновления. Отправляет команды юзерботу.
    """
    action = event.pattern_match.group(1)

    await event.answer("Отправляю команду на обновление...")

    user_client = event.client.user_client
    # ❗️❗️❗️ НОВОЕ: Запоминаем, куда слать отчет ❗️❗️❗️
    report_chat_id = event.chat_id

    if action == "all":
        from modules.updater import check_for_updates
        
        updates = await check_for_updates()
        modules_to_update = [u["module_name"] for u in updates]

        for module_name in modules_to_update:
             # ❗️❗️❗️ ИЗМЕНЕНИЕ: Передаем ID чата для отчета ❗️❗️❗️
             await user_client.send_message("me", f".update {module_name} {report_chat_id}")
             await asyncio.sleep(0.3) 

        await event.edit("✅ <b>Команды на обновление всех модулей отправлены!</b>", buttons=None, parse_mode='html')

    else: 
        module_name = action
        # ❗️❗️❗️ ИЗМЕНЕНИЕ: Передаем ID чата для отчета ❗️❗️❗️
        await user_client.send_message("me", f".update {module_name} {report_chat_id}")
        await event.edit(f"✅ <b>Команда на обновление <code>{module_name}</code> отправлена!</b>", buttons=None, parse_mode='html')

class _TelethonInlineQueryProxy:
    """
    Проксирует Telethon InlineQuery event для Hikka-модулей.
    Конвертирует aiogram InlineQueryResultArticle -> Telethon builder.article().
    Обрабатывает reply_markup любого формата и link_preview_options.
    """
    def __init__(self, event: events.InlineQuery):
        self._event = event

    def __getattr__(self, name):
        return getattr(self._event, name)

    @staticmethod
    def _markup_to_telethon(reply_markup):
        """
        Конвертирует reply_markup из любого формата в Telethon [[Button,...], ...].
          - None                        -> None
          - [[TgButton,...],...]        -> возвращаем как есть (уже Telethon)
          - aiogram InlineKeyboardMarkup -> конвертируем побутонно
        """
        if reply_markup is None:
            return None
        # aiogram InlineKeyboardMarkup имеет .inline_keyboard
        if hasattr(reply_markup, "inline_keyboard"):
            from telethon.tl.custom import Button as TgButton
            rows = []
            for row in reply_markup.inline_keyboard:
                tg_row = []
                for btn in row:
                    text = getattr(btn, "text", "?")
                    url  = getattr(btn, "url", None)
                    cb   = getattr(btn, "callback_data", None)
                    if url:
                        tg_row.append(TgButton.url(text, url))
                    elif cb:
                        tg_row.append(TgButton.inline(text, data=cb.encode("utf-8")[:64]))
                    else:
                        tg_row.append(TgButton.inline(text, data=b"noop"))
                if tg_row:
                    rows.append(tg_row)
            return rows or None
        # Уже Telethon-формат [[TgButton,...],...]
        return reply_markup

    async def answer(self, results, cache_time: int = 0, **kwargs):
        """
        Принимает список aiogram InlineQueryResultArticle
        и конвертирует в Telethon InlineQuery results через event.builder.article().
        """
        tl_results = []
        for r in results:
            if hasattr(r, "input_message_content"):
                imc  = r.input_message_content
                text = getattr(imc, "message_text", "") if imc else ""
                buttons = self._markup_to_telethon(r.reply_markup)
                tl_results.append(
                    self._event.builder.article(
                        id=getattr(r, "id", None),
                        title=r.title or "",
                        description=r.description or "",
                        text=text,
                        parse_mode="html",
                        buttons=buttons,
                        url=getattr(r, "thumbnail_url", None) or "",
                    )
                )
            else:
                # Уже Telethon-объект
                tl_results.append(r)

        await self._event.answer(tl_results, cache_time=cache_time)


class _HikkaQueryWrapper:
    """
    Эмулирует объект `query` который Hikka передаёт в @loader.inline_handler.

    Hikka-контракт:
      - query.args          → строка запроса (после имени обработчика + пробел)
      - query.inline_query  → прокси над Telethon InlineQuery event
      - функция может: return dict(title, description, message, thumb)
                       или    самостоятельно вызвать query.inline_query.answer([...])
    """
    def __init__(self, event: events.InlineQuery, raw_text: str, args: str):
        self.inline_query = _TelethonInlineQueryProxy(event)
        self.args = args
        self._raw_text = raw_text

    @property
    def sender_id(self):
        return getattr(self.inline_query, "sender_id", None)


async def inline_query_handler(event: events.InlineQuery):
    """
    Динамически обрабатывает инлайн-запросы, находя подходящий обработчик.
    """
    import logging as _logging
    _log = _logging.getLogger("bot_callbacks")
    query_text = event.text.strip()
    _log.info(f"[inline_query] sender={event.sender_id} query={query_text!r}")
    _log.info(f"[inline_query] INLINE_HANDLERS_REGISTRY keys: {[str(p.pattern) for p in INLINE_HANDLERS_REGISTRY.keys()]}")

    # Исключение для секреток (включая прямой ввод текста)
    is_wisp = query_text.startswith("wisp:") or query_text.startswith("wisp ")
    
    if not is_wisp:
        if db.get_user_level(event.sender_id) not in ["OWNER", "TRUSTED"]:
            return

    try:
        # Прямой ввод: wisp <target> <text>
        if query_text.startswith("wisp "):
            match = re.match(r"^wisp\s+(\d+|@\w+)\s+(.*)", query_text, re.DOTALL)
            if match:
                target, message_text = match.group(1), match.group(2).strip()
                
                # Короткое превью для инлайна
                display_text = message_text[:30] + "..." if len(message_text) > 30 else message_text
                
                # Мы не создаем запись в БД здесь, а передаем параметры в callback
                # Но лучше вызвать функцию из модуля wisp, если она там есть.
                # Для простоты, мы делегируем это существующему реестру инлайнов.
                pass 

        if query_text == "updates:check":
            
            text = "⚙️ <b>Центр обновлений</b>\n\nНажмите кнопку ниже, чтобы запустить поиск обновлений для ваших модулей."
            buttons = [
                [Button.inline("🔄 Начать проверку", data="run_updates_check")]
            ]
            
            result = event.builder.article(
                title="Центр обновлений",
                description="Нажмите, чтобы запустить проверку",
                text=text,
                buttons=buttons,
                parse_mode="html"
            )
            await event.answer([result])
            return

        if query_text.startswith("module:"):
            module_name = query_text.split(":", 1)[1]
            check = check_module_dependencies(module_name)

            if check["status"] == "error":
                missing_lib = check["library"]
                text = (f"⚠️ **Ошибка в модуле `{module_name}`**\n\n"
                        f"Причина: отсутствует библиотека: `{missing_lib}`.")
                buttons = [[Button.inline(f"📦 Установить {missing_lib}", data=f"dep:install:{module_name}:{missing_lib}")],
                           [Button.inline("🗑️ Удалить модуль", data=f"dep:delete:{module_name}")]]
                result = event.builder.article(
                    title=f"Ошибка в модуле: {module_name}",
                    description=f"Отсутствует библиотека {missing_lib}",
                    text=text, buttons=buttons, parse_mode="md"
                )
            else:
                text, buttons = build_module_menu(module_name, as_text=True)
                result = event.builder.article(
                    title=f"Управление модулем: {module_name}",
                    description="Загрузка, выгрузка и перезагрузка.",
                    text=text, buttons=buttons, parse_mode="html"
                )

            await event.answer([result])
            return

        for pattern, handler_info in list(INLINE_HANDLERS_REGISTRY.items()):
            match = pattern.match(query_text)
            if match:
                # Одноразовый обработчик (для inline.form) — удаляем после вызова
                if handler_info.get("_one_shot"):
                    INLINE_HANDLERS_REGISTRY.pop(pattern, None)
                # ── Raw handler: прямой вызов с Telethon InlineQuery event ─
                if handler_info.get("_raw_handler"):
                    try:
                        await handler_info["func"](event)
                    except Exception:
                        traceback.print_exc()
                    return
                # ── Hikka-стиль: функция принимает query-объект ─────────
                if handler_info.get("hikka_style"):
                    # args = всё что идёт после имени обработчика (напр. "fheta foo bar" → "foo bar")
                    _prefix = handler_info.get("prefix", "")
                    _args = query_text[len(_prefix):].lstrip() if _prefix else query_text
                    query_obj = _HikkaQueryWrapper(event, query_text, _args)
                    try:
                        ret = await handler_info["func"](query_obj)
                    except Exception:
                        traceback.print_exc()
                        return
                    # Если функция вернула dict — оборачиваем в один Article сами
                    if isinstance(ret, dict):
                        from telethon.tl.types import (
                            InputBotInlineResultArticle,
                            InputBotInlineMessageText,
                        )
                        _msg = ret.get("message", ret.get("title", ""))
                        _thumb = ret.get("thumb", "")
                        result = event.builder.article(
                            title=ret.get("title", ""),
                            description=ret.get("description", ""),
                            text=_msg,
                            parse_mode="html",
                        )
                        await event.answer([result], cache_time=0)
                    # None → функция сама вызвала answer(), ничего не делаем
                    return
                # ── Старый стиль: func → (text, buttons) ────────────────
                event.pattern_match = match
                text, buttons = await handler_info["func"](event)
                result = event.builder.article(
                    title=handler_info["title"],
                    description=handler_info["description"],
                    text=text, buttons=buttons, parse_mode="html"
                )
                await event.answer([result])
                return

        text, buttons = build_main_panel(search_query=query_text, as_text=True, user_client=getattr(event.client, "user_client", None))
        result = event.builder.article(
            title="⚙️ Панель управления",
            description="Главное меню.",
            text=text, buttons=buttons, parse_mode="html"
        )
        await event.answer([result])
    except Exception:
        traceback.print_exc()

async def callback_query_handler(event: events.CallbackQuery):
    """
    Динамически обрабатывает нажатия на инлайн-кнопки.
    """
    import logging as _cblog
    _cblog.getLogger("bot_callbacks").info(
        f"[callback] data={getattr(event, 'data', b'').decode('utf-8', errors='replace')!r} "
        f"sender={getattr(event, 'sender_id', None)} "
        f"update_type={type(getattr(event, 'original_update', event)).__name__}"
    )
    try:
        data = event.data.decode()
    except:
        data = ""

    # Исключение для секретных сообщений и других публичных функций
    is_public = data.startswith("wisp_read:") or data.startswith("wisp_open:")
    
    if not is_public:
        if db.get_user_level(event.sender_id) not in ["OWNER", "TRUSTED"]:
            print(f"🚫 Доступ запрещен для ID: {event.sender_id} (Data: {data})")
            return await event.answer(f"🚫 Доступ запрещён.\n(ID: {event.sender_id})", alert=True)

    user_client = event.client.user_client

    try:
        # Системные кнопки Hikka/Heroku
        if data in ("noop", ""):
            await event.answer()
            return

        if data == "close":
            await event.answer()
            try:
                await event.delete()
            except Exception:
                try:
                    await event.edit("✅ Closed.", buttons=None)
                except Exception:
                    pass
            return

        if data == "close_panel":
            await event.answer("Закрыто.")
            await event.edit("Панель закрыта.", buttons=None)
            return
            
        if data == "run_updates_check":
            await event.answer("🔄 Ищу обновления...", alert=False)
            updates_list = await check_for_updates()
            text, buttons = build_updates_panel(updates_list)
            await event.edit(text, buttons=buttons, parse_mode="html")
            return
        
        if data.startswith("do_update:"):
            event.pattern_match = re.match(r"do_update:(.+)", data)
            if event.pattern_match:
                await updates_callback_handler(event)
                return

        if data.startswith("dep:"):
            await event.answer()
            parts = data.split(":")
            action, module_name = parts[1], parts[2]

            if action == "install":
                library_name = parts[3]
                await event.edit(f"⏳ Начинаю установку `{library_name}`...")
                process = await asyncio.create_subprocess_shell(
                    f"{sys.executable} -m pip install {library_name}",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await process.communicate()

                if process.returncode == 0:
                    importlib.invalidate_caches()
                    await event.edit(f"✅ Библиотека `{library_name}` установлена!\nПроверяю модуль `{module_name}` снова...")
                    check = check_module_dependencies(module_name)
                    if check["status"] == "ok":
                        text, buttons = build_module_menu(module_name, as_text=True)
                        await event.edit(text, buttons=buttons, parse_mode="html")
                    else:
                        new_missing_lib = check["library"]
                        text = (f"⚠️ **Ошибка в модуле `{module_name}`**\n\n"
                                f"Найдена еще одна отсутствующая библиотека: `{new_missing_lib}`.")
                        buttons = [[Button.inline(f"📦 Установить {new_missing_lib}", data=f"dep:install:{module_name}:{new_missing_lib}")],
                                   [Button.inline("🔙 Назад", data="back_to_main")]]
                        await event.edit(text, buttons=buttons, parse_mode="md")
                else:
                    output = stderr.decode().strip() or stdout.decode().strip()
                    await event.edit(f"❌ **Ошибка установки `{library_name}`:**\n"
                                     f"<code>{output}</code>", parse_mode="html")
                return

            elif action == "delete":
                MODULES_DIR = Path(__file__).parent.parent / "modules"
                module_path = MODULES_DIR / f"{module_name}.py"
                if module_path.exists():
                    module_path.unlink()
                    db.clear_module(module_name)
                    await event.answer(f"🗑️ Модуль {module_name} полностью удален.", alert=True)
                    text, buttons = build_main_panel(as_text=True, user_client=user_client)
                    await event.edit(text, buttons=buttons, parse_mode="html")
                else:
                    await event.answer(f"ℹ️ Модуль {module_name} уже был удален.", alert=True)
                return

        # ── Приоритет 1: точные ключи из _CallbackRegistry (hc_N — кнопки inline.form) ──
        # Проверяем ДО CALLBACK_REGISTRY чтобы паттерн .* из других модулей не перехватил.
        from compat.loader import _CallbackRegistry
        compat_entry = _CallbackRegistry.get(data)
        if compat_entry:
            func, args = compat_entry
            try:
                # Для UpdateInlineBotCallbackQuery НЕ вызываем event.answer() здесь —
                # это делает сам call.answer() / call.delete() внутри callback-функции
                _raw_upd = getattr(event, "original_update", None)
                _is_inline_upd = (_raw_upd is not None and
                                  type(_raw_upd).__name__ == "UpdateInlineBotCallbackQuery")
                if not _is_inline_upd:
                    await event.answer()
                import logging as _cblog
                _cbl = _cblog.getLogger("bot_callbacks")
                _raw = getattr(event, 'original_update', event)
                _cbl.info(
                    f"[compat_cb] data={data!r} "
                    f"chat_id={getattr(event, 'chat_id', None)!r} "
                    f"message_id={getattr(event, 'message_id', None)!r} "
                    f"inline_message_id={getattr(event, 'inline_message_id', None)!r} "
                    f"sender_id={getattr(event, 'sender_id', None)!r} "
                    f"peer_id={getattr(event, 'peer_id', None)!r} "
                    f"raw_type={type(_raw).__name__} "
                    f"raw_attrs={[a for a in dir(_raw) if not a.startswith('_') and 'inline' in a.lower() or 'msg_id' in a.lower()]!r}"
                )
                import logging as _cbil
                _cbil.getLogger("bot_callbacks").info(
                    f"[compat_cb_call] calling {getattr(func, '__name__', func)!r} args_count={len(args)}"
                )
                await func(_HtmlCallProxy(event), *args)
            except Exception as _ce:
                import logging as _cel
                _cel.getLogger("bot_callbacks").error(f"[compat_cb_call] EXCEPTION in {getattr(func, '__name__', func)!r}: {_ce}")
                traceback.print_exc()
            return

        # ── Приоритет 2: паттерн-матчинг из CALLBACK_REGISTRY ────────────────
        # Сортируем: специфичные паттерны (не ".*") идут раньше универсальных.
        _cb_specific = []
        _cb_wildcard = []
        for _pat, _hf in CALLBACK_REGISTRY.items():
            _ps = getattr(_pat, "pattern", "")
            if _ps in (".*", ".+", ""):
                _cb_wildcard.append((_pat, _hf))
            else:
                _cb_specific.append((_pat, _hf))

        for pattern, handler_func in (_cb_specific + _cb_wildcard):
            _pat_str = getattr(pattern, "pattern", None)
            _match_bytes = isinstance(_pat_str, bytes)
            _match_target = event.data if _match_bytes else data
            match = pattern.match(_match_target)
            if not match and not _match_bytes:
                try:
                    match = pattern.match(event.data)
                except TypeError:
                    pass
            if match:
                event.pattern_match = match
                _is_public_handler = getattr(handler_func, "_is_inline_everyone", False) or                                      getattr(handler_func, "_is_unrestricted", False)
                if not _is_public_handler:
                    _sender = getattr(event, "sender_id", None)
                    if _sender and db.get_user_level(_sender) not in ["OWNER", "TRUSTED"]:
                        await event.answer("🚫 Доступ запрещён.", alert=True)
                        return
                import logging as _mlog
                _mlog.getLogger("bot_callbacks").info(f"[callback] matched pattern={_pat_str!r} handler={handler_func.__name__!r}")
                try:
                    await handler_func(_HtmlCallProxy(event))
                except Exception as _hex:
                    _mlog.getLogger("bot_callbacks").error(f"[callback] EXCEPTION in {handler_func.__name__!r}: {_hex}", exc_info=True)
                return

        text, buttons = None, None

        if data.startswith("load:"):
            module_name = data.split(":", 1)[1]
            if module_name == "all":
                from utils.loader import get_all_modules
                for mod in get_all_modules(): await load_module(user_client, mod)
                update_state_file(user_client)
                await event.answer("✅ Все модули загружены!", alert=True)
                text, buttons = build_main_panel(page=0, as_text=True, user_client=user_client)
            else:
                result = await load_module(user_client, module_name)
                update_state_file(user_client)
                await event.answer(result.get("message", "✅ Готово."), alert=True)
                text, buttons = build_module_menu(module_name, as_text=True)

        elif data.startswith("unload:"):
            module_name = data.split(":", 1)[1]
            if module_name == "all":
                for mod in list(user_client.modules.keys()): await unload_module(user_client, mod)
                update_state_file(user_client)
                await event.answer("🗑️ Все модули выгружены!", alert=True)
                text, buttons = build_main_panel(page=0, as_text=True, user_client=user_client)
            else:
                result = await unload_module(user_client, module_name)
                update_state_file(user_client)
                await event.answer(result.get("message", "✅ Готово."), alert=True)
                text, buttons = build_module_menu(module_name, as_text=True)

        elif data.startswith("reload:"):
            module_name = data.split(":", 1)[1]
            if module_name == "all":
                for mod in list(user_client.modules.keys()): await reload_module(user_client, mod)
                update_state_file(user_client)
                await event.answer("♻️ Все модули перезагружены!", alert=True)
                text, buttons = build_main_panel(page=0, as_text=True, user_client=user_client)
            else:
                result = await reload_module(user_client, module_name)
                update_state_file(user_client)
                await event.answer(result.get("message", "✅ Готово."), alert=True)
                text, buttons = build_module_menu(module_name, as_text=True)

        elif data.startswith("page:"):
            page = int(data.split(":")[1])
            text, buttons = build_main_panel(page=page, as_text=True, user_client=user_client)

        elif data.startswith("module:"):
            module_name = data.split(":")[1].lower()
            
            # Проверка доступа для TRUSTED
            if db.get_user_level(event.sender_id) == "TRUSTED":
                # Сначала персональные, потом глобальные
                allowed = db.get_setting(f"allowed_mods_{event.sender_id}")
                if not allowed:
                    allowed = db.get_setting("allowed_mods_TRUSTED", default="wisp")
                
                if allowed.lower() != "all" and module_name not in [m.strip().lower() for m in allowed.split(",")]:
                    return await event.answer("🚫 У вас нет доступа к этому модулю.", alert=True)

            text, buttons = build_module_menu(module_name, as_text=True)

        elif data == "global_menu":
            if db.get_user_level(event.sender_id) == "TRUSTED":
                 return await event.answer("🚫 Глобальное меню доступно только владельцу.", alert=True)
            text, buttons = build_global_menu(as_text=True)

        elif data in ["back_to_main", "refresh"]:
            text, buttons = build_main_panel(page=0, as_text=True, user_client=user_client)

        if text and buttons:
            await event.edit(text, buttons=buttons, parse_mode="html")

    except MessageNotModifiedError:
        await event.answer() 
    except Exception:
        traceback.print_exc()
        await event.answer("Произошла ошибка при обработке вашего запроса.", alert=True)