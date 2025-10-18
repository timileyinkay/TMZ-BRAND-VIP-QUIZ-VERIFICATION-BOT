"""
tmz_full_backend.py
Combined Fintech Payment OCR backend + Safe Spelling Game (points-based)

Usage:
    export FLASK_ENV=development
    export RECEIVER_NAME="Your Name"
    export OPAY_ACCOUNT_NUMBER="1234567890"
    export TIMEOUT_MINUTES=20
    export DATABASE_NAME=tmz_backend.db
    (Optional) export TESSERACT_PATH="/usr/bin/tesseract"

Run:
    python tmz_full_backend.py

Notes:
- This file merges your OCR-based payment verification system with a game system
  that awards points instead of cash. Keep using your existing payment endpoints.
- The spelling game uses server-side sessions stored in memory and also logs results
  to the DB; you can persist sessions into DB if you want long-term recovery.
"""

import os
import io
import re
import time
import random
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from dotenv import load_dotenv

# Optional OCR libs
try:
    from PIL import Image, ImageEnhance
    import pytesseract
    TESSERACT_AVAILABLE = True
except Exception:
    TESSERACT_AVAILABLE = False

# Load .env
load_dotenv()

# App config
APP_NAME = "TMZ Backend (Payments + Spelling Game)"
RECEIVER_NAME = os.getenv("RECEIVER_NAME", "TMZ Receiver")
OPAY_ACCOUNT = os.getenv("OPAY_ACCOUNT_NUMBER", "0000000000")
TIMEOUT_MINUTES = int(os.getenv("TIMEOUT_MINUTES", 20))
DATABASE_NAME = os.getenv("DATABASE_NAME", "tmz_backend.db")

# If user set a tesseract path
tesseract_path = os.getenv("TESSERACT_PATH")
if tesseract_path and TESSERACT_AVAILABLE:
    pytesseract.pytesseract.tesseract_cmd = tesseract_path

# Initialize app and DB connection
app = Flask(__name__)
conn = sqlite3.connect(DATABASE_NAME, check_same_thread=False)
c = conn.cursor()

# -------------------------
# Database: create tables
# -------------------------
c.execute('''
CREATE TABLE IF NOT EXISTS pending_payments (
    ref TEXT PRIMARY KEY,
    user_id TEXT,
    amount INTEGER,
    created_at REAL,
    expiry_at REAL,
    user_name TEXT,
    user_email TEXT
)
''')

c.execute('''
CREATE TABLE IF NOT EXISTS verified_payments (
    ref TEXT PRIMARY KEY,
    user_id TEXT,
    amount INTEGER,
    verified_at REAL,
    user_name TEXT,
    user_email TEXT
)
''')

# Users (points-based)
c.execute('''
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT,
    points INTEGER DEFAULT 0,
    created_at REAL
)
''')

# Games (archive)
c.execute('''
CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    words_count INTEGER,
    correct INTEGER,
    final_multiplier INTEGER,
    points_earned INTEGER,
    created_at REAL
)
''')

conn.commit()

# -------------------------
# Utilities
# -------------------------
def generate_reference():
    return f"fintech{random.randint(100000, 999999)}"

def cleanup_expired_payments():
    now = time.time()
    c.execute("DELETE FROM pending_payments WHERE expiry_at < ?", (now,))
    conn.commit()

def now_ts():
    return time.time()

# -------------------------
# OCR helpers (receipt)
# -------------------------
def extract_text_from_image(image_data):
    """Return extracted text or None; uses PIL + pytesseract when available."""
    if not TESSERACT_AVAILABLE:
        return None
    try:
        image = Image.open(io.BytesIO(image_data))
        image = image.convert('L')
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(2.0)
        custom_config = r'--oem 3 --psm 6'
        text = pytesseract.image_to_string(image, config=custom_config)
        return text
    except Exception as e:
        print("OCR error:", e)
        return None

