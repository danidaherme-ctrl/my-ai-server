from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
import os
import requests
from datetime import datetime, timedelta, timezone
from functools import wraps

app = Flask(__name__)
CORS(app)

database_url = os.environ.get("DATABASE_URL", "sqlite:///dani_ai.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

JWT_SECRET = os.environ.get("JWT_SECRET", "CHANGE_THIS_SECRET_IN_RENDER")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)


# Keep the old table so the current database stays compatible.
class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)


class Conversation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False, default="New Chat")
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ConversationMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, nullable=False, index=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


with app.app_context():
    db.create_all()


HF_TOKEN = os.environ.get("HF_TOKEN")
MODEL = "Qwen/Qwen3-8B:nscale"
HF_URL = "https://router.huggingface.co/v1/chat/completions"

SYSTEM_PROMPT = """
أنت dani.ai، مساعد ذكاء اصطناعي تم تطويره بواسطة Daniel.

قواعدك:
- أجب باللغة التي يستخدمها المستخدم.
- إذا تحدث المستخدم بالعربية، أجب بالعربية.
- كن واضحًا ومفيدًا وودودًا.
- إذا سُئلت عن اسمك، قل إن اسمك dani.ai.
- إذا سُئلت عن مطورك، قل إنك تم تطويرك بواسطة Daniel.
"""


def create_token(user):
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user.id,
        "email": user.email,
        "iat": now,
        "exp": now + timedelta(days=JWT_EXPIRE_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user():
    auth_header = request.headers.get("Authorization", "")

    if not auth_header:
        return None, "Authorization header missing."
    if not auth_header.startswith("Bearer "):
        return None, "Invalid Authorization header."

    token = auth_header[7:].strip()
    if not token:
        return None, "Token missing."

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("user_id")
        if not user_id:
            return None, "Invalid token."

        user = db.session.get(User, int(user_id))
        if not user:
            return None, "User not found."

        return user, None

    except jwt.ExpiredSignatureError:
        return None, "Token expired."
    except jwt.InvalidTokenError:
        return None, "Invalid token."
    except Exception as error:
        print("AUTH ERROR:", repr(error))
        return None, "Authentication failed."


def auth_required(function):
    @wraps(function)
    def decorated(*args, **kwargs):
        user, error = get_current_user()
        if not user:
            return jsonify({
                "error": "غير مصرح. يرجى تسجيل الدخول.",
                "details": error,
            }), 401

        request.current_user = user
        return function(*args, **kwargs)

    return decorated


def create_new_conversation(user_id):
    conversation = Conversation(user_id=user_id, title="New Chat")
    db.session.add(conversation)
    db.session.commit()
    return conversation


def get_user_conversation(conversation_id, user_id):
    return Conversation.query.filter_by(
        id=conversation_id,
        user_id=user_id,
    ).first()


def get_latest_conversation(user_id):
    return (
        Conversation.query
        .filter_by(user_id=user_id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .first()
    )


def make_title(message):
    clean = " ".join(message.split()).strip()
    if not clean:
        return "New Chat"
    if len(clean) <= 45:
        return clean
    return clean[:45].rstrip() + "..."


def conversation_to_dict(conversation):
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat()
        if conversation.created_at else None,
        "updated_at": conversation.updated_at.isoformat()
        if conversation.updated_at else None,
    }


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "ok",
        "message": "dani.ai Server is running!",
        "authentication": "JWT enabled",
        "chat_history": "enabled",
    })


@app.route("/register", methods=["POST"])
def register():
    try:
        data = request.get_json(silent=True) or {}
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")

        if not email or not password:
            return jsonify({
                "error": "البريد الإلكتروني وكلمة المرور مطلوبان."
            }), 400

        if len(password) < 8:
            return jsonify({
                "error": "كلمة المرور يجب أن تكون 8 أحرف على الأقل."
            }), 400

        if User.query.filter_by(email=email).first():
            return jsonify({
                "error": "هذا البريد الإلكتروني مسجل بالفعل."
            }), 409

        user = User(
            email=email,
            password_hash=generate_password_hash(password),
        )
        db.session.add(user)
        db.session.commit()

        token = create_token(user)

        return jsonify({
            "message": "تم إنشاء الحساب بنجاح.",
            "user_id": str(user.id),
            "email": user.email,
            "token": token,
        }), 201

    except Exception as error:
        db.session.rollback()
        print("REGISTER ERROR:", repr(error))
        return jsonify({"error": "حدث خطأ أثناء إنشاء الحساب."}), 500


