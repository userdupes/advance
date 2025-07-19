import asyncio
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
import threading
import time
from datetime import datetime
import re
import logging
import json
import os
from PIL import Image
import io

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = "7967816394:AAEVj9sdT0JCfxOBtV3igi7AMc2eEFE7Tdk"
ADMIN_ID = 6532749214  # Ваш ID (узнать у @userinfobot)

# Глобальные переменные
active_chats = {}  # {chat_id: {"mode": str, "interval": int, "is_spamming": bool, "message": str, "schedule": dict}}
pending_invites = []  # Список пригласительных ссылок
join_lock = threading.Lock()  # Блокировка для работы с очередью ссылок
bot_instance = None

# Файл для сохранения чатов
CHATS_FILE = "saved_chats.json"

# Рекламное сообщение
AD_MESSAGE = "📣 Хочешь приобрести рассылку по чатам? Пиши - @advcreator"

def save_chats():
    """Сохраняет активные чаты в файл"""
    try:
        chats_to_save = {}
        for chat_id, settings in active_chats.items():
            chats_to_save[str(chat_id)] = {
                'mode': settings['mode'],
                'interval': settings['interval'],
                'is_spamming': False,
                'message': settings['message'],
                'schedule': settings['schedule']
            }

        with open(CHATS_FILE, 'w', encoding='utf-8') as f:
            json.dump(chats_to_save, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ Сохранено {len(chats_to_save)} чатов в {CHATS_FILE}")
    except Exception as e:
        logger.error(f"❌ Ошибка при сохранении чатов: {e}")

def load_chats():
    """Загружает активные чаты из файла"""
    global active_chats
    try:
        if os.path.exists(CHATS_FILE):
            with open(CHATS_FILE, 'r', encoding='utf-8') as f:
                saved_chats = json.load(f)

            for chat_id_str, settings in saved_chats.items():
                chat_id = int(chat_id_str)
                active_chats[chat_id] = settings

            logger.info(f"✅ Загружено {len(active_chats)} чатов из {CHATS_FILE}")
        else:
            logger.info("📁 Файл сохранения чатов не найден, начинаем с пустого списка")
    except Exception as e:
        logger.error(f"❌ Ошибка при загрузке чатов: {e}")
        active_chats = {}

# Клавиатуры
def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙ Управление чатами", callback_data='chat_list')],
        [InlineKeyboardButton("⚡️ Быстрые команды", callback_data='quick_commands')],
        [InlineKeyboardButton("⏰ Расписание работы", callback_data='schedule_menu')],
        [InlineKeyboardButton("🔗 Добавить чат по ссылке", callback_data='add_chat_link')]
    ])

async def chat_list_keyboard():
    keyboard = []
    for chat_id in active_chats:
        try:
            chat = await bot_instance.get_chat(chat_id)
            btn_text = f"{chat.title or 'ЛС'} ({'🚀' if active_chats[chat_id]['mode'] == 'turbo' else '🥷'} {active_chats[chat_id]['interval']}s)"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f'chat_{chat_id}')])
        except:
            pass
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data='back')])
    return InlineKeyboardMarkup(keyboard)

def chat_settings_keyboard(chat_id):
    mode = active_chats[chat_id]['mode']
    intervals = [1, 5] if mode == 'turbo' else [10, 30, 60]

    interval_buttons = []
    for i in intervals:
        interval_buttons.append(
            InlineKeyboardButton(
                f"{'🔵' if active_chats[chat_id]['interval'] == i else '⚪'} {i}s",
                callback_data=f'setinterval_{chat_id}_{i}'
            )
        )

    schedule_status = "🟢" if active_chats[chat_id]['schedule']['enabled'] else "🔴"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✉: {active_chats[chat_id]['message'][:15]}...", callback_data=f'setmsg_{chat_id}')],
        [InlineKeyboardButton(f"Режим: {'🚀 Turbo' if mode == 'turbo' else '🥷 Stealth'}", callback_data=f'togglemode_{chat_id}')],
        interval_buttons,
        [InlineKeyboardButton(f"{schedule_status} Расписание: {active_chats[chat_id]['schedule']['start']}-{active_chats[chat_id]['schedule']['end']}", callback_data=f'schedule_{chat_id}')],
        [InlineKeyboardButton("🟢 ВКЛ" if not active_chats[chat_id]['is_spamming'] else "🔴 ВЫКЛ", callback_data=f'togglespam_{chat_id}')],
        [InlineKeyboardButton("❌ Покинуть чат", callback_data=f'leave_chat_{chat_id}')],
        [InlineKeyboardButton("⬅️ Назад", callback_data='chat_list')]
    ])

