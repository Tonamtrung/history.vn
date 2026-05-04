import os
import json
import random
import time
import threading
import PyPDF2
import logging
from flask import Flask, render_template, jsonify, request, session

# Đọc file .env nếu có
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
app.secret_key = 'trung_phu_xuyen_2026'

# --- CẤU HÌNH ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QUIZ_FILE = os.path.join(BASE_DIR, "kho_de_lich_su.json")
PDF_PATH = os.path.join(BASE_DIR, "lich_su.pdf")

# LẤY API KEYS
GROQ_API_KEYS = [os.environ.get(f"GROQ_KEY_{i}") for i in range(1, 11) if os.environ.get(f"GROQ_KEY_{i}")]
from groq import Groq
MODEL_ID = "llama-3.3-70b-versatile" 
progress = {"current": 0, "total": 0, "percent": 0, "is_done": False}

def extract_text_from_pdf(pdf_path):
    text = ""
    try:
        if not os.path.exists(pdf_path): return None
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                t = page.extract_text()
                if t: text += t
        return text if text.strip() else None
    except Exception as e:
        logger.error(f"Lỗi đọc PDF: {e}")
        return None

def fetch_and_save_quiz(content, total_target=100):
    global progress
    batch_size = 10 
    steps = total_target // batch_size
    progress.update({"total": steps, "current": 0, "percent": 0, "is_done": False})

    for current_step in range(steps):
        try:
            existing_data = []
            if os.path.exists(QUIZ_FILE):
                with open(QUIZ_FILE, "r", encoding="utf-8") as f:
                    try: existing_data = json.load(f)
                    except: existing_data = []

            random_key = random.choice(GROQ_API_KEYS)
            client = Groq(api_key=random_key)
            start_idx = random.randint(0, max(0, len(content) - 6000))
            sub_content = content[start_idx : start_idx + 6000]
            
            # PROMPT: Ép AI tạo nội dung có dấu và phân bổ dạng câu hỏi
            prompt = (f"Nội dung gốc: {sub_content}\n\n"
                      f"Nhiệm vụ: Tạo {batch_size} câu hỏi trắc nghiệm Lịch sử 12 TIẾNG VIỆT CÓ DẤU.\n"
                      f"1. 70% câu hỏi 4 lựa chọn, 30% câu hỏi Đúng/Sai.\n"
                      f"2. Đáp án đúng ('answer') phải là nội dung chữ của đáp án đó.\n"
                      f"Trả về JSON: {{'questions': [{{'question', 'options', 'answer'}}]}}")
            
            chat = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=MODEL_ID,
                response_format={"type": "json_object"}
            )
            
            data = json.loads(chat.choices[0].message.content)
            batch = data.get('questions', [])
            
            final_batch = []
            for q in batch:
                options = q.get('options', [])
                # Chỉ xử lý câu có 2 hoặc 4 đáp án
                if len(options) in [2, 4]:
                    # XÁO TRỘN ĐÁP ÁN: Đảm bảo đáp án đúng không bị cố định ở vị trí A
                    random.shuffle(options)
                    q['options'] = options
                    final_batch.append(q)
            
            if final_batch:
                existing_data.extend(final_batch)
                with open(QUIZ_FILE, "w", encoding="utf-8") as f:
                    json.dump(existing_data, f, ensure_ascii=False, indent=4)
                progress["current"] = current_step + 1
                progress["percent"] = int(((current_step + 1) / steps) * 100)
            time.sleep(1) 
        except Exception as e:
            logger.warning(f"Lỗi batch {current_step}: {e}")
            time.sleep(2)
    progress["is_done"] = True

@app.route('/')
def index(): return render_template('index.html')

@app.route('/get_status')
def get_status(): return jsonify(progress)

@app.route('/get_questions')
def get_questions():
    if not os.path.exists(QUIZ_FILE) or os.stat(QUIZ_FILE).st_size < 10:
        content = extract_text_from_pdf(PDF_PATH)
        if content:
            if progress["current"] == 0 or progress["is_done"]:
                threading.Thread(target=fetch_and_save_quiz, args=(content,)).start()
            return jsonify({"is_generating": True})
        return jsonify({"error": "Lỗi file PDF"}), 400

    try:
        with open(QUIZ_FILE, "r", encoding="utf-8") as f:
            all_q = json.load(f)
        
        clean_questions = [q for q in all_q if len(q.get('options', [])) in [2, 4]]
        
        if not clean_questions:
            return jsonify({"is_generating": True})

        selected = random.sample(clean_questions, min(len(clean_questions), 20))
        session['quiz_data'] = selected
        
        return jsonify([{"question": q["question"], "options": q["options"]} for q in selected])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/check_answer', methods=['POST'])
def check_answer():
    data = request.json
    idx = data.get('question_idx')
    user_ans = data.get('answer', '').strip()
    
    quiz_data = session.get('quiz_data', [])
    if not quiz_data or idx >= len(quiz_data):
        return jsonify({"error": "Hết hạn"}), 400
        
    question_info = quiz_data[idx]
    correct_ans = str(question_info['answer']).strip()

    return jsonify({
        "is_correct": (user_ans == correct_ans),
        "correct_answer": correct_ans
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)