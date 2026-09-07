from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
import os
import requests
from datetime import datetime, timedelta, timezone
from functools import wraps


# ============================================================
# App
# ============================================================

app = Flask(__name__)
CORS(app)


# ============================================================
# Database
# ============================================================

database_url = os.environ.get(
    "DATABASE_URL",
    "sqlite:///dani_ai.db"
)

if database_url.startswith("postgres://"):
    database_url = database_url.replace(
        "postgres://",
        "postgresql://",
        1
    )

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# ============================================================
# JWT Secret
# ============================================================

JWT_SECRET = os.environ.get(
    "JWT_SECRET",
    "CHANGE_THIS_SECRET_IN_RENDER"
)

JWT_ALGORITHM = "HS256"

JWT_EXPIRE_DAYS = 30


# ============================================================
# User Model
# ============================================================

class User(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    email = db.Column(
        db.String(255),
        unique=True,
        nullable=False,
        index=True
    )

    password_hash = db.Column(
        db.String(255),
        nullable=False
    )


# ============================================================
# Message Model
# ============================================================

class Message(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    user_id = db.Column(
        db.String(100),
        nullable=False,
        index=True
    )

    role = db.Column(
        db.String(20),
        nullable=False
    )

    content = db.Column(
        db.Text,
        nullable=False
    )


# ============================================================
# Create Tables
# ============================================================

with app.app_context():
    db.create_all()


# ============================================================
# AI Settings
# ============================================================

HF_TOKEN = os.environ.get("HF_TOKEN")

MODEL = "Qwen/Qwen3-8B:nscale"

HF_URL = (
    "https://router.huggingface.co/"
    "v1/chat/completions"
)


SYSTEM_PROMPT = """
أنت dani.ai، مساعد ذكاء اصطناعي تم تطويره بواسطة Daniel.

قواعدك:
- أجب باللغة التي يستخدمها المستخدم.
- إذا تحدث المستخدم بالعربية، أجب بالعربية.
- كن واضحًا ومفيدًا وودودًا.
- إذا سُئلت عن اسمك، قل إن اسمك dani.ai.
- إذا سُئلت عن مطورك، قل إنك تم تطويرك بواسطة Daniel.
"""


# ============================================================
# Create JWT
# ============================================================

def create_token(user):

    now = datetime.now(timezone.utc)

    payload = {
        "user_id": user.id,
        "email": user.email,
        "iat": now,
        "exp": now + timedelta(
            days=JWT_EXPIRE_DAYS
        )
    }

    token = jwt.encode(
        payload,
        JWT_SECRET,
        algorithm=JWT_ALGORITHM
    )

    return token


# ============================================================
# Get Current User
# ============================================================

def get_current_user():

    auth_header = request.headers.get(
        "Authorization",
        ""
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
            algorithms=[JWT_ALGORITHM]
        )

        user_id = payload.get("user_id")

        if not user_id:
            return None, "Invalid token."

        user = db.session.get(
            User,
            int(user_id)
        )

        if not user:
            return None, "User not found."

        return user, None

    except jwt.ExpiredSignatureError:

        return None, "Token expired."

    except jwt.InvalidTokenError:

        return None, "Invalid token."

    except Exception as error:

        print(
            "AUTH ERROR:",
            repr(error)
        )

        return None, "Authentication failed."


# ============================================================
# Auth Required Decorator
# ============================================================

def auth_required(function):

    @wraps(function)
    def decorated(*args, **kwargs):

        user, error = get_current_user()

        if not user:

            return jsonify({
                "error": "غير مصرح. يرجى تسجيل الدخول.",
                "details": error
            }), 401

        request.current_user = user

        return function(*args, **kwargs)

    return decorated


# ============================================================
# Home
# ============================================================

@app.route("/", methods=["GET"])
def home():

    return jsonify({
        "status": "ok",
        "message": "dani.ai Server is running!",
        "authentication": "JWT enabled"
    })


# ============================================================
# Register
# ============================================================

@app.route(
    "/register",
    methods=["POST"]
)
def register():

    try:

        data = (
            request.get_json(
                silent=True
            ) or {}
        )

        email = data.get(
            "email",
            ""
        ).strip().lower()

        password = data.get(
            "password",
            ""
        )

        if not email or not password:

            return jsonify({
                "error":
                    "البريد الإلكتروني "
                    "وكلمة المرور مطلوبان."
            }), 400

        if len(password) < 8:

            return jsonify({
                "error":
                    "كلمة المرور يجب أن تكون "
                    "8 أحرف على الأقل."
            }), 400

        existing_user = User.query.filter_by(
            email=email
        ).first()

        if existing_user:

            return jsonify({
                "error":
                    "هذا البريد الإلكتروني "
                    "مسجل بالفعل."
            }), 409

        password_hash = generate_password_hash(
            password
        )

        user = User(
            email=email,
            password_hash=password_hash
        )

        db.session.add(user)
        db.session.commit()

        token = create_token(user)

        return jsonify({
            "message":
                "تم إنشاء الحساب بنجاح.",
            "user_id":
                str(user.id),
            "email":
                user.email,
            "token":
                token
        }), 201

    except Exception as error:

        db.session.rollback()

        print(
            "REGISTER ERROR:",
            repr(error)
        )

        return jsonify({
            "error":
                "حدث خطأ أثناء إنشاء الحساب."
        }), 500


# ============================================================
# Login
# ============================================================

@app.route(
    "/login",
    methods=["POST"]
)
def login():

    try:

        data = (
            request.get_json(
                silent=True
            ) or {}
        )

        email = data.get(
            "email",
            ""
        ).strip().lower()

        password = data.get(
            "password",
            ""
        )

        if not email or not password:

            return jsonify({
                "error":
                    "البريد الإلكتروني "
                    "وكلمة المرور مطلوبان."
            }), 400

        user = User.query.filter_by(
            email=email
        ).first()

        if not user:

            return jsonify({
                "error":
                    "البريد الإلكتروني أو "
                    "كلمة المرور غير صحيحة."
            }), 401

        password_is_correct = (
            check_password_hash(
                user.password_hash,
                password
            )
        )

        if not password_is_correct:

            return jsonify({
                "error":
                    "البريد الإلكتروني أو "
                    "كلمة المرور غير صحيحة."
            }), 401

        token = create_token(user)

        return jsonify({
            "message":
                "تم تسجيل الدخول بنجاح.",
            "user_id":
                str(user.id),
            "email":
                user.email,
            "token":
                token
        }), 200

    except Exception as error:

        print(
            "LOGIN ERROR:",
            repr(error)
        )

        return jsonify({
            "error":
                "حدث خطأ أثناء تسجيل الدخول."
        }), 500


# ============================================================
# Verify Token
# ============================================================

@app.route(
    "/me",
    methods=["GET"]
)
@auth_required
def me():

    user = request.current_user

    return jsonify({
        "user_id": str(user.id),
        "email": user.email
    }), 200


# ============================================================
# Chat
# ============================================================

@app.route(
    "/chat",
    methods=["POST"]
)
@auth_required
def chat():

    try:

        if not HF_TOKEN:

            return jsonify({
                "reply":
                    "خطأ: HF_TOKEN غير موجود "
                    "في Environment Variables."
            }), 500

        user = request.current_user

        data = request.get_json(
            silent=True
        )

        if not data:

            return jsonify({
                "reply":
                    "لم يتم استلام بيانات "
                    "JSON صحيحة."
            }), 400

        message = data.get(
            "message",
            ""
        ).strip()

        if not message:

            return jsonify({
                "reply":
                    "الرسالة فارغة."
            }), 400

        # الهوية تأتي من JWT
        # وليس من user_id الموجود في Body

        user_id = str(user.id)

        # ====================================================
        # Save User Message
        # ====================================================

        user_message = Message(
            user_id=user_id,
            role="user",
            content=message
        )

        db.session.add(
            user_message
        )

        db.session.commit()

        # ====================================================
        # Conversation History
        # ====================================================

        previous_messages = (
            Message.query
            .filter_by(
                user_id=user_id
            )
            .order_by(
                Message.id.desc()
            )
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

        # ====================================================
        # Hugging Face
        # ====================================================

        headers = {
            "Authorization":
                f"Bearer {HF_TOKEN}",
            "Content-Type":
                "application/json"
        }

        payload = {
            "model": MODEL,
            "messages": messages,
            "max_tokens": 500,
            "temperature": 0.7
        }

        print(
            "Sending request to Hugging Face..."
        )

        print(
            "Model:",
            MODEL
        )

        print(
            "Authenticated User:",
            user.email
        )

        response = requests.post(
            HF_URL,
            headers=headers,
            json=payload,
            timeout=120
        )

        print(
            "Hugging Face Status:",
            response.status_code
        )

        if response.status_code != 200:

            print(
                "Hugging Face Response:",
                response.text
            )

            return jsonify({
                "reply":
                    "حدث خطأ من خدمة "
                    "الذكاء الاصطناعي.",
                "details":
                    response.text
            }), 500

        result = response.json()

        if (
            "choices" not in result
            or not result["choices"]
            or "message"
            not in result["choices"][0]
        ):

            return jsonify({
                "reply":
                    "لم يتم العثور على رد "
                    "صحيح من الذكاء الاصطناعي.",
                "details":
                    result
            }), 500

        answer = (
            result["choices"][0]
            ["message"]
            ["content"]
        )

        # ====================================================
        # Save AI Response
        # ====================================================

        assistant_message = Message(
            user_id=user_id,
            role="assistant",
            content=answer
        )

        db.session.add(
            assistant_message
        )

        db.session.commit()

        return jsonify({
            "reply": answer
        }), 200

    except requests.exceptions.Timeout:

        return jsonify({
            "reply":
                "انتهت مهلة الاتصال "
                "بخدمة الذكاء الاصطناعي."
        }), 500

    except requests.exceptions.RequestException as error:

        print(
            "REQUEST ERROR:",
            str(error)
        )

        return jsonify({
            "reply":
                "حدث خطأ في الاتصال "
                "بخدمة الذكاء الاصطناعي."
        }), 500

    except Exception as error:

        db.session.rollback()

        print(
            "SERVER ERROR:",
            repr(error)
        )

        return jsonify({
            "reply":
                "حدث خطأ في السيرفر."
        }), 500


# ============================================================
# New Chat
# ============================================================

@app.route(
    "/new-chat",
    methods=["POST"]
)
@auth_required
def new_chat():

    try:

        user = request.current_user

        user_id = str(
            user.id
        )

        Message.query.filter_by(
            user_id=user_id
        ).delete()

        db.session.commit()

        return jsonify({
            "status":
                "new chat started"
        }), 200

    except Exception as error:

        db.session.rollback()

        print(
            "NEW CHAT ERROR:",
            repr(error)
        )

        return jsonify({
            "status":
                "error"
        }), 500


# ============================================================
# Run
# ============================================================

if __name__ == "__main__":

    print(
        "Starting dani.ai Server..."
    )

    app.run(
        host="0.0.0.0",
        port=5000
    )