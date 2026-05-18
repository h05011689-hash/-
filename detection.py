# detection.py
import asyncio
import re
import requests
from typing import List, Dict, Any
from pyrogram import Client

# ════════════════════════════════════════
# بيانات الدخول
API_ID         = 26604893
API_HASH       = "b4dad6237531036f1a4bb2580e4985b1"
SESSION_STRING = "BAGV9V0Af_3r8brUqcEEKfZ0pS6m2mi7vBHXvW-WAeAAd2HCL5xluUtUStq0VslHxtbpgfVKIXRKi9CrWRJWudKeOLA1fHXnwt5c2_hYQiAT2OW4IMrGzWCMrKRrTL2E8yA1AAygPnT7J3jejpylQi0HRavgx-CzlDcBPFB-G6-zgnTi5TKzyuFo9LxpOjV0hjna8nIXHGPX4cgC2QxuD2Dmy8_htVb-uxPIiu5MIcD15ErSyT4mP-A6r3nZb0XAlRaJ9K3CM9a01icSCv19BpFl0QbVtdPvY8zBdRba8aFAAuRBGNYI4akLKKRvHAHXXLMa3dNdLBWOsGBu7UTMn6KCNJgavAAAAAHloT2vAA"
TARGET_CHANNEL = "clanarba"
GROQ_API_KEY   = "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"

# ════════════════════════════════════════
# دوال مساعدة
def _extract_percentage(text: str) -> int:
    """استخراج أول نسبة مئوية من النص"""
    match = re.search(r'(\d+)%', text)
    return int(match.group(1)) if match else 0

def _calc_timing_overlap(times1: list, times2: list) -> int:
    """
    حساب التشابه الزمني بين مستخدمين:
    كم مرة أرسلا رسائل في نفس الساعة (فارق أقل من 5 دقائق)
    """
    if not times1 or not times2:
        return 0
    overlap = 0
    for t1 in times1:
        for t2 in times2:
            if abs(t1 - t2) < 300:  # 5 دقائق
                overlap += 1
    # نسبة مئوية من الأصغر
    base = min(len(times1), len(times2))
    return min(100, int((overlap / base) * 100)) if base else 0

async def get_user_data(app: Client, user_id: int, limit: int = 200) -> dict:
    """
    جلب بيانات المستخدم: رسائله + أوقات إرسالها + معلوماته
    """
    corpus = []
    timestamps = []
    try:
        async for msg in app.search_messages(TARGET_CHANNEL, from_user=user_id, limit=limit):
            if msg.text and len(msg.text.strip()) > 2:
                corpus.append(msg.text.strip())
            if msg.date:
                timestamps.append(msg.date.timestamp())
    except Exception as e:
        pass
    return {
        "corpus": corpus,
        "text": " | ".join(corpus),
        "timestamps": timestamps,
        "msg_count": len(corpus),
    }

async def analyze_pair_groq(data1: dict, data2: dict, user1: str, user2: str) -> Dict[str, Any]:
    """
    تحليل زوج من المستخدمين للكشف عن الحسابات الوهمية/المتشابهة
    """
    # ══ حساب التشابه الزمني مسبقاً ══
    timing_score = _calc_timing_overlap(data1["timestamps"], data2["timestamps"])

    text1 = data1["text"][:2500] if data1["text"] else "لا توجد رسائل"
    text2 = data2["text"][:2500] if data2["text"] else "لا توجد رسائل"

    prompt = f"""أنت خبير أمني متخصص في كشف الحسابات الوهمية والمتشابهة في مجموعات تيليجرام العربية.

مهمتك: هل هذان الحسابان قد يكونان لنفس الشخص أو متواطئين؟

═══ بيانات المستخدم الأول (@{user1}) ═══
عدد الرسائل المحللة: {data1['msg_count']}
رسائله:
{text1}

═══ بيانات المستخدم الثاني (@{user2}) ═══
عدد الرسائل المحللة: {data2['msg_count']}
رسائله:
{text2}

═══ معلومة إضافية ═══
نسبة التشابه الزمني (إرسال في نفس الوقت): {timing_score}%

ابحث عن هذه المؤشرات تحديداً:
1. **نفس الأخطاء الإملائية المتكررة** (مثال: يكتبان "شكراً" بنفس الطريقة الخاطئة دائماً)
2. **نفس التعبيرات والكلمات النادرة** (عبارات مميزة يستخدمها الاثنان)
3. **التنسيق المتطابق** (إيموجيات في نفس المواضع، علامات ترقيم مشابهة)
4. **التوقيت المتزامن** (يرسلان معاً في أوقات قريبة جداً — {timing_score}%)
5. **المواضيع المتطابقة** (يتحدثان عن نفس الأشياء بنفس الرأي دائماً)
6. **الدفاع المتبادل** (كل واحد يدافع عن الآخر دائماً)

**ملاحظة مهمة:** إذا كانت الرسائل قليلة جداً أو غير كافية، قل ذلك صراحةً ولا تخمّن.

أجب بهذا التنسيق الدقيق فقط:
نسبة التشابه: XX%
المؤشرات المكتشفة: [اذكرها أو "لا توجد مؤشرات كافية"]
الحكم: [مشبوه / غير مشبوه / بيانات غير كافية]
"""

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 400
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=40)
        resp.raise_for_status()
        result_text = resp.json()["choices"][0]["message"]["content"]

        # ══ استخراج النسبة ودمجها مع التوقيت ══
        ai_percent  = _extract_percentage(result_text)
        # الجمع الموزون: 70% من رأي الذكاء الاصطناعي + 30% من التوقيت
        final_score = int(ai_percent * 0.7 + timing_score * 0.3)

        return {
            "user1":      user1,
            "user2":      user2,
            "similarity": final_score,
            "timing":     timing_score,
            "report":     result_text,
        }
    except Exception as e:
        return {
            "user1":      user1,
            "user2":      user2,
            "similarity": timing_score,  # نرجع التوقيت على الأقل
            "timing":     timing_score,
            "report":     f"فشل تحليل الذكاء الاصطناعي: {str(e)}\nالتشابه الزمني فقط: {timing_score}%",
        }