@app.route("/login", methods=["POST"])
def login():
    try:
        data = request.get_json(silent=True) or {}
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")

        if not email or not password:
            return jsonify({
                "error": "البريد الإلكتروني وكلمة المرور مطلوبان."
            }), 400

        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(user.password_hash, password):
            return jsonify({
                "error": "البريد الإلكتروني أو كلمة المرور غير صحيحة."
            }), 401

        token = create_token(user)

        return jsonify({
            "message": "تم تسجيل الدخول بنجاح.",
            "user_id": str(user.id),
            "email": user.email,
            "token": token,
        }), 200

    except Exception as error:
        print("LOGIN ERROR:", repr(error))
        return jsonify({"error": "حدث خطأ أثناء تسجيل الدخول."}), 500


@app.route("/me", methods=["GET"])
@auth_required
def me():
    user = request.current_user
    return jsonify({
        "user_id": str(user.id),
        "email": user.email,
    }), 200


@app.route("/conversations", methods=["GET"])
@auth_required
def list_conversations():
    user = request.current_user
    conversations = (
        Conversation.query
        .filter_by(user_id=user.id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .all()
    )

    return jsonify({
        "conversations": [
            conversation_to_dict(item)
            for item in conversations
        ]
    }), 200


@app.route("/conversations", methods=["POST"])
@auth_required
def create_conversation():
    try:
        conversation = create_new_conversation(request.current_user.id)
        return jsonify({
            "conversation": conversation_to_dict(conversation)
        }), 201
    except Exception as error:
        db.session.rollback()
        print("CREATE CONVERSATION ERROR:", repr(error))
        return jsonify({"error": "تعذر إنشاء محادثة جديدة."}), 500


@app.route("/conversations/<int:conversation_id>/messages", methods=["GET"])
@auth_required
def get_conversation_messages(conversation_id):
    user = request.current_user
    conversation = get_user_conversation(conversation_id, user.id)

    if not conversation:
        return jsonify({"error": "المحادثة غير موجودة."}), 404

    messages = (
        ConversationMessage.query
        .filter_by(conversation_id=conversation.id, user_id=user.id)
        .order_by(ConversationMessage.id.asc())
        .all()
    )

    return jsonify({
        "conversation": conversation_to_dict(conversation),
        "messages": [
            {
                "id": item.id,
                "role": item.role,
                "content": item.content,
                "created_at": item.created_at.isoformat()
                if item.created_at else None,
            }
            for item in messages
        ],
    }), 200


@app.route("/conversations/<int:conversation_id>", methods=["PATCH"])
@auth_required
def rename_conversation(conversation_id):
    try:
        user = request.current_user
        conversation = get_user_conversation(conversation_id, user.id)

        if not conversation:
            return jsonify({"error": "المحادثة غير موجودة."}), 404

        data = request.get_json(silent=True) or {}
        title = data.get("title", "").strip()

        if not title:
            return jsonify({"error": "اسم المحادثة مطلوب."}), 400

        conversation.title = title[:160]
        conversation.updated_at = datetime.now(timezone.utc)
        db.session.commit()

        return jsonify({
            "conversation": conversation_to_dict(conversation)
        }), 200

    except Exception as error:
        db.session.rollback()
        print("RENAME CONVERSATION ERROR:", repr(error))
        return jsonify({"error": "تعذر تغيير اسم المحادثة."}), 500


@app.route("/conversations/<int:conversation_id>", methods=["DELETE"])
@auth_required
def delete_conversation(conversation_id):
    try:
        user = request.current_user
        conversation = get_user_conversation(conversation_id, user.id)

        if not conversation:
            return jsonify({"error": "المحادثة غير موجودة."}), 404

        ConversationMessage.query.filter_by(
            conversation_id=conversation.id,
            user_id=user.id,
        ).delete()

        db.session.delete(conversation)
        db.session.commit()

        return jsonify({
            "status": "deleted",
            "conversation_id": conversation_id,
        }), 200

    except Exception as error:
        db.session.rollback()
        print("DELETE CONVERSATION ERROR:", repr(error))
        return jsonify({"error": "تعذر حذف المحادثة."}), 500


@app.route("/chat", methods=["POST"])
@auth_required
def chat():
    try:
        if not HF_TOKEN:
            return jsonify({
                "reply": "خطأ: HF_TOKEN غير موجود في Environment Variables."
            }), 500

        user = request.current_user
        data = request.get_json(silent=True) or {}
        message = data.get("message", "").strip()

        if not message:
            return jsonify({"reply": "الرسالة فارغة."}), 400

        conversation_id = data.get("conversation_id")
        conversation = None

        if conversation_id is not None:
            try:
                conversation_id = int(conversation_id)
            except (TypeError, ValueError):
                return jsonify({
                    "reply": "conversation_id غير صالح."
                }), 400

            conversation = get_user_conversation(conversation_id, user.id)
            if not conversation:
                return jsonify({"reply": "المحادثة غير موجودة."}), 404
        else:
            # Current Flutter remains compatible:
            # use the latest conversation or create one automatically.
            conversation = get_latest_conversation(user.id)
            if not conversation:
                conversation = create_new_conversation(user.id)

        existing_count = ConversationMessage.query.filter_by(
            conversation_id=conversation.id,
            user_id=user.id,
        ).count()

        db.session.add(ConversationMessage(
            conversation_id=conversation.id,
            user_id=user.id,
            role="user",
            content=message,
        ))

        if existing_count == 0 or conversation.title == "New Chat":
            conversation.title = make_title(message)

        conversation.updated_at = datetime.now(timezone.utc)
        db.session.commit()

        previous_messages = (
            ConversationMessage.query
            .filter_by(
                conversation_id=conversation.id,
                user_id=user.id,
            )
            .order_by(ConversationMessage.id.desc())
            .limit(10)
            .all()
        )
        previous_messages.reverse()

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for msg in previous_messages:
            messages.append({
                "role": msg.role,
                "content": msg.content,
            })

        headers = {
            "Authorization": f"Bearer {HF_TOKEN}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": MODEL,
            "messages": messages,
            "max_tokens": 500,
            "temperature": 0.7,
        }

        print("Sending request to Hugging Face...")
        print("Model:", MODEL)
        print("Authenticated User:", user.email)
        print("Conversation:", conversation.id)

        response = requests.post(
            HF_URL,
            headers=headers,
            json=payload,
            timeout=120,
        )

        print("Hugging Face Status:", response.status_code)

        if response.status_code != 200:
            print("Hugging Face Response:", response.text)
            return jsonify({
                "reply": "حدث خطأ من خدمة الذكاء الاصطناعي.",
                "details": response.text,
            }), 500

        result = response.json()

        if (
            "choices" not in result
            or not result["choices"]
            or "message" not in result["choices"][0]
        ):
            return jsonify({
                "reply": "لم يتم العثور على رد صحيح من الذكاء الاصطناعي.",
                "details": result,
            }), 500

        answer = result["choices"][0]["message"]["content"]

        db.session.add(ConversationMessage(
            conversation_id=conversation.id,
            user_id=user.id,
            role="assistant",
            content=answer,
        ))

        conversation.updated_at = datetime.now(timezone.utc)
        db.session.commit()

        return jsonify({
            "reply": answer,
            "conversation_id": conversation.id,
            "conversation_title": conversation.title,
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
        db.session.rollback()
        print("SERVER ERROR:", repr(error))
        return jsonify({"reply": "حدث خطأ في السيرفر."}), 500


@app.route("/new-chat", methods=["POST"])
@auth_required
def new_chat():
    try:
        conversation = create_new_conversation(request.current_user.id)
        return jsonify({
            "status": "new chat started",
            "conversation": conversation_to_dict(conversation),
        }), 201
    except Exception as error:
        db.session.rollback()
        print("NEW CHAT ERROR:", repr(error))
        return jsonify({"status": "error"}), 500


if __name__ == "__main__":
    print("Starting dani.ai Server...")
    app.run(host="0.0.0.0", port=5000)