def extract_amount_from_text(extracted_text, expected_amount=None):
    """Try several heuristics to find amount from OCR text."""
    if not extracted_text:
        return None

    lines = extracted_text.splitlines()
    # Try decimals first
    for line in lines:
        matches = re.findall(r'[0-9,]+\.?[0-9]{0,2}', line)
        for m in matches:
            try:
                amt = float(m.replace(',', ''))
                # Filter unrealistic values (e.g., years/phone numbers)
                if 50 <= amt <= 10_000_000:
                    return amt
            except:
                continue

    # Fallback: find largest reasonable numeric token
    tokens = re.findall(r'\b[0-9]{1,9}(?:,[0-9]{3})*(?:\.[0-9]{0,2})?\b', extracted_text)
    valid = []
    for t in tokens:
        try:
            amt = float(t.replace(',', ''))
            if 50 <= amt <= 10_000_000:
                valid.append(amt)
        except:
            pass
    if valid:
        # if expected_amount given, return closest
        if expected_amount:
            return min(valid, key=lambda x: abs(x - expected_amount))
        return max(valid)
    return None

# -------------------------
# Word bank (sample 200-ish words)
# -------------------------
WORD_LIST = [
    # We'll include a medium-large list (200+ words would be okay; here's a substantial sample)
    "rhythm","receipt","cousin","business","subtle","choir","colonel","biscuit",
    "entrepreneur","queue","debris","gauge","bologna","pneumonia","sovereign",
    "psychology","bureaucracy","onomatopoeia","connoisseur","aesthetic",
    "pharaoh","chauffeur","camouflage","dachshund","aphthous","liaison",
    "fluorescent","eczema","mnemonics","pseudonym","dilettante","synecdoche",
    "aphrodisiac","rendezvous","auspicious","vicissitude","euphemism",
    "paraphernalia","conscientious","surreptitious","miscellaneous","ubiquitous",
    "idiosyncrasy","phenomenon","pronunciation","pronounciation","hierarchy",
    "mischievous","acquiesce","bellwether","camaraderie","capricious","cognizant",
    "conflagration","deleterious","denouement","ephemeral","fortuitous","garrulous",
    "gregarious","harangue","impecunious","inconsequential","insidious","juxtaposition",
    "laconic","loquacious","magnanimous","nefarious","obfuscate","obsequious",
    "paradigm","quixotic","recalcitrant","sagacious","taciturn","ubiquity","vicarious",
    "wane","xenophobia","yen","zephyr","abscond","benevolent","candor","daunting",
    "enervate","flabbergasted","gauche","hone","imbroglio","jejune","knack","lurid",
    "mellifluous","nadir","obstreperous","palimpsest","quandary","reprobate","sinecure",
    "temerity","unctuous","voracious","winsome","yokel","zealous","accolade","blatant",
    "callous","dichotomy","eloquent","fervent","garner","hubris","impetuous","jubilant",
    "kudos","lexicon","meticulous","nonchalant","omniscient","pragmatic","quell","rescind",
    "serendipity","tantamount","uncanny","vestige","wistful","yonder","zelig",
    # Add more as needed by your product; random.sample will pick from this list
]

# -------------------------
# In-memory session store
# -------------------------
# For production you should persist sessions in DB or Redis
GAME_SESSIONS = {}  # session_id -> session dict

# -------------------------
# Payment endpoints (from existing fintech_backend.py)
# -------------------------
@app.route("/")
def home():
    return jsonify({
        "status": "success",
        "message": APP_NAME,
        "version": "1.0",
        "endpoints": {
            "create_payment": "POST /api/payments/create",
            "verify_receipt": "POST /api/payments/verify (multipart/form-data file=receipt_image)",
            "check_status": "GET /api/payments/status/<ref>",
            "user_history": "GET /api/payments/history/<user_id>",
            "game_start": "POST /api/game/start (json user_id,user_name,stake_points)",
            "game_answer": "POST /api/game/answer (json session_id, answer)"
        }
    })

