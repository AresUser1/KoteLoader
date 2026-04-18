# compat/heroku_loader.py
"""
Загрузчик Heroku/Hikka-совместимых модулей.

Решает 3 проблемы:
  1. from .. import loader, utils  — относительный импорт из пакета
  2. import herokutl               — алиас на telethon
  3. from herokutl.xxx import yyy  — то же самое

Использование:
  from compat.heroku_loader import load_heroku_module
  result = await load_heroku_module(client, "/path/to/module.py")
"""

import sys
import types
import logging
import asyncio
import importlib
import importlib.util
import inspect
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Адаптер БД: эмулирует db.get/db.set из Heroku/Hikka
# ═══════════════════════════════════════════════════════════════════════════

class _DbAdapter:
    """
    Оборачивает utils.database KoteLoader в API Heroku/Hikka:
      db.get("ModuleName", "key")           → get_module_data(...)
      db.set("ModuleName", "key", value)    → set_module_data(...)
      db.get("ModuleName", "key", default)  → get_module_data(..., default=default)
    """
    def __init__(self, db_module):
        self._db = db_module

    def get(self, module_name: str, key: str, default=None):
        return self._db.get_module_data(module_name, key, default=default)

    def set(self, module_name: str, key: str, value):
        self._db.set_module_data(module_name, key, value)

    def delete(self, module_name: str, key: str):
        self._db.remove_module_data(module_name, key)

    def __getattr__(self, name):
        # Остальные методы проксируем напрямую
        return getattr(self._db, name)


# ═══════════════════════════════════════════════════════════════════════════
# Шаг 1: Патчим sys.modules так чтобы herokutl → telethon
# ═══════════════════════════════════════════════════════════════════════════

def _patch_herokutl():
    """
    Регистрирует herokutl как алиас telethon в sys.modules,
    а hikka/dragon/watgbridge — как алиасы нашего compat-пакета,
    чтобы `from hikka import loader, utils` работало как `from .. import loader, utils`.
    Вызывается один раз при первой загрузке Heroku/Hikka-модуля.
    """
    # Hikka-совместимость: from hikka import loader, utils
    # Регистрируем каждый раз, т.к. _create_fake_package мог ещё не вызваться
    for _fw in ("hikka", "dragon", "watgbridge"):
        if _fw not in sys.modules:
            _fw_pkg = types.ModuleType(_fw)
            _fw_pkg.__path__ = []
            _fw_pkg.__package__ = _fw
            sys.modules[_fw] = _fw_pkg
        # loader и utils подключим позже в _create_fake_package через _sync_compat_frameworks()

    if "herokutl" in sys.modules:
        return

    import telethon
    import telethon.tl
    import telethon.tl.types
    import telethon.tl.functions
    import telethon.errors
    import telethon.errors.rpcerrorlist

    # Основной модуль
    sys.modules["herokutl"] = telethon

    # Часто используемые подмодули
    _aliases = {
        "herokutl.tl":                      "telethon.tl",
        "herokutl.tl.types":                "telethon.tl.types",
        "herokutl.tl.functions":            "telethon.tl.functions",
        "herokutl.tl.functions.contacts":   "telethon.tl.functions.contacts",
        "herokutl.tl.functions.messages":   "telethon.tl.functions.messages",
        "herokutl.tl.custom":               "telethon.tl.custom",
        "herokutl.errors":                  "telethon.errors",
        "herokutl.errors.rpcerrorlist":     "telethon.errors.rpcerrorlist",
        "herokutl.errors.common":           "telethon.errors",   # нет точного аналога
        "herokutl.hints":                   "telethon.hints",
        "herokutl.sessions":                "telethon.sessions",
        "herokutl.crypto":                  "telethon.crypto",
    }

    for fake, real in _aliases.items():
        if fake not in sys.modules:
            try:
                sys.modules[fake] = importlib.import_module(real)
            except ImportError:
                # Создаём пустой модуль-заглушку
                sys.modules[fake] = types.ModuleType(fake)

    # Специальный случай: herokutl.errors.common.ScamDetectionError
    # В telethon такого нет — добавим заглушку
    common_mod = sys.modules.get("herokutl.errors.common")
    if common_mod and not hasattr(common_mod, "ScamDetectionError"):
        class ScamDetectionError(Exception):
            pass
        common_mod.ScamDetectionError = ScamDetectionError

    logger.debug("[compat] herokutl → telethon aliased")

    # ── Патч get_entity: убираем неизвестный kwarg 'exp' ─────────────────
    # Hikka/Heroku модули (HikariChat и др.) вызывают:
    #   await self._client.get_entity(chat, exp=0)
    # Стандартный Telethon не принимает аргумент 'exp' → TypeError.
    # Патчим на уровне класса один раз.
    try:
        from telethon import TelegramClient as _TGC
        if not getattr(_TGC, "_get_entity_exp_patched", False):
            _orig_get_entity = _TGC.get_entity

            async def _get_entity_patched(self, entity, exp=None, **kwargs):
                # 'exp' — параметр cache-expiry из hikkatl/herokutl форков,
                # в стандартном Telethon его нет — просто игнорируем.
                return await _orig_get_entity(self, entity, **kwargs)

            _TGC.get_entity = _get_entity_patched
            _TGC._get_entity_exp_patched = True
            logger.debug("[compat] get_entity patched: 'exp' kwarg will be ignored")
    except Exception as _ge_err:
        logger.warning(f"[compat] get_entity patch failed (non-critical): {_ge_err}")


# ═══════════════════════════════════════════════════════════════════════════
# Шаг 2: Создаём фейковый пакет чтобы from .. import loader, utils работало
# ═══════════════════════════════════════════════════════════════════════════

