import asyncio
import re
import requests
import logging
from typing import List, Dict, Any
from collections import Counter
from pyrogram import Client

# ════════════════════════════════════════
# بيانات الدخول
API_ID         = 26604893
API_HASH       = "b4dad6237531036f1a4bb2580e4985b1"
SESSION_STRING = "BAGV9V0Af_3r8brUqcEEKfZ0pS6m2mi7vBHXvW-WAeAAd2HCL5xluUtUStq0VslHxtbpgfVKIXRKi9CrWRJWudKeOLA1fHXnwt5c2_hYQiAT2OW4IMrGzWCMrKRrTL2E8yA1AAygPnT7J3jejpylQi0HRavgx-CzlDcBPFB-G6-zgnTi5TKzyuFo9LxpOjV0hjna8nIXHGPX4cgC2QxuD2Dmy8_htVb-uxPIiu5MIcD15ErSyT4mP-A6r3nZb0XAlRaJ9K3CM9a01icSCv19BpFl0QbVtdPvY8zBdRba8aFAAuRBGNYI4akLKKRvHAHXXLMa3dNdLBWOsGBu7UTMn6KCNJgavAAAAAHloT2vAA"
TARGET_CHANNEL = "clanarba"
GROQ_API_KEY   = "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"

logging.basicConfig(level=logging.INFO)

# ════════════════════════════════════════
def _extract_percentage(text: str) -> int:
    match = re.search(r'(?:نسبة التشابه|percentage|similarity):\s*(\d+)', text, re.IGNORECASE)
    if not match:
        match = re.search(r'(\d+)\s*%', text)
    return min(100, int(match.group(1))) if match else 0

