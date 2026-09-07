from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
import os
import requests

app = Flask(__name__)

# =========================
# Database
# =========================

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///dani_ai.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)


with app.app_context():
    db.create_all()


# =========================
# AI Settings
# =========================

HF_TOKEN = os.environ.get("HF_TOKEN")

MODEL = "Qwen/Qwen3-8B:nscale"

SYSTEM_PROMPT = """
أنت dani.ai، مساعد ذكاء اصطناعي تم تطويره بواسطة Daniel.

قواعدك:
- أجب باللغة التي يستخدمها المستخدم.
- إذا تحدث المستخدم بالعربية، أجب بالعربية.
- كن واضحًا ومفيدًا وودودًا.
- إذا سُئلت عن اسمك، قل إن اسمك dani.ai.
- إذا سُئلت عن مطورك، قل إنك تم تطويرك بواسطة Daniel.
"""


# =========================
# Home
# =========================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "ok",
        "message": "dani.ai Server is running!"
    })


# =========================
# Chat
# =========================

@app.route("/chat", methods=["POST"])
def chat():

    try:

        if not HF_TOKEN:
            return jsonify({
                "reply": "خطأ: HF_TOKEN غير موجود في Environment Variables."
            }), 500

        data = request.get_json(silent=True)

        if not data:
            return jsonify({
                "reply": "لم يتم استلام بيانات JSON صحيحة."
            }), 400

        message = data.get("message", "").strip()

        if not message:
            return jsonify({
                "reply": "الرسالة فارغة."
            }), 400

        # إذا التطبيق لم يرسل user_id
        # نعطيه مستخدمًا مؤقتًا
        user_id = str(data.get("user_id", "guest"))

        # حفظ رسالة المستخدم
        user_message = Message(
            user_id=user_id,
            role="user",
            content=message
        )

        db.session.add(user_message)
        db.session.commit()

        # جلب آخر 10 رسائل لهذا المستخدم فقط
        previous_messages = (
            Message.query
            .filter_by(user_id=user_id)
            .order_by(Message.id.desc())
            .limit(10)
            .all()
        )

        previous_messages.reverse()

        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            }
        ]

        for msg in previous_messages:
            messages.append({
                "role": msg.role,
                "content": msg.content
            })

        headers = {
            "Authorization": f"Bearer {HF_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": MODEL,
            "messages": messages,
            "max_tokens": 500,
            "temperature": 0.7
        }

        print("Sending request to Hugging Face...")
        print("Model:", MODEL)
        print("User:", user_id)

        response = requests.post(
            "https://router.huggingface.co/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=120
        )

        print("Hugging Face Status:", response.status_code)
        print("Hugging Face Response:", response.text)

        if response.status_code != 200:

            return jsonify({
                "reply": "حدث خطأ من خدمة الذكاء الاصطناعي.",
                "details": response.text
            }), 500

        result = response.json()

        if (
            "choices" not in result
            or not result["choices"]
            or "message" not in result["choices"][0]
        ):

            return jsonify({
                "reply": "لم يتم العثور على رد صحيح من الذكاء الاصطناعي.",
                "details": result
            }), 500

        answer = result["choices"][0]["message"]["content"]

        # حفظ رد AI
        assistant_message = Message(
            user_id=user_id,
            role="assistant",
            content=answer
        )

        db.session.add(assistant_message)
        db.session.commit()

        return jsonify({
            "reply": answer
        }), 200


    except requests.exceptions.Timeout:

        return jsonify({
            "reply": "انتهت مهلة الاتصال بخدمة الذكاء الاصطناعي."
        }), 500


    except requests.exceptions.RequestException as error:

        print("REQUEST ERROR:", str(error))

        return jsonify({
            "reply": "حدث خطأ في الاتصال بخدمة الذكاء الاصطناعي."
        }), 500


    except Exception as error:

        print("SERVER ERROR:", repr(error))

        return jsonify({
            "reply": "حدث خطأ في السيرفر."
        }), 500


# =========================
# New Chat
# =========================

@app.route("/new-chat", methods=["POST"])
def new_chat():

    try:

        data = request.get_json(silent=True) or {}

        user_id = str(data.get("user_id", "guest"))

        Message.query.filter_by(user_id=user_id).delete()

        db.session.commit()

        return jsonify({
            "status": "new chat started"
        })

    except Exception as error:

        print("NEW CHAT ERROR:", repr(error))

        return jsonify({
            "status": "error"
        }), 500


# =========================
# Run
# =========================

if __name__ == "__main__":

    print("Starting dani.ai Server...")

    app.run(
        host="0.0.0.0",
        port=5000
    )