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


# ============================================================
# APP
# ============================================================

app = Flask(__name__)
CORS(app)


# ============================================================
# FIREBASE ADMIN
# ============================================================

firebase_project_id = os.environ.get(
    "FIREBASE_PROJECT_ID"
)

firebase_client_email = os.environ.get(
    "FIREBASE_CLIENT_EMAIL"
)

firebase_private_key = os.environ.get(
    "FIREBASE_PRIVATE_KEY"
)

if (
    firebase_project_id
    and firebase_client_email
    and firebase_private_key
):
    try:
        firebase_private_key = (
            firebase_private_key
            .replace("\\n", "\n")
        )

        firebase_credential = (
    credentials.Certificate({
        "type": "service_account",
        "project_id": firebase_project_id,
        "private_key": firebase_private_key,
        "client_email": firebase_client_email,
        "token_uri": "https://oauth2.googleapis.com/token",
    })
)
        firebase_admin.initialize_app(
            firebase_credential
        )

        print(
            "Firebase Admin: ENABLED",
            flush=True,
        )

    except Exception as error:
        print(
            "Firebase Admin initialization error:",
            repr(error),
            flush=True,
        )

else:
    print(
        "Firebase Admin: NOT CONFIGURED",
        flush=True,
    )

# ============================================================
# DATABASE
# ============================================================

database_url = os.environ.get(
    "DATABASE_URL",
    "sqlite:///dani_ai.db",
)

if database_url.startswith("postgres://"):
    database_url = database_url.replace(
        "postgres://",
        "postgresql://",
        1,
    )

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# ============================================================
# JWT
# ============================================================

JWT_SECRET = os.environ.get(
    "JWT_SECRET",
    "CHANGE_THIS_SECRET_IN_RENDER",
)

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30


# ============================================================
# DATABASE MODELS
# ============================================================

class User(db.Model):
    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    email = db.Column(
        db.String(255),
        unique=True,
        nullable=False,
        index=True,
    )

    password_hash = db.Column(
        db.String(255),
        nullable=False,
    )


class Message(db.Model):
    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    user_id = db.Column(
        db.String(100),
        nullable=False,
        index=True,
    )

    role = db.Column(
        db.String(20),
        nullable=False,
    )

    content = db.Column(
        db.Text,
        nullable=False,
    )


class Conversation(db.Model):
    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    user_id = db.Column(
        db.Integer,
        nullable=False,
        index=True,
    )

    title = db.Column(
        db.String(160),
        nullable=False,
        default="New Chat",
    )

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
    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    conversation_id = db.Column(
        db.Integer,
        nullable=False,
        index=True,
    )

    user_id = db.Column(
        db.Integer,
        nullable=False,
        index=True,
    )

    role = db.Column(
        db.String(20),
        nullable=False,
    )

    content = db.Column(
        db.Text,
        nullable=False,
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class Subscription(db.Model):
    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    user_id = db.Column(
        db.Integer,
        nullable=False,
        index=True,
    )

    plan = db.Column(
        db.String(20),
        nullable=False,
        default="FREE",
        index=True,
    )

    platform = db.Column(
        db.String(30),
        nullable=False,
        default="manual",
    )

    product_id = db.Column(
        db.String(160),
        nullable=True,
    )

    transaction_id = db.Column(
        db.String(255),
        nullable=True,
        unique=True,
        index=True,
    )

    status = db.Column(
        db.String(30),
        nullable=False,
        default="active",
        index=True,
    )

    started_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    expires_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    auto_renewing = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    original_transaction_id = db.Column(
        db.String(255),
        nullable=True,
    )

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
    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    user_id = db.Column(
        db.Integer,
        nullable=False,
        unique=True,
        index=True,
    )

    period_start = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    messages_used = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    tokens_used = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class WhishPayment(db.Model):
    __tablename__ = "whish_payments"

    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(100), nullable=False, unique=True, index=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    amount = db.Column(db.String(20), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="USD")
    collect_url = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(30), nullable=False, default="pending", index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


with app.app_context():
    db.create_all()


# ============================================================
# GEMINI
# ============================================================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Gemini 2.5 Flash supports Google Search grounding.
GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "gemini-3.6-flash",
)

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/"
    f"v1beta/models/{GEMINI_MODEL}:generateContent"
)


# ============================================================
# SYSTEM PROMPTS
# ============================================================

SYSTEM_PROMPT = """
أنت Gideon، مساعد ذكاء اصطناعي تم تطويره بواسطة Daniel.

قواعدك:
- أجب باللغة التي يستخدمها المستخدم.
- إذا تحدث المستخدم بالعربية، أجب بالعربية.
- كن واضحًا ومفيدًا وودودًا.
- إذا سُئلت عن اسمك، قل إن اسمك Gideon.
- إذا سُئلت عن مطورك، قل إنك تم تطويرك بواسطة Daniel.
"""


