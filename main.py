from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from supabase import create_client
from openai import OpenAI
from passlib.context import CryptContext
from jose import jwt
import requests
import uvicorn
import os
import json
import time
import re

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "my_verify_token")
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
JWT_SECRET = os.getenv("JWT_SECRET", "message_flow_secret_123")

STORE_ID = "store_1"

BOT_SETTINGS = {
    "welcome_message": "هلا بيك، شلون أگدر أساعدك؟",
    "marketing_mode": True,
    "order_collection": True,
    "telegram_enabled": False,
}

supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI(title="Message Flow Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def create_token(data: dict):
    payload = data.copy()
    payload["exp"] = int(time.time()) + 60 * 60 * 24 * 7
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def make_store_id(email: str):
    clean = re.sub(r"[^a-zA-Z0-9]", "_", email.lower())
    return f"store_{clean}"


@app.get("/")
def root():
    return {"message": "message flow backend working"}


@app.get("/settings")
def get_settings():
    return BOT_SETTINGS


@app.post("/settings")
async def update_settings(request: Request):
    data = await request.json()

    BOT_SETTINGS["welcome_message"] = data.get("welcome_message", BOT_SETTINGS["welcome_message"])
    BOT_SETTINGS["marketing_mode"] = data.get("marketing_mode", BOT_SETTINGS["marketing_mode"])
    BOT_SETTINGS["order_collection"] = data.get("order_collection", BOT_SETTINGS["order_collection"])
    BOT_SETTINGS["telegram_enabled"] = data.get("telegram_enabled", BOT_SETTINGS["telegram_enabled"])

    return {"success": True, "settings": BOT_SETTINGS}


@app.post("/auth/register")
async def register(request: Request):
    if not supabase:
        return {"success": False, "error": "Supabase not configured"}

    data = await request.json()
    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return {"success": False, "error": "Email and password are required"}

    try:
        existing = supabase.table("app_users").select("*").eq("email", email).execute()

        if existing.data:
            return {"success": False, "error": "هذا البريد مسجل مسبقاً"}

        store_id = make_store_id(email)
        password = password[:72]
        password_hash = pwd_context.hash(password)

        supabase.table("app_users").insert(
            {
                "email": email,
                "password_hash": password_hash,
                "store_id": store_id,
            }
        ).execute()

        token = create_token({"email": email, "store_id": store_id})

        return {"success": True, "token": token, "store_id": store_id}

    except Exception as e:
        print("REGISTER ERROR:", e)
        return {"success": False, "error": str(e)}


@app.post("/auth/login")
async def login(request: Request):
    if not supabase:
        return {"success": False, "error": "Supabase not configured"}

    data = await request.json()
    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return {"success": False, "error": "Email and password are required"}

    try:
        result = supabase.table("app_users").select("*").eq("email", email).limit(1).execute()

        if not result.data:
            return {"success": False, "error": "بيانات الدخول غير صحيحة"}

        user = result.data[0]
        password = password[:72]

        if not pwd_context.verify(password, user["password_hash"]):
            return {"success": False, "error": "بيانات الدخول غير صحيحة"}

        token = create_token({"email": email, "store_id": user["store_id"]})

        return {"success": True, "token": token, "store_id": user["store_id"]}

    except Exception as e:
        print("LOGIN ERROR:", e)
        return {"success": False, "error": str(e)}


@app.get("/webhook")
def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge or "", media_type="text/plain")

    return Response(content="Verification failed", status_code=403)


