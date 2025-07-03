import os
import asyncio
import sqlite3
import logging
from datetime import datetime
from typing import Dict, Set, Optional
import pandas as pd
from io import BytesIO

from telethon import TelegramClient, events, Button
from telethon.tl.types import User, DocumentAttributeFilename
from telethon.events import StopPropagation

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_ID = os.getenv('API_ID', '')
API_HASH = os.getenv('API_HASH', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))

try:
    from config import WELCOME_MESSAGE
    from config import FINAL_MESSAGE
    logger.info("Trying import config.py")
    logger.info(WELCOME_MESSAGE)
    logger.info(FINAL_MESSAGE)
except Exception as e:
    logger.info(e)
    WELCOME_MESSAGE = '👋 Добро пожаловать! Я бот обратной связи. Ответь на несколько вопросов.'
    FINAL_MESSAGE = '✅ Благодарим за обратную связь! Ваше сообщение отправлено администратору.'

info_string = f"""
API_ID={API_ID}
API_HASH={API_HASH}
BOT_TOKEN={BOT_TOKEN}
ADMIN_ID={ADMIN_ID}
DATABASE={os.getenv('DATABASE', '')}
WELCOME_MESSAGE={WELCOME_MESSAGE}
FINAL_MESSAGE = {FINAL_MESSAGE}
"""

logger.info(info_string)

