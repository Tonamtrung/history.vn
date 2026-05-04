import os
import json
import random
import time
import threading
import PyPDF2
import logging
from flask import Flask, render_template, jsonify, request

# Thêm thư viện này để đọc file .env ở máy cá nhân
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

# --- CẤU HÌNH LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CẤU HÌNH ĐƯỜNG DẪN ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QUIZ_FILE = os.path.join(BASE_DIR, "kho_de_lich_su.json")
PDF_PATH = os.path.join(BASE_DIR, "lich_su.pdf")

# Biến lưu trữ tạm thời các câu đang được người dùng làm (để check đáp án)
# Trong thực tế sản phẩm lớn nên dùng Redis hoặc Session, nhưng với lớp học thì dùng biến global này là đủ.
current_session_questions = []

# --- LẤY API KEYS ---
keys_from_env = [os.environ.get(f"GROQ_KEY_{i}") for i in range(1, 11) if os.environ.get(f"GROQ_KEY_{i}")]
GROQ_API_KEYS = keys_from_env
if not GROQ_API_KEYS:
    # Nếu không có env, Trung có thể dán tạm key vào đây để test local
    GROQ_API_KEYS = ["GÁN_KEY_CỦA_TRUNG_VÀO_ĐÂY_NẾU_CHƯA_CÓ_ENV"]

from groq import Groq
MODEL_ID = "llama-3.3-70b-versatile" 
progress = {"current": 0, "total": 0, "percent": 0, "is_done": False}

def extract_text_from_pdf(pdf_path):
    text = ""
    try:
        if not os.path.exists(pdf_path):
            logger.error(f"❌ File không tồn tại: {pdf_path}")
            return None
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text
        return text if text.strip() else None
    except Exception as e:
        logger.error(f"❌ Lỗi đọc PDF: {e}")
        return None

def fetch_and_save_quiz(content, total_target=100):
    global progress
    all_questions = []
    batch_size = 10 
    steps = total_target // batch_size
    progress.update({"total": steps, "current": 0, "percent": 0, "is_done": False})

    for current_step in range(steps):
        try:
            random_key = random.choice(GROQ_API_KEYS)
            current_client = Groq(api_key=random_key)
            start_idx = random.randint(0, max(0, len(content) - 6000))
            sub_content = content[start_idx : start_idx + 6000]
            
            prompt = (f"Dựa vào nội dung lịch sử 12: {sub_content}. "
                      f"Tạo {batch_size} câu hỏi trắc nghiệm. "
                      f"Trả về JSON: 'questions': [{{question, options, answer}}]")
            
            chat_completion = current_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=MODEL_ID,
                temperature=0.7,
                response_format={"type": "json_object"}
            )
            
            data = json.loads(chat_completion.choices[0].message.content)
            batch_data = data.get('questions', [])
            
            if batch_data:
                all_questions.extend(batch_data)
                with open(QUIZ_FILE, "w", encoding="utf-8") as f:
                    json.dump(all_questions, f, ensure_ascii=False, indent=4)
                
                progress["current"] = current_step + 1
                progress["percent"] = int(((current_step + 1) / steps) * 100)
            
            time.sleep(2) 
        except Exception as e:
            logger.warning(f"⚠️ Lỗi đợt {current_step+1}: {e}")
            time.sleep(4)
            
    progress["is_done"] = True

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_status')
def get_status():
    return jsonify(progress)

@app.route('/get_questions')
def get_questions():
    global current_session_questions
    should_generate = False
    
    if not os.path.exists(QUIZ_FILE) or os.stat(QUIZ_FILE).st_size == 0:
        should_generate = True
    else:
        try:
            with open(QUIZ_FILE, "r", encoding="utf-8") as f:
                current_data = json.load(f)
                if len(current_data) < 5: should_generate = True
        except: should_generate = True

    if should_generate:
        content = extract_text_from_pdf(PDF_PATH)
        if content:
            if progress["current"] == 0 or progress["is_done"]:
                threading.Thread(target=fetch_and_save_quiz, args=(content,)).start()
            return jsonify({"is_generating": True})
        return jsonify({"error": "Lỗi file PDF"}), 400

    try:
        with open(QUIZ_FILE, "r", encoding="utf-8") as f:
            all_q = json.load(f)
        
        random.shuffle(all_q)
        selected = all_q[:20] # Lấy 20 câu
        
        # LƯU VÀO SESSION TẠM THỜI (để server biết đáp án)
        current_session_questions = selected 
        
        # XÓA ĐÁP ÁN TRƯỚC KHI GỬI XUỐNG CLIENT (Bảo mật)
        client_data = []
        for q in selected:
            client_data.append({
                "question": q["question"],
                "options": q["options"]
            })
            
        return jsonify(client_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/check_answer', methods=['POST'])
def check_answer():
    global current_session_questions
    data = request.json
    try:
        idx = data.get('question_idx')
        user_ans = data.get('answer', '').strip()
        
        if not current_session_questions or idx >= len(current_session_questions):
            return jsonify({"error": "Hết hạn phiên làm bài"}), 400
            
        correct_ans = current_session_questions[idx]['answer'].strip()
        
        # So sánh (Trung có thể thêm hàm normalize nếu muốn chính xác tuyệt đối)
        is_correct = (user_ans.lower() == correct_ans.lower())
        
        return jsonify({
            "is_correct": is_correct,
            "correct_answer": correct_ans
        })
    except Exception as e:
        return jsonify({"error": "Lỗi xử lý"}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)