import cv2
import numpy as np
import firebase_admin
from firebase_admin import credentials, firestore, storage
from datetime import datetime
import traceback
import os
import uuid
import tempfile
import time

# 1. Initialize Firebase
def initialize_firebase():
    try:
        if not firebase_admin._apps:
            cred_path = 'smart-class-e9661-firebase-adminsdk-fbsvc-bf137255f5.json'
            if not os.path.exists(cred_path):
                raise FileNotFoundError("Credentials file not found")
            
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred, {
                'storageBucket': 'smart-class-e9661.firebasestorage.app'  # <<== ตรงนี้แก้จาก .app เป็น .com
            })
            
            db = firestore.client()
            print("✅ Firebase connection successful")
            return db
    except Exception as e:
        print(f"❌ Firebase connection error: {str(e)}")
        traceback.print_exc()
        return None

# 2. Download video from Firebase Storage
def download_video_from_storage(storage_path):
    try:
        bucket = storage.bucket()
        blob = bucket.blob(storage_path)
        if not blob.exists():
            print(f"❌ Video not found in storage: {storage_path}")
            return None
        
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
        blob.download_to_filename(temp_file.name)
        print(f"✅ Downloaded video to: {temp_file.name}")
        return temp_file.name
    except Exception as e:
        print(f"❌ Error downloading video: {str(e)}")
        traceback.print_exc()
        return None

# 3. Analyze video
def analyze_video(video_path):
    if not os.path.exists(video_path):
        print(f"❌ Video file not found: {video_path}")
        return None

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print("❌ Could not open video file")
        return None

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = int(cap.get(cv2.CAP_PROP_FPS))
    total_parts = 10
    frames_per_part = max(1, frame_count // total_parts)
    scale = 0.35
    alpha = 0.7

    ret, prev_frame = cap.read()
    if not ret:
        print("❌ Could not read video frames")
        cap.release()
        return None

    prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
    prev_gray = cv2.resize(prev_gray, None, fx=scale, fy=scale)

    part_scores = []
    movement_scores = []
    overall_sum = 0
    prev_movement_score = 0
    current_frame = 0

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=scale, fy=scale)

        flow = cv2.calcOpticalFlowFarneback(
            prev_gray, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0
        )
        magnitude = np.sqrt(flow[...,0]**2 + flow[...,1]**2)
        current_score = np.mean(magnitude)

        if current_score == 0 or np.isnan(current_score):
            current_score = prev_movement_score
        else:
            current_score = alpha * prev_movement_score + (1-alpha) * current_score

        overall_sum += current_score
        movement_scores.append(current_score)
        prev_movement_score = current_score

        if current_frame % frames_per_part == 0 and current_frame > 0:
            part_avg = np.mean(movement_scores[-frames_per_part:])
            part_scores.append(part_avg)

        prev_gray = gray
        current_frame += 1

    cap.release()
    # cv2.destroyAllWindows()

    overall_avg = overall_sum / frame_count if frame_count > 0 else 0
    while len(part_scores) < 10:
        part_scores.append(0)

    return {
        'overall': overall_avg,
        'parts': part_scores[:10],
        'frame_count': frame_count
    }

# 4. Save to Firestore
def save_to_firestore(db, video_path, results):
    try:
        def get_level(score):
            if score < 0.5: return "Very Low"
            elif score < 1.0: return "Low"
            elif score < 1.5: return "Medium"
            elif score < 2.0: return "High"
            else: return "Very High"

        document_id = f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:8]}"
        
        data = {
            'video_path': video_path,
            'overall_score': float(round(results['overall'], 2)),
            'overall_level': get_level(results['overall']),
            'timestamp': firestore.SERVER_TIMESTAMP,
            'frame_count': results['frame_count'],
            'analysis_id': document_id
        }

        doc_ref = db.collection('moverate').document(document_id)
        doc_ref.set(data)
        
        print("✅ Data saved to Firestore!")
        return True

    except Exception as e:
        print(f"❌ Failed to save data: {str(e)}")
        traceback.print_exc()
        return False

# 5. Main loop
def main():
    print("\n" + "="*50)
    print("📹  Video Monitoring and Analysis System")
    print("="*50 + "\n")

    db = initialize_firebase()
    if not db:
        return

    processed_videos = set()

    while True:
        try:
            print("🔄 Checking for new videos...")
            bucket = storage.bucket()
            blobs = bucket.list_blobs(prefix="videos/")

            for blob in blobs:
                if not blob.name.endswith(".mp4"):
                    continue
                if blob.name in processed_videos:
                    continue

                print(f"\n📥 New video found: {blob.name}")
                downloaded_path = download_video_from_storage(blob.name)
                if not downloaded_path:
                    continue

                print("🔍 Analyzing video...")
                results = analyze_video(downloaded_path)
                if not results:
                    continue

                print("💾 Saving results...")
                success = save_to_firestore(db, blob.name, results)
                if success:
                    processed_videos.add(blob.name)
                    os.remove(downloaded_path)

            time.sleep(15)  # รอ 15 วินาทีแล้วค่อยตรวจอีกครั้ง

        except KeyboardInterrupt:
            print("\n🛑 Process interrupted by user.")
            break
        except Exception as e:
            print(f"❌ Unexpected error: {str(e)}")
            traceback.print_exc()
            time.sleep(10)

if __name__ == "__main__":
    main()