def send_instagram_message(recipient_id: str, text: str):
    if not INSTAGRAM_ACCESS_TOKEN:
        print("INSTAGRAM_ACCESS_TOKEN missing")
        return

    url = "https://graph.instagram.com/v21.0/me/messages"

    headers = {
        "Authorization": f"Bearer {INSTAGRAM_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }

    response = requests.post(url, headers=headers, json=payload, timeout=20)

    print("INSTAGRAM SEND TO:", recipient_id)
    print("INSTAGRAM SEND STATUS:", response.status_code)
    print("INSTAGRAM SEND RESPONSE:", response.text)


def save_customer(instagram_id: str):
    if not supabase:
        return

    try:
        supabase.table("customers").upsert(
            {"instagram_id": instagram_id},
            on_conflict="instagram_id",
        ).execute()
    except Exception as e:
        print("SAVE CUSTOMER ERROR:", e)


def save_message(instagram_id: str, role: str, text: str):
    if not supabase:
        return

    try:
        supabase.table("messages").insert(
            {
                "instagram_id": instagram_id,
                "role": role,
                "message_text": text,
            }
        ).execute()
    except Exception as e:
        print("SAVE MESSAGE ERROR:", e)


def get_pending_order(instagram_id: str):
    if not supabase:
        return None

    try:
        result = (
            supabase.table("pending_orders")
            .select("*")
            .eq("instagram_id", instagram_id)
            .eq("store_id", STORE_ID)
            .limit(1)
            .execute()
        )

        if result.data:
            return result.data[0]

    except Exception as e:
        print("GET PENDING ORDER ERROR:", e)

    return None


def save_pending_order(instagram_id: str, order_data: dict):
    if not supabase:
        return

    payload = {
        "store_id": STORE_ID,
        "instagram_id": instagram_id,
        "customer_name": order_data.get("customer_name"),
        "phone": order_data.get("phone"),
        "address": order_data.get("address"),
        "product_name": order_data.get("product_name"),
        "quantity": order_data.get("quantity"),
        "status": "waiting_confirmation",
    }

    try:
        supabase.table("pending_orders").upsert(
            payload,
            on_conflict="instagram_id",
        ).execute()
    except Exception as e:
        print("SAVE PENDING ORDER ERROR:", e)


def confirm_pending_order(instagram_id: str):
    if not supabase:
        return False

    pending = get_pending_order(instagram_id)

    if not pending:
        return False

    order_payload = {
        "store_id": STORE_ID,
        "instagram_id": instagram_id,
        "customer_name": pending.get("customer_name"),
        "phone": pending.get("phone"),
        "address": pending.get("address"),
        "product_name": pending.get("product_name"),
        "quantity": pending.get("quantity"),
        "status": "confirmed",
    }

    try:
        supabase.table("orders").insert(order_payload).execute()

        supabase.table("pending_orders").delete().eq(
            "instagram_id", instagram_id
        ).eq("store_id", STORE_ID).execute()

        return True

    except Exception as e:
        print("CONFIRM ORDER ERROR:", e)
        return False


def cancel_pending_order(instagram_id: str):
    if not supabase:
        return

    try:
        supabase.table("pending_orders").delete().eq(
            "instagram_id", instagram_id
        ).eq("store_id", STORE_ID).execute()
    except Exception as e:
        print("CANCEL PENDING ORDER ERROR:", e)


def is_yes(text: str) -> bool:
    text = text.strip().lower()
    yes_words = ["نعم", "اي", "إي", "اي نعم", "صح", "صحيح", "صحيحه", "تمام", "اوك", "ok", "yes", "y"]

    for word in yes_words:
        if word.lower() in text:
            return True

    return False


def is_no(text: str) -> bool:
    text = text.strip().lower()
    no_words = ["لا", "كلا", "مو", "غلط", "خطأ", "تعديل", "no", "n"]

    for word in no_words:
        if word.lower() in text:
            return True

    return False


def is_marketing_yes(text: str) -> bool:
    text = text.strip().lower()
    yes_words = ["اي", "إي", "نعم", "اي نعم", "تمام", "اوكي", "اوك", "ok", "مهتم", "اشرح", "شلون", "كمل", "اريد", "شنو", "تفاصيل", "yes"]

    for word in yes_words:
        if word in text:
            return True

    return False


def is_marketing_no(text: str) -> bool:
    text = text.strip().lower()
    no_words = ["لا", "كلا", "مو مهتم", "ما اريد", "ما احتاج", "بعدين", "لاحقا", "مو هسه", "no", "not interested"]

    for word in no_words:
        if word in text:
            return True

    return False


def marketing_intro_reply() -> str:
    return """
هلا 👋

أنا بوت مبيعات مخصص لمتاجر الإنستغرام.

أساعد المتجر على:
* الرد على رسائل الزبائن تلقائيًا
* جمع الطلبات: الاسم، الرقم، العنوان، المنتج والكمية
* تثبيت الطلب بعد تأكيد الزبون
* ترتيب الطلبات داخل لوحة تحكم سهلة

💰 الاشتراك الشهري يبدأ من 15$ فقط.

إذا تحب أشرح لك شلون يشتغل النظام بالتفصيل، اكتب: نعم.
""".strip()


def marketing_details_reply() -> str:
    return """
أكيد، أوضح لك أكثر 👇

Message Flow هو نظام يساعد متاجر الإنستغرام على إدارة الرسائل والطلبات بدون فوضى.

المميزات:
* يرد على الزبائن تلقائيًا 24/7
* يسأل الزبون عن معلومات الطلب
* يجمع الاسم، الرقم، العنوان، المنتج والكمية
* يعرض الطلب على الزبون للتأكيد
* يحفظ الطلب داخل لوحة تحكم مرتبة
* يقلل ضغط الردود على الموظفين
* يقلل ضياع الطلبات داخل الـ DM
* مناسب للملابس، العطور، الإكسسوارات، المنتجات المنزلية وغيرها

الفكرة مو استبدال الموظف، الفكرة تنظيم الشغل وتسريع الطلبات.

السعر يبدأ من 15$ شهريًا، وأقدر أسوي تجربة بسيطة حتى تشوفه يشتغل عمليًا.
""".strip()


def marketing_rejection_reply() -> str:
    return """
تمام، ماكو مشكلة أبدًا 🌹

بس حتى أوضح الفكرة: النظام مو ضروري لكل متجر، لكنه يفيد المتاجر اللي عدها ضغط رسائل أو طلبات تضيع بالخاص.

إذا يومًا احتجتوا:
* رد أسرع على الزبائن
* ترتيب الطلبات
* تقليل ضغط الرسائل
* لوحة تحكم للطلبات

أقدر أجهز لكم تجربة بسيطة بدون التزام.
""".strip()


def extract_order_data(user_message: str) -> dict:
    if not openai_client:
        return {
            "is_order": False,
            "customer_name": None,
            "phone": None,
            "address": None,
            "product_name": None,
            "quantity": None,
        }

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """
استخرج معلومات الطلب من رسالة زبون إنستغرام.
أرجع JSON فقط بدون شرح.

الحقول:
{
  "is_order": true/false,
  "customer_name": string/null,
  "phone": string/null,
  "address": string/null,
  "product_name": string/null,
  "quantity": string/null
}

إذا الرسالة لا تحتوي طلب، خلي is_order=false.
إذا معلومة ناقصة خليها null.
                    """,
                },
                {"role": "user", "content": user_message},
            ],
        )

        content = response.choices[0].message.content.strip()
        content = content.replace("json", "").replace("", "").strip()
        return json.loads(content)

    except Exception as e:
        print("ORDER EXTRACTION ERROR:", e)
        return {
            "is_order": False,
            "customer_name": None,
            "phone": None,
            "address": None,
            "product_name": None,
            "quantity": None,
        }