@app.route("/api/payments/create", methods=["POST"])
def create_payment():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data provided"}), 400
        required = ["user_id","amount","user_name"]
        for r in required:
            if r not in data:
                return jsonify({"status":"error","message":f"Missing {r}"}),400
        user_id = data["user_id"]
        user_name = data["user_name"]
        user_email = data.get("user_email","")
        amount = int(data["amount"])
        if amount <= 0:
            return jsonify({"status":"error","message":"Amount must be positive"}),400
        if amount > 1_000_000:
            return jsonify({"status":"error","message":"Max ₦1,000,000"}),400

        cleanup_expired_payments()
        c.execute("SELECT ref, amount FROM pending_payments WHERE user_id=?", (user_id,))
        existing = c.fetchone()
        if existing:
            ref_existing, amount_existing = existing
            return jsonify({
                "status":"error",
                "message":"Existing pending payment",
                "existing_payment": {"reference": ref_existing, "amount": amount_existing}
            }), 409

        ref = generate_reference()
        created_at = time.time()
        expiry_at = created_at + (TIMEOUT_MINUTES * 60)
        c.execute("INSERT INTO pending_payments VALUES (?,?,?,?,?,?,?)",
                  (ref, user_id, amount, created_at, expiry_at, user_name, user_email))
        conn.commit()
        created_time = datetime.fromtimestamp(created_at).strftime("%Y-%m-%d %H:%M:%S")
        expiry_time = datetime.fromtimestamp(expiry_at).strftime("%Y-%m-%d %H:%M:%S")
        return jsonify({
            "status":"success",
            "payment_request": {
                "reference": ref,
                "amount": amount,
                "account_number": OPAY_ACCOUNT,
                "receiver_name": RECEIVER_NAME,
                "created_at": created_time,
                "expires_at": expiry_time,
                "timeout_minutes": TIMEOUT_MINUTES,
                "instructions": f"Send exactly ₦{amount:,} to {OPAY_ACCOUNT} with reference '{ref}' in remark field"
            }
        }), 201
    except Exception as e:
        return jsonify({"status":"error", "message": str(e)}), 500

@app.route("/api/payments/verify", methods=["POST"])
def verify_receipt():
    try:
        if 'receipt_image' not in request.files:
            return jsonify({"status":"error","message":"No receipt image provided"}),400
        image_file = request.files['receipt_image']
        user_id = request.form.get('user_id')
        reference = request.form.get('reference')
        if not user_id or not reference:
            return jsonify({"status":"error","message":"user_id and reference required"}),400

        cleanup_expired_payments()
        c.execute("SELECT ref, amount, expiry_at, user_name FROM pending_payments WHERE user_id=? AND ref=?",
                  (user_id, reference))
        row = c.fetchone()
        if not row:
            return jsonify({"status":"error","message":"No pending payment found"}),404
        ref, expected_amount, expiry_at, user_name = row
        if time.time() > expiry_at:
            c.execute("DELETE FROM pending_payments WHERE ref=?", (ref,))
            conn.commit()
            return jsonify({"status":"error","message":"Payment request expired"}), 410

        image_data = image_file.read()
        extracted_text = extract_text_from_image(image_data) if TESSERACT_AVAILABLE else None
        if extracted_text is None:
            return jsonify({"status":"error","message":"Could not read text from image (OCR unavailable or failed)"}), 400
        amount_found = extract_amount_from_text(extracted_text, expected_amount)
        # Receiver name check (flexible)
        receiver_patterns = [RECEIVER_NAME.upper(), RECEIVER_NAME.split()[0].upper() if RECEIVER_NAME else ""]
        receiver_found = any(pat and pat in extracted_text.upper() for pat in receiver_patterns)
        # Reference check
        reference_found = False
        if ref.upper() in extracted_text.upper():
            reference_found = True
        else:
            ref_num = ref.replace('fintech','')
            if ref_num in extracted_text:
                reference_found = True
        # status indicators
        status_indicators = ['success','successful','completed','approved','confirmed']
        status_found = any(ind in extracted_text.lower() for ind in status_indicators)

        errors = []
        if not amount_found:
            errors.append("Could not find payment amount in receipt")
        elif abs(amount_found - expected_amount) > 1:  # allow small rounding
            errors.append(f"Amount mismatch: Expected ₦{expected_amount:,}, Found ₦{amount_found:,}")
        if not receiver_found:
            errors.append("Receiver name not found in receipt")
        if not reference_found:
            errors.append("Reference not found in receipt")
        if not status_found:
            errors.append("Transaction not clearly marked successful in receipt")

        if errors:
            return jsonify({
                "status":"error",
                "message":"Payment verification failed",
                "errors": errors,
                "ocr_detected_amount": amount_found
            }), 400

        # Success: move to verified
        c.execute("DELETE FROM pending_payments WHERE ref=?", (ref,))
        c.execute("INSERT INTO verified_payments VALUES (?,?,?,?,?,?)",
                  (ref, user_id, expected_amount, time.time(), user_name, ""))
        # Also create user record if not exists (points system)
        c.execute("SELECT id FROM users WHERE id=?", (user_id,))
        if not c.fetchone():
            c.execute("INSERT INTO users (id, name, points, created_at) VALUES (?,?,?,?)",
                      (user_id, user_name, 0, time.time()))
        conn.commit()
        return jsonify({
            "status":"success",
            "message":"Payment verified and approved",
            "verification": {
                "reference": ref,
                "amount": expected_amount,
                "user_name": user_name,
                "verified_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "ocr_detected_amount": amount_found
            }
        }), 200

    except Exception as e:
        return jsonify({"status":"error","message": str(e)}), 500

