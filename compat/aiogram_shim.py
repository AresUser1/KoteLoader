# compat/aiogram_shim.py
"""
Shim-слой для aiogram.

Проблема:
  Чужие модули (HikariChat, FHeta и т.д.) написаны под aiogram 2.x и делают:
    from aiogram.utils.exceptions import MessageCantBeDeleted, ...
    from aiogram.types import CallbackQuery, ChatPermissions, ...

  На машине может быть:
    A) aiogram вообще не установлен
    B) aiogram 3.x — там нет aiogram.utils.exceptions (убрали в v3)
    C) aiogram 2.x — всё работает нативно, shim не нужен

Решение:
  install_aiogram_shim() вызывается ДО _load_source_as_package().
  Функция:
    1. Проверяет реальный aiogram (версию).
    2. Если aiogram 2.x — не трогаем ничего (уже работает).
    3. Если aiogram 3.x — патчим только недостающие подмодули (utils.exceptions),
       адаптируем InlineKeyboardMarkup чтобы он реально был aiogram.types.InlineKeyboardMarkup
       (pydantic в aiogram 3 проверяет isinstance).
    4. Если aiogram нет — ставим полный stub в sys.modules.

Всё это изолировано в sys.modules и не ломает систему.
"""

import sys
import types
import logging

logger = logging.getLogger(__name__)


# ─── Stub-классы (используются когда aiogram недоступен совсем) ───────────────

class _StubException(Exception):
    """Базовый класс для заглушек aiogram exceptions."""
    pass

class MessageCantBeDeleted(_StubException):
    pass

class MessageToDeleteNotFound(_StubException):
    pass

class TelegramAPIError(_StubException):
    pass

class BadRequest(_StubException):
    pass

class Unauthorized(_StubException):
    pass

class CantInitiateConversation(_StubException):
    pass

class BotBlocked(_StubException):
    pass

class ChatNotFound(_StubException):
    pass

class UserDeactivated(_StubException):
    pass

class NetworkError(_StubException):
    pass

class RetryAfter(_StubException):
    def __init__(self, timeout=0):
        self.timeout = timeout
        super().__init__(f"Retry after {timeout}")

class MigrateToChat(_StubException):
    def __init__(self, chat_id=0):
        self.migrate_to_chat_id = chat_id
        super().__init__(f"MigrateToChat {chat_id}")


# ─── Stub types ──────────────────────────────────────────────────────────────

