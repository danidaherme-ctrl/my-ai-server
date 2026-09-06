from flask import Flask, request, jsonify
import os
import requests

app = Flask(__name__)

# ضع الـ Hugging Face Token في متغير البيئة
HF_TOKEN = os.environ.get("HF_TOKEN")

# نموذج الذكاء الاصطناعي
MODEL = "Qwen/Qwen2.5-7B-Instruct"

conversation = []


@app.route("/", methods=["GET"])
def home():
    return "My AI Server is running!"


@app.route("/chat", methods=["POST"])
def chat():
    global conversation

    try:
        data = request.get_json()

        if not data:
            return jsonify({
                "reply": "لم يتم استلام البيانات."
            }), 400

        message = data.get("message", "").strip()

        if not message:
            return jsonify({
                "reply": "الرسالة فارغة."
            }), 400

        # إضافة رسالة المستخدم للذاكرة
        conversation.append({
            "role": "user",
            "content": message
        })

        # آخر 10 رسائل فقط
        messages = conversation[-10:]

        headers = {
            "Authorization": f"Bearer {HF_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": MODEL,
            "messages": messages,
            "max_tokens": 500
        }

        response = requests.post(
            "https://router.huggingface.co/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=120
        )

        if response.status_code != 200:
            print("Hugging Face Error:", response.text)

            return jsonify({
                "reply": "حدث خطأ في الاتصال بخدمة الذكاء الاصطناعي."
            }), 500

        result = response.json()

        answer = result["choices"][0]["message"]["content"]

        # حفظ رد الذكاء الاصطناعي
        conversation.append({
            "role": "assistant",
            "content": answer
        })

        return jsonify({
            "reply": answer
        })

    except Exception as error:
        print("ERROR:", str(error))

        return jsonify({
            "reply": "حدث خطأ: " + str(error)
        }), 500


@app.route("/new-chat", methods=["POST"])
def new_chat():
    global conversation

    conversation = []

    return jsonify({
        "status": "new chat started"
    })


if __name__ == "__main__":
    print("Starting My AI Server...")

    app.run(
        host="0.0.0.0",
        port=5000
    )