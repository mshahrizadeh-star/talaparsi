from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
import os
import random
import string
import datetime
import requests
import threading
import uuid

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False, allow_headers=["Content-Type", "X-Admin-Pass", "X-Token"])

# ===== Config =====
DB_HOST     = os.environ.get("DB_HOST", "")
DB_PORT     = os.environ.get("DB_PORT", "5432")
DB_USER     = os.environ.get("DB_USER", "base-user")
DB_PASS     = os.environ.get("DB_PASS", "")
DB_NAME     = os.environ.get("DB_NAME", "default")
SMS_IR_API  = os.environ.get("SMS_IR_APIKEY", "6Sy90FuUf4SwRwE7srdmzFwLqgghJt0rsPw19kXtqfhn09Mb")
ADMIN_PASS  = os.environ.get("ADMIN_PASS", "sekeparsi@admin")

# ===== ZarinPal Config =====
ZARINPAL_MERCHANT_ID = os.environ.get("ZARINPAL_MERCHANT_ID", "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
ZARINPAL_SANDBOX = os.environ.get("ZARINPAL_SANDBOX", "true").lower() == "true"
SITE_URL = os.environ.get("SITE_URL", "https://www.sekeparsi.ir")

if ZARINPAL_SANDBOX:
    ZP_REQUEST_URL = "https://sandbox.zarinpal.com/pg/v4/payment/request.json"
    ZP_VERIFY_URL  = "https://sandbox.zarinpal.com/pg/v4/payment/verify.json"
    ZP_GATEWAY_URL = "https://sandbox.zarinpal.com/pg/StartPay/"
else:
    ZP_REQUEST_URL = "https://payment.zarinpal.com/pg/v4/payment/request.json"
    ZP_VERIFY_URL  = "https://payment.zarinpal.com/pg/v4/payment/verify.json"
    ZP_GATEWAY_URL = "https://payment.zarinpal.com/pg/StartPay/"

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
            token VARCHAR(64),
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
            first_name VARCHAR(100),
            last_name VARCHAR(100),
            province VARCHAR(100),
            city VARCHAR(100),
            postal_code VARCHAR(10),
            address TEXT,
            items JSONB NOT NULL,
            total_price BIGINT,
            shipping_cost BIGINT DEFAULT 150000,
            final_price BIGINT,
            status VARCHAR(20) DEFAULT 'pending',
            payment_ref VARCHAR(100),
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("""
        ALTER TABLE users ADD COLUMN IF NOT EXISTS token VARCHAR(64);
    """)
    conn.commit()
    cur.close()
    conn.close()

def get_user_by_token(token):
    if not token:
        return None
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, mobile, first_name, last_name FROM users WHERE token=%s", (token,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row

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

    def send_sms():
        try:
            requests.post(
                "https://api.sms.ir/v1/send/verify",
                headers={
                    "X-API-KEY": SMS_IR_API,
                    "Content-Type": "application/json"
                },
                json={
                    "mobile": mobile,
                    "templateId": 156930,
                    "parameters": [
                        {"name": "CODE", "value": code}
                    ]
                },
                timeout=10
            )
        except Exception as e:
            print(f"SMS error: {e}")

    threading.Thread(target=send_sms, daemon=True).start()
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

    token = str(uuid.uuid4()).replace("-", "")

    cur.execute("SELECT id, first_name, last_name FROM users WHERE mobile=%s", (mobile,))
    user = cur.fetchone()
    if user:
        user_id = user[0]
        fn = first_name or user[1] or ""
        ln = last_name  or user[2] or ""
        cur.execute("UPDATE users SET first_name=%s, last_name=%s, token=%s WHERE id=%s",
                    (fn, ln, token, user_id))
    else:
        cur.execute("""
            INSERT INTO users (mobile, first_name, last_name, token)
            VALUES (%s, %s, %s, %s) RETURNING id
        """, (mobile, first_name, last_name, token))
        user_id = cur.fetchone()[0]
        fn = first_name
        ln = last_name

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "user_id": user_id,
        "mobile": mobile,
        "first_name": fn,
        "last_name": ln,
        "token": token
    }), 200


# ===== User Profile =====
@app.route("/user/profile", methods=["GET"])
def get_profile():
    token = request.headers.get("X-Token", "")
    user = get_user_by_token(token)
    if not user:
        return jsonify({"error": "احراز هویت نشده"}), 401
    return jsonify({
        "user_id": user[0],
        "mobile": user[1],
        "first_name": user[2] or "",
        "last_name": user[3] or ""
    }), 200


@app.route("/user/profile", methods=["PUT"])
def update_profile():
    token = request.headers.get("X-Token", "")
    user = get_user_by_token(token)
    if not user:
        return jsonify({"error": "احراز هویت نشده"}), 401

    data = request.json
    first_name = data.get("first_name", "").strip()
    last_name  = data.get("last_name", "").strip()

    if not first_name or not last_name:
        return jsonify({"error": "نام و نام خانوادگی الزامی است"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET first_name=%s, last_name=%s WHERE id=%s",
                (first_name, last_name, user[0]))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"message": "مشخصات بروز شد"}), 200


