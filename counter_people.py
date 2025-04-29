import cv2
from ultralytics import YOLO
from datetime import datetime
from zoneinfo import ZoneInfo
import firebase_admin
from firebase_admin import credentials, firestore, storage
import subprocess
import os

# ===== Config =====
timestamp = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d_%H%M%S")
VIDEO_PATH = "latest_video.mp4"
MODEL_PATH = "yolov8n.pt"
OUTPUT_FILENAME = f"{timestamp}.mp4"
OUTPUT_PATH = OUTPUT_FILENAME
CONF_THRESHOLD = 0.5
FIREBASE_CRED_PATH = "smart-class-e9661-firebase-adminsdk-fbsvc-bf137255f5.json"
FIREBASE_BUCKET = "smart-class-e9661.firebasestorage.app"

# ===== Firebase Init =====
print("Initializing Firebase...")
cred = credentials.Certificate(FIREBASE_CRED_PATH)
firebase_admin.initialize_app(cred, {'storageBucket': FIREBASE_BUCKET})
db = firestore.client()
bucket = storage.bucket()
print("Firebase initialized.")

# ===== Find New Video File =====
print("Looking for new video file...")

# ดึงรายการที่เคยใช้ไปแล้ว
used_video_names = set(
    doc.to_dict().get("video_name")
    for doc in db.collection("people_counter").stream()
)

# ดึง blob ทั้งหมดในโฟลเดอร์ "videos/"
blobs = list(bucket.list_blobs(prefix="videos/"))
new_blob = None
for blob in sorted(blobs, key=lambda b: b.updated):
    if blob.name.endswith(".mp4") and blob.name not in used_video_names:
        new_blob = blob
        break

if not new_blob:
    print("❌ No new video found to process.")
    exit()

print(f"✅ New video found: {new_blob.name}")
new_blob.download_to_filename(VIDEO_PATH)

# ===== Load Model =====
print("Loading YOLOv8 model...")
model = YOLO(MODEL_PATH)
print("Model loaded.")

# ===== Video Setup =====
cap = cv2.VideoCapture(VIDEO_PATH)
if not cap.isOpened():
    raise RuntimeError(f"Cannot open video: {VIDEO_PATH}")
ret, frame = cap.read()
if not ret:
    raise RuntimeError("Cannot read video frames")
frame_h, frame_w = frame.shape[:2]
fps = cap.get(cv2.CAP_PROP_FPS) or 30
fourcc = cv2.VideoWriter_fourcc(*"mp4v")
out = cv2.VideoWriter(OUTPUT_PATH, fourcc, fps, (frame_w, frame_h))
print(f"Writing output to {OUTPUT_PATH}: {frame_w}x{frame_h} @ {fps:.1f} FPS")

# ===== Line Position =====
LINE_START = (0, frame_h - 50)
LINE_END   = (frame_w - 1450, frame_h - 300)
line_y = (LINE_START[1] + LINE_END[1]) / 2

prev_positions = {}
in_count = 0
out_count = 0
frame_idx = 0
total_count = 0

def get_time_str():
    return datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d %H:%M:%S")

print(f"Counting line: {LINE_START} -> {LINE_END}, line_y={line_y}")
print("Starting processing...")

# ===== Frame Loop =====
while True:
    ret, frame = cap.read()
    if not ret:
        break
    frame_idx += 1
    results = model.track(frame, persist=True, classes=[0], conf=CONF_THRESHOLD, verbose=False)[0]
    boxes = results.boxes.xyxy.cpu().numpy()

    if results.boxes.id is None:
        out.write(frame)
        continue

    ids = results.boxes.id.cpu().numpy()
    print(f"\nFrame {frame_idx}: Detected IDs {ids.tolist()}")

    for box, tid in zip(boxes, ids):
        x1, y1, x2, y2 = map(int, box)
        cx = int((x1 + x2) / 2)
        cy = int((y1 + y2) / 2)

        if tid not in prev_positions:
            prev_positions[tid] = (cx, cy)
            continue

        px, py = prev_positions[tid]
        if py < line_y <= cy:
            out_count += 1
        elif py >= line_y > cy:
            in_count += 1

        total_count = in_count - out_count
        prev_positions[tid] = (cx, cy)

        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 87, 212), 2)
        cv2.circle(frame, (cx, cy), 4, (0, 0, 255), -1)
        cv2.putText(frame, f"ID{int(tid)}", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (78, 151, 255), 2)

    cv2.line(frame, LINE_START, LINE_END, (250, 192, 23), 6)
    cv2.putText(frame, f"In: {in_count}", (1600, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
    cv2.putText(frame, f"Out: {out_count}", (1600, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
    cv2.putText(frame, f"Total_count: {total_count}", (1600, 110), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 0), 2)
    out.write(frame)

cap.release()
out.release()
print(f"Finished. Total In={in_count}, Out={out_count}, Total_count={total_count}")

# ===== Convert to H.264 MP4 =====
converted_filename = f"Room901_{OUTPUT_FILENAME}"
subprocess.run([
    "C:/ffmpeg/ffmpeg.exe", "-i", OUTPUT_FILENAME, "-c:v", "libx264",
    "-preset", "fast", "-movflags", "+faststart", "-y", converted_filename
])

# ===== Upload to Firebase =====
time_now = datetime.now(ZoneInfo("Asia/Bangkok"))
blob = bucket.blob(f"counter_videos/{converted_filename}")
blob.upload_from_filename(converted_filename, content_type='video/mp4')
blob.make_public()
video_url = blob.public_url
print(f"Uploaded to Firebase: {video_url}")

# ===== Save Metadata to Firestore =====
db.collection("people_counter").document(timestamp).set({
    "timestamp": time_now.isoformat(),
    "in": in_count,
    "out": out_count,
    "total_count": total_count,
    "video_name": new_blob.name,
    "video_url": video_url
})
print("Uploaded counts to Firestore.")

# ===== Cleanup =====
for f in [VIDEO_PATH, OUTPUT_FILENAME, converted_filename]:
    if os.path.exists(f):
        os.remove(f)
        print(f"Removed file: {f}")