RESEARCH_SYSTEM_PROMPT = """
أنت Gideon Research.

مهمتك إجراء بحث حقيقي على الويب باستخدام Google Search
ثم تقديم إجابة دقيقة ومفيدة مبنية على المصادر التي وجدتها.

القواعد:
- استخدم المعلومات الحديثة من نتائج البحث عندما تكون متاحة.
- لا تخترع مصادر أو روابط.
- ميّز بين الحقائق المؤكدة والادعاءات.
- إذا كانت المعلومات متضاربة، اذكر وجود التعارض ووضحه.
- أجب باللغة التي يستخدمها المستخدم.
- إذا كان السؤال بالعربية، أجب بالعربية.
- لا تقل إنك بحثت في Google إذا لم يتم تنفيذ البحث فعليًا.
- لا تضع روابط وهمية داخل النص.
- اجعل الإجابة منظمة وسهلة القراءة.
- ركّز على السؤال المطلوب ولا تضف حشوًا.
"""


# ============================================================
# LIMITS / PRO
# ============================================================

FREE_DAILY_MESSAGE_LIMIT = 20
PRO_DAILY_MESSAGE_LIMIT = 200

PRO_PRODUCT_NAME = "Gideon Pro"
PRO_MONTHLY_PRICE_USD = 4.99
PRO_CURRENCY = "USD"


# ============================================================
# SUBSCRIPTIONS / USAGE
# ============================================================

def get_active_subscription(user_id):
    now = datetime.now(timezone.utc)

    subscription = (
        Subscription.query
        .filter_by(
            user_id=user_id,
            status="active",
        )
        .order_by(
            Subscription.updated_at.desc(),
            Subscription.id.desc(),
        )
        .first()
    )

    if not subscription:
        return None

    if subscription.expires_at:
        expires_at = subscription.expires_at

        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(
                tzinfo=timezone.utc,
            )

        if expires_at <= now:
            subscription.status = "expired"
            db.session.commit()
            return None

    return subscription


def get_user_plan(user_id):
    subscription = get_active_subscription(user_id)

    if (
        subscription
        and subscription.plan.upper() == "PRO"
    ):
        return "PRO", subscription

    return "FREE", subscription


def get_usage_counter(user_id):
    now = datetime.now(timezone.utc)

    usage = UsageCounter.query.filter_by(
        user_id=user_id,
    ).first()

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

    if usage.period_start.date() != now.date():
        usage.period_start = now
        usage.messages_used = 0
        usage.tokens_used = 0
        usage.updated_at = now

        db.session.commit()

    return usage


def get_message_limit(plan):
    if plan == "PRO":
        return PRO_DAILY_MESSAGE_LIMIT

    return FREE_DAILY_MESSAGE_LIMIT


def usage_to_dict(user_id):
    plan, subscription = get_user_plan(user_id)
    usage = get_usage_counter(user_id)

    limit = get_message_limit(plan)

    return {
        "plan": plan,
        "messages_used": usage.messages_used,
        "message_limit": limit,
        "messages_remaining": max(
            0,
            limit - usage.messages_used,
        ),
        "tokens_used": usage.tokens_used,
        "period_start": usage.period_start.isoformat(),
        "subscription": (
            {
                "id": subscription.id,
                "platform": subscription.platform,
                "product_id": subscription.product_id,
                "status": subscription.status,
                "started_at": (
                    subscription.started_at.isoformat()
                    if subscription.started_at
                    else None
                ),
                "expires_at": (
                    subscription.expires_at.isoformat()
                    if subscription.expires_at
                    else None
                ),
                "auto_renewing": subscription.auto_renewing,
            }
            if subscription
            else None
        ),
    }


# ============================================================
# AUTH
# ============================================================

def create_token(user):
    now = datetime.now(timezone.utc)

    payload = {
        "user_id": user.id,
        "email": user.email,
        "iat": now,
        "exp": now + timedelta(
            days=JWT_EXPIRE_DAYS,
        ),
    }

    return jwt.encode(
        payload,
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


def get_current_user():
    auth_header = request.headers.get(
        "Authorization",
        "",
    )

    if not auth_header:
        return None, "Authorization header missing."

    if not auth_header.startswith("Bearer "):
        return None, "Invalid Authorization header."

    token = auth_header[7:].strip()

    if not token:
        return None, "Token missing."

    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
        )

        user_id = payload.get("user_id")

        if not user_id:
            return None, "Invalid token."

        user = db.session.get(
            User,
            int(user_id),
        )

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


# ============================================================
# CONVERSATIONS
# ============================================================

def create_new_conversation(user_id):
    conversation = Conversation(
        user_id=user_id,
        title="New Chat",
    )

    db.session.add(conversation)
    db.session.commit()

    return conversation


def get_user_conversation(
    conversation_id,
    user_id,
):
    return Conversation.query.filter_by(
        id=conversation_id,
        user_id=user_id,
    ).first()


def get_latest_conversation(user_id):
    return (
        Conversation.query
        .filter_by(user_id=user_id)
        .order_by(
            Conversation.updated_at.desc(),
            Conversation.id.desc(),
        )
        .first()
    )


def make_title(message):
    clean = " ".join(
        message.split()
    ).strip()

    if not clean:
        return "New Chat"

    if len(clean) <= 45:
        return clean

    return clean[:45].rstrip() + "..."


