# utils/message_builder.py

from telethon.tl.types import (
    MessageEntityBold, 
    MessageEntityCode, 
    MessageEntityCustomEmoji, 
    MessageEntityItalic,
    MessageEntityBlockquote,
    MessageEntityTextUrl
)
from telethon.errors.rpcerrorlist import MessageNotModifiedError
from typing import Any, List, Tuple, Optional, Dict, Union
from telethon.events import NewMessage
from telethon.tl.custom.message import Message # Добавлен импорт для типизации

def utf16len(s: str) -> int:
    """Вычисляет длину строки в UTF-16."""
    return len(s.encode('utf-16-le')) // 2

def build_message(parts_list: List[Dict[str, Any]]) -> Tuple[str, List[Any]]:
    """
    Собирает текст и форматирование из списка частей (словарей).
    Возвращает кортеж (final_text, entities).
    """
    text_parts = []
    entities = []
    current_offset = 0

    for part in parts_list:
        text = str(part.get("text", "")) 
        entity_type = part.get("entity")
        entity_kwargs = part.get("kwargs", {})

        text_parts.append(text)
        if entity_type:
            length = utf16len(text)
            if length > 0:
                entities.append(entity_type(offset=current_offset, length=length, **entity_kwargs))
        current_offset += utf16len(text)
    
    return "".join(text_parts), entities

async def build_and_edit(
    event: Union[NewMessage.Event, Message, None], # Расширена типизация
    message_parts: Union[str, List[Dict[str, Any]]], 
    parse_mode: Optional[str] = None, 
    link_preview: Optional[bool] = None, 
    formatting_entities: Optional[List[Any]] = None,
    **kwargs 
) -> Optional[Message]: # Функция теперь явно возвращает Message или None
    """
    Универсальный сборщик и редактор/ответчик.
    
    ВАЖНОЕ ИСПРАВЛЕНИЕ: Функция теперь возвращает объект сообщения, чтобы избежать
    ошибки 'NoneType' object has no attribute 'out'.
    """
    
    # 1. Защита от NoneType
    if event is None:
        print("[MessageBuilder] Предупреждение: event был None. Отправка/редактирование пропущено.")
        return None
        
    final_text: str
    entities: Optional[List[Any]] = formatting_entities

    # 2. Обработка частей сообщения
    if isinstance(message_parts, str):
        final_text = message_parts
    elif isinstance(message_parts, list):
        try:
            final_text, entities = build_message(message_parts)
        except Exception as e:
            final_text = f"❌ Ошибка сборки сообщения: {type(e).__name__}"
            entities = None
    else:
        final_text = str(message_parts)

    # 3. Настройка параметров отправки
    if not entities and parse_mode is None:
        if final_text:
            parse_mode = "md" 

    kwargs.pop('parse_mode', None)
    
    send_kwargs = {
        'parse_mode': parse_mode,
        'link_preview': link_preview,
        'formatting_entities': entities,
        **kwargs
    }
    
    send_kwargs = {k: v for k, v in send_kwargs.items() if v is not None}
    
    # 4. Отправка/Редактирование с возвратом объекта
    try:
        if event.out:
            edited_message = await event.edit(final_text, **send_kwargs)
            return edited_message # <-- КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: Возвращаем отредактированное сообщение
        else:
            sent_message = await event.respond(final_text, **send_kwargs)
            return sent_message # <-- КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: Возвращаем отправленное сообщение
    except MessageNotModifiedError:
        return event # Если не изменено, возвращаем объект для дальнейшего использования
    except Exception as e:
        # Логирование ошибки (только для информации)
        print(f"[MessageBuilder] Ошибка отправки/редактирования: {type(e).__name__} (caused by {type(event).__name__})")
        
        # Обработка ошибки при редактировании исходящего сообщения
        if getattr(event, 'out', False):
            try:
                # В случае ошибки редактирования, отправляем новое сообщение
                await event.client.send_message(
                    getattr(event, 'chat_id', None), 
                    f"❌ Ошибка редактирования: `{type(e).__name__}`\n\n{final_text}", 
                    reply_to=getattr(event, 'id', None),
                    **send_kwargs
                )
            except Exception:
                pass
        
        return None # <-- КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: При ошибке возвращаем None