class FeedbackBot:
    def __init__(self):
        self.client = TelegramClient('feedback_bot', API_ID, API_HASH)
        os.makedirs('/data', exist_ok=True)
        self.db_path = os.getenv('DATABASE', '')
        self.welcome_message = WELCOME_MESSAGE
        self.final_message = FINAL_MESSAGE
        self.active_conversations: Set[int] = set()  # Активные разговоры
        self.blocked_users: Set[int] = set()  # Заблокированные пользователи

        # Вопросы для обратной связи
        questions = os.getenv('QUESTIONS', '')
        self.questions = [q.strip() for q in questions.split('|') if q != '']

        self.init_database()
        self.load_blocked_users()

    def init_database(self):
        """Инициализация базы данных SQLite"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_blocked INTEGER DEFAULT 0
            )
        ''')

        # Таблица обратной связи
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                answers TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')

        conn.commit()
        conn.close()
        logger.info("База данных инициализирована")

    def load_blocked_users(self):
        """Загрузка заблокированных пользователей из БД"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE is_blocked = 1")
        self.blocked_users = {row[0] for row in cursor.fetchall()}
        conn.close()
        logger.info(f"Загружено {len(self.blocked_users)} заблокированных пользователей")

    def save_user(self, user: User):
        """Сохранение информации о пользователе"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT OR REPLACE INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
        ''', (user.id, user.username, user.first_name, user.last_name))

        conn.commit()
        conn.close()

    def save_feedback(self, user_id: int, answers: str):
        """Сохранение обратной связи в БД"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO feedback (user_id, answers)
            VALUES (?, ?)
        ''', (user_id, answers))

        conn.commit()
        conn.close()

    def block_user(self, user_id: int):
        """Блокировка пользователя"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_blocked = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        self.blocked_users.add(user_id)

    def unblock_user(self, user_id: int):
        """Разблокировка пользователя"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_blocked = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        self.blocked_users.discard(user_id)

    def get_all_users(self):
        """Получение всех пользователей"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, first_name, is_blocked FROM users")
        users = cursor.fetchall()
        conn.close()
        return users

    def generate_report(self):
        """Генерация отчёта в формате Excel"""
        conn = sqlite3.connect(self.db_path)

        # Получаем данные с JOIN
        query = '''
            SELECT
                u.user_id,
                u.username,
                u.first_name,
                u.last_name,
                f.answers,
                f.created_at
            FROM feedback f
            JOIN users u ON f.user_id = u.user_id
            ORDER BY f.created_at DESC
        '''

        df = pd.read_sql_query(query, conn)
        conn.close()

        # Создаём Excel файл в памяти
        output_excel = BytesIO()
        with pd.ExcelWriter(output_excel, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Feedback Report', index=False)
        output_csv = BytesIO()
        df.to_csv(output_csv, index=False)

        output_excel.seek(0)
        output_csv.seek(0)
        return output_excel, output_csv

    async def setup_handlers(self):
        """Настройка обработчиков событий"""

        @self.client.on(events.NewMessage(pattern='/start'))
        async def start_handler(event):
            """Обработчик команды /start с conversation"""
            user_id = event.sender_id

            # Проверка на блокировку
            if user_id in self.blocked_users:
                #await event.respond("❌ Вы заблокированы и не можете использовать бота.")
                raise StopPropagation

            # Проверка на администратора
            if user_id == ADMIN_ID:
                await self.show_admin_panel(event)
                raise StopPropagation

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM feedback")
            users = [row[0] for row in cursor.fetchall()]
            conn.close()

            if user_id in users:
                raise StopPropagation

            # Добавляем пользователя в активные разговоры
            self.active_conversations.add(user_id)

            try:
                # Сохраняем информацию о пользователе
                self.save_user(event.sender)

                await event.respond(self.welcome_message)

                # Начинаем conversation
                async with self.client.conversation(user_id, timeout=300) as conv:
                    answers = []

                    for i, question in enumerate(self.questions):
                        await conv.send_message(f"**Вопрос {i+1}/{len(self.questions)}:** {question}")
                        response = await conv.get_response()
                        answers.append(response.message)

                    # Формируем сообщение для администратора
                    user_info = f"👤 **Новое обращение от пользователя:**\n"
                    user_info += f"**ID:** {user_id}\n"
                    user_info += f"**Username:** @{event.sender.username or 'не указан'}\n"
                    user_info += f"**Имя:** {event.sender.first_name or 'не указано'}\n\n"

                    feedback_text = "📝 **Ответы на вопросы:**\n"
                    for i, (question, answer) in enumerate(zip(self.questions, answers)):
                        feedback_text += f"**{i+1}.** {question}\n**Ответ:** {answer}\n\n"
                    
                    # Сохраняем обратную связь в БД
                    self.save_feedback(user_id, feedback_text)

                    # Отправляем администратору
                    buttons = [
                        [Button.inline("✉️ Ответить", f"reply_{user_id}")],
                        [Button.inline("🚫 Заблокировать", f"block_{user_id}"),
                        Button.inline("✅ Разблокировать", f"unblock_{user_id}")]
                    ]
                    await self.client.send_message(ADMIN_ID, user_info + feedback_text, buttons=buttons)
                    await conv.send_message(self.final_message)
                    self.block_user(user_id)

            except asyncio.TimeoutError:
                await event.respond("⏰ Время ожидания истекло. Попробуйте снова с /start")
            except Exception as e:
                logger.error(f"Ошибка в start_handler: {e}")
                await event.respond("❌ Произошла ошибка. Попробуйте позже.")
            finally:
                # Убираем пользователя из активных разговоров
                self.active_conversations.discard(user_id)
            raise StopPropagation

        @self.client.on(events.NewMessage)
        async def message_forwarder(event):
            """Пересылка сообщений пользователей администратору"""
            user_id = event.sender_id

            # Пропускаем сообщения от администратора
            if user_id == ADMIN_ID:
                return

            # Пропускаем сообщения от пользователей в активном conversation
            if user_id in self.active_conversations:
                return

            # Проверка на блокировку
            if user_id in self.blocked_users:
                #await event.respond("❌ Вы заблокированы и не можете писать боту.")
                return

            # Сохраняем пользователя если его нет в БД
            self.save_user(event.sender)

            # Формируем сообщение для администратора
            forward_msg = f"💬 **Сообщение от пользователя:**\n"
            forward_msg += f"**ID:** {user_id}\n"
            forward_msg += f"**Username:** @{event.sender.username or 'не указан'}\n"
            forward_msg += f"**Имя:** {event.sender.first_name or 'не указано'}\n\n"
            forward_msg += f"**Сообщение:** {event.message.message}"

            # Отправляем администратору с кнопками быстрых действий
            buttons = [
                [Button.inline("✉️ Ответить", f"reply_{user_id}")],
                [Button.inline("🚫 Заблокировать", f"block_{user_id}"),
                 Button.inline("✅ Разблокировать", f"unblock_{user_id}")]
            ]
            await self.client.send_message(ADMIN_ID, forward_msg, buttons=buttons)

        @self.client.on(events.CallbackQuery)
        async def callback_handler(event):
            """Обработчик inline кнопок"""
            if event.sender_id != ADMIN_ID:
                await event.answer("❌ Доступ запрещён!")
                return

            data = event.data.decode()

            if data.startswith("reply_"):
                user_id = int(data.split("_")[1])
                # Ждём ответ от администратора
                async with self.client.conversation(ADMIN_ID, timeout=300) as conv:
                    try:
                        await conv.send_message(f"💬 Напишите ответ пользователю {user_id}:")
                        response = await conv.get_response()
                        await self.client.send_message(user_id, f"📨 **Ответ от администратора:**\n\n{response.message}")
                        await conv.send_message("✅ Ответ отправлен пользователю!")
                    except asyncio.TimeoutError:
                        await conv.send_message("⏰ Время ожидания ответа истекло.")

            elif data.startswith("block_"):
                user_id = int(data.split("_")[1])
                self.block_user(user_id)
                await event.respond("🚫 Пользователь заблокирован!")

            elif data.startswith("unblock_"):
                user_id = int(data.split("_")[1])
                self.unblock_user(user_id)
                await event.respond("✅ Пользователь разблокирован!")

            elif data == "mass_broadcast":
                await self.handle_mass_broadcast(event)

            elif data == "generate_report":
                await self.handle_generate_report(event)

            elif data == "user_management":
                await self.show_user_management(event)

            elif data == "back_to_admin":
                await self.show_admin_panel(event)

    async def show_admin_panel(self, event):
        """Показ админ-панели"""
        admin_text = "🛠 **Панель администратора**\n\nВыберите действие:"
        buttons = [
            [Button.inline("👥 Управление пользователями", "user_management")],
            [Button.inline("📢 Массовая рассылка", "mass_broadcast")],
            [Button.inline("📊 Сгенерировать отчёт", "generate_report")]
        ]
        await event.respond(admin_text, buttons=buttons)

    async def show_user_management(self, event):
        """Показ управления пользователями"""
        users = self.get_all_users()
        if not users:
            await event.edit("👥 Пользователей не найдено.",
                           buttons=[[Button.inline("⬅️ Назад", "back_to_admin")]])
            return

        text = "👥 **Управление пользователями:**\n\n"
        buttons = []

        for user_id, username, first_name, is_blocked in users:  # Показываем первых 10
            status = "🚫" if is_blocked else "✅"
            name = f"{first_name or 'Без имени'} (@{username or 'без username'})"
            text += f"{status} {name} (ID: {user_id})\n"

            action = "unblock" if is_blocked else "block"
            action_text = "Разблокировать" if is_blocked else "Заблокировать"
            buttons.append([Button.inline(f"{action_text} {user_id}", f"{action}_{user_id}")])

        buttons.append([Button.inline("⬅️ Назад", "back_to_admin")])
        await event.edit(text, buttons=buttons)

    async def handle_mass_broadcast(self, event):
        """Обработка массовой рассылки"""
        async with self.client.conversation(ADMIN_ID, timeout=300) as conv:
            try:
                await event.delete()
                await conv.send_message("📢 Напишите сообщение для массовой рассылки:")
                response = await conv.get_response()
                broadcast_msg = response.message

                # Получаем всех незаблокированных пользователей
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM users WHERE is_blocked = 0")
                users = [row[0] for row in cursor.fetchall()]
                conn.close()

                # Отправляем сообщение
                sent_count = 0
                failed_count = 0

                for user_id in users:
                    try:
                        await self.client.send_message(user_id, f"📢 **Рассылка:**\n\n{broadcast_msg}")
                        sent_count += 1
                    except Exception as e:
                        logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
                        failed_count += 1

                await conv.send_message(f"✅ Рассылка завершена!\n"
                                      f"Отправлено: {sent_count}\n"
                                      f"Ошибок: {failed_count}")

            except asyncio.TimeoutError:
                await conv.send_message("⏰ Время ожидания сообщения истекло.")

    async def handle_generate_report(self, event):
        """Генерация и отправка отчёта"""
        try:
            await event.edit("📊 Генерирую отчёт...")

            # Генерируем Excel отчёт
            report_file_excel, report_file_csv = self.generate_report()
            filename_excel = f"feedback_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            filename_csv = f"feedback_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

            # Отправляем файл
            await self.client.send_file(
                ADMIN_ID,
                file=report_file_excel,
                caption="📊 Отчёт по обратной связи .xlsx",
                attributes=[
                    DocumentAttributeFilename(file_name=filename_excel)
                ]
            )
            await self.client.send_file(
                ADMIN_ID,
                file=report_file_csv,
                caption="📊 Отчёт по обратной связи .csv",
                attributes=[
                    DocumentAttributeFilename(file_name=filename_csv)
                ]
            )
            await event.edit("✅ Отчёт отправлен!")

        except Exception as e:
            logger.error(f"Ошибка генерации отчёта: {e}")
            await event.edit("❌ Ошибка при генерации отчёта.")

    async def start(self):
        """Запуск бота"""
        await self.client.start(bot_token=BOT_TOKEN)
        await self.setup_handlers()

        # Запускаем бота
        await self.client.run_until_disconnected()

# Запуск бота
if __name__ == "__main__":
    bot = FeedbackBot()
    asyncio.run(bot.start())