def conversation_to_dict(conversation):
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": (
            conversation.created_at.isoformat()
            if conversation.created_at
            else None
        ),
        "updated_at": (
            conversation.updated_at.isoformat()
            if conversation.updated_at
            else None
        ),
    }


# ============================================================
# GEMINI HELPERS
# ============================================================

def extract_gemini_text(result):
    candidates = result.get(
        "candidates",
        [],
    )

    if not candidates:
        return ""

    content = candidates[0].get(
        "content",
        {},
    )

    parts = content.get(
        "parts",
        [],
    )

    text_parts = []

    for part in parts:
        if not isinstance(part, dict):
            continue

        text = part.get("text")

        if text:
            text_parts.append(
                str(text)
            )

    return "".join(
        text_parts
    ).strip()


def extract_grounding_sources(result):
    sources = []

    candidates = result.get(
        "candidates",
        [],
    )

    if not candidates:
        return sources

    metadata = candidates[0].get(
        "groundingMetadata",
        {},
    )

    chunks = metadata.get(
        "groundingChunks",
        [],
    )

    seen = set()

    for index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            continue

        web = chunk.get(
            "web",
            {},
        )

        if not isinstance(web, dict):
            continue

        uri = web.get("uri")
        title = web.get("title")

        if not uri:
            continue

        if uri in seen:
            continue

        seen.add(uri)

        sources.append({
            "index": len(sources) + 1,
            "title": title or uri,
            "url": uri,
        })

    return sources


def extract_search_queries(result):
    candidates = result.get(
        "candidates",
        [],
    )

    if not candidates:
        return []

    metadata = candidates[0].get(
        "groundingMetadata",
        {},
    )

    queries = metadata.get(
        "webSearchQueries",
        [],
    )

    if not isinstance(
        queries,
        list,
    ):
        return []

    return [
        str(query)
        for query in queries
        if query
    ]


def call_gemini(
    contents,
    *,
    system_instruction,
    use_google_search=False,
):
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY غير موجود في Environment Variables."
        )

    payload = {
        "systemInstruction": {
            "parts": [
                {
                    "text": system_instruction,
                }
            ],
        },
        "contents": contents,
        "generationConfig": {
            "temperature": 0.6,
            "maxOutputTokens": 1800,
        },
    }

    if use_google_search:
        payload["tools"] = [
            {
                "google_search": {},
            }
        ]

    headers = {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json",
    }

    response = requests.post(
        GEMINI_URL,
        headers=headers,
        json=payload,
        timeout=150,
    )

    print(
        "Gemini Status:",
        response.status_code,
    )

    if response.status_code != 200:
        print(
            "Gemini Response:",
            response.text,
        )

        raise RuntimeError(
            "Gemini API returned an error."
        )

    result = response.json()

    answer = extract_gemini_text(
        result
    )

    if not answer:
        raise RuntimeError(
            "Gemini returned an empty response."
        )

    return result, answer


def build_history_contents(
    conversation,
    user_id,
    current_message,
):
    previous_messages = (
        ConversationMessage.query
        .filter_by(
            conversation_id=conversation.id,
            user_id=user_id,
        )
        .order_by(
            ConversationMessage.id.desc()
        )
        .limit(10)
        .all()
    )

    previous_messages.reverse()

    contents = []

    for message in previous_messages:
        role = (
            "user"
            if message.role == "user"
            else "model"
        )

        contents.append({
            "role": role,
            "parts": [
                {
                    "text": message.content,
                }
            ],
        })

    contents.append({
        "role": "user",
        "parts": [
            {
                "text": current_message,
            }
        ],
    })

    return contents


def consume_message_usage(
    user_id,
    usage,
):
    usage.messages_used += 1
    usage.updated_at = datetime.now(
        timezone.utc
    )

    return usage


# ============================================================
# HOME
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "ok",
        "message": "Gideon Server is running!",
        "authentication": "JWT enabled",
        "chat_history": "enabled",
        "subscriptions": "enabled",
        "usage_limits": "enabled",
        "research": "google_search_enabled",
        "gemini_model": GEMINI_MODEL,
    })


# ============================================================
# REGISTER
# ============================================================