def quick_commands_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Турбо во всех чатах", callback_data='turbo_all')],
        [InlineKeyboardButton("🥷 Скрытый во всех чатах", callback_data='stealth_all')],
        [InlineKeyboardButton("▶ Старт везде", callback_data='start_all')],
        [InlineKeyboardButton("⏹ Стоп везде", callback_data='stop_all')],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back')]
    ])

def convert_image_to_jpeg(image_path):
    """Конвертирует изображение в JPEG формат для лучшей совместимости с Telegram"""
    try:
        with Image.open(image_path) as img:
            # Конвертируем в RGB если изображение в другом режиме
            if img.mode != 'RGB':
                img = img.convert('RGB')

            # Уменьшаем размер если он слишком большой
            max_size = (1280, 1280)
            img.thumbnail(max_size, Image.Resampling.LANCZOS)

            # Сохраняем в буфер как JPEG
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=85, optimize=True)
            buffer.seek(0)
            return buffer
    except Exception as e:
        logger.error(f"Ошибка конвертации изображения: {e}")
        return None

def schedule_keyboard(chat_id=None):
    if chat_id:
        schedule = active_chats[chat_id]['schedule']
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Начало: {schedule['start']}", callback_data=f'set_start_{chat_id}')],
            [InlineKeyboardButton(f"Конец: {schedule['end']}", callback_data=f'set_end_{chat_id}')],
            [InlineKeyboardButton("✅ Включить расписание", callback_data=f'enable_schedule_{chat_id}')],
            [InlineKeyboardButton("❌ Выключить расписание", callback_data=f'disable_schedule_{chat_id}')],
            [InlineKeyboardButton("⬅️ Назад", callback_data=f'chat_{chat_id}')]
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад", callback_data='back')]
        ])

async def send_ad_to_non_admins(update: Update):
    """Отправляет рекламное сообщение всем пользователям, кроме администратора"""
    if update.effective_user.id != ADMIN_ID:
        try:
            await update.message.reply_text(AD_MESSAGE)
        except Exception as e:
            logger.error(f"Ошибка при отправке рекламы пользователю {update.effective_user.id}: {e}")

async def process_invites():
    global bot_instance
    while True:
        try:
            with join_lock:
                if pending_invites:
                    invite_link = pending_invites.pop(0)
                    try:
                        chat = await bot_instance.get_chat(invite_link)

                        if chat.id not in active_chats:
                            try:
                                await bot_instance.join_chat(invite_link)
                            except Exception as join_error:
                                logger.warning(f"Не удалось присоединиться к чату {invite_link}: {join_error}")
                                pass

                            active_chats[chat.id] = {
                                'mode': 'stealth',
                                'interval': 10,
                                'is_spamming': False,
                                'message': f"Сообщение для чата {chat.title or chat.id}",
                                'schedule': {
                                    'start': '09:00',
                                    'end': '18:00',
                                    'enabled': False
                                }
                            }
                            save_chats()

                            quick_manage_keyboard = InlineKeyboardMarkup([
                                [InlineKeyboardButton("⚙ Управлять этим чатом", callback_data=f'chat_{chat.id}')],
                                [InlineKeyboardButton("📋 Все чаты", callback_data='chat_list')],
                                [InlineKeyboardButton("🏠 Главное меню", callback_data='back')]
                            ])

                            await bot_instance.send_message(
                                chat_id=ADMIN_ID,
                                text=f"🤖 Бот успешно добавлен в чат!\n\n"
                                     f"📌 Название: {chat.title or 'Неизвестно'}\n"
                                     f"🆔 ID: {chat.id}\n"
                                     f"🔗 Ссылка: {invite_link}\n\n"
                                     f"Выберите действие:",
                                reply_markup=quick_manage_keyboard
                            )
                    except Exception as e:
                        await bot_instance.send_message(
                            chat_id=ADMIN_ID,
                            text=f"❌ Ошибка при обработке ссылки {invite_link}:\n{str(e)}\n\n"
                                 "Проверьте, что:\n"
                                 "1. Ссылка действительна\n"
                                 "2. Бот имеет права администратора (если требуется)\n"
                                 "3. Чат не является приватным (для приватных чатов добавьте бота вручную)"
                        )
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Ошибка в process_invites: {e}")
            await asyncio.sleep(10)

