# modules/twins.py
"""
<manifest>
version: 2.3.0
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/twins.py
author: Kote
</manifest>

Модуль для управления твинками.
Поддерживает Twin+ (свои api_id/hash) для снижения риска спам-блока.
"""

import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError, 
    PasswordHashInvalidError, PhoneNumberInvalidError
)
from telethon.tl.types import (
    MessageEntityBold, MessageEntityCode, MessageEntityCustomEmoji
)

from core import register, watcher
from services.twin_manager import twin_manager
from utils import database as db
from utils.message_builder import build_and_edit
from utils.security import check_permission

# --- CONSTANTS ---
NUM_1_ID = 5249450556933550940
NUM_2_ID = 5251425391486183744
NUM_3_ID = 5249051365493190415
PHONE_ID = 5785379836008598919
KEY_ID = 5454386656628991407
SMS_ID = 5454386656628991407
SUCCESS_ID = 5776375003280838798
ERROR_ID = 5778527486270770928
USER_ID_EMOJI = 5920344347152224466
TRASH_ID = 5841541824803509441
INFO_ID = 5879785854284599288
STOP_ID = 5877413297170419326
ONLINE_ID = 5818797194127346654
OFFLINE_ID = 5819137913882939159

WAIT_PHONE = "WAIT_PHONE"
WAIT_CODE = "WAIT_CODE"
WAIT_PASSWORD = "WAIT_PASSWORD"

AUTH_SESSIONS = {}

# --- HELPERS ---

async def _handle_error(user_id, msg, e):
    if user_id in AUTH_SESSIONS:
        try:
            await AUTH_SESSIONS[user_id]['client'].disconnect()
        except: pass
        del AUTH_SESSIONS[user_id]
    
    parts = [
        {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_ID}},
        {"text": " Ошибка: ", "entity": MessageEntityBold},
        {"text": str(e), "entity": MessageEntityCode},
        {"text": "\nПроцесс отменен."}
    ]
    if msg: await build_and_edit(msg, parts)

async def _finish_auth(msg, session, user_id):
    try:
        client = session["client"]
        name = session["name"]
        
        # Достаем кастомные данные, если есть
        c_api_id = session.get("custom_api_id")
        c_api_hash = session.get("custom_api_hash")

        string_session = StringSession.save(client.session)
        await client.disconnect()
        
        # Сохраняем в менеджер
        twin_manager.save_twin(name, string_session, api_id=c_api_id, api_hash=c_api_hash)
        
        # Сразу запускаем
        started_client = await twin_manager.start_twin(name)
        me = await started_client.get_me()
        
        type_str = "Twin+" if c_api_id else "Twin"
        
        await build_and_edit(msg, [
            {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_ID}},
            {"text": f" {type_str} добавлен!\n\n", "entity": MessageEntityBold},
            {"text": "👤 Имя: "}, {"text": me.first_name, "entity": MessageEntityCode}, {"text": "\n"},
            {"text": "🔖 Алиас: "}, {"text": name, "entity": MessageEntityCode}
        ])
    except Exception as e:
        await build_and_edit(msg, [{"text": f"❌ Ошибка сохранения: {e}"}])
    finally:
        if user_id in AUTH_SESSIONS:
            del AUTH_SESSIONS[user_id]

# --- COMMANDS ---

