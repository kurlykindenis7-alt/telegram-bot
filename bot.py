# -*- coding: utf-8 -*-
import logging
import json
import base64
import re
import os
import asyncio
from typing import List, Optional, Tuple, Union
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.error import Conflict

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

from openai import OpenAI

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

logging.basicConfig(level=logging.INFO)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not found in environment variables")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not found in environment variables")

client = OpenAI(api_key=OPENAI_API_KEY)
app = ApplicationBuilder().token(BOT_TOKEN).build()

# ================== ДАННЫЕ ==================
user_settings = {}  # Настройки уведомлений (пока не используем)
weekly_data = {}    # Накопление ежедневных данных (пока не используем)
answers = {}        # Ответы пользователей (для анкеты)
scheduled_tasks = {}  # Планируемые задачи уведомлений

# ================== СОСТОЯНИЯ ==================
START_MENU, QUESTION_FLOW, FINAL_MENU_STATE = range(3)

# ================== КЛАВИАТУРЫ ==================
START_KEYBOARD = ReplyKeyboardMarkup(
    [["Начать анкетирование"]],
    resize_keyboard=True,
    one_time_keyboard=False,
)

YES_NO = ReplyKeyboardMarkup([["да", "нет"]], resize_keyboard=True, one_time_keyboard=True)
SCALE_0_5 = ReplyKeyboardMarkup([[str(i) for i in range(0, 6)]], resize_keyboard=True, one_time_keyboard=True)