# ===== User Orders =====
@app.route("/user/orders", methods=["GET"])
def get_user_orders():
    token = request.headers.get("X-Token", "")
    user = get_user_by_token(token)
    if not user:
        return jsonify({"error": "احراز هویت نشده"}), 401

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, items, total_price, shipping_cost, final_price,
               status, created_at, province, city
        FROM orders
        WHERE user_id=%s
        ORDER BY created_at DESC
    """, (user[0],))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    orders = []
    for r in rows:
        orders.append({
            "id": r[0],
            "items": r[1],
            "total_price": r[2],
            "shipping_cost": r[3],
            "final_price": r[4],
            "status": r[5],
            "created_at": str(r[6]),
            "province": r[7],
            "city": r[8]
        })
    return jsonify(orders), 200


# ===== سفارش =====
@app.route("/order/submit", methods=["POST"])
def submit_order():
    data         = request.json
    mobile       = data.get("mobile", "")
    items        = data.get("items", [])
    total        = data.get("total_price", 0)
    first_name   = data.get("first_name", "")
    last_name    = data.get("last_name", "")
    province     = data.get("province", "")
    city         = data.get("city", "")
    postal_code  = data.get("postal_code", "")
    address      = data.get("address", "")
    shipping     = 150000
    final        = total + shipping

    if not mobile or not items:
        return jsonify({"error": "اطلاعات ناقص"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE mobile=%s", (mobile,))
    user = cur.fetchone()
    user_id = user[0] if user else None

    import json
    cur.execute("""
        INSERT INTO orders (user_id, mobile, first_name, last_name, province, city,
                           postal_code, address, items, total_price, shipping_cost, final_price)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
    """, (user_id, mobile, first_name, last_name, province, city,
          postal_code, address, json.dumps(items, ensure_ascii=False), total, shipping, final))
    order_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"order_id": order_id, "final_price": final, "message": "سفارش ثبت شد"}), 200


# ===== پرداخت زرین‌پال =====
@app.route("/payment/request", methods=["POST"])
def payment_request():
    data = request.json
    order_id = data.get("order_id")
    if not order_id:
        return jsonify({"error": "شناسه سفارش الزامی است"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT final_price, mobile, status FROM orders WHERE id=%s", (order_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return jsonify({"error": "سفارش یافت نشد"}), 404

    final_price, mobile, status = row
    if status == "paid":
        cur.close(); conn.close()
        return jsonify({"error": "این سفارش قبلاً پرداخت شده است"}), 400

    callback_url = f"{SITE_URL}/payment-callback.html?order_id={order_id}"

    try:
        resp = requests.post(ZP_REQUEST_URL, json={
            "merchant_id": ZARINPAL_MERCHANT_ID,
            "amount": int(final_price) * 10,  # تومان به ریال
            "callback_url": callback_url,
            "description": f"سفارش #{order_id} - سکه پارسی",
            "metadata": {"mobile": mobile, "order_id": order_id}
        }, timeout=15)
        result = resp.json()
    except Exception as e:
        cur.close(); conn.close()
        return jsonify({"error": "خطا در اتصال به درگاه پرداخت"}), 502

    data_block = result.get("data", {})
    if data_block.get("code") == 100:
        authority = data_block.get("authority")
        cur.execute("UPDATE orders SET payment_ref=%s WHERE id=%s", (authority, order_id))
        conn.commit()
        cur.close(); conn.close()
        return jsonify({
            "payment_url": ZP_GATEWAY_URL + authority,
            "authority": authority
        }), 200
    else:
        cur.close(); conn.close()
        errors = result.get("errors", {})
        return jsonify({"error": "خطا در ایجاد تراکنش", "details": errors}), 400


@app.route("/payment/verify", methods=["POST"])
def payment_verify():
    data = request.json
    order_id  = data.get("order_id")
    authority = data.get("authority")

    if not order_id or not authority:
        return jsonify({"error": "اطلاعات ناقص است"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT final_price, status, payment_ref FROM orders WHERE id=%s", (order_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return jsonify({"error": "سفارش یافت نشد"}), 404

    final_price, status, saved_authority = row

    if status == "paid":
        cur.close(); conn.close()
        return jsonify({"message": "این سفارش قبلاً تایید شده است", "already_paid": True}), 200

    if saved_authority != authority:
        cur.close(); conn.close()
        return jsonify({"error": "اطلاعات تراکنش مطابقت ندارد"}), 400

    try:
        resp = requests.post(ZP_VERIFY_URL, json={
            "merchant_id": ZARINPAL_MERCHANT_ID,
            "amount": int(final_price) * 10,
            "authority": authority
        }, timeout=15)
        result = resp.json()
    except Exception as e:
        cur.close(); conn.close()
        return jsonify({"error": "خطا در اتصال به درگاه پرداخت"}), 502

    data_block = result.get("data", {})
    if data_block.get("code") in (100, 101):
        ref_id = data_block.get("ref_id", "")
        cur.execute("UPDATE orders SET status='paid', payment_ref=%s WHERE id=%s",
                    (str(ref_id), order_id))
        conn.commit()
        cur.close(); conn.close()
        return jsonify({"message": "پرداخت با موفقیت تایید شد", "ref_id": ref_id, "success": True}), 200
    else:
        cur.execute("UPDATE orders SET status='cancelled' WHERE id=%s", (order_id,))
        conn.commit()
        cur.close(); conn.close()
        return jsonify({"error": "پرداخت ناموفق بود", "success": False}), 400


# ===== پنل مدیریت =====
@app.route("/admin/orders", methods=["GET"])
def admin_orders():
    password = request.headers.get("X-Admin-Pass", "")
    if password != ADMIN_PASS:
        return jsonify({"error": "دسترسی ندارید"}), 403

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT o.id, o.mobile, o.first_name, o.last_name,
               o.province, o.city, o.postal_code, o.address,
               o.items, o.total_price, o.shipping_cost, o.final_price,
               o.status, o.created_at
        FROM orders o
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
            "province": r[4], "city": r[5],
            "postal_code": r[6], "address": r[7],
            "items": r[8], "total_price": r[9],
            "shipping_cost": r[10], "final_price": r[11],
            "status": r[12], "created_at": str(r[13])
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
