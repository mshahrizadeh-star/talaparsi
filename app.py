from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
import os
import random
import string
import datetime
import requests

app = Flask(__name__)
CORS(app)

# ===== Config =====
DB_HOST     = os.environ.get("DB_HOST", "")
DB_PORT     = os.environ.get("DB_PORT", "5432")
DB_USER     = os.environ.get("DB_USER", "base-user")
DB_PASS     = os.environ.get("DB_PASS", "")
DB_NAME     = os.environ.get("DB_NAME", "default")
KAV_API     = os.environ.get("KAV_APIKEY", "")
ADMIN_PASS  = os.environ.get("ADMIN_PASS", "sekeparsi@admin")
SENDER      = "2000660110"

def get_db():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASS,
        dbname=DB_NAME
    )

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            mobile VARCHAR(15) UNIQUE NOT NULL,
            first_name VARCHAR(100),
            last_name VARCHAR(100),
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS otps (
            id SERIAL PRIMARY KEY,
            mobile VARCHAR(15) NOT NULL,
            code VARCHAR(6) NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used BOOLEAN DEFAULT FALSE
        );
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            mobile VARCHAR(15),
            items JSONB NOT NULL,
            total_price BIGINT,
            status VARCHAR(20) DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

# ===== OTP =====
@app.route("/auth/send-otp", methods=["POST"])
def send_otp():
    data = request.json
    mobile = data.get("mobile", "").strip()
    if not mobile or len(mobile) != 11:
        return jsonify({"error": "شماره موبایل معتبر نیست"}), 400

    code = "".join(random.choices(string.digits, k=6))
    expires = datetime.datetime.now() + datetime.timedelta(minutes=3)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM otps WHERE mobile=%s", (mobile,))
    cur.execute("INSERT INTO otps (mobile, code, expires_at) VALUES (%s, %s, %s)",
                (mobile, code, expires))
    conn.commit()
    cur.close()
    conn.close()

    # ارسال SMS
    url = f"https://api.kavenegar.com/v1/{KAV_API}/sms/send.json"
    requests.post(url, data={
        "sender": SENDER,
        "receptor": mobile,
        "message": f"کد تایید سکه پارسی: {code}\nاعتبار: ۳ دقیقه"
    })

    return jsonify({"message": "کد ارسال شد"}), 200


@app.route("/auth/verify-otp", methods=["POST"])
def verify_otp():
    data = request.json
    mobile     = data.get("mobile", "").strip()
    code       = data.get("code", "").strip()
    first_name = data.get("first_name", "").strip()
    last_name  = data.get("last_name", "").strip()

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id FROM otps
        WHERE mobile=%s AND code=%s AND used=FALSE AND expires_at > NOW()
    """, (mobile, code))
    row = cur.fetchone()

    if not row:
        cur.close()
        conn.close()
        return jsonify({"error": "کد اشتباه یا منقضی شده"}), 400

    cur.execute("UPDATE otps SET used=TRUE WHERE mobile=%s", (mobile,))

    # ساخت یا آپدیت کاربر
    cur.execute("SELECT id FROM users WHERE mobile=%s", (mobile,))
    user = cur.fetchone()
    if user:
        user_id = user[0]
        if first_name:
            cur.execute("UPDATE users SET first_name=%s, last_name=%s WHERE id=%s",
                        (first_name, last_name, user_id))
    else:
        cur.execute("""
            INSERT INTO users (mobile, first_name, last_name)
            VALUES (%s, %s, %s) RETURNING id
        """, (mobile, first_name, last_name))
        user_id = cur.fetchone()[0]

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"user_id": user_id, "mobile": mobile}), 200


# ===== سفارش =====
@app.route("/order/submit", methods=["POST"])
def submit_order():
    data       = request.json
    mobile     = data.get("mobile", "")
    items      = data.get("items", [])
    total      = data.get("total_price", 0)

    if not mobile or not items:
        return jsonify({"error": "اطلاعات ناقص"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE mobile=%s", (mobile,))
    user = cur.fetchone()
    user_id = user[0] if user else None

    import json
    cur.execute("""
        INSERT INTO orders (user_id, mobile, items, total_price)
        VALUES (%s, %s, %s, %s) RETURNING id
    """, (user_id, mobile, json.dumps(items, ensure_ascii=False), total))
    order_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"order_id": order_id, "message": "سفارش ثبت شد"}), 200


# ===== پنل مدیریت =====
@app.route("/admin/orders", methods=["GET"])
def admin_orders():
    password = request.headers.get("X-Admin-Pass", "")
    if password != ADMIN_PASS:
        return jsonify({"error": "دسترسی ندارید"}), 403

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT o.id, o.mobile, u.first_name, u.last_name,
               o.items, o.total_price, o.status, o.created_at
        FROM orders o
        LEFT JOIN users u ON o.user_id = u.id
        ORDER BY o.created_at DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    orders = []
    for r in rows:
        orders.append({
            "id": r[0], "mobile": r[1],
            "first_name": r[2], "last_name": r[3],
            "items": r[4], "total_price": r[5],
            "status": r[6], "created_at": str(r[7])
        })
    return jsonify(orders), 200


@app.route("/admin/order/<int:order_id>/status", methods=["PUT"])
def update_order_status(order_id):
    password = request.headers.get("X-Admin-Pass", "")
    if password != ADMIN_PASS:
        return jsonify({"error": "دسترسی ندارید"}), 403

    status = request.json.get("status", "")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE orders SET status=%s WHERE id=%s", (status, order_id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"message": "وضعیت بروز شد"}), 200


@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)
