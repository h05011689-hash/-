"""
بوت إدارة متكامل + كشف الحسابات المتشابهة
يعمل على جميع الجروبات والقنوات التي يُضاف إليها تلقائياً
"""

import asyncio
import logging
import sqlite3
import threading
import time
import requests
import detection   # ملف خارجي يحتوي على منطق الكشف

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
)
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CommandHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from groq import Groq

# ═══════════════════════════════════════════════
#                   الإعدادات
# ═══════════════════════════════════════════════
GROQ_API_KEY        = "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"
SIGHTENGINE_USER    = "115219136"
SIGHTENGINE_SECRET  = "RKv87dBmMry9HGhESY4KZLp8gVAZgwWB"
TOKEN               = "8209098346:AAE_gOEWsB4bL8Jr7lFvQTOA46TCZUnjpe0"
MY_ID               = 8147516847
DEV_USERNAME        = "Leeeeeeevi"
AUTHORIZED_USERS    = {"Q_12_T", "Leeeeeeevi", "PHT_10"}

# ══ المالكون الخاصون بأوامر الوهمي والانتحال ══
OWNER_USERNAMES = {"Q_12_T", "Leeeeeeevi"}

NUDE_THRESHOLD      = 25
GORE_THRESHOLD      = 25

groq_client = Groq(api_key=GROQ_API_KEY)
executor    = ThreadPoolExecutor(max_workers=20)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ═══════════════════════════════════════════════
#              قواعد المخالفات
# ═══════════════════════════════════════════════
RULES = {
    "الكفر":            {"type": "ban",      "time": None},
    "الاساءة للاتحاد": {"type": "ban",      "time": None},
    "اباحي":            {"type": "ban",      "time": None},
    "دموي":             {"type": "ban",      "time": None},
    "سب الاهل":         {"type": "restrict", "time": 24 * 3600},
    "الترويج":          {"type": "restrict", "time": 6  * 3600},
    "سب المشرف":        {"type": "restrict", "time": 12 * 3600},
    "سب اي كلان":       {"type": "restrict", "time": 6  * 3600},
    "سب المباشر":       {"type": "restrict", "time": 4  * 3600},
    "السبام":           {"type": "restrict", "time": 4  * 3600},
    "التلميح":          {"type": "restrict", "time": 30 * 60},
}

GLOBAL_VIOLATIONS = {"الكفر", "الاساءة للاتحاد", "اباحي", "دموي", "سب اي كلان"}

RANK_HIERARCHY = {
    "مطور": 10, "مالك اساسي": 9, "مالك": 8,
    "مدير": 7,  "ادمن": 6,       "مميز": 5, "عضو": 1,
}

# ═══════════════════════════════════════════════
#                قاعدة البيانات
# ═══════════════════════════════════════════════
_db_lock = threading.Lock()
_local   = threading.local()

def _get_conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        c = sqlite3.connect("bot_system.db", check_same_thread=True, timeout=30)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        _local.conn = c
    return _local.conn

def db_exec(sql, params=(), *, many=False, fetchone=False, fetchall=False, commit=False):
    with _db_lock:
        conn = _get_conn()
        cur  = conn.cursor()
        if many:
            cur.executemany(sql, params)
        else:
            cur.execute(sql, params)
        result = None
        if fetchone:
            result = cur.fetchone()
        elif fetchall:
            result = cur.fetchall()
        if commit:
            conn.commit()
        return result

def db_script(sql):
    with _db_lock:
        conn = _get_conn()
        conn.executescript(sql)
        conn.commit()

def _init_db():
    db_script("""
    CREATE TABLE IF NOT EXISTS ranks (
        chat_id TEXT, user_id INTEGER, rank TEXT,
        PRIMARY KEY (chat_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS punishments (
        chat_id TEXT, user_id INTEGER, type TEXT, until TIMESTAMP,
        PRIMARY KEY (chat_id, user_id, type)
    );
    CREATE TABLE IF NOT EXISTS locks (
        chat_id TEXT, item TEXT,
        PRIMARY KEY (chat_id, item)
    );
    CREATE TABLE IF NOT EXISTS responses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT, trigger TEXT, reply_type TEXT,
        reply_data TEXT, caption TEXT, file_id TEXT
    );
    CREATE TABLE IF NOT EXISTS custom_ranks (
        chat_id TEXT, rank_key TEXT, rank_name TEXT,
        PRIMARY KEY (chat_id, rank_key)
    );
    CREATE TABLE IF NOT EXISTS stats (
        chat_id TEXT, user_id INTEGER, msgs INTEGER DEFAULT 0,
        PRIMARY KEY (chat_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS users (
        chat_id TEXT, user_id INTEGER, username TEXT,
        first_name TEXT, last_seen TIMESTAMP,
        PRIMARY KEY (chat_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS bot_managed_admins (
        chat_id TEXT, user_id INTEGER,
        PRIMARY KEY (chat_id, user_id)
    );
    """)

_init_db()

# متغيرات عامة
user_message_times  = {}
add_response_state  = {}
spam_tracker        = defaultdict(lambda: defaultdict(list))

# ═══════════════════════════════════════════════
#              دوال الوقت
# ═══════════════════════════════════════════════
def secs(txt: str) -> int:
    if not txt:
        return 3600
    dual_map = {
        "ثانيتين": 2, "دقيقتين": 120, "ساعتين": 7200,
        "يومين": 172800, "اسبوعين": 1209600, "شهرين": 5184000,
    }
    for word, val in dual_map.items():
        if word in txt:
            return val
    units = {
        "ثانية": 1,    "ثواني":  1,
        "دقيقة": 60,   "دقائق":  60,
        "ساعة":  3600, "ساعات":  3600,
        "يوم":   86400,"ايام":   86400,
        "اسبوع": 604800,"اسابيع":604800,
        "شهر":   2592000,"اشهر": 2592000,
    }
    parts = txt.split()
    total = 0
    for i in range(len(parts) - 1):
        try:
            num = int(parts[i])
            total += num * units.get(parts[i + 1], 0)
        except ValueError:
            pass
    return total if total > 0 else 3600

def fmt_duration(seconds: int) -> str:
    if not seconds:
        return "نهائي"
    if seconds < 60:
        return f"{seconds} ثانية"
    elif seconds < 3600:
        return f"{seconds // 60} دقيقة"
    elif seconds < 86400:
        h = seconds // 3600
        return "ساعتين" if h == 2 else f"{h} ساعة"
    elif seconds < 604800:
        d = seconds // 86400
        return "يومين" if d == 2 else f"{d} يوم"
    else:
        w = seconds // 604800
        return "اسبوعين" if w == 2 else f"{w} اسبوع"

# ═══════════════════════════════════════════════
#              تحليل النصوص (Groq)
# ═══════════════════════════════════════════════
def analyze_text(content: str) -> str:
    categories = list(RULES.keys())
    prompt = f"""أنت نظام تصنيف محتوى دقيق لمجموعة عربية على تيليجرام.

الرسالة: "{content}"

مهمتك: هل هذه الرسالة تحتوي على أي من المخالفات التالية؟
{chr(10).join(f'- {c}' for c in categories)}

**تعريفات دقيقة**:
- الكفر: ازدراء صريح للدين أو الذات الإلهية أو الأنبياء
- الاساءة للاتحاد: هجوم موجه لاتحاد أو منظمة بعينها
- اباحي: محتوى جنسي صريح أو إيحائي
- دموي: محتوى عنيف أو دموي مفرط
- **سب الاهل**: شتيمة تتعلق بأم أو أب أو أخت أو أي قريب. مثل: كسمك، كس اختك، امك، اختك، ابوك
- الترويج: دعاية لقناة أو منتج بدون إذن
- سب المشرف: إهانة للإدارة
- سب اي كلان: هجوم على مجموعة أو فريق
- **سب المباشر**: شتيمة مباشرة للشخص نفسه دون ذكر أهله. مثل: عرص، ديوث، قحبة، خرا
- السبام: تكرار لا معنى له
- التلميح: إيحاء غير مباشر

**تنبيه**: كسمك/كس اختك/امك = سب الاهل لا سب المباشر.

**الرد**: اكتب اسم المخالفة فقط أو "سليم"."""

    try:
        res = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": "أنت مصنف محتوى دقيق. ردك كلمة واحدة فقط."},
                {"role": "user",   "content": prompt}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0,
            max_tokens=15,
        )
        result = res.choices[0].message.content.strip().strip('.,!؟"\'').strip()
        if result in RULES:
            return result
        if result == "سليم":
            return "سليم"
        for cat in RULES:
            if cat in result:
                return cat
        return "سليم"
    except Exception as e:
        logging.error(f"Groq error: {e}")
        return "سليم"

