import json
import os
import hashlib
import hmac
import base64
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler

# นำเข้า psycopg2 สำหรับเชื่อมต่อ PostgreSQL
try:
    import psycopg2
except ImportError:
    print("Warning: psycopg2-binary not installed. Database logging will fail unless deployed on Vercel with correct dependencies.")

def load_dotenv():
    """
    โหลดไฟล์ .env (ถ้ามีอยู่) เข้าสู่ os.environ อัตโนมัติสำหรับการรันแบบโลคอล
    โดยไม่ต้องติดตั้งไลบรารีภายนอกเพิ่มเติม
    """
    try:
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
        if os.path.exists(env_path):
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, val = line.split('=', 1)
                        os.environ[key.strip()] = val.strip()
            print("โหลดค่าจากไฟล์ .env สำเร็จ")
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการโหลดไฟล์ .env: {e}")

# เรียกใช้งานโหลด .env
load_dotenv()

# โหลดค่าคอนฟิกจาก Environment Variables (พร้อมตั้งค่าเริ่มต้นสำรองไว้สำหรับการทดสอบ)
LINE_CHANNEL_SECRET = os.environ.get(
    "LINE_CHANNEL_SECRET", 
    "ac3cbbc2f2ce387afc7a5a05b017bc17"
)
LINE_ACCESS_TOKEN = os.environ.get(
    "LINE_ACCESS_TOKEN", 
    "9WP9aN44vx6Ho47/ajmUdv7btLBfBkPFykwEXsiZS3Rhk9LR32qXXwgh7EEejXUz3c06aEzHXWYvZfMDTX3AFi5IBKL7+VAVuGOhb9YwV5yr7Kv4lC8B8/pPfnuYNuaEjk93BBbhboexJ65U0ihotQdB04t89/1O/w1cDnyilFU="
)
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_P8dMm7QsFULy@ep-mute-mode-aoacf741-pooler.c-2.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
)

def init_db():
    """
    ฟังก์ชันสำหรับสร้างตาราง messages และ keywords ในฐานข้อมูล Neon PostgreSQL
    หากยังไม่มีตารางนี้ ระบบจะสร้างตารางขึ้นมาอัตโนมัติพร้อมใส่ข้อมูลเริ่มต้น
    """
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        # 1. สร้างตารางเก็บข้อความแชท (messages)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        
        # 2. สร้างตารางเก็บคีย์เวิร์ดตอบกลับ (keywords)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS keywords (
                id SERIAL PRIMARY KEY,
                keyword TEXT UNIQUE NOT NULL,
                reply_text TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        
        # 3. ใส่ข้อมูลเริ่มต้นให้ตาราง keywords (หากยังไม่มีข้อมูลเลย)
        cur.execute("SELECT COUNT(*) FROM keywords;")
        count = cur.fetchone()[0]
        if count == 0:
            defaults = [
                ("ราคา", "ราคาของสินค้าเริ่มต้นที่ 100 บาทครับ สามารถสอบถามรุ่นที่สนใจเพิ่มเติมได้เลยครับ 🏷️"),
                ("สั่งซื้อ", "หากต้องการสั่งซื้อสินค้า สามารถพิมพ์รายการสินค้าที่ต้องการและจำนวน หรือแจ้งชื่อ-ที่อยู่เพื่อตรวจสอบค่าจัดส่งได้เลยครับ 🛒"),
                ("สวัสดี", "สวัสดีครับ! ยินดีต้อนรับสู่บริการของเรา มีอะไรให้เราช่วยเหลือวันนี้ไหมครับ? 😊")
            ]
            cur.executemany(
                "INSERT INTO keywords (keyword, reply_text) VALUES (%s, %s);",
                defaults
            )
            print("ใส่ข้อมูลคีย์เวิร์ดเริ่มต้นสำเร็จ")
            
        conn.commit()
        cur.close()
        conn.close()
        print("ฐานข้อมูลได้รับการเริ่มต้น/ตรวจสอบตารางเรียบร้อยแล้ว")
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการตรวจสอบหรือสร้างตารางในฐานข้อมูล: {e}")

