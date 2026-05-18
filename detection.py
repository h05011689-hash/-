import asyncio
import re
import requests
import logging
import math
from typing import List, Dict, Any
from collections import Counter
from pyrogram import Client

# ════════════════════════════════════════
# الإعدادات الفنية (النسخة النفاثة المصححة)
API_ID         = 26604893
API_HASH       = "b4dad6237531036f1a4bb2580e4985b1"
SESSION_STRING = "BAGV9V0Af_3r8brUqcEEKfZ0pS6m2mi7vBHXvW-WAeAAd2HCL5xluUtUStq0VslHxtbpgfVKIXRKi9CrWRJWudKeOLA1fHXnwt5c2_hYQiAT2OW4IMrGzWCMrKRrTL2E8yA1AAygPnT7J3jejpylQi0HRavgx-CzlDcBPFB-G6-zgnTi5TKzyuFo9LxpOjV0hjna8nIXHGPX4cgC2QxuD2Dmy8_htVb-uxPIiu5MIcD15ErSyT4mP-A6r3nZb0XAlRaJ9K3CM9a01icSCv19BpFl0QbVtdPvY8zBdRba8aFAAuRBGNYI4akLKKRvHAHXXLMa3dNdLBWOsGBu7UTMn6KCNJgavAAAAAHloT2vAA"
TARGET_CHANNEL = "clanarba"
GROQ_API_KEY   = "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"

logging.basicConfig(level=logging.INFO)

# نمط آمن لعد الإيموجيات في بايثون دون ضرب الـ Unicode Range
EMOJI_REGEX = re.compile(r'[\u2600-\u27BF]|[\uE000-\uF8FF]|\uD83C[\uDF00-\uDFFF]|\uD83D[\uDC00-\uDDFF]|\uD83E[\uDD00-\uDFFF]')

# ════════════════════════════════════════
def _extract_percentage(text: str) -> int:
    match = re.search(r'(?:نسبة التشابه|percentage|similarity):\s*(\d+)', text, re.IGNORECASE)
    if not match: match = re.search(r'(\d+)\s*%', text)
    return min(100, int(match.group(1))) if match else 0

def _clean_text_from_trends(text_list: list, global_trends: set) -> str:
    cleaned_messages = []
    for msg in text_list:
        words = msg.split()
        filtered_words = [w for w in words if w.lower() not in global_trends and not w.startswith('@')]
        if filtered_words:
            cleaned_messages.append(" ".join(filtered_words))
    return " | ".join(cleaned_messages)

def _calculate_syntax_fingerprint(msgs1: list, msgs2: list) -> float:
    if not msgs1 or not msgs2: return 0.0
    
    def get_features(msgs):
        full_text = "".join(msgs)
        total_len = len(full_text)
        if total_len == 0: return 0, 0, 0
        
        avg_len = total_len / len(msgs)
        emoji_count = len(EMOJI_REGEX.findall(full_text)) / max(1, total_len)
        q_count = len(re.findall(r'[؟\?]', full_text)) / max(1, total_len)
        return avg_len, emoji_count, q_count

    f1 = get_features(msgs1)
    f2 = get_features(msgs2)
    
    distance = math.sqrt(sum((f1[i] - f2[i])**2 for i in range(3)))
    similarity = 1 / (1 + distance)
    return similarity * 100