async def check_schedule():
    global bot_instance
    while True:
        try:
            now = datetime.now().strftime("%H:%M")
            for chat_id, settings in active_chats.items():
                if settings['schedule']['enabled']:
                    start = settings['schedule']['start']
                    end = settings['schedule']['end']

                    if start <= now < end and not settings['is_spamming']:
                        settings['is_spamming'] = True
                        asyncio.create_task(send_spam_messages(chat_id))
                        await bot_instance.send_message(
                            chat_id=ADMIN_ID,
                            text=f"⏰ Автоматический запуск в чате {chat_id} по расписанию"
                        )
                    elif (now < start or now >= end) and settings['is_spamming']:
                        settings['is_spamming'] = False
                        await bot_instance.send_message(
                            chat_id=ADMIN_ID,
                            text=f"⏰ Автоматическая остановка в чате {chat_id} по расписанию"
                        )
            await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"Ошибка в check_schedule: {e}")
            await asyncio.sleep(60)

async def periodic_chat_validation():
    """Периодически проверяет доступность чатов"""
    while True:
        await asyncio.sleep(3600)
        await validate_saved_chats()

async def validate_saved_chats():
    """Проверяет доступность сохраненных чатов и удаляет недоступные"""
    global active_chats
    if not active_chats:
        return

    unavailable_chats = []
    available_count = 0

    for chat_id in list(active_chats.keys()):
        try:
            await bot_instance.get_chat(chat_id)
            available_count += 1
        except Exception as e:
            if "bot is not a member" in str(e):
                logger.warning(f"⚠ Бот больше не является участником чата {chat_id}")
            else:
                logger.warning(f"⚠ Чат {chat_id} недоступен: {e}")
            unavailable_chats.append(chat_id)

    for chat_id in unavailable_chats:
        del active_chats[chat_id]

    if unavailable_chats:
        save_chats()
        await bot_instance.send_message(
            chat_id=ADMIN_ID,
            text=f"🔄 Проверка чатов завершена:\n"
                 f"✅ Доступно: {available_count}\n"
                 f"❌ Удалено недоступных: {len(unavailable_chats)}"
        )
    else:
        await bot_instance.send_message(
            chat_id=ADMIN_ID,
            text=f"✅ Все {available_count} сохраненных чатов доступны!"
        )

