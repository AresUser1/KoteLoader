# services/twin_manager.py
import asyncio
import json
import random
from pathlib import Path
from telethon import TelegramClient
from telethon.sessions import StringSession
from configparser import ConfigParser

TWINS_FILE = Path(__file__).parent.parent / "twins.json"
CONFIG_FILE = Path(__file__).parent.parent / "config.ini"

def generate_device_info():
    """Генерирует реалистичные данные устройства."""
    devices = [
        ("Android 13", "Samsung SM-S908B", "10.5.0"),
        ("Android 14", "Google Pixel 7 Pro", "10.6.1"),
        ("iOS 16.6.1", "iPhone 14 Pro Max", "10.0.1"),
        ("Windows 10", "PC 64bit", "4.15.2"),
        ("macOS 14.2.1", "MacBook Pro", "10.3.1"),
        ("Android 12", "Xiaomi 12 Pro", "10.1.2")
    ]
    return random.choice(devices)

class TwinManager:
    def __init__(self):
        self.clients = {}
        self.global_api_id = None
        self.global_api_hash = None
        self._load_config()

    def _load_config(self):
        """Загружает глобальные API ID/Hash из конфига."""
        if not CONFIG_FILE.exists():
            return

        config = ConfigParser()
        try:
            config.read(CONFIG_FILE, encoding='utf-8')
            if config.has_section("telethon"):
                self.global_api_id = config.getint("telethon", "api_id")
                self.global_api_hash = config.get("telethon", "api_hash")
        except Exception:
            pass

    def get_stored_twins(self) -> dict:
        """Возвращает словарь твинков. Автоматически мигрирует старый формат."""
        if not TWINS_FILE.exists():
            return {}
        try:
            with open(TWINS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            # Миграция: если формат старый {"name": "session_str"}, переделываем в структуру
            migrated_data = {}
            needs_save = False
            
            for name, value in data.items():
                if isinstance(value, str):
                    migrated_data[name] = {"session": value}
                    needs_save = True
                else:
                    migrated_data[name] = value
            
            if needs_save:
                with open(TWINS_FILE, "w", encoding="utf-8") as f:
                    json.dump(migrated_data, f, indent=4)
                    
            return migrated_data
        except Exception:
            return {}

    def save_twin(self, name: str, session_str: str, api_id: int = None, api_hash: str = None):
        """Сохраняет твинка. Если переданы api_id/hash — это Twin+."""
        data = self.get_stored_twins()
        
        entry = {"session": session_str}
        if api_id and api_hash:
            entry["api_id"] = api_id
            entry["api_hash"] = api_hash
            
        data[name] = entry
        
        with open(TWINS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def remove_twin_data(self, name: str):
        data = self.get_stored_twins()
        if name in data:
            del data[name]
            with open(TWINS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

    async def start_twin(self, name: str):
        # Если уже запущен, возвращаем объект
        if name in self.clients and self.clients[name].is_connected():
            return self.clients[name]

        stored = self.get_stored_twins()
        twin_data = stored.get(name)
        
        if not twin_data:
            raise ValueError(f"Твинк {name} не найден.")

        # Получаем данные сессии
        session_str = twin_data.get("session")
        
        # Логика выбора API ID: Личный > Глобальный
        t_api_id = twin_data.get("api_id") or self.global_api_id
        t_api_hash = twin_data.get("api_hash") or self.global_api_hash

        # Проверка/генерация данных устройства для твинка
        sys_ver = twin_data.get("system_version")
        model = twin_data.get("device_model")
        app_ver = twin_data.get("app_version")

        if not all([sys_ver, model, app_ver]):
            sys_ver, model, app_ver = generate_device_info()
            twin_data["system_version"] = sys_ver
            twin_data["device_model"] = model
            twin_data["app_version"] = app_ver
            
            # Сохраняем обновленные данные твинка
            all_twins = self.get_stored_twins()
            all_twins[name] = twin_data
            with open(TWINS_FILE, "w", encoding="utf-8") as f:
                json.dump(all_twins, f, indent=4)

        if not t_api_id or not t_api_hash:
            # Если глобальные не загрузились сразу, пробуем перечитать
            self._load_config()
            t_api_id = t_api_id or self.global_api_id
            t_api_hash = t_api_hash or self.global_api_hash
            
            if not t_api_id:
                raise ValueError("API ID/Hash не найдены ни в твинке, ни в config.ini")

        try:
            client = TelegramClient(
                StringSession(session_str), 
                t_api_id, 
                t_api_hash,
                system_version=sys_ver,
                device_model=model,
                app_version=app_ver,
                lang_code="ru",
                system_lang_code="ru-RU"
            )
            await client.connect()
            
            if not await client.is_user_authorized():
                # Если сессия умерла
                raise Exception("Session revoked")

            self.clients[name] = client
            return client
        except Exception as e:
            # Если ошибка при старте, удаляем из памяти (но не из файла, вдруг временный сбой)
            if name in self.clients:
                del self.clients[name]
            raise e

    async def stop_twin(self, name: str):
        if name in self.clients:
            await self.clients[name].disconnect()
            del self.clients[name]

    async def start_all_twins(self):
        stored = self.get_stored_twins()
        if not stored: return 0
            
        count = 0
        for name in stored.keys():
            try:
                await self.start_twin(name)
                count += 1
            except Exception as e:
                print(f"⚠️ Не удалось запустить твинка {name}: {e}")
        return count

    def get_client(self, name: str):
        return self.clients.get(name)

twin_manager = TwinManager()