@app.route("/api/payments/status/<reference>", methods=["GET"])
def check_payment_status(reference):
    try:
        cleanup_expired_payments()
        c.execute("SELECT ref,user_id,amount,created_at,expiry_at,user_name FROM pending_payments WHERE ref=?", (reference,))
        row = c.fetchone()
        if row:
            ref, user_id, amount, created_at, expiry_at, user_name = row
            now = time.time()
            if now > expiry_at:
                c.execute("DELETE FROM pending_payments WHERE ref=?", (ref,))
                conn.commit()
                return jsonify({"status":"expired","message":"Payment request expired"}), 410
            time_left = int(expiry_at - now)
            minutes = time_left // 60
            seconds = time_left % 60
            return jsonify({
                "status":"pending",
                "payment": {
                    "reference": ref,
                    "amount": amount,
                    "user_id": user_id,
                    "user_name": user_name,
                    "created_at": datetime.fromtimestamp(created_at).strftime("%Y-%m-%d %H:%M:%S"),
                    "expires_at": datetime.fromtimestamp(expiry_at).strftime("%Y-%m-%d %H:%M:%S"),
                    "time_left_seconds": time_left,
                    "time_left": f"{minutes}m {seconds}s"
                }
            })
        # verified?
        c.execute("SELECT ref,user_id,amount,verified_at,user_name FROM verified_payments WHERE ref=?", (reference,))
        row = c.fetchone()
        if row:
            ref, user_id, amount, verified_at, user_name = row
            return jsonify({
                "status":"verified",
                "payment": {
                    "reference": ref,
                    "amount": amount,
                    "user_id": user_id,
                    "user_name": user_name,
                    "verified_at": datetime.fromtimestamp(verified_at).strftime("%Y-%m-%d %H:%M:%S")
                }
            })
        return jsonify({"status":"not_found","message":"No payment found with this reference"}),404
    except Exception as e:
        return jsonify({"status":"error","message": str(e)}), 500

@app.route("/api/payments/history/<user_id>", methods=["GET"])
def payment_history(user_id):
    try:
        c.execute("SELECT ref,amount,verified_at FROM verified_payments WHERE user_id=? ORDER BY verified_at DESC LIMIT 50", (user_id,))
        rows = c.fetchall()
        payments = []
        for ref, amount, verified_at in rows:
            payments.append({
                "reference": ref,
                "amount": amount,
                "verified_at": datetime.fromtimestamp(verified_at).strftime("%Y-%m-%d %H:%M:%S"),
                "status":"verified"
            })
        c.execute("SELECT ref,amount,created_at FROM pending_payments WHERE user_id=? ORDER BY created_at DESC LIMIT 10", (user_id,))
        rows = c.fetchall()
        for ref, amount, created_at in rows:
            payments.append({
                "reference": ref,
                "amount": amount,
                "created_at": datetime.fromtimestamp(created_at).strftime("%Y-%m-%d %H:%M:%S"),
                "status":"pending"
            })
        payments.sort(key=lambda x: x.get("verified_at", x.get("created_at","")), reverse=True)
        return jsonify({"status":"success","user_id": user_id,"total_payments":len(payments),"payments":payments})
    except Exception as e:
        return jsonify({"status":"error","message": str(e)}), 500

# -------------------------
# Game endpoints (points-based)
# -------------------------
def get_or_create_user(user_id, user_name="Guest"):
    c.execute("SELECT id FROM users WHERE id=?", (user_id,))
    if not c.fetchone():
        c.execute("INSERT INTO users (id,name,points,created_at) VALUES (?,?,?,?)",
                  (user_id, user_name, 0, time.time()))
        conn.commit()