async def send_spam_messages(chat_id: int):
    global bot_instance
    while chat_id in active_chats and active_chats[chat_id]['is_spamming']:
        try:
            await bot_instance.send_message(
                chat_id=chat_id,
                text=active_chats[chat_id]['message']
            )
            await asyncio.sleep(active_chats[chat_id]['interval'])
        except Exception as e:
            if "bot is not a member" in str(e):
                if chat_id in active_chats:
                    del active_chats[chat_id]
                    save_chats()
                await bot_instance.send_message(
                    chat_id=ADMIN_ID,
                    text=f"⚠️ Бот был удалён из чата {chat_id} и больше не является его участником. Чат удалён из списка активных."
                )
                break
            else:
                await bot_instance.send_message(
                    chat_id=ADMIN_ID,
                    text=f"❌ Ошибка в чате {chat_id}: {str(e)}"
                )
                active_chats[chat_id]['is_spamming'] = False
                break

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        message_text = ("📣 Advance Posts - лучший сервис для рекламы любых проектов.\n\n"
                       "⚙ Выбери нужную функцию для пользования ботом:")

        try:
            # Пробуем отправить с изображением
            image_path = 'attached_assets/image_1752928390698.png'
            if os.path.exists(image_path):
                # Конвертируем изображение в JPEG
                converted_image = convert_image_to_jpeg(image_path)
                if converted_image:
                    await update.message.reply_photo(
                        photo=converted_image,
                        caption=message_text,
                        reply_markup=main_keyboard()
                    )
                else:
                    raise Exception("Image conversion failed")
            else:
                logger.warning(f"Файл изображения не найден: {image_path}")
                raise Exception("Image file not found")
        except Exception as e:
            # Если не получилось с изображением, отправляем только текст
            logger.warning(f"Не удалось отправить изображение: {e}")
            await update.message.reply_text(
                message_text,
                reply_markup=main_keyboard()
            )
    else:
        await send_ad_to_non_admins(update)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'chat_list':
        if not active_chats:
            try:
                if query.message.photo:
                    await query.delete_message()
                    await bot_instance.send_message(
                        chat_id=query.from_user.id,
                        text="📭 Нет активных чатов",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("⬅️ Назад", callback_data='back')]
                        ])
                    )
                else:
                    await query.edit_message_text(
                        "📭 Нет активных чатов",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("⬅️ Назад", callback_data='back')]
                        ])
                    )
            except:
                await query.delete_message()
                await bot_instance.send_message(
                    chat_id=query.from_user.id,
                    text="📭 Нет активных чатов",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Назад", callback_data='back')]
                    ])
                )
        else:
            keyboard = await chat_list_keyboard()
            try:
                if query.message.photo:
                    await query.delete_message()
                    await bot_instance.send_message(
                        chat_id=query.from_user.id,
                        text="📋 Список чатов (🚀=турбо, 🥷=скрытый):",
                        reply_markup=keyboard
                    )
                else:
                    await query.edit_message_text(
                        "📋 Список чатов (🚀=турбо, 🥷=скрытый):",
                        reply_markup=keyboard
                    )
            except:
                await query.delete_message()
                await bot_instance.send_message(
                    chat_id=query.from_user.id,
                    text="📋 Список чатов (🚀=турбо, 🥷=скрытый):",
                    reply_markup=keyboard
                )

    elif query.data == 'quick_commands':
        try:
            if query.message.photo:
                await query.delete_message()
                await bot_instance.send_message(
                    chat_id=query.from_user.id,
                    text="⚡ Быстрые команды:",
                    reply_markup=quick_commands_keyboard()
                )
            else:
                await query.edit_message_text(
                    "⚡ Быстрые команды:",
                    reply_markup=quick_commands_keyboard()
                )
        except:
            await query.delete_message()
            await bot_instance.send_message(
                chat_id=query.from_user.id,
                text="⚡ Быстрые команды:",
                reply_markup=quick_commands_keyboard()
            )

    elif query.data == 'schedule_menu':
        try:
            if query.message.photo:
                await query.delete_message()
                await bot_instance.send_message(
                    chat_id=query.from_user.id,
                    text="⏰ Управление расписанием:",
                    reply_markup=schedule_keyboard()
                )
            else:
                await query.edit_message_text(
                    "⏰ Управление расписанием:",
                    reply_markup=schedule_keyboard()
                )
        except:
            await query.delete_message()
            await bot_instance.send_message(
                chat_id=query.from_user.id,
                text="⏰ Управление расписанием:",
                reply_markup=schedule_keyboard()
            )

    elif query.data == 'add_chat_link':
        context.user_data['awaiting'] = 'chat_link'
        try:
            if query.message.photo:
                await query.delete_message()
                await bot_instance.send_message(
                    chat_id=query.from_user.id,
                    text="🔗 Пришлите ссылку-приглашение в чат (формат: @username или https://t.me/...):",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("❌ Отмена", callback_data='back')]
                    ])
                )
            else:
                await query.edit_message_text(
                    "🔗 Пришлите ссылку-приглашение в чат (формат: @username или https://t.me/...):",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("❌ Отмена", callback_data='back')]
                    ])
                )
        except:
            await query.delete_message()
            await bot_instance.send_message(
                chat_id=query.from_user.id,
                text="🔗 Пришлите ссылку-приглашение в чат (формат: @username или https://t.me/...):",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Отмена", callback_data='back')]
                ])
            )

    elif query.data.startswith('chat_'):
        chat_id = int(query.data.split('_')[1])
        await query.edit_message_text(
            f"⚙ Настройки чата {chat_id}:",
            reply_markup=chat_settings_keyboard(chat_id)
        )

    elif query.data.startswith('leave_chat_'):
        chat_id = int(query.data.split('_')[2])
        try:
            await bot_instance.leave_chat(chat_id)
            if chat_id in active_chats:
                del active_chats[chat_id]
                save_chats()
            await query.edit_message_text(
                f"✅ Бот покинул чат {chat_id}",
                reply_markup=main_keyboard()
            )
        except Exception as e:
            await query.edit_message_text(
                f"❌ Ошибка при выходе из чата: {str(e)}",
                reply_markup=chat_settings_keyboard(chat_id)
            )

    elif query.data.startswith('setmsg_'):
        chat_id = int(query.data.split('_')[1])
        context.user_data['awaiting'] = 'message'
        context.user_data['editing_chat'] = chat_id
        await query.edit_message_text(
            f"📝 Введите новое сообщение для чата {chat_id}:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Отмена", callback_data=f'chat_{chat_id}')]
            ])
        )

    elif query.data.startswith('togglemode_'):
        chat_id = int(query.data.split('_')[1])
        if chat_id not in active_chats:
            await query.edit_message_text("❌ Чат не найден", reply_markup=main_keyboard())
            return
        current_mode = active_chats[chat_id]['mode']
        new_mode = 'stealth' if current_mode == 'turbo' else 'turbo'
        active_chats[chat_id]['mode'] = new_mode
        active_chats[chat_id]['interval'] = 1 if new_mode == 'turbo' else 10
        save_chats()
        await query.edit_message_text(
            f"✅ Режим изменён на {'🚀 Turbo' if new_mode == 'turbo' else '🥷 Stealth'}",
            reply_markup=chat_settings_keyboard(chat_id)
        )

    elif query.data.startswith('setinterval_'):
        _, chat_id, interval = query.data.split('_')
        chat_id = int(chat_id)
        interval = int(interval)
        if chat_id not in active_chats:
            await query.edit_message_text("❌ Чат не найден", reply_markup=main_keyboard())
            return
        active_chats[chat_id]['interval'] = interval
        save_chats()
        await query.edit_message_text(
            f"✅ Интервал обновлён: {interval} сек",
            reply_markup=chat_settings_keyboard(chat_id)
        )

    elif query.data.startswith('schedule_'):
        chat_id = int(query.data.split('_')[1])
        await query.edit_message_text(
            f"⏰ Расписание для чата {chat_id}:",
            reply_markup=schedule_keyboard(chat_id)
        )

    elif query.data.startswith('set_start_'):
        chat_id = int(query.data.split('_')[2])
        context.user_data['awaiting'] = 'start_time'
        context.user_data['editing_chat'] = chat_id
        await query.edit_message_text(
            f"🕘 Введите время начала для чата {chat_id} (HH:MM):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Отмена", callback_data=f'schedule_{chat_id}')]
            ])
        )

    elif query.data.startswith('set_end_'):
        chat_id = int(query.data.split('_')[2])
        context.user_data['awaiting'] = 'end_time'
        context.user_data['editing_chat'] = chat_id
        await query.edit_message_text(
            f"🕘 Введите время окончания для чата {chat_id} (HH:MM):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Отмена", callback_data=f'schedule_{chat_id}')]
            ])
        )

    elif query.data.startswith('enable_schedule_'):
        chat_id = int(query.data.split('_')[2])
        if chat_id not in active_chats:
            await query.edit_message_text("❌ Чат не найден", reply_markup=main_keyboard())
            return
        active_chats[chat_id]['schedule']['enabled'] = True
        save_chats()
        await query.edit_message_text(
            f"✅ Расписание включено для чата {chat_id}",
            reply_markup=schedule_keyboard(chat_id)
        )

    elif query.data.startswith('disable_schedule_'):
        chat_id = int(query.data.split('_')[2])
        if chat_id not in active_chats:
            await query.edit_message_text("❌ Чат не найден", reply_markup=main_keyboard())
            return
        active_chats[chat_id]['schedule']['enabled'] = False
        save_chats()
        await query.edit_message_text(
            f"❌ Расписание отключено для чата {chat_id}",
            reply_markup=schedule_keyboard(chat_id)
        )

    elif query.data.startswith('togglespam_'):
        chat_id = int(query.data.split('_')[1])
        if chat_id not in active_chats:
            await query.edit_message_text("❌ Чат не найден", reply_markup=main_keyboard())
            return
        active_chats[chat_id]['is_spamming'] = not active_chats[chat_id]['is_spamming']

        if active_chats[chat_id]['is_spamming']:
            asyncio.create_task(send_spam_messages(chat_id))

        await query.edit_message_text(
            f"⚙ Настройки чата {chat_id}",
            reply_markup=chat_settings_keyboard(chat_id)
        )

    elif query.data == 'turbo_all':
        for chat_id in active_chats:
            active_chats[chat_id]['mode'] = 'turbo'
            active_chats[chat_id]['interval'] = 1
        save_chats()
        await query.edit_message_text(
            "✅ Во всех чатах установлен режим 🚀 Turbo (1 сек)",
            reply_markup=quick_commands_keyboard()
        )

    elif query.data == 'stealth_all':
        for chat_id in active_chats:
            active_chats[chat_id]['mode'] = 'stealth'
            active_chats[chat_id]['interval'] = 10
        save_chats()
        await query.edit_message_text(
            "✅ Во всех чатах установлен режим 🥷 Stealth (10 сек)",
            reply_markup=quick_commands_keyboard()
        )

    elif query.data == 'start_all':
        for chat_id in active_chats:
            active_chats[chat_id]['is_spamming'] = True
            asyncio.create_task(send_spam_messages(chat_id))
        await query.edit_message_text(
            "🚀 Спам запущен во всех чатах!",
            reply_markup=quick_commands_keyboard()
        )

    elif query.data == 'stop_all':
        for chat_id in active_chats:
            active_chats[chat_id]['is_spamming'] = False
        await query.edit_message_text(
            "🛑 Спам остановлен во всех чатах.",
            reply_markup=quick_commands_keyboard()
        )

    elif query.data == 'back':
        try:
            await query.delete_message()
        except:
            pass

        message_text = ("📣 Advance Posts - лучший сервис для рекламы любых проектов.\n\n"
                       "⚙ Выбери нужную функцию для пользования ботом:")

        try:
            # Пробуем отправить с изображением
            image_path = 'attached_assets/image_1752928390698.png'
            if os.path.exists(image_path):
                # Конвертируем изображение в JPEG
                converted_image = convert_image_to_jpeg(image_path)
                if converted_image:
                    await bot_instance.send_photo(
                        chat_id=query.from_user.id,
                        photo=converted_image,
                        caption=message_text,
                        reply_markup=main_keyboard()
                    )
                else:
                    raise Exception("Image conversion failed")
            else:
                logger.warning(f"Файл изображения не найден: {image_path}")
                raise Exception("Image file not found")
        except Exception as e:
            # Если не получилось с изображением, отправляем только текст
            logger.warning(f"Не удалось отправить изображение: {e}")
            await bot_instance.send_message(
                chat_id=query.from_user.id,
                text=message_text,
                reply_markup=main_keyboard()
            )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await send_ad_to_non_admins(update)
        return

    if 'awaiting' in context.user_data:
        if context.user_data['awaiting'] == 'chat_link':
            link = update.message.text.strip()

            if link.startswith('@'):
                link = f"https://t.me/{link[1:]}"
            elif not link.startswith('http'):
                link = f"https://t.me/{link}"

            with join_lock:
                pending_invites.append(link)

            await update.message.reply_text(
                "✅ Ссылка добавлена в очередь на присоединение. Бот попытается присоединиться в ближайшее время.",
                reply_markup=main_keyboard()
            )
            del context.user_data['awaiting']

        elif context.user_data['awaiting'] == 'message' and 'editing_chat' in context.user_data:
            chat_id = context.user_data['editing_chat']
            new_message = update.message.text
            active_chats[chat_id]['message'] = new_message
            save_chats()
            await update.message.reply_text(
                f"✅ Сообщение для чата {chat_id} обновлено:\n{new_message}",
                reply_markup=chat_settings_keyboard(chat_id)
            )
            del context.user_data['awaiting']
            del context.user_data['editing_chat']

        elif context.user_data['awaiting'] in ['start_time', 'end_time'] and 'editing_chat' in context.user_data:
            chat_id = context.user_data['editing_chat']
            text = update.message.text
            try:
                datetime.strptime(text, "%H:%M")
                time_type = context.user_data['awaiting'].split('_')[0]
                active_chats[chat_id]['schedule'][time_type] = text
                save_chats()
                await update.message.reply_text(
                    f"✅ Время {time_type} установлено: {text}",
                    reply_markup=schedule_keyboard(chat_id)
                )
                del context.user_data['awaiting']
                del context.user_data['editing_chat']
            except ValueError:
                await update.message.reply_text("❌ Неверный формат времени. Используйте HH:MM")

