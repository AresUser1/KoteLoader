# modules/twins.py
"""<manifest>
version: 2.1.0
source: https://github.com/AresUser1/KoteLoader/raw/main/modules/twins.py
author: Kote

Команды:
• addtwin <имя> - Запустить интерактивное добавление твинка.
• deltwin <имя> - Удалить твинка.
• twins - Список активных твинков.
• twinping <имя> - Проверка отклика твинка.
• cancel - Отменить текущий процесс.
</manifest>"""

import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError, 
    PasswordHashInvalidError, PhoneNumberInvalidError
)
from core import register, watcher
from services.twin_manager import twin_manager
from utils.message_builder import build_and_edit
from utils.security import check_permission
from utils import database as db
from telethon.tl.types import MessageEntityBold, MessageEntityCode, MessageEntityCustomEmoji

# --- PREMIUM EMOJI CONSTANTS ---
# Цифры для шагов
NUM_1_ID = 5249450556933550940 # 1️⃣ 
NUM_2_ID = 5251425391486183744 # 2️⃣
NUM_3_ID = 5249051365493190415 # 3️⃣ (используем похожий если нет уникального)

# Интерфейс
PHONE_ID = 5785379836008598919   # 📱
KEY_ID = 5454386656628991407     # 🔑 (для пароля 2FA)
SMS_ID = 5454386656628991407     # 📩 (для кода)
SUCCESS_ID = 5776375003280838798 # ✅
ERROR_ID = 5778527486270770928   # ❌
LOADING_ID = 5877410604225924969 # 🔄
USER_ID_EMOJI = 5920344347152224466 # 👤
TRASH_ID = 5841541824803509441   # 🗑
INFO_ID = 5879785854284599288    # ℹ️
STOP_ID = 5877413297170419326    # ⛔ (для отмены)
ONLINE_ID = 5818797194127346654  # 🟢
OFFLINE_ID = 5819137913882939159 # 🔴

# --- STATE MANAGEMENT ---
AUTH_SESSIONS = {}

class AuthState:
    WAIT_PHONE = "WAIT_PHONE"
    WAIT_CODE = "WAIT_CODE"
    WAIT_PASSWORD = "WAIT_PASSWORD"

@register("addtwin")
async def start_add_twin(event):
    """Начинает процесс добавления твинка."""
    if not check_permission(event, min_level="OWNER"):
        return

    prefix = db.get_setting("prefix", default=".") 
    user_id = event.sender_id
    
    if user_id in AUTH_SESSIONS:
        return await build_and_edit(event, [
            {"text": "⚠️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": INFO_ID}},
            {"text": f" У вас уже запущен процесс. Используйте {prefix}cancel для отмены."}
        ])

    args = event.message.text.split(maxsplit=1)
    if len(args) < 2:
        return await build_and_edit(event, [
            {"text": "ℹ️", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": INFO_ID}},
            {"text": " Использование: ", "entity": MessageEntityBold},
            {"text": f"{prefix}addtwin <имя_твинка>", "entity": MessageEntityCode} 
        ])
    
    twin_name = args[1]
    if twin_name in twin_manager.get_stored_twins():
        return await build_and_edit(event, [
            {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_ID}},
            {"text": " Твинк с таким именем уже существует."}
        ])

    temp_client = TelegramClient(StringSession(), twin_manager.api_id, twin_manager.api_hash)
    await temp_client.connect()

    AUTH_SESSIONS[user_id] = {
        "state": AuthState.WAIT_PHONE,
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
        {"text": " Для отмены: ", "entity": MessageEntityBold},
        {"text": f"{prefix}cancel", "entity": MessageEntityCode} 
    ])

