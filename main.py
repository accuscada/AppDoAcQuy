import os
import io
import json
import re
import sqlite3
import pandas as pd
from PIL import Image
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import google.generativeai as genai

# ==========================================
# 1. CẤU HÌNH API KEY VÀ MODEL GEMINI
# ==========================================
# Thay chuỗi bên dưới bằng API Key thật của bạn (dạng AIzaSy...)
API_KEY = "AQ.Ab8RN6LuW5GlCcCW1q9k-M8wGSh1rAlDHiIkFoX6LsFVFT_PMg"

# Cấu hình Gemini API
genai.configure(api_key=API_KEY)

# ==========================================
# 2. KHỞI TẠO DỊCH VỤ FASTAPI & CƠ SỞ DỮ LIỆU
# ==========================================
app = FastAPI(title="AccuField Vision Backend")

# Cho phép kết nối CORS từ GitHub Pages / Mobile Browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_NAME = "battery_reports.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_name TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            v1 REAL, r1 REAL,
            v2 REAL, r2 REAL,
            img1_path TEXT, img2_path TEXT,
            img3_path TEXT, img4_path TEXT, img5_path TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ==========================================
# 3. HÀM XỬ LÝ OCR BẰNG AI GEMINI
# ==========================================
def analyze_image_with_gemini(image_bytes: bytes):
    try:
        # 1. Sử dụng mô hình gemini-2.5-flash
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        prompt = """
        Bạn là chuyên gia OCR đọc màn hình máy đo ắc quy.
        Hãy đọc giá trị Điện áp (Volt / V) và Nội trở (milli-Ohm / mΩ hoặc Ω) trên màn hình.
        Trả về kết quả chuẩn duy nhất dưới dạng JSON thuần túy, KHÔNG dùng markdown codeblock:
        {"voltage": 12.65, "resistance": 4.15}
        """
        
        image = Image.open(io.BytesIO(image_bytes))
        response = model.generate_content([prompt, image])
        text_response = response.text.strip()
        
        # 2. Làm sạch chuỗi JSON (xóa bỏ markdown ```json nếu có)
        text_response = re.sub(r'```json\s*', '', text_response)
        text_response = re.sub(r'```\s*', '', text_response)
        text_response = text_response.strip()
        
        # 3. Trích xuất JSON bằng Regex
        json_match = re.search(r'\{.*?\}', text_response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            v = float(data.get("voltage", 0.0))
            r = float(data.get("resistance", 0.0))
            return v, r
            
        return 0.0, 0.0
    except Exception as e:
        print(f"Lỗi AI OCR chi tiết: {e}")
        return 0.0, 0.0



# ==========================================
# 4. CAC ENDPOINTS API
# ==========================================

@app.get("/")
def root():
    return {"status": "online", "message": "AccuField Vision API đang hoạt động!"}

@app.post("/api/ocr")
async def ocr_endpoint(file: UploadFile = File(...)):
    contents = await file.read()
    voltage, resistance = analyze_image_with_gemini(contents)
    return {"voltage": voltage, "resistance": resistance}

@app.post("/api/save-report")
async def save_report(
    station_name: str = Form(...),
    v1: float = Form(0.0), r1: float = Form(0.0),
    v2: float = Form(0.0), r2: float = Form(0.0)
):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO reports (station_name, v1, r1, v2, r2)
            VALUES (?, ?, ?, ?, ?)
        ''', (station_name, v1, r1, v2, r2))
        conn.commit()
        conn.close()
        return {"success": True, "message": "Đã lưu báo cáo thành công!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/export-excel")
def export_excel():
    try:
        conn = sqlite3.connect(DB_NAME)
        df = pd.read_sql_query("SELECT * FROM reports ORDER BY timestamp DESC", conn)
        conn.close()
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Báo Cáo Ắc Quy')
        output.seek(0)
        
        headers = {
            'Content-Disposition': 'attachment; filename="Bao_Cao_Do_Ac_Quy.xlsx"'
        }
        return StreamingResponse(
            output, 
            headers=headers, 
            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000, reload=True)
