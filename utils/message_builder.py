# utils/message_builder.py

from telethon.tl.types import MessageEntityBold, MessageEntityCode, MessageEntityCustomEmoji, MessageEntityItalic
from telethon.errors.rpcerrorlist import MessageNotModifiedError

def utf16len(s: str) -> int:
    """Вычисляет длину строки в UTF-16."""
    return len(s.encode('utf-16-le')) // 2

def build_message(parts_list: list) -> tuple:
    """
    Собирает текст и форматирование из списка частей.
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

async def build_and_edit(event, parts_list: list, **kwargs):
    """
    Универсальный сборщик и редактор/ответчик.
    Автоматически определяет, использовать parse_mode или entities.
    """
    final_text, entities = build_message(parts_list)
    
    parse_mode = None
    if final_text and not entities:
        parse_mode = "html"

    kwargs.pop('parse_mode', None)

    try:
        if event.out:
            if entities:
                await event.edit(final_text, formatting_entities=entities, **kwargs)
            else:
                await event.edit(final_text, parse_mode=parse_mode, link_preview=False, **kwargs)
        else:
            if entities:
                await event.respond(final_text, formatting_entities=entities, **kwargs)
            else:
                await event.respond(final_text, parse_mode=parse_mode, link_preview=False, **kwargs)
            
    except MessageNotModifiedError:
        pass
    except Exception as e:
        print(f"Ошибка в build_and_edit: {e}")
        pass