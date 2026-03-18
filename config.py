import os

__all__ = ['API_ID', 'API_HASH', 'BOT_TOKEN', 'DATABASE_URL', 'MIN_DELAY', 'MAX_DELAY']

# Конфигурация бота
API_ID = int(os.environ.get("API_ID", 32510266))
API_HASH = os.environ.get("API_HASH", "b65af61f1c3e54d29b5b555fd996e5cb")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8611210220:AATtWNL75G5FVyHFHsb_5TWizbXTTGwFD6Q")

# База данных
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///spam_bot.db")

# Настройки рассылки
MIN_DELAY = int(os.environ.get("MIN_DELAY", "5"))
MAX_DELAY = int(os.environ.get("MAX_DELAY", "15"))
