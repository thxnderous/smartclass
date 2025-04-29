import time
import subprocess

def run_script(script_name):
    print(f"🔄 Running {script_name}...")
    result = subprocess.run(["python3", script_name])
    print(f"✅ {script_name} finished with code {result.returncode}\n")

if __name__ == "__main__":
    while True:
        run_script("uploadclip.py")        # 1. บันทึกและอัปโหลดวิดีโอ
        run_script("counter_people.py")    # 2. นับจำนวนคนจากวิดีโอที่อัปโหลด
        run_script("test2.py")          # 3. วิเคราะห์การเคลื่อนไหว

        print("⏳ รอ 5 นาทีเพื่อประมวลผลรอบถัดไป...\n")
        time.sleep(300)  # 5 นาที