# ═══════════════════════════════════════════════
#              Sightengine (فحص الصور)
# ═══════════════════════════════════════════════
def _get_scores_sync(image_bytes: bytes) -> dict | None:
    try:
        r = requests.post(
            "https://api.sightengine.com/1.0/check.json",
            files={"media": ("img.jpg", image_bytes)},
            data={
                "models":     "nudity,nudity-2.0,gore,offensive",
                "api_user":   SIGHTENGINE_USER,
                "api_secret": SIGHTENGINE_SECRET,
            },
            timeout=10,
        )
        r.raise_for_status()
        d    = r.json()
        nu   = d.get("nudity",    {})
        nu2  = d.get("nudity-2.0",{})
        nude = max(nu.get("raw", 0), nu.get("partial", 0)) * 100
        if nu2:
            nude = max(nude, (1 - nu2.get("none", 1)) * 100)
        return {
            "nude":    round(nude, 1),
            "gore":    round(d.get("gore",      {}).get("prob", 0) * 100, 1),
            "offense": round(d.get("offensive", {}).get("prob", 0) * 100, 1),
        }
    except Exception as e:
        logging.error(f"Sightengine: {e}")
        return None

async def get_verdict(image_bytes: bytes) -> str:
    loop   = asyncio.get_event_loop()
    scores = await loop.run_in_executor(executor, _get_scores_sync, image_bytes)
    if not scores:
        return "NO"
    logging.info(f"Nude={scores['nude']}% Gore={scores['gore']}% Off={scores['offense']}%")
    if scores["nude"] >= NUDE_THRESHOLD:
        return "اباحي"
    if scores["gore"] >= GORE_THRESHOLD:
        return "دموي"
    return "NO"

# ═══════════════════════════════════════════════
#              دوال الرتب والصلاحيات
# ═══════════════════════════════════════════════
async def is_tg_admin(context: ContextTypes.DEFAULT_TYPE, chat_id, uid) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, uid)
        return member.status in ("administrator", "creator")
    except:
        return False

async def get_user_rank(context: ContextTypes.DEFAULT_TYPE, chat_id, uid) -> str:
    try:
        m = await context.bot.get_chat_member(chat_id, uid)
        if m.user.username == DEV_USERNAME:
            return "مطور"
        if m.status == "creator":
            return "مالك اساسي"
    except:
        pass
    row = db_exec(
        "SELECT rank FROM ranks WHERE chat_id=? AND user_id=?",
        (str(chat_id), uid), fetchone=True,
    )
    return row[0] if row else "عضو"

def get_custom_rank(chat_id, key) -> str:
    row = db_exec(
        "SELECT rank_name FROM custom_ranks WHERE chat_id=? AND rank_key=?",
        (str(chat_id), key), fetchone=True,
    )
    return row[0] if row else key

async def can_punish(context, chat_id, punisher_id, target_id) -> bool:
    if punisher_id == target_id:
        return False
    pr = RANK_HIERARCHY.get(await get_user_rank(context, chat_id, punisher_id), 1)
    tr = RANK_HIERARCHY.get(await get_user_rank(context, chat_id, target_id),   1)
    return pr > tr

def is_authorized(user) -> bool:
    if not user:
        return False
    return user.id == MY_ID or (user.username and user.username in AUTHORIZED_USERS)

def is_owner(user) -> bool:
    """التحقق هل المستخدم من المالكين لأوامر الوهمي والانتحال"""
    if not user:
        return False
    return user.id == MY_ID or (user.username and user.username in OWNER_USERNAMES)

# ═══════════════════════════════════════════════
#              دوال قاعدة البيانات
# ═══════════════════════════════════════════════
def update_user(chat_id, uid, username, first_name):
    db_exec(
        "INSERT OR REPLACE INTO users (chat_id,user_id,username,first_name,last_seen) VALUES (?,?,?,?,?)",
        (str(chat_id), uid, username, first_name, datetime.now().isoformat()),
        commit=True,
    )

async def find_by_username(context, chat_id, username):
    u = username.strip().lstrip("@").lower()
    row = db_exec(
        "SELECT user_id, first_name FROM users WHERE chat_id=? AND LOWER(username)=?",
        (str(chat_id), u), fetchone=True,
    )
    if row:
        return row[0], row[1]
    try:
        cu = await context.bot.get_chat(f"@{u}")
        await context.bot.get_chat_member(chat_id, cu.id)
        update_user(chat_id, cu.id, cu.username, cu.first_name)
        return cu.id, cu.first_name
    except:
        pass
    return None, None

async def extract_target(context, m):
    cid = str(m.chat.id)
    if m.reply_to_message:
        t = m.reply_to_message.from_user
        update_user(cid, t.id, t.username, t.first_name)
        return t.id, t.first_name
    text = m.text or ""
    for p in text.split():
        if p.startswith("@"):
            uid, name = await find_by_username(context, cid, p)
            if uid:
                return uid, name
        elif p.isdigit() and len(p) > 5:
            try:
                uid  = int(p)
                mem  = await context.bot.get_chat_member(cid, uid)
                update_user(cid, uid, mem.user.username, mem.user.first_name)
                return uid, mem.user.first_name
            except:
                pass
    return None, None

def is_punished(chat_id, uid, typ) -> bool:
    row = db_exec(
        "SELECT until FROM punishments WHERE chat_id=? AND user_id=? AND type=?",
        (str(chat_id), uid, typ), fetchone=True,
    )
    if row:
        until = datetime.fromisoformat(row[0])
        if datetime.now() < until:
            return True
        db_exec(
            "DELETE FROM punishments WHERE chat_id=? AND user_id=? AND type=?",
            (str(chat_id), uid, typ), commit=True,
        )
    return False

async def already_punished(context, chat_id, target_id) -> bool:
    try:
        st = (await context.bot.get_chat_member(chat_id, target_id)).status
        if st in ("kicked", "restricted"):
            return True
    except:
        pass
    row = db_exec(
        "SELECT until FROM punishments WHERE chat_id=? AND user_id=? AND type IN ('ban','restrict','mute')",
        (str(chat_id), target_id), fetchone=True,
    )
    if row and datetime.now() < datetime.fromisoformat(row[0]):
        return True
    return False

async def do_punish(context, chat_id, target_id, rule_key):
    rule = RULES[rule_key]
    if rule["type"] == "restrict":
        until_ts = int(time.time()) + rule["time"]
        until    = datetime.fromtimestamp(until_ts)
        await context.bot.restrict_chat_member(
            chat_id, target_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until_ts,
        )
        db_exec(
            "INSERT OR REPLACE INTO punishments VALUES (?,?,?,?)",
            (str(chat_id), target_id, "restrict", until.isoformat()), commit=True,
        )
    else:
        await context.bot.ban_chat_member(chat_id, target_id)
        db_exec(
            "INSERT OR REPLACE INTO punishments VALUES (?,?,?,?)",
            (str(chat_id), target_id, "ban",
             (datetime.now() + timedelta(days=365)).isoformat()), commit=True,
        )