# รันการเตรียมตารางฐานข้อมูลเมื่อเซิร์ฟเวอร์เริ่มทำงาน
try:
    init_db()
except Exception as e:
    print(f"ไม่สามารถเริ่มการเชื่อมต่อฐานข้อมูลตอนรันโมดูลได้: {e}")

def verify_signature(body_bytes, signature_str):
    """
    ฟังก์ชันตรวจสอบความถูกต้องของ x-line-signature เพื่อยืนยันว่า
    ข้อมูลถูกส่งมาจากเซิร์ฟเวอร์ LINE จริงๆ
    """
    if not signature_str:
        return False
    
    hash_obj = hmac.new(
        LINE_CHANNEL_SECRET.encode('utf-8'),
        body_bytes,
        hashlib.sha256
    )
    expected_signature = base64.b64encode(hash_obj.digest()).decode('utf-8')
    return hmac.compare_digest(expected_signature, signature_str)

def save_message(user_id, message_text):
    """
    ฟังก์ชันบันทึกข้อความที่ผู้ใช้ส่งมา ลงในฐานข้อมูล PostgreSQL
    """
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO messages (user_id, message) VALUES (%s, %s)",
            (user_id, message_text)
        )
        conn.commit()
        cur.close()
        conn.close()
        print(f"บันทึกข้อความจาก {user_id} สำเร็จ")
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการบันทึกข้อมูลลงฐานข้อมูล: {e}")

def get_reply_text(user_message):
    """
    ฟังก์ชันวิเคราะห์ข้อความของผู้ใช้เพื่อจับคู่ Keyword แบบไดนามิกจากฐานข้อมูล
    """
    msg = user_message.strip()
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        # ดึงคำสำคัญและคำตอบทั้งหมดที่มีอยู่ในตาราง keywords
        cur.execute("SELECT keyword, reply_text FROM keywords;")
        keywords = cur.fetchall()
        cur.close()
        conn.close()
        
        # วนลูปตรวจจับคำที่มีอยู่ในข้อความของผู้ใช้ (ไม่สนตัวอักษรพิมพ์เล็ก-ใหญ่ในภาษาอังกฤษ)
        for keyword, reply_text in keywords:
            if keyword.lower() in msg.lower():
                return reply_text
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการดึงข้อมูลคีย์เวิร์ดจากฐานข้อมูล: {e}")

    # ข้อความเริ่มต้นหากไม่ตรงกับ Keyword ใดๆ เลย
    return "ขอบคุณที่ทักทายเราครับ! ระบบได้บันทึกข้อความของคุณแล้ว เจ้าหน้าที่จะรีบเข้ามาตอบกลับโดยเร็วที่สุดครับ 💬"

def reply_message(reply_token, text):
    """
    ส่ง HTTP POST เพื่อตอบกลับข้อความ (Reply Message) ไปยัง LINE
    """
    url = "https://api.line.me/v2/bot/message/reply"
    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as response:
            response_body = response.read().decode('utf-8')
            print(f"ส่งข้อความตอบกลับ LINE สำเร็จ: {response_body}")
    except urllib.error.HTTPError as e:
        print(f"เกิดข้อผิดพลาด HTTP ในการตอบกลับ LINE: รหัส {e.code} {e.reason}")
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการตอบกลับ LINE: {e}")

