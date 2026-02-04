import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional, Dict, Set, Tuple, List

import aiosqlite
from aiohttp import web

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

# =======================
# SOZLAMALAR
# =======================
TOKEN = "8478058553:AAGR0eMotTJy5_zM-65bHGGsm2ImcOKKfeE"
ADMINS = {5815294733}

# Majburiy obuna kanallari
REQUIRED_CHATS = ["@bypass_bypasss", "@kino_olami_kinolar", "@telefon_olami_12_viloyat"]

# Kino topilmasa yo'naltirish
MOVIES_CHANNEL = "@kino_olami_kinolar"

DB_PATH = "kino.db"
dp = Dispatcher()


def is_admin(uid: int) -> bool:
    return uid in ADMINS


# =======================
# DB
# =======================
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY,
  joined_at INTEGER
);

CREATE TABLE IF NOT EXISTS movies (
  code TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  file_id TEXT NOT NULL,
  added_by INTEGER,
  added_at INTEGER
);
"""

async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_SQL)
        await db.commit()

async def db_add_user(uid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users(user_id, joined_at) VALUES(?,?)",
            (uid, int(time.time()))
        )
        await db.commit()

async def db_stats() -> Tuple[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        (u_cnt,) = await cur.fetchone()
        cur = await db.execute("SELECT COUNT(*) FROM movies")
        (m_cnt,) = await cur.fetchone()
        return int(u_cnt), int(m_cnt)

async def db_add_movie(code: str, title: str, file_id: str, added_by: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO movies(code, title, file_id, added_by, added_at) VALUES(?,?,?,?,?)",
            (code, title, file_id, added_by, int(time.time()))
        )
        await db.commit()

async def db_get_movie(code: str) -> Optional[Tuple[str, str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT title, file_id FROM movies WHERE code = ?", (code,))
        row = await cur.fetchone()
        if not row:
            return None
        return row[0], row[1]

async def db_delete_movie(code: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM movies WHERE code = ?", (code,))
        await db.commit()
        return cur.rowcount > 0

async def db_all_users() -> List[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users")
        rows = await cur.fetchall()
        return [int(r[0]) for r in rows]


# =======================
# MAJBURI OBUNA
# =======================
ALLOWED_STATUSES = {"member", "administrator", "creator"}

async def is_subscribed(bot: Bot, chat_id: str | int, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return m.status in ALLOWED_STATUSES
    except (TelegramForbiddenError, TelegramBadRequest):
        return False

async def check_required(bot: Bot, user_id: int) -> Tuple[bool, List[str | int]]:
    missing = []
    for ch in REQUIRED_CHATS:
        ok = await is_subscribed(bot, ch, user_id)
        if not ok:
            missing.append(ch)
    return (len(missing) == 0), missing

def kb_join(missing: List[str | int]):
    kb = InlineKeyboardBuilder()
    for ch in missing:
        if isinstance(ch, str) and ch.startswith("@"):
            kb.button(text=f"➕ {ch}", url=f"https://t.me/{ch[1:]}")
    kb.button(text="✅ Tekshirish", callback_data="check_join")
    kb.adjust(1)
    return kb.as_markup()

async def require_sub_message(bot: Bot, message: types.Message) -> bool:
    ok, missing = await check_required(bot, message.from_user.id)
    if not ok:
        await message.answer(
            "❗ Botdan foydalanish uchun quyidagi kanal(lar)ga obuna bo‘ling.\n"
            "So‘ng ✅ Tekshirish ni bosing.",
            reply_markup=kb_join(missing)
        )
        return False
    return True

async def require_sub_callback(bot: Bot, call: types.CallbackQuery) -> bool:
    ok, missing = await check_required(bot, call.from_user.id)
    if not ok:
        await call.message.edit_text(
            "❗ Hali ham hammasiga obuna bo‘lmagansiz.\n"
            "Obuna bo‘ling va ✅ Tekshirish ni bosing:",
            reply_markup=kb_join(missing)
        )
        await call.answer()
        return False
    return True


# =======================
# KEYBOARDS
# =======================
def kb_user():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🎬 Kino olish")
    kb.button(text="📢 Kinolar bo‘lim")
    kb.button(text="ℹ️ Yordam")
    kb.adjust(2, 1)
    return kb.as_markup(resize_keyboard=True)

def kb_admin():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🎬 Kino olish")
    kb.button(text="📢 Kinolar bo‘lim")
    kb.button(text="ℹ️ Yordam")
    kb.button(text="➕ Kino qo‘shish")
    kb.button(text="❌ Kino o‘chirish")
    kb.button(text="🔎 Admin kino qidirish")
    kb.button(text="📊 Statistika")
    kb.button(text="📢 Broadcast")
    kb.adjust(2, 1, 2, 1, 2)
    return kb.as_markup(resize_keyboard=True)

def kb_channel_link():
    kb = InlineKeyboardBuilder()
    if MOVIES_CHANNEL.startswith("@"):
        kb.button(text="📢 Kanalga kirish", url=f"https://t.me/{MOVIES_CHANNEL[1:]}")
    kb.adjust(1)
    return kb.as_markup()


# =======================
# FLOWS
# =======================
@dataclass
class AddFlow:
    step: int = 1
    code: str = ""
    title: str = ""

ADD_FLOW: Dict[int, AddFlow] = {}
DEL_FLOW: Set[int] = set()
ADMIN_SEARCH_FLOW: Set[int] = set()
BC_WAIT: Set[int] = set()

# ✅ User kino olish rejimi (faqat tugmadan keyin kod qabul qilamiz)
USER_GET_FLOW: Set[int] = set()


# =======================
# BROADCAST
# =======================
async def send_broadcast(bot: Bot, from_message: types.Message) -> Tuple[int, int]:
    users = await db_all_users()
    ok = 0
    fail = 0
    for uid in users:
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=from_message.chat.id, message_id=from_message.message_id)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.03)
    return ok, fail


# =======================
# EXTRA: /id va bot qo'shilganda id yuborish
# =======================
@dp.message(Command("id"))
async def cmd_id(message: types.Message):
    await message.answer(f"🆔 Chat ID: `{message.chat.id}`", parse_mode="Markdown")

@dp.my_chat_member()
async def bot_added(event: types.ChatMemberUpdated, bot: Bot):
    # bot qo'shilganda adminlarga chat id yuboradi
    if event.new_chat_member.user.id != (await bot.me()).id:
        return
    chat = event.chat
    text = (
        "✅ Bot chatga qo‘shildi!\n"
        f"📌 Nomi: {chat.title or chat.username or 'Noma’lum'}\n"
        f"🆔 Chat ID: {chat.id}\n"
        f"👤 Turi: {chat.type}"
    )
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass


# =======================
# START + CHECK BUTTON
# =======================
@dp.message(CommandStart())
async def start_cmd(message: types.Message, bot: Bot):
    ok, missing = await check_required(bot, message.from_user.id)
    if not ok:
        await message.answer(
            "❗ Botdan foydalanish uchun quyidagi kanal(lar)ga obuna bo‘ling.\nSo‘ng ✅ Tekshirish ni bosing.",
            reply_markup=kb_join(missing)
        )
        return

    await db_add_user(message.from_user.id)
    if is_admin(message.from_user.id):
        await message.answer("✅ Admin menyu", reply_markup=kb_admin())
    else:
        await message.answer("✅ Xush kelibsiz!", reply_markup=kb_user())

@dp.callback_query(F.data == "check_join")
async def check_join_cb(call: types.CallbackQuery, bot: Bot):
    ok = await require_sub_callback(bot, call)
    if not ok:
        return

    await db_add_user(call.from_user.id)
    if is_admin(call.from_user.id):
        await call.message.edit_text("✅ Obuna tasdiqlandi! Admin menyu ochildi.")
        await call.message.answer("✅ Admin menyu", reply_markup=kb_admin())
    else:
        await call.message.edit_text("✅ Obuna tasdiqlandi! Endi botdan foydalanishingiz mumkin.")
        await call.message.answer("✅ Menyu", reply_markup=kb_user())
    await call.answer()


# =======================
# MENYU TUGMALARI (hammasida doimiy majburiy obuna)
# =======================
@dp.message(F.text == "📢 Kinolar bo‘lim")
async def movies_channel(message: types.Message, bot: Bot):
    if not await require_sub_message(bot, message):
        return
    if MOVIES_CHANNEL.startswith("@"):
        await message.answer("📢 Kinolar kanali:", reply_markup=kb_channel_link())
    else:
        await message.answer("📢 Kinolar kanali sozlanmagan.")

@dp.message(F.text == "ℹ️ Yordam")
async def help_cmd(message: types.Message, bot: Bot):
    if not await require_sub_message(bot, message):
        return
    await message.answer(
        "📌 Kino olish🎬 tugmasini bosing, keyin kod yuboring.\n"
        "📌 bu bot kino lar va zavq uchun.\n"
        "📌 Admin @fon_abidjan reklama uchun."
    )

@dp.message(F.text == "🎬 Kino olish")
async def kino_olish(message: types.Message, bot: Bot):
    if not await require_sub_message(bot, message):
        return
    USER_GET_FLOW.add(message.from_user.id)
    await message.answer("🎬 Kino kodini yuboring")

# --- Admin tugmalar
@dp.message(F.text == "➕ Kino qo‘shish")
async def add_start(message: types.Message, bot: Bot):
    if not await require_sub_message(bot, message):
        return
    if not is_admin(message.from_user.id):
        return
    ADD_FLOW[message.from_user.id] = AddFlow(step=1)
    await message.answer("1/3) Kino kodini yuboring (masalan: 102 yoki 134)", reply_markup=kb_admin())

@dp.message(F.text == "❌ Kino o‘chirish")
async def del_start(message: types.Message, bot: Bot):
    if not await require_sub_message(bot, message):
        return
    if not is_admin(message.from_user.id):
        return
    DEL_FLOW.add(message.from_user.id)
    await message.answer("❌ O‘chirish uchun kino kodini yuboring.", reply_markup=kb_admin())

@dp.message(F.text == "📊 Statistika")
async def stats_cmd(message: types.Message, bot: Bot):
    if not await require_sub_message(bot, message):
        return
    if not is_admin(message.from_user.id):
        return
    u_cnt, m_cnt = await db_stats()
    await message.answer(f"👥 Users: {u_cnt}\n🎬 Kinolar: {m_cnt}", reply_markup=kb_admin())

@dp.message(F.text == "📢 Broadcast")
async def bc_start(message: types.Message, bot: Bot):
    if not await require_sub_message(bot, message):
        return
    if not is_admin(message.from_user.id):
        return
    BC_WAIT.add(message.from_user.id)
    await message.answer("📢 Tarqatish uchun xabar yuboring (matn/rasm/video).", reply_markup=kb_admin())

@dp.message(F.text == "🔎 Admin kino qidirish")
async def admin_search_start(message: types.Message, bot: Bot):
    if not await require_sub_message(bot, message):
        return
    if not is_admin(message.from_user.id):
        return
    ADMIN_SEARCH_FLOW.add(message.from_user.id)
    await message.answer("🔎 Kino kodini kiriting:", reply_markup=kb_admin())


# =======================
# UNIVERSAL HANDLER (barcha xabarlar)
# =======================
@dp.message()
async def universal(message: types.Message, bot: Bot):
    uid = message.from_user.id

    # Doimiy majburiy obuna tekshiruv
    if not await require_sub_message(bot, message):
        return

    # user DBga yozib qo'yamiz
    await db_add_user(uid)

    # 0) Broadcast kutilyapti
    if is_admin(uid) and uid in BC_WAIT:
        BC_WAIT.discard(uid)
        await message.answer("⏳ Reklama tarqatilyapti...")
        okc, failc = await send_broadcast(bot, message)
        await message.answer(f"✅ Yuborildi: {okc}\n❌ Xato: {failc}", reply_markup=kb_admin())
        return

    # 1) Delete flow
    if is_admin(uid) and uid in DEL_FLOW and message.text:
        code = message.text.strip()
        DEL_FLOW.discard(uid)
        okd = await db_delete_movie(code)
        await message.answer("✅ O‘chirildi" if okd else "❌ Bunday kod topilmadi", reply_markup=kb_admin())
        return

    # 2) Admin search flow
    if is_admin(uid) and uid in ADMIN_SEARCH_FLOW and message.text:
        code = message.text.strip()
        ADMIN_SEARCH_FLOW.discard(uid)
        movie = await db_get_movie(code)
        if movie:
            title, file_id = movie
            await message.answer_video(video=file_id, caption=f"🎬 {title}\n🔑 Kod: {code}")
        else:
            await message.answer(f"❌ Topilmadi: {code}", reply_markup=kb_admin())
        return

    # 3) Add flow
    if is_admin(uid) and uid in ADD_FLOW:
        flow = ADD_FLOW[uid]

        if flow.step == 1:
            code = (message.text or "").strip()
            if not re.match(r"^[A-Za-z0-9_-]{1,20}$", code):
                await message.answer("❌ Kod noto‘g‘ri. Faqat harf/son. Qayta yuboring.", reply_markup=kb_admin())
                return
            flow.code = code
            flow.step = 2
            ADD_FLOW[uid] = flow
            await message.answer("2/3) Kino nomini yuboring.", reply_markup=kb_admin())
            return

        if flow.step == 2:
            title = (message.text or "").strip()
            if len(title) < 2:
                await message.answer("❌ Nom juda qisqa. Qayta yuboring.", reply_markup=kb_admin())
                return
            flow.title = title
            flow.step = 3
            ADD_FLOW[uid] = flow
            await message.answer("3/3) Endi kinoni VIDEO qilib yuboring.", reply_markup=kb_admin())
            return

        if flow.step == 3:
            if not message.video:
                await message.answer("❌ Video yuboring (Telegram video).", reply_markup=kb_admin())
                return
            await db_add_movie(flow.code, flow.title, message.video.file_id, uid)
            ADD_FLOW.pop(uid, None)
            await message.answer(
                f"✅ Kino qo‘shildi!\n🔑 Kod: {flow.code}\n🎬 Nomi: {flow.title}",
                reply_markup=kb_admin()
            )
            return

    # 4) User kino olish rejimi: faqat shu paytda kod qabul qilamiz
    if message.text:
        txt = message.text.strip()

        # menyu tugmalarini skip
        if txt in {
            "🎬 Kino olish", "📢 Kinolar bo‘lim", "ℹ️ Yordam",
            "➕ Kino qo‘shish", "❌ Kino o‘chirish", "📢 Broadcast", "📊 Statistika", "🔎 Admin kino qidirish"
        }:
            return

        # User kino olish tugmasini bosmagan bo'lsa — kod ishlamaydi
        if (not is_admin(uid)) and (uid not in USER_GET_FLOW):
            await message.answer("🎬 Kino olish uchun avval **🎬 Kino olish** tugmasini bosing.", parse_mode="Markdown")
            return

        # endi kod sifatida ko'ramiz
        code = txt
        if len(code) > 25:
            USER_GET_FLOW.discard(uid)
            return

        movie = await db_get_movie(code)
        USER_GET_FLOW.discard(uid)  # 1 ta qidiruvdan keyin chiqib ketadi

        if movie:
            title, file_id = movie
            await message.answer_video(video=file_id, caption=f"🎬 {title}\n🔑 Kod: {code}")
        else:
            if MOVIES_CHANNEL.startswith("@"):
                await message.answer(
                    f"❌ Bu kod bo‘yicha kino topilmadi: {code}\n\n"
                    "📢 Kinolarni kanaldan topishingiz mumkin 👇",
                    reply_markup=kb_channel_link()
                )
            else:
                await message.answer(f"❌ Bu kod bo‘yicha kino topilmadi: {code}")


# =======================
# UPTIMEROBOT uchun PING SERVER
# =======================
async def start_webserver():
    app = web.Application()

    async def home(_):
        return web.Response(text="OK")

    app.router.add_get("/", home)
    app.router.add_get("/ping", home)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()


async def main():
    logging.basicConfig(level=logging.INFO)

    await db_init()
    bot = Bot(TOKEN)

    await start_webserver()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
