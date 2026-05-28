from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from flask import Flask, abort, request
from pymongo import ASCENDING, MongoClient, ReturnDocument
from telebot import TeleBot
from telebot.apihelper import ApiTelegramException
from telebot.types import (
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)


load_dotenv()


def csv_ints(value: str) -> set[int]:
    result: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if item:
            result.add(int(item))
    return result


@dataclass(frozen=True)
class Settings:
    bot_token: str
    mongodb_uri: str
    mongodb_db: str
    webhook_secret: str
    public_base_url: str
    admin_ids: set[int]
    default_emoji_id: str


settings = Settings(
    bot_token=os.getenv("BOT_TOKEN", "8808790182:AAHwqdXOdcCJ-CXINpDA4FfAKogPTvms-c4"),
    mongodb_uri=os.getenv("MONGODB_URI", "mongodb+srv://bmurodova550_db_user:javohir2011@kinobot1.vlz17q5.mongodb.net/?appName=kinobot1"),
    mongodb_db=os.getenv("MONGODB_DB", "qorovul_like_bot"),
    webhook_secret=os.getenv("WEBHOOK_SECRET", "change-this-secret"),
    public_base_url=os.getenv("PUBLIC_BASE_URL", ""),
    admin_ids=csv_ints(os.getenv("ADMIN_IDS", "6968399046")),
    default_emoji_id=os.getenv("DEFAULT_EMOJI_ID", "5458794766248459827"),
)

if not settings.bot_token:
    raise RuntimeError("BOT_TOKEN .env ichida yozilishi kerak.")


bot = TeleBot(settings.bot_token, parse_mode="HTML", threaded=False)
app = Flask(__name__)
client = MongoClient(settings.mongodb_uri)
db = client[settings.mongodb_db]

users = db.users
groups = db.groups
mandatory_channels = db.mandatory_channels

admin_state: dict[int, dict[str, Any]] = {}


class StyledInlineKeyboardButton(InlineKeyboardButton):
    """Bot API color/icon fields with graceful fallback for older libraries."""

    def __init__(
        self,
        text: str,
        *,
        style: str | None = None,
        icon_custom_emoji_id: str | None = None,
        **kwargs,
    ):
        super().__init__(text=text, **kwargs)
        self.style = style
        self.icon_custom_emoji_id = icon_custom_emoji_id

    def to_dict(self):
        data = super().to_dict()
        if self.style:
            data["style"] = self.style
        if self.icon_custom_emoji_id:
            data["icon_custom_emoji_id"] = self.icon_custom_emoji_id
        return data


def ibutton(
    text: str,
    *,
    style: str = "primary",
    icon_custom_emoji_id: str | None = None,
    **kwargs,
) -> StyledInlineKeyboardButton:
    return StyledInlineKeyboardButton(
        text,
        style=style,
        icon_custom_emoji_id=icon_custom_emoji_id or settings.default_emoji_id or None,
        **kwargs,
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_indexes() -> None:
    users.create_index("telegram_id", unique=True)
    groups.create_index("chat_id", unique=True)
    mandatory_channels.create_index("chat_id", unique=True)
    mandatory_channels.create_index([("active", ASCENDING), ("chat_id", ASCENDING)])


def is_admin(user_id: int) -> bool:
    return bool(settings.admin_ids) and user_id in settings.admin_ids


def save_user(message: Message) -> dict:
    payload = {
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name,
        "updated_at": utcnow(),
    }
    return users.find_one_and_update(
        {"telegram_id": message.from_user.id},
        {"$set": payload, "$setOnInsert": {"created_at": utcnow()}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        ibutton("📊 Statistika", callback_data="admin:stats", style="primary"),
        ibutton("📌 Obuna qo'shish", callback_data="admin:add_channel", style="success"),
        ibutton("🧾 Obunalar ro'yxati", callback_data="admin:list_channels", style="primary"),
        ibutton("🗑 Obunani o'chirish", callback_data="admin:del_channel", style="danger"),
        ibutton("👥 Userlarga xabar", callback_data="admin:broadcast_users", style="success"),
        ibutton("👤 Bitta userga xabar", callback_data="admin:send_user", style="primary"),
        ibutton("📣 Guruhlarga reklama", callback_data="admin:broadcast_groups", style="success"),
        ibutton("💬 Bitta guruhga xabar", callback_data="admin:send_group", style="primary"),
        ibutton("🏘 Admin guruhlar", callback_data="admin:list_groups", style="primary"),
        ibutton("❌ Yopish", callback_data="admin:close", style="danger"),
    )
    return markup


def user_menu_keyboard() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        ibutton("➕ Botni guruhga admin qilish", url=add_group_url(), style="success"),
        ibutton("✅ Obunani tekshirish", callback_data="subcheck:private", style="primary"),
    )
    return markup


def subscribe_keyboard() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=1)
    for channel in mandatory_channels.find({"active": True}).sort("created_at", ASCENDING):
        title = channel.get("title") or channel.get("chat_id")
        url = channel.get("invite_link") or channel.get("url")
        if url:
            markup.add(ibutton(f"📌 {title}", url=url, style="success"))
    markup.add(ibutton("✅ Tekshirish", callback_data="subcheck:private", style="primary"))
    return markup