# ═══════════════════════════════════════════════
#  فحص توجيه الرسالة (هل موجهة للمُبلِّغ؟)
# ═══════════════════════════════════════════════
async def is_directed_at_reporter(context, target_msg, reporter_id) -> bool:
    if target_msg.reply_to_message:
        if target_msg.reply_to_message.from_user.id == reporter_id:
            return True
    text = target_msg.text or target_msg.caption or ""
    if text:
        try:
            reporter_user = await context.bot.get_chat_member(target_msg.chat.id, reporter_id)
            if reporter_user.user.username and \
               f"@{reporter_user.user.username}".lower() in text.lower():
                return True
        except:
            pass
        if str(reporter_id) in text:
            return True
    if target_msg.reply_to_message:
        if target_msg.reply_to_message.from_user.id != reporter_id:
            return False
    return True

# ═══════════════════════════════════════════════
#   جلب file_id من الرسالة (للفحص الإباحي)
# ═══════════════════════════════════════════════
async def get_file_id_for_check(msg) -> str | None:
    if msg.photo:
        return msg.photo[-1].file_id
    if msg.sticker and msg.sticker.thumbnail:
        return msg.sticker.thumbnail.file_id
    if msg.animation:
        return msg.animation.thumbnail.file_id if msg.animation.thumbnail else msg.animation.file_id
    if msg.video and msg.video.thumbnail:
        return msg.video.thumbnail.file_id
    if msg.document:
        mime = msg.document.mime_type or ""
        if mime.startswith("image/") or mime.startswith("video/"):
            return msg.document.file_id
    return None

# ═══════════════════════════════════════════════
#          فحص أقفال المحتوى
# ═══════════════════════════════════════════════
async def check_locks(context, m, rank, tg_is_admin=False) -> bool:
    cid = str(m.chat_id)
    if rank in ("مطور", "مالك اساسي", "مالك", "مدير", "ادمن") or tg_is_admin:
        return True
    if db_exec("SELECT 1 FROM locks WHERE chat_id=? AND item='chat'", (cid,), fetchone=True):
        try:
            await context.bot.delete_message(m.chat_id, m.message_id)
        except:
            pass
        return False
    if rank == "مميز":
        return True
    lock_map = {
        "photo": "photo", "video": "video", "sticker": "sticker",
        "animation": "animation", "document": "document", "audio": "audio",
    }
    ct = m.content_type if hasattr(m, "content_type") else ""
    if ct in lock_map:
        if db_exec("SELECT 1 FROM locks WHERE chat_id=? AND item=?",
                   (cid, lock_map[ct]), fetchone=True):
            try:
                await context.bot.delete_message(m.chat_id, m.message_id)
            except:
                pass
            return False
    if db_exec("SELECT 1 FROM locks WHERE chat_id=? AND item='all'", (cid,), fetchone=True):
        try:
            await context.bot.delete_message(m.chat_id, m.message_id)
        except:
            pass
        return False
    if m.text:
        if "http" in m.text.lower() or "www." in m.text.lower():
            if db_exec("SELECT 1 FROM locks WHERE chat_id=? AND item='links'",
                       (cid,), fetchone=True):
                try:
                    await context.bot.delete_message(m.chat_id, m.message_id)
                except:
                    pass
                return False
        if "@" in m.text:
            if db_exec("SELECT 1 FROM locks WHERE chat_id=? AND item='usernames'",
                       (cid,), fetchone=True):
                try:
                    await context.bot.delete_message(m.chat_id, m.message_id)
                except:
                    pass
                return False
    return True

# ═══════════════════════════════════════════════
#   رفع مشرف محدود (إرسال + تعديل + حذف)
# ═══════════════════════════════════════════════
async def _promote_admin(context, reply_target, chat_id: int, user_id: int):
    try:
        await context.bot.promote_chat_member(
            chat_id=chat_id, user_id=user_id,
            can_change_info=False,
            can_post_messages=True,
            can_edit_messages=True,
            can_delete_messages=True,
            can_invite_users=False,
            can_restrict_members=False,
            can_pin_messages=False,
            can_manage_chat=False,
            can_manage_video_chats=False,
        )
        db_exec(
            "INSERT OR IGNORE INTO bot_managed_admins VALUES (?,?)",
            (str(chat_id), user_id), commit=True,
        )
        try:
            info = await context.bot.get_chat(user_id)
            name = info.full_name or str(user_id)
        except:
            name = str(user_id)
        txt = f"✅ تم ترقية **{name}**\n📋 إرسال · تعديل · حذف فقط"
        if hasattr(reply_target, "edit_message_text"):
            await reply_target.edit_message_text(txt, parse_mode="Markdown")
        else:
            await reply_target.reply_text(txt, parse_mode="Markdown")
    except Exception as e:
        txt = f"❌ فشل: {e}"
        if hasattr(reply_target, "edit_message_text"):
            await reply_target.edit_message_text(txt)
        else:
            await reply_target.reply_text(txt)

async def _demote_admin(context, chat_id, user_id):
    try:
        await context.bot.promote_chat_member(
            chat_id=chat_id, user_id=user_id,
            can_change_info=False, can_post_messages=False,
            can_edit_messages=False, can_delete_messages=False,
            can_invite_users=False, can_restrict_members=False,
            can_pin_messages=False, can_manage_chat=False,
            can_manage_video_chats=False,
        )
        db_exec(
            "DELETE FROM bot_managed_admins WHERE chat_id=? AND user_id=?",
            (str(chat_id), user_id), commit=True,
        )
        logging.info(f"✅ تنزيل {user_id} من {chat_id}")
    except Exception as e:
        logging.error(f"demote: {e}")

# ═══════════════════════════════════════════════
#          معالجة الوسائط (صور/فيديو/ملصقات)
# ═══════════════════════════════════════════════
async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not msg or not chat:
        return

    cid = str(chat.id)
    context.bot_data.setdefault("known_chats", {})[cid] = chat.title or cid

    uid  = user.id if user else None
    rank = await get_user_rank(context, chat.id, uid) if uid else "عضو"
    tg_a = await is_tg_admin(context, chat.id, uid)   if uid else False

    if not await check_locks(context, msg, rank, tg_a):
        return

    if rank in ("مطور","مالك اساسي","مالك","مدير","ادمن") or tg_a:
        await _handle_response_media(msg, context)
        return

    asyncio.create_task(_scan_media_task(context, msg, chat, user, rank, cid))
    await _handle_response_media(msg, context)

async def _scan_media_task(context, msg, chat, user, rank, cid):
    try:
        fid = await get_file_id_for_check(msg)
        if not fid or (msg.sticker and not msg.sticker.thumbnail):
            return
        file      = await context.bot.get_file(fid)
        img_bytes = bytes(await file.download_as_bytearray())
        verdict   = await get_verdict(img_bytes)
        del img_bytes
        if verdict == "NO":
            return

        try:
            await msg.delete()
        except:
            pass

        uid    = user.id if user else None
        sender = f"@{user.username}" if user and user.username else str(uid) if uid else "قناة"
        tag    = "⚡" if cid in [str(i) for i in context.bot_data.get("priority_chat_ids", set())] else "🚨"

        try:
            await context.bot.send_message(
                MY_ID,
                f"{tag} حُذف محتوى غير لائق [{verdict}]\n"
                f"👤 {sender}\n📍 {chat.title or chat.id}",
            )
        except:
            pass

        if not uid:
            return

        is_managed = db_exec(
            "SELECT 1 FROM bot_managed_admins WHERE chat_id=? AND user_id=?",
            (cid, uid), fetchone=True,
        )
        if is_managed:
            await _demote_admin(context, chat.id, uid)
        elif rank == "عضو":
            until_ts = int(time.time()) + 3600
            until    = datetime.fromtimestamp(until_ts)
            await context.bot.restrict_chat_member(
                chat.id, uid,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until_ts,
            )
            db_exec(
                "INSERT OR REPLACE INTO punishments VALUES (?,?,?,?)",
                (cid, uid, "mute", until.isoformat()), commit=True,
            )
            name = user.first_name or str(uid)
            try:
                await context.bot.send_message(
                    chat.id, f"⚠️ تم كتم {name} ساعة بسبب محتوى [{verdict}]."
                )
            except:
                pass
    except Exception as e:
        logging.error(f"_scan_media_task: {e}")