def _create_fake_package(package_name: str):
    """
    Создаёт иерархию фейковых пакетов в sys.modules:

      heroku_compat_pkg            ← корень, содержит .loader и .utils
      heroku_compat_pkg.modules    ← промежуточный (создаётся в _load_source_as_package)
      heroku_compat_pkg.modules.X  ← сам модуль

    `from .. import loader` из X поднимается до heroku_compat_pkg — там всё есть.
    """
    from compat import loader as compat_loader
    from compat.loader import utils as compat_utils

    # Всегда обновляем — могут звать повторно для нового модуля
    if package_name not in sys.modules:
        pkg = types.ModuleType(package_name)
        pkg.__path__ = []
        pkg.__package__ = package_name
        sys.modules[package_name] = pkg
    else:
        pkg = sys.modules[package_name]

    # Ключевые атрибуты на корневом пакете
    pkg.loader = compat_loader
    pkg.utils = compat_utils

    # utils как полноценный submodule
    utils_mod = types.ModuleType(f"{package_name}.utils")
    utils_mod.__package__ = package_name
    for attr in dir(compat_utils):
        if not attr.startswith("__"):
            setattr(utils_mod, attr, getattr(compat_utils, attr))
    # Добавляем сами методы напрямую (utils.answer и т.д.)
    for name in ("answer", "escape_html", "get_args_raw", "get_args",
                 "get_chat_id", "get_display_name"):
        fn = getattr(compat_utils, name, None)
        if fn:
            setattr(utils_mod, name, fn)

    sys.modules[f"{package_name}.loader"] = compat_loader
    sys.modules[f"{package_name}.utils"] = utils_mod

    # ── Синкаем hikka/dragon/watgbridge → наш compat-слой ───────────────
    # Это позволяет `from hikka import loader, utils` работать напрямую.
    for _fw in ("hikka", "dragon", "watgbridge"):
        if _fw not in sys.modules:
            _fw_mod = types.ModuleType(_fw)
            _fw_mod.__path__ = []
            sys.modules[_fw] = _fw_mod
        else:
            _fw_mod = sys.modules[_fw]
        _fw_mod.loader = compat_loader
        _fw_mod.utils = utils_mod
        sys.modules[f"{_fw}.loader"] = compat_loader
        sys.modules[f"{_fw}.utils"] = utils_mod

    # ── Заглушки для всех остальных относительных импортов ──────────────
    # from ..types import CoreOverwriteError  (FHeta)
    types_mod = types.ModuleType(f"{package_name}.types")
    class CoreOverwriteError(Exception): pass
    class StopPropagation(Exception): pass
    types_mod.CoreOverwriteError = CoreOverwriteError
    types_mod.StopPropagation = StopPropagation
    sys.modules[f"{package_name}.types"] = types_mod
    pkg.types = types_mod

    # from ..inline.types import InlineCall, InlineMessage
    inline_mod = types.ModuleType(f"{package_name}.inline")
    inline_types_mod = types.ModuleType(f"{package_name}.inline.types")

    class InlineCall:
        """
        Hikka InlineCall — объект передаваемый в @loader.callback_handler обработчики.
        Методы делегируют реальные Telegram-операции через сохранённый event.
        Реальный event устанавливается через InlineCall._bind(event) после создания.
        """
        def __init__(self):
            self.message_id = None
            self.chat_id = None
            self._event = None  # устанавливается в bot_callbacks при вызове

        @classmethod
        def _bind(cls, event):
            """Создаёт InlineCall привязанный к реальному Telethon event."""
            obj = cls()
            obj._event = event
            obj.message_id = getattr(event, "message_id", None)
            obj.chat_id = getattr(event, "chat_id", None)
            return obj

        @property
        def data(self):
            d = getattr(self._event, "data", b"") if self._event else b""
            return d.encode() if isinstance(d, str) else (d or b"")

        @property
        def from_user(self):
            if self._event is None:
                return None
            class _FU:
                def __init__(self, ev):
                    self.id = getattr(ev, "sender_id", None)
            return _FU(self._event)

        async def answer(self, text="", show_alert=False, alert=None, **kw):
            if alert is not None:
                show_alert = alert
            if self._event is None:
                return
            try:
                from handlers.bot_callbacks import _HtmlCallProxy
                await _HtmlCallProxy(self._event).answer(text, show_alert=show_alert)
            except Exception as _e:
                import logging; logging.getLogger(__name__).warning(f"InlineCall.answer: {_e}")

        async def delete(self):
            if self._event is None:
                return
            try:
                from handlers.bot_callbacks import _HtmlCallProxy
                await _HtmlCallProxy(self._event).delete()
            except Exception as _e:
                import logging; logging.getLogger(__name__).warning(f"InlineCall.delete: {_e}")

        async def edit(self, text, reply_markup=None, parse_mode="html", **kw):
            if self._event is None:
                return
            try:
                from handlers.bot_callbacks import _HtmlCallProxy
                await _HtmlCallProxy(self._event).edit(text, reply_markup=reply_markup,
                                                       parse_mode=parse_mode, **kw)
            except Exception as _e:
                import logging; logging.getLogger(__name__).warning(f"InlineCall.edit: {_e}")

    class InlineMessage:
        """
        Hikka InlineMessage — объект для редактирования inline-сообщений отправленных ботом.
        Создаётся когда бот отправил сообщение через inline-режим и нужно его изменить.
        """
        def __init__(self):
            self.message_id = None
            self.chat_id = None
            self._client = None
            self._bot = None

        @classmethod
        def _bind(cls, client, bot, chat_id, message_id):
            obj = cls()
            obj._client = client
            obj._bot = bot
            obj.chat_id = chat_id
            obj.message_id = message_id
            return obj

        async def delete(self):
            if self._bot and self.chat_id and self.message_id:
                try:
                    await self._bot.delete_messages(self.chat_id, [self.message_id])
                except Exception as _e:
                    import logging; logging.getLogger(__name__).warning(f"InlineMessage.delete: {_e}")

        async def edit(self, text, reply_markup=None, parse_mode="html", **kw):
            if self._bot and self.chat_id and self.message_id:
                try:
                    from compat.loader import _BotStub
                    bs = _BotStub(self._client, bot_client=self._bot)
                    await bs.edit_message_text(
                        chat_id=self.chat_id,
                        message_id=self.message_id,
                        text=text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode,
                    )
                except Exception as _e:
                    import logging; logging.getLogger(__name__).warning(f"InlineMessage.edit: {_e}")
    inline_types_mod.InlineCall = InlineCall
    inline_types_mod.InlineMessage = InlineMessage
    inline_mod.types = inline_types_mod
    sys.modules[f"{package_name}.inline"] = inline_mod
    sys.modules[f"{package_name}.inline.types"] = inline_types_mod
    pkg.inline = inline_mod



# ═══════════════════════════════════════════════════════════════════════════
# Шаг 3: Загружаем .py файл с патченым окружением
# ═══════════════════════════════════════════════════════════════════════════

def _load_source_as_package(file_path: Path, package_name: str) -> types.ModuleType:
    """
    Загружает Python-файл как модуль внутри пакета `package_name`.

    Герку-модули используют `from .. import loader, utils` — это относительный
    импорт на уровень вверх от своего пакета.

    Схема:
      sys.modules["heroku_compat_pkg"]           ← содержит .loader и .utils
      sys.modules["heroku_compat_pkg.modules"]   ← промежуточный пакет
      sys.modules["heroku_compat_pkg.modules.FHeta"]  ← сам модуль

    Тогда `from .. import loader` из `heroku_compat_pkg.modules.FHeta`
    поднимается до `heroku_compat_pkg` — где loader уже есть.
    """
    # Промежуточный пакет (аналог папки modules/ в Heroku)
    sub_pkg = f"{package_name}.modules"
    if sub_pkg not in sys.modules:
        pkg = types.ModuleType(sub_pkg)
        pkg.__path__ = [str(file_path.parent)]
        pkg.__package__ = sub_pkg
        sys.modules[sub_pkg] = pkg

    module_name = f"{sub_pkg}.{file_path.stem}"

    spec = importlib.util.spec_from_file_location(
        module_name,
        str(file_path),
        submodule_search_locations=[]
    )
    mod = importlib.util.module_from_spec(spec)
    # __package__ = sub_pkg означает что `..` поднимается до package_name
    mod.__package__ = sub_pkg
    mod.__name__ = module_name

    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ═══════════════════════════════════════════════════════════════════════════
# Публичный API
# ═══════════════════════════════════════════════════════════════════════════

FAKE_PACKAGE = "heroku_compat_pkg"


import re as _re

import functools as _functools
import re as _re2