def add_group_url() -> str:
    username = bot.get_me().username
    permissions = "delete_messages+manage_chat+restrict_members"
    return f"https://t.me/{username}?startgroup=setup&admin={permissions}"


def normalize_channel(raw: str) -> tuple[str, str]:
    text = raw.strip()
    if text.startswith("https://t.me/"):
        username = text.removeprefix("https://t.me/").split("/", 1)[0]
        return f"@{username}", f"https://t.me/{username}"
    if text.startswith("t.me/"):
        username = text.removeprefix("t.me/").split("/", 1)[0]
        return f"@{username}", f"https://t.me/{username}"
    if text.startswith("@"):
        return text, f"https://t.me/{text[1:]}"
    return text, text


def bot_is_admin(chat_id: str | int) -> bool:
    try:
        member = bot.get_chat_member(chat_id, bot.get_me().id)
        return member.status in {"administrator", "creator"}
    except ApiTelegramException:
        return False


def user_is_subscribed(user_id: int) -> bool:
    active_channels = list(mandatory_channels.find({"active": True}))
    if not active_channels:
        return True
    for channel in active_channels:
        try:
            member = bot.get_chat_member(channel["chat_id"], user_id)
            if member.status in {"left", "kicked"}:
                return False
        except ApiTelegramException:
            return False
    return True


def send_need_subscribe(chat_id: int, user_id: int, *, reply_to_message_id: int | None = None) -> None:
    text = (
        "🔐 <b>Majburiy obuna</b>\n\n"
        "Xabar yozish yoki botdan foydalanish uchun avval quyidagi kanal(lar)ga obuna bo'ling."
    )
    bot.send_message(
        chat_id,
        text,
        reply_markup=subscribe_keyboard(),
        reply_to_message_id=reply_to_message_id,
        disable_web_page_preview=True,
    )


def copy_to_chat(target_chat_id: int, source: Message) -> bool:
    try:
        bot.copy_message(target_chat_id, source.chat.id, source.message_id)
        return True
    except ApiTelegramException:
        return False


def broadcast_to_users(source: Message) -> tuple[int, int]:
    ok = 0
    fail = 0
    for user in users.find({}, {"telegram_id": 1}):
        if copy_to_chat(user["telegram_id"], source):
            ok += 1
        else:
            fail += 1
    return ok, fail


def broadcast_to_groups(source: Message) -> tuple[int, int]:
    ok = 0
    fail = 0
    query = {"bot_admin": True, "active": True}
    for group in groups.find(query, {"chat_id": 1}):
        if copy_to_chat(group["chat_id"], source):
            ok += 1
        else:
            fail += 1
    return ok, fail


@bot.message_handler(commands=["start"])
def start(message: Message) -> None:
    if message.chat.type != "private":
        return
    save_user(message)
    if not user_is_subscribed(message.from_user.id):
        send_need_subscribe(message.chat.id, message.from_user.id)
        return
    text = (
        "🛡 <b>Qorovul bot tayyor</b>\n\n"
        "Botni guruhingizga qo'shib admin qiling. Majburiy obunaga kirmagan foydalanuvchilarning "
        "xabarlari guruhda avtomatik o'chiriladi."
    )
    bot.send_message(message.chat.id, text, reply_markup=user_menu_keyboard())


