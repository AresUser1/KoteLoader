# utils/message_builder.py

from telethon.tl.types import MessageEntityBold, MessageEntityCode, MessageEntityCustomEmoji, MessageEntityItalic
from telethon.errors.rpcerrorlist import MessageNotModifiedError
from typing import Any, List, Tuple, Optional, Dict, Union
from telethon.events import NewMessage

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
        # ОШИБКА ПРОИСХОДИЛА ЗДЕСЬ, ЕСЛИ `part` БЫЛ СТРОКОЙ
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
    event: NewMessage.Event, 
    message_parts: Union[str, List[Dict[str, Any]]], 
    parse_mode: Optional[str] = None, 
    link_preview: Optional[bool] = None, 
    formatting_entities: Optional[List[Any]] = None,
    # ⭐ ИСПРАВЛЕНИЕ: Добавляем **kwargs, чтобы избежать NameError
    **kwargs 
):
    """
    Универсальный сборщик и редактор/ответчик.
    Обрабатывает как список частей, так и простую строку.
    """
    final_text: str
    entities: Optional[List[Any]] = formatting_entities

    # Обрабатываем случай, когда передана простая строка
    if isinstance(message_parts, str):
        final_text = message_parts
    elif isinstance(message_parts, list):
        # Если это список частей, используем основную функцию build_message
        try:
            # Замените 'build_message' на фактическое имя вашей функции, если оно отличается
            final_text, entities = build_message(message_parts)
        except Exception:
            final_text = "Ошибка при сборке сообщения."
            entities = None
    else:
        final_text = str(message_parts)


    # Автоматически устанавливаем parse_mode, если нет entities
    if not entities and parse_mode is None:
        if final_text:
            parse_mode = "md" 

    # ❗ ЭТО ТЕПЕРЬ РАБОТАЕТ, Т.К. **kwargs ОПРЕДЕЛЕНЫ
    kwargs.pop('parse_mode', None)
    
    send_kwargs = {
        'parse_mode': parse_mode,
        'link_preview': link_preview,
        'formatting_entities': entities,
        **kwargs
    }
    
    send_kwargs = {k: v for k, v in send_kwargs.items() if v is not None}
    
    try:
        if event.out:
            await event.edit(final_text, **send_kwargs)
        else:
            await event.respond(final_text, **send_kwargs)
    except MessageNotModifiedError:
        pass
    except Exception as e:
        if event.out:
            try:
                await event.client.send_message(
                    event.chat_id, 
                    f"❌ Ошибка редактирования: `{type(e).__name__}`\n\n{final_text}", 
                    reply_to=event.id,
                    **send_kwargs
                )
            except Exception:
                pass
        else:
            pass