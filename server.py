from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
import os
import requests
from datetime import datetime, timedelta, timezone
from functools import wraps
import firebase_admin
from firebase_admin import credentials, auth as firebase_auth

app = Flask(__name__)
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "https://gideonassistant.uk,https://www.gideonassistant.uk"
)
CORS(
    app,
    resources={r"/*": {"origins": [origin.strip() for origin in ALLOWED_ORIGINS.split(",") if origin.strip()]}},
)

database_url = os.environ.get("DATABASE_URL", "sqlite:///dani_ai.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET environment variable is required.")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30

FIREBASE_CREDENTIALS_PATH = "/etc/secrets/firebase-service-account.json"

if not firebase_admin._apps:
    firebase_credential = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
    firebase_admin.initialize_app(firebase_credential)


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


# Subscription foundation.
# Payment verification will be connected later to Google Play, Apple, and Whish.
class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    plan = db.Column(db.String(20), nullable=False, default="FREE", index=True)
    platform = db.Column(db.String(30), nullable=False, default="manual")
    product_id = db.Column(db.String(160), nullable=True)
    transaction_id = db.Column(db.String(255), nullable=True, unique=True, index=True)
    status = db.Column(db.String(30), nullable=False, default="active", index=True)
    started_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    auto_renewing = db.Column(db.Boolean, nullable=False, default=False)
    original_transaction_id = db.Column(db.String(255), nullable=True)
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


class UsageCounter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, unique=True, index=True)
    period_start = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    messages_used = db.Column(db.Integer, nullable=False, default=0)
    tokens_used = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


with app.app_context():
    db.create_all()


HF_TOKEN = os.environ.get("HF_TOKEN")
MODEL = "Qwen/Qwen3-8B:nscale"
HF_URL = "https://router.huggingface.co/v1/chat/completions"

# Production request limits.
MAX_MESSAGE_LENGTH = 8000
MAX_CONVERSATION_TITLE_LENGTH = 160
MAX_JSON_BODY_BYTES = 64 * 1024
app.config["MAX_CONTENT_LENGTH"] = MAX_JSON_BODY_BYTES

SYSTEM_PROMPT = """
أنت Gideon، مساعد ذكاء اصطناعي تم تطويره بواسطة Daniel.

قواعدك:
- أجب باللغة التي يستخدمها المستخدم.
- إذا تحدث المستخدم بالعربية، أجب بالعربية.
- كن واضحًا ومفيدًا وودودًا.
- إذا سُئلت عن اسمك، قل إن اسمك Gideon.
- إذا سُئلت عن مطورك، قل إنك تم تطويرك بواسطة Daniel.
"""


FREE_DAILY_MESSAGE_LIMIT = 20
PRO_DAILY_MESSAGE_LIMIT = 200

# Gideon Pro launch pricing.
# This is product metadata only; payment verification is NOT enabled yet.
PRO_PRODUCT_NAME = "Gideon Pro"
PRO_MONTHLY_PRICE_USD = 4.99
PRO_CURRENCY = "USD"


def get_active_subscription(user_id):
    now = datetime.now(timezone.utc)

    subscription = (
        Subscription.query
        .filter_by(user_id=user_id, status="active")
        .order_by(Subscription.updated_at.desc(), Subscription.id.desc())
        .first()
    )

    if not subscription:
        return None

    # SQLite may return DateTime values without timezone information.
    # Normalize them to UTC before comparing with an aware datetime.
    if subscription.expires_at:
        expires_at = subscription.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if expires_at <= now:
            subscription.status = "expired"
            db.session.commit()
            return None

    return subscription


def get_user_plan(user_id):
    subscription = get_active_subscription(user_id)
    if subscription and subscription.plan.upper() == "PRO":
        return "PRO", subscription
    return "FREE", subscription


def get_usage_counter(user_id):
    now = datetime.now(timezone.utc)
    usage = UsageCounter.query.filter_by(user_id=user_id).first()

    if not usage:
        usage = UsageCounter(
            user_id=user_id,
            period_start=now,
            messages_used=0,
            tokens_used=0,
        )
        db.session.add(usage)
        db.session.commit()
        return usage

    # Daily UTC reset for the first production version.
    if usage.period_start.date() != now.date():
        usage.period_start = now
        usage.messages_used = 0
        usage.tokens_used = 0
        usage.updated_at = now
        db.session.commit()

    return usage


def get_message_limit(plan):
    return PRO_DAILY_MESSAGE_LIMIT if plan == "PRO" else FREE_DAILY_MESSAGE_LIMIT


def usage_to_dict(user_id):
    plan, subscription = get_user_plan(user_id)
    usage = get_usage_counter(user_id)
    limit = get_message_limit(plan)

    return {
        "plan": plan,
        "messages_used": usage.messages_used,
        "message_limit": limit,
        "messages_remaining": max(0, limit - usage.messages_used),
        "tokens_used": usage.tokens_used,
        "period_start": usage.period_start.isoformat(),
        "subscription": {
            "id": subscription.id,
            "platform": subscription.platform,
            "product_id": subscription.product_id,
            "status": subscription.status,
            "started_at": subscription.started_at.isoformat()
            if subscription.started_at else None,
            "expires_at": subscription.expires_at.isoformat()
            if subscription.expires_at else None,
            "auto_renewing": subscription.auto_renewing,
        } if subscription else None,
    }


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


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "ok",
        "message": "Gideon Server is running!",
        "authentication": "JWT enabled",
        "chat_history": "enabled",
        "subscriptions": "enabled",
        "usage_limits": "enabled",
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

        if len(email) > 255:
            return jsonify({"error": "البريد الإلكتروني طويل جدًا."}), 400

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
            "plan": "FREE",
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
            "plan": get_user_plan(user.id)[0],
        }), 200

    except Exception as error:
        print("LOGIN ERROR:", repr(error))
        return jsonify({"error": "حدث خطأ أثناء تسجيل الدخول."}), 500


