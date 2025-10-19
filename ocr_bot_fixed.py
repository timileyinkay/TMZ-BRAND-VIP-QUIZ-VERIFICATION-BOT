# ocr_bot_fixed.py
import sqlite3
import time
import random
import re
from datetime import datetime
import pytesseract
from PIL import Image, ImageEnhance
import io
import os
from dotenv import load_dotenv
from flask import Flask 
import secrets

app = Flask(__name__)
# Load environment variables
load_dotenv()

print("🤖 Starting Opay Payment Bot with OCR...")

# Configuration from .env file
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
OPAY_ACCOUNT = os.getenv('OPAY_ACCOUNT_NUMBER')
RECEIVER_NAME = os.getenv('RECEIVER_NAME')
TIMEOUT_MINUTES = int(os.getenv('PAYMENT_TIMEOUT_MINUTES', 20))
ADMIN_ID = int(os.getenv('ADMIN_ID'))
# Enforced base amount (only this amount will be accepted)
BASE_AMOUNT = int(os.getenv('BASE_AMOUNT', 2000))
# Optional TMZ brand fee to display (does NOT change required payment amount)
TMZ_BRAND_FEE_NAIRA = int(os.getenv('TMZ_BRAND_FEE_NAIRA', 0))
# VIP group/chat where verified users will join. Must be set to the target chat id (as int)
VIP_CHAT_ID = int(os.getenv('VIP_CHAT_ID', '0')) if os.getenv('VIP_CHAT_ID') else None

# Safety check: ensure your bot token exists
if not TOKEN:
    print("❌ Missing TELEGRAM_BOT_TOKEN in .env file")
    exit(1)

# Tesseract OCR Configuration
tesseract_path = os.getenv('TESSERACT_PATH')
if tesseract_path and os.path.exists(tesseract_path):
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
    TESSERACT_AVAILABLE = True
    print(f"✅ Tesseract configured: {tesseract_path}")
else:
    # Try common installation paths
    common_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    
    for path in common_paths:
        if os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            TESSERACT_AVAILABLE = True
            print(f"✅ Tesseract found at: {path}")
            break
    else:
        TESSERACT_AVAILABLE = False
        print("❌ Tesseract not found. OCR will not work.")

