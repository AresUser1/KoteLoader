# handlers/user_commands.py

import traceback
from telethon import events
from telethon.tl.functions.messages import GetInlineBotResultsRequest, SendInlineBotResultRequest

async def _call_inline_bot(event: events.NewMessage.Event, query: str):
    """Внутренняя функция для вызова инлайн-бота с указанным запросом."""
    try:
        bot_client = event.client.bot_client
        if not bot_client:
            return await event.edit("❌ **Бот-помощник неактивен.**")
            
        bot_info = await bot_client.get_me()
        
        result = await event.client(GetInlineBotResultsRequest(
            bot=bot_info.username,
            peer=await event.get_input_chat(),
            query=query,
            offset="",
            geo_point=None
        ))

        if not result.results:
            return await event.edit(f"❌ **Бот не вернул результат для запроса:** `{query}`")

        await event.client(SendInlineBotResultRequest(
            peer=await event.get_input_chat(),
            query_id=result.query_id,
            id=str(result.results[0].id)
        ))
        
        await event.delete()
        
    except Exception:
        await event.edit(f"⚠️ **Ошибка при вызове инлайн-панели:**\n`{traceback.format_exc()}`")

async def user_panel_helper(event: events.NewMessage.Event):
    """Программно вызывает главную инлайн-панель бота."""
    if not event.out: return
    search_query = event.pattern_match.group(2) or ""
    await _call_inline_bot(event, search_query)

# ❗️ НАША НОВАЯ ФУНКЦИЯ-ОБРАБОТЧИК ❗️
async def module_inline_handler(event: events.NewMessage.Event):
    """
    Перехватывает команды управления модулями и вызывает инлайн-панель для конкретного модуля.
    """
    if not event.out: return
    
    # event.pattern_match.group(1) будет 'load', 'reload' и т.д.
    module_name = event.pattern_match.group(2) or ""
    
    if not module_name:
        return await event.edit("❌ **Укажите имя модуля.**")
        
    # Формируем специальный запрос для инлайн-бота
    query = f"module:{module_name.strip()}"
    await _call_inline_bot(event, query)