def missing_fields(order_data: dict):
    fields = {
        "customer_name": "الاسم",
        "phone": "رقم الهاتف",
        "address": "العنوان",
        "product_name": "اسم المنتج",
        "quantity": "الكمية",
    }

    missing = []

    for key, label in fields.items():
        if not order_data.get(key):
            missing.append(label)

    return missing


def build_confirmation_message(order_data: dict) -> str:
    return f"""
شكرًا لك! هاي معلومات طلبك للتأكيد:

* الاسم: {order_data.get("customer_name")}
* رقم الهاتف: {order_data.get("phone")}
* العنوان: {order_data.get("address")}
* اسم المنتج: {order_data.get("product_name")}
* الكمية: {order_data.get("quantity")}

هل كلشي صحيح؟
""".strip()


def generate_ai_reply(user_message: str) -> str:
    if not openai_client:
        return BOT_SETTINGS["welcome_message"]

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """
أنت بوت مبيعات لمتجر إنستغرام.
رد باللهجة العراقية بشكل قصير ومهذب.
إذا الزبون يسأل سؤال عام، جاوبه ببساطة.
إذا يريد يطلب، اطلب منه:
الاسم، رقم الهاتف، العنوان، اسم المنتج، الكمية.
لا تخترع أسعار.
                    """,
                },
                {"role": "user", "content": user_message},
            ],
        )

        return response.choices[0].message.content

    except Exception as e:
        print("AI ERROR:", e)
        return "هلا بيك 🌹 صار خطأ بسيط، اكتب رسالتك مرة ثانية."


