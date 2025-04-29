import cv2
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import firebase_admin
from firebase_admin import credentials, storage, firestore
import os
import subprocess

# ✅ ฟังก์ชัน Remux วิดีโอด้วย ffmpeg
def fix_mp4_metadata(input_file, output_file):
    ffmpeg_path = r"C:\ffmpeg\ffmpeg.exe"  # เปลี่ยนเป็น path ffmpeg ของคุณ
    subprocess.run([
        ffmpeg_path, "-i", input_file,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        output_file
    ])

# ✅ ตั้งค่า Firebase
cred = credentials.Certificate("smart-class-e9661-firebase-adminsdk-fbsvc-bf137255f5.json")
firebase_admin.initialize_app(cred, {
    'storageBucket': 'smart-class-e9661.firebasestorage.app'
})
db = firestore.client()
bucket = storage.bucket()

os.makedirs("videos", exist_ok=True)

# ✅ ฟังก์ชันบันทึกวิดีโอจาก RTSP
def record_rtsp_video(rtsp_url, duration_sec=60, output_path="output.mp4"):
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        print("❌ ไม่สามารถเชื่อมต่อกล้องได้")
        return None

    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    width = int(cap.get(3))
    height = int(cap.get(4))
    out = cv2.VideoWriter(output_path, fourcc, 20.0, (width, height))

    start_time = time.time()
    while time.time() - start_time < duration_sec:
        ret, frame = cap.read()
        if not ret:
            break
        out.write(frame)

    cap.release()
    out.release()
    return output_path

# ✅ ฟังก์ชันอัปโหลดวิดีโอไป Firebase
def upload_to_firebase(file_path):
    filename = os.path.basename(file_path)
    blob = bucket.blob(f"videos/{filename}")
    blob.upload_from_filename(file_path)
    blob.make_public()

    # เพิ่มลิงก์เข้า Firestore
    db.collection("videos").add({
        "fileName": filename,
        "url": blob.public_url,
        "timestamp": datetime.utcnow()
    })

    print(f"✅ อัปโหลดเสร็จแล้ว: {blob.public_url}")

# ✅ เรียกใช้ทุก 5 นาที
rtsp_url = "rtsp://smartclass:112233@192.168.1.118:554/stream1"

while True:
    timestamp = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d_%H%M%S")
    raw_file = f"videos/camera_{timestamp}_raw.mp4"
    fixed_file = f"videos/camera_{timestamp}.mp4"

    print("🔴 เริ่มบันทึกวิดีโอ...")
    result = record_rtsp_video(rtsp_url, duration_sec=60, output_path=raw_file)

    if result is None or not os.path.exists(raw_file):
        print(f"❌ ไม่พบไฟล์ raw video: {raw_file}, ข้ามรอบนี้")
        time.sleep(300)
        continue

    print("🛠️ กำลัง Remux วิดีโอ...")
    fix_mp4_metadata(raw_file, fixed_file)

    if not os.path.exists(fixed_file):
        print(f"❌ Remux ล้มเหลว, ไม่พบไฟล์: {fixed_file}, ข้ามการอัปโหลด")
        time.sleep(300)
        continue

    print("⬆️ กำลังอัปโหลดวิดีโอไป Firebase...")
    upload_to_firebase(fixed_file)

    # 🧹 ลบไฟล์ local หลังอัปโหลด
    for f in [raw_file, fixed_file]:
        if os.path.exists(f):
            os.remove(f)
            print(f"🧹 ลบไฟล์ local แล้ว: {f}")

    # พัก 5 นาที
    time.sleep(300)