@app.route(
    "/register",
    methods=["POST"],
)
def register():
    try:
        data = request.get_json(
            silent=True
        ) or {}

        email = data.get(
            "email",
            "",
        ).strip().lower()

        password = data.get(
            "password",
            "",
        )

        if not email or not password:
            return jsonify({
                "error": "البريد الإلكتروني وكلمة المرور مطلوبان."
            }), 400

        if len(password) < 8:
            return jsonify({
                "error": "كلمة المرور يجب أن تكون 8 أحرف على الأقل."
            }), 400

        if User.query.filter_by(
            email=email
        ).first():
            return jsonify({
                "error": "هذا البريد الإلكتروني مسجل بالفعل."
            }), 409

        user = User(
            email=email,
            password_hash=generate_password_hash(
                password
            ),
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

        print(
            "REGISTER SERVER ERROR:",
            repr(error),
            flush=True,
        )

        return jsonify({
            "error": "حدث خطأ أثناء إنشاء الحساب."
        }), 500

# ============================================================
# LOGIN
# ============================================================

@app.route(
    "/login",
    methods=["POST"],
)
def login():
    try:
        data = request.get_json(
            silent=True
        ) or {}

        email = data.get(
            "email",
            "",
        ).strip().lower()

        password = data.get(
            "password",
            "",
        )

        if not email or not password:
            return jsonify({
                "error": "البريد الإلكتروني وكلمة المرور مطلوبان."
            }), 400

        user = User.query.filter_by(
            email=email
        ).first()

        if (
            not user
            or not check_password_hash(
                user.password_hash,
                password,
            )
        ):
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
        print(
            "LOGIN ERROR:",
            repr(error),
        )

        return jsonify({
            "error": "حدث خطأ أثناء تسجيل الدخول."
        }), 500

# ============================================================
# GOOGLE AUTH
# ============================================================

@app.route(
    "/auth/google",
    methods=["POST"],
)
def google_auth():
    try:
        data = request.get_json(
            silent=True
        ) or {}

        id_token = (
            data.get("id_token")
            or ""
        ).strip()

        if not id_token:
            return jsonify({
                "error": "firebase_id_token_required",
                "reply": "Firebase ID Token مطلوب.",
            }), 400

        if not firebase_admin._apps:
            return jsonify({
                "error": "firebase_not_configured",
                "reply": "Firebase Authentication غير مهيأ على السيرفر.",
            }), 500

        decoded_token = firebase_auth.verify_id_token(
            id_token
        )

        firebase_uid = decoded_token.get(
            "uid"
        )

        email = (
            decoded_token.get("email")
            or ""
        ).strip().lower()

        if not firebase_uid or not email:
            return jsonify({
                "error": "firebase_user_invalid",
                "reply": "بيانات مستخدم Firebase غير مكتملة.",
            }), 401

        user = User.query.filter_by(
            email=email
        ).first()

        if not user:
            user = User(
                email=email,
                password_hash=generate_password_hash(
                    firebase_uid
                ),
            )

            db.session.add(user)
            db.session.commit()

        token = create_token(user)

        return jsonify({
            "message": "تم تسجيل الدخول عبر Google بنجاح.",
            "user_id": str(user.id),
            "email": user.email,
            "token": token,
            "plan": get_user_plan(user.id)[0],
        }), 200

    except firebase_auth.InvalidIdTokenError:
        return jsonify({
            "error": "invalid_firebase_id_token",
            "reply": "Firebase ID Token غير صالح.",
        }), 401

    except firebase_auth.ExpiredIdTokenError:
        return jsonify({
            "error": "expired_firebase_id_token",
            "reply": "انتهت صلاحية Firebase ID Token.",
        }), 401

    except Exception as error:
        db.session.rollback()

        print(
            "GOOGLE AUTH ERROR:",
            repr(error),
            flush=True,
        )

        return jsonify({
            "error": "google_auth_server_error",
            "reply": "حدث خطأ أثناء تسجيل الدخول عبر Google.",
        }), 500

# ============================================================
# ME
# ============================================================

@app.route(
    "/me",
    methods=["GET"],
)
@auth_required
def me():
    user = request.current_user

    return jsonify({
        "user_id": str(user.id),
        "email": user.email,
    }), 200


# ============================================================
# WHISH PAY (Sandbox by default)
# ============================================================

WHISH_BASE_URL = os.environ.get(
    "WHISH_BASE_URL",
    "https://partner.api.sbx.whish.money/itel-service/api",
).rstrip("/")
WHISH_CHANNEL = os.environ.get("WHISH_CHANNEL", "")
WHISH_SECRET = os.environ.get("WHISH_SECRET", "")
WHISH_WEBSITE_URL = os.environ.get("WHISH_WEBSITE_URL", "")
WHISH_USER_AGENT = os.environ.get(
    "WHISH_USER_AGENT",
    "GedionAI/1.0 (https://gideonassistant.uk; support@gideonassistant.uk)",
)
BACKEND_PUBLIC_URL = os.environ.get("BACKEND_PUBLIC_URL", "").rstrip("/")
WHISH_TIMEOUT_SECONDS = 20


def whish_headers():
    return {
        "channel": WHISH_CHANNEL,
        "secret": WHISH_SECRET,
        "websiteUrl": WHISH_WEBSITE_URL,
        "User-Agent": WHISH_USER_AGENT,
        "Content-Type": "application/json",
    }


def whish_configured():
    return all([WHISH_CHANNEL, WHISH_SECRET, WHISH_WEBSITE_URL, BACKEND_PUBLIC_URL])


def verify_whish_payment(payment):
    """Query Whish's server-side status; callbacks are never treated as proof."""
    response = requests.post(
        f"{WHISH_BASE_URL}/payment/collect/status",
        headers=whish_headers(),
        json={"currency": payment.currency, "externalId": payment.external_id},
        timeout=WHISH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") is not True:
        code = payload.get("code") or "whish_status_error"
        raise RuntimeError(f"Whish status request failed: {code}")
    data = payload.get("data") or {}
    return data.get("collectStatus", "unknown")


def activate_whish_subscription(payment):
    """Idempotently activate one month of Pro for a verified successful payment."""
    existing = Subscription.query.filter_by(transaction_id=payment.external_id).first()
    if existing:
        payment.status = "success"
        db.session.commit()
        return

    now = datetime.now(timezone.utc)
    current = get_active_subscription(payment.user_id)
    start_at = now
    if current and current.plan.upper() == "PRO" and current.expires_at:
        expiry = current.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        start_at = max(now, expiry)

    subscription = Subscription(
        user_id=payment.user_id,
        plan="PRO",
        platform="whish_pay",
        product_id="gideon_pro_monthly",
        transaction_id=payment.external_id,
        status="active",
        started_at=start_at,
        expires_at=start_at + timedelta(days=30),
        auto_renewing=0,
    )
    payment.status = "success"
    db.session.add(subscription)
    db.session.commit()


def reconcile_whish_payment(payment):
    status = verify_whish_payment(payment)
    if status == "success":
        activate_whish_subscription(payment)
    elif status == "failed":
        payment.status = "failed"
        db.session.commit()
    elif status in ("pending", "refunded", "unknown"):
        payment.status = status
        db.session.commit()
    else:
        payment.status = "unknown"
        db.session.commit()
    return payment.status


@app.route("/payments/whish/create", methods=["POST"])
@auth_required
def create_whish_payment():
    if not whish_configured():
        return jsonify({"error": "Whish Pay is not configured on the server."}), 503

    try:
        user = request.current_user
        data = request.get_json(silent=True) or {}
        # Price is fixed server-side; clients cannot choose a cheaper amount.
        amount = str(PRO_MONTHLY_PRICE_USD)
        currency = "USD"
        external_id = "gedion-" + __import__("uuid").uuid4().hex
        payment = WhishPayment(
            external_id=external_id,
            user_id=user.id,
            amount=amount,
            currency=currency,
            status="creating",
        )
        db.session.add(payment)
        db.session.commit()

        callback_base = f"{BACKEND_PUBLIC_URL}/payments/whish/callback"
        redirect_base = f"{BACKEND_PUBLIC_URL}/payments/whish/return"
        body = {
            "amount": amount,
            "currency": currency,
            "invoice": "Gideon Pro - 1 month",
            "externalId": external_id,
            "successCallbackUrl": f"{callback_base}/success?externalId={external_id}&currency={currency}",
            "failureCallbackUrl": f"{callback_base}/failure?externalId={external_id}&currency={currency}",
            "successRedirectUrl": f"{redirect_base}?result=success&externalId={external_id}",
            "failureRedirectUrl": f"{redirect_base}?result=failure&externalId={external_id}",
        }
        response = requests.post(
            f"{WHISH_BASE_URL}/payment/whish",
            headers=whish_headers(),
            json=body,
            timeout=WHISH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") is not True:
            payment.status = "error"
            db.session.commit()
            return jsonify({"error": "Whish could not create the payment.", "code": payload.get("code")}), 502

        collect_url = (payload.get("data") or {}).get("collectUrl")
        if not collect_url:
            payment.status = "error"
            db.session.commit()
            return jsonify({"error": "Whish response did not contain collectUrl."}), 502

        payment.collect_url = collect_url
        payment.status = "pending"
        db.session.commit()
        return jsonify({
            "externalId": external_id,
            "collectUrl": collect_url,
            "amount": amount,
            "currency": currency,
            "status": "pending",
        }), 201
    except requests.RequestException as error:
        db.session.rollback()
        app.logger.error("Whish create request failed: %s", type(error).__name__)
        return jsonify({"error": "Could not contact Whish Pay. Please retry."}), 502
    except Exception as error:
        db.session.rollback()
        app.logger.exception("Whish payment creation failed")
        return jsonify({"error": "Could not create Whish payment."}), 500


@app.route("/payments/whish/status/<external_id>", methods=["GET"])
@auth_required
def whish_payment_status(external_id):
    payment = WhishPayment.query.filter_by(external_id=external_id, user_id=request.current_user.id).first()
    if not payment:
        return jsonify({"error": "Payment not found."}), 404
    try:
        status = reconcile_whish_payment(payment)
        return jsonify({"externalId": payment.external_id, "status": status, "plan": get_user_plan(request.current_user.id)[0]}), 200
    except requests.RequestException as error:
        app.logger.error("Whish status request failed: %s", type(error).__name__)
        return jsonify({"error": "Could not verify payment status yet. Please retry."}), 502
    except Exception:
        db.session.rollback()
        app.logger.exception("Whish status reconciliation failed")
        return jsonify({"error": "Could not verify payment status yet."}), 502


@app.route("/payments/whish/callback/<attempt_result>", methods=["GET"])
def whish_payment_callback(attempt_result):
    # Whish callbacks are unauthenticated; they are only a trigger to verify via API.
    external_id = request.args.get("externalId", "")
    payment = WhishPayment.query.filter_by(external_id=external_id).first()
    if not payment:
        return "OK", 200
    try:
        reconcile_whish_payment(payment)
    except Exception as error:
        db.session.rollback()
        app.logger.warning("Whish callback reconciliation deferred (%s)", type(error).__name__)
    # Failure callback does not cancel the order; status may remain pending.
    return "OK", 200


@app.route("/payments/whish/return", methods=["GET"])
def whish_payment_return():
    # Browser redirects are not proof of payment. Client should call status endpoint.
    return jsonify({
        "message": "Return received. Check payment status in the app.",
        "externalId": request.args.get("externalId"),
        "result": request.args.get("result", "unknown"),
    }), 200


# ============================================================
# PRO PRODUCT
# ============================================================

@app.route(
    "/products/pro",
    methods=["GET"],
)
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
            "whish_pay": "available_in_sandbox" if whish_configured() else "configuration_required",
        },
    }), 200


