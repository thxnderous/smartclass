import matplotlib
matplotlib.use('Agg')  # ใช้ Agg backend สำหรับการสร้างกราฟแบบไม่ต้องเปิดหน้าต่าง

import firebase_admin
from firebase_admin import credentials, firestore, storage
import matplotlib.pyplot as plt
from flask import Flask, render_template, Response, jsonify
from datetime import datetime
from io import BytesIO
import pandas as pd
import logging
from dateutil import parser, tz
from collections import deque

# ตั้งค่า logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Firebase Initialization
cred = credentials.Certificate("smart-class-e9661-firebase-adminsdk-fbsvc-bf137255f5.json")
firebase_admin.initialize_app(cred, {
    'storageBucket': "smart-class-e9661.appspot.com"
})

db = firestore.client()
bucket = storage.bucket()
app = Flask(__name__)

# ตัวแปรเก็บคิววิดีโอ
video_queue = deque()
current_video_index = 0

def format_timestamp(timestamp):
    """แปลง timestamp ให้อยู่ในรูปแบบที่อ่านง่าย (ปรับเป็น Asia/Bangkok)"""
    try:
        # แปลง input ให้เป็น datetime object
        if isinstance(timestamp, str):
            dt = parser.parse(timestamp)
        elif isinstance(timestamp, datetime):
            dt = timestamp
        else:
            return "Unknown"

        # ถ้า dt ยังไม่มี tzinfo ให้ถือเป็น UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz.UTC)

        # แปลงโซนเวลาเป็น Asia/Bangkok
        local_dt = dt.astimezone(tz.gettz("Asia/Bangkok"))
        return local_dt.strftime('%H:%M:%S')  # แสดงแค่เวลา

    except Exception as e:
        logger.error(f"Error parsing timestamp {timestamp}: {e}")
        # fallback: ตัด string หรือใช้ fmt เดิม
        if isinstance(timestamp, str):
            return timestamp[:19]
        elif isinstance(timestamp, datetime):
            return timestamp.strftime('%Y-%m-%d %H:%M:%S')
        return "Unknown"

def update_video_queue():
    """อัปเดตคิววิดีโอจาก Storage และ Firestore"""
    global video_queue
    try:
        video_queue.clear()
        
        # ดึงข้อมูลจาก collection videos ใน Firestore
        video_docs = db.collection("videos").order_by("timestamp", direction=firestore.Query.DESCENDING).stream()
        
        for doc in video_docs:
            try:
                video_data = doc.to_dict()
                file_name = video_data.get("fileName", "")
                
                # ดึงข้อมูล blob จาก Storage
                blob = bucket.blob(f"videos/{file_name}")
                
                if blob.exists():
                    signed_url = blob.generate_signed_url(
                        version="v4",
                        expiration=3600,  # 1 ชั่วโมง
                        method="GET"
                    )
                    
                    video_queue.append({
                        'url': signed_url,
                        'name': file_name,
                        'timestamp': video_data.get("timestamp"),
                        'updated': blob.updated
                    })
                
            except Exception as e:
                logger.error(f"Error processing video document {doc.id}: {e}")

        logger.info(f"Updated video queue with {len(video_queue)} videos")
        return True

    except Exception as e:
        logger.error(f"Error updating video queue: {e}")
        return False

def get_next_video():
    """ดึงวิดีโอถัดไปจากคิว"""
    global current_video_index, video_queue

    if current_video_index >= len(video_queue) or not video_queue:
        update_video_queue()
        current_video_index = 0

    if not video_queue:
        return None

    video = video_queue[current_video_index]
    current_video_index += 1
    return video