async def find_top_similar_pairs(max_users: int = 15, top_n: int = 3) -> List[Dict[str, Any]]:
    """
    جلب المستخدمين النشطين في القناة ومقارنتهم للكشف عن الحسابات المشبوهة.
    يُرجع فقط الأزواج التي نسبتها > 20% أو إذا لم يوجد يُبلّغ بذلك.
    """
    async with Client(
        "detect_session",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION_STRING,
        in_memory=True
    ) as app:

        # ══ 1. جمع المستخدمين الفعليين النشطين ══
        users_data: Dict[int, dict] = {}  # {user_id: {username, first_name}}
        msg_count_map: Dict[int, int] = {}

        async for message in app.get_chat_history(TARGET_CHANNEL, limit=1000):
            if not message.from_user:
                continue
            uid  = message.from_user.id
            uname = message.from_user.username or ""
            # تجاهل البوتات والقنوات
            if message.from_user.is_bot:
                continue
            if uid not in users_data:
                users_data[uid] = {
                    "username":   uname,
                    "first_name": message.from_user.first_name or "",
                }
                msg_count_map[uid] = 0
            msg_count_map[uid] += 1

        # ══ 2. فلترة: أكثر من 10 رسائل فقط ══
        active_users = [
            uid for uid, cnt in msg_count_map.items()
            if cnt >= 10
        ]

        # ترتيب حسب الأكثر نشاطاً وأخذ max_users
        active_users.sort(key=lambda u: msg_count_map[u], reverse=True)
        active_users = active_users[:max_users]

        if len(active_users) < 2:
            return []

        # ══ 3. جلب بيانات كل مستخدم ══
        corpora: Dict[int, dict] = {}
        for uid in active_users:
            data = await get_user_data(app, uid, limit=200)
            # نحتاج على الأقل 5 رسائل نصية
            if data["msg_count"] >= 5:
                corpora[uid] = data

        if len(corpora) < 2:
            return []

        # ══ 4. مقارنة كل زوج ══
        tasks = []
        uid_list = list(corpora.keys())
        pairs_meta = []

        for i in range(len(uid_list)):
            for j in range(i + 1, len(uid_list)):
                u1_id = uid_list[i]
                u2_id = uid_list[j]
                u1_name = users_data[u1_id].get("username") or users_data[u1_id].get("first_name") or str(u1_id)
                u2_name = users_data[u2_id].get("username") or users_data[u2_id].get("first_name") or str(u2_id)
                tasks.append(
                    analyze_pair_groq(corpora[u1_id], corpora[u2_id], u1_name, u2_name)
                )
                pairs_meta.append((u1_name, u2_name))

        results = await asyncio.gather(*tasks)

        # ══ 5. فلترة النتائج: فقط المشبوهة (>20%) ثم ترتيب تنازلي ══
        suspicious = [r for r in results if r["similarity"] > 20]
        suspicious.sort(key=lambda x: x["similarity"], reverse=True)

        # لو مفيش مشبوه نرجع أعلى top_n على أي حال مع تنبيه
        if not suspicious:
            all_sorted = sorted(results, key=lambda x: x["similarity"], reverse=True)
            for r in all_sorted[:top_n]:
                r["no_suspicious"] = True
            return all_sorted[:top_n]

        return suspicious[:top_n]
