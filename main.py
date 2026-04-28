from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from supabase import create_client
from openai import OpenAI
import requests
import uvicorn
import os

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "my_verify_token")
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

app = FastAPI(title="Message Flow Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"message": "message flow backend working"}


@app.get("/webhook")
def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    print("VERIFY MODE:", mode)
    print("VERIFY TOKEN FROM META:", token)
    print("VERIFY TOKEN FROM ENV:", VERIFY_TOKEN)
    print("CHALLENGE:", challenge)

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge or "", media_type="text/plain")

    return Response(content="Verification failed", status_code=403)


def save_customer(instagram_id: str):
    if not supabase:
        print("SUPABASE not configured")
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
        print("SUPABASE not configured")
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


def generate_ai_reply(user_message: str) -> str:
    if not openai_client:
        return "هلا بيك 🌹 شلون أگدر أساعدك؟"

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
إذا الزبون يريد يطلب، اطلب منه:
الاسم، رقم الهاتف، العنوان، اسم المنتج، الكمية.
لا تخترع أسعار أو توفر منتجات إذا ما عندك معلومات.
                    """,
                },
                {"role": "user", "content": user_message},
            ],
        )

        return response.choices[0].message.content

    except Exception as e:
        print("AI ERROR:", e)
        return "هلا بيك 🌹 صار خطأ بسيط، اكتب رسالتك مرة ثانية."


def send_instagram_message(recipient_id: str, text: str):
    if not INSTAGRAM_ACCESS_TOKEN:
        print("INSTAGRAM_ACCESS_TOKEN missing")
        return

    try:
        url = "https://graph.facebook.com/v19.0/me/messages"

        headers = {
            "Authorization": f"Bearer {INSTAGRAM_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }

        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": text},
        }

        response = requests.post(url, headers=headers, json=payload, timeout=20)
        print("INSTAGRAM SEND STATUS:", response.status_code)
        print("INSTAGRAM SEND RESPONSE:", response.text)

    except Exception as e:
        print("INSTAGRAM SEND ERROR:", e)


@app.post("/webhook")
async def receive_webhook(request: Request):
    data = await request.json()
    print("FULL EVENT:", data)

    try:
        for entry in data.get("entry", []):
            messaging_events = entry.get("messaging", [])

            for event in messaging_events:
                sender_id = event.get("sender", {}).get("id")
                message = event.get("message", {})
                text = message.get("text", "")

                if not sender_id or not text:
                    continue

                print("MESSAGE FROM:", sender_id)
                print("TEXT:", text)

                save_customer(sender_id)
                save_message(sender_id, "user", text)

                ai_reply = generate_ai_reply(text)
                save_message(sender_id, "bot", ai_reply)

                send_instagram_message(sender_id, ai_reply)

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

    result = supabase.table("orders").select("*").order("id", desc=True).execute()
    return {"orders": result.data}


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)