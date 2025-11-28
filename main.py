# main.py
import asyncio
import logging
import re
import time
import os
import uuid
from configparser import ConfigParser
from telethon import TelegramClient, events
from telethon.errors import AccessTokenInvalidError, AccessTokenExpiredError

LOG_FILE = "kote_loader.log"
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    handlers=[logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8'), logging.StreamHandler()])
logging.getLogger('telethon').setLevel(logging.WARNING)

try:
    from handlers.bot_callbacks import inline_query_handler, callback_query_handler
    from handlers.user_commands import user_panel_helper
    from workers.command_worker import command_worker
    from utils import database as db
    from utils import loader
    from services.twin_manager import twin_manager 
except ImportError as e:
    print(f"Критическая ошибка: не удалось импортировать необходимый компонент: {e}")
    exit()

START_TIME = time.time()

async def heartbeat():
    while True:
        await asyncio.sleep(60)
        print("💓 System Pulse: OK") 

async def ensure_inline_mode_enabled(user_client, bot_username):
    try:
        print(f"🔄 Проверяем настройки inline-режима для @{bot_username}...")
        async with user_client.conversation('@BotFather', timeout=40, exclusive=False) as conv:
            await conv.send_message('/cancel')
            await asyncio.sleep(0.5)
            
            await conv.send_message('/setinline')
            resp = await conv.get_response()
            
            if "Choose a bot" in resp.text:
                await conv.send_message(f"@{bot_username}")
                resp = await conv.get_response()

            if "placeholder" in resp.text.lower():
                await conv.send_message("Search...")
                await conv.get_response()
                print(f"✅ Inline-режим для @{bot_username} успешно активирован/обновлен.")
            elif "Success" in resp.text:
                print(f"✅ Inline-режим для @{bot_username} уже активен.")
            else:
                print(f"ℹ️ Ответ BotFather: {resp.text.splitlines()[0]}")
                 
    except Exception as e:
        print(f"⚠️ Не удалось автоматически включить inline-режим: {e}")
        print("   (Если меню не работает, включите его вручную в @BotFather -> Bot Settings -> Inline Mode)")

async def auto_create_bot(user_client):
    print("\n🤖 Начинаем автоматическое создание бота через @BotFather...")
    async with user_client.conversation('@BotFather', timeout=60, exclusive=True) as conv:
        try:
            await conv.send_message('/cancel')
            await asyncio.sleep(0.5)
            
            await conv.send_message('/newbot')
            resp = await conv.get_response()
            
            if "try again in" in resp.text:
                print("❌ BotFather просит подождать (флуд-лимит).")
                return None
            
            if "can't add more than" in resp.text:
                print("❌ ОШИБКА: Достигнут лимит созданных ботов.")
                print("   Удалите старых через /deletebot в @BotFather.")
                return None
            
            await conv.send_message("KoteLoader Userbot")
            resp = await conv.get_response()
            
            if "choose a username" not in resp.text.lower():
                print(f"⚠️ Сбой диалога с BotFather: {resp.text}")
                return None

            bot_token = None
            
            for attempt in range(5):
                random_part = uuid.uuid4().hex[:6]
                username_to_try = f"Kote_{random_part}_bot"
                await conv.send_message(username_to_try)
                resp = await conv.get_response()
                
                if "Done!" in resp.text:
                    match = re.search(r'(\d+:[a-zA-Z0-9_-]{35})', resp.text)
                    if match:
                        bot_token = match.group(1)
                        print(f"✅ Бот успешно создан: @{username_to_try}")
                        break
                elif "taken" in resp.text:
                    print(f"   Юзернейм {username_to_try} занят, пробую другой...")
                    continue
                else:
                    print(f"❌ Ошибка BotFather: {resp.text}")
                    return None

            if not bot_token:
                print("❌ Не удалось создать бота за 5 попыток.")
                return None
            
            return bot_token

        except asyncio.TimeoutError:
            print("❌ BotFather не ответил вовремя.")
            return None

async def all_messages_handler(event):
    for watcher_func, kwargs in loader.WATCHERS_REGISTRY:
        is_incoming = kwargs.get("incoming", False)
        is_outgoing = kwargs.get("outgoing", False)
        if (is_incoming and event.incoming) or (is_outgoing and event.outgoing):
            await watcher_func(event)

async def start_clients():
    config = ConfigParser()
    config_file = "config.ini"

    if os.path.exists(config_file):
        config.read(config_file, encoding='utf-8')
    
    if not config.has_section("telethon"):
        print(f"⚙️ Файл конфигурации не найден. Приступим к настройке.")
        api_id = input("Введите api_id: ")
        api_hash = input("Введите api_hash: ")
        session_name = "my_account"
        
        config['telethon'] = {
            'api_id': api_id, 
            'api_hash': api_hash, 
            'session_name': session_name
        }
        with open(config_file, 'w', encoding='utf-8') as f:
            config.write(f)
    else:
        api_id = config.getint("telethon", "api_id")
        api_hash = config.get("telethon", "api_hash")
        session_name = config.get("telethon", "session_name")

    print(f"\n🚀 Подключение к аккаунту ({session_name})...")
    user_client = TelegramClient(session_name, api_id, api_hash)
    
    await user_client.connect()
    if not await user_client.is_user_authorized():
        phone_number = input("Введите номер телефона (например +79001234567): ")
        await user_client.start(phone=phone_number)
    else:
        await user_client.start()

    print("✅ Успешный вход в аккаунт!")

    bot_client = None
    
    while True:
        bot_token = config.get("telethon", "bot_token", fallback=None)
        
        if not bot_token:
            print("\n🤖 Для работы меню нужен Бот-помощник.")
            print("1. Ввести токен вручную")
            print("2. Создать автоматически")
            
            while True:
                choice = input("Ваш выбор (1/2): ").strip()
                if choice == "1":
                    bot_token = input("Введите токен бота: ").strip()
                    break
                elif choice == "2":
                    bot_token = await auto_create_bot(user_client) 
                    if bot_token:
                        break
                    else:
                        print("⚠️ Не удалось создать бота. Введите токен вручную.")
                else:
                    print("Введите 1 или 2.")
            
            if bot_token:
                config['telethon']['bot_token'] = bot_token
                with open(config_file, 'w', encoding='utf-8') as f:
                    config.write(f)

        if bot_token:
            print(f"🚀 Проверка запуска бота...")
            try:
                bot_client = TelegramClient(None, api_id, api_hash)
                await bot_client.start(bot_token=bot_token)
                print("✅ Бот успешно запущен!")
                break 
            except (AccessTokenInvalidError, AccessTokenExpiredError):
                print(f"❌ ОШИБКА: Токен бота невалиден или устарел!")
                print("🗑 Удаляю старый токен, давайте создадим нового.")
                config.remove_option('telethon', 'bot_token')
                with open(config_file, 'w', encoding='utf-8') as f:
                    config.write(f)
                bot_token = None 
            except Exception as e:
                print(f"⚠️ Ошибка при запуске бота: {e}")
                print("Попробуем настроить заново...")
                config.remove_option('telethon', 'bot_token')
                with open(config_file, 'w', encoding='utf-8') as f:
                    config.write(f)
                bot_token = None

    db.init_db()
    if db.get_setting("debug_mode") == "True":
        logging.getLogger().setLevel(logging.DEBUG)
    
    loader.PREFIX = db.get_setting("prefix", default=".")
    print(f"ℹ️ Префикс команд: {loader.PREFIX}")

    user_client.bot_client = bot_client
    if bot_client: 
        bot_client.user_client = user_client
        try:
            bot_info = await bot_client.get_me()
            
            await asyncio.sleep(1) 
            await ensure_inline_mode_enabled(user_client, bot_info.username)
            
            # Отправка start самому себе, чтобы бот появился в диалогах
            await user_client.send_message(bot_info.username, "/start")
        except Exception as e:
             print(f"⚠️ Небольшая ошибка при инициализации диалога с ботом: {e}")

    panel_pattern = re.compile(f"^{re.escape(loader.PREFIX)}(panel|settings)(?:\\s+(.*))?", re.IGNORECASE)
    user_client.add_event_handler(user_panel_helper, events.NewMessage(pattern=panel_pattern, outgoing=True))
    user_client.add_event_handler(all_messages_handler)

    if bot_client:
        bot_client.add_event_handler(inline_query_handler, events.InlineQuery)
        bot_client.add_event_handler(callback_query_handler, events.CallbackQuery)

    me = await user_client.get_me()
    if db.get_user_level(me.id) != "OWNER":
        db.add_user(me.id, "OWNER")
        print(f"👑 Права владельца выданы: {me.first_name} (ID: {me.id})")

    return user_client, bot_client

async def main():
    user_client, bot_client = await start_clients()
    if not user_client: return
        
    worker_task = asyncio.create_task(command_worker(user_client))
    
    print("👥 Запускаю твинков...")
    try:
        twins_count = await twin_manager.start_all_twins()
        print(f"✅ Запущено твинков: {twins_count}")
    except Exception as e:
        print(f"⚠️ Ошибка при запуске твинков: {e}")

    print("\n🟢 KoteLoader полностью запущен! Напишите .help в чате.")
    
    try:
        # Добавляем heartbeat в список задач
        tasks = [worker_task, user_client.run_until_disconnected(), heartbeat()]
        if bot_client: 
            tasks.append(bot_client.run_until_disconnected())
        await asyncio.gather(*tasks)
    finally:
        print("\nЗавершение работы...")
        db.close_db()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nБот остановлен вручную.")