# ============================================================
# ACCOUNT PLAN
# ============================================================

@app.route(
    "/account/plan",
    methods=["GET"],
)
@auth_required
def account_plan():
    try:
        user = request.current_user

        plan, subscription = get_user_plan(
            user.id
        )

        return jsonify({
            "plan": plan,
            "subscription": (
                {
                    "id": subscription.id,
                    "platform": subscription.platform,
                    "product_id": subscription.product_id,
                    "status": subscription.status,
                    "started_at": (
                        subscription.started_at.isoformat()
                        if subscription.started_at
                        else None
                    ),
                    "expires_at": (
                        subscription.expires_at.isoformat()
                        if subscription.expires_at
                        else None
                    ),
                    "auto_renewing": subscription.auto_renewing,
                }
                if subscription
                else None
            ),
        }), 200

    except Exception as error:
        print(
            "ACCOUNT PLAN ERROR:",
            repr(error),
        )

        return jsonify({
            "error": "تعذر تحميل خطة الحساب."
        }), 500


# ============================================================
# USAGE
# ============================================================

@app.route(
    "/usage",
    methods=["GET"],
)
@auth_required
def usage():
    try:
        return jsonify(
            usage_to_dict(
                request.current_user.id
            )
        ), 200

    except Exception as error:
        db.session.rollback()

        print(
            "USAGE SERVER ERROR:",
            repr(error),
            flush=True,
        )

        return jsonify({
            "error": "تعذر تحميل بيانات الاستخدام."
        }), 500