async def _handle_response_media(msg, context):
    if not msg.from_user:
        return
    uid   = msg.from_user.id
    state = add_response_state.get(uid)
    if not state or state.get("step") != 2:
        return
    ct  = msg.content_type
    cap = msg.caption or ""
    fid = None
    if ct == "photo"     and msg.photo:      fid = msg.photo[-1].file_id
    elif ct == "video"   and msg.video:      fid = msg.video.file_id
    elif ct == "sticker" and msg.sticker:    fid = msg.sticker.file_id
    elif ct == "animation" and msg.animation:fid = msg.animation.file_id
    elif ct == "voice"   and msg.voice:      fid = msg.voice.file_id
    elif ct == "document" and msg.document:  fid = msg.document.file_id
    elif ct == "audio"   and msg.audio:      fid = msg.audio.file_id
    if fid:
        cid     = state["chat_id"]
        trigger = state["trigger"]
        db_exec("DELETE FROM responses WHERE chat_id=? AND trigger=?", (cid, trigger), commit=True)
        db_exec(
            "INSERT INTO responses (chat_id,trigger,reply_type,reply_data,caption,file_id) VALUES (?,?,?,?,?,?)",
            (cid, trigger, ct, "", cap, fid), commit=True,
        )
        await msg.reply_text(f"⌯ تم حفظ الرد على '{trigger}' بنجاح!")
        del add_response_state[uid]

# ═══════════════════════════════════════════════
#          أمر "ادمن" (الإبلاغ)
# ═══════════════════════════════════════════════
async def handle_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m or not m.text or m.text.strip() != "ادمن" or not m.reply_to_message:
        return
    asyncio.create_task(_report_worker(context, m))

async def _report_worker(context, m):
    try:
        target      = m.reply_to_message
        cid         = m.chat.id
        reporter_id = m.from_user.id

        if target.from_user.id == reporter_id:
            await context.bot.send_message(cid, "⚠️ لا يمكنك الإبلاغ عن نفسك.")
            return

        rep_rank         = await get_user_rank(context, cid, reporter_id)
        is_reporter_admin = rep_rank in ("مطور","مالك اساسي","مالك","مدير","ادمن") or \
                            await is_tg_admin(context, cid, reporter_id)

        content      = target.text or target.caption or ""
        media_verdict = None

        if not content:
            fid = await get_file_id_for_check(target)
            if fid:
                try:
                    file      = await context.bot.get_file(fid)
                    img_bytes = bytes(await file.download_as_bytearray())
                    v         = await get_verdict(img_bytes)
                    del img_bytes
                    if v != "NO":
                        media_verdict = v
                except:
                    pass
            content = "ميديا"

        loop = asyncio.get_event_loop()
        if media_verdict:
            decision = media_verdict
        elif content != "ميديا":
            decision = await loop.run_in_executor(executor, analyze_text, content)
        else:
            decision = "سليم"

        matched = decision if decision in RULES else None

        if not is_reporter_admin:
            directed  = await is_directed_at_reporter(context, target, reporter_id)
            is_global = matched in GLOBAL_VIOLATIONS if matched else False
            if not directed and not is_global:
                until_ts = int(time.time()) + 1800
                try:
                    await context.bot.restrict_chat_member(
                        cid, reporter_id,
                        permissions=ChatPermissions(can_send_messages=False),
                        until_date=until_ts,
                    )
                    db_exec(
                        "INSERT OR REPLACE INTO punishments VALUES (?,?,?,?)",
                        (str(cid), reporter_id, "restrict",
                         datetime.fromtimestamp(until_ts).isoformat()), commit=True,
                    )
                except Exception as e:
                    logging.error(f"restrict reporter: {e}")
                name = m.from_user.first_name or str(reporter_id)
                await context.bot.send_message(
                    cid, f"⚠️ {name} ليس معنياً بهذه الرسالة. تم تقييده 30 دقيقة."
                )
                return

        if matched:
            await context.bot.send_message(
                cid,
                f"⚠️ مخالفة مكتشفة: **{matched}**\n⏳ انتظار 5 دقائق لتدخل الأدمن...",
                parse_mode="Markdown",
            )
            await asyncio.sleep(300)

            if await already_punished(context, str(cid), target.from_user.id):
                await context.bot.send_message(cid, "✅ تمت معاقبة العضو من قِبل الأدمنيه.")
            else:
                try:
                    await do_punish(context, str(cid), target.from_user.id, matched)
                    dur = fmt_duration(RULES[matched].get("time") or 0)
                    typ = "حظر" if RULES[matched]["type"] == "ban" else "تقييد"
                    await context.bot.send_message(
                        cid,
                        f"🤖 لم يتصرف الأدمن. نُفِّذت عقوبة **{matched}** ({typ} {dur})",
                        parse_mode="Markdown",
                    )
                except TelegramError:
                    await context.bot.send_message(cid, "⚠️ لا يمكنني معاقبة أدمن.")
        else:
            if not is_reporter_admin:
                until_ts = int(time.time()) + 1800
                try:
                    await context.bot.restrict_chat_member(
                        cid, reporter_id,
                        permissions=ChatPermissions(can_send_messages=False),
                        until_date=until_ts,
                    )
                    db_exec(
                        "INSERT OR REPLACE INTO punishments VALUES (?,?,?,?)",
                        (str(cid), reporter_id, "restrict",
                         datetime.fromtimestamp(until_ts).isoformat()), commit=True,
                    )
                except:
                    pass
                await context.bot.send_message(cid, "⚠️ بلاغ غير دقيق. تم تقييدك 30 دقيقة.")
            else:
                await context.bot.send_message(cid, "⚠️ البلاغ غير دقيق، وبما أنك أدمن فلن تُقيد.")

    except Exception as e:
        logging.error(f"_report_worker: {e}")