@bot.message_handler(commands=["admin"])
def admin(message: Message) -> None:
    if message.chat.type != "private" or not is_admin(message.from_user.id):
        return
    save_user(message)
    admin_state.pop(message.from_user.id, None)
    bot.send_message(message.chat.id, "🛠 <b>Admin panel</b>", reply_markup=admin_panel_keyboard())


@bot.message_handler(commands=["id"])
def chat_id(message: Message) -> None:
    if message.chat.type in {"group", "supergroup"}:
        bot.reply_to(message, f"💬 Guruh ID: <code>{message.chat.id}</code>")


@bot.callback_query_handler(func=lambda call: call.data.startswith("admin:"))
def admin_callbacks(call) -> None:
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Ruxsat yo'q.", show_alert=True)
        return

    action = call.data.split(":", 1)[1]
    admin_state.pop(call.from_user.id, None)

    if action == "close":
        bot.answer_callback_query(call.id, "Yopildi.")
        bot.delete_message(call.message.chat.id, call.message.message_id)
        return

    if action == "stats":
        text = (
            "📊 <b>Statistika</b>\n\n"
            f"👥 Foydalanuvchilar: <b>{users.count_documents({})}</b>\n"
            f"🏘 Admin guruhlar: <b>{groups.count_documents({'bot_admin': True, 'active': True})}</b>\n"
            f"📌 Majburiy obunalar: <b>{mandatory_channels.count_documents({'active': True})}</b>"
        )
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=admin_panel_keyboard())
        return

    if action == "add_channel":
        admin_state[call.from_user.id] = {"step": "add_channel"}
        bot.send_message(
            call.message.chat.id,
            "📌 Majburiy obuna kanalini yuboring.\n\nMasalan: <code>@kanal</code> yoki <code>https://t.me/kanal</code>\n"
            "Bot o'sha kanalda admin bo'lsa tekshirish aniq ishlaydi.",
        )
        bot.answer_callback_query(call.id)
        return

    if action == "list_channels":
        lines = ["🧾 <b>Majburiy obunalar</b>"]
        for channel in mandatory_channels.find({"active": True}).sort("created_at", ASCENDING):
            status = "✅ admin" if channel.get("bot_admin") else "⚠️ admin emas"
            lines.append(f"\n<code>{channel['chat_id']}</code> - {channel.get('title', 'Kanal')} - {status}")
        if len(lines) == 1:
            lines.append("\nHozircha kanal yo'q.")
        bot.edit_message_text("\n".join(lines), call.message.chat.id, call.message.message_id, reply_markup=admin_panel_keyboard())
        return

    if action == "del_channel":
        admin_state[call.from_user.id] = {"step": "del_channel"}
        bot.send_message(call.message.chat.id, "🗑 O'chiriladigan kanal username yoki ID sini yuboring.")
        bot.answer_callback_query(call.id)
        return

    if action == "broadcast_users":
        admin_state[call.from_user.id] = {"step": "broadcast_users"}
        bot.send_message(call.message.chat.id, "👥 Userlarga yuboriladigan xabar/reklamani jo'nating.")
        bot.answer_callback_query(call.id)
        return

    if action == "send_user":
        admin_state[call.from_user.id] = {"step": "send_user_id"}
        bot.send_message(call.message.chat.id, "👤 Xabar yuboriladigan user ID ni yuboring.")
        bot.answer_callback_query(call.id)
        return

    if action == "broadcast_groups":
        admin_state[call.from_user.id] = {"step": "broadcast_groups"}
        bot.send_message(call.message.chat.id, "📣 Admin qilingan hamma guruhlarga yuboriladigan reklamani jo'nating.")
        bot.answer_callback_query(call.id)
        return

    if action == "send_group":
        admin_state[call.from_user.id] = {"step": "send_group_id"}
        bot.send_message(call.message.chat.id, "💬 Xabar yuboriladigan guruh ID ni yuboring. Guruhda /id yozib bilib olasiz.")
        bot.answer_callback_query(call.id)
        return

    if action == "list_groups":
        lines = ["🏘 <b>Bot admin qilingan guruhlar</b>"]
        for group in groups.find({"bot_admin": True, "active": True}).sort("updated_at", ASCENDING):
            lines.append(f"\n<code>{group['chat_id']}</code> - {group.get('title', 'Guruh')}")
        if len(lines) == 1:
            lines.append("\nHozircha guruh yo'q.")
        bot.edit_message_text("\n".join(lines), call.message.chat.id, call.message.message_id, reply_markup=admin_panel_keyboard())