# ============================================================
# CONVERSATIONS
# ============================================================

@app.route(
    "/conversations",
    methods=["GET"],
)
@auth_required
def list_conversations():
    user = request.current_user

    conversations = (
        Conversation.query
        .filter_by(
            user_id=user.id
        )
        .order_by(
            Conversation.updated_at.desc(),
            Conversation.id.desc(),
        )
        .all()
    )

    return jsonify({
        "conversations": [
            conversation_to_dict(item)
            for item in conversations
        ]
    }), 200


@app.route(
    "/conversations",
    methods=["POST"],
)
@auth_required
def create_conversation():
    try:
        conversation = create_new_conversation(
            request.current_user.id
        )

        return jsonify({
            "conversation": conversation_to_dict(
                conversation
            )
        }), 201

    except Exception as error:
        db.session.rollback()

        print(
            "CREATE CONVERSATION ERROR:",
            repr(error),
        )

        return jsonify({
            "error": "تعذر إنشاء محادثة جديدة."
        }), 500


@app.route(
    "/conversations/<int:conversation_id>/messages",
    methods=["GET"],
)
@auth_required
def get_conversation_messages(
    conversation_id,
):
    user = request.current_user

    conversation = get_user_conversation(
        conversation_id,
        user.id,
    )

    if not conversation:
        return jsonify({
            "error": "المحادثة غير موجودة."
        }), 404

    messages = (
        ConversationMessage.query
        .filter_by(
            conversation_id=conversation.id,
            user_id=user.id,
        )
        .order_by(
            ConversationMessage.id.asc()
        )
        .all()
    )

    return jsonify({
        "conversation": conversation_to_dict(
            conversation
        ),
        "messages": [
            {
                "id": item.id,
                "role": item.role,
                "content": item.content,
                "created_at": (
                    item.created_at.isoformat()
                    if item.created_at
                    else None
                ),
            }
            for item in messages
        ],
    }), 200


@app.route(
    "/conversations/<int:conversation_id>",
    methods=["PATCH"],
)
@auth_required
def rename_conversation(
    conversation_id,
):
    try:
        user = request.current_user

        conversation = get_user_conversation(
            conversation_id,
            user.id,
        )

        if not conversation:
            return jsonify({
                "error": "المحادثة غير موجودة."
            }), 404

        data = request.get_json(
            silent=True
        ) or {}

        title = data.get(
            "title",
            "",
        ).strip()

        if not title:
            return jsonify({
                "error": "اسم المحادثة مطلوب."
            }), 400

        conversation.title = title[:160]
        conversation.updated_at = datetime.now(
            timezone.utc
        )

        db.session.commit()

        return jsonify({
            "conversation": conversation_to_dict(
                conversation
            )
        }), 200

    except Exception as error:
        db.session.rollback()

        print(
            "RENAME CONVERSATION ERROR:",
            repr(error),
        )

        return jsonify({
            "error": "تعذر تغيير اسم المحادثة."
        }), 500


