# panels/updates_panel.py

from telethon.tl.custom import Button

def build_updates_panel(updates: list):
    """
    Собирает меню со списком доступных обновлений.
    Всегда возвращает HTML-текст и кнопки, так как используется только в inline-режиме.
    
    Args:
        updates: Список словарей с информацией об обновлениях.
    """
    buttons = []
    
    if not updates:
        text = "✅ <b>Все модули имеют последнюю версию!</b>"
    else:
        text = f"🔎 <b>Найдено обновлений: {len(updates)}</b>\n\n"
        
        # Добавляем кнопку для каждого модуля
        for u in updates:
            text += f"• <code>{u['module_name']}</code>: {u['old_version']} → <b>{u['new_version']}</b>\n"
            buttons.append([Button.inline(
                f"🚀 Обновить {u['module_name']}",
                data=f"do_update:{u['module_name']}"
            )])
        
        # Добавляем кнопку "Обновить всё", если обновлений больше одного
        if len(updates) > 1:
            buttons.insert(0, [Button.inline("🚀 Обновить всё", data="do_update:all")])

    buttons.append([Button.inline("❌ Закрыть", data="close_panel")])
    
    return text, buttons