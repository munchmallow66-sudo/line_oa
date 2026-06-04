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
    ฟังก์ชันสำหรับสร้างตาราง messages ในฐานข้อมูล Neon PostgreSQL
    หากยังไม่มีตารางนี้ ระบบจะสร้างตารางขึ้นมาอัตโนมัติ
    """
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        # คำสั่ง SQL สำหรับสร้างตารางเก็บข้อความ
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("ฐานข้อมูลได้รับการเริ่มต้น/ตรวจสอบตารางเรียบร้อยแล้ว")
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการตรวจสอบหรือสร้างตารางในฐานข้อมูล: {e}")

# รันการเตรียมการสร้างตารางฐานข้อมูล ณ ตอนโหลดโมดูล (Cold Start)
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
    
    # คำนวณค่า HMAC-SHA256 โดยใช้ LINE_CHANNEL_SECRET เป็นคีย์
    hash_obj = hmac.new(
        LINE_CHANNEL_SECRET.encode('utf-8'),
        body_bytes,
        hashlib.sha256
    )
    
    # แปลงผลลัพธ์ที่คำนวณได้เป็น Base64
    expected_signature = base64.b64encode(hash_obj.digest()).decode('utf-8')
    
    # เปรียบเทียบสองลายเซ็นด้วย hmac.compare_digest เพื่อป้องกันปัญหา Timing Attack
    return hmac.compare_digest(expected_signature, signature_str)

def save_message(user_id, message_text):
    """
    ฟังก์ชันบันทึกข้อความที่ผู้ใช้ส่งมา ลงในตาราง messages ของฐานข้อมูล Neon PostgreSQL
    """
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        # ป้องกัน SQL Injection โดยการส่งค่าพารามิเตอร์แยกกับคำสั่ง SQL
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
    ฟังก์ชันวิเคราะห์ข้อความของผู้ใช้เพื่อจับคู่ Keyword และเตรียมข้อความตอบกลับ
    """
    # ตัดช่องว่างหัวท้ายของข้อความที่รับมา เพื่อความเสถียร
    msg = user_message.strip()
    
    if "ราคา" in msg:
        return "ราคาของสินค้าเริ่มต้นที่ 100 บาทครับ สามารถสอบถามรุ่นที่สนใจเพิ่มเติมได้เลยครับ 🏷️"
    elif "สั่งซื้อ" in msg:
        return "หากต้องการสั่งซื้อสินค้า สามารถพิมพ์รายการสินค้าที่ต้องการและจำนวน หรือแจ้งชื่อ-ที่อยู่เพื่อตรวจสอบค่าจัดส่งได้เลยครับ 🛒"
    elif "สวัสดี" in msg:
        return "สวัสดีครับ! ยินดีต้อนรับสู่บริการของเรา มีอะไรให้เราช่วยเหลือวันนี้ไหมครับ? 😊"
    else:
        # ข้อความตอบกลับเริ่มต้นหากผู้ใช้พิมพ์ข้อความอื่นนอกเหนือจาก Keyword
        return "ขอบคุณที่ทักทายเราครับ! ระบบได้บันทึกข้อความของคุณแล้ว เจ้าหน้าที่จะรีบเข้ามาตอบกลับโดยเร็วที่สุดครับ 💬"

def reply_message(reply_token, text):
    """
    ฟังก์ชันส่ง HTTP POST เพื่อตอบกลับข้อความ (Reply Message) ไปยัง LINE Messaging API
    โดยใช้ไลบรารีมาตรฐาน urllib ของ Python
    """
    url = "https://api.line.me/v2/bot/message/reply"
    
    # โครงสร้าง JSON ของข้อมูลที่ส่งให้ LINE
    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }
    
    # แปลง payload เป็น bytes ที่เข้ารหัส utf-8
    data = json.dumps(payload).encode('utf-8')
    
    # สร้าง Request วัตถุ และระบุ Headers ที่ต้องการ
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
        # ทำการส่งคำขอกลาง และรับข้อมูลตอบกลับ
        with urllib.request.urlopen(req) as response:
            response_body = response.read().decode('utf-8')
            print(f"ส่งข้อความตอบกลับ LINE สำเร็จ: {response_body}")
    except urllib.error.HTTPError as e:
        print(f"เกิดข้อผิดพลาด HTTP ในการตอบกลับ LINE: รหัส {e.code} {e.reason}")
        print(f"ข้อมูลเพิ่มเติม: {e.read().decode('utf-8')}")
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการตอบกลับ LINE: {e}")