@app.route("/api/game/start", methods=["POST"])
def game_start():
    """
    Start a 1-player spelling game.
    JSON body:
      { "user_id": "u123", "user_name": "Timmy", "stake_points": 0 }
    This endpoint assumes user already exists in users table (e.g., payment verified created them)
    or it will create the user.
    Returns session_id and first word.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status":"error","message":"JSON body required"}),400
        user_id = data.get("user_id")
        user_name = data.get("user_name","Guest")
        stake_points = int(data.get("stake_points", 0))  # optional, just for display
        if not user_id:
            return jsonify({"status":"error","message":"user_id required"}),400

        get_or_create_user(user_id, user_name)

        # Build session: 5 words by default
        words_count = 5
        # Ensure variety by sampling
        words = random.sample(WORD_LIST, min(words_count, len(WORD_LIST)))
        session_id = f"game{random.randint(100000, 999999)}"
        # Session structure
        session = {
            "id": session_id,
            "user_id": user_id,
            "user_name": user_name,
            "words": words,
            "index": 0,
            "correct": 0,
            "multiplier": 3,   # start multiplier (3x)
            "attempts_left_for_current_word": 3,  # up to 3 attempts total as you described
            "stake_points": stake_points,
            "started_at": time.time()
        }
        GAME_SESSIONS[session_id] = session

        return jsonify({
            "status":"ok",
            "session_id": session_id,
            "message": f"Game started for {user_name}. 5 spellings, 15s per word, 3 attempts total per word.",
            "first_word": words[0]
        })
    except Exception as e:
        return jsonify({"status":"error","message": str(e)}),500

@app.route("/api/game/answer", methods=["POST"])
def game_answer():
    """
    Submit an answer for a session.
    JSON:
      {"session_id":"game123", "answer":"aphthous"}
    Returns next word or final result.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status":"error","message":"JSON body required"}),400
        session_id = data.get("session_id")
        answer = data.get("answer","").strip().lower()
        if not session_id:
            return jsonify({"status":"error","message":"session_id required"}),400
        session = GAME_SESSIONS.get(session_id)
        if not session:
            return jsonify({"status":"error","message":"Session not found"}),404

        # Simple time check: allow 15s per word from when session started/word presented
        # For a stateless client we would provide timestamps; here we keep it simple.
        # You can extend by storing `word_started_at` per session.
        WORD_TIME_LIMIT = 15  # seconds (you can tune)
        now = time.time()
        # For simplicity we consider game-wide time (not per-word) - but you can change
        # We'll enforce per-word timer by storing last_word_ts
        if "last_word_ts" not in session:
            session["last_word_ts"] = session["started_at"]
        # If too late, treat as wrong and decrement multiplier/attempts
        if now - session["last_word_ts"] > WORD_TIME_LIMIT:
            # timeout: count as wrong attempt
            session["attempts_left_for_current_word"] -= 1
            timed_out = True
        else:
            timed_out = False

        # Current correct spelling
        current_word = session["words"][session["index"]].lower()

        if not timed_out and answer == current_word:
            # Correct
            session["correct"] += 1
            session["index"] += 1
            # Reset attempts for next word
            session["attempts_left_for_current_word"] = 3
            session["last_word_ts"] = time.time()
            # Check end
            if session["index"] >= len(session["words"]):
                # End game - compute points
                score = session["correct"]
                final_mult = session["multiplier"]
                # Points formula (example): 10 * correct * multiplier
                points_earned = 10 * score * final_mult
                # Persist game
                game_id = session["id"]
                c.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?)",
                          (game_id, session["user_id"], len(session["words"]),
                           score, final_mult, points_earned, time.time()))
                # Update user points
                c.execute("UPDATE users SET points = points + ? WHERE id=?", (points_earned, session["user_id"]))
                conn.commit()
                # Remove session
                GAME_SESSIONS.pop(session_id, None)
                return jsonify({
                    "status":"finished",
                    "correct": score,
                    "final_multiplier": final_mult,
                    "points_earned": points_earned,
                    "message": f"You spelled {score}/{len(session['words'])} correctly. +{points_earned} pts"
                })
            else:
                next_word = session["words"][session["index"]]
                return jsonify({
                    "status":"ok",
                    "result":"correct",
                    "next_word": next_word,
                    "index": session["index"]+1,
                    "multiplier": session["multiplier"]
                })
        else:
            # Wrong answer or timed out
            session["attempts_left_for_current_word"] -= 1
            # Reduce multiplier when they miss (your spec: multiplier decreases when they miss)
            session["multiplier"] = max(1, session["multiplier"] - 1)

            # If attempts remain on this word, allow retry of same word
            if session["attempts_left_for_current_word"] > 0:
                session["last_word_ts"] = time.time()  # reset timer to let them try again (optional)
                return jsonify({
                    "status":"ok",
                    "result":"wrong",
                    "message":"Wrong or timed out. Attempts left on this word: {}".format(session["attempts_left_for_current_word"]),
                    "multiplier": session["multiplier"]
                })
            else:
                # No attempts left for current word -> move to next word but multiplier already reduced
                session["index"] += 1
                session["attempts_left_for_current_word"] = 3
                session["last_word_ts"] = time.time()
                # Check if game finished
                if session["index"] >= len(session["words"]):
                    score = session["correct"]
                    final_mult = session["multiplier"]
                    points_earned = 10 * score * final_mult
                    game_id = session["id"]
                    c.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?)",
                              (game_id, session["user_id"], len(session["words"]),
                               score, final_mult, points_earned, time.time()))
                    c.execute("UPDATE users SET points = points + ? WHERE id=?", (points_earned, session["user_id"]))
                    conn.commit()
                    GAME_SESSIONS.pop(session_id, None)
                    return jsonify({
                        "status":"finished",
                        "correct": score,
                        "final_multiplier": final_mult,
                        "points_earned": points_earned,
                        "message": f"Game over. You spelled {score}/{len(session['words'])} correctly. +{points_earned} pts"
                    })
                else:
                    # send next word
                    next_word = session["words"][session["index"]]
                    return jsonify({
                        "status":"ok",
                        "result":"moved_on",
                        "next_word": next_word,
                        "index": session["index"]+1,
                        "multiplier": session["multiplier"]
                    })

    except Exception as e:
        return jsonify({"status":"error","message": str(e)}),500

