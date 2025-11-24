# modules/twins.py
"""
<manifest>
version: 2.1.1
source: https://t.me/KoteModulesMint
author: Kote
</manifest>

Модуль для управления твинками (дополнительными юзерботами).
Позволяет добавлять аккаунты через встроенный процесс авторизации, проверять их статус и удалять.
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

# Emoji IDs
NUM_1_ID = 5249450556933550940
NUM_2_ID = 5251425391486183744
NUM_3_ID = 5249051365493190415
PHONE_ID = 5785379836008598919
KEY_ID = 5454386656628991407
SMS_ID = 5454386656628991407
SUCCESS_ID = 5776375003280838798
ERROR_ID = 5778527486270770928
LOADING_ID = 5877410604225924969
USER_ID_EMOJI = 5920344347152224466
TRASH_ID = 5841541824803509441
INFO_ID = 5879785854284599288
STOP_ID = 5877413297170419326
ONLINE_ID = 5818797194127346654
OFFLINE_ID = 5819137913882939159

# State Constants
WAIT_PHONE = "WAIT_PHONE"
WAIT_CODE = "WAIT_CODE"
WAIT_PASSWORD = "WAIT_PASSWORD"

# Global State
AUTH_SESSIONS = {}

# --- HELPERS ---

async def _handle_error(user_id, msg, e):
    """Обработка ошибок авторизации и очистка сессии."""
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
    
    if msg:
        await build_and_edit(msg, parts)

async def _finish_auth(msg, session, user_id):
    """Завершение авторизации и сохранение твинка."""
    try:
        client = session["client"]
        name = session["name"]
        
        # Сохраняем строку сессии
        string_session = StringSession.save(client.session)
        await client.disconnect()
        
        # Регистрируем твинка в менеджере
        twin_manager.save_twin(name, string_session)
        
        # Запускаем, чтобы получить информацию о профиле
        started_client = await twin_manager.start_twin(name)
        me = await started_client.get_me()
        
        await build_and_edit(msg, [
            {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_ID}},
            {"text": " Твинк успешно добавлен!\n\n", "entity": MessageEntityBold},
            {"text": "👤 Имя: "}, {"text": me.first_name, "entity": MessageEntityCode}, {"text": "\n"},
            {"text": "🆔 ID: "}, {"text": str(me.id), "entity": MessageEntityCode}, {"text": "\n"},
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
    """Запустить процесс добавления нового твинка.
    
    Usage: {prefix}addtwin <имя>
    """
    if not check_permission(event, min_level="OWNER"):
        return

    prefix = db.get_setting("prefix", default=".") 
    user_id = event.sender_id
    
    if user_id in AUTH_SESSIONS:
        return await build_and_edit(event, [
            {"text": "⚠️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": INFO_ID}},
            {"text": f" Процесс уже запущен. Отмена: {prefix}cancel"}
        ])

    args = event.message.text.split(maxsplit=1)
    if len(args) < 2:
        return await build_and_edit(event, [
            {"text": "ℹ️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": INFO_ID}},
            {"text": " Использование: ", "entity": MessageEntityBold},
            {"text": f"{prefix}addtwin <имя>", "entity": MessageEntityCode} 
        ])
    
    twin_name = args[1]
    if twin_name in twin_manager.get_stored_twins():
        return await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_ID}},
            {"text": " Твинк с таким именем уже существует."}
        ])

    # Инициализация временного клиента
    try:
        temp_client = TelegramClient(StringSession(), twin_manager.api_id, twin_manager.api_hash)
        await temp_client.connect()
    except Exception as e:
        return await build_and_edit(event, [{"text": f"❌ Ошибка соединения: {e}"}])

    AUTH_SESSIONS[user_id] = {
        "state": WAIT_PHONE,
        "client": temp_client,
        "name": twin_name,
        "chat_id": event.chat_id 
    }

    await build_and_edit(event, [
        {"text": "👤", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": USER_ID_EMOJI}},
        {"text": " Добавление твинка: ", "entity": MessageEntityBold},
        {"text": twin_name, "entity": MessageEntityCode},
        {"text": "\n\n"},
        
        {"text": "1️⃣", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": NUM_1_ID}},
        {"text": " Введите номер телефона", "entity": MessageEntityBold},
        {"text": " (например +79990000000).\n\n"},
        
        {"text": "⛔", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": STOP_ID}},
        {"text": " Отмена: ", "entity": MessageEntityBold},
        {"text": f"{prefix}cancel", "entity": MessageEntityCode} 
    ])

@register("cancel", incoming=True)
async def cancel_auth(event):
    """Отменить текущий процесс добавления.
    
    Usage: {prefix}cancel
    """
    if not check_permission(event, min_level="OWNER"):
        return

    user_id = event.sender_id
    if user_id not in AUTH_SESSIONS:
        return await build_and_edit(event, [{"text": "ℹ️ Нет активных процессов."}])

    try:
        await AUTH_SESSIONS[user_id]['client'].disconnect()
    except: pass
    
    del AUTH_SESSIONS[user_id]

    await build_and_edit(event, [
        {"text": "⛔", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": STOP_ID}},
        {"text": " Процесс добавления отменен."}
    ])

@register("deltwin", incoming=True)
async def del_twin_cmd(event):
    """Удалить твинка.
    
    Usage: {prefix}deltwin <имя>
    """
    if not check_permission(event, min_level="OWNER"): return
    name = event.pattern_match.group(1)
    if not name: return await build_and_edit(event, [{"text": "❌ Укажите имя."}])

    if name not in twin_manager.clients and name not in twin_manager.get_stored_twins():
        return await build_and_edit(event, [{"text": "❌ Твинк не найден."}])

    await twin_manager.stop_twin(name)
    twin_manager.remove_twin_data(name)

    await build_and_edit(event, [
        {"text": "🗑", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": TRASH_ID}},
        {"text": f" Твинк {name} удален."}
    ])

@register("twins", incoming=True)
async def list_twins_cmd(event):
    """Показать список твинков.
    
    Usage: {prefix}twins
    """
    if not check_permission(event, min_level="OWNER"): return
    
    active = twin_manager.clients
    stored = twin_manager.get_stored_twins()

    if not stored:
        return await build_and_edit(event, [{"text": "ℹ️ Список твинков пуст."}])

    parts = [
        {"text": "👥", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": USER_ID_EMOJI}},
        {"text": " Ваши твинки:\n\n", "entity": MessageEntityBold}
    ]
    
    for name in stored:
        is_online = name in active
        status_id = ONLINE_ID if is_online else OFFLINE_ID
        
        parts.append({"text": "🟢" if is_online else "🔴", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": status_id}})
        parts.append({"text": f" {name}", "entity": MessageEntityBold})
        
        if is_online:
            try:
                me = await active[name].get_me()
                parts.append({"text": f" (ID: {me.id})"})
            except: pass
        parts.append({"text": "\n"})

    await build_and_edit(event, parts)

@register("twinping", incoming=True)
async def twin_ping_cmd(event):
    """Проверка пинга твинка.
    
    Usage: {prefix}twinping <имя>
    """
    if not check_permission(event, min_level="OWNER"): return
    name = event.pattern_match.group(1)
    
    client = twin_manager.get_client(name)
    if not client: 
        return await build_and_edit(event, [{"text": "❌ Твинк оффлайн или не найден."}])
    
    try:
        start = asyncio.get_event_loop().time()
        msg = await client.send_message("me", "Ping!")
        end = asyncio.get_event_loop().time()
        await msg.delete()
        
        await build_and_edit(event, [
            {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_ID}},
            {"text": f" {name}: {(end - start) * 1000:.2f} ms"}
        ])
    except Exception as e:
        await build_and_edit(event, [{"text": f"❌ Ошибка: {e}"}])

# --- WATCHER (AUTH FLOW) ---

@watcher(outgoing=True)
async def auth_input_watcher(event):
    """Перехватчик ввода для процесса авторизации."""
    user_id = event.sender_id
    if user_id not in AUTH_SESSIONS: return
    
    session = AUTH_SESSIONS[user_id]
    client = session["client"]
    text = event.message.text.strip()
    
    # Игнор команд и других чатов
    prefix = db.get_setting("prefix", default=".")
    if text.startswith(prefix): return 
    if event.chat_id != session["chat_id"]: return 

    try:
        # STEP 1: PHONE NUMBER
        if session["state"] == WAIT_PHONE:
            await event.delete()
            phone = text.replace(" ", "")
            
            status_msg = await event.client.send_message(event.chat_id, "🔄 Запрос кода...")
            
            try:
                pc_hash = await client.send_code_request(phone)
                session["phone"] = phone
                session["phone_hash"] = pc_hash.phone_code_hash
                session["state"] = WAIT_CODE
                
                await build_and_edit(status_msg, [
                    {"text": "📩", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SMS_ID}},
                    {"text": " Код отправлен на ", "entity": MessageEntityBold},
                    {"text": phone, "entity": MessageEntityCode},
                    {"text": "\n\n"},
                    {"text": "2️⃣", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": NUM_2_ID}},
                    {"text": " Введите код из Telegram.", "entity": MessageEntityBold}
                ])
            except PhoneNumberInvalidError:
                await build_and_edit(status_msg, [
                    {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_ID}},
                    {"text": " Неверный номер телефона."}
                ])
            except Exception as e:
                await _handle_error(user_id, status_msg, e)

        # STEP 2: CODE
        elif session["state"] == WAIT_CODE:
            await event.delete()
            code = text
            status_msg = await event.client.send_message(event.chat_id, "🔄 Проверка кода...")

            try:
                await client.sign_in(session["phone"], code, phone_code_hash=session["phone_hash"])
                await _finish_auth(status_msg, session, user_id)
            except SessionPasswordNeededError:
                session["state"] = WAIT_PASSWORD
                await build_and_edit(status_msg, [
                    {"text": "🔑", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": KEY_ID}},
                    {"text": " Нужен пароль 2FA.", "entity": MessageEntityBold},
                    {"text": "\n\n"},
                    {"text": "3️⃣", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": NUM_3_ID}},
                    {"text": " Введите облачный пароль.", "entity": MessageEntityBold}
                ])
            except PhoneCodeInvalidError:
                await build_and_edit(status_msg, [{"text": "❌ Неверный код."}])
            except Exception as e:
                await _handle_error(user_id, status_msg, e)

        # STEP 3: PASSWORD (2FA)
        elif session["state"] == WAIT_PASSWORD:
            await event.delete()
            password = text
            status_msg = await event.client.send_message(event.chat_id, "🔄 Проверка пароля...")

            try:
                await client.sign_in(password=password)
                await _finish_auth(status_msg, session, user_id)
            except PasswordHashInvalidError:
                await build_and_edit(status_msg, [{"text": "❌ Неверный пароль."}])
            except Exception as e:
                await _handle_error(user_id, status_msg, e)

    except Exception as e:
        await _handle_error(user_id, None, e)