@register("cancel")
async def cancel_auth(event):
    """Отменяет процесс."""
    if not check_permission(event, min_level="OWNER"):
        return

    user_id = event.sender_id
    if user_id not in AUTH_SESSIONS:
        return await build_and_edit(event, [{"text": "ℹ️ Нет активных процессов."}])

    session = AUTH_SESSIONS[user_id]
    await session['client'].disconnect()
    del AUTH_SESSIONS[user_id]

    await build_and_edit(event, [
        {"text": "⛔", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": STOP_ID}},
        {"text": " Процесс добавления отменен."}
    ])

@watcher(outgoing=True)
async def auth_input_watcher(event):
    """Перехватывает ввод данных."""
    user_id = event.sender_id
    if user_id not in AUTH_SESSIONS: return
    
    session = AUTH_SESSIONS[user_id]
    client = session["client"]
    text = event.message.text.strip()
    
    prefix = db.get_setting("prefix", default=".")
    if text.startswith(prefix): return # Игнор команд
    if event.chat_id != session["chat_id"]: return # Игнор других чатов

    try:
        if session["state"] == AuthState.WAIT_PHONE:
            await event.delete()
            phone = text.replace(" ", "")
            
            # Сообщение ожидания
            status_msg = await event.client.send_message(event.chat_id, "🔄 Запрос кода...", parse_mode="md") # Можно тоже сделать через build_and_edit но для скорости оставим так, эмодзи в тексте
            
            try:
                pc_hash = await client.send_code_request(phone)
                session["phone"] = phone
                session["phone_hash"] = pc_hash.phone_code_hash
                session["state"] = AuthState.WAIT_CODE
                
                # Используем build_and_edit для редактирования
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
                    {"text": " Неверный номер. Попробуйте снова."}
                ])
            except Exception as e:
                await _handle_error(event, user_id, status_msg, e)

        elif session["state"] == AuthState.WAIT_CODE:
            await event.delete()
            code = text
            status_msg = await event.client.send_message(event.chat_id, "🔄 Проверка кода...")

            try:
                await client.sign_in(session["phone"], code, phone_code_hash=session["phone_hash"])
                await _finish_auth(status_msg, session)
            except SessionPasswordNeededError:
                session["state"] = AuthState.WAIT_PASSWORD
                await build_and_edit(status_msg, [
                    {"text": "🔑", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": KEY_ID}},
                    {"text": " Нужен облачный пароль (2FA).", "entity": MessageEntityBold},
                    {"text": "\n\n"},
                    {"text": "3️⃣", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": NUM_3_ID}},
                    {"text": " Введите пароль.", "entity": MessageEntityBold}
                ])
            except PhoneCodeInvalidError:
                await build_and_edit(status_msg, [{"text": "❌ Неверный код. Попробуйте снова."}])
            except Exception as e:
                await _handle_error(event, user_id, status_msg, e)

        elif session["state"] == AuthState.WAIT_PASSWORD:
            await event.delete()
            password = text
            status_msg = await event.client.send_message(event.chat_id, "🔄 Проверка пароля...")

            try:
                await client.sign_in(password=password)
                await _finish_auth(status_msg, session)
            except PasswordHashInvalidError:
                await build_and_edit(status_msg, [{"text": "❌ Неверный пароль. Попробуйте снова."}])
            except Exception as e:
                await _handle_error(event, user_id, status_msg, e)

    except Exception as e:
        # Глобальный перехватчик
        await _handle_error(event, user_id, None, e)

async def _handle_error(event, user_id, msg, e):
    """Вспомогательная функция для обработки фатальных ошибок."""
    if user_id in AUTH_SESSIONS:
        await AUTH_SESSIONS[user_id]['client'].disconnect()
        del AUTH_SESSIONS[user_id]
    
    text_parts = [
        {"text": "❌", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": ERROR_ID}},
        {"text": " Ошибка: ", "entity": MessageEntityBold},
        {"text": str(e), "entity": MessageEntityCode},
        {"text": "\nПроцесс отменен."}
    ]
    
    if msg:
        await build_and_edit(msg, text_parts)
    else:
        await build_and_edit(event, text_parts)

async def _finish_auth(msg, session):
    try:
        client = session["client"]
        name = session["name"]
        string_session = StringSession.save(client.session)
        await client.disconnect()
        
        twin_manager.save_twin(name, string_session)
        me = await (await twin_manager.start_twin(name)).get_me()
        
        await build_and_edit(msg, [
            {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_ID}},
            {"text": " Твинк добавлен!\n\n", "entity": MessageEntityBold},
            {"text": "👤 Имя: "}, {"text": me.first_name, "entity": MessageEntityCode}, {"text": "\n"},
            {"text": "🆔 ID: "}, {"text": str(me.id), "entity": MessageEntityCode}, {"text": "\n"},
            {"text": "🔖 Алиас: "}, {"text": name, "entity": MessageEntityCode}
        ])
    except Exception as e:
        await build_and_edit(msg, [{"text": f"❌ Ошибка сохранения: {e}"}])
    finally:
        # Удаляем сессию из памяти
        keys_to_del = [k for k, v in AUTH_SESSIONS.items() if v == session]
        for k in keys_to_del: del AUTH_SESSIONS[k]

@register("deltwin")
async def del_twin_cmd(event):
    if not check_permission(event, min_level="OWNER"): return
    name = event.pattern_match.group(1)
    if not name: return await build_and_edit(event, [{"text": "❌ Укажите имя."}])

    if name not in twin_manager.clients and name not in twin_manager.get_stored_twins():
        return await build_and_edit(event, [{"text": "❌ Не найден."}])

    await twin_manager.stop_twin(name)
    twin_manager.remove_twin_data(name)

    await build_and_edit(event, [
        {"text": "🗑", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": TRASH_ID}},
        {"text": f" Твинк {name} удален."}
    ])

@register("twins")
async def list_twins_cmd(event):
    if not check_permission(event, min_level="OWNER"): return
    
    active = twin_manager.clients
    stored = twin_manager.get_stored_twins()

    if not stored:
        return await build_and_edit(event, [{"text": "ℹ️ Список пуст."}])

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

@register("twinping")
async def twin_ping_cmd(event):
    if not check_permission(event, min_level="OWNER"): return
    name = event.pattern_match.group(1)
    client = twin_manager.get_client(name)
    
    if not client: return await build_and_edit(event, [{"text": "❌ Оффлайн или не найден."}])
    
    try:
        start = asyncio.get_event_loop().time()
        msg = await client.send_message("me", f"Ping!")
        end = asyncio.get_event_loop().time()
        await msg.delete()
        
        await build_and_edit(event, [
            {"text": "✅", "entity": MessageEntityCustomEmoji, "kwargs": {"document_id": SUCCESS_ID}},
            {"text": f" {name}: {(end - start) * 1000:.2f} ms"}
        ])
    except Exception as e:
        await build_and_edit(event, [{"text": f"❌ {e}"}])