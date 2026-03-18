import asyncio
import random
import json
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import (
    PeerIdInvalid, UsernameInvalid, ChatAdminRequired,
    UserAlreadyParticipant, InviteHashExpired, FloodWait,
    SessionPasswordNeeded
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import API_ID, API_HASH, BOT_TOKEN, MIN_DELAY, MAX_DELAY
from database import init_db, User, Account, SpamTask

# Инициализация бота
app = Client(
    "spam_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Хранилище активных задач
active_tasks = {}
user_sessions = {}

# ========== БАЗОВЫЕ ФУНКЦИИ ==========

async def get_or_create_user(session: AsyncSession, telegram_id: int):
    """Получить или создать пользователя"""
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        user = User(telegram_id=telegram_id)
        session.add(user)
        await session.commit()
    
    return user

async def save_session(user_id: int, phone: str, session_string: str):
    """Сохранить сессию аккаунта"""
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
    """Получить аккаунты пользователя"""
    async with await init_db() as session:
        result = await session.execute(
            select(Account).where(Account.user_id == user_id)
        )
        return result.scalars().all()

async def get_chats_from_folder(user_client: Client, folder_name: str):
    """Получить все чаты из указанной папки"""
    try:
        # Получаем все диалоги
        dialogs = []
        async for dialog in user_client.get_dialogs():
            dialogs.append(dialog)
        
        # Получаем информацию о папках
        folders = await user_client.get_folders()
        
        # Ищем нужную папку
        target_folder = None
        for folder in folders:
            if folder.title.lower() == folder_name.lower():
                target_folder = folder
                break
        
        if not target_folder:
            return None, f"Папка '{folder_name}' не найдена"
        
        # Собираем чаты из папки
        folder_chats = []
        for peer in target_folder.included_peers:
            try:
                # Получаем информацию о чате
                if hasattr(peer, 'channel_id'):
                    chat = await user_client.get_chat(peer.channel_id)
                elif hasattr(peer, 'chat_id'):
                    chat = await user_client.get_chat(peer.chat_id)
                elif hasattr(peer, 'user_id'):
                    chat = await user_client.get_chat(peer.user_id)
                else:
                    continue
                
                folder_chats.append({
                    'id': chat.id,
                    'title': chat.title or f"{chat.first_name} {chat.last_name or ''}",
                    'username': chat.username
                })
            except Exception as e:
                continue
        
        return folder_chats, None
    except Exception as e:
        return None, str(e)

# ========== КОМАНДЫ БОТА ==========

@app.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    """Приветствие"""
    await message.reply_text(
        "🔥 **Spam Bot v3.0**\n\n"
        "Команды:\n"
        "/add_account - добавить аккаунт для рассылки\n"
        "/my_accounts - список аккаунтов\n"
        "/add_links - добавить ссылки для спама\n"
        "/add_folder - спам по папке Telegram\n"
        "/select_account - выбрать аккаунт\n"
        "/spam_start - запустить рассылку\n"
        "/spam_stop - остановить\n"
        "/status - прогресс\n\n"
        "⚡ Работает 24/7 на Railway"
    )

@app.on_message(filters.command("add_account"))
async def add_account(client: Client, message: Message):
    """Добавление аккаунта через userbot"""
    user_id = message.from_user.id
    
    # Создаем пользователя в БД
    async with await init_db() as session:
        await get_or_create_user(session, user_id)
    
    # Запрашиваем номер телефона
    await message.reply_text(
        "📱 Отправь номер телефона аккаунта (в формате +71234567890):"
    )
    
    # Сохраняем состояние
    user_sessions[user_id] = {"state": "waiting_phone"}

@app.on_message(filters.command("my_accounts"))
async def my_accounts(client: Client, message: Message):
    """Список аккаунтов пользователя"""
    user_id = message.from_user.id
    accounts = await get_user_accounts(user_id)
    
    if not accounts:
        await message.reply_text("❌ У тебя нет добавленных аккаунтов")
        return
    
    text = "📋 **Твои аккаунты:**\n\n"
    for i, acc in enumerate(accounts, 1):
        status = "✅ активен" if acc.is_active else "❌ неактивен"
        text += f"{i}. {acc.phone} - {status}\n"
    
    await message.reply_text(text)

@app.on_message(filters.command("add_links"))
async def add_links(client: Client, message: Message):
    """Добавление ссылок для спама"""
    user_id = message.from_user.id
    
    # Проверяем есть ли аккаунты
    accounts = await get_user_accounts(user_id)
    if not accounts:
        await message.reply_text("❌ Сначала добавь аккаунт через /add_account")
        return
    
    # Запрашиваем ссылки
    await message.reply_text(
        "🔗 Отправь список ссылок на чаты/каналы (каждая с новой строки):\n\n"
        "Пример:\n"
        "https://t.me/chat1\n"
        "https://t.me/+InviteLink\n"
        "@username\n\n"
        "И сообщение для рассылки через пустую строку:"
    )
    
    user_sessions[user_id] = {"state": "waiting_links"}

@app.on_message(filters.command("add_folder"))
async def add_folder(client: Client, message: Message):
    """Добавить папку для спама"""
    user_id = message.from_user.id
    
    # Проверяем есть ли аккаунты
    accounts = await get_user_accounts(user_id)
    if not accounts:
        await message.reply_text("❌ Сначала добавь аккаунт через /add_account")
        return
    
    # Получаем выбранный аккаунт из сессии или берем первый
    account_id = user_sessions.get(user_id, {}).get("account_id")
    if not account_id:
        account_id = accounts[0].id
        await message.reply_text(f"⚠️ Аккаунт не выбран, использую {accounts[0].phone}")
    
    # Запрашиваем название папки
    await message.reply_text(
        "📁 Отправь **название папки** из Telegram (как оно отображается):\n\n"
        "Например: `Работа`, `Друзья`, `Каналы`\n\n"
        "И сообщение для рассылки через пустую строку:"
    )
    
    user_sessions[user_id] = {
        "state": "waiting_folder",
        "account_id": account_id
    }

@app.on_message(filters.command("select_account"))
async def select_account(client: Client, message: Message):
    """Выбор аккаунта для рассылки"""
    user_id = message.from_user.id
    accounts = await get_user_accounts(user_id)
    
    if not accounts:
        await message.reply_text("❌ Сначала добавь аккаунт")
        return
    
    text = "🔢 **Выбери номер аккаунта:**\n\n"
    for i, acc in enumerate(accounts, 1):
        text += f"{i}. {acc.phone}\n"
    text += "\nОтправь /select_account [номер]"
    
    # Парсим номер из команды
    parts = message.text.split()
    if len(parts) > 1 and parts[1].isdigit():
        index = int(parts[1]) - 1
        if 0 <= index < len(accounts):
            selected = accounts[index]
            
            # Сохраняем выбранный аккаунт в сессии
            user_sessions[user_id] = {
                "state": "account_selected",
                "account_id": selected.id
            }
            
            await message.reply_text(f"✅ Выбран аккаунт {selected.phone}")
            return
    
    await message.reply_text(text)

@app.on_message(filters.command("spam_start"))
async def spam_start(client: Client, message: Message):
    """Запуск рассылки"""
    user_id = message.from_user.id
    
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
        await message.reply_text("❌ Нет активной задачи. Сначала добавь ссылки или папку")
        return
    
    if user_id in active_tasks and active_tasks[user_id]:
        await message.reply_text("⚠️ Рассылка уже запущена")
        return
    
    # Запускаем рассылку
    await message.reply_text("🚀 Запускаю рассылку...")
    
    # Создаем задачу
    active_tasks[user_id] = asyncio.create_task(
        run_spam_task(user_id, task.id)
    )

@app.on_message(filters.command("spam_stop"))
async def spam_stop(client: Client, message: Message):
    """Остановка рассылки"""
    user_id = message.from_user.id
    
    if user_id in active_tasks and active_tasks[user_id]:
        active_tasks[user_id].cancel()
        del active_tasks[user_id]
        
        # Обновляем статус в БД
        async with await init_db() as session:
            result = await session.execute(
                select(SpamTask).where(
                    SpamTask.user_id == user_id,
                    SpamTask.is_running == True
                )
            )
            task = result.scalar_one_or_none()
            if task:
                task.is_running = False
                await session.commit()
        
        await message.reply_text("⏹ Рассылка остановлена")
    else:
        await message.reply_text("❌ Нет активной рассылки")

@app.on_message(filters.command("status"))
async def status_command(client: Client, message: Message):
    """Статус рассылки"""
    user_id = message.from_user.id
    
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
        await message.reply_text(
            f"📊 **Статус рассылки:**\n\n"
            f"Отправлено: {task.total_sent}/{len(links)}\n"
            f"Аккаунт ID: {task.account_id}\n"
            f"Задержка: {task.delay_min}-{task.delay_max} сек"
        )
    else:
        await message.reply_text("❌ Нет активной рассылки")

# ========== ОБРАБОТКА СООБЩЕНИЙ ==========

@app.on_message(filters.private & ~filters.command(["start", "add_account", "my_accounts", "add_links", "add_folder", "select_account", "spam_start", "spam_stop", "status"]))
async def handle_messages(client: Client, message: Message):
    """Обработка входящих сообщений"""
    user_id = message.from_user.id
    
    # Проверяем состояние пользователя
    if user_id not in user_sessions:
        return
    
    state = user_sessions[user_id].get("state")
    
    # === ЭТАП 1: ВВОД НОМЕРА ТЕЛЕФОНА ===
    if state == "waiting_phone":
        phone = message.text.strip()
        
        # Создаем клиент для userbot
        user_client = Client(
            f"user_{user_id}_{phone}",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True
        )
        
        try:
            # Отправляем код подтверждения
            await message.reply_text("⏳ Отправляю код подтверждения...")
            
            await user_client.connect()
            sent_code = await user_client.send_code(phone)
            
            # Сохраняем данные
            user_sessions[user_id].update({
                "state": "waiting_code",
                "phone": phone,
                "user_client": user_client,
                "phone_code_hash": sent_code.phone_code_hash
            })
            
            await message.reply_text("🔐 Введи код из Telegram (цифры):")
            
        except Exception as e:
            await message.reply_text(f"❌ Ошибка: {str(e)}")
            del user_sessions[user_id]
    
    # === ЭТАП 2: ВВОД КОДА ПОДТВЕРЖДЕНИЯ ===
    elif state == "waiting_code":
        code = message.text.strip().replace(" ", "")
        
        user_client = user_sessions[user_id]["user_client"]
        phone = user_sessions[user_id]["phone"]
        phone_code_hash = user_sessions[user_id]["phone_code_hash"]
        
        try:
            # Пытаемся войти
            await user_client.sign_in(
                phone_number=phone,
                phone_code_hash=phone_code_hash,
                phone_code=code
            )
            
            # Если успешно - сохраняем сессию
            session_string = await user_client.export_session_string()
            await save_session(user_id, phone, session_string)
            
            await message.reply_text("✅ Аккаунт успешно добавлен!")
            
            await user_client.disconnect()
            del user_sessions[user_id]
            
        except SessionPasswordNeeded:
            # Требуется двухфакторка
            user_sessions[user_id]["state"] = "waiting_2fa"
            await message.reply_text("🔐 На аккаунте включена двухфакторка. Введи пароль:")
            
        except Exception as e:
            await message.reply_text(f"❌ Ошибка: {str(e)}")
            del user_sessions[user_id]
    
    # === ЭТАП 3: ДВУХФАКТОРКА ===
    elif state == "waiting_2fa":
        password = message.text.strip()
        
        user_client = user_sessions[user_id]["user_client"]
        phone = user_sessions[user_id]["phone"]
        phone_code_hash = user_sessions[user_id]["phone_code_hash"]
        
        try:
            # Вход с паролем
            await user_client.sign_in(
                phone_number=phone,
                phone_code_hash=phone_code_hash,
                password=password
            )
            
            session_string = await user_client.export_session_string()
            await save_session(user_id, phone, session_string)
            
            await message.reply_text("✅ Аккаунт успешно добавлен!")
            
            await user_client.disconnect()
            del user_sessions[user_id]
            
        except Exception as e:
            await message.reply_text(f"❌ Неправильный пароль: {str(e)}")
            # Оставляем в состоянии waiting_2fa для повторной попытки
    
    # === ЭТАП 4: ДОБАВЛЕНИЕ ССЫЛОК ===
    elif state == "waiting_links":
        text = message.text.strip()
        
        # Разделяем ссылки и сообщение
        parts = text.split("\n\n")
        if len(parts) < 2:
            await message.reply_text("❌ Неправильный формат. Нужно: ссылки (по одной на строке), пустая строка, сообщение")
            return
        
        links_text = parts[0].strip()
        spam_message = "\n\n".join(parts[1:]).strip()
        
        # Парсим ссылки
        links = []
        for line in links_text.split("\n"):
            line = line.strip()
            if line:
                links.append(line)
        
        if not links or not spam_message:
            await message.reply_text("❌ Ссылки или сообщение пустые")
            return
        
        # Получаем выбранный аккаунт
        account_id = user_sessions[user_id].get("account_id")
        if not account_id:
            # Берем первый аккаунт
            accounts = await get_user_accounts(user_id)
            if not accounts:
                await message.reply_text("❌ Нет аккаунтов")
                return
            account_id = accounts[0].id
        
        # Сохраняем задачу в БД
        async with await init_db() as session:
            task = SpamTask(
                user_id=user_id,
                account_id=account_id,
                links=json.dumps(links),
                message=spam_message,
                delay_min=MIN_DELAY,
                delay_max=MAX_DELAY,
                is_running=False
            )
            session.add(task)
            await session.commit()
            
            await message.reply_text(
                f"✅ Задача создана!\n\n"
                f"Ссылок: {len(links)}\n"
                f"Аккаунт ID: {account_id}\n\n"
                f"Для запуска используй /spam_start"
            )
        
        del user_sessions[user_id]
    
    # === ЭТАП 5: ДОБАВЛЕНИЕ ПАПКИ ===
    elif state == "waiting_folder":
        text = message.text.strip()
        
        # Разделяем название папки и сообщение
        parts = text.split("\n\n")
        if len(parts) < 2:
            await message.reply_text("❌ Неправильный формат. Нужно: название папки, пустая строка, сообщение")
            return
        
        folder_name = parts[0].strip()
        spam_message = "\n\n".join(parts[1:]).strip()
        
        if not folder_name or not spam_message:
            await message.reply_text("❌ Название папки или сообщение пустые")
            return
        
        account_id = user_sessions[user_id].get("account_id")
        
        # Получаем аккаунт из БД
        async with await init_db() as session:
            result = await session.execute(
                select(Account).where(Account.id == account_id)
            )
            account = result.scalar_one_or_none()
        
        if not account:
            await message.reply_text("❌ Аккаунт не найден")
            del user_sessions[user_id]
            return
        
        # Подключаемся к аккаунту
        try:
            await message.reply_text(f"⏳ Получаю чаты из папки '{folder_name}'...")
            
            user_client = Client(
                f"folder_{account_id}",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=account.session_string
            )
            
            await user_client.start()
            
            # Получаем чаты из папки
            folder_chats, error = await get_chats_from_folder(user_client, folder_name)
            
            if error:
                await message.reply_text(f"❌ {error}")
                await user_client.stop()
                del user_sessions[user_id]
                return
            
            if not folder_chats:
                await message.reply_text(f"❌ В папке '{folder_name}' нет чатов")
                await user_client.stop()
                del user_sessions[user_id]
                return
            
            # Формируем ссылки для рассылки
            links = []
            for chat in folder_chats:
                if chat['username']:
                    links.append(f"@{chat['username']}")
                else:
                    # Для приватных чатов используем ID
                    links.append(f"private:{chat['id']}")
            
            # Сохраняем задачу в БД
            async with await init_db() as session:
                task = SpamTask(
                    user_id=user_id,
                    account_id=account_id,
                    links=json.dumps(links),
                    message=spam_message,
                    delay_min=MIN_DELAY,
                    delay_max=MAX_DELAY,
                    is_running=False
                )
                session.add(task)
                await session.commit()
                
                # Показываем список найденных чатов
                chats_list = "\n".join([f"• {chat['title']}" for chat in folder_chats[:10]])
                if len(folder_chats) > 10:
                    chats_list += f"\n• ... и еще {len(folder_chats) - 10} чатов"
                
                await message.reply_text(
                    f"✅ Задача создана из папки '{folder_name}'!\n\n"
                    f"Найдено чатов: {len(folder_chats)}\n"
                    f"Первые 10:\n{chats_list}\n\n"
                    f"Аккаунт ID: {account_id}\n\n"
                    f"Для запуска используй /spam_start"
                )
            
            await user_client.stop()
            del user_sessions[user_id]
            
        except Exception as e:
            await message.reply_text(f"❌ Ошибка при получении папки: {str(e)}")
            del user_sessions[user_id]

# ========== ОСНОВНАЯ ЛОГИКА РАССЫЛКИ ==========

async def run_spam_task(user_id: int, task_id: int):
    """Запуск рассылки в фоне"""
    try:
        # Получаем задачу из БД
        async with await init_db() as session:
            result = await session.execute(
                select(SpamTask).where(SpamTask.id == task_id)
            )
            task = result.scalar_one_or_none()
            
            if not task:
                return
            
            # Получаем аккаунт
            acc_result = await session.execute(
                select(Account).where(Account.id == task.account_id)
            )
            account = acc_result.scalar_one_or_none()
            
            if not account:
                return
            
            # Помечаем как запущенную
            task.is_running = True
            await session.commit()
        
        # Создаем клиент для рассылки
        user_client = Client(
            f"spammer_{account.id}",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=account.session_string
        )
        
        await user_client.start()
        
        # Получаем список ссылок
        links = json.loads(task.links)
        total = len(links)
        sent = task.total_sent
        
        # Отправляем уведомление
        await app.send_message(
            user_id,
            f"▶️ Начинаю рассылку. Всего целей: {total}"
        )
        
        # Рассылаем по очереди
        for i, link in enumerate(links[sent:], start=sent+1):
            # Проверяем не остановлена ли задача
            if user_id not in active_tasks:
                break
            
            try:
                # Для приватных чатов по ID
                if link.startswith("private:"):
                    chat_id = int(link.replace("private:", ""))
                    try:
                        chat = await user_client.get_chat(chat_id)
                    except:
                        await app.send_message(user_id, f"❌ Не удалось получить чат {chat_id}")
                        continue
                else:
                    # Пытаемся присоединиться к чату по ссылке/юзернейму
                    try:
                        chat = await user_client.join_chat(link)
                    except UserAlreadyParticipant:
                        # Уже в чате, получаем информацию
                        if link.startswith("https://t.me/+"):
                            chat = await user_client.get_chat(link)
                        else:
                            username = link.replace("https://t.me/", "").replace("@", "")
                            chat = await user_client.get_chat(username)
                    except InviteHashExpired:
                        await app.send_message(user_id, f"❌ Ссылка {link} истекла")
                        continue
                    except Exception as e:
                        await app.send_message(user_id, f"❌ Не удалось войти в {link}: {str(e)}")
                        continue
                
                # Отправляем сообщение
                await user_client.send_message(chat.id, task.message)
                
                # Обновляем счетчик
                sent += 1
                async with await init_db() as session:
                    await session.execute(
                        SpamTask.__table__.update().
                        where(SpamTask.id == task_id).
                        values(total_sent=sent)
                    )
                    await session.commit()
                
                await app.send_message(
                    user_id,
                    f"✅ [{i}/{total}] Отправлено в {chat.title}"
                )
                
                # Задержка
                delay = random.randint(task.delay_min, task.delay_max)
                await asyncio.sleep(delay)
                
            except FloodWait as e:
                wait = e.value
                await app.send_message(
                    user_id,
                    f"⚠️ Flood wait {wait} секунд. Ждем..."
                )
                await asyncio.sleep(wait)
            except Exception as e:
                await app.send_message(
                    user_id,
                    f"❌ Ошибка при отправке в {link}: {str(e)}"
                )
                continue
        
        # Завершаем
        await user_client.stop()
        
        async with await init_db() as session:
            await session.execute(
                SpamTask.__table__.update().
                where(SpamTask.id == task_id).
                values(is_running=False)
            )
            await session.commit()
        
        await app.send_message(
            user_id,
            f"🏁 Рассылка завершена! Отправлено: {sent}/{total}"
        )
        
    except asyncio.CancelledError:
        # Задача отменена пользователем
        async with await init_db() as session:
            await session.execute(
                SpamTask.__table__.update().
                where(SpamTask.id == task_id).
                values(is_running=False)
            )
            await session.commit()
        
        await app.send_message(user_id, "⏹ Рассылка остановлена")
    except Exception as e:
        await app.send_message(user_id, f"💥 Критическая ошибка: {str(e)}")
    finally:
        if user_id in active_tasks:
            del active_tasks[user_id]

# ========== ЗАПУСК ==========

if __name__ == "__main__":
    print("🔥 Spam Bot запущен...")
    app.run()
