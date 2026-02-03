# main.py
import asyncio
import logging
import re
import time
import os
import uuid
import random
from configparser import ConfigParser
from telethon import TelegramClient, events, errors
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

def generate_device_info():
    """Generates realistic device info based on official Telegram client patterns."""
    devices = [
        ("Android 13", "Samsung Galaxy S23 Ultra", "10.3.2"),
        ("Android 14", "Google Pixel 8 Pro", "10.5.0"),
        ("iOS 17.2", "iPhone 15 Pro Max", "10.4.1"),
        ("Windows 11", "Desktop PC", "4.11.8 x64"),
        ("macOS 14.1", "MacBook Air M2", "10.3.1"),
        ("Android 12", "Xiaomi 13 Pro", "10.0.1")
    ]
    sys_ver, model, app_ver = random.choice(devices)
    return sys_ver, model, f"{app_ver} KoteLoader"

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

async def ensure_folder_added(client):
    """Проверяет и добавляет папку каналов KoteLoader, если её нет."""
    try:
        from telethon import functions
        from telethon.tl.types.chatlists import ChatlistInviteAlready
        
        slug = "eNIT7MB1ledlNTVi"
        
        # Проверяем инвайт-ссылку папки
        invite = await client(functions.chatlists.CheckChatlistInviteRequest(slug=slug))
        
        # Если папка уже добавлена и обновлений нет
        if isinstance(invite, ChatlistInviteAlready):
            return
            
        # Если это новый инвайт или есть новые пиры (каналы)
        if hasattr(invite, 'peers'):
            print(f"\n📂 Обнаружена папка с обновлениями модулей. Добавляю...")
            
            from telethon import utils
            
            # Собираем все сущности (чаты/юзеры) из инвайта, чтобы достать access_hash
            all_entities = {e.id: e for e in getattr(invite, 'chats', []) + getattr(invite, 'users', [])}
            
            input_peers = []
            for peer in invite.peers:
                # Извлекаем "чистый" ID (без -100 префикса), так как ключи в all_entities - это чистые ID
                bare_id = getattr(peer, 'user_id', None) or \
                          getattr(peer, 'channel_id', None) or \
                          getattr(peer, 'chat_id', None)
                          
                if bare_id and bare_id in all_entities:
                    input_peers.append(utils.get_input_peer(all_entities[bare_id]))
            
            if input_peers:
                await client(functions.chatlists.JoinChatlistInviteRequest(
                    slug=slug,
                    peers=input_peers
                ))
                print("✅ Папка успешно добавлена в ваш список чатов!")
            else:
                print(f"⚠️ Не удалось найти доступные чаты в папке. (Peers: {len(invite.peers)}, Entities: {len(all_entities)})")
            
    except Exception as e:
        # Если папка уже есть, Telegram может выкинуть ошибку, просто игнорируем
        if "CHATLIST_ALREADY_JOINED" not in str(e):
            print(f"⚠️ Ошибка при добавлении папки: {e}")
            pass

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

    # --- ❗️ FIX: Загружаем или генерируем (и сохраняем) данные устройства ---
    # Если менять устройство при каждом запуске, Telegram даст бан (FloodWait).
    
    if not config.has_option("telethon", "system_version"):
        print("🛠 Генерация и сохранение постоянных данных устройства...")
        gen_sys, gen_model, gen_app = generate_device_info()
        
        config.set("telethon", "system_version", gen_sys)
        config.set("telethon", "device_model", gen_model)
        config.set("telethon", "app_version", gen_app)
        
        with open(config_file, 'w', encoding='utf-8') as f:
            config.write(f)
            
    system_version = config.get("telethon", "system_version")
    device_model = config.get("telethon", "device_model")
    app_version = config.get("telethon", "app_version")
    # -----------------------------------------------------------------------

    print(f"\n🚀 Подключение к аккаунту ({session_name})...")
    print(f"📱 Устройство: {device_model} ({system_version})") # Для инфо
    
    user_client = TelegramClient(
        session_name, 
        api_id, 
        api_hash,
        system_version=system_version,
        device_model=device_model,
        app_version=app_version,
        lang_code="en",
        system_lang_code="en-US"
    )
    
    await user_client.connect()
    if not await user_client.is_user_authorized():
        session_file = f"{session_name}.session"
        if os.path.exists(config_file) or os.path.exists("database.db"):
            print(f"\n⚠️ Сессия '{session_name}' не авторизована (возможно, слетела).")
            print("1. Попробовать войти заново (сохранить текущие данные и настройки)")
            print("2. Перезаписать всё (удалить конфигурацию, базу данных и начать с нуля)")
            
            while True:
                choice = input("Ваш выбор (1/2): ").strip()
                if choice == "1":
                    break
                elif choice == "2":
                    print("🗑 Удаление старых данных...")
                    await user_client.disconnect()
                    for file in [config_file, session_file, "database.db", "database.db-shm", "database.db-wal"]:
                        if os.path.exists(file):
                            try: os.remove(file)
                            except: pass
                    print("✅ Данные очищены. Пожалуйста, запустите бота снова для чистой настройки.")
                    exit()
                else:
                    print("Введите 1 или 2.")
        
        # --- РУЧНОЙ ВХОД (Manual Flow) ---
        phone_number = input("\n📱 Введите номер телефона (например +79001234567): ")
        try:
            sent_code = await user_client.send_code_request(phone_number)
            print(f"✅ Код успешно отправлен в Telegram на номер {phone_number}")
            
            code = input("💬 Введите код подтверждения из Telegram: ")
            try:
                await user_client.sign_in(phone_number, code, password=None)
            except errors.SessionPasswordNeededError:
                # Ввод пароля (сделан видимым по запросу)
                password = input("🔐 Аккаунт защищен облачным паролем.\nВведите пароль (будет виден): ")
                await user_client.sign_in(password=password)
                
        except errors.PhonePasswordFloodError:
            print("\n❌ \033[91mTelegram временно заблокировал вход для этого номера из-за частых попыток.\033[0m")
            print("⏳ Пожалуйста, подождите от 30 минут до 24 часов перед следующей попыткой.")
            exit()
        except Exception as e:
            print(f"\n❌ Ошибка при входе: {e}")
            exit()
    else:
        await user_client.start()

    print("✅ Успешный вход в аккаунт!")
    await ensure_folder_added(user_client)

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

    print("\n🟢 KoteLoader полностью запущен! Напишите help в чате.")
    
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
