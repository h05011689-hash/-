import logging
import os
import requests
from urllib.parse import quote
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

# ================================================================
API_TOKEN = os.environ.get('API_TOKEN', '8635160416:AAHPp8DT1DKmersIg-sPLz5QQDxaj7vbqfc')
HF_TOKEN  = os.environ.get('HF_TOKEN',  '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')
# ================================================================

logging.basicConfig(level=logging.INFO)
bot     = Bot(token=API_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp      = Dispatcher(bot, storage=storage)

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
        resp = requests.post(HF_API_URL, headers=headers, json=payload, timeout=120)
        if resp.status_code == 200:
            return resp.content
        logging.error(f"HF Error {resp.status_code}: {resp.text}")
        return None
    except Exception as e:
        logging.error(f"HF Exception: {e}")
        return None

def translate_to_english(text: str) -> str:
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=en&dt=t&q={quote(text)}"
        return requests.get(url, timeout=5).json()[0][0][0]
    except:
        return text

class MusicStates(StatesGroup):
    waiting_for_genre = State()

@dp.message_handler(commands=['start', 'help'], state="*")
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer(
        "🎵 <b>أهلاً بك في بوت توليد الموسيقى!</b>\n\n"
        "✏️ أرسل وصف الموسيقى التي تريدها\n"
        "مثال: <i>موسيقى هادئة للنوم</i>"
    )

@dp.message_handler(content_types=types.ContentType.TEXT, state="*")
async def handle_prompt(message: types.Message, state: FSMContext):
    await state.update_data(user_prompt=message.text.strip())
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("🎧 Lo-Fi",       callback_data="genre_lofi"),
        types.InlineKeyboardButton("⚔️ ملحمي",       callback_data="genre_epic"),
        types.InlineKeyboardButton("🎶 عربي",        callback_data="genre_arabic"),
        types.InlineKeyboardButton("🎛️ إلكتروني",    callback_data="genre_electronic"),
        types.InlineKeyboardButton("🎷 جاز",         callback_data="genre_jazz"),
        types.InlineKeyboardButton("🌌 هادئ",        callback_data="genre_ambient"),
    )
    await bot.send_message(message.chat.id, "👇 <b>اختر نوع الموسيقى:</b>", reply_markup=keyboard)
    await MusicStates.waiting_for_genre.set()

@dp.callback_query_handler(lambda c: c.data.startswith('genre_'), state=MusicStates.waiting_for_genre)
async def handle_genre(call: types.CallbackQuery, state: FSMContext):
    chat_id   = call.message.chat.id
    genre_key = call.data.split('_', 1)[1]
    data      = await state.get_data()
    original  = data.get('user_prompt')

    if not original:
        await call.answer("❌ انتهت الجلسة، أرسل وصفاً جديداً.", show_alert=True)
        await state.finish()
        return

    await call.message.edit_text("🎵 <b>جاري توليد الموسيقى... ⏳ (30-60 ثانية)</b>")

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
        with open(path, "rb") as f:
            await bot.send_audio(
                chat_id=chat_id,
                audio=f,
                title=f"🎵 {original[:40]}",
                performer="AI Music Bot",
                caption=(
                    f"✨ <b>تم التوليد!</b>\n"
                    f"✏️ <b>الوصف:</b> « {original} »\n"
                    f"🎭 <b>النوع:</b> {GENRE_MAP.get(genre_key, genre_key)}"
                )
            )
        os.remove(path)
        await bot.delete_message(chat_id, call.message.message_id)
    else:
        await bot.send_message(chat_id, "❌ فشل التوليد، حاول مرة أخرى.")

    await state.finish()

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
