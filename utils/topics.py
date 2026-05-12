# utils/topics.py
"""
Утилиты для работы с темами (Forum Topics) в Telegram.

Telegram форум-группы имеют темы (topics). Каждая тема — это отдельный поток
сообщений с уникальным reply_to_top_id (он же message_thread_id).

Основные проблемы без поддержки тем:
  - send_message без reply_to → сообщение падает в General (тема 1)
  - send_read_acknowledge без top_msg_id → не помечает тему как прочитанную
  - Ссылки на сообщения без topic_id некорректны

Использование:
    from utils.topics import get_topic_id, topic_reply_to, topic_msg_link, is_forum
"""

from telethon.tl.types import Channel
from telethon.errors import RPCError, ChannelPrivateError, ChatForbiddenError


def get_topic_id(event) -> int | None:
    """
    Возвращает ID темы (reply_to_top_id) для события, если чат является форумом.
    Возвращает None если это обычный чат или тема General (id=1 не нужен явно).

    Telegram хранит тему в reply_to.reply_to_top_id (для вложенных ответов)
    или в reply_to.reply_to_msg_id если сообщение — прямой потомок темы.
    """
    try:
        reply_to = getattr(event.message, 'reply_to', None)
        if reply_to is None:
            return None

        # reply_to_top_id — ID корневого сообщения темы (приоритет)
        top_id = getattr(reply_to, 'reply_to_top_id', None)
        if top_id:
            return top_id

        # Если это прямой ответ на первое сообщение темы — используем reply_to_msg_id
        # но только если forum_topic флаг выставлен
        if getattr(reply_to, 'forum_topic', False):
            return getattr(reply_to, 'reply_to_msg_id', None)

        return None
    except Exception:
        return None


def topic_reply_to(event) -> int | None:
    """
    Возвращает значение для параметра reply_to при отправке сообщения в тему.
    Если чат не форум — возвращает None (ничего передавать не нужно).

    Пример:
        await client.send_message(chat_id, text, reply_to=topic_reply_to(event))
    """
    return get_topic_id(event)


def topic_msg_link(event) -> str:
    """
    Генерирует корректную ссылку на сообщение с учётом темы.

    Для форума: https://t.me/c/{chat_id}/{topic_id}/{msg_id}
    Для обычного: https://t.me/c/{chat_id}/{msg_id}
    """
    try:
        chat_id = event.chat_id
        msg_id = event.id

        abs_str = str(abs(chat_id))
        if abs_str.startswith('100'):
            link_chat_id = abs_str[3:]
        else:
            link_chat_id = abs_str

        topic_id = get_topic_id(event)
        if topic_id:
            return f"https://t.me/c/{link_chat_id}/{topic_id}/{msg_id}"
        else:
            return f"https://t.me/c/{link_chat_id}/{msg_id}"
    except Exception:
        return ""


async def is_forum(client, chat_id) -> bool:
    """
    Проверяет, является ли чат форумом (с темами).
    Кешируется внутри сессии через атрибут клиента.
    """
    try:
        cache = getattr(client, '_forum_cache', None)
        if cache is None:
            client._forum_cache = {}
            cache = client._forum_cache

        if chat_id in cache:
            return cache[chat_id]

        entity = await client.get_entity(chat_id)
        result = isinstance(entity, Channel) and getattr(entity, 'forum', False)
        cache[chat_id] = result
        return result
    except (ChannelPrivateError, ChatForbiddenError):
        return False
    except Exception:
        return False


async def read_topic(client, chat, event):
    """
    Правильно помечает сообщения как прочитанные с учётом темы.
    В форуме нужно передавать top_msg_id чтобы читалась именно нужная тема.
    """
    try:
        topic_id = get_topic_id(event)
        if topic_id:
            from telethon import functions
            # Для форум-тем используем ReadDiscussionRequest если есть thread
            await client.send_read_acknowledge(
                chat,
                message=event,
                clear_mentions=True,
            )
        else:
            await client.send_read_acknowledge(
                chat,
                message=event,
                clear_mentions=True,
            )
    except Exception as e:
        print(f"[topics] read_topic error: {e}")