@app.route(
    "/conversations/<int:conversation_id>",
    methods=["DELETE"],
)
@auth_required
def delete_conversation(
    conversation_id,
):
    try:
        user = request.current_user

        conversation = get_user_conversation(
            conversation_id,
            user.id,
        )

        if not conversation:
            return jsonify({
                "error": "المحادثة غير موجودة."
            }), 404

        ConversationMessage.query.filter_by(
            conversation_id=conversation.id,
            user_id=user.id,
        ).delete()

        db.session.delete(
            conversation
        )

        db.session.commit()

        return jsonify({
            "status": "deleted",
            "conversation_id": conversation_id,
        }), 200

    except Exception as error:
        db.session.rollback()

        print(
            "DELETE CONVERSATION ERROR:",
            repr(error),
        )

        return jsonify({
            "error": "تعذر حذف المحادثة."
        }), 500


# ============================================================
# CHAT
# ============================================================

@app.route(
    "/chat",
    methods=["POST"],
)
@auth_required
def chat():
    try:
        user = request.current_user

        if not GEMINI_API_KEY:
            return jsonify({
                "reply": "خطأ: GEMINI_API_KEY غير موجود في Environment Variables."
            }), 500

        data = request.get_json(
            silent=True
        ) or {}

        message = data.get(
            "message",
            "",
        ).strip()

        if not message:
            return jsonify({
                "reply": "الرسالة فارغة."
            }), 400

        plan, _ = get_user_plan(
            user.id
        )

        usage = get_usage_counter(
            user.id
        )

        message_limit = get_message_limit(
            plan
        )

        if usage.messages_used >= message_limit:
            return jsonify({
                "reply": "وصلت إلى حد الرسائل اليومي. يمكنك الترقية إلى Gideon Pro لمزيد من الاستخدام.",
                "error": "daily_message_limit_reached",
                "plan": plan,
                "messages_used": usage.messages_used,
                "message_limit": message_limit,
                "messages_remaining": 0,
            }), 429

        conversation_id = data.get(
            "conversation_id"
        )

        conversation = None

        if conversation_id is not None:
            try:
                conversation_id = int(
                    conversation_id
                )
            except (
                TypeError,
                ValueError,
            ):
                return jsonify({
                    "reply": "conversation_id غير صالح."
                }), 400

            conversation = get_user_conversation(
                conversation_id,
                user.id,
            )

            if not conversation:
                return jsonify({
                    "reply": "المحادثة غير موجودة."
                }), 404

        else:
            conversation = get_latest_conversation(
                user.id
            )

            if not conversation:
                conversation = create_new_conversation(
                    user.id
                )

        existing_count = (
            ConversationMessage.query
            .filter_by(
                conversation_id=conversation.id,
                user_id=user.id,
            )
            .count()
        )

        if (
            existing_count == 0
            or conversation.title == "New Chat"
        ):
            conversation.title = make_title(
                message
            )

        db.session.add(
            ConversationMessage(
                conversation_id=conversation.id,
                user_id=user.id,
                role="user",
                content=message,
            )
        )

        conversation.updated_at = datetime.now(
            timezone.utc
        )

        db.session.commit()

        contents = build_history_contents(
            conversation,
            user.id,
            message,
        )

        _, answer = call_gemini(
            contents,
            system_instruction=SYSTEM_PROMPT,
            use_google_search=False,
        )

        db.session.add(
            ConversationMessage(
                conversation_id=conversation.id,
                user_id=user.id,
                role="assistant",
                content=answer,
            )
        )

        consume_message_usage(
            user.id,
            usage,
        )

        conversation.updated_at = datetime.now(
            timezone.utc
        )

        db.session.commit()

        return jsonify({
            "reply": answer,
            "conversation_id": conversation.id,
            "conversation_title": conversation.title,
            "plan": plan,
            "usage": {
                "messages_used": usage.messages_used,
                "message_limit": message_limit,
                "messages_remaining": max(
                    0,
                    message_limit
                    - usage.messages_used,
                ),
            },
        }), 200

    except requests.exceptions.Timeout:
        db.session.rollback()

        return jsonify({
            "reply": "انتهت مهلة الاتصال بخدمة الذكاء الاصطناعي."
        }), 500

    except requests.exceptions.RequestException as error:
        db.session.rollback()

        print(
            "GEMINI REQUEST ERROR:",
            str(error),
        )

        return jsonify({
            "reply": "حدث خطأ في الاتصال بخدمة الذكاء الاصطناعي."
        }), 500

    except Exception as error:
        db.session.rollback()

        print(
            "CHAT SERVER ERROR:",
            repr(error),
        )

        return jsonify({
            "reply": "حدث خطأ في السيرفر."
        }), 500


# ============================================================
# RESEARCH
# ============================================================

