# detection.py
import asyncio
import re
import requests
from typing import List, Tuple, Dict, Any
from pyrogram import Client

# ════════════════════════════════════════
# بيانات الدخول – يجب تعديلها حسب حسابك
# (نفس البيانات الموجودة في البوت الثاني)
API_ID = 26604893
API_HASH = "b4dad6237531036f1a4bb2580e4985b1"
SESSION_STRING = "BAGV9V0Af_3r8brUqcEEKfZ0pS6m2mi7vBHXvW-WAeAAd2HCL5xluUtUStq0VslHxtbpgfVKIXRKi9CrWRJWudKeOLA1fHXnwt5c2_hYQiAT2OW4IMrGzWCMrKRrTL2E8yA1AAygPnT7J3jejpylQi0HRavgx-CzlDcBPFB-G6-zgnTi5TKzyuFo9LxpOjV0hjna8nIXHGPX4cgC2QxuD2Dmy8_htVb-uxPIiu5MIcD15ErSyT4mP-A6r3nZb0XAlRaJ9K3CM9a01icSCv19BpFl0QbVtdPvY8zBdRba8aFAAuRBGNYI4akLKKRvHAHXXLMa3dNdLBWOsGBu7UTMn6KCNJgavAAAAAHloT2vAA"
TARGET_CHANNEL = "clanarba"   # القناة التي يتم فحصها
GROQ_API_KEY = "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"

# ════════════════════════════════════════
# دوال مساعدة
def _extract_percentage(text: str) -> int:
    """استخراج أول نسبة مئوية من النص (مثل '97%')"""
    match = re.search(r'(\d+)%', text)
    return int(match.group(1)) if match else 0

async def get_user_corpus(app: Client, username: str, limit: int = 300) -> str:
    """جلب آخر limit رسالة نصية لمستخدم معين في القناة"""
    corpus = []
    try:
        user = await app.get_users(username)
    except:
        return ""
    async for message in app.search_messages(TARGET_CHANNEL, from_user=user.id, limit=limit):
        if message.text:
            corpus.append(message.text.strip())
    return " | ".join(corpus) if corpus else ""

async def analyze_pair_groq(corpus1: str, corpus2: str, user1: str, user2: str) -> Dict[str, Any]:
    """إرسال زوج من النصوص إلى Groq لتحليل التشابه وإرجاع نسبة التطابق والتقرير"""
    prompt = f"""
أنت "القاضي الرقمي". قارن بين مستخدمين بناءً على أرشيف رسائلهما.

المستخدم الأول (@{user1}):
{corpus1[:3000]}

المستخدم الثاني (@{user2}):
{corpus2[:3000]}

المطلوب:
1. نسبة التشابه الرقمية (0-100%) بناءً على (الأسلوب، الأخطاء الإملائية، الإيموجيات، طريقة الرد).
2. تقرير مختصر بالأدلة الدامغة (إن وجدت).

أجب بدقة بهذا التنسيق:
نسبة التشابه: XX%
التقرير: ...
"""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 300
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()["choices"][0]["message"]["content"]
        percent = _extract_percentage(result)
        return {"user1": user1, "user2": user2, "similarity": percent, "report": result}
    except Exception as e:
        return {"user1": user1, "user2": user2, "similarity": 0, "report": f"فشل التحليل: {str(e)}"}

async def find_top_similar_pairs(max_users: int = 15, top_n: int = 2) -> List[Dict[str, Any]]:
    """
    جلب المستخدمين النشطين في القناة، مقارنة كل زوج، وإعادة أفضل top_n أزواج حسب التشابه.
    كل عنصر في القائمة يحتوي على user1, user2, similarity, report.
    """
    async with Client("detect_session", api_id=API_ID, api_hash=API_HASH,
                      session_string=SESSION_STRING, in_memory=True) as app:
        # 1. جمع المستخدمين الذين أرسلوا رسائل في القناة
        users_set = set()
        async for message in app.search_messages(TARGET_CHANNEL, limit=500):
            if message.from_user and message.from_user.username:
                users_set.add(message.from_user.username)
        users = list(users_set)[:max_users]
        if len(users) < 2:
            return []

        # 2. جلب النصوص لكل مستخدم (تجنب من لا يملك نصوصاً كافية)
        user_corpus = {}
        for uname in users:
            corpus = await get_user_corpus(app, uname, limit=200)
            if len(corpus.split()) > 50:   # على الأقل 50 كلمة
                user_corpus[uname] = corpus

        if len(user_corpus) < 2:
            return []

        # 3. مقارنة كل زوج (باستخدام Groq)
        tasks = []
        user_list = list(user_corpus.keys())
        for i in range(len(user_list)):
            for j in range(i+1, len(user_list)):
                u1, u2 = user_list[i], user_list[j]
                tasks.append(analyze_pair_groq(user_corpus[u1], user_corpus[u2], u1, u2))
        results = await asyncio.gather(*tasks)

        # 4. ترتيب حسب التشابه (تنازلي) وأخذ top_n
        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:top_n]