# HTML โครงสร้างแดชบอร์ดจัดการข้อมูล (Aesthetic Slate Dark Theme)
HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="th">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LINE OA Chatbot Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Prompt:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-primary: #0f172a;
            --bg-secondary: #1e293b;
            --bg-tertiary: #334155;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --accent: #06C755;
            --accent-hover: #05b04b;
            --danger: #ef4444;
            --danger-hover: #dc2626;
            --border: #1e293b;
            --glass-bg: rgba(30, 41, 59, 0.7);
            --glass-border: rgba(255, 255, 255, 0.05);
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Inter', 'Prompt', sans-serif;
        }

        body {
            background-color: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            overflow-x: hidden;
        }

        ::-webkit-scrollbar {
            width: 6px;
            height: 6px;
        }
        ::-webkit-scrollbar-track {
            background: var(--bg-primary);
        }
        ::-webkit-scrollbar-thumb {
            background: var(--bg-tertiary);
            border-radius: 4px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: var(--text-secondary);
        }

        header {
            background: var(--glass-bg);
            backdrop-filter: blur(12px);
            border-bottom: 1px solid var(--glass-border);
            padding: 1.5rem 2rem;
            position: sticky;
            top: 0;
            z-index: 10;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .logo-area {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .logo-dot {
            width: 12px;
            height: 12px;
            background-color: var(--accent);
            border-radius: 50%;
            box-shadow: 0 0 12px var(--accent);
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0% { transform: scale(0.9); opacity: 0.6; }
            50% { transform: scale(1.1); opacity: 1; }
            100% { transform: scale(0.9); opacity: 0.6; }
        }

        header h1 {
            font-size: 1.4rem;
            font-weight: 600;
            letter-spacing: -0.025em;
        }

        header p {
            font-size: 0.85rem;
            color: var(--text-secondary);
        }

        main {
            flex: 1;
            padding: 2rem;
            max-width: 1400px;
            width: 100%;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            gap: 2rem;
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 1.5rem;
        }

        .stat-card {
            background: var(--bg-secondary);
            border: 1px solid var(--glass-border);
            border-radius: 16px;
            padding: 1.5rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            transition: all 0.3s ease;
        }

        .stat-card:hover {
            transform: translateY(-2px);
            border-color: rgba(6, 199, 85, 0.2);
            box-shadow: 0 10px 20px -10px rgba(0, 0, 0, 0.5);
        }

        .stat-info h3 {
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
            margin-bottom: 0.5rem;
        }

        .stat-info p {
            font-size: 2rem;
            font-weight: 700;
        }

        .stat-icon {
            width: 48px;
            height: 48px;
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.03);
            display: flex;
            align-items: center;
            justify-content: center;
            color: var(--accent);
        }

        .dashboard-layout {
            display: grid;
            grid-template-columns: 1fr 2fr;
            gap: 2rem;
        }

        @media (max-width: 1024px) {
            .dashboard-layout {
                grid-template-columns: 1fr;
            }
        }

        .card {
            background: var(--bg-secondary);
            border: 1px solid var(--glass-border);
            border-radius: 16px;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        .card-header {
            padding: 1.5rem;
            border-bottom: 1px solid var(--glass-border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .card-header h2 {
            font-size: 1.1rem;
            font-weight: 600;
        }

        .card-body {
            padding: 1.5rem;
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }

        .form-group {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }

        label {
            font-size: 0.85rem;
            font-weight: 500;
            color: var(--text-secondary);
        }

        input, textarea {
            background: var(--bg-primary);
            border: 1px solid var(--glass-border);
            border-radius: 8px;
            padding: 0.75rem 1rem;
            color: var(--text-primary);
            font-size: 0.95rem;
            outline: none;
            transition: all 0.2s ease;
            width: 100%;
        }

        input:focus, textarea:focus {
            border-color: var(--accent);
            box-shadow: 0 0 0 2px rgba(6, 199, 85, 0.15);
        }

        textarea {
            resize: vertical;
            min-height: 100px;
        }

        .btn {
            background: var(--accent);
            color: white;
            border: none;
            border-radius: 8px;
            padding: 0.75rem 1.5rem;
            font-size: 0.95rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
        }

        .btn:hover {
            background: var(--accent-hover);
        }

        .btn-danger {
            background: var(--danger);
        }

        .btn-danger:hover {
            background: var(--danger-hover);
        }

        .btn-outline {
            background: transparent;
            border: 1px solid var(--glass-border);
            color: var(--text-secondary);
        }

        .btn-outline:hover {
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-primary);
        }

        .keywords-list {
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            max-height: 400px;
            overflow-y: auto;
            padding-right: 4px;
        }

        .keyword-item {
            background: var(--bg-primary);
            border: 1px solid var(--glass-border);
            border-radius: 8px;
            padding: 1rem;
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 1rem;
            transition: border-color 0.2s ease;
        }

        .keyword-item:hover {
            border-color: rgba(255, 255, 255, 0.1);
        }

        .keyword-details {
            flex: 1;
        }

        .keyword-badge {
            background: rgba(6, 199, 85, 0.1);
            color: var(--accent);
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
            display: inline-block;
            margin-bottom: 0.5rem;
        }

        .keyword-reply {
            font-size: 0.85rem;
            color: var(--text-secondary);
            line-height: 1.4;
        }

        .actions {
            display: flex;
            gap: 0.5rem;
        }

        .action-btn {
            background: transparent;
            border: none;
            color: var(--text-secondary);
            cursor: pointer;
            padding: 4px;
            border-radius: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s ease;
        }

        .action-btn:hover {
            background: rgba(255, 255, 255, 0.05);
        }

        .action-btn.delete:hover {
            color: var(--danger);
        }

        .action-btn.edit:hover {
            color: var(--accent);
        }

        .table-container {
            overflow-x: auto;
            border-radius: 8px;
            background: var(--bg-primary);
            border: 1px solid var(--glass-border);
            max-height: 580px;
            overflow-y: auto;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }

        th {
            background: rgba(255, 255, 255, 0.02);
            padding: 1rem;
            font-size: 0.85rem;
            color: var(--text-secondary);
            font-weight: 500;
            border-bottom: 1px solid var(--glass-border);
            position: sticky;
            top: 0;
            z-index: 1;
        }

        td {
            padding: 1rem;
            font-size: 0.85rem;
            border-bottom: 1px solid var(--glass-border);
            color: var(--text-secondary);
            vertical-align: middle;
        }

        tr:last-child td {
            border-bottom: none;
        }

        tr:hover td {
            background: rgba(255, 255, 255, 0.01);
            color: var(--text-primary);
        }

        .user-id-cell {
            font-family: monospace;
            font-size: 0.8rem;
        }

        .message-cell {
            color: var(--text-primary);
        }

        .date-cell {
            white-space: nowrap;
        }

        .search-container {
            display: flex;
            gap: 1rem;
            margin-bottom: 0.5rem;
        }

        .toast-container {
            position: fixed;
            bottom: 2rem;
            right: 2rem;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            z-index: 100;
        }

        .toast {
            background: var(--bg-secondary);
            border-left: 4px solid var(--accent);
            border-radius: 8px;
            padding: 1rem 1.5rem;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
            display: flex;
            align-items: center;
            gap: 0.75rem;
            color: var(--text-primary);
            font-size: 0.85rem;
            animation: slideIn 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards;
            min-width: 300px;
        }

        .toast.error {
            border-left-color: var(--danger);
        }

        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }

        .empty-state {
            text-align: center;
            padding: 3rem;
            color: var(--text-secondary);
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 1rem;
        }

        .empty-state svg {
            width: 48px;
            height: 48px;
            opacity: 0.2;
        }
    </style>
</head>
<body>
    <header>
        <div class="logo-area">
            <div class="logo-dot"></div>
            <div>
                <h1>LINE OA Chatbot Dashboard</h1>
                <p>ควบคุม แก้ไขคำตอบบอท และสถิติคำสนทนา</p>
            </div>
        </div>
        <div>
            <span style="font-size: 0.8rem; background: rgba(255,255,255,0.05); padding: 6px 12px; border-radius: 20px; border: 1px solid var(--glass-border)">
                Neon PostgreSQL & Vercel Serverless
            </span>
        </div>
    </header>

    <main>
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-info">
                    <h3>จำนวนคำคีย์เวิร์ดบอท</h3>
                    <p id="stat-keywords">0</p>
                </div>
                <div class="stat-icon">
                    <svg width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M7 20l4-16m2 16l4-16M6 9h14M4 15h14"></path></svg>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-info">
                    <h3>ข้อความที่ได้รับทั้งหมด</h3>
                    <p id="stat-messages">0</p>
                </div>
                <div class="stat-icon">
                    <svg width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"></path></svg>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-info">
                    <h3>จำนวนผู้ใช้ติดต่อเข้ามา</h3>
                    <p id="stat-users">0</p>
                </div>
                <div class="stat-icon">
                    <svg width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"></path></svg>
                </div>
            </div>
        </div>

        <div class="dashboard-layout">
            <div class="card">
                <div class="card-header">
                    <h2>จัดการคีย์เวิร์ดและการตอบกลับ</h2>
                </div>
                <div class="card-body">
                    <form id="keyword-form" onsubmit="handleFormSubmit(event)">
                        <div class="form-group">
                            <label for="input-keyword">คำสำคัญ (Keyword)</label>
                            <input type="text" id="input-keyword" required placeholder="เช่น โปรโมชั่น, จัดส่ง">
                        </div>
                        <div class="form-group">
                            <label for="input-reply">ข้อความที่ต้องการให้ตอบกลับ</label>
                            <textarea id="input-reply" required placeholder="เช่น รายละเอียดโปรโมชั่นเดือนนี้..."></textarea>
                        </div>
                        <button type="submit" class="btn" id="submit-btn" style="width: 100%">
                            บันทึกข้อมูลคีย์เวิร์ด
                        </button>
                    </form>
                    
                    <div style="border-top: 1px solid var(--glass-border); padding-top: 1.5rem;">
                        <h3 style="font-size: 0.85rem; font-weight: 500; color: var(--text-secondary); margin-bottom: 1rem;">รายการคีย์เวิร์ดในระบบ</h3>
                        <div class="keywords-list" id="keywords-container"></div>
                    </div>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <h2>ประวัติข้อความของลูกค้าที่ส่งมา</h2>
                    <div class="actions">
                        <button onclick="confirmClearMessages()" class="btn btn-danger btn-outline" style="padding: 6px 12px; font-size: 0.8rem;">
                            ล้างประวัติทั้งหมด
                        </button>
                    </div>
                </div>
                <div class="card-body">
                    <div class="search-container">
                        <input type="text" id="search-input" oninput="filterMessages()" placeholder="ค้นหายูสเซอร์ หรือข้อความของลูกค้า...">
                    </div>
                    <div class="table-container">
                        <table id="messages-table">
                            <thead>
                                <tr>
                                    <th style="width: 30%">User ID (LINE)</th>
                                    <th style="width: 45%">ข้อความที่ส่งมา</th>
                                    <th style="width: 25%">วัน-เวลา</th>
                                </tr>
                            </thead>
                            <tbody id="messages-container"></tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </main>

    <div class="toast-container" id="toast-container"></div>

    <script>
        let allMessages = [];
        let allKeywords = [];

        async function loadData() {
            try {
                const kwRes = await fetch('/api/keywords');
                allKeywords = await kwRes.json();
                renderKeywords(allKeywords);
                document.getElementById('stat-keywords').textContent = allKeywords.length;

                const msgRes = await fetch('/api/messages');
                allMessages = await msgRes.json();
                renderMessages(allMessages);
                document.getElementById('stat-messages').textContent = allMessages.length;

                const uniqueUsers = new Set(allMessages.map(m => m.user_id)).size;
                document.getElementById('stat-users').textContent = uniqueUsers;

            } catch (err) {
                console.error("Error loading data:", err);
                showToast("ไม่สามารถดึงข้อมูลจากระบบได้", true);
            }
        }

        function renderKeywords(keywords) {
            const container = document.getElementById('keywords-container');
            if (keywords.length === 0) {
                container.innerHTML = `
                    <div class="empty-state">
                        <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M7 20l4-16m2 16l4-16M6 9h14M4 15h14"></path></svg>
                        <p style="font-size: 0.8rem;">ยังไม่มีคีย์เวิร์ดในระบบ</p>
                    </div>
                `;
                return;
            }

            container.innerHTML = keywords.map(k => `
                <div class="keyword-item">
                    <div class="keyword-details">
                        <span class="keyword-badge">${escapeHtml(k.keyword)}</span>
                        <div class="keyword-reply">${escapeHtml(k.reply_text)}</div>
                    </div>
                    <div class="actions">
                        <button class="action-btn edit" onclick="editKeyword('${escapeJs(k.keyword)}', '${escapeJs(k.reply_text)}')" title="แก้ไข">
                            <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"></path></svg>
                        </button>
                        <button class="action-btn delete" onclick="deleteKeyword('${escapeJs(k.keyword)}')" title="ลบ">
                            <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
                        </button>
                    </div>
                </div>
            `).join('');
        }

        function renderMessages(messages) {
            const container = document.getElementById('messages-container');
            if (messages.length === 0) {
                container.innerHTML = `
                    <tr>
                        <td colspan="3">
                            <div class="empty-state">
                                <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"></path></svg>
                                <p style="font-size: 0.8rem;">ยังไม่มีประวัติการส่งข้อความ</p>
                            </div>
                        </td>
                    </tr>
                `;
                return;
            }

            container.innerHTML = messages.map(m => `
                <tr>
                    <td class="user-id-cell">${escapeHtml(m.user_id)}</td>
                    <td class="message-cell">${escapeHtml(m.message)}</td>
                    <td class="date-cell">${escapeHtml(m.created_at)}</td>
                </tr>
            `).join('');
        }

        function filterMessages() {
            const q = document.getElementById('search-input').value.toLowerCase();
            const filtered = allMessages.filter(m => 
                m.user_id.toLowerCase().includes(q) || 
                m.message.toLowerCase().includes(q)
            );
            renderMessages(filtered);
        }

        function editKeyword(keyword, replyText) {
            document.getElementById('input-keyword').value = keyword;
            document.getElementById('input-reply').value = replyText;
            document.getElementById('input-reply').focus();
        }

        async function handleFormSubmit(e) {
            e.preventDefault();
            const keyword = document.getElementById('input-keyword').value.trim();
            const reply_text = document.getElementById('input-reply').value.trim();
            
            try {
                const res = await fetch('/api/keywords/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ keyword, reply_text })
                });
                const result = await res.json();
                if (result.status === 'success') {
                    showToast(result.message);
                    document.getElementById('keyword-form').reset();
                    loadData();
                } else {
                    showToast(result.error || "เกิดข้อผิดพลาดในการบันทึก", true);
                }
            } catch (err) {
                showToast("การเชื่อมต่อล้มเหลว", true);
            }
        }

        async function deleteKeyword(keyword) {
            if (!confirm(`คุณต้องการลบคีย์เวิร์ด "${keyword}" ใช่หรือไม่?`)) return;
            try {
                const res = await fetch('/api/keywords/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ keyword })
                });
                const result = await res.json();
                if (result.status === 'success') {
                    showToast(result.message);
                    loadData();
                } else {
                    showToast(result.error || "เกิดข้อผิดพลาดในการลบ", true);
                }
            } catch (err) {
                showToast("การเชื่อมต่อล้มเหลว", true);
            }
        }

        async function confirmClearMessages() {
            if (!confirm("คุณต้องการลบประวัติการรับข้อความทั้งหมดใช่หรือไม่? (การกระทำนี้ไม่สามารถย้อนกลับได้)")) return;
            try {
                const res = await fetch('/api/messages/clear', { method: 'POST' });
                const result = await res.json();
                if (result.status === 'success') {
                    showToast(result.message);
                    loadData();
                } else {
                    showToast(result.error || "เกิดข้อผิดพลาดในการล้างประวัติ", true);
                }
            } catch (err) {
                showToast("การเชื่อมต่อล้มเหลว", true);
            }
        }

        function showToast(message, isError = false) {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            toast.className = `toast ${isError ? 'error' : ''}`;
            toast.innerHTML = `
                <svg width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                    ${isError 
                        ? '<path d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>'
                        : '<path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>'
                    }
                </svg>
                <span>${escapeHtml(message)}</span>
            `;
            container.appendChild(toast);
            setTimeout(() => {
                toast.style.animation = 'slideIn 0.3s cubic-bezier(0.16, 1, 0.3, 1) reverse forwards';
                setTimeout(() => toast.remove(), 300);
            }, 3000);
        }

        function escapeHtml(str) {
            return str.replace(/[&<>'"]/g, 
                tag => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag] || tag)
            );
        }

        function escapeJs(str) {
            return str.replace(/['"\\\n\r]/g, 
                char => ({ "'": "\\'", '"': '\\"', '\\': '\\\\', '\n': '\\n', '\r': '\\r' }[char] || char)
            );
        }

        // โหลดข้อมูลเริ่มต้นและตั้งค่าการดึงข้อมูลใหม่ทุก 10 วินาที
        window.onload = loadData;
        setInterval(loadData, 10000);
    </script>
</body>
</html>
"""

class handler(BaseHTTPRequestHandler):
    """
    คลาส Handler สำหรับจัดสรรการเรียกคำขอรับ Webhook และส่งหน้า Frontend Dashboard API
    """
    
    def do_GET(self):
        """
        จัดการคำขอ HTTP GET สำหรับการดึงข้อมูล API หรือการเรียกเข้าแดชบอร์ดหลัก
        """
        # API: เรียกดึงประวัติข้อความของลูกค้า
        if self.path == '/api/messages':
            try:
                conn = psycopg2.connect(DATABASE_URL)
                cur = conn.cursor()
                cur.execute("""
                    SELECT user_id, message, to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') 
                    FROM messages 
                    ORDER BY id DESC 
                    LIMIT 100;
                """)
                rows = cur.fetchall()
                cur.close()
                conn.close()
                
                messages = [{"user_id": r[0], "message": r[1], "created_at": r[2]} for r in rows]
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps(messages).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"ข้อผิดพลาดจากเซิร์ฟเวอร์: {str(e)}"}).encode('utf-8'))
                
        # API: เรียกดึงคีย์เวิร์ดทั้งหมดที่มีในระบบ
        elif self.path == '/api/keywords':
            try:
                conn = psycopg2.connect(DATABASE_URL)
                cur = conn.cursor()
                cur.execute("SELECT keyword, reply_text FROM keywords ORDER BY keyword ASC;")
                rows = cur.fetchall()
                cur.close()
                conn.close()
                
                keywords = [{"keyword": r[0], "reply_text": r[1]} for r in rows]
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps(keywords).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"ข้อผิดพลาดจากเซิร์ฟเวอร์: {str(e)}"}).encode('utf-8'))
                
        # แสดงหน้าจอหลัก Admin Dashboard (Frontend)
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_DASHBOARD.encode('utf-8'))

    def do_POST(self):
        """
        จัดการคำขอ HTTP POST ซึ่งใช้รับข้อมูล Webhook จาก LINE และบันทึก/แก้ไขข้อมูลจากแดชบอร์ด
        """
        # API Dashboard: บันทึกหรืออัปเดต Keyword
        if self.path == '/api/keywords/save':
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                body_bytes = self.rfile.read(content_length)
                data = json.loads(body_bytes.decode('utf-8'))
                
                keyword = data.get('keyword', '').strip()
                reply_text = data.get('reply_text', '').strip()
                
                if not keyword or not reply_text:
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "กรุณากรอกคำสำคัญและข้อความตอบกลับให้ครบถ้วน"}).encode('utf-8'))
                    return
                
                conn = psycopg2.connect(DATABASE_URL)
                cur = conn.cursor()
                # ทำการ UPSERT (หากมีคีย์เวิร์ดอยู่แล้วจะแทนที่ตัวเดิม หากไม่มีจะเพิ่มใหม่)
                cur.execute("""
                    INSERT INTO keywords (keyword, reply_text)
                    VALUES (%s, %s)
                    ON CONFLICT (keyword)
                    DO UPDATE SET reply_text = EXCLUDED.reply_text;
                """, (keyword, reply_text))
                conn.commit()
                cur.close()
                conn.close()
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "บันทึกคีย์เวิร์ดสำเร็จเรียบร้อย"}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"เกิดข้อผิดพลาดในการบันทึก: {str(e)}"}).encode('utf-8'))
                
        # API Dashboard: ลบ Keyword ออกจากระบบ
        elif self.path == '/api/keywords/delete':
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                body_bytes = self.rfile.read(content_length)
                data = json.loads(body_bytes.decode('utf-8'))
                
                keyword = data.get('keyword', '').strip()
                
                if not keyword:
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "ไม่พบข้อมูลคีย์เวิร์ดที่ระบุ"}).encode('utf-8'))
                    return
                
                conn = psycopg2.connect(DATABASE_URL)
                cur = conn.cursor()
                cur.execute("DELETE FROM keywords WHERE keyword = %s;", (keyword,))
                conn.commit()
                cur.close()
                conn.close()
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "ลบคีย์เวิร์ดสำเร็จ"}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"เกิดข้อผิดพลาดในการลบ: {str(e)}"}).encode('utf-8'))
                
        # API Dashboard: ลบประวัติแชททั้งหมด
        elif self.path == '/api/messages/clear':
            try:
                conn = psycopg2.connect(DATABASE_URL)
                cur = conn.cursor()
                cur.execute("TRUNCATE TABLE messages;")
                conn.commit()
                cur.close()
                conn.close()
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "ล้างประวัติข้อความสำเร็จ"}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"เกิดข้อผิดพลาดในการล้างประวัติ: {str(e)}"}).encode('utf-8'))
                
        # การทำงานปกติ: LINE Webhook POST
        else:
            signature = self.headers.get('x-line-signature') or self.headers.get('X-Line-Signature')
            content_length = int(self.headers.get('Content-Length', 0))
            body_bytes = self.rfile.read(content_length)
            
            # ตรวจสอบความถูกต้องของ Signature
            if not verify_signature(body_bytes, signature):
                self.send_response(403)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "ลายเซ็นไม่ถูกต้อง"}).encode('utf-8'))
                print("คำเตือน: ลายเซ็นของ Webhook ไม่ถูกต้อง")
                return
                
            try:
                body_str = body_bytes.decode('utf-8')
                data = json.loads(body_str)
                events = data.get('events', [])
                
                for event in events:
                    event_type = event.get('type')
                    if event_type == 'message':
                        message_info = event.get('message', {})
                        if message_info.get('type') == 'text':
                            reply_token = event.get('replyToken')
                            user_id = event.get('source', {}).get('userId', 'Unknown')
                            message_text = message_info.get('text', '')
                            
                            # ก) บันทึกข้อความลงฐานข้อมูล Neon
                            save_message(user_id, message_text)
                            
                            # ข) ดึงประโยคตอบกลับที่ตรงกับคีย์เวิร์ดล่าสุดใน PostgreSQL
                            reply_text = get_reply_text(message_text)
                            
                            # ค) ส่งข้อความกลับหาผู้ใช้
                            if reply_token:
                                reply_message(reply_token, reply_text)
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "สำเร็จ"}).encode('utf-8'))
                
            except Exception as e:
                print(f"เกิดข้อผิดพลาดในการประมวลผล Webhook: {e}")
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