# ═══════════════════════════════════════════════
#          معالج النصوص الرئيسي
# ═══════════════════════════════════════════════
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m or not m.text:
        return

    cid  = str(m.chat.id)
    uid  = m.from_user.id
    text = m.text.strip()

    context.bot_data.setdefault("known_chats", {})[cid] = m.chat.title or cid

    db_exec("INSERT OR IGNORE INTO stats VALUES (?,?,0)", (cid, uid), commit=True)
    db_exec("UPDATE stats SET msgs=msgs+1 WHERE chat_id=? AND user_id=?", (cid, uid), commit=True)
    update_user(cid, uid, m.from_user.username, m.from_user.first_name)

    if is_punished(cid, uid, "mute"):
        try:
            await context.bot.delete_message(m.chat.id, m.message_id)
        except:
            pass
        return

    rank        = await get_user_rank(context, m.chat.id, uid)
    tg_is_admin = await is_tg_admin(context, m.chat.id, uid)
    is_any_admin = rank in ("مطور","مالك اساسي","مالك","مدير","ادمن") or tg_is_admin

    # ════════════════════════════════════════════
    #   ✅ FIX 1: البوت يرد على ياسر بشكل صحيح
    #   يتحقق من النص المجرد بدون @ وبه @
    # ════════════════════════════════════════════
    text_lower = text.lower()
    if text_lower in ("@yas_r7", "yas_r7"):
        if m.from_user.username and m.from_user.username.lower() == "yas_r7":
            await m.reply_text("نعم سيدي المالك")
            return

    # ════════════════════════════════════════════
    #   أوامر الكشف (للمالكين فقط)
    # ════════════════════════════════════════════
    if text == "اذن الكشف":
        if not is_owner(m.from_user):
            await m.reply_text("⛔ هذا الأمر للمالكين فقط.")
            return
        await m.reply_text("⚖️ جاري الكشف عن الحسابات الوهمية والمشبوهة...")
        asyncio.create_task(_run_detection_and_reply(m, context))
        return

    if text == "كشف الشخص المنتحل":
        if not is_owner(m.from_user):
            await m.reply_text("⛔ هذا الأمر للمالكين فقط.")
            return
        await m.reply_text(
            "📱 **الرقم المستعمل:** `07714698848`\n"
            "📱 **الرقم الثاني الأصلي:** `07725666391`",
            parse_mode="Markdown"
        )
        return

    if text == "كشف الوهمي":
        if not is_owner(m.from_user):
            await m.reply_text("⛔ هذا الأمر للمالكين فقط.")
            return
        await m.reply_text("🔍 جاري كشف الحسابات الوهمية...")
        asyncio.create_task(_run_detection_and_reply(m, context))
        return

    if text == "بوت":
        if not is_owner(m.from_user):
            await m.reply_text("⛔ هذا الأمر للمالكين فقط.")
            return
        bot_info = await context.bot.get_me()
        chats_count = len(context.bot_data.get("known_chats", {}))
        await m.reply_text(
            f"🤖 **معلومات البوت**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📛 الاسم: {bot_info.full_name}\n"
            f"🆔 ID: `{bot_info.id}`\n"
            f"🔗 يوزر: @{bot_info.username}\n"
            f"📊 الجروبات المُراقبة: {chats_count}\n"
            f"━━━━━━━━━━━━━━━━━━",
            parse_mode="Markdown"
        )
        return

    # فحص السبام للأعضاء
    if not is_any_admin:
        now = time.time()
        spam_tracker[cid][uid] = [t for t in spam_tracker[cid][uid] if now - t <= 6]
        spam_tracker[cid][uid].append(now)
        if len(spam_tracker[cid][uid]) >= 4:
            until_ts = int(now) + 14400
            until    = datetime.fromtimestamp(until_ts)
            try:
                await context.bot.restrict_chat_member(
                    cid, uid,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until_ts,
                )
                db_exec(
                    "INSERT OR REPLACE INTO punishments VALUES (?,?,?,?)",
                    (str(cid), uid, "restrict", until.isoformat()), commit=True,
                )
            except Exception as e:
                logging.error(f"Spam restrict: {e}")
            try:
                spam_name = (await context.bot.get_chat_member(cid, uid)).user.first_name or str(uid)
            except:
                spam_name = str(uid)
            try:
                await context.bot.send_message(
                    cid, f"🚫 تم تقييد {spam_name} لمدة 4 ساعات بسبب السبام."
                )
            except:
                pass
            spam_tracker[cid][uid] = []
            return

        if rank == "عضو" and await check_flood(context, cid, uid, m.chat.id):
            return

    if uid in add_response_state:
        await _add_response_text(m, context)
        return

    if rank == "عضو" and not tg_is_admin:
        if text in ("رتبتي", "ايدي", "id"):
            await _show_id(m, context)
        elif text.startswith("رتبته"):
            await _rank_of(m, context)
        elif text.startswith("كشف"):
            await _user_info(m, context)
    else:
        await _admin_commands(m, rank, text, context)

    if not await check_locks(context, m, rank, tg_is_admin):
        return
    asyncio.create_task(_auto_response(m, cid, context))

async def check_flood(context, chat_id, uid, real_chat_id) -> bool:
    now = time.time()
    key = f"{chat_id}_{uid}"
    user_message_times.setdefault(key, [])
    user_message_times[key] = [t for t in user_message_times[key] if now - t <= 5]
    user_message_times[key].append(now)
    if len(user_message_times[key]) >= 6:
        until_ts = int(now) + 6 * 3600
        until    = datetime.fromtimestamp(until_ts)
        try:
            await context.bot.restrict_chat_member(
                real_chat_id, uid,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until_ts,
            )
            db_exec(
                "INSERT OR REPLACE INTO punishments VALUES (?,?,?,?)",
                (str(chat_id), uid, "restrict", until.isoformat()), commit=True,
            )
            try:
                name = (await context.bot.get_chat_member(real_chat_id, uid)).user.first_name or str(uid)
            except:
                name = str(uid)
            await context.bot.send_message(
                real_chat_id,
                f"⚠️ تم تقييد {name} لمدة 6 ساعات بسبب الفلود (6 رسائل في 5 ثوانٍ).",
            )
            user_message_times[key] = []
            return True
        except Exception as e:
            logging.error(f"Flood restrict: {e}")
    return False

# ═══════════════════════════════════════════════
#          إضافة ردود تلقائية
# ═══════════════════════════════════════════════
async def _add_response_text(m, context):
    uid   = m.from_user.id
    state = add_response_state[uid]
    if m.text == "الغاء":
        del add_response_state[uid]
        await m.reply_text("⌯ تم الإلغاء.")
        return
    if state["step"] == 1:
        add_response_state[uid] = {
            "step": 2, "trigger": m.text, "chat_id": state["chat_id"]
        }
        await m.reply_text(
            f"⌯ الكلمة المفتاحية: {m.text}\n⌯ الآن أرسل الرد (نص، صورة، فيديو، ملصق...):"
        )
    elif state["step"] == 2:
        cid     = state["chat_id"]
        trigger = state["trigger"]
        db_exec("DELETE FROM responses WHERE chat_id=? AND trigger=?", (cid, trigger), commit=True)
        db_exec(
            "INSERT INTO responses (chat_id,trigger,reply_type,reply_data,caption,file_id) VALUES (?,?,?,?,?,?)",
            (cid, trigger, "text", m.text, "", ""), commit=True,
        )
        await m.reply_text(f"⌯ تم حفظ الرد النصي على '{trigger}' بنجاح!")
        del add_response_state[uid]

async def _auto_response(m, cid, context):
    if not m.text:
        return
    incoming = m.text.strip()
    rows = db_exec(
        "SELECT trigger, reply_type, reply_data, caption, file_id FROM responses WHERE chat_id=?",
        (cid,), fetchall=True,
    )
    if not rows:
        return
    matched_row = None
    for row in rows:
        if incoming == row[0] or incoming.lower() == row[0].lower():
            matched_row = row
            break
    if not matched_row:
        return
    trigger, typ, data, cap, fid = matched_row
    try:
        if typ == "text":
            await m.reply_text(data)
        else:
            handlers = {
                "photo":     lambda: context.bot.send_photo(m.chat.id, fid, caption=cap, reply_to_message_id=m.message_id),
                "video":     lambda: context.bot.send_video(m.chat.id, fid, caption=cap, reply_to_message_id=m.message_id),
                "sticker":   lambda: context.bot.send_sticker(m.chat.id, fid, reply_to_message_id=m.message_id),
                "animation": lambda: context.bot.send_animation(m.chat.id, fid, caption=cap, reply_to_message_id=m.message_id),
                "voice":     lambda: context.bot.send_voice(m.chat.id, fid, caption=cap, reply_to_message_id=m.message_id),
                "document":  lambda: context.bot.send_document(m.chat.id, fid, caption=cap, reply_to_message_id=m.message_id),
                "audio":     lambda: context.bot.send_audio(m.chat.id, fid, caption=cap, reply_to_message_id=m.message_id),
            }
            if typ in handlers:
                await handlers[typ]()
    except Exception as e:
        logging.error(f"Auto response: {e}")