@register("addtwin", incoming=True)
async def start_add_twin(event):
    """Добавить твинка.
    
    Twin:  {prefix}addtwin <имя>
    Twin+: {prefix}addtwin <имя> <api_id> <api_hash>
    """
    if not check_permission(event, min_level="OWNER"): return

    prefix = db.get_setting("prefix", default=".") 
    user_id = event.sender_id
    
    if user_id in AUTH_SESSIONS:
        return await build_and_edit(event, [{"text": f"⚠️ Процесс уже запущен. Отмена: {prefix}cancel"}])

    args = event.message.text.split()
    # args[0] = .addtwin, args[1] = Name, args[2] = ID, args[3] = Hash
    
    if len(args) < 2:
        return await build_and_edit(event, [{"text": f"ℹ️ Использование: {prefix}addtwin <имя> [api_id] [api_hash]" }])
    
    twin_name = args[1]
    
    c_api_id = None
    c_api_hash = None
    
    # Парсим аргументы для Twin+
    if len(args) >= 4:
        try:
            c_api_id = int(args[2])
            c_api_hash = args[3]
        except ValueError:
            return await build_and_edit(event, [{"text": "❌ api_id должен быть числом."}])

    if twin_name in twin_manager.get_stored_twins():
        return await build_and_edit(event, [{"text": "❌ Твинк с таким именем уже существует."}])

    # Пробуем подключиться с нужным API ID
    try:
        use_id = c_api_id or twin_manager.global_api_id
        use_hash = c_api_hash or twin_manager.global_api_hash
        
        temp_client = TelegramClient(StringSession(), use_id, use_hash)
        await temp_client.connect()
    except Exception as e:
        return await build_and_edit(event, [{"text": f"❌ Ошибка инициализации клиента: {e}"}])

    AUTH_SESSIONS[user_id] = {
        "state": WAIT_PHONE,
        "client": temp_client,
        "name": twin_name,
        "chat_id": event.chat_id,
        "custom_api_id": c_api_id,     # Сохраняем в сессии авторизации
        "custom_api_hash": c_api_hash
    }

    type_msg = "Twin+" if c_api_id else "Twin"
    await build_and_edit(event, [
        {"text": "👤", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": USER_ID_EMOJI}},
        {"text": f" Добавление {type_msg}: ", "entity": MessageEntityBold},
        {"text": twin_name, "entity": MessageEntityCode},
        {"text": "\n\n"},
        {"text": "1️⃣ Введите номер телефона..."}
    ])

@register("cancel", incoming=True)
async def cancel_auth(event):
    """Отменить процесс добавления твинка."""
    if not check_permission(event, min_level="OWNER"): return
    user_id = event.sender_id
    if user_id not in AUTH_SESSIONS: return await build_and_edit(event, [{"text": "ℹ️ Нет активных процессов."}])
    try: await AUTH_SESSIONS[user_id]['client'].disconnect()
    except: pass
    del AUTH_SESSIONS[user_id]
    await build_and_edit(event, [{"text": "⛔ Отменено."}])

@register("deltwin", incoming=True)
async def del_twin_cmd(event):
    """Удалить твинка.
    
    Usage: {prefix}deltwin <имя>
    """
    if not check_permission(event, min_level="OWNER"): return
    name = event.pattern_match.group(1)
    if not name: return await build_and_edit(event, [{"text": "❌ Укажите имя."}])
    await twin_manager.stop_twin(name)
    twin_manager.remove_twin_data(name)
    await build_and_edit(event, [{"text": f"🗑 Твинк {name} удален."}])

@register("twins", incoming=True)
async def list_twins_cmd(event):
    """Список добавленных твинков.
    
    Usage: {prefix}twins
    """
    if not check_permission(event, min_level="OWNER"): return
    active = twin_manager.clients
    stored = twin_manager.get_stored_twins()
    if not stored: return await build_and_edit(event, [{"text": "ℹ️ Список пуст."}])

    parts = [{"text": "👥 Ваши твинки:\n\n", "entity": MessageEntityBold}]
    for name, data in stored.items():
        is_online = name in active
        is_plus = "api_id" in data # Проверка на наличие кастомного API ID
        
        status_icon = "🟢" if is_online else "🔴"
        type_icon = "⚡️" if is_plus else "🤖"
        
        parts.append({"text": f"{status_icon} {type_icon} "})
        parts.append({"text": f"{name}", "entity": MessageEntityBold})
        if is_online:
            try:
                me = await active[name].get_me()
                parts.append({"text": f" (ID: {me.id})"})
            except: pass
        parts.append({"text": "\n"})

    await build_and_edit(event, parts)

# --- WATCHER ---
@watcher(outgoing=True)
async def auth_input_watcher(event):
    """Перехватчик ввода для авторизации."""
    user_id = event.sender_id
    if user_id not in AUTH_SESSIONS: return
    session = AUTH_SESSIONS[user_id]
    client = session["client"]
    text = event.message.text.strip()
    prefix = db.get_setting("prefix", default=".")
    if text.startswith(prefix): return 
    if event.chat_id != session["chat_id"]: return 

    try:
        if session["state"] == WAIT_PHONE:
            await event.delete()
            phone = text.replace(" ", "")
            status_msg = await event.client.send_message(event.chat_id, "🔄 Запрос кода...")
            try:
                pc_hash = await client.send_code_request(phone)
                session["phone"] = phone
                session["phone_hash"] = pc_hash.phone_code_hash
                session["state"] = WAIT_CODE
                await build_and_edit(status_msg, [{"text": "📩 Код отправлен. Введите его:"}])
            except Exception as e: await _handle_error(user_id, status_msg, e)

        elif session["state"] == WAIT_CODE:
            await event.delete()
            status_msg = await event.client.send_message(event.chat_id, "🔄 Проверка...")
            try:
                await client.sign_in(session["phone"], text, phone_code_hash=session["phone_hash"])
                await _finish_auth(status_msg, session, user_id)
            except SessionPasswordNeededError:
                session["state"] = WAIT_PASSWORD
                await build_and_edit(status_msg, [{"text": "🔑 Введите пароль 2FA:"}])
            except Exception as e: await _handle_error(user_id, status_msg, e)

        elif session["state"] == WAIT_PASSWORD:
            await event.delete()
            status_msg = await event.client.send_message(event.chat_id, "🔄 Проверка пароля...")
            try:
                await client.sign_in(password=text)
                await _finish_auth(status_msg, session, user_id)
            except Exception as e: await _handle_error(user_id, status_msg, e)

    except Exception as e: await _handle_error(user_id, None, e)