def fetch_data_from_firestore():
    """ดึงข้อมูลจาก Firestore และประมวลผล โดยใช้ timestamp จาก videos"""
    try:
        # ดึงข้อมูลจาก videos collection (ล่าสุด 10 รายการ)
        video_docs = list(db.collection("videos")
                         .order_by("timestamp", direction=firestore.Query.DESCENDING)
                         .limit(10).stream())
        
        # ดึงข้อมูลจาก moverate และ people_counter เพื่อนำมาเชื่อมกับข้อมูล videos
        moverate_docs = list(db.collection("moverate").stream())
        people_docs = list(db.collection("people_counter").stream())

        data = []
        for video_doc in video_docs:
            try:
                video_data = video_doc.to_dict()
                video_ts = video_data.get("timestamp")
                
                if not video_ts:
                    continue
                
                # แปลง timestamp ของ video เป็น datetime object สำหรับการเปรียบเทียบ
                video_dt = parser.parse(video_ts) if isinstance(video_ts, str) else video_ts
                
                # หา moverate ที่ใกล้เคียงที่สุดกับ timestamp ของ video
                matched_move = None
                min_move_diff = float('inf')
                for move_doc in moverate_docs:
                    move_data = move_doc.to_dict()
                    move_ts = move_data.get("timestamp")
                    if move_ts:
                        m_dt = parser.parse(move_ts) if isinstance(move_ts, str) else move_ts
                        diff = abs((video_dt - m_dt).total_seconds())
                        if diff < min_move_diff:
                            min_move_diff = diff
                            matched_move = move_data
                
                # หา people_counter ที่ใกล้เคียงที่สุดกับ timestamp ของ video
                matched_people = None
                min_people_diff = float('inf')
                for pdoc in people_docs:
                    pdata = pdoc.to_dict()
                    pts = pdata.get("timestamp")
                    if pts:
                        p_dt = parser.parse(pts) if isinstance(pts, str) else pts
                        diff = abs((video_dt - p_dt).total_seconds())
                        if diff < min_people_diff:
                            min_people_diff = diff
                            matched_people = pdata
                
                # เพิ่มข้อมูลลงในรายการผลลัพธ์
                data.append({
                    "timestamp": format_timestamp(video_ts),
                    "num_people": matched_people.get("total_count", 0) if matched_people else 0,
                    "move_rate": matched_move.get("overall_level", "Unknown") if matched_move else "Unknown",
                    "raw_timestamp": video_ts,
                    "video_name": video_data.get("fileName", "")
                })
                
            except Exception as e:
                logger.error(f"Error processing video doc {video_doc.id}: {e}")

        # เรียงข้อมูลตามเวลา (เก่าสุดไปใหม่สุด)
        data.sort(key=lambda x: x.get("raw_timestamp", ""))
        return data

    except Exception as e:
        logger.error(f"Error in fetch_data_from_firestore: {e}")
        return []

@app.route('/')
def index():
    """หน้าแดชบอร์ดหลัก"""
    data = fetch_data_from_firestore()
    video = get_next_video()
    return render_template('index.html', data=data, current_video=video)

@app.route('/next_video')
def next_video():
    """ดึงวิดีโอต่อไป (API)"""
    video = get_next_video()
    if video:
        return jsonify({
            "video_url": video['url'],
            "video_name": video['name'],
            "timestamp": format_timestamp(video['timestamp']),
            "updated": video['updated'].isoformat() if hasattr(video['updated'], 'isoformat') else str(video['updated'])
        })
    return jsonify({"error": "No videos available"}), 404

@app.route('/plot_move_rate')
def plot_move_rate():
    """สร้างกราฟระดับการเคลื่อนไหว"""
    data = fetch_data_from_firestore()
    if not data:
        return "No data available", 404

    df = pd.DataFrame(data)
    mapping = {"Very Low": 0, "Low": 1, "Medium": 2, "High": 3, "Very High": 4}
    df["move_rate_value"] = df["move_rate"].map(mapping)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(df["timestamp"], df["move_rate_value"], marker='o')
    ax.set_title("Movement Rate Over Time")
    ax.set_xlabel("Time")
    ax.set_ylabel("Movement Level")
    ax.set_yticks(list(mapping.values()))
    ax.set_yticklabels(list(mapping.keys()))
    plt.xticks(rotation=45)
    fig.tight_layout()

    buf = BytesIO()
    fig.savefig(buf, format='png')
    buf.seek(0)
    return Response(buf.getvalue(), mimetype='image/png')

@app.route('/plot_people_count')
def plot_people_count():
    """สร้างกราฟจำนวนคน"""
    data = fetch_data_from_firestore()
    if not data:
        return "No data available", 404

    df = pd.DataFrame(data)
    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(df["timestamp"], df["num_people"])
    for bar in bars:
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height(),
                f'{int(bar.get_height())}', ha='center', va='bottom')
    ax.set_title("People Count Over Time")
    ax.set_xlabel("Time")
    ax.set_ylabel("Number of People")
    plt.xticks(rotation=45)
    fig.tight_layout()

    buf = BytesIO()
    fig.savefig(buf, format='png')
    buf.seek(0)
    return Response(buf.getvalue(), mimetype='image/png')

if __name__ == "__main__":
    # อัปเดตคิววิดีโอครั้งแรกก่อนเริ่มเซิร์ฟเวอร์
    update_video_queue()
    app.run(host="0.0.0.0", port=5000, debug=True)