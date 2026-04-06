# utils/message_builder.py

from telethon.tl.types import (
    MessageEntityBold, 
    MessageEntityCode, 
    MessageEntityCustomEmoji, 
    MessageEntityItalic,
    MessageEntityBlockquote,
    MessageEntityTextUrl,
    MessageEntityPre
)
from telethon.errors.rpcerrorlist import MessageNotModifiedError
from typing import Any, List, Tuple, Optional, Dict, Union
from telethon.events import NewMessage
from telethon.tl.custom.message import Message

def utf16len(s: str) -> int:
    """Вычисляет длину строки в UTF-16 (нужно для Telegram API)."""
    return len(s.encode('utf-16-le')) // 2

def build_message(parts_list: List[Dict[str, Any]]) -> Tuple[str, List[Any]]:
    """
    Собирает текст и форматирование из списка частей.
    Автоматически обрабатывает ошибки несовместимых аргументов сущностей.
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
                try:
                    # Попытка 1: Создаем сущность со всеми аргументами (например, language для Pre)
                    entities.append(entity_type(offset=current_offset, length=length, **entity_kwargs))
                except TypeError:
                    # Попытка 2: Если сущность не принимает аргументы (например, Blockquote не знает про collapsed),
                    # создаем её без kwargs, чтобы сообщение всё равно отправилось (пусть и не свернутое).
                    entities.append(entity_type(offset=current_offset, length=length))
        
        current_offset += utf16len(text)
    
    return "".join(text_parts), entities

async def build_and_edit(
    event: Union[NewMessage.Event, Message, None], 
    message_parts: Union[str, List[Dict[str, Any]]], 
    parse_mode: Optional[str] = None, 
    link_preview: Optional[bool] = None, 
    formatting_entities: Optional[List[Any]] = None,
    **kwargs 
) -> Optional[Message]:
    """
    Универсальный сборщик и редактор/ответчик.
    Возвращает объект сообщения или None при ошибке.
    """
    
    if event is None:
        return None
        
    final_text: str
    entities: Optional[List[Any]] = formatting_entities

    # Обработка частей сообщения
    if isinstance(message_parts, str):
        final_text = message_parts
    elif isinstance(message_parts, list):
        try:
            final_text, entities = build_message(message_parts)
        except Exception as e:
            # Fallback на случай критической ошибки в структуре
            final_text = f"❌ Ошибка сборки (message_builder):\n{type(e).__name__}: {e}"
            entities = None
    else:
        final_text = str(message_parts)

    # Если entities пустые, но есть текст — ставим дефолтный маркдаун, если не указано иное
    if not entities and parse_mode is None:
        if final_text:
            parse_mode = "md" 

    # Очищаем kwargs от конфликтов
    kwargs.pop('parse_mode', None)
    
    send_kwargs = {
        'parse_mode': parse_mode,
        'link_preview': link_preview,
        'formatting_entities': entities,
        **kwargs
    }
    
    # Удаляем None значения
    send_kwargs = {k: v for k, v in send_kwargs.items() if v is not None}
    
    try:
        # Пытаемся редактировать, если сообщение исходящее
        if getattr(event, 'out', False):
            return await event.edit(final_text, **send_kwargs)
        else:
            # Иначе отвечаем
            return await event.respond(final_text, **send_kwargs)
            
    except MessageNotModifiedError:
        return event # Сообщение не изменилось, это нормально
        
    except Exception as e:
        print(f"[MessageBuilder] Error: {type(e).__name__} (Event: {type(event).__name__})")
        
        # Если редактирование не удалось (например, сообщение слишком старое), пробуем отправить новое
        if getattr(event, 'out', False):
            try:
                chat_id = getattr(event, 'chat_id', None) or event.peer_id
                return await event.client.send_message(
                    chat_id, 
                    f"❌ Не удалось обновить сообщение.\nОшибка: `{type(e).__name__}`\n\n{final_text}",
                    reply_to=getattr(event, 'id', None),
                    **send_kwargs
                )
            except Exception:
                pass
        return None