@bot.callback_query_handler(func=lambda call: call.data.startswith("subcheck:"))
def subcheck(call) -> None:
    if user_is_subscribed(call.from_user.id):
        bot.answer_callback_query(call.id, "✅ Obuna tasdiqlandi.")
        if call.message.chat.type == "private":
            bot.edit_message_text(
                "✅ Obuna tasdiqlandi. Botdan foydalanishingiz mumkin.",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=user_menu_keyboard(),
            )
    else:
        bot.answer_callback_query(call.id, "📌 Avval kanallarga obuna bo'ling.", show_alert=True)


@bot.my_chat_member_handler()
def my_chat_member(update: ChatMemberUpdated) -> None:
    chat = update.chat
    new_status = update.new_chat_member.status
    if chat.type not in {"group", "supergroup"}:
        return
    groups.update_one(
        {"chat_id": chat.id},
        {
            "$set": {
                "chat_id": chat.id,
                "title": chat.title,
                "type": chat.type,
                "bot_admin": new_status in {"administrator", "creator"},
                "active": new_status not in {"left", "kicked"},
                "updated_at": utcnow(),
            },
            "$setOnInsert": {"created_at": utcnow()},
        },
        upsert=True,
    )
    if new_status in {"administrator", "creator"}:
        for admin_id in settings.admin_ids:
            try:
                bot.send_message(admin_id, f"✅ Bot admin qilindi:\n<code>{chat.id}</code> - {chat.title}")
            except ApiTelegramException:
                pass


@bot.message_handler(content_types=[
    "text",
    "audio",
    "document",
    "photo",
    "sticker",
    "video",
    "video_note",
    "voice",
    "contact",
    "location",
    "venue",
    "animation",
    "dice",
])
def messages(message: Message) -> None:
    if message.from_user and not message.from_user.is_bot:
        save_user(message)

    if message.chat.type in {"group", "supergroup"}:
        enforce_group_subscription(message)
        return

    if message.chat.type != "private":
        return

    if is_admin(message.from_user.id) and handle_admin_state(message):
        return

    if not user_is_subscribed(message.from_user.id):
        send_need_subscribe(message.chat.id, message.from_user.id)
        return

    bot.send_message(message.chat.id, "🛡 Bot menyusi", reply_markup=user_menu_keyboard())