_HTML_TAG_RE = _re2.compile(r"<(?:b|i|u|s|code|pre|a|emoji|spoiler)[ />]", _re2.I)
_EMOJI_TAG_RE = _re2.compile(
    r'<emoji\s+document_id=["\'\']?(\d+)["\'\']?>(.*?)</emoji>',
    _re2.DOTALL | _re2.IGNORECASE
)
_PATCHED_CLIENTS = set()


def _has_html(text):
    return bool(_HTML_TAG_RE.search(text))


def _has_emoji_tags(text):
    return bool(_EMOJI_TAG_RE.search(text))


def _parse_emoji_html(text):
    """
    Парсит HTML с <emoji document_id=...> и <blockquote expandable> тегами.
    Возвращает (plain_text, all_entities) готовые для formatting_entities.
    """
    import re as _re_local
    import html as _html_mod
    from telethon.tl.types import MessageEntityCustomEmoji, MessageEntityBlockquote
    from telethon.extensions import html as tl_html

    # Заменяем <blockquote expandable>...</blockquote> на обычный <blockquote>
    # но запоминаем позиции для collapsed=True entity
    expandable_ranges = []  # список (start_plain_idx, content_plain)
    _BQ_EXP_RE = _re_local.compile(r'<blockquote\s+expandable\s*>(.*?)</blockquote>', _re_local.DOTALL | _re_local.IGNORECASE)

    def _mark_expandable(m):
        expandable_ranges.append(m.group(1))
        return f'<blockquote>{m.group(1)}</blockquote>'

    text_normalized = _BQ_EXP_RE.sub(_mark_expandable, text)

    # Убираем <emoji> теги, оставляем содержимое — получаем чистый HTML
    clean_html = _EMOJI_TAG_RE.sub(lambda m: m.group(2), text_normalized)
    # Парсим через Telethon — получаем plain text + entities для b/i/code/blockquote/etc
    parsed_text, base_entities = tl_html.parse(clean_html)

    # Исправляем blockquote entities на collapsed=True для expandable
    if expandable_ranges and base_entities:
        from telethon.extensions import html as tl_html2
        search_from = 0
        for bq_html in expandable_ranges:
            # Получаем plain text контента blockquote
            bq_plain, _ = tl_html2.parse(bq_html)
            if not bq_plain:
                continue
            idx = parsed_text.find(bq_plain, search_from)
            if idx == -1:
                continue
            utf16_offset = len(parsed_text[:idx].encode("utf-16-le")) // 2
            utf16_length = len(bq_plain.encode("utf-16-le")) // 2
            # Ищем совпадающий MessageEntityBlockquote и заменяем на collapsed
            for i, ent in enumerate(base_entities):
                if (isinstance(ent, MessageEntityBlockquote)
                        and ent.offset == utf16_offset
                        and ent.length == utf16_length):
                    try:
                        base_entities[i] = MessageEntityBlockquote(
                            offset=utf16_offset, length=utf16_length, collapsed=True
                        )
                    except TypeError:
                        # Старая версия telethon без collapsed параметра — оставляем как есть
                        pass
                    break
            search_from = idx + len(bq_plain)

    # Ищем позицию каждого emoji в parsed_text последовательно
    custom_entities = []
    search_from = 0
    for m in _EMOJI_TAG_RE.finditer(text):
        doc_id = int(m.group(1))
        # Контент emoji после удаления возможных вложенных тегов и unescape
        content_html = m.group(2)
        content_plain, _ = tl_html.parse(content_html)

        if not content_plain:
            continue

        idx = parsed_text.find(content_plain, search_from)
        if idx == -1:
            continue

        utf16_offset = len(parsed_text[:idx].encode("utf-16-le")) // 2
        utf16_length = len(content_plain.encode("utf-16-le")) // 2
        if utf16_length > 0:
            custom_entities.append(MessageEntityCustomEmoji(
                offset=utf16_offset, length=utf16_length, document_id=doc_id,
            ))
        search_from = idx + len(content_plain)

    return parsed_text, list(base_entities or []) + custom_entities


async def _send_html_with_emoji(send_func, target, text, **kwargs):
    """
    Отправляет через client.send_message(entity, text, ...) с premium emoji.
    target — entity (chat_id, peer и т.п.)
    """
    try:
        parsed_text, all_entities = _parse_emoji_html(text)
        kwargs.pop("parse_mode", None)
        return await send_func(target, parsed_text, formatting_entities=all_entities, **kwargs)
    except Exception as e:
        logger.warning(f"[compat] emoji send failed: {e}")
        clean = _EMOJI_TAG_RE.sub(lambda m: m.group(2), text)
        kwargs.setdefault("parse_mode", "html")
        return await send_func(target, clean, **kwargs)


async def _edit_html_with_emoji(edit_func, text, **kwargs):
    """
    Отправляет через msg.edit(text, ...) с premium emoji.
    edit_func — bound метод msg.edit, не принимает entity.
    """
    try:
        parsed_text, all_entities = _parse_emoji_html(text)
        kwargs.pop("parse_mode", None)
        return await edit_func(parsed_text, formatting_entities=all_entities, **kwargs)
    except Exception as e:
        logger.warning(f"[compat] emoji edit failed: {e}")
        clean = _EMOJI_TAG_RE.sub(lambda m: m.group(2), text)
        kwargs.setdefault("parse_mode", "html")
        return await edit_func(clean, **kwargs)


def _patch_client_html(client):
    cid = id(client)
    if cid in _PATCHED_CLIENTS:
        return
    _PATCHED_CLIENTS.add(cid)

    _orig_send = client.send_message
    _orig_edit = client.edit_message

    async def _smart_send(entity, message="", *args, **kwargs):
        if isinstance(message, str) and "parse_mode" not in kwargs and "formatting_entities" not in kwargs:
            if _has_emoji_tags(message):
                return await _send_html_with_emoji(_orig_send, entity, message, **kwargs)
            if _has_html(message):
                kwargs["parse_mode"] = "html"
        return await _orig_send(entity, message, *args, **kwargs)

    async def _smart_edit(entity, message, *args, **kwargs):
        text = kwargs.get("text") or (message if isinstance(message, str) else "")
        if isinstance(text, str) and "parse_mode" not in kwargs and "formatting_entities" not in kwargs:
            if _has_emoji_tags(text):
                return await _send_html_with_emoji(_orig_edit, entity, message, **kwargs)
            if _has_html(text):
                kwargs["parse_mode"] = "html"
        return await _orig_edit(entity, message, *args, **kwargs)

    client.send_message = _smart_send
    client.edit_message = _smart_edit


def _wrap_html_handler(func):
    @_functools.wraps(func)
    async def wrapper(event):
        msg = getattr(event, "message", None) or event

        for method_name in ("edit", "reply", "respond"):
            orig = getattr(msg, method_name, None)
            if orig is None:
                continue

            def _make_patched(orig_m):
                @_functools.wraps(orig_m)
                async def _patched(text=None, *a, **kw):
                    if isinstance(text, str) and "parse_mode" not in kw and "formatting_entities" not in kw:
                        if _has_emoji_tags(text):
                            # msg.edit(text) — bound метод, entity не нужен
                            return await _edit_html_with_emoji(orig_m, text, **kw)
                        if _has_html(text):
                            kw["parse_mode"] = "html"
                    return await orig_m(text, *a, **kw)
                return _patched

            try:
                setattr(msg, method_name, _make_patched(orig))
            except (AttributeError, TypeError):
                pass

        return await func(event)
    return wrapper