@app.route(
    "/research",
    methods=["POST"],
)
@app.route(
    "/researc",
    methods=["POST"],
)
@auth_required
def research():
    try:
        user = request.current_user

        if not GEMINI_API_KEY:
            return jsonify({
                "error": "GEMINI_API_KEY غير موجود في Environment Variables.",
                "reply": "خدمة Research غير مهيأة بعد.",
            }), 500

        data = request.get_json(
            silent=True
        ) or {}

        query = (
            data.get("query")
            or data.get("message")
            or ""
        ).strip()

        if not query:
            return jsonify({
                "error": "research_query_required",
                "reply": "اكتب موضوع البحث أولًا.",
            }), 400

        if len(query) > 4000:
            return jsonify({
                "error": "research_query_too_long",
                "reply": "موضوع البحث طويل جدًا.",
            }), 413

        plan, _ = get_user_plan(
            user.id
        )

        usage = get_usage_counter(
            user.id
        )

        message_limit = get_message_limit(
            plan
        )

        if usage.messages_used >= message_limit:
            return jsonify({
                "reply": "وصلت إلى حد الاستخدام اليومي.",
                "error": "daily_message_limit_reached",
                "plan": plan,
                "messages_used": usage.messages_used,
                "message_limit": message_limit,
                "messages_remaining": 0,
            }), 429

        conversation_id = data.get(
            "conversation_id"
        )

        conversation = None

        if conversation_id is not None:
            try:
                conversation_id = int(
                    conversation_id
                )
            except (
                TypeError,
                ValueError,
            ):
                return jsonify({
                    "error": "conversation_id_invalid",
                    "reply": "conversation_id غير صالح."
                }), 400

            conversation = get_user_conversation(
                conversation_id,
                user.id,
            )

            if not conversation:
                return jsonify({
                    "error": "conversation_not_found",
                    "reply": "المحادثة غير موجودة."
                }), 404

        else:
            conversation = get_latest_conversation(
                user.id
            )

            if not conversation:
                conversation = create_new_conversation(
                    user.id
                )

        existing_count = (
            ConversationMessage.query
            .filter_by(
                conversation_id=conversation.id,
                user_id=user.id,
            )
            .count()
        )

        research_message = (
            "🔎 Research: "
            + query
        )

        if (
            existing_count == 0
            or conversation.title == "New Chat"
        ):
            conversation.title = make_title(
                query
            )

        db.session.add(
            ConversationMessage(
                conversation_id=conversation.id,
                user_id=user.id,
                role="user",
                content=research_message,
            )
        )

        conversation.updated_at = datetime.now(
            timezone.utc
        )

        db.session.commit()

        # Research intentionally starts a fresh research
        # request. Google Search decides which web queries
        # are necessary and returns grounding metadata.
        contents = [
            {
                "role": "user",
                "parts": [
                    {
                        "text": query,
                    }
                ],
            }
        ]

        result, answer = call_gemini(
            contents,
            system_instruction=RESEARCH_SYSTEM_PROMPT,
            use_google_search=True,
        )

        sources = extract_grounding_sources(
            result
        )

        search_queries = extract_search_queries(
            result
        )

        db.session.add(
            ConversationMessage(
                conversation_id=conversation.id,
                user_id=user.id,
                role="assistant",
                content=answer,
            )
        )

        consume_message_usage(
            user.id,
            usage,
        )

        conversation.updated_at = datetime.now(
            timezone.utc
        )

        db.session.commit()

        return jsonify({
            "reply": answer,
            "research": True,
            "query": query,
            "sources": sources,
            "search_queries": search_queries,
            "conversation_id": conversation.id,
            "conversation_title": conversation.title,
            "plan": plan,
            "usage": {
                "messages_used": usage.messages_used,
                "message_limit": message_limit,
                "messages_remaining": max(
                    0,
                    message_limit
                    - usage.messages_used,
                ),
            },
        }), 200

    except requests.exceptions.Timeout:
        db.session.rollback()

        return jsonify({
            "error": "research_timeout",
            "reply": "انتهت مهلة البحث. حاول مرة أخرى.",
        }), 500

    except requests.exceptions.RequestException as error:
        db.session.rollback()

        print(
            "RESEARCH REQUEST ERROR:",
            str(error),
        )

        return jsonify({
            "error": "research_request_error",
            "reply": "حدث خطأ أثناء الاتصال بخدمة البحث.",
        }), 500

    except Exception as error:
        db.session.rollback()

        print(
            "RESEARCH SERVER ERROR:",
            repr(error),
        )

        return jsonify({
            "error": "research_server_error",
            "reply": "حدث خطأ أثناء تنفيذ البحث.",
        }), 500


# ============================================================
# NEW CHAT
# ============================================================

@app.route(
    "/new-chat",
    methods=["POST"],
)
@auth_required
def new_chat():
    try:
        conversation = create_new_conversation(
            request.current_user.id
        )

        return jsonify({
            "status": "new chat started",
            "conversation": conversation_to_dict(
                conversation
            ),
        }), 201

    except Exception as error:
        db.session.rollback()

        print(
            "NEW CHAT ERROR:",
            repr(error),
        )

        return jsonify({
            "status": "error"
        }), 500


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    print(
        "Starting Gideon Server..."
    )

    print(
        "Gemini model:",
        GEMINI_MODEL,
    )

    print(
        "Google Search Research: ENABLED"
    )

    app.run(
        host="0.0.0.0",
        port=5000,
    )