# ═══════════════════════════════════════════════
#          أوامر الأدمن
# ═══════════════════════════════════════════════
async def _admin_commands(m, rank, text, context):

    # ════════════════════════════════════════════
    #  ✅ FIX 2: رفع القيود — إصلاح ChatPermissions
    #  نستخدم can_send_messages=True مع باقي الصلاحيات
    # ════════════════════════════════════════════
    if text.startswith("رفع القيود"):
        parts = text.split()
        tid, tname = None, None
        if len(parts) > 1:
            class _F:
                chat = m.chat; reply_to_message = None
                text = " ".join(parts[1:])
            tid, tname = await extract_target(context, _F())
        if not tid and m.reply_to_message:
            tid   = m.reply_to_message.from_user.id
            tname = m.reply_to_message.from_user.first_name
        if not tid:
            await m.reply_text("⌯ استخدم الرد على المستخدم أو اكتب @username مع الأمر.")
            return
        if not await can_punish(context, str(m.chat.id), m.from_user.id, tid):
            await m.reply_text("⌯ لا يمكنك رفع القيود عن من هو أعلى منك!")
            return
        try:
            # ✅ الطريقة الصحيحة لرفع القيود في python-telegram-bot v20+
            await context.bot.restrict_chat_member(
                m.chat.id, tid,
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_polls=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                    can_change_info=False,
                    can_invite_users=True,
                    can_pin_messages=False,
                ),
            )
        except Exception as e:
            logging.error(f"lift restrict error: {e}")
            # محاولة بديلة بأقل صلاحيات
            try:
                await context.bot.restrict_chat_member(
                    m.chat.id, tid,
                    permissions=ChatPermissions(can_send_messages=True),
                )
            except Exception as e2:
                logging.error(f"lift restrict fallback error: {e2}")

        # ✅ مسح من قاعدة البيانات أيضاً
        db_exec(
            "DELETE FROM punishments WHERE chat_id=? AND user_id=?",
            (str(m.chat.id), tid), commit=True,
        )
        await m.reply_text(f"✅ تم رفع جميع القيود عن {tname or tid}.")
        return

    if text.startswith("تنزيل الكل"):
        parts = text.split()
        if len(parts) > 1:
            class _F:
                chat = m.chat; reply_to_message = None
                text = " ".join(parts[1:])
            tid, tname = await extract_target(context, _F())
            if tid:
                await _remove_all_ranks(m, rank, context, tid, tname)
                return
        if m.reply_to_message:
            await _remove_all_ranks(
                m, rank, context,
                m.reply_to_message.from_user.id,
                m.reply_to_message.from_user.first_name,
            )
            return
        await m.reply_text("⌯ استخدم الرد على المستخدم أو اكتب @username مع الأمر.")
        return

    if text.startswith("كشف"):
        await _user_info(m, context); return
    if text in ("ايدي", "id", "رتبتي"):
        await _show_id(m, context)
    elif text.startswith("رتبته"):
        await _rank_of(m, context)
    elif text.startswith(("رفع ", "تنزيل ")):
        await _promote(m, rank, context)
    elif any(c in text for c in ("حظر","كتم","تقييد","طرد","الغاء")):
        await _punish(m, rank, context)
    elif text.startswith(("قفل ","فتح ")):
        await _lock(m, rank, context)
    elif text in ("الردود","اضف رد","مسح الردود") or text.startswith("مسح رد "):
        await _responses_cmd(m, rank, context)
    elif text.startswith("مسح"):
        await _clean(m, rank, context)
    elif text in ("المطورين","المالكيين الاساسيين","المالكيين",
                  "المدراء","الادمنيه","المميزين","المشرفين"):
        await _lists(m, rank, context)

def escape_md(t):
    for ch in ["_","*","[","]","(",")",
               "~","`",">","#","+","-","=","|","{","}",".","|"]:
        t = t.replace(ch, f"\\{ch}")
    return t

async def _user_info(m, context):
    tid, tname = await extract_target(context, m)
    if not tid:
        await m.reply_text("⌯ استخدم الرد على المستخدم أو اكتب @username أو الايدي.")
        return
    cid    = str(m.chat.id)
    rank   = await get_user_rank(context, m.chat.id, tid)
    c_rank = get_custom_rank(cid, rank)
    msgs   = db_exec("SELECT msgs FROM stats WHERE chat_id=? AND user_id=?",
                     (cid, tid), fetchone=True)
    msgs   = msgs[0] if msgs else 0
    try:
        user      = (await context.bot.get_chat_member(cid, tid)).user
        username  = f"@{user.username}" if user.username else "لا يوجد"
        first_name= user.first_name
        last_name = user.last_name or ""
    except:
        username   = "لا يوجد"
        first_name = tname or str(tid)
        last_name  = ""
    caption = (
        f"\n📊 **معلومات العضو** 📊\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 **الاسم:** {escape_md(first_name)} {escape_md(last_name)}\n"
        f"🆔 **الايدي:** `{tid}`\n"
        f"🔗 **المعرف:** {escape_md(username)}\n"
        f"🎖 **الرتبة:** {escape_md(c_rank)}\n"
        f"💬 **الرسائل:** {msgs}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
    )
    try:
        photos = await context.bot.get_user_profile_photos(tid, limit=1)
        if photos.total_count > 0:
            await context.bot.send_photo(
                m.chat.id, photos.photos[0][-1].file_id,
                caption=caption, parse_mode="Markdown",
            )
            return
    except Exception as e:
        logging.error(f"Profile photo: {e}")
    await m.reply_text(caption.replace("*","").replace("_",""))

async def _show_id(m, context):
    cid    = str(m.chat.id)
    target = m.reply_to_message.from_user if m.reply_to_message else m.from_user
    rank   = await get_user_rank(context, m.chat.id, target.id)
    c_rank = get_custom_rank(cid, rank)
    msgs   = db_exec("SELECT msgs FROM stats WHERE chat_id=? AND user_id=?",
                     (cid, target.id), fetchone=True)
    msgs   = msgs[0] if msgs else 0
    full   = f"{target.first_name} {target.last_name or ''}"
    caption = (
        f"\n📊 **معلومات العضو** 📊\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 **الاسم:** {escape_md(full)}\n"
        f"🆔 **الايدي:** `{target.id}`\n"
        f"🔗 **المعرف:** {escape_md(f'@{target.username}' if target.username else 'لا يوجد')}\n"
        f"🎖 **الرتبة:** {escape_md(c_rank)}\n"
        f"💬 **الرسائل:** {msgs}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
    )
    try:
        photos = await context.bot.get_user_profile_photos(target.id, limit=1)
        if photos.total_count > 0:
            await context.bot.send_photo(
                m.chat.id, photos.photos[0][-1].file_id,
                caption=caption, parse_mode="Markdown",
            )
            return
    except:
        pass
    await m.reply_text(caption.replace("*","").replace("_",""))

async def _rank_of(m, context):
    tid, tname = await extract_target(context, m)
    if not tid:
        await m.reply_text("⌯ استخدم الرد أو @username")
        return
    rank   = await get_user_rank(context, m.chat.id, tid)
    c_rank = get_custom_rank(str(m.chat.id), rank)
    await m.reply_text(f"🎖 **رتبة {tname or tid}:** {c_rank}", parse_mode="Markdown")

async def _remove_all_ranks(m, rank, context, target_id=None, target_name=None):
    if rank not in ("مطور","مالك اساسي","مالك","مدير"):
        return
    tid   = target_id
    tname = target_name
    if tid is None:
        tid, tname = await extract_target(context, m)
    if not tid:
        await m.reply_text("⌯ لم أجد المستخدم!")
        return
    cid = str(m.chat.id)
    if not await can_punish(context, cid, m.from_user.id, tid):
        await m.reply_text("⌯ لا يمكنك تنزيل رتبة من هو أعلى منك!")
        return
    db_exec("DELETE FROM ranks WHERE chat_id=? AND user_id=?", (cid, tid), commit=True)
    await m.reply_text(f"⌯ تم تنزيل {tname or tid} إلى عضو عادي.")

