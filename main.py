import socket
_orig = socket.getaddrinfo
def _patch(host, port, *a, **k):
    _map = {
        'api-inference.huggingface.co': '18.184.233.10',
        'translate.googleapis.com':     '142.250.185.138',
    }
    if host in _map:
        host = _map[host]
    return _orig(host, port, *a, **k)
socket.getaddrinfo = _patch

import logging
import os
import requests
from urllib.parse import quote
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import asyncio

API_TOKEN = os.environ.get('API_TOKEN')
HF_TOKEN  = os.environ.get('HF_TOKEN')

logging.basicConfig(level=logging.INFO)
bot     = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp      = Dispatcher(storage=storage)

GENRE_MAP = {
    "lofi":       "🎧 Lo-Fi & Chill",
    "epic":       "⚔️ ملحمي (Epic/Cinematic)",
    "arabic":     "🎶 عربي (Arabic Oriental)",
    "electronic": "🎛️ إلكتروني (Electronic/EDM)",
    "jazz":       "🎷 جاز (Jazz & Blues)",
    "ambient":    "🌌 هادئ (Ambient/Meditation)",
}

HF_API_URL = "https://api-inference.huggingface.co/models/facebook/musicgen-small"

def generate_music(prompt: str) -> bytes | None:
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    payload = {
        "inputs": prompt,
        "parameters": {"max_new_tokens": 512, "do_sample": True},
        "options": {"wait_for_model": True, "use_cache": False}
    }
    try:
        logging.info(f"Calling HF API...")
        resp = requests.post(HF_API_URL, headers=headers, json=payload, timeout=120, verify=False)
        logging.info(f"HF Response: {resp.status_code}")
        if resp.status_code == 200:
            return resp.content
        logging.error(f"HF Error {resp.status_code}: {resp.text[:300]}")
        return None
    except Exception as e:
        logging.error(f"HF Exception: {e}")
        return None

def translate_to_english(text: str) -> str:
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=en&dt=t&q={quote(text)}"
        return requests.get(url, timeout=5, verify=False).json()[0][0][0]
    except:
        return text

class MusicStates(StatesGroup):
    waiting_for_genre = State()

@dp.message(Command("start", "help"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🎵 <b>أهلاً بك في بوت توليد الموسيقى!</b>\n\n"
        "✏️ أرسل وصف الموسيقى التي تريدها\n"
        "مثال: <i>موسيقى هادئة للنوم</i>",
        parse_mode="HTML"
    )

@dp.message(F.text)
async def handle_prompt(message: types.Message, state: FSMContext):
    await state.update_data(user_prompt=message.text.strip())
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="🎧 Lo-Fi",    callback_data="genre_lofi"),
            types.InlineKeyboardButton(text="⚔️ ملحمي",    callback_data="genre_epic"),
        ],
        [
            types.InlineKeyboardButton(text="🎶 عربي",     callback_data="genre_arabic"),
            types.InlineKeyboardButton(text="🎛️ إلكتروني", callback_data="genre_electronic"),
        ],
        [
            types.InlineKeyboardButton(text="🎷 جاز",      callback_data="genre_jazz"),
            types.InlineKeyboardButton(text="🌌 هادئ",     callback_data="genre_ambient"),
        ],
    ])
    await message.answer("👇 <b>اختر نوع الموسيقى:</b>", reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(MusicStates.waiting_for_genre)

@dp.callback_query(F.data.startswith("genre_"), MusicStates.waiting_for_genre)
async def handle_genre(call: types.CallbackQuery, state: FSMContext):
    chat_id   = call.message.chat.id
    genre_key = call.data.split("_", 1)[1]
    data      = await state.get_data()
    original  = data.get("user_prompt")

    if not original:
        await call.answer("❌ انتهت الجلسة، أرسل وصفاً جديداً.", show_alert=True)
        await state.clear()
        return

    await call.message.edit_text("🎵 <b>جاري توليد الموسيقى... ⏳ (30-60 ثانية)</b>", parse_mode="HTML")

    english = translate_to_english(original)
    prompts = {
        "lofi":       f"{english}, lofi hip hop, relaxing beats, chill, mellow",
        "epic":       f"{english}, epic cinematic orchestral, dramatic, powerful",
        "arabic":     f"{english}, arabic oriental music, oud, qanun, middle eastern",
        "electronic": f"{english}, electronic EDM, synthesizer, energetic, dance",
        "jazz":       f"{english}, jazz music, saxophone, piano, smooth, swing",
        "ambient":    f"{english}, ambient meditation, peaceful, soft pads, calm",
    }

    audio = generate_music(prompts.get(genre_key, english))

    if audio and len(audio) > 1000:
        path = f"/tmp/music_{chat_id}.wav"
        with open(path, "wb") as f:
            f.write(audio)
        audio_file = types.BufferedInputFile(open(path, "rb").read(), filename="music.wav")
        await bot.send_audio(
            chat_id=chat_id,
            audio=audio_file,
            title=f"🎵 {original[:40]}",
            performer="AI Music Bot",
            caption=(
                f"✨ <b>تم التوليد!</b>\n"
                f"✏️ <b>الوصف:</b> « {original} »\n"
                f"🎭 <b>النوع:</b> {GENRE_MAP.get(genre_key, genre_key)}"
            ),
            parse_mode="HTML"
        )
        os.remove(path)
        await call.message.delete()
    else:
        await call.message.edit_text("❌ فشل التوليد، حاول مرة أخرى.")

    await state.clear()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