_HTML_TAG_RE = _re.compile(r"<(?:b|i|u|s|code|pre|a|emoji|spoiler)[ />]", _re.I)
_PATCHED_CLIENTS = set()

def _patch_client_html(client):
    """
    Оборачивает client.send_message и client.edit_message чтобы автоматически
    применять parse_mode="html" если в тексте есть HTML-теги и parse_mode не задан.
    Патч применяется один раз на клиент (проверяем по id объекта).
    """
    cid = id(client)
    if cid in _PATCHED_CLIENTS:
        return
    _PATCHED_CLIENTS.add(cid)

    _orig_send = client.send_message
    _orig_edit = client.edit_message

    async def _smart_send(entity, message="", *args, **kwargs):
        if isinstance(message, str) and "parse_mode" not in kwargs:
            if _HTML_TAG_RE.search(message):
                kwargs["parse_mode"] = "html"
        return await _orig_send(entity, message, *args, **kwargs)

    async def _smart_edit(entity, message, *args, **kwargs):
        text = kwargs.get("text") or (message if isinstance(message, str) else "")
        if isinstance(text, str) and "parse_mode" not in kwargs:
            if _HTML_TAG_RE.search(text):
                kwargs["parse_mode"] = "html"
        return await _orig_edit(entity, message, *args, **kwargs)

    client.send_message = _smart_send
    client.edit_message = _smart_edit