async def _promote(m, rank, context):
    if rank not in ("مطور","مالك اساسي","مالك","مدير"):
        return
    cid = str(m.chat.id)
    tid, tname = await extract_target(context, m)
    if not tid:
        await m.reply_text("⌯ لم أجد المستخدم!")
        return
    if not await can_punish(context, cid, m.from_user.id, tid):
        await m.reply_text("⌯ لا يمكنك تعديل رتبة أعلى منك!")
        return
    action   = m.text.split()[0]
    valid    = ["مالك اساسي","مالك","مدير","ادمن","مميز","مشرف"]
    new_rank = next((r for r in valid if r in m.text), None)
    if not new_rank:
        await m.reply_text(f"⌯ رتبة غير صحيحة. المتاحة: {', '.join(valid)}")
        return
    if action == "رفع":
        db_exec("INSERT OR REPLACE INTO ranks VALUES (?,?,?)", (cid, tid, new_rank), commit=True)
        await m.reply_text(f"⌯ تم رفع {tname or tid} إلى {new_rank}")
    else:
        db_exec("DELETE FROM ranks WHERE chat_id=? AND user_id=? AND rank=?",
                (cid, tid, new_rank), commit=True)
        await m.reply_text(f"⌯ تم تنزيل {tname or tid} من {new_rank}")

async def _punish(m, rank, context):
    if rank not in ("مطور","مالك اساسي","مالك","مدير"):
        return
    cid = str(m.chat.id)
    tid, tname = await extract_target(context, m)
    if not tid:
        await m.reply_text("⌯ لم أجد المستخدم!")
        return
    me = await context.bot.get_me()
    if tid == me.id:
        await m.reply_text("⌯ لا أعاقب نفسي!")
        return
    if not await can_punish(context, cid, m.from_user.id, tid):
        await m.reply_text("⌯ لا يمكنك معاقبة من هو أعلى!")
        return

    text  = m.text
    parts = text.split()
    dur_str = None
    for dw in ["ثانيتين","دقيقتين","ساعتين","يومين","اسبوعين","شهرين"]:
        if dw in text:
            dur_str = dw; break
    if not dur_str:
        for i in range(len(parts)):
            if parts[i].isdigit() and i + 1 < len(parts):
                dur_str = f"{parts[i]} {parts[i+1]}"; break

    until_ts = (int(time.time()) + secs(dur_str)) if dur_str else None
    until    = datetime.fromtimestamp(until_ts) if until_ts else None
    disp     = tname or f"المستخدم {tid}"

    if "الغاء" in text:
        if "حظر" in text:
            try: await context.bot.unban_chat_member(cid, tid)
            except: pass
            db_exec("DELETE FROM punishments WHERE chat_id=? AND user_id=? AND type='ban'",
                    (cid, tid), commit=True)
            await m.reply_text(f"⌯ تم إلغاء حظر {disp}")
        elif "كتم" in text:
            db_exec("DELETE FROM punishments WHERE chat_id=? AND user_id=? AND type='mute'",
                    (cid, tid), commit=True)
            await m.reply_text(f"⌯ تم إلغاء كتم {disp}")
        elif "تقييد" in text:
            try:
                await context.bot.restrict_chat_member(
                    cid, tid,
                    permissions=ChatPermissions(
                        can_send_messages=True,
                        can_send_polls=True,
                        can_send_other_messages=True,
                        can_add_web_page_previews=True,
                    ),
                )
            except: pass
            db_exec("DELETE FROM punishments WHERE chat_id=? AND user_id=? AND type='restrict'",
                    (cid, tid), commit=True)
            await m.reply_text(f"⌯ تم إلغاء تقييد {disp}")
        return

    if "حظر" in text:
        try:
            kwargs = {"until_date": until_ts} if until_ts else {}
            await context.bot.ban_chat_member(cid, tid, **kwargs)
            db_exec("INSERT OR REPLACE INTO punishments VALUES (?,?,?,?)",
                    (cid, tid, "ban",
                     (until or datetime.now() + timedelta(days=365)).isoformat()), commit=True)
            await m.reply_text(f"⌯ تم حظر {disp}" + (f" لمدة {dur_str}" if dur_str else ""))
        except Exception as e:
            await m.reply_text(f"⌯ فشل: {e}")

    elif "كتم" in text:
        exp = until or datetime.now() + timedelta(days=365)
        db_exec("INSERT OR REPLACE INTO punishments VALUES (?,?,?,?)",
                (cid, tid, "mute", exp.isoformat()), commit=True)
        await m.reply_text(f"⌯ تم كتم {disp}" + (f" لمدة {dur_str}" if dur_str else ""))

    elif "تقييد" in text:
        try:
            kwargs = {"until_date": until_ts} if until_ts else {}
            await context.bot.restrict_chat_member(
                cid, tid,
                permissions=ChatPermissions(can_send_messages=False),
                **kwargs,
            )
            db_exec("INSERT OR REPLACE INTO punishments VALUES (?,?,?,?)",
                    (cid, tid, "restrict",
                     (until or datetime.now() + timedelta(days=365)).isoformat()), commit=True)
            await m.reply_text(f"⌯ تم تقييد {disp}" + (f" لمدة {dur_str}" if dur_str else ""))
        except Exception as e:
            await m.reply_text(f"⌯ فشل: {e}")

    elif "طرد" in text:
        try:
            await context.bot.ban_chat_member(cid, tid)
            await context.bot.unban_chat_member(cid, tid)
            await m.reply_text(f"⌯ تم طرد {disp}")
        except Exception as e:
            await m.reply_text(f"⌯ فشل: {e}")

async def _lock(m, rank, context):
    if rank not in ("مطور","مالك اساسي","مالك","مدير"):
        return
    parts  = m.text.split()
    action = parts[0]
    ltype  = " ".join(parts[1:])
    items  = {
        "الصور":"photo","الفيديو":"video","الملصقات":"sticker",
        "المتحركات":"animation","الملفات":"document","الصوت":"audio",
        "الروابط":"links","اليوزرات":"usernames","الدردشه":"chat","الكل":"all",
    }
    if ltype not in items:
        await m.reply_text(f"⌯ متاحة: {', '.join(items)}")
        return
    db_item = items[ltype]
    cid     = str(m.chat.id)
    if action == "قفل":
        db_exec("INSERT OR IGNORE INTO locks VALUES (?,?)", (cid, db_item), commit=True)
        await m.reply_text(f"⌯ تم قفل {ltype}")
    else:
        db_exec("DELETE FROM locks WHERE chat_id=? AND item=?", (cid, db_item), commit=True)
        await m.reply_text(f"⌯ تم فتح {ltype}")

async def _responses_cmd(m, rank, context):
    cid  = str(m.chat.id)
    text = m.text
    if text == "اضف رد":
        if rank not in ("مطور","مالك اساسي","مالك","مدير"):
            return
        add_response_state[m.from_user.id] = {"step": 1, "chat_id": cid}
        await m.reply_text("⌯ أرسل الكلمة المفتاحية:")
    elif text.startswith("مسح رد "):
        trig = text[8:].strip()
        db_exec("DELETE FROM responses WHERE chat_id=? AND trigger=?", (cid, trig), commit=True)
        await m.reply_text(f"⌯ تم مسح الرد على '{trig}'")
    elif text == "مسح الردود":
        db_exec("DELETE FROM responses WHERE chat_id=?", (cid,), commit=True)
        await m.reply_text("⌯ تم مسح جميع الردود")
    elif text == "الردود":
        rows = db_exec("SELECT trigger, reply_type FROM responses WHERE chat_id=?",
                       (cid,), fetchall=True) or []
        if not rows:
            await m.reply_text("⌯ لا توجد ردود")
        else:
            await m.reply_text("⌯ الردود المضافة:\n" +
                               "\n".join(f"• {r[0]} ({r[1]})" for r in rows))

async def _clean(m, rank, context):
    if rank not in ("مطور","مالك اساسي","مالك","مدير"):
        return
    if m.text == "مسح" and m.reply_to_message:
        try: await context.bot.delete_message(m.chat.id, m.reply_to_message.message_id)
        except: pass
        try: await context.bot.delete_message(m.chat.id, m.message_id)
        except: pass
    elif any(c.isdigit() for c in m.text):
        n = min(int("".join(filter(str.isdigit, m.text))), 100)
        for i in range(n):
            try: await context.bot.delete_message(m.chat.id, m.message_id - i)
            except: pass