@app.route("/api/users/<user_id>/stats", methods=["GET"])
def user_stats(user_id):
    try:
        c.execute("SELECT name,points FROM users WHERE id=?", (user_id,))
        row = c.fetchone()
        if not row:
            return jsonify({"status":"error","message":"User not found"}),404
        name, pts = row
        c.execute("SELECT COUNT(*), SUM(correct) FROM games WHERE user_id=?", (user_id,))
        total_games, total_correct = c.fetchone()
        return jsonify({
            "status":"success",
            "user": name,
            "points": pts,
            "games_played": total_games or 0,
            "total_correct": total_correct or 0
        })
    except Exception as e:
        return jsonify({"status":"error","message": str(e)}),500

# -------------------------
# Admin endpoints (simple)
# -------------------------
@app.route("/api/admin/system", methods=["GET"])
def admin_system():
    try:
        c.execute("SELECT COUNT(*) FROM pending_payments")
        pending = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM verified_payments")
        verified = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users")
        users_count = c.fetchone()[0]
        c.execute("SELECT SUM(points) FROM users")
        total_points = c.fetchone()[0] or 0
        return jsonify({
            "status":"success",
            "system": {
                "database": DATABASE_NAME,
                "ocr_available": TESSERACT_AVAILABLE,
                "timeout_minutes": TIMEOUT_MINUTES,
                "receiver_account": OPAY_ACCOUNT,
                "receiver_name": RECEIVER_NAME
            },
            "statistics": {
                "pending_payments": pending,
                "verified_payments": verified,
                "users": users_count,
                "total_points": total_points
            }
        })
    except Exception as e:
        return jsonify({"status":"error","message": str(e)}),500

# -------------------------
# Run server
# -------------------------
if __name__ == "__main__":
    cleanup_expired_payments()
    print("Starting TMZ Backend")
    print(f"DB: {DATABASE_NAME} | OCR available: {TESSERACT_AVAILABLE}")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