# Database setup
DATABASE_NAME = os.getenv('DATABASE_NAME', 'opay_payments.db')
conn = sqlite3.connect(DATABASE_NAME, check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS pending_payments
             (ref TEXT PRIMARY KEY, user_id INTEGER, amount INTEGER, 
              created_at REAL, expiry_at REAL)''')
c.execute('''CREATE TABLE IF NOT EXISTS verified_payments
             (ref TEXT PRIMARY KEY, user_id INTEGER, amount INTEGER, 
              verified_at REAL, user_name TEXT)''')
# Table to store one-time join tokens per user
c.execute('''CREATE TABLE IF NOT EXISTS join_tokens
             (token TEXT PRIMARY KEY, user_id INTEGER, created_at REAL, expiry_at REAL, used INTEGER)''')
conn.commit()

def generate_reference():
    """Generate unique reference like tmzbrand123456"""
    return f"tmzbrand{random.randint(100000, 999999)}"

def cleanup_expired_payments():
    """Clean up expired payments from database"""
    current_time = time.time()
    c.execute("DELETE FROM pending_payments WHERE expiry_at < ?", (current_time,))
    conn.commit()

def cleanup_expired_tokens():
    """Remove expired tokens from DB"""
    current_time = time.time()
    c.execute("DELETE FROM join_tokens WHERE expiry_at < ?", (current_time,))
    conn.commit()

def extract_text_from_image(image_data):
    """Extract text from image using OCR with better configuration for financial receipts"""
    try:
        if not TESSERACT_AVAILABLE:
            return None
            
        # Open image from bytes
        image = Image.open(io.BytesIO(image_data))
        
        # Enhanced image preprocessing for better OCR
        image = image.convert('L')  # Convert to grayscale
        
        # Increase contrast
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(2.0)  # Increase contrast
        
        # Use Tesseract with optimized configuration for receipts
        custom_config = r'--oem 3 --psm 6'
        extracted_text = pytesseract.image_to_string(image, config=custom_config)
        
        print("📸 OCR Text Extracted Successfully")
        print(f"🔍 Raw OCR Text:\n{extracted_text}")
        return extracted_text
    except Exception as e:
        print(f"❌ OCR Error: {e}")
        return None

def extract_amount_from_text(extracted_text, expected_amount):
    """Extract payment amount from OCR text - FIXED VERSION"""
    if not extracted_text:
        return None
    
    print(f"🔍 Searching for amount in receipt. Expected: ₦{expected_amount}")
    
    # Debug: Show all numbers found
    all_numbers_debug = re.findall(r'\b[0-9,.]+\b', extracted_text)
    print(f"🔢 All numbers found: {all_numbers_debug}")
    
    # Convert to uppercase for easier matching
    text_upper = extracted_text.upper()
    lines = extracted_text.split('\n')
    
    # STRATEGY 1: Look for the main transaction amount (usually at top with 2 decimal places)
    for i, line in enumerate(lines):
        clean_line = line.strip()
        
        # Skip obvious date lines
        if any(date_word in clean_line.upper() for date_word in ['OCT', 'NOV', 'DEC', 'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', '2025', '2024', '2026']):
            continue
            
        # Look for lines that contain numbers with 2 decimal places (money format)
        decimal_matches = re.findall(r'[0-9,]+\.?[0-9]{2}', clean_line)
        for match in decimal_matches:
            try:
                amount = float(match.replace(',', ''))
                # Valid amount range and not a date
                if 50 <= amount <= 1000000 and amount != 2025.0 and amount != 2024.0 and amount != 2026.0:
                    print(f"💰 Decimal amount found: ₦{amount}")
                    return amount
            except ValueError:
                continue
        
        # Look for standalone numbers that could be amounts
        if re.match(r'^\s*[0-9,]+\s*$', clean_line):
            try:
                amount = float(clean_line.replace(',', ''))
                # Check if it's a reasonable amount (not a phone number, date, etc.)
                if 50 <= amount <= 1000000 and amount != 2025:
                    print(f"💰 Standalone number as amount: ₦{amount}")
                    return amount
            except ValueError:
                pass
    
    # STRATEGY 2: Look near "Successful Transaction" text
    for i, line in enumerate(lines):
        if 'SUCCESSFUL' in line.upper() or 'TRANSACTION' in line.upper():
            # Check 2 lines before this line (where amount usually is)
            for j in range(max(0, i-2), i):
                check_line = lines[j].strip()
                # Look for numbers with decimals
                decimal_matches = re.findall(r'[0-9,]+\.?[0-9]{0,2}', check_line)
                for match in decimal_matches:
                    try:
                        amount = float(match.replace(',', ''))
                        if 50 <= amount <= 1000000 and amount != 2025:
                            print(f"💰 Amount near 'Successful': ₦{amount}")
                            return amount
                    except ValueError:
                        continue
    
    # STRATEGY 3: Find all valid amounts and pick the most reasonable one
    all_numbers = re.findall(r'\b[0-9]{1,6}(?:,[0-9]{3})*(?:\.[0-9]{0,2})?\b', extracted_text)
    valid_amounts = []
    
    for num_str in all_numbers:
        try:
            amount = float(num_str.replace(',', ''))
            # Filter out dates, phone numbers, and unreasonable amounts
            if 50 <= amount <= 1000000 and amount != 2025 and amount != 2024 and amount != 2026:
                # Exclude numbers that look like phone numbers or IDs
                if amount != 8079304530 and amount != 9077430:  # Example phone numbers
                    valid_amounts.append(amount)
        except ValueError:
            continue
    
    if valid_amounts:
        # If we have expected amount, find closest match
        if expected_amount:
            closest_amount = min(valid_amounts, key=lambda x: abs(x - expected_amount))
            print(f"💰 Closest amount to expected: ₦{closest_amount}")
            return closest_amount
        else:
            # Otherwise take the largest reasonable number
            largest_amount = max(valid_amounts)
            print(f"💰 Largest reasonable amount: ₦{largest_amount}")
            return largest_amount
    
    # STRATEGY 4: Manual pattern matching for common receipt formats
    # Look for pattern like: "##.##" at the beginning of lines
    for i, line in enumerate(lines):
        if i < 5:  # Only check first 5 lines (where amount usually is)
            amount_match = re.search(r'^\s*([0-9,]+\.?[0-9]{0,2})\s*$', line.strip())
            if amount_match:
                try:
                    amount = float(amount_match.group(1).replace(',', ''))
                    if 50 <= amount <= 1000000:
                        print(f"💰 Amount in header line: ₦{amount}")
                        return amount
                except ValueError:
                    pass
    
    print("❌ No valid amount found in receipt")
    return None

def start(update, context):
    """Handle /start command"""
    user_id = update.effective_user.id
    user_name = user_name = "TMZ BRAND VIP"
    print(f"User {user_id} ({user_name}) started the bot")
    
    welcome_text = f"""
🤖 TMZBRAND VIP Payment Verification Bot  

🎉 Welcome to **TMZ BRAND VIP**, {user_name}! 🚀  
'Where you face your fears, test your mind, and prove your worth 🧠 🏆  

How to join the VIP Room:  
1️⃣ Use /pay 2000 to create your VIP payment request.  
2️⃣ Send the exact amount to our official Opay account.  
3️⃣ Include your unique reference in the remark field.  
4️⃣ Upload your payment receipt (screenshot) for instant verification.  

⏰ Verification Window: {TIMEOUT_MINUTES} minutes  

Commands:
/pay <amount> - Create VIP payment request  
/check - Check pending payment  
/history - View payment history  
/help - Show help message  

Example: /pay 2000  

⚡ Once verified, you'll gain access to the **TMZBRAND VIP Quiz Room** — where smart minds win big and legends are made! 💰🚀"""

    update.message.reply_text(welcome_text)

def generate_join_token_for_user(user_id, ttl_minutes=60):
    """Create a one-time token for a user valid for ttl_minutes"""
    token = secrets.token_urlsafe(9)  # reasonably short but random
    now = time.time()
    expiry = now + (ttl_minutes * 60)
    c.execute("INSERT OR REPLACE INTO join_tokens VALUES (?,?,?,?,?)", (token, user_id, now, expiry, 0))
    conn.commit()
    return token, expiry

def get_invite_link(bot):
    """Return an invite link to the VIP group. Requires bot admin in that group."""
    if not VIP_CHAT_ID:
        return None
    try:
        # export_chat_invite_link returns a permanent link
        link = bot.export_chat_invite_link(VIP_CHAT_ID)
        return link
    except Exception as e:
        print(f"❌ Could not create invite link: {e}")
        return None

def handle_token_dm(update, context):
    """Handle private messages where user redeems their join token"""
    if update.message.chat.type != 'private':
        return
    text = (update.message.text or '').strip()
    if not text:
        return

    # Check token in DB
    c.execute("SELECT user_id, used, expiry_at FROM join_tokens WHERE token=?", (text,))
    row = c.fetchone()
    if not row:
        update.message.reply_text("❌ Invalid token. Please ensure you entered the token provided after payment.")
        return

    token_user_id, used, expiry_at = row
    if time.time() > expiry_at:
        update.message.reply_text("❌ This token has expired. Please request a new one after payment.")
        return

    if token_user_id != update.effective_user.id:
        update.message.reply_text("❌ This token is not assigned to your account.")
        return

    if used:
        update.message.reply_text("❌ This token has already been used.")
        return

    # Validate and use token: create single-use invite and return link
    invite = validate_and_use_token(text, update.effective_user.id, context.bot)
    if invite:
        update.message.reply_text(f"✅ Token accepted. Here is your single-use invite link (expires soon): {invite}")
    else:
        update.message.reply_text("❌ Token accepted but could not generate an invite link. Please contact admin.")

def redeem_command(update, context):
    """Handle /redeem <token> command"""
    if not context.args:
        update.message.reply_text("Usage: /redeem <your-token-here>")
        return
    token = context.args[0].strip()
    invite = validate_and_use_token(token, update.effective_user.id, context.bot)
    if invite:
        update.message.reply_text(f"✅ Token accepted. Here is your single-use invite link (expires soon): {invite}")
    else:
        update.message.reply_text("❌ Invalid/expired/used token or failed to create invite. Contact admin.")

def validate_and_use_token(token, user_id, bot, invite_ttl_seconds=600):
    """Validate token ownership/expiry/used flag; mark used and create a single-use invite link."""
    cleanup_expired_tokens()
    c.execute("SELECT user_id, used, expiry_at FROM join_tokens WHERE token=?", (token,))
    row = c.fetchone()
    if not row:
        return None
    token_user_id, used, expiry_at = row
    if time.time() > expiry_at:
        return None
    if used:
        return None
    if token_user_id != user_id:
        return None

    # Create a single-use invite link for this user (do this BEFORE marking token used)
    if not VIP_CHAT_ID:
        return None
    try:
        expire_date = int(time.time()) + invite_ttl_seconds
        # Prefer create_chat_invite_link with member_limit=1 (if supported by bot/API)
        try:
            link_obj = bot.create_chat_invite_link(chat_id=VIP_CHAT_ID, expire_date=expire_date, member_limit=1)
            link = link_obj.invite_link if hasattr(link_obj, 'invite_link') else link_obj
        except Exception:
            # Fallback to export_chat_invite_link (may not support single-use)
            link = bot.export_chat_invite_link(VIP_CHAT_ID)

        # If invite was created successfully, mark token used
        if link:
            try:
                c.execute("UPDATE join_tokens SET used=1 WHERE token=?", (token,))
                conn.commit()
            except Exception as db_e:
                print(f"❌ Failed to mark token used in DB: {db_e}")
                # Don't return None here; still return link so user can join

        return link
    except Exception as e:
        print(f"❌ Error creating single-use invite: {e}")
        return None

def handle_new_members(update, context):
    """When new members join the VIP group, ensure they have a used token; otherwise remove them and instruct how to get one."""
    chat = update.effective_chat
    if not VIP_CHAT_ID or chat.id != VIP_CHAT_ID:
        return

    for member in update.message.new_chat_members:
        # Skip bots
        if member.is_bot:
            continue

        # Check if user has a used token
        c.execute("SELECT token FROM join_tokens WHERE user_id=? AND used=1", (member.id,))
        row = c.fetchone()
        if row:
            # Welcome message
            context.bot.send_message(chat.id, f"✅ Welcome {member.full_name}! Your token has been verified.")
            continue

        # No valid token: kick then unban to allow rejoin after getting token
        try:
            context.bot.send_message(member.id, "You must verify ownership by sending your personal TMZ secret token to me in this chat before you can join the VIP group. Use /pay to create a payment request if you haven't paid yet.")
        except Exception:
            # Could not DM the user
            pass

        try:
            context.bot.kick_chat_member(chat.id, member.id)
            # unban to allow rejoin
            context.bot.unban_chat_member(chat.id, member.id)
            context.bot.send_message(chat.id, f"⛔ {member.full_name} was removed — please DM the bot your token to get a rejoin link.")
        except Exception as e:
            print(f"❌ Could not remove unverified member: {e}")

def pay(update, context):
    """Handle /pay command"""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    # Clean up expired payments first
    cleanup_expired_payments()
    
    # Enforce a single allowed payment amount (BASE_AMOUNT)
    if not context.args:
        # If no argument provided, instruct the user to use the exact command
        update.message.reply_text(
            f"❌ Usage: /pay {BASE_AMOUNT}\n\n"
            f"Only ₦{BASE_AMOUNT:,} is accepted."
        )
        return

    # Validate provided amount strictly
    try:
        amount = int(context.args[0])
    except ValueError:
        update.message.reply_text("❌ Please provide a valid number (e.g. /pay 2000)")
        return

    # If user typed an amount other than the enforced BASE_AMOUNT, reject
    if amount != BASE_AMOUNT:
        fee_msg = f"\nTMZ BRAND FEE: ₦{TMZ_BRAND_FEE_NAIRA:,}" if TMZ_BRAND_FEE_NAIRA else ""
        update.message.reply_text(
            f"❌ Only ₦{BASE_AMOUNT:,} is accepted for this service.\n"
            f"You attempted: ₦{amount:,}.\n\n"
            f"Please run: /pay {BASE_AMOUNT} to create your payment request.{fee_msg}"
        )
        return
    
    # Check if user has existing pending payment
    c.execute("SELECT ref, amount FROM pending_payments WHERE user_id=?", (user_id,))
    existing = c.fetchone()
    if existing:
        ref_existing, amount_existing = existing
        update.message.reply_text(
            f"⚠️ You already have a pending payment:\n"
            f"💰 Amount: ₦{amount_existing:,}\n"
            f"🔑 Reference: {ref_existing}\n\n"
            f"Use /check to view details or wait for it to expire."
        )
        return
    
    # Use the enforced amount
    amount = BASE_AMOUNT

    # Generate unique reference
    ref = generate_reference()

    # Calculate timestamps
    created_at = time.time()
    expiry_at = created_at + (TIMEOUT_MINUTES * 60)
    
    # Save to database
    c.execute("INSERT INTO pending_payments VALUES (?,?,?,?,?)", 
              (ref, user_id, amount, created_at, expiry_at))
    conn.commit()
    
    # Format times for display
    created_time = datetime.fromtimestamp(created_at).strftime("%H:%M:%S")
    expiry_time = datetime.fromtimestamp(expiry_at).strftime("%H:%M:%S")
    
    fee_section = f"\nTMZ BRAND FEE: ₦{TMZ_BRAND_FEE_NAIRA:,}\n" if TMZ_BRAND_FEE_NAIRA else ""

    instructions = f"""

🏷️ Requested by: TMZ BRAND VIP 🎯  
💰 Amount: ₦{amount:,}  
🔑 Reference: {ref}  
⏰ Time Window: {TIMEOUT_MINUTES} minutes  
🕐 Created: {created_time}  
🕒 Expires: {expiry_time}  

📲 Please send the exact amount to our official Opay account and upload your receipt for verification.  
⚡ Be quick — the request will expire once the timer runs out!  

---

PAYMENT INSTRUCTIONS:

1️⃣ Send exactly ₦{amount:,} to:
   💳 {OPAY_ACCOUNT}

2️⃣ Receiver Name must be:
   👤 {RECEIVER_NAME}

3️⃣ Include this EXACT reference in Remark:
   🏷️ {ref}

4️⃣ Upload receipt SCREENSHOT within {TIMEOUT_MINUTES} minutes

📸 How to take good screenshot:
• Ensure text is clear and readable
• Include amount, receiver name, and reference
• Make sure transaction shows "Successful"
• Capture the entire receipt

⚠️ Important:
• Amount must be exact: ₦{amount:,}
• Reference must match exactly: {ref}
• Receipt must show timestamp
• Transaction must be successful

🔍 Use /check to monitor your payment status
    """
    # Append fee info if present (fee is informational only; required payment remains BASE_AMOUNT)
    if TMZ_BRAND_FEE_NAIRA:
        instructions += f"\nTMZ BRAND FEE: ₦{TMZ_BRAND_FEE_NAIRA:,} (this is a platform fee)\n"

    update.message.reply_text(instructions)
    print(f"Payment request created: User {user_id}, Amount {amount}, Ref {ref}")

def check(update, context):
    """Handle /check command"""
    user_id = update.effective_user.id
    
    # Clean up expired payments first
    cleanup_expired_payments()
    
    c.execute("SELECT ref, amount, created_at, expiry_at FROM pending_payments WHERE user_id=? ORDER BY created_at DESC LIMIT 1", 
              (user_id,))
    row = c.fetchone()
    
    if not row:
        update.message.reply_text("📭 No pending payments found. Use /pay <amount> to create one.")
        return
    
    ref, amount, created_at, expiry_at = row
    now = time.time()
    
    if now > expiry_at:
        c.execute("DELETE FROM pending_payments WHERE ref=?", (ref,))
        conn.commit()
        update.message.reply_text("⏰ Payment request expired. Use /pay <amount> to create a new one.")
        return
    
    time_left = int(expiry_at - now)
    minutes_left = time_left // 60
    seconds_left = time_left % 60
    
    created_time = datetime.fromtimestamp(created_at).strftime("%H:%M:%S")
    expiry_time = datetime.fromtimestamp(expiry_at).strftime("%H:%M:%S")
    
    status = f"""
📋 PENDING PAYMENT

💰 Amount: ₦{amount:,}
🔑 Reference: {ref}
⏰ Time Left: {minutes_left}m {seconds_left}s
🕐 Created: {created_time}
🕒 Expires: {expiry_time}

📸 Upload your receipt SCREENSHOT to verify payment.
🚨 Payment will expire in {minutes_left} minutes {seconds_left} seconds
    """
    
    update.message.reply_text(status)

def history(update, context):
    """Show user's payment history"""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    c.execute("SELECT ref, amount, verified_at FROM verified_payments WHERE user_id=? ORDER BY verified_at DESC LIMIT 10", 
              (user_id,))
    rows = c.fetchall()
    
    if not rows:
        update.message.reply_text("📊 No payment history found.")
        return
    
    history_text = f"""
📊 PAYMENT HISTORY for {user_name}

"""
    
    for ref, amount, verified_at in rows:
        verified_time = datetime.fromtimestamp(verified_at).strftime("%Y-%m-%d %H:%M:%S")
        history_text += f"✅ ₦{amount:,} - {ref}\n"
        history_text += f"   🕐 {verified_time}\n\n"
    
    update.message.reply_text(history_text)

def help_cmd(update, context):
    """Handle /help command"""
    help_text = f"""
ℹ️ HELP - Opay Payment Verification

Available Commands:
/start - Start the bot
/pay 2000 - That is the amount to pay (₦2,000 for this game)
/check - Check pending payment
/history - Show your payment history
/help - Show this message

Payment Process:
1. Use /pay 2000 (for ₦2,000) to create payment request
2. Send exactly #2000 to: {OPAY_ACCOUNT}
3. Receiver: {RECEIVER_NAME}
4. Include reference in Remark field
5. Upload receipt SCREENSHOT for verification

📸 Screenshot Tips:
• Ensure all text is clear and readable
• Include amount, receiver, reference
• Show transaction status "Successful"
• Capture full receipt

Verification Checks:
✅ Exact amount match
✅ Correct receiver name  
✅ Valid reference in remark
✅ Successful transaction status

Need Help?
Ensure screenshot is clear and all details are visible.
    """
    update.message.reply_text(help_text)

def stats(update, context):
    """Admin command to show bot statistics"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        update.message.reply_text("❌ Admin only command.")
        return
    
    # Get statistics
    c.execute("SELECT COUNT(*) FROM pending_payments")
    pending_count = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM verified_payments")
    verified_count = c.fetchone()[0]
    
    c.execute("SELECT SUM(amount) FROM verified_payments")
    total_amount = c.fetchone()[0] or 0
    
    stats_text = f"""
📊 BOT STATISTICS (Admin)

🟡 Pending Payments: {pending_count}
✅ Verified Payments: {verified_count}
💰 Total Processed: ₦{total_amount:,}

💾 Database: {DATABASE_NAME}
⏰ Timeout: {TIMEOUT_MINUTES} minutes
🤖 OCR: {'Enabled' if TESSERACT_AVAILABLE else 'Disabled'}
    """
    
    update.message.reply_text(stats_text)

def handle_image(update, context):
    """Handle receipt image uploads - FIXED AMOUNT DETECTION"""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    print(f"📸 Image received from user {user_id} ({user_name})")
    
    # Clean up expired payments first
    cleanup_expired_payments()
    
    # Get pending payment
    c.execute("SELECT ref, amount, expiry_at FROM pending_payments WHERE user_id=? ORDER BY created_at DESC LIMIT 1", 
              (user_id,))
    row = c.fetchone()
    
    if not row:
        update.message.reply_text("❌ No pending payment found. Use /pay <amount> first.")
        return
    
    ref, expected_amount, expiry_at = row
    
    # Check if expired
    if time.time() > expiry_at:
        c.execute("DELETE FROM pending_payments WHERE ref=?", (ref,))
        conn.commit()
        update.message.reply_text("⏰ Payment request expired. Use /pay <amount> to create a new one.")
        return
    
    # Inform user that processing has started
    processing_msg = update.message.reply_text("🔍 Processing receipt image... Verifying amount and details...")
    
    try:
        # Get the photo file
        photo_file = update.message.photo[-1].get_file()
        image_data = photo_file.download_as_bytearray()
        
        # Extract text from image using OCR
        extracted_text = extract_text_from_image(image_data)
        
        if not extracted_text:
            processing_msg.edit_text("❌ Could not read text from image. Please ensure the screenshot is clear and try again.")
            return
        
        print(f"🔍 Extracted Text:\n{extracted_text}")
        
        # Extract the actual amount from receipt
        amount_found = extract_amount_from_text(extracted_text, expected_amount)
        
        # Check receiver name (more flexible matching)
        receiver_patterns = [
            RECEIVER_NAME.upper(),
            'OLUWATOBILOBA SHERIFDEEN KEHINDE',
            'OLUWATOBILOBA SHERIFDEEN',
            'SHERIFDEEN KEHINDE',
            'OLUWATOBILOBA KEHINDE',
            'OLUWATOBILOBA',
            'SHERIFDEEN',
        ]
        
        receiver_found = False
        for pattern in receiver_patterns:
            if pattern in extracted_text.upper():
                receiver_found = True
                print(f"👤 Receiver found: {pattern}")
                break
        
        # Check reference (flexible matching)
        reference_found = False
        if ref.upper() in extracted_text.upper():
            reference_found = True
            print(f"🔑 Reference found: {ref}")
        else:
            # Try without "tmzbrand" prefix
            ref_number = ref.replace('tmzbrand', '')
            if ref_number in extracted_text:
                reference_found = True
                print(f"🔑 Reference found (number only): {ref_number}")
        
        # Check status (multiple success indicators)
        status_indicators = ['success', 'successful', 'completed', 'approved', 'confirmed']
        status_found = False
        for indicator in status_indicators:
            if indicator in extracted_text.lower():
                status_found = True
                print(f"✅ Status found: {indicator}")
                break
        
        # Validate receipt - STRICT AMOUNT CHECK
        errors = []
        
        if not amount_found:
            errors.append("❌ Could not find payment amount in receipt")
        elif abs(amount_found - expected_amount) > 1:  # Allow 1 naira difference for rounding
            errors.append(f"❌ Amount mismatch!\nExpected: ₦{expected_amount:,}\nFound in receipt: ₦{amount_found:,}")

        if not receiver_found:
            errors.append(f"❌ Receiver name not found or doesn't match.\nExpected: {RECEIVER_NAME}")
        
        if not reference_found:
            errors.append(f"❌ Reference not found in receipt.\nExpected: {ref}")
        
        if not status_found:
            errors.append("❌ Transaction not marked as successful/completed")
        
        # Final validation
        if not errors:
            # Payment approved!
            c.execute("DELETE FROM pending_payments WHERE ref=?", (ref,))
            # Save to verified payments
            c.execute("INSERT INTO verified_payments VALUES (?,?,?,?,?)", 
                     (ref, user_id, expected_amount, time.time(), user_name))
            conn.commit()
            
            success_message = f"""
✅ PAYMENT VERIFIED & APPROVED! 🎉  

👤 User: {user_name}  
💰 Amount: ₦{expected_amount:,} ✅  
🔑 Reference: {ref} ✅  
🕐 Time: {datetime.now().strftime("%H:%M:%S")}  
🎊 Status: Verified successfully!  

Welcome to **TMZ BRAND VIP** — where smart minds face their fears and win big! 🏆🧠 💰  
Thank you for your payment — let the game begin! 🚀🚀

            """
            processing_msg.edit_text(success_message)
            print(f"Payment approved via OCR: User {user_id}, Amount {expected_amount}, Ref {ref}")
            
            # Notify admin
            if ADMIN_ID:
                try:
                    context.bot.send_message(
                        ADMIN_ID,
                        f"💰 New Payment Verified!\n"
                        f"User: {user_name} ({user_id})\n"
                        f"Amount: ₦{expected_amount:,}\n"
                        f"Ref: {ref}"
                    )
                except:
                    pass
                # Generate and send one-time join token to user (DM)
                try:
                    token, expiry = generate_join_token_for_user(user_id, ttl_minutes=60)
                    context.bot.send_message(user_id, f"✅ Payment verified. Your personal TMZ join token: {token}\nThis token is valid for 60 minutes. Use /redeem <token> or DM me the token to receive your single-use invite link.")
                except Exception as e:
                    print(f"❌ Could not send join token DM: {e}")
        else:
            error_message = "❌ PAYMENT VERIFICATION FAILED\n\n" + "\n".join(errors)
            error_message += f"\n\n📋 Expected Details:"
            error_message += f"\n💰 Amount: ₦{expected_amount:,}"
            error_message += f"\n🔑 Reference: {ref}"
            error_message += f"\n👤 Receiver: {RECEIVER_NAME}"
            error_message += f"\n\n💡 Tip: Ensure screenshot shows all details clearly."
            error_message += f"\n🔍 OCR detected amount: ₦{amount_found if amount_found else 'Not found'}"
            
            processing_msg.edit_text(error_message)
            
    except Exception as e:
        processing_msg.edit_text(f"❌ Error processing image: {str(e)}\nPlease try again with a clearer screenshot.")
        print(f"Image processing error: {e}")

def handle_text(update, context):
    """Handle text messages (fallback for text receipts)"""
    user_id = update.effective_user.id
    text = update.message.text
    
    # If it's a command, ignore
    if text.startswith('/'):
        return
        
    update.message.reply_text("📸 Please upload a SCREENSHOT of your receipt for verification.")

def main():
    """Main function with proxy support"""
    try:
        from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
        
        # Try with proxy settings (common free proxies - may need to find working ones)
        proxy_urls = [
            None,  # First try without proxy
            'http://proxy.server:3128',
            'http://185.199.229.156:7492',  # Example proxy, find working ones
        ]
        
        for proxy_url in proxy_urls:
            try:
                if proxy_url:
                    print(f"🔗 Trying with proxy: {proxy_url}")
                    request_kwargs = {
                        'proxy_url': proxy_url,
                        'read_timeout': 20,
                        'connect_timeout': 20
                    }
                else:
                    print("🔗 Trying direct connection...")
                    request_kwargs = {
                        'read_timeout': 20,
                        'connect_timeout': 20
                    }
                
                updater = Updater(
                    TOKEN, 
                    use_context=True,
                    request_kwargs=request_kwargs
                )
                
                # Test connection
                bot_info = updater.bot.get_me()
                print(f"✅ Connected to Telegram as: {bot_info.first_name} (@{bot_info.username})")
                break
                
            except Exception as e:
                print(f"❌ Connection failed: {e}")
                continue
        else:
            print("❌ All connection attempts failed. Please use a VPN.")
            return
        
        dispatcher = updater.dispatcher

        # Add command handlers
        dispatcher.add_handler(CommandHandler("start", start))
        dispatcher.add_handler(CommandHandler("pay", pay))
        dispatcher.add_handler(CommandHandler("check", check))
        dispatcher.add_handler(CommandHandler("history", history))
        dispatcher.add_handler(CommandHandler("help", help_cmd))
        dispatcher.add_handler(CommandHandler("stats", stats))
        dispatcher.add_handler(CommandHandler("redeem", redeem_command))

        # Add message handlers
        dispatcher.add_handler(MessageHandler(Filters.photo, handle_image))
        # Private message handler for token redemption (register before generic text handler)
        dispatcher.add_handler(MessageHandler(Filters.private & Filters.text & ~Filters.command, handle_token_dm))
        dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))
        # New chat members handler to enforce token verification
        dispatcher.add_handler(MessageHandler(Filters.status_update.new_chat_members, handle_new_members))

        # Clean up on startup
        cleanup_expired_payments()
        
        print("🤖 Enhanced Opay Bot with OCR is running successfully!")
        print(f"⏰ Time window: {TIMEOUT_MINUTES} minutes")
        print(f"👤 Receiver: {RECEIVER_NAME}")
        print(f"💳 Account: {OPAY_ACCOUNT}")
        print(f"🔍 OCR: {TESSERACT_AVAILABLE}")
        print("💰 MODE: Strict amount verification")
        print("Press Ctrl+C to stop the bot")
        
        # Start polling
        updater.start_polling()
        updater.idle()
        
    except ImportError as e:
        print(f"❌ Cannot import telegram modules: {e}")
        print("Please install: pip install python-telegram-bot==13.15")
    except Exception as e:
        print(f"❌ Error starting bot: {e}")

if __name__ == '__main__':
    # Import here to avoid circular imports
    from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
    
    # Start bot in a separate thread
    import threading
    bot_thread = threading.Thread(target=main, daemon=True)
    bot_thread.start()
    
    # Start Flask app (required for Render)
    port = int(os.environ.get("PORT", 10000))
    print(f"🌐 Starting Flask web server on port {port}")
    app.run(host="0.0.0.0", port=port)