async def _lists(m, rank, context):
    cid  = str(m.chat.id)
    text = m.text
    allowed = {
        "المطورين":            ("مطور",),
        "المالكيين الاساسيين": ("مطور",),
        "المالكيين":           ("مطور","مالك اساسي"),
        "المدراء":             ("مطور","مالك اساسي","مالك","مدير"),
        "الادمنيه":            ("مطور","مالك اساسي","مالك","مدير","ادمن"),
        "المميزين":            ("مطور","مالك اساسي","مالك","مدير","ادمن","مميز"),
        "المشرفين":            ("مطور","مالك اساسي","مالك","مدير"),
    }
    if text not in allowed or rank not in allowed[text]:
        await m.reply_text("⌯ ليس لديك صلاحية")
        return
    if text == "المطورين":
        await m.reply_text(f"⌯ المطور: @{DEV_USERNAME}")
    elif text == "المشرفين":
        try:
            admins = await context.bot.get_chat_administrators(cid)
            msg = "⌯ قائمة المشرفين:\n" + "\n".join(
                f"• {a.user.first_name} (@{a.user.username or 'لا يوجد'})" for a in admins
            )
            await m.reply_text(msg)
        except:
            await m.reply_text("⌯ لا يمكن جلب القائمة")
    else:
        rmap = {
            "المالكيين الاساسيين":"مالك اساسي","المالكيين":"مالك",
            "المدراء":"مدير","الادمنيه":"ادمن","المميزين":"مميز",
        }
        t_rank = rmap.get(text)
        if t_rank:
            rows = db_exec("SELECT user_id FROM ranks WHERE chat_id=? AND rank=?",
                           (cid, t_rank), fetchall=True) or []
            if not rows:
                await m.reply_text(f"⌯ لا يوجد {t_rank}")
            else:
                msg = f"⌯ قائمة {t_rank}:\n"
                for (uid,) in rows:
                    try:
                        u = (await context.bot.get_chat_member(cid, uid)).user
                        msg += f"• {u.first_name} (@{u.username or 'لا يوجد'}) - {uid}\n"
                    except:
                        msg += f"• مستخدم غادر - {uid}\n"
                await m.reply_text(msg)

# ═══════════════════════════════════════════════
#     لوحة التحكم: رفع/تنزيل المشرفين (Inline)
# ═══════════════════════════════════════════════
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user):
        return
    chats = context.bot_data.get("known_chats", {})
    if not chats:
        await update.message.reply_text("⚠️ لا توجد قنوات/جروبات بعد. أضف البوت وأرسل رسالة.")
        return
    kb = [
        [InlineKeyboardButton(f"📢 {title}", callback_data=f"sc:{cid}")]
        for cid, title in chats.items()
    ]
    await update.message.reply_text(
        "🎯 **لوحة تحكم البوت**\n\nاختر الجروب/القناة:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown",
    )

async def on_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_authorized(q.from_user):
        return
    chat_id = int(q.data.split(":")[1])
    title   = context.bot_data.get("known_chats", {}).get(str(chat_id), str(chat_id))
    rows    = db_exec(
        "SELECT user_id FROM bot_managed_admins WHERE chat_id=?",
        (str(chat_id),), fetchall=True,
    ) or []
    managed = [r[0] for r in rows]
    kb = []
    for uid in managed:
        try:
            info = await context.bot.get_chat(uid)
            name = (info.full_name or info.username or str(uid))[:28]
        except:
            name = str(uid)
        kb.append([
            InlineKeyboardButton(f"👑 {name}", callback_data="noop"),
            InlineKeyboardButton("🔻 تنزيل",  callback_data=f"dm:{chat_id}:{uid}"),
        ])
    kb.append([InlineKeyboardButton("➕ رفع عضو بـ ID أو @يوزر", callback_data=f"ai:{chat_id}")])
    txt = (
        f"👥 **{title}**\n\n"
        f"{'المشرفون المُدارون بالبوت:' if managed else 'لا يوجد مشرفون مُدارون بعد.'}\n"
        f"استخدم ➕ لرفع شخص جديد."
    )
    await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def on_demote_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_authorized(q.from_user):
        return
    _, cid, uid = q.data.split(":")
    chat_id, user_id = int(cid), int(uid)
    await _demote_admin(context, chat_id, user_id)
    try:
        info = await context.bot.get_chat(user_id)
        name = info.full_name or str(user_id)
    except:
        name = str(user_id)
    await q.edit_message_text(f"🔻 تم تنزيل **{name}** من المشرفين.", parse_mode="Markdown")

async def on_ask_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_authorized(q.from_user):
        return
    context.user_data["await_promote_cid"] = q.data.split(":")[1]
    await q.edit_message_text(
        "✏️ أرسل **User ID** أو **@يوزر** للعضو المراد رفعه:",
        parse_mode="Markdown",
    )

async def on_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        return
    if "await_promote_cid" not in context.user_data:
        return
    cid  = int(context.user_data.pop("await_promote_cid"))
    text = update.message.text.strip()
    try:
        target = text if text.startswith("@") else int(text)
        await _promote_admin(context, update.message, cid, target)
    except ValueError:
        await update.message.reply_text("❌ أدخل User ID رقم أو @يوزر.")

async def on_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

# ═══════════════════════════════════════════════
#          دالة الكشف المساعدة
# ═══════════════════════════════════════════════
async def _run_detection_and_reply(msg, context):
    """تنفيذ عملية الكشف وإرسال النتيجة للمستخدم"""
    try:
        top_pairs = await detection.find_top_similar_pairs(max_users=12, top_n=2)
        if not top_pairs:
            await msg.reply_text("⚠️ لا توجد بيانات كافية في القناة لإجراء الكشف.")
            return

        answer = "🔍 **نتائج كشف الحسابات المتشابهة**\n\n"
        for idx, pair in enumerate(top_pairs, 1):
            answer += (
                f"┏━━ **الزوج {idx}**\n"
                f"┣ 👤 {pair['user1']}  ⇄  {pair['user2']}\n"
                f"┣ 📊 نسبة التشابه: `{pair['similarity']}%`\n"
                f"┗ 📝 {pair['report'][:200]}...\n\n"
            )
        await msg.reply_text(answer, parse_mode="Markdown")
    except Exception as e:
        await msg.reply_text(f"❌ حدث خطأ أثناء الكشف: {str(e)}")
        logging.error(f"Detection error: {e}")

# ═══════════════════════════════════════════════
#                   التشغيل
# ═══════════════════════════════════════════════
if __name__ == "__main__":
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .read_timeout(15)
        .write_timeout(15)
        .connect_timeout(15)
        .build()
    )

    media_filter = (
        filters.PHOTO | filters.Sticker.ALL | filters.ANIMATION |
        filters.VIDEO | filters.VIDEO_NOTE | filters.Document.ALL
    )

    app.add_handler(MessageHandler(media_filter, handle_media), group=0)

    app.add_handler(
        MessageHandler(filters.TEXT & filters.REPLY & filters.Regex(r"^ادمن$"), handle_report),
        group=1,
    )

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
        group=2,
    )

    app.add_handler(CommandHandler("start", start_cmd))

    app.add_handler(CallbackQueryHandler(on_select,   pattern=r"^sc:"))
    app.add_handler(CallbackQueryHandler(on_demote_cb,pattern=r"^dm:"))
    app.add_handler(CallbackQueryHandler(on_ask_id,   pattern=r"^ai:"))
    app.add_handler(CallbackQueryHandler(on_noop,     pattern=r"^noop$"))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            on_id_input,
        ),
        group=3,
    )

    print("🚀 البوت المدمج يعمل — يراقب جميع الجروبات والقنوات تلقائياً ⚡")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
