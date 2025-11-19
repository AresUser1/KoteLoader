# services/twin_manager.py
import asyncio
import json
from pathlib import Path
from telethon import TelegramClient
from telethon.sessions import StringSession
from configparser import ConfigParser

TWINS_FILE = Path(__file__).parent.parent / "twins.json"
CONFIG_FILE = Path(__file__).parent.parent / "config.ini"

class TwinManager:
    def __init__(self):
        self.clients = {}
        self.api_id = None
        self.api_hash = None
        self._load_config()

    def _load_config(self):
        if not CONFIG_FILE.exists():
            return

        config = ConfigParser()
        try:
            config.read(CONFIG_FILE, encoding='utf-8')
            if config.has_section("telethon"):
                self.api_id = config.getint("telethon", "api_id")
                self.api_hash = config.get("telethon", "api_hash")
        except Exception:
            pass

    def get_stored_twins(self) -> dict:
        if not TWINS_FILE.exists():
            return {}
        try:
            with open(TWINS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_twin(self, name: str, session_str: str):
        data = self.get_stored_twins()
        data[name] = session_str
        with open(TWINS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def remove_twin_data(self, name: str):
        data = self.get_stored_twins()
        if name in data:
            del data[name]
            with open(TWINS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

    async def start_twin(self, name: str, session_str: str = None):
        if not self.api_id or not self.api_hash:
            self._load_config()
            if not self.api_id:
                raise ValueError("API ID/Hash не найдены в config.ini")

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
            raise Exception("Сессия невалидна")

        self.clients[name] = client
        return client

    async def stop_twin(self, name: str):
        if name in self.clients:
            await self.clients[name].disconnect()
            del self.clients[name]

    async def start_all_twins(self):
        stored = self.get_stored_twins()
        if not stored:
            return 0
            
        if not self.api_id:
            self._load_config()
            
        count = 0
        for name, session in stored.items():
            try:
                await self.start_twin(name, session)
                count += 1
            except Exception as e:
                print(f"⚠️ Не удалось запустить твинка {name}: {e}")
        return count

    def get_client(self, name: str):
        return self.clients.get(name)

twin_manager = TwinManager()