def handle_message(sender_id: str, text: str) -> str:
    if BOT_SETTINGS["marketing_mode"]:
        if is_marketing_yes(text):
            return marketing_details_reply()

        if is_marketing_no(text):
            return marketing_rejection_reply()

        return marketing_intro_reply()

    if not BOT_SETTINGS["order_collection"]:
        return BOT_SETTINGS["welcome_message"]

    pending = get_pending_order(sender_id)

    if pending:
        if is_yes(text):
            confirmed = confirm_pending_order(sender_id)

            if confirmed:
                return "تم تأكيد طلبك ✅ راح يتواصل وياك فريقنا قريبًا."

            return "صار خطأ أثناء تأكيد الطلب، أرسل التفاصيل مرة ثانية."

        if is_no(text):
            cancel_pending_order(sender_id)
            return "تمام، أرسل التصحيح أو تفاصيل الطلب من جديد."

        return "عندي طلب بانتظار التأكيد. إذا المعلومات صحيحة اكتب: نعم، وإذا تحتاج تعديل اكتب: لا."

    order_data = extract_order_data(text)

    if order_data.get("is_order"):
        missing = missing_fields(order_data)

        if missing:
            missing_text = "، ".join(missing)
            return f"تمام، حتى أكمل الطلب أحتاج منك: {missing_text}"

        save_pending_order(sender_id, order_data)
        return build_confirmation_message(order_data)

    return generate_ai_reply(text)


@app.post("/webhook")
async def receive_webhook(request: Request):
    data = await request.json()
    print("FULL EVENT:", data)

    try:
        for entry in data.get("entry", []):
            bot_instagram_id = entry.get("id")
            messaging_events = entry.get("messaging", [])

            for event in messaging_events:
                sender_id = event.get("sender", {}).get("id")
                recipient_id = event.get("recipient", {}).get("id")
                message = event.get("message", {})
                text = message.get("text", "")

                print("SENDER ID:", sender_id)
                print("RECIPIENT ID:", recipient_id)
                print("BOT ID:", bot_instagram_id)

                if not text:
                    print("IGNORED EVENT WITHOUT TEXT")
                    continue

                if sender_id == bot_instagram_id:
                    print("IGNORED BOT SELF MESSAGE")
                    continue

                print("TEXT:", text)

                save_customer(sender_id)
                save_message(sender_id, "user", text)

                reply = handle_message(sender_id, text)

                save_message(sender_id, "bot", reply)
                send_instagram_message(sender_id, reply)

    except Exception as e:
        print("WEBHOOK ERROR:", e)

    return {"status": "ok"}


@app.get("/messages")
def get_messages():
    if not supabase:
        return {"messages": []}

    result = supabase.table("messages").select("*").order("id", desc=True).execute()
    return {"messages": result.data}


@app.get("/customers")
def get_customers():
    if not supabase:
        return {"customers": []}

    result = supabase.table("customers").select("*").order("id", desc=True).execute()
    return {"customers": result.data}


@app.get("/orders")
def get_orders():
    if not supabase:
        return {"orders": []}

    result = (
        supabase.table("orders")
        .select("*")
        .eq("store_id", STORE_ID)
        .order("id", desc=True)
        .execute()
    )

    return {"orders": result.data}


@app.post("/update-order")
async def update_order(request: Request):
    if not supabase:
        return {"success": False, "error": "Supabase not configured"}

    data = await request.json()
    order_id = data.get("id")
    status = data.get("status")

    if not order_id or not status:
        return {"success": False, "error": "Missing id or status"}

    try:
        supabase.table("orders").update(
            {"status": status}
        ).eq("id", order_id).eq("store_id", STORE_ID).execute()

        return {"success": True}

    except Exception as e:
        print("UPDATE ORDER ERROR:", e)
        return {"success": False, "error": str(e)}


if __name__ == "_main_":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)