def _calc_timing_overlap(times1: list, times2: list) -> int:
    if not times1 or not times2: return 0
    buckets1 = Counter(int(t // 60) for t in times1)
    buckets2 = Counter(int(t // 60) for t in times2)
    common_minutes = set(buckets1.keys()) & set(buckets2.keys())
    total_overlaps = sum(min(buckets1[m], buckets2[m]) for m in common_minutes)
    total_msg_count = min(len(times1), len(times2))
    if total_msg_count == 0: return 0
    score = int((total_overlaps / total_msg_count) * 100)
    return min(100, score if total_overlaps >= 3 else min(score, 15))

# ════════════════════════════════════════
async def _collect_members_and_trends(app: Client, limit: int = 10000):
    """سحب الـ 10 آلاف رسالة بأقصى سرعة مدعومة من تلجرام"""
    users_data: Dict[int, dict] = {}
    all_words = []
    
    logging.info(f"بدء سحب {limit} رسالة من {TARGET_CHANNEL}...")
    
    try:
        async for msg in app.get_chat_history(TARGET_CHANNEL, limit=limit):
            u = msg.from_user
            if not u or u.is_bot: continue
            uid = u.id
            if uid not in users_data:
                users_data[uid] = {"username": u.username or "", "first_name": u.first_name or str(uid), "messages": [], "timestamps": [], "msg_count": 0}
            
            if msg.text and len(msg.text.strip()) > 2:
                txt = msg.text.strip()
                users_data[uid]["messages"].append(txt)
                all_words.extend(txt.lower().split())
            if msg.date:
                users_data[uid]["timestamps"].append(msg.date.timestamp())
            users_data[uid]["msg_count"] += 1
    except Exception as e:
        logging.error(f"فشل السحب السريع: {e}")

    word_counts = Counter(all_words)
    global_trends = set([word for word, count in word_counts.items() if count > (limit * 0.015)]) 
    
    logging.info(f"اكتمل السحب. تم العثور على {len(users_data)} مستخدم.")
    return users_data, global_trends

# ════════════════════════════════════════
async def analyze_pair_groq(data1: dict, data2: dict, user1: str, user2: str, timing_score: int, syntax_score: float, global_trends: set) -> Dict[str, Any]:
    clean_text1 = _clean_text_from_trends(data1["messages"], global_trends)[:1500]
    clean_text2 = _clean_text_from_trends(data2["messages"], global_trends)[:1500]

    if len(clean_text1) < 25 or len(clean_text2) < 25:
        return {"user1": user1, "user2": user2, "similarity": 0, "ai_score": 0, "timing": timing_score, "report": "غير مشبوه - النصوص غير كافية."}

    prompt = f"""أنت رئيس الاستخبارات الجنائية الرقمية. حلل الأسلوب الباقي الصافي بعد حذف الكلمات المستهلكة بالكلان.

[المشتبه به الأول: {user1}]
{clean_text1}

[المشتبه به الثاني: {user2}]
{clean_text2}

[التحليل: {syntax_score:.1f}% تشابه هيكلي، {timing_score}% تزامن زمني]

أجب بالتنسيق الصارم التالي:
نسبة التشابه: [رقم]%
المؤشرات: [أدلة البصمة العميقة فقط]
الحكم: [مشبوه جداً / غير مشبوه]"""

    url     = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 150
    }
    try:
        loop = asyncio.get_event_loop()
        # تقليص الـ timeout لـ 15 ثانية لمنع تعليق الكود نهائياً
        resp = await loop.run_in_executor(None, lambda: requests.post(url, headers=headers, json=payload, timeout=15))
        result_txt = resp.json()["choices"][0]["message"]["content"]
        ai_pct = _extract_percentage(result_txt)
        
        if ai_pct < 45:
            final = 0
        else:
            final = int(ai_pct * 0.60 + syntax_score * 0.20 + timing_score * 0.20)
            
        return {"user1": user1, "user2": user2, "similarity": final, "ai_score": ai_pct, "timing": timing_score, "report": result_txt}
    except Exception as e:
        return {"user1": user1, "user2": user2, "similarity": 0, "ai_score": 0, "timing": timing_score, "report": f"تخطى بسبب المهلة: {e}"}

# ════════════════════════════════════════
async def find_top_similar_pairs(max_users: int = 12, top_n: int = 3) -> List[Dict[str, Any]]:
    async with Client("detect_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True) as app:

        all_users, global_trends = await _collect_members_and_trends(app, limit=10000)
        if not all_users: return []

        # التركيز على الحسابات المتفاعلة جداً لضمان عدم تضييع الوقت في حسابات كتبت كلمة أو كلمتين
        active = {uid: d for uid, d in all_users.items() if d["msg_count"] >= 15 and len(d["messages"]) >= 5}
        sorted_uids = sorted(active.keys(), key=lambda u: active[u]["msg_count"], reverse=True)[:max_users]

        if len(sorted_uids) < 2: return []

        pre_pairs = []
        for i in range(len(sorted_uids)):
            for j in range(i + 1, len(sorted_uids)):
                u1_id, u2_id = sorted_uids[i], sorted_uids[j]
                d1, d2 = active[u1_id], active[u2_id]

                timing = _calc_timing_overlap(d1["timestamps"], d2["timestamps"])
                syntax = _calculate_syntax_fingerprint(d1["messages"], d2["messages"])
                
                # فلتر التصفية الطائرة الصارم جداً لرفع السرعة الصاروخية
                if timing < 8 and syntax < 45:
                    continue

                u1_name = d1["username"] or d1["first_name"] or str(u1_id)
                u2_name = d2["username"] or d2["first_name"] or str(u2_id)

                pre_pairs.append((d1, d2, u1_name, u2_name, timing, syntax))

        if not pre_pairs: return []

        pre_pairs.sort(key=lambda x: (x[4] + x[5]), reverse=True)
        final_targets = pre_pairs[:8] # فحص أعمق لأخطر 8 أزواج فقط لمنع البطء والـ Rate limit

        tasks = [
            analyze_pair_groq(p[0], p[1], p[2], p[3], p[4], p[5], global_trends)
            for p in final_targets
        ]
        
        results = await asyncio.gather(*tasks)
        results_list = list(results)
        results_list.sort(key=lambda x: x["similarity"], reverse=True)

        suspicious = [r for r in results_list if r["similarity"] >= 45]
        if not suspicious:
            for r in results_list[:top_n]: r["no_suspicious"] = True
            return results_list[:top_n]

        return suspicious[:top_n]
