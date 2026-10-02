import io
import re
import os
import sys
import base64
import sqlite3
import requests
from datetime import datetime
from fastapi import FastAPI, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image

# Sửa mã hóa ký tự UTF-8 cho Windows CMD
sys.stdout.reconfigure(encoding='utf-8')

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "images_storage"
os.makedirs(UPLOAD_DIR, exist_ok=True)
DB_NAME = "battery_data.db"

# --- 1. KHỞI TẠO CSDL SQLITE LƯU VẾT LỊCH SỬ ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS measure_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_name TEXT NOT NULL,
            measure_date TEXT NOT NULL,
            v1 REAL, r1 REAL,
            v2 REAL, r2 REAL,
            avg_resistance REAL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Khai báo API Key của bạn
# Lấy API Key từ Environment Variable của Render, nếu không có mới dùng chuỗi mặc định
API_KEY = os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6IYn99iatQHFwlziNqamIlFxCyZz0uzsOZFOeC3coOyhw")

# --- 2. API QUÉT ẢNH AI VISION ---
@app.post("/api/scan-meter")
async def scan_meter(file: UploadFile = File(...)):
    """Sử dụng Gemini REST API trực tiếp bóc tách số Điện áp và Nội trở"""
    try:
        contents = await file.read()
        base64_image = base64.b64encode(contents).decode('utf-8')
        
        prompt = (
            "Trích xuất số điện áp (đơn vị V) và nội trở (đơn vị mΩ hoặc Ω) từ ảnh máy đo này.\n"
            "Trả về duy nhất dạng JSON ngắn gọn không định dạng markdown:\n"
            '{"voltage": 13.128, "resistance": 9.50}'
        )
        
        models_to_try = [
            "gemini-3.8-flash",
            "gemini-3.5-flash",
            "gemini-3.1-flash"
        ]
        text_result = None

        for model in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={API_KEY}"
            payload = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": base64_image
                            }
                        }
                    ]
                }]
            }
            res = requests.post(url, json=payload, timeout=30)
            if res.status_code == 200:
                try:
                    text_result = res.json()['candidates'][0]['content']['parts'][0]['text'].strip()
                    break
                except Exception:
                    pass

        if not text_result:
            return {"status": "error", "message": "Không thể kết nối mô hình Gemini API."}

        voltage = None
        resistance = None
        
        # Bóc tách điện áp
        v_match = re.search(r'"voltage"\s*:\s*([\d\.]+)', text_result, re.IGNORECASE)
        if not v_match:
            v_match = re.search(r'(\d+[\.,]\d+|\d+)\s*[vV]', text_result)
        if v_match:
            voltage = float(v_match.group(1).replace(',', '.'))
            
        # Bóc tách nội trở
        r_match = re.search(r'"resistance"\s*:\s*([\d\.]+)', text_result, re.IGNORECASE)
        if not r_match:
            r_match = re.search(r'(\d+[\.,]\d+|\d+)', text_result)
        if r_match:
            resistance = float(r_match.group(1).replace(',', '.'))
            
        return {"status": "success", "voltage": voltage, "resistance": resistance}
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- 3. API LƯU BÁO CÁO VÀO CSDL ---
@app.post("/api/submit-report")
async def submit_report(
    station_name: str = Form(...),
    v1: float = Form(...),
    r1: float = Form(...),
    v2: float = Form(...),
    r2: float = Form(...),
    file_img1: UploadFile = File(None),
    file_img2: UploadFile = File(None)
):
    avg_r = round((r1 + r2) / 2, 2)
    current_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # Lưu file ảnh lưu trữ nếu có
    for i, file in enumerate([file_img1, file_img2], 1):
        if file and file.filename:
            file_path = os.path.join(UPLOAD_DIR, f"{station_name}_AQ{i}_{file.filename}")
            with open(file_path, "wb") as f:
                f.write(await file.read())
            
    # Lưu bản ghi đo vào SQLite CSDL
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO measure_history (station_name, measure_date, v1, r1, v2, r2, avg_resistance)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (station_name, current_date, v1, r1, v2, r2, avg_r))
    conn.commit()
    conn.close()
    
    print(f"--> [DB Saved] Station={station_name}, Date={current_date}, AvgR={avg_r}")
    
    return {
        "status": "success",
        "message": f"Cập nhật thành công trạm {station_name}",
        "avg_resistance": avg_r
    }

# --- 4. API LẤY DỮ LIỆU LỊCH SỬ CHO BIỂU ĐỒ & BẢNG ---
@app.get("/api/station-history/{station_name}")
async def get_station_history(station_name: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT measure_date, v1, r1, v2, r2, avg_resistance 
        FROM measure_history 
        WHERE station_name = ? 
        ORDER BY id ASC
    ''', (station_name,))
    rows = cursor.fetchall()
    conn.close()
    
    history = []
    for r in rows:
        history.append({
            "date": r[0],
            "v1": r[1], "r1": r[2],
            "v2": r[3], "r2": r[4],
            "avg_r": r[5]
        })
    return {"status": "success", "station": station_name, "history": history}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