async def new_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.bot.id in [user.id for user in update.message.new_chat_members]:
        chat_id = update.effective_chat.id
        if chat_id not in active_chats:
            active_chats[chat_id] = {
                'mode': 'stealth',
                'interval': 10,
                'is_spamming': False,
                'message': f"Сообщение для чата {update.effective_chat.title or chat_id}",
                'schedule': {
                    'start': '09:00',
                    'end': '18:00',
                    'enabled': False
                }
            }
            save_chats()

            quick_manage_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙ Управлять этим чатом", callback_data=f'chat_{chat_id}')],
                [InlineKeyboardButton("📋 Все чаты", callback_data='chat_list')],
                [InlineKeyboardButton("🏠 Главное меню", callback_data='back')]
            ])

            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🤖 Бот добавлен в новый чат!\n\n"
                     f"📌 Название: {update.effective_chat.title or 'Личные сообщения'}\n"
                     f"🆔 ID: {chat_id}\n\n"
                     f"Выберите действие:",
                reply_markup=quick_manage_keyboard
            )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Ошибка обновления: {context.error}")
    if update and update.effective_user:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"❌ Произошла ошибка: {str(context.error)[:500]}"
            )
        except:
            pass

async def main():
    global bot_instance

    logger.info("🚀 Запуск бота...")

    bot_instance = Bot(TOKEN)

    load_chats()
    await validate_saved_chats()

    asyncio.create_task(check_schedule())
    asyncio.create_task(process_invites())
    asyncio.create_task(periodic_chat_validation())

    application = Application.builder().token(TOKEN).build()

    # Добавляем обработчик ошибок
    application.add_error_handler(error_handler)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_chat_member))

    logger.info("✅ Бот запущен и готов к работе!")

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    try:
        await asyncio.sleep(float('inf'))
    except KeyboardInterrupt:
        logger.info("🛑 Остановка бота...")
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(main())
        loop.run_forever()
    except KeyboardInterrupt:
        logger.info("🛑 Программа остановлена пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