STOOL_FREQ = ReplyKeyboardMarkup(
    [
        ["2–3 раза в сутки", "1 раз в сутки"],
        ["1 раз в 1–2 дня", "1 раз в 2–3 дня", "1 раз в 3–5 дней"],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)

STOOL_TYPE = ReplyKeyboardMarkup(
    [
        ["оформленный, нормальный"],
        ["твёрдый", "жидкий"],
        ["иногда твёрдый, иногда жидкий", "чередуется"],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)

CYCLE = ReplyKeyboardMarkup(
    [["я мужчина", "я женщина, цикла нет"], ["регулярный", "нерегулярный"]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

APPETITE = ReplyKeyboardMarkup(
    [["нормальный", "повышенный", "пониженный"]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

ACTIVITY = ReplyKeyboardMarkup(
    [["нет", "1–2 раза в неделю", "3 и более раз в неделю"]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

CHECKIN_DAY_RESULT = ReplyKeyboardMarkup(
    [["Отлично", "Нормально", "Плохо"]], resize_keyboard=True, one_time_keyboard=True
)

MORNING_CHECKIN_MESSAGES = [
    ("🌅 Доброе утро! Быстрый чек-ин.\n\nКак спали? (0–5)", SCALE_0_5),
    ("Энергия сейчас? (0–5)", SCALE_0_5),
    ("💧 Напоминание: выпейте стакан воды прямо сейчас.", None),
]

DAY_CHECKIN_MESSAGES = [
    ("🏙 Дневной чек-ин.\n\nУровень энергии сейчас? (0–5)", SCALE_0_5),
    ("Уровень стресса? (0–5)", SCALE_0_5),
    ("💧 Напоминание: вода. Даже 300–500 мл уже меняют самочувствие.", None),
]

EVENING_CHECKIN_MESSAGES = [
    ("🌙 Вечерний итог дня.\n\nКак прошёл день?", CHECKIN_DAY_RESULT),
    ("Сон сегодня планируете во сколько лечь?", None),
    (
        "😴 Напоминание: постарайтесь лечь пораньше. "
        "Даже +30 минут сна часто дают ощутимый прирост энергии завтра.",
        None,
    ),
]

FINAL_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🔔 Подписаться на уведомления"],
        ["Связь с командой Екатерины 🌿"],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)

AFTER_SUBSCRIBE_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["Связь с командой Екатерины 🌿"],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)
CONTACT_URL = "https://t.me/doc_kazachkova_team"
CONTACT_INLINE_KEYBOARD = InlineKeyboardMarkup(
    [[InlineKeyboardButton("Связь с командой Екатерины 🌿", url=CONTACT_URL)]]
)
# ================== АНКЕТА ==================
QUESTIONS = [
    ("height_cm", "Ваш рост (см):", None),
    ("weight_kg", "Ваш вес (кг):", None),
    ("chest_cm", "Окружность груди (см):", None),
    ("waist_cm", "Окружность талии (см):", None),
    ("hips_cm", "Окружность бёдер (см):", None),
    ("stool_frequency", "Как часто у вас бывает стул?", "stool_freq"),
    ("stool_type", "Какой стул бывает чаще всего?", "stool_type"),
    ("cycle_status", "Менструальный цикл?", "cycle"),
    ("energy_level", "Оцените уровень энергии (0–5, где 0-Низкая 5-Все супер):", "scale"),
    ("stress_level", "Оцените уровень стресса (0–5, где 0-Много стресса 5-Все супер):", "scale"),
    ("sleep_quality", "Оцените качество сна (0–5, где 0-Плохо сплю 5-Все супер):", "scale"),
    ("focus_issues", "Снижение концентрации внимания?", "yes_no"),
    ("irritability_day", "Дневная раздражительность?", "yes_no"),
    ("sleepiness_day", "Дневная сонливость?", "yes_no"),
    ("appetite_level", "Какой аппетит вам больше подходит?", "appetite"),
    ("sweet_craving", "Есть ли тяга к сладкому?", "yes_no"),
    ("fat_craving", "Есть ли тяга к жирному?", "yes_no"),
    ("palpitations", "Одышка или учащённое сердцебиение?", "yes_no"),
    ("cold_hands_feet", "Зябкость рук и ног?", "yes_no"),
    ("skin_itch", "Кожный зуд?", "yes_no"),
    ("blue_sclera", "Голубоватый оттенок склер?", "yes_no"),
    ("headache", "Беспокоит ли вас головная боль?", "yes_no"),
    ("oily_skin", "Жирность кожи лица?", "yes_no"),
    ("dry_skin", "Сухость кожи лица?", "yes_no"),
    ("low_libido", "Сниженное либидо?", "yes_no"),
    ("vaginal_itch", "Вагинальный зуд (для женщин)?", "yes_no"),
    ("joint_pain", "Боли в суставах?", "yes_no"),
    ("abdominal_pain", "Боли или спазмы в животе?", "yes_no"),
    ("bloating", "Повышенное газообразование?", "yes_no"),
    ("hair_loss", "Выпадение волос?", "yes_no"),
    ("dry_mouth", "Сухость во рту?", "yes_no"),
    ("steps_daily", "Сколько шагов в среднем в день?", None),
    ("activity_level", "Есть ли дополнительная физическая активность?", "activity"),
]

# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================
SETTINGS_FILE = "user_settings.json"


def save_user_settings():
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(user_settings, f, ensure_ascii=False, indent=4)


def load_user_settings():
    global user_settings
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            user_settings = json.loads(content) if content else {}
    except FileNotFoundError:
        user_settings = {}
    except Exception:
        logging.exception("Не удалось загрузить user_settings.json")
        user_settings = {}


def get_keyboard(q_type):
    return {
        "yes_no": YES_NO,
        "scale": SCALE_0_5,
        "stool_freq": STOOL_FREQ,
        "stool_type": STOOL_TYPE,
        "cycle": CYCLE,
        "appetite": APPETITE,
        "activity": ACTIVITY,
    }.get(q_type, ReplyKeyboardRemove())


def calculate_bmi(height_cm, weight_kg):
    try:
        h = float(height_cm) / 100
        w = float(weight_kg)
        return round(w / (h * h), 1)
    except Exception:
        return None


def get_user_tz(chat_id: int):
    # фиксируем МСК: UTC+3 (чтобы не зависеть от времени хостинга)
    return timezone(timedelta(hours=3))


def now_in_tz(tz: timezone) -> datetime:
    return datetime.now(tz=tz)


def next_run_dt(tz: timezone, hour: int, minute: int) -> datetime:
    n = now_in_tz(tz)
    run = n.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if run <= n:
        run += timedelta(days=1)
    return run


# ================== ПЛАНИРОВЩИК УВЕДОМЛЕНИЙ (без JobQueue) ==================
# Важно: это работает без python-telegram-bot[job-queue]


async def _send_scheduled_messages(bot, chat_id, message_payloads):
    for text, markup in message_payloads:
        if markup:
            await bot.send_message(chat_id, text, reply_markup=markup)
        else:
            await bot.send_message(chat_id, text)


async def _daily_loop(bot, chat_id, tz: timezone, hour: int, minute: int, message_text):
    while True:
        run = next_run_dt(tz, hour, minute)
        delay = (run - now_in_tz(tz)).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            if isinstance(message_text, list):
                await _send_scheduled_messages(bot, chat_id, message_text)
            else:
                await bot.send_message(chat_id, message_text)
        except Exception:
            logging.exception("Не удалось отправить scheduled message chat_id=%s", chat_id)
        # чтобы точно не сработало два раза подряд в одну и ту же секунду
        await asyncio.sleep(1)


def schedule_daily_notifications(application, chat_id: int):
    # отменяем старые задачи, если были
    old = scheduled_tasks.get(chat_id, [])
    for t in old:
        t.cancel()

    tz = get_user_tz(chat_id)


    tasks = [
        application.create_task(_daily_loop(application.bot, chat_id, tz, 9, 30, MORNING_CHECKIN_MESSAGES)),
        application.create_task(_daily_loop(application.bot, chat_id, tz, 15, 0, DAY_CHECKIN_MESSAGES)),
        application.create_task(_daily_loop(application.bot, chat_id, tz, 20, 0, EVENING_CHECKIN_MESSAGES)),
    ]
    scheduled_tasks[chat_id] = tasks


# ================== АНКЕТИРОВАНИЕ ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_message = (
        "Здравствуйте!\nЯ — ваш индивидуальный помощник Клуба Здоровья 🌿\n\n"
        "Сейчас я задам несколько вопросов, чтобы понять текущее состояние организма "
        "и дать первые персональные рекомендации.\nАнкетирование займет 7–10 минут.\n"
        "Отвечайте честно, здесь нет неправильных ответов 💚"
    )
    try:
        file_path = Path(__file__).parent / "photo_2026-01-05_03-09-46.jpg"
        if file_path.exists():
            await update.message.reply_photo(photo=str(file_path), caption=text_message, reply_markup=START_KEYBOARD)
        else:
            await update.message.reply_text(text_message, reply_markup=START_KEYBOARD)
    except Exception:
        await update.message.reply_text(text_message, reply_markup=START_KEYBOARD)
    return START_MENU


async def start_survey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["q_index"] = 0
    _, text, q_type = QUESTIONS[0]
    await update.message.reply_text(text, reply_markup=get_keyboard(q_type))
    return QUESTION_FLOW


async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q_index = context.user_data.get("q_index", 0)
    key, _, _ = QUESTIONS[q_index]
    context.user_data[key] = (update.message.text or "").strip()

    q_index += 1
    if q_index >= len(QUESTIONS):
        return await summary(update, context)

    context.user_data["q_index"] = q_index
    _, text, q_type = QUESTIONS[q_index]
    await update.message.reply_text(text, reply_markup=get_keyboard(q_type))
    return QUESTION_FLOW


# ================== ОБРАБОТКА ФОТО ==================
async def analyze_food_image(image_bytes: bytes) -> dict:
    encoded = base64.b64encode(image_bytes).decode("utf-8")

    prompt = (
        "Ты нутрициолог. Проанализируй еду на фото.\n"
        "Верни СТРОГО JSON без пояснений.\n\n"
        "{"
        "\"dish\": str, "
        "\"calories\": number, "
        "\"protein\": number, "
        "\"fat\": number, "
        "\"carbs\": number, "
        "\"comment\": str"
        "}\n\n"
        "Если есть сомнения — укажи приблизительные значения."
    )

    # OpenAI клиент синхронный -> уводим в поток, чтобы не блокировать бота
    def _call():
        return client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{encoded}"},
                    ],
                }
            ],
        )

    response = await asyncio.to_thread(_call)

    text = (getattr(response, "output_text", None) or "").strip()

    if not text:
        # вытаскиваем руками из response.output
        try:
            parts = []
            for item in getattr(response, "output", []) or []:
                if getattr(item, "type", None) == "message":
                    for c in getattr(item, "content", []) or []:
                        if getattr(c, "type", None) in ("output_text", "text"):
                            parts.append(getattr(c, "text", "") or "")
            text = "\n".join([p for p in parts if p]).strip()
        except Exception:
            text = ""

    if not text:
        logging.error("Пустой ответ от модели в analyze_food_image")
        raise ValueError("Empty model output")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if m:
            return json.loads(m.group(0))
        logging.error("Не удалось извлечь JSON из ответа модели: %r", text[:500])
        raise


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("📸 Фото получено. Считаю калории и БЖУ…")

        if not update.message.photo:
            await update.message.reply_text("Не вижу фото 😕 Попробуйте отправить изображение еще раз.")
            return

        photo = update.message.photo[-1]
        file = await photo.get_file()
        image_bytes = await file.download_as_bytearray()

        result = await analyze_food_image(bytes(image_bytes))

        reply = (
            f"🍽 Блюдо: {result.get('dish','—')}\n\n"
            f"🔥 Калории: ~{result.get('calories','—')} ккал\n"
            f"🥩 Белки: ~{result.get('protein','—')} г\n"
            f"🧈 Жиры: ~{result.get('fat','—')} г\n"
            f"🍞 Углеводы: ~{result.get('carbs','—')} г\n\n"
            f"💬 {result.get('comment','')}\n\n"
            "⚠️ Значения приблизительные и основаны на визуальной оценке."
        )
        await update.message.reply_text(reply)

    except Exception:
        logging.exception("Ошибка анализа фото")
        await update.message.reply_text(
            "Не получилось распознать блюдо 😕\n"
            "Попробуйте сделать фото ближе и при хорошем освещении."
        )


# ================== ИТОГИ ==================
ZONE_TEXTS = {
    "zone_gut": "🟢 Пищеварение: сигналы нестабильной работы ЖКТ.",
    "zone_bmi": "🟢 Метаболический фокус: окружность талии выше нормы.",
    "zone_cycle": "🟢 Цикл: отмечена нерегулярность или отсутствие цикла.",
    "zone_appetite": "🟢 Аппетит и тяги: есть сигналы нарушения пищевого поведения.",
    "zone_symptoms": "🟢 Нервная система: сонливость, раздражительность, сложности с концентрацией.",
    "zone_skin": "🟢 Кожа: сухость, жирность, зуд.",
    "zone_libido": "🟢 Интимное здоровье: есть сигналы, на которые стоит обратить внимание.",
    "zone_pain": "🟢 Болевой фон: боли в голове, суставах или животе.",
    "zone_dry_mouth": "🟢 Сухость во рту.",
    "zone_red_flags": "🔴 Важно: симптомы требуют консультации специалиста.",
}


def calculate_general_score(u):
    score, max_score = 0, 0
    YES_NO_QUESTIONS = [
        "focus_issues", "irritability_day", "sleepiness_day", "palpitations", "cold_hands_feet",
        "skin_itch", "blue_sclera", "headache", "sweet_craving", "fat_craving",
        "oily_skin", "dry_skin", "low_libido", "vaginal_itch", "joint_pain", "abdominal_pain",
        "bloating", "hair_loss", "dry_mouth",
    ]
    SCALE_QUESTIONS = ["energy_level", "sleep_quality"]

    for q in YES_NO_QUESTIONS:
        max_score += 2
        if u.get(q) == "нет":
            score += 2

    scale_sum = 0
    for q in SCALE_QUESTIONS:
        max_score += 5
        try:
            val = int((u.get(q, "0") or "0")[0])
            score += val
            scale_sum += val
        except Exception:
            pass

    BUTTON_SCORE_MAP = {
        "stool_frequency": {"2–3 раза в сутки": 2, "1 раз в сутки": 1, "1 раз в 1–2 дня": 1, "1 раз в 2–3 дня": 0, "1 раз в 3–5 дней": 0},
        "activity_level": {"нет": 0, "1–2 раза в неделю": 2, "3 и более раз в неделю": 5},
        "appetite_level": {"нормальный": 5, "повышенный": 2, "пониженный": 2},
    }
    for q, mapping in BUTTON_SCORE_MAP.items():
        val = u.get(q)
        if val in mapping:
            score += mapping[val]
            max_score += max(mapping.values())

    health_score = round((scale_sum / (len(SCALE_QUESTIONS) * 5)) * 10) if SCALE_QUESTIONS else 0
    general_score = round((score / max_score) * 100) if max_score else 0
    return general_score, health_score


def calculate_zones(u):
    zones = {k: 0 for k in ZONE_TEXTS.keys()}
    if u.get("stool_frequency") in ["1 раз в 2–3 дня", "1 раз в 3–5 дней"] or u.get("bloating") == "да" or u.get("abdominal_pain") == "да":
        zones["zone_gut"] = 1
    try:
        if float(u.get("waist_cm", 0)) >= 85:
            zones["zone_bmi"] = 1
    except Exception:
        pass
    if u.get("cycle_status") in ["нерегулярный", "я женщина, цикла нет"]:
        zones["zone_cycle"] = 1
    if u.get("appetite_level") in ["повышенный", "пониженный"] or u.get("sweet_craving") == "да" or u.get("fat_craving") == "да":
        zones["zone_appetite"] = 1
    if u.get("focus_issues") == "да" or u.get("irritability_day") == "да" or u.get("sleepiness_day") == "да":
        zones["zone_symptoms"] = 1
    if u.get("oily_skin") == "да" or u.get("dry_skin") == "да" or u.get("skin_itch") == "да":
        zones["zone_skin"] = 1
    if u.get("low_libido") == "да" or u.get("vaginal_itch") == "да":
        zones["zone_libido"] = 1
    if u.get("headache") == "да" or u.get("joint_pain") == "да" or u.get("abdominal_pain") == "да":
        zones["zone_pain"] = 1
    if u.get("dry_mouth") == "да":
        zones["zone_dry_mouth"] = 1
    if u.get("blue_sclera") == "да" or u.get("palpitations") == "да":
        zones["zone_red_flags"] = 1
    return zones


# ================== ОТЧЕТ И ФИНАЛЬНОЕ МЕНЮ ==================
async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = context.user_data
    general_score, health_score = calculate_general_score(u)

    height, weight = u.get("height_cm"), u.get("weight_kg")
    bmi = calculate_bmi(height, weight)

    if bmi is not None:
        if bmi < 18.5:
            bmi_text = "недостаточная масса тела"
        elif bmi < 25:
            bmi_text = "норма"
        else:
            bmi_text = "избыточная масса тела"
    else:
        bmi_text = "не удалось рассчитать"

    try:
        water = round(float(weight) * 0.03, 1)
    except Exception:
        water = 2

    calories = 2000
    if bmi:
        if bmi < 18.5:
            calories = 2200
        elif bmi > 25:
            calories = 1800

    energy = u.get("energy_level", "0")
    sleep = u.get("sleep_quality", "0")

    zones = calculate_zones(u)
    zone_msgs = [ZONE_TEXTS[k] for k, v in zones.items() if v == 1]
    zones_text = "\n\n".join(zone_msgs) if zone_msgs else "🟢 По анкете не выявлено выраженных зон напряжения."

    result_message = (
        f"Супер! Я подвёл итоги теста:\n\n"
        f"🧠 Здоровье организма: {health_score}/10\n"
        f"⚡ Уровень энергии: {energy}/5\n"
        f"😴 Качество сна: {sleep}/5\n"
        f"📊 Общее состояние: {general_score}/100\n\n"
        f"📐 Ваш индекс массы тела: {bmi} — {bmi_text}\n"
        f"🔥 Рекомендационная калорийность: ~{calories} ккал/день\n"
        f"💧 Воды: не менее {water} л/день\n\n"
        f"Зоны внимания:\n\n{zones_text}"
    )
    await update.message.reply_text(result_message)

    final_message = (
        "✅ Анкета завершена!\n\n"
        "Теперь вам доступны функции:\n"
        "🍽 Подсчёт калорий по фото еды (работает только с VPN)\n"
        "🌅 Утренние опросы сна в 9:30 каждый день\n"
        "🌙 Вечерние итоги дня в 20;00 каждый день\n"
        "📊 Недельная статистика:\n"
        "• средний сон\n"
        "• уровень энергии\n"
        "• стресс\n"
        "• активность\n\n"
        "💧 Напоминания о воде\n"
        "😴 Рекомендации ко сну\n\n"
        "👇 Следующий шаг — настройка удобного времени уведомлений"
    )
    await update.message.reply_text(final_message, reply_markup=FINAL_KEYBOARD)
    await update.message.reply_text(
        "Нужна помощь? Нажмите кнопку ниже:",
        reply_markup=CONTACT_INLINE_KEYBOARD,
    )
    return FINAL_MENU_STATE


async def final_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    chat_id = update.effective_chat.id

    logging.info("final_menu_handler: text=%r chat_id=%s", text, chat_id)

if text == "🔔 Подписаться на уведомления":
    schedule_daily_notifications(context.application, chat_id)
    
    await update.message.reply_text(
        "Уведомления включены ✅\n\n"
        "📌 Каждый день вам будут приходить:\n"
        "🌅 09:30 — утренний опрос + напоминание выпить воды\n"
        "🕒 15:00 — дневной опрос + напоминание выпить воды\n"
        "🌙 20:00 — вечерний опрос + напоминание лечь спать пораньше\n\n"
        "Ничего дополнительно настраивать не нужно 💚",
        reply_markup=AFTER_SUBSCRIBE_KEYBOARD,  # убираем кнопку подписки
    )
          await update.message.reply_text(
            "Связь с командой доступна по кнопке ниже:",
            reply_markup=CONTACT_INLINE_KEYBOARD,
        )
        return FINAL_MENU_STATE
        return FINAL_MENU_STATE

if text == "Связь с командой Екатерины 🌿":
        await update.message.reply_text(
        "Связь с командой:",
        reply_markup=CONTACT_INLINE_KEYBOARD,
    )
    return FINAL_MENU_STATE

    await update.message.reply_text(
        "Пожалуйста, выберите действие кнопкой ниже.",
        reply_markup=FINAL_KEYBOARD,
    )
    return FINAL_MENU_STATE


# ================== СТАРТ / WEBHOOK СБРОС ==================
async def on_startup(application):
    # чтобы не было конфликтов webhook vs polling
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        logging.exception("delete_webhook failed")


app.post_init = on_startup


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    logging.exception("Unhandled exception while handling update", exc_info=err)
    if isinstance(err, Conflict):
        logging.error(
            "Conflict: another bot instance is already polling getUpdates. "
            "Stopping this instance."
        )
        await context.application.stop()

# ================== ХЕНДЛЕРЫ ==================
survey_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        START_MENU: [MessageHandler(filters.Regex(r"^Начать анкетирование$"), start_survey)],
        QUESTION_FLOW: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_answer)],
        FINAL_MENU_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, final_menu_handler)],
    },
    fallbacks=[],
)

# ВАЖНО: фото-хендлер отдельно и выше conversation, чтобы точно срабатывал
app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
app.add_handler(survey_handler)
app.add_error_handler(error_handler)

load_user_settings()

if __name__ == "__main__":
    print("Бот запущен")
    # ВАЖНО: конфликт "terminated by other getUpdates request" НЕ лечится кодом,
    # он лечится тем, что запущен только 1 экземпляр бота.
    app.run_polling(drop_pending_updates=True)