async def _install_module_requires(file_path) -> list:
    """
    Читает заголовок файла модуля (первые 60 строк), парсит строки вида:
      # requires: aiohttp websockets requests>=2.0
      # requires: git+https://github.com/user/repo

    Для каждого пакета:
      - Для git+https:// URL — определяет import-имя из egg= или имени репозитория.
      - Проверяет importlib.util.find_spec() — если уже установлен, пропускает.
      - Если нет — устанавливает через `pip install --quiet` в фоне.

    Возвращает список установленных пакетов.
    """
    import importlib.util
    import asyncio
    import re as _re

    # Маппинг: pip-имя (lowercase) -> import-имя для проверки наличия
    _PIP_TO_IMPORT = {
        "pillow": "PIL",
        "pycryptodome": "Crypto",
        "pycryptodomex": "Cryptodome",
        "python-dateutil": "dateutil",
        "python-telegram-bot": "telegram",
        "beautifulsoup4": "bs4",
        "scikit-learn": "sklearn",
        "opencv-python": "cv2",
        "opencv-python-headless": "cv2",
        "pytz": "pytz",
        "attrs": "attr",
        "typing-extensions": "typing_extensions",
        # git-пакеты по имени репо → import-имя
        "yandex-music-api": "yandex_music",
        "yandex_music": "yandex_music",
    }

    # Маппинг известных git-репо → import-имя
    # ключ: часть URL (lowercase repo name или egg= значение)
    _GIT_REPO_TO_IMPORT = {
        "yandex-music-api": "yandex_music",
        "yandex_music": "yandex_music",
        "telethon": "telethon",
        "pyrogram": "pyrogram",
    }

    def _get_import_name_for_git(pkg_spec: str) -> str:
        """
        Для git+https://github.com/user/repo[@branch][#egg=name]
        определяет import-имя:
          1. egg=name в URL → _PIP_TO_IMPORT.get(name) or name.replace("-","_")
          2. последний сегмент URL (имя репо) → _GIT_REPO_TO_IMPORT.get(repo)
          3. fallback: имя репо без суффикса .git → заменяем - на _
        """
        # Извлекаем egg= если есть: git+https://...#egg=yandex-music
        egg_m = _re.search(r"#egg=([A-Za-z0-9_\-]+)", pkg_spec)
        if egg_m:
            egg_name = egg_m.group(1).lower()
            return _PIP_TO_IMPORT.get(egg_name, egg_name.replace("-", "_"))

        # Берём имя репо из URL: последний сегмент пути до @ или #
        # git+https://github.com/MarshalX/yandex-music-api → yandex-music-api
        url_path = _re.split(r"[@#]", pkg_spec.rstrip("/"))[0]
        repo_name = url_path.rstrip("/").rsplit("/", 1)[-1]
        # Убираем .git если есть
        repo_name = _re.sub(r"\.git$", "", repo_name, flags=_re.IGNORECASE)
        repo_lower = repo_name.lower()

        return _GIT_REPO_TO_IMPORT.get(repo_lower, repo_lower.replace("-", "_"))

    # Читаем заголовок
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            header = [next(f, "") for _ in range(60)]
    except Exception:
        return []

    # Парсим все строки # requires:
    packages = []
    for line in header:
        m = _re.match(r"^\s*#\s*requires?\s*:\s*(.+)", line, _re.IGNORECASE)
        if m:
            pkgs = m.group(1).strip().split()
            packages.extend(pkgs)

    if not packages:
        return []

    installed = []
    for pkg_spec in packages:
        # ── git+https:// обрабатываем отдельно ──────────────────────────
        if pkg_spec.startswith("git+"):
            import_name = _get_import_name_for_git(pkg_spec)
            pip_spec = pkg_spec  # полный URL передаём в pip как есть
            display_name = import_name
        else:
            # Обычный пакет: aiohttp>=3.0 → pip_spec=aiohttp>=3.0, name=aiohttp
            pip_spec = pkg_spec
            pkg_name = _re.split(r"[><=!;]", pkg_spec)[0].strip()
            pkg_lower = pkg_name.lower()
            import_name = _PIP_TO_IMPORT.get(pkg_lower, pkg_lower.replace("-", "_"))
            display_name = pkg_name

        # Проверяем наличие
        try:
            if importlib.util.find_spec(import_name) is not None:
                logger.debug(f"[requires] {display_name} уже установлен, пропускаем")
                continue
        except (ModuleNotFoundError, ValueError):
            # find_spec бросает ModuleNotFoundError если имя содержит недопустимые символы
            # (например, если import_name каким-то образом содержит "/" или "+")
            # В таком случае просто пробуем установить
            logger.warning(f"[requires] find_spec({import_name!r}) failed, попробуем установить")

        # Устанавливаем
        logger.info(f"[requires] Устанавливаем {display_name} ({pip_spec})...")
        try:
            proc = await asyncio.create_subprocess_exec(
                "pip", "install", "--quiet", "--no-input", pip_spec,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode == 0:
                logger.info(f"[requires] ✅ {display_name} успешно установлен")
                installed.append(display_name)
                importlib.invalidate_caches()
            else:
                err_text = stderr.decode(errors="replace").strip()
                logger.warning(f"[requires] ❌ Не удалось установить {pip_spec}: {err_text}")
        except asyncio.TimeoutError:
            logger.warning(f"[requires] ⏱ Таймаут установки {pip_spec}")
        except FileNotFoundError:
            logger.warning(f"[requires] pip не найден, пропускаем {pip_spec}")
        except Exception as e:
            logger.warning(f"[requires] Ошибка установки {pip_spec}: {e}")

    return installed

async def load_heroku_module(client, file_path: str | Path, chat_id: int = None) -> dict:
    """
    Загружает Heroku/Hikka-совместимый модуль.

    Возвращает dict:
      {"status": "ok", "module_name": ..., "commands": [...]}
      {"status": "error", "message": ...}
    """
    from compat.loader import Module as HerokuModule, ModuleConfig, _CallbackRegistry
    from utils import database as db_module
    import utils.loader as _loader_mod
    _COMMANDS = _loader_mod.COMMANDS_REGISTRY
    _CALLBACKS = _loader_mod.CALLBACK_REGISTRY
    _WATCHERS = _loader_mod.WATCHERS_REGISTRY

    file_path = Path(file_path)
    if not file_path.exists():
        return {"status": "error", "message": f"Файл не найден: {file_path}"}

    # 1. Патчим herokutl
    _patch_herokutl()

    # 1.5. Устанавливаем compatibility shims ДО загрузки модуля
    #
    # aiogram shim: чужие модули делают
    #   from aiogram.utils.exceptions import MessageCantBeDeleted   ← aiogram 2 API
    # на машинах с aiogram 3.x или вообще без aiogram (Termux).
    try:
        from compat.aiogram_shim import install_aiogram_shim
        install_aiogram_shim()
    except Exception as _shim_err:
        logger.warning(f"[compat] aiogram_shim install failed (non-critical): {_shim_err}")

    # imghdr shim: модуль imghdr убрали из stdlib в Python 3.13.
    # Чужие модули (HikariChat и др.) делают `import imghdr` — на Python 3.13+ крашится.
    try:
        from compat.imghdr_shim import install_imghdr_shim
        install_imghdr_shim()
    except Exception as _shim_err:
        logger.warning(f"[compat] imghdr_shim install failed (non-critical): {_shim_err}")

    # 2. Парсим и устанавливаем зависимости из заголовка модуля
    # Поддерживаем форматы:
    #   # requires: aiohttp websockets requests
    #   # requires: aiohttp>=3.0 websockets
    await _install_module_requires(file_path)

    # 2b. Проверяем базовые зависимости которые модули часто используют
    # без объявления в # requires: (например HikariChat использует websockets молча)
    _BASE_DEPS = {"websockets": "websockets", "aiohttp": "aiohttp", "requests": "requests"}
    import importlib.util as _ilu
    for _imp_name, _pip_name in _BASE_DEPS.items():
        if _ilu.find_spec(_imp_name) is None:
            logger.info(f"[compat] auto-installing missing base dep: {_pip_name}")
            try:
                import asyncio as _aio_d, sys as _sys_d
                _dp = await _aio_d.create_subprocess_exec(
                    _sys_d.executable, "-m", "pip", "install", "-q",
                    "--disable-pip-version-check", _pip_name,
                    stdout=_aio_d.subprocess.PIPE, stderr=_aio_d.subprocess.PIPE,
                )
                await _dp.wait()
                import importlib as _il_d; _il_d.invalidate_caches()
            except Exception as _de:
                logger.warning(f"[compat] auto-install {_pip_name} failed: {_de}")

    # 3. Создаём фейковый пакет для from .. import loader, utils
    _create_fake_package(FAKE_PACKAGE)

    # 4. Загружаем модуль
    try:
        mod = _load_source_as_package(file_path, FAKE_PACKAGE)
    except Exception as e:
        import traceback
        return {
            "status": "error",
            "message": f"Ошибка импорта: {e}\n{traceback.format_exc()}"
        }

    # 4. Ищем класс модуля (subclass HerokuModule или имеет _is_heroku_module)
    module_instance = None
    for name, obj in inspect.getmembers(mod, inspect.isclass):
        if obj is HerokuModule:
            continue
        try:
            is_sub = issubclass(obj, HerokuModule)
        except TypeError:
            is_sub = False
        if is_sub or getattr(obj, "_is_heroku_module", False):
            module_instance = obj()
            break

    if module_instance is None:
        return {"status": "error", "message": "Класс модуля не найден (нет subclass loader.Module)"}

    # 5. Инжектируем зависимости
    module_instance.client = client
    module_instance._client = client   # алиас: Hikka-модули используют self._client
    # Оборачиваем db в адаптер: db.get("Mod","key") → db_module.get_module_data("Mod","key")
    module_instance.db = _DbAdapter(db_module)

    # _tg_id ставим на клиент ДО client_ready — HikariChatAPI._queue_processor
    # стартует внутри client_ready через asyncio.ensure_future и сразу читает client._tg_id
    _me_id = getattr(client, "_tg_id", None)
    if not _me_id:
        try:
            _me_id = (await client.get_me()).id
        except Exception:
            _me_id = 0
    client._tg_id = _me_id
    client.tg_id  = _me_id
    module_instance._tg_id = _me_id

    # inline manager — bot_username берём из bot_client (бот), а НЕ из client (юзербот)
    bot_client = getattr(client, "bot_client", None)
    bot_username = getattr(client, "bot_username", "") or ""
    if not bot_username and bot_client is not None:
        try:
            _bme = await bot_client.get_me()
            bot_username = getattr(_bme, "username", "") or ""
            client.bot_username = bot_username  # кэшируем
        except Exception:
            bot_username = ""
    from compat.loader import _InlineManager
    module_instance.inline = _InlineManager(client, bot_username, bot_client=bot_client)
    _patch_client_html(client)

    # Патчим клиент чтобы send_message/edit_message автоматически применяли
    # parse_mode="html" когда в тексте есть HTML-теги.
    # Heroku/Hikka модули (напр. GoyPulse) отправляют HTML без явного parse_mode.
    _patch_client_html(client)

    # ── Патчим client.inline_query ───────────────────────────────────────
    # Telethon имеет встроенный inline_query, но он возвращает сырые TL-объекты
    # без .title и .click(). Hikka-модули (FHeta и др.) вызывают:
    #   results = await self._client.inline_query(bot, "fheta __cmd__ gemini")
    #   await results[0].click(chat_id)
    # Перезаписываем как instance-атрибут (перекрывает class method Telethon)
    # чтобы вызов шёл к локальному inline_handler модуля напрямую.
    _iq_client_ref = client
    _iq_bot_ref = getattr(client, "bot_client", None)

    async def _inline_query_patch(bot_username_arg: str, query_text: str):
        from utils.loader import INLINE_HANDLERS_REGISTRY as _IHR
        from compat.loader import _FakeInlineResult
        results_holder = []

        # ── 1. _raw_handler: inline.form — делаем реальный GetInlineBotResultsRequest ──
        # Бот получит настоящий InlineQuery, bot_callbacks вызовет _raw_handler,
        # бот ответит через event.builder.article() с кнопками.
        # Результат возвращаем как _FakeInlineResult с _raw_tl_result для click().
        for _rpat, _rentry in list(_IHR.items()):
            if not _rentry.get("_raw_handler"):
                continue
            if not _rpat.match(query_text):
                continue
            try:
                from telethon import functions as _tl_fn
                _bot_un = bot_username_arg or getattr(_iq_client_ref, "bot_username", "")
                _iq_res = None
                try:
                    _iq_res = await _iq_client_ref(
                        _tl_fn.messages.GetInlineBotResultsRequest(
                            bot=_bot_un,
                            peer="me",
                            query=query_text,
                            offset="",
                        )
                    )
                except Exception as _iqe:
                    logger.warning(f"[compat] _raw_handler GetInlineBotResults: {_iqe}")
                if _iq_res and _iq_res.results:
                    return [_FakeInlineResult(
                        title=getattr(_iq_res.results[0], "title", ""),
                        message="",
                        buttons=None,
                        client=_iq_client_ref,
                        bot_client=_iq_bot_ref,
                        inline_query=query_text,
                        _raw_tl_result=_iq_res,
                    )]
                logger.warning(f"[compat] _raw_handler: бот не вернул результатов для {query_text!r}")
            except Exception as _re:
                logger.warning(f"[compat] _raw_handler inline_query failed: {_re}")
            return []

        # ── 2. hikka_style: FHeta и другие Hikka-модули ──────────────────
        matched_func = None
        matched_args = None
        for pat, entry in _IHR.items():
            if not entry.get("hikka_style"):
                continue
            m = pat.match(query_text)
            if m:
                prefix = entry.get("prefix", "")
                if prefix and query_text.startswith(prefix):
                    matched_args = query_text[len(prefix):].lstrip()
                elif m.lastindex and m.group(1) is not None:
                    matched_args = m.group(1).lstrip()
                else:
                    matched_args = query_text
                matched_func = entry["func"]
                break

        if matched_func is None:
            # ── 3. Обычные @inline_handler из utils/loader.py (не hikka_style) ──
            # Например, namelist из name_manager.py
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
                        _result = await _nentry["func"](_nev)
                        if isinstance(_result, tuple) and len(_result) == 2:
                            _text, _buttons = _result
                            return [_FakeInlineResult(
                                title=_nentry.get("title", query_text),
                                message=_text,
                                buttons=_buttons,
                                client=_iq_client_ref,
                                bot_client=_iq_bot_ref,
                                inline_query=query_text,
                            )]
                    except Exception as _ne:
                        logger.warning(f"[compat] inline_query native handler error: {_ne}")
                        import traceback; traceback.print_exc()
                    return []

            logger.warning(f"[compat] inline_query: нет hikka обработчика для {query_text!r}")
            return []

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

        if isinstance(ret, dict):
            return [_FakeInlineResult(
                title=ret.get("title", ""),
                message=ret.get("message", ret.get("title", "")),
                buttons=None,
                client=_iq_client_ref,
                bot_client=_iq_bot_ref,
                inline_query=query_text,
            )]

        fake = []
        for r in results_holder:
            if hasattr(r, "input_message_content"):
                imc  = r.input_message_content
                text = getattr(imc, "message_text", "") if imc else ""
                fake.append(_FakeInlineResult(
                    title=r.title or "",
                    message=text,
                    buttons=r.reply_markup,
                    client=_iq_client_ref,
                    bot_client=_iq_bot_ref,
                    inline_query=query_text,
                ))
        return fake

    client.inline_query = _inline_query_patch

    # Загружаем конфиг из БД
    mod_name = module_instance._get_module_name()
    if hasattr(module_instance, "config") and isinstance(module_instance.config, ModuleConfig):
        for key, cv in module_instance.config._meta.items():
            saved = db_module.get_module_config(mod_name, key)
            if saved is not None:
                module_instance.config.set_db_value(key, saved)

    # 6. Вызываем client_ready
    db_adapter = _DbAdapter(db_module)
    try:
        cr = module_instance.client_ready
        sig = inspect.signature(cr)
        params = list(sig.parameters.keys())
        # Hikka: client_ready(self)  /  старый: client_ready(self, client, db)
        if len(params) <= 1:
            await cr()
        else:
            await cr(client, db_adapter)
    except Exception as e:
        import traceback
        logger.warning(f"[compat] client_ready failed for {mod_name}: {e}\n{traceback.format_exc()}")
        print(f"[compat] client_ready error in {mod_name}: {e}")

    # 6b. Патчим HikariChatAPI.request() для работы в local-режиме без задержек.
    # В local-режиме request() кладёт данные в очередь, а _queue_processor
    # обрабатывает их через asyncio.sleep(1). Из-за этого .newfed создаёт
    # федерацию только через секунду — если сразу написать .fadd, fed не найден.
    # Патч применяет изменения к _feds/_chats немедленно, минуя очередь.
    try:
        _api = getattr(module_instance, "api", None)
        if _api is not None and not hasattr(_api, "_request_patched"):
            import random as _rnd, types as _types

            def _patched_request(self, payload: dict, message=None):
                # Добавляем в очередь как обычно
                try:
                    if message is not None:
                        try:
                            from utils import tools as _tools
                            import utils as _u
                            chat_id = _u.get_chat_id(message)
                        except Exception:
                            # fallback: peer_id у Telethon Message
                            peer = getattr(message, "peer_id", None)
                            if peer is not None:
                                chat_id = getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None) or getattr(peer, "user_id", None)
                                if chat_id and hasattr(peer, "channel_id") and peer.channel_id:
                                    chat_id = -int(str(chat_id).lstrip("-"))
                            else:
                                chat_id = getattr(message, "chat_id", None)
                        if chat_id:
                            payload = {
                                **payload,
                                "chat_id": chat_id,
                                "message_id": message.id,
                            }
                except Exception:
                    pass
                # В local-режиме НЕ добавляем в очередь — _queue_processor повторно применит
                # то же действие и выдаст "API Error: Chat is already in this federation".
                # В online-режиме добавляем как обычно — сервер обрабатывает через WebSocket.
                if not getattr(self, "_local", False):
                    self._queue += [payload]

                action = payload.get("action", "")
                args = payload.get("args", {})
                u = str(getattr(getattr(self, "_client", None), "_tg_id", "0") or "0")

                import logging as _hc_log
                _hcl = _hc_log.getLogger("compat.hikari_debug")
                _hcl.info(f"[HikariChat] request: action={action!r}, args={args}, owner={u}")
                _hcl.info(f"[HikariChat] _feds before: { {k: v.get('shortname') for k,v in self._feds.items()} }")

                def _sync_feds():
                    """Сохраняем в БД. self.feds в local-режиме строится через __getattr__."""
                    self.module.set("feds", self._feds)
                    # В local-режиме HikariChatAPI делает delattr(self, "feds") при init,
                    # после чего self.feds работает через __getattr__ и каждый раз строится
                    # из self._feds "на лету". Мы НЕ перезаписываем instance attr "feds",
                    # иначе __getattr__ перестаёт вызываться и find_fed видит устаревший кеш.
                    # Если всё же instance attr существует (online-режим) — обновляем его.
                    if "feds" in self.__dict__:
                        rebuilt = {v["shortname"]: v for v in self._feds.values()}
                        self.__dict__["feds"] = rebuilt
                        _hcl.info(f"[HikariChat] feds (instance attr) after sync: { list(rebuilt.keys()) }")
                    else:
                        rebuilt = {v["shortname"]: v for v in self._feds.values()}
                        _hcl.info(f"[HikariChat] feds after sync: { list(rebuilt.keys()) }")

                if action == "create federation":
                    sn = args.get("shortname", "")
                    name = args.get("name", "")
                    if sn and name:
                        # Проверяем что shortname не занят
                        if any(v.get("shortname") == sn for v in self._feds.values()):
                            _hcl.warning(f"[HikariChat] create federation: shortname {sn!r} уже занят")
                            return
                        t = "fed_" + "".join(
                            _rnd.choice("abcdefghijklmnopqrstuvwyz1234567890")
                            for _ in range(32)
                        )
                        self._feds[t] = {
                            "shortname": sn, "name": name, "chats": [],
                            "warns": {}, "admins": [u], "owner": u,
                            "fdef": [], "notes": {}, "uid": t,
                        }
                        _hcl.info(f"[HikariChat] created fed uid={t}, shortname={sn!r}, name={name!r}")
                        _sync_feds()

                elif action == "add chat to federation":
                    uid = args.get("uid", "")
                    cid = str(args.get("cid", ""))
                    _hcl.info(f"[HikariChat] add chat: uid={uid!r}, cid={cid!r}, known_feds={list(self._feds.keys())[:5]}")
                    if uid in self._feds and cid not in self._feds[uid]["chats"]:
                        self._feds[uid]["chats"].append(cid)
                        _hcl.info(f"[HikariChat] chat {cid} added to fed {uid}")
                        _sync_feds()
                    elif uid not in self._feds:
                        _hcl.warning(f"[HikariChat] add chat: uid={uid!r} не найден в _feds!")

                elif action == "remove chat from federation":
                    uid = args.get("uid", "")
                    cid = str(args.get("cid", ""))
                    if uid in self._feds and cid in self._feds[uid]["chats"]:
                        self._feds[uid]["chats"].remove(cid)
                        _hcl.info(f"[HikariChat] chat {cid} removed from fed {uid}")
                        _sync_feds()

                elif action == "delete federation":
                    uid = args.get("uid", "")
                    if uid in self._feds:
                        _hcl.info(f"[HikariChat] deleted fed uid={uid}")
                        del self._feds[uid]
                        _sync_feds()

                elif action == "warn user":
                    uid = args.get("uid", "")
                    user = str(args.get("user", ""))
                    reason = args.get("reason", "")
                    if uid in self._feds:
                        self._feds[uid].setdefault("warns", {}).setdefault(user, []).append(reason)
                        _hcl.info(f"[HikariChat] warn user={user} in fed={uid}, reason={reason!r}")
                        _sync_feds()

                elif action == "clear warns":
                    uid = args.get("uid", "")
                    user = str(args.get("user", ""))
                    if uid in self._feds:
                        self._feds[uid].get("warns", {}).pop(user, None)
                        _hcl.info(f"[HikariChat] cleared warns for user={user} in fed={uid}")
                        _sync_feds()

                elif action == "clear federation warns":
                    uid = args.get("uid", "")
                    if uid in self._feds:
                        self._feds[uid]["warns"] = {}
                        _hcl.info(f"[HikariChat] cleared all warns in fed={uid}")
                        _sync_feds()

                elif action == "update protections":
                    chat_key = str(args.get("chat", ""))
                    protection = args.get("protection", "")
                    state = args.get("state", "off")
                    if chat_key and protection:
                        # ВАЖНО: НЕ меняем self.chats здесь!
                        # HikariChat сам делает del/set после вызова request() —
                        # если мы сделаем pop() первыми, модуль упадёт с KeyError.
                        # Инициализируем чат если совсем нет записи (новый чат).
                        if chat_key not in self.chats:
                            self.chats[chat_key] = {}
                        _hcl.info(f"[HikariChat] protection {protection}={state} for chat {chat_key} (module will apply)")
                        # Сохраняем в БД через ensure_future с задержкой 0.3с —
                        # этого достаточно чтобы HikariChat успел сделать del/set
                        # внутри своего кода, и мы запишем уже актуальное состояние.
                        async def _save_chats_delayed(api_ref=self, prot=protection, st=state, ckey=chat_key):
                            import asyncio as _aio_d2
                            await _aio_d2.sleep(0.3)
                            try:
                                api_ref.module.set("chats", api_ref.chats)
                                _hcl.info(f"[HikariChat] chats saved to DB after {prot}={st} for {ckey}")
                                _hcl.info(f"[HikariChat] chat state now: {api_ref.chats.get(ckey, {})}")
                            except Exception as _sce:
                                _hcl.warning(f"[HikariChat] chats save failed: {_sce}")
                        try:
                            import asyncio as _aio_cs
                            _aio_cs.ensure_future(_save_chats_delayed())
                        except Exception:
                            pass

                elif action == "forgive user warn":
                    uid = args.get("uid", "")
                    user = str(args.get("user", ""))
                    if uid in self._feds and user in self._feds[uid].get("warns", {}):
                        warns = self._feds[uid]["warns"][user]
                        if warns:
                            del warns[-1]
                            if not warns:
                                del self._feds[uid]["warns"][user]
                            _hcl.info(f"[HikariChat] forgave last warn for user={user} in fed={uid}")
                            _sync_feds()
                        else:
                            _hcl.warning(f"[HikariChat] forgive warn: no warns for user={user}")
                    else:
                        _hcl.warning(f"[HikariChat] forgive warn: uid={uid!r} or user={user!r} not found")

                elif action == "clear all user warns":
                    uid = args.get("uid", "")
                    user = str(args.get("user", ""))
                    if uid in self._feds:
                        self._feds[uid].get("warns", {}).pop(user, None)
                        _hcl.info(f"[HikariChat] cleared all warns for user={user} in fed={uid}")
                        _sync_feds()
                    else:
                        _hcl.warning(f"[HikariChat] clear all warns: uid={uid!r} not found")

                elif action == "protect user":
                    uid = args.get("uid", "")
                    user = str(args.get("user", ""))
                    if uid in self._feds:
                        fdef = self._feds[uid].setdefault("fdef", [])
                        if user in fdef:
                            fdef.remove(user)
                            _hcl.info(f"[HikariChat] removed fdef for user={user} in fed={uid}")
                        else:
                            fdef.append(user)
                            _hcl.info(f"[HikariChat] added fdef for user={user} in fed={uid}")
                        _sync_feds()

                elif action == "rename federation":
                    uid = args.get("uid", "")
                    name = args.get("name", "")
                    if uid in self._feds and name:
                        self._feds[uid]["name"] = name
                        _hcl.info(f"[HikariChat] renamed fed={uid} to {name!r}")
                        _sync_feds()

                elif action == "new note":
                    uid = args.get("uid", "")
                    shortname = args.get("shortname", "")
                    note = args.get("note", "")
                    if uid in self._feds and shortname:
                        self._feds[uid].setdefault("notes", {})[shortname] = {
                            "creator": u, "text": note
                        }
                        _hcl.info(f"[HikariChat] new note {shortname!r} in fed={uid}")
                        _sync_feds()

                elif action == "delete note":
                    uid = args.get("uid", "")
                    shortname = args.get("shortname", "")
                    if uid in self._feds:
                        self._feds[uid].get("notes", {}).pop(shortname, None)
                        _hcl.info(f"[HikariChat] deleted note {shortname!r} from fed={uid}")
                        _sync_feds()

                else:
                    _hcl.warning(f"[HikariChat] неизвестный action: {action!r}")

            _api.request = _types.MethodType(_patched_request, _api)
            _api._request_patched = True
            logger.info(f"[compat] HikariChatAPI.request() patched for instant local apply ({mod_name})")
    except Exception as _patch_err:
        logger.debug(f"[compat] HikariChatAPI request patch skipped: {_patch_err}")

    # 7. Регистрируем обработчики
    from telethon import events
    from utils.loader import PREFIX

    registered_commands = []
    print(f"[compat] Scanning methods of {mod_name}...")

    for attr_name in dir(module_instance):
        try:
            func = getattr(module_instance, attr_name)
        except Exception:
            continue
        if not callable(func):
            continue

        # ── Старый стиль Hikka: метод называется <команда>cmd без декоратора ──
        # Например: амамамcmd, блятьcmd, searchcmd
        if (not getattr(func, "_is_command", False)
                and attr_name.endswith("cmd")
                and not attr_name.startswith("_")):
            cmd_name = attr_name[:-3]  # убираем суффикс "cmd"
            if cmd_name:
                # bound method нельзя патчить — патчим __func__
                _f = getattr(func, "__func__", func)
                _f._is_command = True
                _f._command_name = cmd_name
                _f._command_kwargs = {"outgoing": True}
                _f._command_doc = func.__doc__ or ""

        # ── Команды ──────────────────────────────────────────────────────
        if getattr(func, "_is_command", False):
            cmd = func._command_name
            pattern = re.compile(
                re.escape(PREFIX) + re.escape(cmd) + r"(?:\s+(.*))?\s*$",
                re.IGNORECASE | re.DOTALL
            )
            # Оставляем только kwargs которые понимает events.NewMessage
            _VALID_NM_KWARGS = {"chats", "blacklist_chats", "incoming", "outgoing",
                                "from_users", "forwards", "pattern", "func"}
            handler_kwargs = {
                k: v for k, v in func._command_kwargs.items()
                if k in _VALID_NM_KWARGS
            }
            handler_kwargs["pattern"] = pattern

            event_handler = events.NewMessage(**handler_kwargs)
            client.add_event_handler(_wrap_html_handler(func), event_handler)

            if cmd not in _COMMANDS:
                _COMMANDS[cmd] = []
            # Описание: приоритет ru_doc > другие _doc kwargs > __doc__
            _ckw = getattr(func, '_command_kwargs', {})
            _doc_str = (
                _ckw.get('ru_doc')
                or _ckw.get('en_doc')
                or _ckw.get('doc')
                or next((v for k, v in _ckw.items() if k.endswith('_doc') and v), '')
                or getattr(func, '_command_doc', '')
                or ''
            )
            _COMMANDS[cmd].append({
                "module": f"heroku:{mod_name}",
                "doc": _doc_str.strip() or "Нет описания"
            })
            registered_commands.append(cmd)

        # ── Watchers ─────────────────────────────────────────────────────
        if getattr(func, "_is_watcher", False):
            wkw = func._watcher_kwargs.copy()
            ev = wkw.pop("event", None)
            if ev is None:
                # chat_id= не поддерживается в NewMessage, конвертируем в chats=
                if "chat_id" in wkw:
                    wkw["chats"] = wkw.pop("chat_id")
                try:
                    ev = events.NewMessage(**wkw)
                except Exception as _we:
                    logger.warning(f"[compat] watcher kwargs error in {mod_name}: {_we}, skipping")
                    continue
            client.add_event_handler(_wrap_html_handler(func), ev)

        # ── Callback-кнопки ──────────────────────────────────────────────
        # Автодетект по суффиксу: Hikka соглашение — метод *_callback_handler
        # регистрируется как глобальный callback handler (ловит любой data).
        # Пример: actions_callback_handler в HikariChat ловит "dw/", "fb/" и т.д.
        if (not getattr(func, "_is_callback_handler", False)
                and attr_name.endswith("_callback_handler")
                and not attr_name.startswith("_")):
            _f2 = getattr(func, "__func__", func)
            _f2._is_callback_handler = True
            _f2._callback_pattern = re.compile(r"[fbmudw]{1,3}/[-0-9]+/[-#0-9]+")
            _f2._is_inline_everyone = True  # кнопки публичные
            logger.info(f"[compat] Auto-registered callback handler: {attr_name} for {mod_name}")

        if getattr(func, "_is_callback_handler", False):
            import re as _re
            pat = getattr(func, "_callback_pattern", _re.compile(".*"))
            _CALLBACKS[pat] = func

        # ── Hikka inline-обработчики ─────────────────────────────────────
        # @loader.inline_handler() помечает метод флагом _is_inline_handler.
        # Имя метода = «префикс» запроса: "fheta" матчит "fheta ..." и "fheta __cmd__ ..."
        # Функция принимает query-объект и либо вызывает query.inline_query.answer([...])
        # сама, либо возвращает dict с одиночным результатом.
        if getattr(func, "_is_inline_handler", False):
            import re as _re
            from utils.loader import INLINE_HANDLERS_REGISTRY as _IHR
            _inline_name = attr_name  # напр. "fheta"
            # Матчим "<name>" и "<name> <всё остальное>"
            _pat = _re.compile(rf"^{_re.escape(_inline_name)}(\s.*|$)", _re.DOTALL)
            _IHR[_pat] = {
                "func": func,
                "title": _inline_name,
                "description": "",
                "hikka_style": True,
                # prefix нужен чтобы bot_callbacks вычислил args
                "prefix": _inline_name,
            }
            print(f"[compat] Registered inline handler: {_inline_name} for {mod_name}")

    # 8. Запускаем loop-методы (autostart=True)
    _loop_tasks = []
    for _attr in dir(module_instance):
        try:
            _fn = getattr(module_instance, _attr)
        except Exception:
            continue
        if not callable(_fn):
            continue
        if not getattr(_fn, "_is_loop", False):
            continue
        if not getattr(_fn, "_loop_autostart", False):
            continue
        _interval = getattr(_fn, "_loop_interval", 1)

        async def _make_loop_task(fn=_fn, interval=_interval, name=_attr, mname=mod_name):
            logger.info(f"[compat] loop task started: {mname}.{name} (interval={interval}s)")
            while True:
                try:
                    await fn()
                except asyncio.CancelledError:
                    logger.info(f"[compat] loop task cancelled: {mname}.{name}")
                    return
                except Exception as _le:
                    logger.warning(f"[compat] loop task {mname}.{name} error: {_le}")
                await asyncio.sleep(interval)

        _task = asyncio.ensure_future(_make_loop_task())
        _loop_tasks.append((_attr, _task))
        logger.info(f"[compat] Scheduled loop: {mod_name}.{_attr} every {_interval}s")

    # 9. Сохраняем в реестре клиента
    if not hasattr(client, "modules"):
        client.modules = {}
    client.modules[f"heroku:{mod_name}"] = {
        "module": mod,
        "instance": module_instance,
        "handlers": [],
        "heroku_compat": True,
        "file_name": file_path.stem,  # имя файла без .py, напр. "goypulse"
        "loop_tasks": _loop_tasks,
    }

    print(f"[compat] Loaded {mod_name}, registered commands: {registered_commands}")
    logger.info(f"[compat] Loaded Heroku module: {mod_name}, commands: {registered_commands}")
    return {
        "status": "ok",
        "module_name": mod_name,
        "commands": registered_commands,
    }


import re  # нужен в конце файла для pattern compile