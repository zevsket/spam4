import asyncio
import random
import json
from datetime import datetime
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.types import (
    PeerChannel, PeerChat, PeerUser,
    InputPeerChannel, InputPeerChat, InputPeerUser,
    DialogFilter
)
from telethon.tl.functions.messages import GetDialogFiltersRequest
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import API_ID, API_HASH, BOT_TOKEN
from database import init_db, User, Account, SpamTask

# Инициализация бота (Telethon)
bot = TelegramClient('bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# Хранилище активных задач и сессий пользователей
active_tasks = {}
user_sessions = {}
user_folders_cache = {}  # Кэш папок пользователя {user_id: [folders]}

# ========== БАЗОВЫЕ ФУНКЦИИ ==========

async def get_or_create_user(user_id: int):
    async with await init_db() as session:
        result = await session.execute(select(User).where(User.telegram_id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            user = User(telegram_id=user_id)
            session.add(user)
            await session.commit()
        return user

async def save_session(user_id: int, phone: str, session_string: str):
    async with await init_db() as session:
        account = Account(
            user_id=user_id,
            phone=phone,
            session_string=session_string
        )
        session.add(account)
        await session.commit()
        return account

async def get_user_accounts(user_id: int):
    async with await init_db() as session:
        result = await session.execute(select(Account).where(Account.user_id == user_id))
        return result.scalars().all()

async def get_account_by_id(account_id: int):
    async with await init_db() as session:
        result = await session.execute(select(Account).where(Account.id == account_id))
        return result.scalar_one_or_none()

# ========== ПОЛУЧЕНИЕ ПАПОК ЧЕРЕЗ TELETHON ==========

async def get_folders_with_chats(client: TelegramClient):
    """Получает все папки и чаты в них"""
    try:
        # Получаем все папки
        result = await client(GetDialogFiltersRequest())
        folders = []
        
        for i, folder in enumerate(result):
            if not hasattr(folder, 'title') or not folder.title:
                continue
                
            # Получаем чаты в папке
            chats_in_folder = []
            for peer in folder.include_peers:
                try:
                    # Получаем сущность чата
                    chat = await client.get_entity(peer)
                    chats_in_folder.append({
                        'id': chat.id,
                        'title': getattr(chat, 'title', None) or f"{getattr(chat, 'first_name', '')} {getattr(chat, 'last_name', '')}".strip(),
                        'username': getattr(chat, 'username', None),
                        'entity': chat
                    })
                except Exception as e:
                    continue
            
            folders.append({
                'index': i,
                'title': folder.title,
                'chats': chats_in_folder,
                'count': len(chats_in_folder)
            })
        
        return folders, None
    except Exception as e:
        return None, str(e)

# ========== КОМАНДЫ БОТА ==========

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.reply(
        "🔥 **Spam Bot v4.0**\n\n"
        "Команды:\n"
        "/add_account - добавить аккаунт\n"
        "/my_accounts - список аккаунтов\n"
        "/select_account - выбрать аккаунт\n"
        "/list_folders - показать папки на аккаунте\n"
        "/select_folder - выбрать папку для спама\n"
        "/set_spam - настроить рассылку\n"
        "/spam_start - запустить\n"
        "/spam_stop - остановить\n"
        "/status - прогресс\n\n"
        "⚡ Работает 24/7"
    )

@bot.on(events.NewMessage(pattern='/add_account'))
async def add_account_handler(event):
    user_id = event.sender_id
    await get_or_create_user(user_id)
    
    await event.reply("📱 Отправь номер телефона (в формате +71234567890):")
    user_sessions[user_id] = {"state": "waiting_phone"}

@bot.on(events.NewMessage(pattern='/my_accounts'))
async def my_accounts_handler(event):
    user_id = event.sender_id
    accounts = await get_user_accounts(user_id)
    
    if not accounts:
        await event.reply("❌ Нет аккаунтов")
        return
    
    text = "📋 **Аккаунты:**\n"
    for acc in accounts:
        status = "✅" if acc.is_active else "❌"
        text += f"\n{status} {acc.phone}"
    
    await event.reply(text)

@bot.on(events.NewMessage(pattern='/select_account'))
async def select_account_handler(event):
    user_id = event.sender_id
    accounts = await get_user_accounts(user_id)
    
    if not accounts:
        await event.reply("❌ Сначала добавь аккаунт")
        return
    
    buttons = []
    for acc in accounts:
        buttons.append([Button.inline(f"{acc.phone}", data=f"select_acc_{acc.id}")])
    
    await event.reply("🔢 **Выбери аккаунт:**", buttons=buttons)

@bot.on(events.NewMessage(pattern='/list_folders'))
async def list_folders_handler(event):
    user_id = event.sender_id
    
    # Получаем выбранный аккаунт
    account_id = user_sessions.get(user_id, {}).get("account_id")
    if not account_id:
        await event.reply("❌ Сначала выбери аккаунт через /select_account")
        return
    
    account = await get_account_by_id(account_id)
    if not account:
        await event.reply("❌ Аккаунт не найден")
        return
    
    await event.reply("⏳ Получаю папки...")
    
    try:
        # Создаем клиент для аккаунта
        client = TelegramClient(StringSession(account.session_string), API_ID, API_HASH)
        await client.start()
        
        # Получаем папки
        folders, error = await get_folders_with_chats(client)
        
        if error:
            await event.reply(f"❌ Ошибка: {error}")
            await client.disconnect()
            return
        
        if not folders:
            await event.reply("❌ Папки не найдены")
            await client.disconnect()
            return
        
        # Сохраняем в кэш
        user_folders_cache[user_id] = folders
        
        # Показываем папки
        text = "📁 **Найденные папки:**\n\n"
        for i, folder in enumerate(folders[:10]):  # Показываем первые 10
            preview = ", ".join([c['title'][:20] for c in folder['chats'][:3]])
            text += f"{i+1}. **{folder['title']}** - {folder['count']} чатов\n"
            if preview:
                text += f"   *{preview}...*\n\n"
        
        text += "\nИспользуй /select_folder [номер] для выбора"
        await event.reply(text)
        
        await client.disconnect()
        
    except Exception as e:
        await event.reply(f"❌ Ошибка: {str(e)}")

@bot.on(events.NewMessage(pattern='/select_folder'))
async def select_folder_handler(event):
    user_id = event.sender_id
    
    if user_id not in user_folders_cache:
        await event.reply("❌ Сначала выполни /list_folders")
        return
    
    parts = event.message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await event.reply("❌ Укажи номер папки: /select_folder 1")
        return
    
    folder_index = int(parts[1]) - 1
    folders = user_folders_cache[user_id]
    
    if folder_index < 0 or folder_index >= len(folders):
        await event.reply("❌ Неправильный номер")
        return
    
    selected_folder = folders[folder_index]
    
    # Сохраняем выбранную папку
    if user_id not in user_sessions:
        user_sessions[user_id] = {}
    
    user_sessions[user_id]['folder'] = {
        'title': selected_folder['title'],
        'chats': selected_folder['chats']
    }
    
    await event.reply(
        f"✅ Выбрана папка **{selected_folder['title']}**\n"
        f"Чатов в папке: {selected_folder['count']}\n\n"
        f"Теперь используй /set_spam для настройки рассылки"
    )

@bot.on(events.NewMessage(pattern='/set_spam'))
async def set_spam_handler(event):
    user_id = event.sender_id
    
    if user_id not in user_sessions or 'folder' not in user_sessions[user_id]:
        await event.reply("❌ Сначала выбери папку через /select_folder")
        return
    
    # Запрашиваем сообщение и задержку
    user_sessions[user_id]['state'] = 'waiting_spam_settings'
    await event.reply(
        "📝 Отправь настройки рассылки в формате:\n\n"
        "**Сообщение**\n\n"
        "Задержка (сек)\n\n"
        "Пример:\n"
        "Привет! Это сообщение для всех чатов\n\n"
        "5-15"
    )

@bot.on(events.NewMessage(pattern='/spam_start'))
async def spam_start_handler(event):
    user_id = event.sender_id
    
    # Проверяем есть ли задача
    async with await init_db() as session:
        result = await session.execute(
            select(SpamTask).where(
                SpamTask.user_id == user_id,
                SpamTask.is_running == True
            )
        )
        task = result.scalar_one_or_none()
    
    if not task:
        await event.reply("❌ Нет активной задачи. Сначала настрой через /set_spam")
        return
    
    if user_id in active_tasks and active_tasks[user_id]:
        await event.reply("⚠️ Рассылка уже запущена")
        return
    
    await event.reply("🚀 Запускаю рассылку...")
    active_tasks[user_id] = asyncio.create_task(run_spam_task(user_id, task.id))

@bot.on(events.NewMessage(pattern='/spam_stop'))
async def spam_stop_handler(event):
    user_id = event.sender_id
    
    if user_id in active_tasks and active_tasks[user_id]:
        active_tasks[user_id].cancel()
        del active_tasks[user_id]
        await event.reply("⏹ Рассылка остановлена")
    else:
        await event.reply("❌ Нет активной рассылки")

@bot.on(events.NewMessage(pattern='/status'))
async def status_handler(event):
    user_id = event.sender_id
    
    async with await init_db() as session:
        result = await session.execute(
            select(SpamTask).where(
                SpamTask.user_id == user_id,
                SpamTask.is_running == True
            )
        )
        task = result.scalar_one_or_none()
    
    if task:
        links = json.loads(task.links)
        await event.reply(
            f"📊 **Статус:**\n"
            f"Отправлено: {task.total_sent}/{len(links)}\n"
            f"Задержка: {task.delay_min}-{task.delay_max} сек"
        )
    else:
        await event.reply("❌ Нет активной рассылки")

# ========== ОБРАБОТКА СООБЩЕНИЙ ==========

@bot.on(events.NewMessage)
async def handle_messages(event):
    user_id = event.sender_id
    
    if user_id not in user_sessions:
        return
    
    state = user_sessions[user_id].get("state")
    
    # === ДОБАВЛЕНИЕ АККАУНТА ===
    if state == "waiting_phone":
        phone = event.message.text.strip()
        
        client = TelegramClient(f'session_{user_id}', API_ID, API_HASH)
        await client.connect()
        
        try:
            await event.reply("⏳ Отправляю код...")
            sent = await client.send_code_request(phone)
            
            user_sessions[user_id].update({
                "state": "waiting_code",
                "phone": phone,
                "client": client,
                "phone_code_hash": sent.phone_code_hash
            })
            
            await event.reply("🔐 Введи код из Telegram:")
            
        except Exception as e:
            await event.reply(f"❌ Ошибка: {str(e)}")
            del user_sessions[user_id]
    
    elif state == "waiting_code":
        code = event.message.text.strip()
        
        client = user_sessions[user_id]["client"]
        phone = user_sessions[user_id]["phone"]
        phone_code_hash = user_sessions[user_id]["phone_code_hash"]
        
        try:
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            
            session_string = client.session.save()
            await save_session(user_id, phone, session_string)
            
            await event.reply("✅ Аккаунт добавлен!")
            await client.disconnect()
            del user_sessions[user_id]
            
        except SessionPasswordNeededError:
            user_sessions[user_id]["state"] = "waiting_2fa"
            await event.reply("🔐 Введи пароль 2FA:")
            
        except Exception as e:
            await event.reply(f"❌ Ошибка: {str(e)}")
            del user_sessions[user_id]
    
    elif state == "waiting_2fa":
        password = event.message.text.strip()
        
        client = user_sessions[user_id]["client"]
        phone = user_sessions[user_id]["phone"]
        
        try:
            await client.sign_in(password=password)
            
            session_string = client.session.save()
            await save_session(user_id, phone, session_string)
            
            await event.reply("✅ Аккаунт добавлен!")
            await client.disconnect()
            del user_sessions[user_id]
            
        except Exception as e:
            await event.reply(f"❌ Неправильный пароль: {str(e)}")
    
    # === НАСТРОЙКА СПАМА ===
    elif state == "waiting_spam_settings":
        text = event.message.text.strip()
        parts = text.split("\n\n")
        
        if len(parts) < 2:
            await event.reply("❌ Нужно: сообщение, пустая строка, задержка")
            return
        
        message_text = parts[0].strip()
        delay_part = parts[1].strip()
        
        # Парсим задержку
        if "-" in delay_part:
            min_delay, max_delay = map(int, delay_part.split("-"))
        else:
            min_delay = max_delay = int(delay_part)
        
        # Получаем чаты из выбранной папки
        folder = user_sessions[user_id]['folder']
        account_id = user_sessions[user_id].get("account_id")
        
        # Формируем ссылки
        links = []
        for chat in folder['chats']:
            if chat['username']:
                links.append(f"@{chat['username']}")
            else:
                links.append(f"private:{chat['id']}")
        
        # Сохраняем задачу
        async with await init_db() as session:
            task = SpamTask(
                user_id=user_id,
                account_id=account_id,
                links=json.dumps(links),
                message=message_text,
                delay_min=min_delay,
                delay_max=max_delay,
                is_running=False
            )
            session.add(task)
            await session.commit()
        
        await event.reply(
            f"✅ **Задача создана!**\n\n"
            f"Папка: {folder['title']}\n"
            f"Чатов: {len(links)}\n"
            f"Задержка: {min_delay}-{max_delay} сек\n\n"
            f"Запусти: /spam_start"
        )
        
        del user_sessions[user_id]['state']

# ========== ОБРАБОТКА INLINE КНОПОК ==========

@bot.on(events.CallbackQuery)
async def callback_handler(event):
    user_id = event.sender_id
    data = event.data.decode()
    
    if data.startswith("select_acc_"):
        account_id = int(data.replace("select_acc_", ""))
        
        if user_id not in user_sessions:
            user_sessions[user_id] = {}
        
        user_sessions[user_id]['account_id'] = account_id
        
        await event.edit(f"✅ Аккаунт выбран!")
        await event.respond("Теперь используй /list_folders для просмотра папок")

# ========== ЗАПУСК РАССЫЛКИ ==========

async def run_spam_task(user_id: int, task_id: int):
    try:
        async with await init_db() as session:
            result = await session.execute(select(SpamTask).where(SpamTask.id == task_id))
            task = result.scalar_one_or_none()
            
            acc_result = await session.execute(select(Account).where(Account.id == task.account_id))
            account = acc_result.scalar_one_or_none()
            
            task.is_running = True
            await session.commit()
        
        # Подключаемся к аккаунту
        client = TelegramClient(StringSession(account.session_string), API_ID, API_HASH)
        await client.start()
        
        links = json.loads(task.links)
        total = len(links)
        sent = task.total_sent
        
        await bot.send_message(user_id, f"▶️ Начинаю рассылку. Всего: {total}")
        
        for i, link in enumerate(links[sent:], start=sent+1):
            if user_id not in active_tasks:
                break
            
            try:
                # Получаем чат
                if link.startswith("private:"):
                    chat_id = int(link.replace("private:", ""))
                    chat = await client.get_entity(chat_id)
                else:
                    chat = await client.get_entity(link)
                
                # Отправляем
                await client.send_message(chat, task.message)
                
                sent += 1
                async with await init_db() as session:
                    await session.execute(
                        SpamTask.__table__.update()
                        .where(SpamTask.id == task_id)
                        .values(total_sent=sent)
                    )
                    await session.commit()
                
                await bot.send_message(
                    user_id,
                    f"✅ [{i}/{total}] Отправлено в {getattr(chat, 'title', chat.first_name)}"
                )
                
                delay = random.randint(task.delay_min, task.delay_max)
                await asyncio.sleep(delay)
                
            except FloodWaitError as e:
                await bot.send_message(user_id, f"⚠️ Flood wait {e.seconds} сек")
                await asyncio.sleep(e.seconds)
            except Exception as e:
                await bot.send_message(user_id, f"❌ Ошибка: {str(e)}")
                continue
        
        await client.disconnect()
        
        async with await init_db() as session:
            await session.execute(
                SpamTask.__table__.update()
                .where(SpamTask.id == task_id)
                .values(is_running=False)
            )
            await session.commit()
        
        await bot.send_message(user_id, f"🏁 Рассылка завершена! Отправлено: {sent}/{total}")
        
    except asyncio.CancelledError:
        async with await init_db() as session:
            await session.execute(
                SpamTask.__table__.update()
                .where(SpamTask.id == task_id)
                .values(is_running=False)
            )
            await session.commit()
        await bot.send_message(user_id, "⏹ Рассылка остановлена")
    except Exception as e:
        await bot.send_message(user_id, f"💥 Ошибка: {str(e)}")
    finally:
        if user_id in active_tasks:
            del active_tasks[user_id]

# ========== ЗАПУСК ==========

if __name__ == "__main__":
    print("🔥 Spam Bot v4.0 запущен...")
    bot.run_until_disconnected()