class handler(BaseHTTPRequestHandler):
    """
    คลาส Handler สำหรับรับ HTTP request จาก Vercel Serverless Platform
    ขยายความสามารถมาจาก BaseHTTPRequestHandler ในไลบรารีมาตรฐาน Python
    """
    
    def do_POST(self):
        """
        จัดการคำขอ HTTP POST ซึ่งใช้รับ Webhook Event จากเซิร์ฟเวอร์ LINE
        """
        # ดึงลายเซ็น x-line-signature จาก Header
        signature = self.headers.get('x-line-signature') or self.headers.get('X-Line-Signature')
        
        # ตรวจสอบขนาดของเนื้อหาข้อมูล (Body)
        content_length = int(self.headers.get('Content-Length', 0))
        body_bytes = self.rfile.read(content_length)
        
        # 1. ตรวจสอบ Signature เพื่อยืนยันแหล่งที่มาของ Request
        if not verify_signature(body_bytes, signature):
            self.send_response(403)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": "ลายเซ็นไม่ถูกต้องหรือไม่พบข้อมูลลายเซ็น"}).encode('utf-8'))
            print("คำเตือน: ความพยายามเข้าใช้งานไม่ผ่านเนื่องจากลายเซ็นไม่ถูกต้อง")
            return
            
        try:
            # 2. แปลงเนื้อหาข้อมูลจาก Bytes เป็น JSON
            body_str = body_bytes.decode('utf-8')
            data = json.loads(body_str)
            events = data.get('events', [])
            
            # 3. ประมวลผลแต่ละ Event ที่ได้รับจาก LINE
            for event in events:
                event_type = event.get('type')
                
                # ตรวจจับเฉพาะข้อความ (Message Event) และเป็นตัวอักษร (Text Message)
                if event_type == 'message':
                    message_info = event.get('message', {})
                    if message_info.get('type') == 'text':
                        reply_token = event.get('replyToken')
                        user_id = event.get('source', {}).get('userId', 'Unknown')
                        message_text = message_info.get('text', '')
                        
                        # ก) บันทึกข้อความที่ผู้ใช้พิมพ์ส่งเข้ามาลงฐานข้อมูล Neon PostgreSQL
                        save_message(user_id, message_text)
                        
                        # ข) จับคู่ Keyword เพื่อหาประโยคตอบกลับที่เหมาะสม
                        reply_text = get_reply_text(message_text)
                        
                        # ค) เรียกฟังก์ชันตอบกลับข้อความไปยัง LINE
                        if reply_token:
                            reply_message(reply_token, reply_text)
            
            # ตอบกลับเซิร์ฟเวอร์ LINE ว่าได้รับคำขอเรียบร้อยแล้ว (HTTP 200 OK)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "สำเร็จ"}).encode('utf-8'))
            
        except Exception as e:
            # หากเกิดข้อผิดพลาดในการประมวลผลภายในโค้ด
            print(f"เกิดข้อผิดพลาดในการประมวลผล Webhook: {e}")
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"เกิดข้อผิดพลาดในเซิร์ฟเวอร์: {str(e)}"}).encode('utf-8'))

    def do_GET(self):
        """
        จัดการคำขอ HTTP GET สำหรับการเข้าถึงผ่านบราวเซอร์ทั่วไปเพื่อตรวจสอบสถานะ
        """
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        
        # แสดงหน้าเว็บ HTML ง่ายๆ เพื่อระบุว่าเซอร์วิสกำลังทำงานอยู่
        html_response = """
        <!DOCTYPE html>
        <html lang="th">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>LINE OA Chatbot Status</title>
            <style>
                body {
                    font-family: 'Helvetica Neue', Arial, sans-serif;
                    text-align: center;
                    background-color: #f7f9fa;
                    color: #333;
                    margin: 0;
                    padding: 50px 20px;
                }
                .container {
                    max-width: 600px;
                    margin: 0 auto;
                    background: white;
                    padding: 40px;
                    border-radius: 12px;
                    box-shadow: 0 4px 15px rgba(0,0,0,0.05);
                }
                h1 {
                    color: #06C755; /* LINE Green Color */
                    margin-bottom: 10px;
                }
                .status-badge {
                    display: inline-block;
                    background-color: #e6fcf0;
                    color: #06c755;
                    padding: 6px 16px;
                    border-radius: 20px;
                    font-weight: bold;
                    margin-bottom: 20px;
                }
                p {
                    font-size: 16px;
                    line-height: 1.6;
                    color: #666;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>LINE OA Chatbot API</h1>
                <div class="status-badge">ระบบกำลังทำงาน (Active)</div>
                <p>ระบบพร้อมให้บริการ Webhook รับข้อมูล POST จากเซิร์ฟเวอร์ LINE Developers แล้ว</p>
                <p style="font-size: 12px; color: #aaa; margin-top: 40px;">พัฒนาด้วย Python Standard Libraries (urllib) & psycopg2</p>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html_response.encode('utf-8'))
