from flask import Flask, request, jsonify
import os
import requests

app = Flask(__name__)

HF_TOKEN = os.environ.get("HF_TOKEN")

MODEL = "Qwen/Qwen3-8B:nscale"

conversation = []


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "ok",
        "message": "My AI Server is running!"
    })


@app.route("/chat", methods=["POST"])
def chat():
    global conversation

    try:
        # التأكد من وجود Token
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

        # إضافة رسالة المستخدم
        conversation.append({
            "role": "user",
            "content": message
        })

        # الاحتفاظ بآخر 10 رسائل
        messages = conversation[-10:]

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

        response = requests.post(
            "https://router.huggingface.co/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=120
        )

        print("Hugging Face Status:", response.status_code)
        print("Hugging Face Response:", response.text)

        # في حال حدوث خطأ من Hugging Face
        if response.status_code != 200:
            return jsonify({
                "reply": f"خطأ من Hugging Face. رمز الخطأ: {response.status_code}",
                "details": response.text
            }), 500

        result = response.json()

        # التأكد من وجود الرد
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

        # حفظ رد الذكاء الاصطناعي
        conversation.append({
            "role": "assistant",
            "content": answer
        })

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
            "reply": f"خطأ في الاتصال: {str(error)}"
        }), 500

    except Exception as error:
        print("SERVER ERROR:", repr(error))

        return jsonify({
            "reply": f"خطأ في السيرفر: {str(error)}"
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