def _calc_timing_overlap(times1: list, times2: list) -> int:
    """
    حساب التشابه الزمني الذكي:
    يقسم الوقت الدقيق لـ (دقائق)، ويحسب كم مرة التقى الطرفان في نفس الدقيقة.
    يمنع الـ 100% الوهمية الناتجة عن عدد رسائل قليل.
    """
    if not times1 or not times2:
        return 0
    
    # تحويل التوقيت لدقائق وحساب التكرارات
    buckets1 = Counter(int(t // 60) for t in times1)
    buckets2 = Counter(int(t // 60) for t in times2)
    
    # حساب التقاطعات الفعلية بناءً على التكرار الأقل في الدقيقة المشتركة
    common_minutes = set(buckets1.keys()) & set(buckets2.keys())
    total_overlaps = sum(min(buckets1[m], buckets2[m]) for m in common_minutes)
    
    total_msg_count = min(len(times1), len(times2))
    if total_msg_count == 0:
        return 0
        
    score = int((total_overlaps / total_msg_count) * 100)
    
    # كبح جماح النسبة: إذا كان عدد الرسائل المشتركة الكلي أقل من 3، لا يمكن إعطاء نسبة عالية
    if total_overlaps < 3:
        score = min(score, 25)
        
    return min(100, score)

# ════════════════════════════════════════
async def _collect_members(app: Client, limit: int = 800) -> Dict[int, dict]:
    users_data: Dict[int, dict] = {}
    count = 0

    try:
        async for msg in app.get_chat_history(TARGET_CHANNEL, limit=limit):
            count += 1
            u = msg.from_user
            if not u or u.is_bot:
                continue
            uid = u.id
            if uid not in users_data:
                users_data[uid] = {
                    "username":   u.username or "",
                    "first_name": u.first_name or str(uid),
                    "messages":   [],
                    "timestamps": [],
                    "msg_count":  0,
                }
            if msg.text and len(msg.text.strip()) > 2:
                users_data[uid]["messages"].append(msg.text.strip())
            if msg.date:
                users_data[uid]["timestamps"].append(msg.date.timestamp())
            users_data[uid]["msg_count"] += 1

    except Exception as e:
        logging.warning(f"get_chat_history failed ({e}), trying search_messages...")
        try:
            async for msg in app.search_messages(TARGET_CHANNEL, limit=limit):
                u = msg.from_user
                if not u or u.is_bot:
                    continue
                uid = u.id
                if uid not in users_data:
                    users_data[uid] = {
                        "username":   u.username or "",
                        "first_name": u.first_name or str(uid),
                        "messages":   [],
                        "timestamps": [],
                        "msg_count":  0,
                    }
                if msg.text and len(msg.text.strip()) > 2:
                    users_data[uid]["messages"].append(msg.text.strip())
                if msg.date:
                    users_data[uid]["timestamps"].append(msg.date.timestamp())
                users_data[uid]["msg_count"] += 1
        except Exception as e2:
            logging.error(f"search_messages also failed: {e2}")

    logging.info(f"جُمع {len(users_data)} مستخدم من {count} رسالة")
    return users_data

# ════════════════════════════════════════
async def _fetch_more_messages(app: Client, uid: int, existing: list, limit: int = 200) -> list:
    extra = []
    try:
        async for msg in app.search_messages(TARGET_CHANNEL, from_user=uid, limit=limit):
            if msg.text and len(msg.text.strip()) > 2:
                t = msg.text.strip()
                if t not in existing:
                    extra.append(t)
    except Exception as e:
        logging.warning(f"fetch_more for {uid}: {e}")
    return extra

# ════════════════════════════════════════
async def analyze_pair_groq(
    data1: dict, data2: dict,
    user1: str, user2: str,
    timing_score: int
) -> Dict[str, Any]:
    """تحليل زوج بالذكاء الاصطناعي بأعلى درجات الصرامة القضائية"""

    # إذا كانت الرسائل فارغة تماماً، نمنع الـ AI من الهبد ونعطي صفر فوراً
    if not data1["messages"] or not data2["messages"]:
        return {
            "user1": user1, "user2": user2,
            "similarity": 0, "ai_score": 0, "timing": timing_score,
            "report": "بيانات غير كافية - أحد الطرفين أو كلاهما لا يملك نصوصاً حقيقية للأرشفة."
        }

    text1 = " | ".join(data1["messages"])[:3000]
    text2 = " | ".join(data2["messages"])[:3000]

    prompt = f"""أنت رئيس المحكمة الجنائية الرقمية لكشف الحسابات البديلة (Multi-accounts) في مجتمعات الألعاب العربية.
مهمتك: النطق بالحكم بناءً على أدلة "لاوعية" حقيقية، وليس مجرد كلام إنشائي عام.

[المتحدث الأول: {user1}]
{text1}

[المتحدث الثاني: {user2}]
{text2}

[مؤشر التزامن الزمني المحسوب رياضياً: {timing_score}%]

قواعد الإدانة الصارمة:
1. اترك الكليشيهات (مثل لغة الشارع، مرح، حزين). الكل في التلجرام يكتب هكذا.
2. ابحث عن الأدلة القاتلة: هل ينادون نفس الأشخاص (مثل: ياسر، مصطفى، دهش) بنفس الطريقة أو السياق؟
3. هل يتصنع أحدهم لهجة أخرى لتمثيل مسرحية خناق وهمي لإبعاد الشبهات؟
4. راقب العادات اللغوية الثابتة: (مكان وضع الإيموجي، عدد حروف الضحكة "هههه"، وعلامات الترقيم "؟؟").

إذا كانت الرسائل قصيرة أو عادية جداً (مثل: تمام، هلا، منور)، اجعل النسبة 0% واكتب "بيانات غير كافية". لا تخترع شبهاً من العدم.

أجب بدقة بهذا التنسيق المقتضب فقط:
نسبة التشابه: [رقم]%
المؤشرات: [نقاط مختصرة جداً للأدلة]
الحكم: [مشبوه جداً / اشتباه متوسط / غير مشبوه / بيانات غير كافية]"""

    url     = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model":       "llama-3.3-70b-versatile",
        "messages":    [{"role": "user", "content": prompt}],
        "temperature": 0.0,  # صفر لمنع الهبد والتخيل تماماً
        "max_tokens":  400,
    }
    try:
        resp       = requests.post(url, headers=headers, json=payload, timeout=40)
        resp.raise_for_status()
        result_txt = resp.json()["choices"][0]["message"]["content"]
        ai_pct     = _extract_percentage(result_txt)
        
        # وزن حقيقي: 70% للتحليل اللغوي الصارم للـ AI و 30% للتوقيت الرياضي الكابح
        final      = int(ai_pct * 0.70 + timing_score * 0.30)
        
        return {
            "user1": user1, "user2": user2,
            "similarity": final,
            "ai_score":   ai_pct,
            "timing":     timing_score,
            "report":     result_txt,
        }
    except Exception as e:
        return {
            "user1": user1, "user2": user2,
            "similarity": int(timing_score * 0.30),
            "ai_score":   0,
            "timing":     timing_score,
            "report":     f"فشل الاتصال بالمحكمة الرقمية: {e}",
        }

# ════════════════════════════════════════
async def find_top_similar_pairs(
    max_users: int = 12,
    top_n:     int = 3,
) -> List[Dict[str, Any]]:
    """
    المحرك الرئيسي المحدث للقضاء على نتايج الـ 100% الوهمية
    """
    async with Client(
        "detect_session",
        api_id=API_ID, api_hash=API_HASH,
        session_string=SESSION_STRING,
        in_memory=True,
    ) as app:

        all_users = await _collect_members(app, limit=800)

        if not all_users:
            return []

        # فلترة صارمة: يجب أن يملك المستخدم على الأقل 5 رسائل حقيقية للمقارنة
        active = {
            uid: d for uid, d in all_users.items()
            if d["msg_count"] >= 5 and len(d["messages"]) >= 2
        }

        sorted_uids = sorted(active.keys(), key=lambda u: active[u]["msg_count"], reverse=True)
        sorted_uids = sorted_uids[:max_users]

        if len(sorted_uids) < 2:
            return []

        # جلب أرشيف أعمق (200 رسالة إضافية) للمتأهلين للمحاكمة لضمان دقة النص
        for uid in sorted_uids:
            if len(active[uid]["messages"]) < 25:
                extra = await _fetch_more_messages(app, uid, active[uid]["messages"], limit=200)
                active[uid]["messages"].extend(extra)
                await asyncio.sleep(0.3)

        tasks = []
        for i in range(len(sorted_uids)):
            for j in range(i + 1, len(sorted_uids)):
                u1_id = sorted_uids[i]
                u2_id = sorted_uids[j]
                d1    = active[u1_id]
                d2    = active[u2_id]

                timing = _calc_timing_overlap(d1["timestamps"], d2["timestamps"])

                u1_name = d1["username"] or d1["first_name"] or str(u1_id)
                u2_name = d2["username"] or d2["first_name"] or str(u2_id)

                tasks.append(analyze_pair_groq(d1, d2, u1_name, u2_name, timing))

        results = await asyncio.gather(*tasks)

        results_list = list(results)
        results_list.sort(key=lambda x: x["similarity"], reverse=True)

        # لا نعتبر الحساب مشبوهاً إلا إذا كسر حاجز الـ 45% تشابه حقيقي مدمج
        suspicious = [r for r in results_list if r["similarity"] >= 45]

        if not suspicious:
            for r in results_list[:top_n]:
                r["no_suspicious"] = True
            return results_list[:top_n]

        return suspicious[:top_n]
