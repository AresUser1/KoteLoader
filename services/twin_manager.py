# services/twin_manager.py
import asyncio
import json
from pathlib import Path
from telethon import TelegramClient
from telethon.sessions import StringSession
from configparser import ConfigParser

# Файл для хранения данных твинков (имя: session_string)
TWINS_FILE = Path(__file__).parent.parent / "twins.json"
CONFIG_FILE = Path(__file__).parent.parent / "config.ini"

class TwinManager:
    def __init__(self):
        self.clients = {}  # {name: TelegramClient}
        self.api_id = None
        self.api_hash = None
        self._load_config()

    def _load_config(self):
        """Загружает API ID/Hash из основного конфига."""
        config = ConfigParser()
        if CONFIG_FILE.exists():
            config.read(CONFIG_FILE, encoding='utf-8')
            self.api_id = config.getint("telethon", "api_id")
            self.api_hash = config.get("telethon", "api_hash")

    def get_stored_twins(self) -> dict:
        """Возвращает словарь сохраненных сессий."""
        if not TWINS_FILE.exists():
            return {}
        try:
            with open(TWINS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_twin(self, name: str, session_str: str):
        """Сохраняет новую сессию."""
        data = self.get_stored_twins()
        data[name] = session_str
        with open(TWINS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def remove_twin_data(self, name: str):
        """Удаляет твинка из файла."""
        data = self.get_stored_twins()
        if name in data:
            del data[name]
            with open(TWINS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

    async def start_twin(self, name: str, session_str: str = None):
        """Запускает клиента твинка."""
        if name in self.clients and self.clients[name].is_connected():
            return self.clients[name]

        if not session_str:
            stored = self.get_stored_twins()
            session_str = stored.get(name)
        
        if not session_str:
            raise ValueError(f"Твинк {name} не найден.")

        client = TelegramClient(StringSession(session_str), self.api_id, self.api_hash)
        await client.connect()
        
        if not await client.is_user_authorized():
            raise AuthError("Сессия невалидна")

        self.clients[name] = client
        return client

    async def stop_twin(self, name: str):
        """Останавливает и удаляет клиента из памяти."""
        if name in self.clients:
            await self.clients[name].disconnect()
            del self.clients[name]

    async def start_all_twins(self):
        """Запускает всех сохраненных твинков при старте бота."""
        stored = self.get_stored_twins()
        count = 0
        for name, session in stored.items():
            try:
                await self.start_twin(name, session)
                count += 1
            except Exception as e:
                print(f"⚠️ Не удалось запустить твинка {name}: {e}")
        return count

    def get_client(self, name: str):
        """Получить объект клиента для использования в модулях."""
        return self.clients.get(name)

# Глобальный экземпляр
twin_manager = TwinManager()