class _StubModel:
    """Базовый stub для aiogram типов — принимает любые kwargs."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

class ChatPermissions(_StubModel):
    can_send_messages: bool = True
    can_send_media_messages: bool = True
    can_send_polls: bool = True
    can_send_other_messages: bool = True
    can_add_web_page_previews: bool = True
    can_change_info: bool = False
    can_invite_users: bool = True
    can_pin_messages: bool = False

class CallbackQuery(_StubModel):
    id: str = ""
    data: str = ""
    message = None
    from_user = None
    async def answer(self, text="", **kw): pass

class InlineQuery(_StubModel):
    id: str = ""
    query: str = ""
    from_user = None

class Message(_StubModel):
    message_id: int = 0
    text: str = ""
    chat = None
    from_user = None

class User(_StubModel):
    id: int = 0
    username: str = ""
    first_name: str = ""

class Chat(_StubModel):
    id: int = 0
    type: str = "group"

class InlineKeyboardButton(_StubModel):
    text: str = ""
    callback_data: str = None
    url: str = None
    switch_inline_query_current_chat: str = None

class InlineKeyboardMarkup(_StubModel):
    inline_keyboard: list = None
    def __init__(self, inline_keyboard=None, **kw):
        self.inline_keyboard = inline_keyboard or []

class InputTextMessageContent(_StubModel):
    message_text: str = ""
    parse_mode: str = None

class InlineQueryResultArticle(_StubModel):
    id: str = ""
    title: str = ""
    input_message_content = None
    reply_markup = None
    description: str = ""
    thumb_url: str = ""

class LinkPreviewOptions(_StubModel):
    is_disabled: bool = False

class ContentTypes:
    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"
    AUDIO = "audio"
    VOICE = "voice"
    STICKER = "sticker"
    ANY = "any"
    ALL = "*"

class ParseMode:
    HTML = "HTML"
    MARKDOWN = "Markdown"
    MARKDOWN_V2 = "MarkdownV2"


# ─── Главная функция ──────────────────────────────────────────────────────────

def install_aiogram_shim():
    """
    Анализирует окружение и устанавливает минимально необходимый shim.

    Вызывать один раз перед загрузкой чужого модуля.
    Безопасно вызывать повторно — идемпотентно.
    """
    if _is_shim_installed():
        return  # уже установлен

    aio_version = _detect_aiogram_version()

    if aio_version is None:
        # Нет aiogram вообще — ставим полный stub
        logger.info("[aiogram_shim] aiogram не найден, устанавливаем полный stub")
        _install_full_stub()

    elif aio_version >= (3, 0):
        # aiogram 3.x — есть большинство типов, но нет utils.exceptions
        # Адаптируем InlineKeyboardMarkup и добавляем utils.exceptions
        logger.info(f"[aiogram_shim] aiogram {aio_version[0]}.x, патчим utils.exceptions и markup")
        _patch_aiogram3()

    else:
        # aiogram 2.x — всё ок нативно
        logger.info(f"[aiogram_shim] aiogram 2.x, shim не нужен")


def _is_shim_installed() -> bool:
    """Проверяет что shim уже в sys.modules."""
    exc_mod = sys.modules.get("aiogram.utils.exceptions")
    return exc_mod is not None and getattr(exc_mod, "_kote_shim", False)


def _detect_aiogram_version():
    """Возвращает (major, minor) tuple или None."""
    try:
        import importlib.metadata
        ver = importlib.metadata.version("aiogram")
        parts = ver.split(".")
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except Exception:
        pass
    # Пробуем через сам пакет
    try:
        import aiogram
        ver = getattr(aiogram, "__version__", "")
        if ver:
            parts = ver.split(".")
            return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except Exception:
        pass
    return None


def _install_full_stub():
    """Устанавливает полный aiogram stub в sys.modules."""
    import importlib.machinery

    def _make_module(name, parent=None, **attrs):
        mod = types.ModuleType(name)
        mod._kote_shim = True
        # __path__ и __package__ нужны чтобы Python считал модуль пакетом
        # и позволял импортировать подмодули (from aiogram.xxx import yyy)
        mod.__path__ = []
        mod.__package__ = name
        mod.__spec__ = importlib.machinery.ModuleSpec(name, None)
        mod.__spec__.submodule_search_locations = []
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod
        # Регистрируем как атрибут родителя
        if parent is not None:
            last_part = name.rsplit(".", 1)[-1]
            setattr(parent, last_part, mod)
        return mod

    # aiogram корень
    aio_root = _make_module("aiogram",
        __version__="2.25.2",  # эмулируем 2.x для совместимости
    )

    # aiogram.types
    types_mod = _make_module("aiogram.types", parent=aio_root,
        CallbackQuery=CallbackQuery,
        InlineQuery=InlineQuery,
        InlineKeyboardMarkup=InlineKeyboardMarkup,
        InlineKeyboardButton=InlineKeyboardButton,
        InputTextMessageContent=InputTextMessageContent,
        InlineQueryResultArticle=InlineQueryResultArticle,
        ChatPermissions=ChatPermissions,
        Message=Message,
        User=User,
        Chat=Chat,
        ContentTypes=ContentTypes,
        ParseMode=ParseMode,
        LinkPreviewOptions=LinkPreviewOptions,
    )

    # aiogram.utils
    utils_mod = _make_module("aiogram.utils", parent=aio_root)

    # aiogram.utils.exceptions — для HikariChat (aiogram 2.x API)
    exc_mod = _make_module("aiogram.utils.exceptions", parent=utils_mod,
        TelegramAPIError=TelegramAPIError,
        BadRequest=BadRequest,
        Unauthorized=Unauthorized,
        MessageCantBeDeleted=MessageCantBeDeleted,
        MessageToDeleteNotFound=MessageToDeleteNotFound,
        CantInitiateConversation=CantInitiateConversation,
        BotBlocked=BotBlocked,
        ChatNotFound=ChatNotFound,
        UserDeactivated=UserDeactivated,
        NetworkError=NetworkError,
        RetryAfter=RetryAfter,
        MigrateToChat=MigrateToChat,
    )

    # aiogram.exceptions — для HikariChat (from aiogram.exceptions import TelegramAPIError)
    # Это aiogram 3.x путь, но некоторые модули используют его
    aio_exc_mod = _make_module("aiogram.exceptions", parent=aio_root,
        TelegramAPIError=TelegramAPIError,
        BadRequest=BadRequest,
        TelegramBadRequest=BadRequest,
        Unauthorized=Unauthorized,
        TelegramUnauthorizedError=Unauthorized,
        MessageCantBeDeleted=MessageCantBeDeleted,
        MessageToDeleteNotFound=MessageToDeleteNotFound,
        CantInitiateConversation=CantInitiateConversation,
        BotBlocked=BotBlocked,
        TelegramForbiddenError=BotBlocked,
        ChatNotFound=ChatNotFound,
        UserDeactivated=UserDeactivated,
        NetworkError=NetworkError,
        TelegramNetworkError=NetworkError,
        RetryAfter=RetryAfter,
        TelegramRetryAfter=RetryAfter,
        MigrateToChat=MigrateToChat,
    )

    # aiogram.dispatcher
    dp_mod = _make_module("aiogram.dispatcher", parent=aio_root)

    # aiogram.dispatcher.filters
    _make_module("aiogram.dispatcher.filters", parent=dp_mod)

    # aiogram.bot
    _make_module("aiogram.bot", parent=aio_root)

    logger.info("[aiogram_shim] Полный stub установлен")


def _patch_aiogram3():
    """
    Патчим aiogram 3.x:
    1. Добавляем aiogram.utils.exceptions (его убрали в v3)
    2. Адаптируем generate_aiogram_markup чтобы возвращал
       реальный aiogram.types.InlineKeyboardMarkup (pydantic проверяет isinstance)
    """
    import aiogram

    # ── 1. Добавляем aiogram.utils.exceptions ────────────────────────────
    if "aiogram.utils" not in sys.modules:
        utils_mod = types.ModuleType("aiogram.utils")
        utils_mod._kote_shim = True
        sys.modules["aiogram.utils"] = utils_mod
        aiogram.utils = utils_mod
    else:
        utils_mod = sys.modules["aiogram.utils"]

    exc_mod = types.ModuleType("aiogram.utils.exceptions")
    exc_mod._kote_shim = True

    # В aiogram 3 исключения в aiogram.exceptions, попробуем взять оттуда
    try:
        import aiogram.exceptions as _aio3_exc

        # Маппинг aiogram2_name -> aiogram3 аналог или наш stub
        _exc_map = {
            "TelegramAPIError": getattr(_aio3_exc, "TelegramAPIError", TelegramAPIError),
            "BadRequest": getattr(_aio3_exc, "TelegramBadRequest", BadRequest),
            "Unauthorized": getattr(_aio3_exc, "TelegramUnauthorizedError", Unauthorized),
            "MessageCantBeDeleted": getattr(_aio3_exc, "TelegramBadRequest", MessageCantBeDeleted),
            "MessageToDeleteNotFound": getattr(_aio3_exc, "TelegramBadRequest", MessageToDeleteNotFound),
            "CantInitiateConversation": getattr(_aio3_exc, "TelegramForbiddenError", CantInitiateConversation),
            "BotBlocked": getattr(_aio3_exc, "TelegramForbiddenError", BotBlocked),
            "ChatNotFound": getattr(_aio3_exc, "TelegramBadRequest", ChatNotFound),
            "UserDeactivated": getattr(_aio3_exc, "TelegramForbiddenError", UserDeactivated),
            "NetworkError": getattr(_aio3_exc, "TelegramNetworkError", NetworkError),
            "RetryAfter": getattr(_aio3_exc, "TelegramRetryAfter", RetryAfter),
        }
        for name, cls in _exc_map.items():
            setattr(exc_mod, name, cls)
        logger.info("[aiogram_shim] utils.exceptions создан из aiogram.exceptions (v3 mapping)")
    except Exception as e:
        # Не смогли взять из v3, ставим наши stub'ы
        logger.warning(f"[aiogram_shim] Не смогли импортировать aiogram.exceptions: {e}, ставим stubs")
        for name, cls in [
            ("TelegramAPIError", TelegramAPIError),
            ("BadRequest", BadRequest),
            ("Unauthorized", Unauthorized),
            ("MessageCantBeDeleted", MessageCantBeDeleted),
            ("MessageToDeleteNotFound", MessageToDeleteNotFound),
            ("CantInitiateConversation", CantInitiateConversation),
            ("BotBlocked", BotBlocked),
            ("ChatNotFound", ChatNotFound),
            ("UserDeactivated", UserDeactivated),
            ("NetworkError", NetworkError),
            ("RetryAfter", RetryAfter),
            ("MigrateToChat", MigrateToChat),
        ]:
            setattr(exc_mod, name, cls)

    sys.modules["aiogram.utils.exceptions"] = exc_mod
    utils_mod.exceptions = exc_mod

    # ── 2. Патчим InlineKeyboardMarkup в aiogram.types ────────────────────
    # aiogram 3 использует pydantic для InlineQueryResultArticle.reply_markup
    # и проверяет isinstance(obj, InlineKeyboardMarkup).
    # Наш generate_aiogram_markup возвращает stub-класс → TypeError.
    # Решение: патчим generate_aiogram_markup в _InlineManager чтобы
    # возвращал реальный aiogram.types.InlineKeyboardMarkup.
    try:
        from aiogram.types import InlineKeyboardMarkup as _RealIKM
        from aiogram.types import InlineKeyboardButton as _RealIKB
        # Сохраняем реальные классы как атрибуты модуля для использования в loader.py
        sys.modules["aiogram.types"]._real_InlineKeyboardMarkup = _RealIKM
        sys.modules["aiogram.types"]._real_InlineKeyboardButton = _RealIKB
        logger.info("[aiogram_shim] Реальные aiogram3 InlineKeyboardMarkup/Button доступны")
    except Exception as e:
        logger.warning(f"[aiogram_shim] Не смогли импортировать реальный InlineKeyboardMarkup: {e}")

    logger.info("[aiogram_shim] aiogram 3.x патч установлен")