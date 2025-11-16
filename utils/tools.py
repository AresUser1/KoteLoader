# utils/tools.py

from telethon import events
from telethon.errors.rpcerrorlist import (
    PeerIdInvalidError, UsernameInvalidError
)
from telethon.tl.types import User, Channel # ❗️ Добавлен Channel для get_target_and_text

async def get_target_user(event, target_str: str = None) -> User | None:
    """
    Получает объект пользователя (User) по строке (username, ID) или из ответа на сообщение.
    Если target_str не указан, использует отправителя сообщения-ответа.
    """
    if target_str:
        target_str_cleaned = target_str.strip()
        try:
            # --- ❗️❗️❗️ КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ ❗️❗️❗️ ---
            # Пытаемся сначала преобразовать в int, если это похоже на ID
            entity_to_find = int(target_str_cleaned) if target_str_cleaned.isdigit() else target_str_cleaned
            # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
            
            return await event.client.get_entity(entity_to_find)
        except (PeerIdInvalidError, UsernameInvalidError, ValueError, TypeError):
            # Не удалось найти по строке или ID
            pass
    
    # Ищем в ответе, если target_str не был указан или не найден
    reply = await event.get_reply_message()
    if reply:
        try:
            # Пытаемся получить отправителя ответа
            return await reply.get_sender()
        except Exception:
            pass
            
    return None

async def get_target_and_text(event, args: str) -> tuple[User | None, str]:
    """
    Пытается извлечь целевого пользователя и оставшийся текст из аргументов.
    Приоритет: ответ на сообщение -> аргумент (username/ID) -> отсутствие цели.
    Возвращает (User | None, оставшийся текст).
    """
    user = None
    text = args
    
    reply = await event.get_reply_message()

    if reply and (not event.out or reply.sender_id != event.sender_id):
        # 1. Сначала проверяем ответ на сообщение (на другого пользователя, если исходящее)
        try:
            user = await reply.get_sender()
            # Оставшийся текст - это все аргументы
        except Exception:
            pass
    
    if user is None:
        # 2. Проверяем, является ли первый аргумент пользователем/чатом
        parts = (args or "").split(maxsplit=1)
        if parts:
            potential_target_str = parts[0]
            try:
                # --- ❗️❗️❗️ ИСПРАВЛЕНИЕ (аналогично get_target_user) ❗️❗️❗️ ---
                entity_to_find = int(potential_target_str) if potential_target_str.isdigit() else potential_target_str
                # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
                
                # Поиск сущности по первому слову
                target_entity = await event.client.get_entity(entity_to_find)
                
                # Простая проверка на тип Entity, который может быть целью
                if isinstance(target_entity, (User, Channel)): 
                    user = target_entity
                    # Оставшийся текст - вторая часть аргументов
                    text = parts[1] if len(parts) > 1 else ""
            except Exception:
                # Первое слово не является сущностью, весь текст - это текст/ник
                pass
                
    # 3. Если ничего не найдено, цель - отправитель сообщения-ответа (если есть ответ на себя)
    if user is None and reply and event.out and reply.sender_id == event.sender_id:
        user = await reply.get_sender()

    # 4. Если вообще ничего не найдено, а текст пустой, цель - сам отправитель
    if user is None and not (text or "").strip():
         user = await event.get_sender()

    return user, (text or "").strip()