def handle_admin_state(message: Message) -> bool:
    state = admin_state.get(message.from_user.id)
    if not state:
        return False

    step = state["step"]

    if step == "add_channel":
        chat_id, url = normalize_channel(message.text or "")
        title = chat_id
        bot_admin = bot_is_admin(chat_id)
        try:
            chat = bot.get_chat(chat_id)
            title = chat.title or chat.username or chat_id
            if chat.username:
                url = f"https://t.me/{chat.username}"
        except ApiTelegramException:
            pass
        mandatory_channels.update_one(
            {"chat_id": chat_id},
            {
                "$set": {
                    "chat_id": chat_id,
                    "title": title,
                    "url": url,
                    "invite_link": url,
                    "bot_admin": bot_admin,
                    "active": True,
                    "updated_at": utcnow(),
                },
                "$setOnInsert": {"created_at": utcnow()},
            },
            upsert=True,
        )
        admin_state.pop(message.from_user.id, None)
        status = "✅ Bot kanalda admin." if bot_admin else "⚠️ Bot kanalda admin emas, tekshirish ishlamasligi mumkin."
        bot.send_message(message.chat.id, f"📌 Kanal qo'shildi: <code>{chat_id}</code>\n{status}", reply_markup=admin_panel_keyboard())
        return True

    if step == "del_channel":
        chat_id, _ = normalize_channel(message.text or "")
        result = mandatory_channels.update_one({"chat_id": chat_id}, {"$set": {"active": False, "updated_at": utcnow()}})
        admin_state.pop(message.from_user.id, None)
        text = "🗑 Kanal o'chirildi." if result.matched_count else "Kanal topilmadi."
        bot.send_message(message.chat.id, text, reply_markup=admin_panel_keyboard())
        return True

    if step == "broadcast_users":
        ok, fail = broadcast_to_users(message)
        admin_state.pop(message.from_user.id, None)
        bot.send_message(message.chat.id, f"👥 Userlarga yuborildi.\n✅ {ok} ta\n❌ {fail} ta", reply_markup=admin_panel_keyboard())
        return True

    if step == "broadcast_groups":
        ok, fail = broadcast_to_groups(message)
        admin_state.pop(message.from_user.id, None)
        bot.send_message(message.chat.id, f"📣 Guruhlarga reklama yuborildi.\n✅ {ok} ta\n❌ {fail} ta", reply_markup=admin_panel_keyboard())
        return True

    if step == "send_user_id":
        try:
            target_id = int((message.text or "").strip())
        except ValueError:
            bot.send_message(message.chat.id, "User ID raqam bo'lishi kerak.")
            return True
        admin_state[message.from_user.id] = {"step": "send_user_message", "target_id": target_id}
        bot.send_message(message.chat.id, "Endi shu userga yuboriladigan xabarni jo'nating.")
        return True

    if step == "send_user_message":
        target_id = state["target_id"]
        ok = copy_to_chat(target_id, message)
        admin_state.pop(message.from_user.id, None)
        text = "✅ Userga xabar yuborildi." if ok else "❌ Userga yuborib bo'lmadi."
        bot.send_message(message.chat.id, text, reply_markup=admin_panel_keyboard())
        return True

    if step == "send_group_id":
        try:
            target_id = int((message.text or "").strip())
        except ValueError:
            bot.send_message(message.chat.id, "Guruh ID raqam bo'lishi kerak. Masalan: -1001234567890")
            return True
        admin_state[message.from_user.id] = {"step": "send_group_message", "target_id": target_id}
        bot.send_message(message.chat.id, "Endi shu guruhga yuboriladigan xabar/reklamani jo'nating.")
        return True

    if step == "send_group_message":
        target_id = state["target_id"]
        ok = copy_to_chat(target_id, message)
        admin_state.pop(message.from_user.id, None)
        text = "✅ Guruhga xabar yuborildi." if ok else "❌ Guruhga yuborib bo'lmadi."
        bot.send_message(message.chat.id, text, reply_markup=admin_panel_keyboard())
        return True

    return False


def enforce_group_subscription(message: Message) -> None:
    if not message.from_user or message.from_user.is_bot:
        return
    if is_admin(message.from_user.id):
        return
    if user_is_subscribed(message.from_user.id):
        return
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except ApiTelegramException:
        pass
    try:
        send_need_subscribe(message.chat.id, message.from_user.id)
    except ApiTelegramException:
        pass


@app.get("/")
def healthcheck():
    return {"ok": True, "service": "Qorovul Like Bot"}


@app.post(f"/webhook/{settings.webhook_secret}")
def telegram_webhook():
    if request.headers.get("content-type") != "application/json":
        abort(403)
    update = Update.de_json(request.get_data().decode("utf-8"))
    bot.process_new_updates([update])
    return {"ok": True}


@app.cli.command("set-webhook")
def set_webhook():
    if not settings.public_base_url:
        raise RuntimeError("PUBLIC_BASE_URL .env ichida yozilishi kerak.")
    url = f"{settings.public_base_url.rstrip('/')}/webhook/{settings.webhook_secret}"
    bot.remove_webhook()
    bot.set_webhook(url=url)
    print(f"Webhook o'rnatildi: {url}")


def start_polling() -> None:
    bot.remove_webhook()
    print("Polling ishga tushdi. To'xtatish uchun CTRL+C bosing.")
    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30,
        allowed_updates=["message", "callback_query", "my_chat_member"],
    )


@app.cli.command("run-polling")
def run_polling():
    start_polling()


ensure_indexes()


if __name__ == "__main__":
    if os.getenv("RUN_MODE", "flask").lower() == "polling":
        start_polling()
    else:
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
