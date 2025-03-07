import os
import logging
import asyncio
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, CallbackContext

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

DB_PATH = "cloakroom.db"

# Инициализация базы данных с использованием aiosqlite
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS hangers (
                id INTEGER PRIMARY KEY,
                status TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                hanger_id INTEGER
            )
        """)
        async with db.execute("SELECT COUNT(*) FROM hangers") as cursor:
            row = await cursor.fetchone()
            if row[0] == 0:
                for i in range(1, 201):  # Номера от 1 до 200
                    await db.execute("INSERT INTO hangers (id, status) VALUES (?, 'free')", (i,))
                await db.commit()
                logger.info("База данных инициализирована: добавлены гардеробы.")

# Функция отображения кнопок
async def show_buttons(update: Update, user_id: int, delete_prev_msg=False) -> None:
    keyboard = [[InlineKeyboardButton("Взять номерок", callback_data='get_hanger')]]
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT hanger_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
    if user:
        keyboard = [[InlineKeyboardButton("Сдать номерок", callback_data='free_hanger')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if delete_prev_msg and update.callback_query:
        try:
            await update.callback_query.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")
    
    if update.message:
        await update.message.reply_text("Выберите действие:", reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.reply_text("Выберите действие:", reply_markup=reply_markup)

# Команда /start
async def start(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    await update.message.reply_text(
        "Добро пожаловать в бота гардеробщика, здесь вы можете получить электронный номерок"
    )
    await show_buttons(update, user_id)

# Команда для получения номерка
async def get_hanger(update: Update, context: CallbackContext) -> None:
    user_id = update.callback_query.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT hanger_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
        if user:
            await update.callback_query.message.reply_text(
                f"Вы уже взяли номерок {user[0]}. Чтобы его сдать, нажмите кнопку."
            )
            await show_buttons(update, user_id)
            return
        async with db.execute("SELECT id FROM hangers WHERE status = 'free' ORDER BY id LIMIT 1") as cursor:
            row = await cursor.fetchone()
        if row:
            hanger_id = row[0]
            await db.execute("UPDATE hangers SET status = 'taken' WHERE id = ?", (hanger_id,))
            await db.execute("INSERT INTO users (user_id, hanger_id) VALUES (?, ?)", (user_id, hanger_id))
            await db.commit()
            await update.callback_query.message.reply_text(f"Ваш номерок № {hanger_id}")
            await show_buttons(update, user_id, delete_prev_msg=True)
        else:
            await update.callback_query.message.reply_text("К сожалению, все номерки заняты.")

# Команда для освобождения номерка
async def free_hanger(update: Update, context: CallbackContext) -> None:
    user_id = update.callback_query.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT hanger_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
        if not user:
            await update.callback_query.message.reply_text("Вы не брали номерок.")
            return
        hanger_id = user[0]
        await db.execute("UPDATE hangers SET status = 'free' WHERE id = ?", (hanger_id,))
        await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        await db.commit()
        await update.callback_query.message.reply_text("Вы успешно сдали номерок!")
        await show_buttons(update, user_id, delete_prev_msg=True)

# Обработка нажатия кнопок
async def button_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    action = query.data
    if action == 'get_hanger':
        await get_hanger(update, context)
    elif action == 'free_hanger':
        await free_hanger(update, context)
    await query.answer()

# Глобальный обработчик ошибок
async def error_handler(update: object, context: CallbackContext) -> None:
    logger.error("Исключение при обработке обновления:", exc_info=context.error)

# Основная функция запуска
def main():
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        logger.error("Переменная окружения BOT_TOKEN не установлена!")
        return

    # Инициализируем БД
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_error_handler(error_handler)

    # Определяем режим запуска: polling (локальное тестирование) или webhook (для Railway)
    WEBHOOK_MODE = os.getenv("WEBHOOK_MODE", "false").lower() == "true"
    if WEBHOOK_MODE:
        PORT = int(os.getenv("PORT", "8443"))
        WEBHOOK_URL = os.getenv("WEBHOOK_URL")
        if not WEBHOOK_URL:
            logger.error("Для webhook режима необходимо установить переменную WEBHOOK_URL!")
            return
        logger.info("Запуск бота в режиме webhook...")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
        )
    else:
        logger.info("Запуск бота в режиме polling...")
        application.run_polling()

if __name__ == '__main__':
    main()
