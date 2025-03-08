import os
import logging
import asyncio
import aiosqlite
import io
import time

from PIL import Image, ImageDraw, ImageFont, ImageFilter
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, CallbackContext

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

DB_PATH = "cloakroom.db"

# Асинхронная инициализация базы данных: номера от 1 до 500
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
                for i in range(1, 501):  # Номера от 1 до 500
                    await db.execute("INSERT INTO hangers (id, status) VALUES (?, 'free')", (i,))
                await db.commit()
                logger.info("База данных инициализирована: добавлены гардеробы от 1 до 500.")

# Функция для генерации изображения с номерком на подготовленном фоне
def generate_ticket_image(number: int) -> io.BytesIO:
    # Пытаемся загрузить подготовленный фон (background.png)
    try:
        bg = Image.open("background.png").convert("RGB")
    except Exception as e:
        logger.error(f"Ошибка загрузки background.png: {e}")
        # Если не удалось, создаём запасной фон с текстурой
        width, height = 600, 300
        background = Image.new("RGB", (width, height), color="black")
        try:
            noise = Image.effect_noise((width, height), 100).convert("RGB")
            bg = Image.blend(background, noise, 0.1)
        except Exception as e:
            logger.error(f"Ошибка при создании текстурированного фона: {e}")
            bg = background

    draw = ImageDraw.Draw(bg)
    neon_pink = "#FF6EC7"  # Неоново-розовый цвет

    # Определяем шрифт для номера – размер   pt
    try:
        number_font = ImageFont.truetype("Medium.otf", 150)
    except IOError:
        number_font = ImageFont.load_default()

    text = str(number)
    # Вычисляем размеры текста с помощью textbbox (Pillow >= 8.0)
    bbox = draw.textbbox((0, 0), text, font=number_font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    # Центрируем текст по горизонтали и вертикали
    width, height = bg.size
    text_x = (width - text_width) / 2
    text_y = (height - text_height) / 2

    # Рисуем эффект неонового свечения: обводка белым
    outline_range = 3
    for dx in range(-outline_range, outline_range + 1):
        for dy in range(-outline_range, outline_range + 1):
            if dx != 0 or dy != 0:
                draw.text((text_x + dx, text_y + dy), text, font=number_font, fill="white")
    draw.text((text_x, text_y), text, font=number_font, fill=neon_pink)

    bio = io.BytesIO()
    bg.save(bio, format="PNG")
    bio.seek(0)
    return bio

# Функция для отображения кнопок (Взять/Сдать номерок)
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

# Обработка команды /start
async def start(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    await update.message.reply_text("Добро пожаловать в бота гардеробщика, здесь вы можете получить электронный номерок")
    await show_buttons(update, user_id)

# Обработка получения номерка
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
            image = generate_ticket_image(hanger_id)
            await update.callback_query.message.reply_photo(
                photo=image, caption=f"Ваш номерок № {hanger_id}"
            )
            await show_buttons(update, user_id, delete_prev_msg=True)
        else:
            await update.callback_query.message.reply_text("К сожалению, все номерки заняты.")

# Обработка сдачи номерка
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

# Обработка нажатий кнопок
async def button_handler(update: Update, context: CallbackContext) -> None:
    action = update.callback_query.data
    if action == 'get_hanger':
        await get_hanger(update, context)
    elif action == 'free_hanger':
        await free_hanger(update, context)
    await update.callback_query.answer()

# Глобальный обработчик ошибок
async def error_handler(update: object, context: CallbackContext) -> None:
    logger.error("Ошибка при обработке обновления", exc_info=context.error)

def main():
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        logger.error("Переменная окружения BOT_TOKEN не установлена!")
        return

    # Создаем новый event loop и устанавливаем его как текущий
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_db())

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_error_handler(error_handler)

    # Удаляем вебхук (на всякий случай) и ждем пару секунд
    loop.run_until_complete(application.bot.delete_webhook(drop_pending_updates=True))
    time.sleep(2)

    logger.info("Запуск бота в режиме polling...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
