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

    # from ..inline.types import InlineCall  (Gemini)
    inline_mod = types.ModuleType(f"{package_name}.inline")
    inline_types_mod = types.ModuleType(f"{package_name}.inline.types")
    class InlineCall:
        def __init__(self): self.message_id = None; self.chat_id = None
        async def answer(self, text="", **kw): pass
        async def delete(self): pass
        async def edit(self, text, **kw): pass
    inline_types_mod.InlineCall = InlineCall
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

    # 2. Создаём фейковый пакет для from .. import loader, utils
    _create_fake_package(FAKE_PACKAGE)

    # 3. Загружаем модуль
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
        if issubclass(obj, HerokuModule) or getattr(obj, "_is_heroku_module", False):
            module_instance = obj()
            break

    if module_instance is None:
        return {"status": "error", "message": "Класс модуля не найден (нет subclass loader.Module)"}

    # 5. Инжектируем зависимости
    module_instance.client = client
    module_instance._client = client   # алиас: Hikka-модули используют self._client
    # Оборачиваем db в адаптер: db.get("Mod","key") → db_module.get_module_data("Mod","key")
    module_instance.db = _DbAdapter(db_module)
    module_instance._tg_id = (await client.get_me()).id

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
    if isinstance(module_instance.config, ModuleConfig):
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

    # 8. Сохраняем в реестре клиента
    if not hasattr(client, "modules"):
        client.modules = {}
    client.modules[f"heroku:{mod_name}"] = {
        "module": mod,
        "instance": module_instance,
        "handlers": [],
        "heroku_compat": True,
        "file_name": file_path.stem,  # имя файла без .py, напр. "goypulse"
    }

    print(f"[compat] Loaded {mod_name}, registered commands: {registered_commands}")
    logger.info(f"[compat] Loaded Heroku module: {mod_name}, commands: {registered_commands}")
    return {
        "status": "ok",
        "module_name": mod_name,
        "commands": registered_commands,
    }


import re  # нужен в конце файла для pattern compile
