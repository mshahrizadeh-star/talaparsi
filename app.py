from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
import os
import random
import string
import datetime
import requests
import threading

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

# ===== Config =====
DB_HOST     = os.environ.get("DB_HOST", "")
DB_PORT     = os.environ.get("DB_PORT", "5432")
DB_USER     = os.environ.get("DB_USER", "base-user")
DB_PASS     = os.environ.get("DB_PASS", "")
DB_NAME     = os.environ.get("DB_NAME", "default")
SMS_IR_API  = os.environ.get("SMS_IR_APIKEY", "6Sy90FuUf4SwRwE7srdmzFwLqgghJt0rsPw19kXtqfhn09Mb")
ADMIN_PASS  = os.environ.get("ADMIN_PASS", "sekeparsi@admin")

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

    def send_sms():
        try:
            requests.post(
                "https://api.sms.ir/v1/send/verify",
                headers={
                    "X-API-KEY": SMS_IR_API,
                    "Content-Type": "application/json"
                },
                json={