@app.route("/auth/google", methods=["POST"])
def google_auth():
    try:
        data = request.get_json(silent=True) or {}
        id_token = data.get("id_token", "").strip()

        if not id_token:
            return jsonify({"error": "Firebase ID token مطلوب."}), 400

        decoded_token = firebase_auth.verify_id_token(id_token)
        firebase_uid = decoded_token.get("uid")
        email = (decoded_token.get("email") or "").strip().lower()

        if not firebase_uid or not email:
            return jsonify({"error": "بيانات حساب Google غير مكتملة."}), 401

        user = User.query.filter_by(email=email).first()

        if not user:
            random_password = os.urandom(32).hex()
            user = User(email=email, password_hash=generate_password_hash(random_password))
            db.session.add(user)
            db.session.commit()

        token = create_token(user)

        return jsonify({
            "message": "تم تسجيل الدخول باستخدام Google بنجاح.",
            "user_id": str(user.id),
            "email": user.email,
            "token": token,
            "plan": get_user_plan(user.id)[0],
            "provider": "google",
        }), 200

    except firebase_auth.InvalidIdTokenError:
        return jsonify({"error": "Firebase ID token غير صالح."}), 401
    except firebase_auth.ExpiredIdTokenError:
        return jsonify({"error": "Firebase ID token منتهي الصلاحية."}), 401
    except Exception as error:
        db.session.rollback()
        print("GOOGLE AUTH ERROR:", repr(error))
        return jsonify({"error": "تعذر تسجيل الدخول باستخدام Google."}), 401


@app.route("/me", methods=["GET"])
@auth_required
def me():
    user = request.current_user
    return jsonify({
        "user_id": str(user.id),
        "email": user.email,
    }), 200


@app.route("/products/pro", methods=["GET"])
def pro_product():
    return jsonify({
        "id": "gideon_pro_monthly",
        "name": PRO_PRODUCT_NAME,
        "price": PRO_MONTHLY_PRICE_USD,
        "currency": PRO_CURRENCY,
        "billing_period": "month",
        "status": "coming_soon",
        "payment_channels": {
            "google_play": "coming_soon",
            "apple_app_store": "coming_soon",
            "whish_pay": "pending_merchant_integration",
        },
    }), 200


@app.route("/account/plan", methods=["GET"])
@auth_required
def account_plan():
    try:
        user = request.current_user
        plan, subscription = get_user_plan(user.id)

        return jsonify({
            "plan": plan,
            "subscription": {
                "id": subscription.id,
                "platform": subscription.platform,
                "product_id": subscription.product_id,
                "status": subscription.status,
                "started_at": subscription.started_at.isoformat()
                if subscription.started_at else None,
                "expires_at": subscription.expires_at.isoformat()
                if subscription.expires_at else None,
                "auto_renewing": subscription.auto_renewing,
            } if subscription else None,
        }), 200
    except Exception as error:
        print("ACCOUNT PLAN ERROR:", repr(error))
        return jsonify({"error": "تعذر تحميل خطة الحساب."}), 500


@app.route("/usage", methods=["GET"])
@auth_required
def usage():
    try:
        return jsonify(usage_to_dict(request.current_user.id)), 200
    except Exception as error:
        db.session.rollback()
        print("USAGE ERROR:", repr(error))
        return jsonify({"error": "تعذر تحميل الاستخدام."}), 500


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

        if len(title) > MAX_CONVERSATION_TITLE_LENGTH:
            title = title[:MAX_CONVERSATION_TITLE_LENGTH]

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

        if len(message) > MAX_MESSAGE_LENGTH:
            return jsonify({
                "reply": f"الرسالة طويلة جدًا. الحد الأقصى {MAX_MESSAGE_LENGTH} حرف."
            }), 413

        # Enforce the current FREE/PRO daily message limit.
        plan, _ = get_user_plan(user.id)
        usage = get_usage_counter(user.id)
        message_limit = get_message_limit(plan)

        if usage.messages_used >= message_limit:
            return jsonify({
                "reply": "وصلت إلى حد الرسائل اليومي. يمكنك الترقية إلى Gideon Pro لمزيد من الاستخدام.",
                "error": "daily_message_limit_reached",
                "plan": plan,
                "messages_used": usage.messages_used,
                "message_limit": message_limit,
                "messages_remaining": 0,
            }), 429

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
        print("Authenticated User ID:", user.id)
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
                "error": "ai_provider_error",
            }), 502

        result = response.json()

        if (
            "choices" not in result
            or not result["choices"]
            or "message" not in result["choices"][0]
        ):
            return jsonify({
                "reply": "لم يتم العثور على رد صحيح من الذكاء الاصطناعي.",
                "error": "invalid_ai_response",
            }), 502

        answer = result["choices"][0]["message"]["content"]

        db.session.add(ConversationMessage(
            conversation_id=conversation.id,
            user_id=user.id,
            role="assistant",
            content=answer,
        ))

        usage.messages_used += 1
        conversation.updated_at = datetime.now(timezone.utc)
        db.session.commit()

        return jsonify({
            "reply": answer,
            "conversation_id": conversation.id,
            "conversation_title": conversation.title,
            "plan": plan,
            "usage": {
                "messages_used": usage.messages_used,
                "message_limit": message_limit,
                "messages_remaining": max(0, message_limit - usage.messages_used),
            },
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
    print("Starting Gideon Server...")
    app.run(host="0.0.0.0", port=5000)
