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
from flask import Flask, request, jsonify

app = Flask(__name__)
# Load environment variables
load_dotenv()

print("🤖 Starting TMZ BRAND VIP Payment Bot with OCR...")

# Configuration from .env file
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
OPAY_ACCOUNT = os.getenv('OPAY_ACCOUNT_NUMBER')
RECEIVER_NAME = os.getenv('RECEIVER_NAME')
TIMEOUT_MINUTES = int(os.getenv('PAYMENT_TIMEOUT_MINUTES', 20))
ADMIN_ID = int(os.getenv('ADMIN_ID'))
# Initial base amount (will be dynamic)
BASE_AMOUNT = int(os.getenv('BASE_AMOUNT', 2000))
# Optional TMZ brand fee to display (does NOT change required payment amount)
TMZ_BRAND_FEE_NAIRA = int(os.getenv('TMZ_BRAND_FEE_NAIRA', 0))
# Group ID for the private VIP group (REQUIRED - get this from @RawDataBot)
GROUP_ID = os.getenv('GROUP_ID')  # Add this to your .env file

# Safety check: ensure your bot token exists
if not TOKEN:
    print("❌ Missing TELEGRAM_BOT_TOKEN in .env file")
    exit(1)

if not GROUP_ID:
    print("❌ Missing GROUP_ID in .env file")
    print("💡 Get your group ID by adding @RawDataBot to your group and checking the 'chat_id' field")
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

# Enhanced database setup with schema updates
def setup_database():
    """Setup database with all required tables and columns"""
    # Check if pending_payments has the new columns
    c.execute("PRAGMA table_info(pending_payments)")
    columns = [column[1] for column in c.fetchall()]
    
    if 'sender_name' not in columns:
        print("🔄 Updating database schema...")
        # Create new table with all columns
        c.execute('''CREATE TABLE IF NOT EXISTS pending_payments_new
                     (ref TEXT PRIMARY KEY, user_id INTEGER, amount INTEGER, 
                      created_at REAL, expiry_at REAL, sender_name TEXT, 
                      account_name TEXT, payment_platform TEXT)''')
        
        # Copy existing data
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pending_payments'")
        if c.fetchone():
            c.execute("INSERT INTO pending_payments_new (ref, user_id, amount, created_at, expiry_at, sender_name, account_name, payment_platform) SELECT ref, user_id, amount, created_at, expiry_at, 'Unknown', 'Unknown', 'Unknown' FROM pending_payments")
            c.execute("DROP TABLE pending_payments")
        
        c.execute("ALTER TABLE pending_payments_new RENAME TO pending_payments")
        print("✅ Updated pending_payments table")
    
    # Check if verified_payments has the new columns
    c.execute("PRAGMA table_info(verified_payments)")
    columns = [column[1] for column in c.fetchall()]
    
    if 'sender_name' not in columns:
        print("🔄 Updating verified_payments schema...")
        # Create new table with all columns
        c.execute('''CREATE TABLE IF NOT EXISTS verified_payments_new
                     (ref TEXT PRIMARY KEY, user_id INTEGER, amount INTEGER, 
                      verified_at REAL, user_name TEXT, sender_name TEXT,
                      account_name TEXT, payment_platform TEXT)''')
        
        # Copy existing data
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='verified_payments'")
        if c.fetchone():
            c.execute("INSERT INTO verified_payments_new (ref, user_id, amount, verified_at, user_name, sender_name, account_name, payment_platform) SELECT ref, user_id, amount, verified_at, user_name, 'Unknown', 'Unknown', 'Unknown' FROM verified_payments")
            c.execute("DROP TABLE verified_payments")
        
        c.execute("ALTER TABLE verified_payments_new RENAME TO verified_payments")
        print("✅ Updated verified_payments table")
    
    # Create join_requests table to track join requests
    c.execute('''CREATE TABLE IF NOT EXISTS join_requests
                 (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
                  request_time REAL, status TEXT, processed_by TEXT, 
                  processed_time REAL)''')
    
    # Create other tables if they don't exist
    c.execute('''CREATE TABLE IF NOT EXISTS admin_settings
                 (id INTEGER PRIMARY KEY, base_amount INTEGER, 
                  updated_at REAL, updated_by INTEGER)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS user_profiles
                 (user_id INTEGER PRIMARY KEY, real_name TEXT,
                  created_at REAL, last_updated REAL)''')
    
    conn.commit()

# Initialize database
setup_database()

# Initialize admin settings if not exists
c.execute("SELECT COUNT(*) FROM admin_settings WHERE id=1")
if c.fetchone()[0] == 0:
    c.execute("INSERT INTO admin_settings (id, base_amount, updated_at, updated_by) VALUES (1, ?, ?, ?)",
              (BASE_AMOUNT, time.time(), ADMIN_ID))
    conn.commit()

def get_current_base_amount():
    """Get current base amount from database"""
    c.execute("SELECT base_amount FROM admin_settings WHERE id=1")
    result = c.fetchone()
    return result[0] if result else BASE_AMOUNT

def update_base_amount(new_amount, admin_id):
    """Update base amount in database"""
    c.execute("UPDATE admin_settings SET base_amount=?, updated_at=?, updated_by=? WHERE id=1",
              (new_amount, time.time(), admin_id))
    conn.commit()
    return True

def save_user_profile(user_id, real_name):
    """Save or update user profile"""
    c.execute('''INSERT OR REPLACE INTO user_profiles 
                 (user_id, real_name, created_at, last_updated) 
                 VALUES (?, ?, COALESCE((SELECT created_at FROM user_profiles WHERE user_id=?), ?), ?)''',
              (user_id, real_name, user_id, time.time(), time.time()))
    conn.commit()

def get_user_profile(user_id):
    """Get user profile"""
    c.execute("SELECT real_name FROM user_profiles WHERE user_id=?", (user_id,))
    result = c.fetchone()
    return result[0] if result else None

def generate_reference():
    """Generate unique reference like tmzbrand123456"""
    return f"tmzbrand{random.randint(100000, 999999)}"

def cleanup_expired_payments():
    """Clean up expired payments from database"""
    current_time = time.time()
    c.execute("DELETE FROM pending_payments WHERE expiry_at < ?", (current_time,))
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
    """Extract payment amount from OCR text - UPDATED FOR BOTH OPAY & PALMPAY"""
    if not extracted_text:
        return None
    
    print(f"🔍 Searching for amount in receipt. Expected: ₦{expected_amount}")
    
    # Debug: Show all numbers found
    all_numbers_debug = re.findall(r'\b[0-9,.]+\b', extracted_text)
    print(f"🔢 All numbers found: {all_numbers_debug}")
    
    # Convert to uppercase for easier matching
    text_upper = extracted_text.upper()
    lines = extracted_text.split('\n')
    
    # SPECIAL CASE: Look for PalmPay amount format (centered amount with symbols)
    for i, line in enumerate(lines):
        clean_line = line.strip()
        
        # PalmPay specific pattern: number with .00 surrounded by symbols or spaces
        palmPay_match = re.search(r'[#\s]*([0-9,]+\.?[0-9]{2})[#\s]*', clean_line)
        if palmPay_match:
            try:
                amount = float(palmPay_match.group(1).replace(',', ''))
                if 50 <= amount <= 1000000 and amount != 2025.0 and amount != 2024.0 and amount != 2026.0:
                    print(f"💰 PalmPay formatted amount found: ₦{amount}")
                    return amount
            except ValueError:
                pass
    
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
    user_name = update.effective_user.first_name
    current_amount = get_current_base_amount()
    print(f"User {user_id} ({user_name}) started the bot")
    
    welcome_text = f"""
🤖 TMZ BRAND VIP Payment Verification Bot  

🎉 Welcome to **TMZ BRAND VIP**, {user_name}! 🚀  
Where you face your fears, test your mind, and prove your worth 🧠 🏆  

How to join the PRIVATE VIP Room:  
1️⃣ Use /pay to create your VIP payment request  
2️⃣ Send ₦{current_amount:,} to our official account via **Opay OR PalmPay**  
3️⃣ Include your unique reference in the remark field  
4️⃣ Upload your payment receipt (screenshot) for instant verification  
5️⃣ Get **AUTO-APPROVED** for the private group 🔒

⏰ Verification Window: {TIMEOUT_MINUTES} minutes  

Commands:
/pay - Create VIP payment request  
/check - Check pending payment  
/history - View payment history  
/help - Show help message  

⚡ Once verified, you'll be automatically approved for the private VIP group! 💰🚀"""

    update.message.reply_text(welcome_text)

def pay(update, context):
    """Handle /pay command - SIMPLIFIED VERSION"""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    current_amount = get_current_base_amount()
    
    # Clean up expired payments first
    cleanup_expired_payments()
    
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
    
    # Generate unique reference
    ref = generate_reference()

    # Calculate timestamps
    created_at = time.time()
    expiry_at = created_at + (TIMEOUT_MINUTES * 60)
    
    # Save to database with default values for new fields
    c.execute("INSERT INTO pending_payments VALUES (?,?,?,?,?,?,?,?)", 
              (ref, user_id, current_amount, created_at, expiry_at, 
               user_name, user_name, 'Opay/PalmPay'))
    conn.commit()
    
    # Format times for display
    created_time = datetime.fromtimestamp(created_at).strftime("%H:%M:%S")
    expiry_time = datetime.fromtimestamp(expiry_at).strftime("%H:%M:%S")
    
    instructions = f"""
✅ PAYMENT REQUEST CREATED!

🏷️ Requested by: TMZ BRAND VIP 🎯  
💰 Amount: ₦{current_amount:,}  
🔑 Reference: {ref}  
⏰ Time Window: {TIMEOUT_MINUTES} minutes  
🕐 Created: {created_time}  
🕒 Expires: {expiry_time}  

📲 **Send payment via Opay OR PalmPay** and upload your receipt for verification.  
⚡ Be quick — the request will expire once the timer runs out!  

---

PAYMENT INSTRUCTIONS:

1️⃣ Send exactly ₦{current_amount:,} to:
   💳 {OPAY_ACCOUNT} (Opay/PalmPay)

2️⃣ Receiver Name must be:
   👤 {RECEIVER_NAME}

3️⃣ Include this EXACT reference in Remark/Narration:
   🏷️ {ref}

4️⃣ Upload receipt SCREENSHOT within {TIMEOUT_MINUTES} minutes

🎯 **After verification:**
• Get **AUTO-APPROVED** for private group 🔒
• No links shared - complete privacy 🔐
• Direct access to VIP content 🚀

🔍 Use /check to monitor your payment status
    """
    
    if TMZ_BRAND_FEE_NAIRA:
        instructions += f"\nTMZ BRAND FEE: ₦{TMZ_BRAND_FEE_NAIRA:,} (this is a platform fee)\n"

    update.message.reply_text(instructions)
    print(f"Payment request created: User {user_id}, Amount {current_amount}, Ref {ref}")

def check(update, context):
    """Handle /check command"""
    user_id = update.effective_user.id
    
    # Clean up expired payments first
    cleanup_expired_payments()
    
    c.execute("SELECT ref, amount, created_at, expiry_at FROM pending_payments WHERE user_id=? ORDER BY created_at DESC LIMIT 1", 
              (user_id,))
    row = c.fetchone()
    
    if not row:
        update.message.reply_text("📭 No pending payments found. Use /pay to create one.")
        return
    
    ref, amount, created_at, expiry_at = row
    now = time.time()
    
    if now > expiry_at:
        c.execute("DELETE FROM pending_payments WHERE ref=?", (ref,))
        conn.commit()
        update.message.reply_text("⏰ Payment request expired. Use /pay to create a new one.")
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
    current_amount = get_current_base_amount()
    help_text = f"""
ℹ️ HELP - TMZ BRAND VIP Payment Verification

Available Commands:
/start - Start the bot
/pay - Create payment request (₦{current_amount:,} for this game)
/check - Check pending payment
/history - Show your payment history
/help - Show this message

Payment Process:
1. Use /pay to create payment request
2. Send exactly ₦{current_amount:,} to: {OPAY_ACCOUNT}
3. Platform: Opay OR PalmPay
4. Receiver: {RECEIVER_NAME}
5. Include reference in Remark/Narration field
6. Upload receipt SCREENSHOT for verification
7. Get AUTO-APPROVED for private group

📸 Screenshot Tips:
• Ensure all text is clear and readable
• Include amount, receiver, reference
• Show transaction status "Successful"
• Capture full receipt

🎯 After Verification:
• Automatically approved for private group
• No links shared - complete privacy
• Direct access to VIP content

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
    
    c.execute("SELECT COUNT(*) FROM join_requests WHERE status='pending'")
    pending_requests = c.fetchone()[0]
    
    current_amount = get_current_base_amount()
    
    # Get admin settings info
    c.execute("SELECT base_amount, updated_at, updated_by FROM admin_settings WHERE id=1")
    admin_settings = c.fetchone()
    
    if admin_settings:
        base_amount, updated_at, updated_by = admin_settings
        updated_time = datetime.fromtimestamp(updated_at).strftime("%Y-%m-%d %H:%M:%S")
    else:
        base_amount = current_amount
        updated_time = "Never"
        updated_by = "System"
    
    stats_text = f"""
📊 BOT STATISTICS (Admin)

🟡 Pending Payments: {pending_count}
✅ Verified Payments: {verified_count}
📥 Pending Join Requests: {pending_requests}
💰 Total Processed: ₦{total_amount:,}
🎯 Current Price: ₦{current_amount:,}
🕒 Last Price Update: {updated_time}

💾 Database: {DATABASE_NAME}
⏰ Timeout: {TIMEOUT_MINUTES} minutes
🤖 OCR: {'Enabled' if TESSERACT_AVAILABLE else 'Disabled'}
🔒 Security: Auto-approval (no links shared)

Admin Commands:
/setprice <amount> - Change current price
/pricesettings - View price settings
/pendingrequests - View pending join requests
/approve <user_id> - Approve join request
/decline <user_id> - Decline join request
    """
    
    update.message.reply_text(stats_text)

def setprice(update, context):
    """Admin command to change the base amount"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        update.message.reply_text("❌ Admin only command.")
        return
    
    if not context.args:
        update.message.reply_text("❌ Usage: /setprice <amount>\nExample: /setprice 2500")
        return
    
    try:
        new_amount = int(context.args[0])
        if new_amount < 50 or new_amount > 100000:
            update.message.reply_text("❌ Amount must be between ₦50 and ₦100,000")
            return
        
        old_amount = get_current_base_amount()
        success = update_base_amount(new_amount, user_id)
        
        if success:
            update.message.reply_text(
                f"✅ Price updated successfully!\n\n"
                f"📊 Old Price: ₦{old_amount:,}\n"
                f"💰 New Price: ₦{new_amount:,}\n\n"
                f"All new payment requests will use this amount."
            )
            print(f"Admin {user_id} changed price from ₦{old_amount:,} to ₦{new_amount:,}")
        else:
            update.message.reply_text("❌ Failed to update price. Please try again.")
            
    except ValueError:
        update.message.reply_text("❌ Please provide a valid number (e.g. /setprice 2500)")

def pricesettings(update, context):
    """Admin command to view price settings"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        update.message.reply_text("❌ Admin only command.")
        return
    
    current_amount = get_current_base_amount()
    
    # Get admin settings info
    c.execute("SELECT base_amount, updated_at, updated_by FROM admin_settings WHERE id=1")
    admin_settings = c.fetchone()
    
    if admin_settings:
        base_amount, updated_at, updated_by = admin_settings
        updated_time = datetime.fromtimestamp(updated_at).strftime("%Y-%m-%d %H:%M:%S")
        
        settings_text = f"""
💰 PRICE SETTINGS (Admin)

🎯 Current Price: ₦{current_amount:,}
🕒 Last Updated: {updated_time}
👤 Updated By: {updated_by}

Commands:
/setprice <amount> - Change current price
/stats - View full statistics
        """
    else:
        settings_text = "❌ No price settings found."
    
    update.message.reply_text(settings_text)

def send_private_access(update, context, user_name, ref):
    """Send private group access instructions WITHOUT sharing the link"""
    try:
        user_id = update.effective_user.id
        
        # Send success message
        update.message.reply_text(
            f"🎉 PAYMENT VERIFIED! 🎉\n\n"
            f"Welcome to TMZ BRAND VIP, {user_name}! 🚀\n\n"
            f"🔑 Reference: {ref}\n"
            f"✅ Status: Verified\n\n"
            f"🔒 **You now have access to the private VIP group!**\n\n"
            f"To join:\n"
            f"1. Search for the group: **TMZ BRAND VIP**\n"
            f"2. Request to join\n"
            f"3. Your request will be **automatically approved**!\n\n"
            f"🎯 Welcome to the inner circle! 🏆"
        )
        
        # Mark user as verified in database for auto-approval
        c.execute('''INSERT OR REPLACE INTO join_requests 
                    (user_id, username, first_name, request_time, status, processed_by, processed_time) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)''',
                 (user_id, update.effective_user.username, user_name, time.time(), 'pre_approved', 'bot', time.time()))
        conn.commit()
        
        print(f"✅ User {user_id} marked for auto-approval")
        
    except Exception as e:
        print(f"❌ Error sending access instructions: {e}")
        update.message.reply_text("✅ Payment verified! Please contact admin for group access instructions.")

def handle_join_request(update, context):
    """Handle join requests to the group - AUTO APPROVE VERIFIED USERS"""
    try:
        from telegram import ChatJoinRequest
        
        join_request = update.chat_join_request
        user_id = join_request.from_user.id
        username = join_request.from_user.username or "No username"
        first_name = join_request.from_user.first_name or "Unknown"
        chat_id = join_request.chat.id
        
        print(f"📥 Join request from {first_name} (@{username}) - ID: {user_id}")
        
        # Check if user has verified payment OR is pre-approved
        c.execute("SELECT COUNT(*) FROM verified_payments WHERE user_id=?", (user_id,))
        has_verified_payment = c.fetchone()[0] > 0
        
        # Check if user is pre-approved
        c.execute("SELECT status FROM join_requests WHERE user_id=?", (user_id,))
        join_request_data = c.fetchone()
        is_pre_approved = join_request_data and join_request_data[0] == 'pre_approved'
        
        if has_verified_payment or is_pre_approved:
            # Auto-approve if payment is verified or pre-approved
            try:
                context.bot.approve_chat_join_request(chat_id, user_id)
                
                # Update join_requests table
                c.execute('''INSERT OR REPLACE INTO join_requests 
                            (user_id, username, first_name, request_time, status, processed_by, processed_time) 
                            VALUES (?, ?, ?, ?, ?, ?, ?)''',
                         (user_id, username, first_name, time.time(), 'approved', 'bot', time.time()))
                conn.commit()
                
                print(f"✅ Auto-approved join request for {first_name} (verified/pre-approved)")
                
                # Notify user
                try:
                    context.bot.send_message(
                        user_id,
                        f"🎉 Welcome to TMZ BRAND VIP, {first_name}! 🚀\n\n"
                        f"Your join request has been approved automatically!\n"
                        f"You now have access to the private VIP group.\n\n"
                        f"Enjoy the exclusive content! 🏆"
                    )
                except:
                    pass
                    
            except Exception as e:
                print(f"❌ Error approving join request: {e}")
        else:
            # Save as pending for manual review
            c.execute('''INSERT OR REPLACE INTO join_requests 
                        (user_id, username, first_name, request_time, status) 
                        VALUES (?, ?, ?, ?, ?)''',
                     (user_id, username, first_name, time.time(), 'pending'))
            conn.commit()
            
            print(f"📝 Saved pending join request for {first_name} (no verified payment)")
            
            # Notify admin
            if ADMIN_ID:
                try:
                    context.bot.send_message(
                        ADMIN_ID,
                        f"📥 NEW JOIN REQUEST\n\n"
                        f"👤 User: {first_name} (@{username})\n"
                        f"🆔 ID: {user_id}\n"
                        f"💰 Status: No verified payment\n"
                        f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                        f"Commands:\n"
                        f"/approve {user_id} - Approve request\n"
                        f"/decline {user_id} - Decline request\n"
                        f"/pendingrequests - View all pending"
                    )
                except:
                    pass
                    
    except Exception as e:
        print(f"❌ Error handling join request: {e}")

def pending_requests(update, context):
    """Admin command to view pending join requests"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        update.message.reply_text("❌ Admin only command.")
        return
    
    c.execute("SELECT user_id, username, first_name, request_time FROM join_requests WHERE status='pending' ORDER BY request_time")
    rows = c.fetchall()
    
    if not rows:
        update.message.reply_text("📭 No pending join requests.")
        return
    
    requests_text = "📥 PENDING JOIN REQUESTS\n\n"
    
    for user_id, username, first_name, request_time in rows:
        request_date = datetime.fromtimestamp(request_time).strftime("%Y-%m-%d %H:%M:%S")
        requests_text += f"👤 {first_name} (@{username})\n"
        requests_text += f"🆔 ID: {user_id}\n"
        requests_text += f"🕒 Requested: {request_date}\n"
        requests_text += f"⚡ Commands:\n/approve_{user_id} /decline_{user_id}\n\n"
    
    update.message.reply_text(requests_text)

def approve_request(update, context):
    """Admin command to approve a join request"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        update.message.reply_text("❌ Admin only command.")
        return
    
    if not context.args:
        update.message.reply_text("❌ Usage: /approve <user_id>\nExample: /approve 123456789")
        return
    
    try:
        target_user_id = int(context.args[0])
        
        # Check if request exists
        c.execute("SELECT username, first_name FROM join_requests WHERE user_id=? AND status='pending'", (target_user_id,))
        request = c.fetchone()
        
        if not request:
            update.message.reply_text("❌ No pending join request found for this user ID.")
            return
        
        username, first_name = request
        
        # Approve the join request
        try:
            if GROUP_ID:
                context.bot.approve_chat_join_request(GROUP_ID, target_user_id)
            
            # Update database
            c.execute("UPDATE join_requests SET status='approved', processed_by=?, processed_time=? WHERE user_id=?", 
                     (user_id, time.time(), target_user_id))
            conn.commit()
            
            update.message.reply_text(f"✅ Join request for {first_name} (@{username}) approved!")
            
            # Notify user
            try:
                context.bot.send_message(
                    target_user_id,
                    f"🎉 Your join request for TMZ BRAND VIP has been approved! 🚀\n\n"
                    f"Welcome to the private VIP group, {first_name}!\n"
                    f"Enjoy the exclusive content! 🏆"
                )
            except:
                pass
                
        except Exception as e:
            update.message.reply_text(f"❌ Error approving request: {e}")
            
    except ValueError:
        update.message.reply_text("❌ Please provide a valid user ID (numbers only)")

def decline_request(update, context):
    """Admin command to decline a join request"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        update.message.reply_text("❌ Admin only command.")
        return
    
    if not context.args:
        update.message.reply_text("❌ Usage: /decline <user_id>\nExample: /decline 123456789")
        return
    
    try:
        target_user_id = int(context.args[0])
        
        # Check if request exists
        c.execute("SELECT username, first_name FROM join_requests WHERE user_id=? AND status='pending'", (target_user_id,))
        request = c.fetchone()
        
        if not request:
            update.message.reply_text("❌ No pending join request found for this user ID.")
            return
        
        username, first_name = request
        
        # Decline the join request
        try:
            if GROUP_ID:
                context.bot.decline_chat_join_request(GROUP_ID, target_user_id)
            
            # Update database
            c.execute("UPDATE join_requests SET status='declined', processed_by=?, processed_time=? WHERE user_id=?", 
                     (user_id, time.time(), target_user_id))
            conn.commit()
            
            update.message.reply_text(f"❌ Join request for {first_name} (@{username}) declined.")
            
            # Notify user
            try:
                context.bot.send_message(
                    target_user_id,
                    f"❌ Your join request for TMZ BRAND VIP has been declined.\n\n"
                    f"If you believe this is an error, please contact support."
                )
            except:
                pass
                
        except Exception as e:
            update.message.reply_text(f"❌ Error declining request: {e}")
            
    except ValueError:
        update.message.reply_text("❌ Please provide a valid user ID (numbers only)")

def handle_receipt(update, context):
    """Handle receipt image upload and verification"""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    # Check if user has pending payment
    c.execute("SELECT ref, amount, expiry_at FROM pending_payments WHERE user_id=?", (user_id,))
    row = c.fetchone()
    
    if not row:
        update.message.reply_text("❌ No pending payment found. Use /pay to create a payment request first.")
        return
    
    ref, expected_amount, expiry_at = row
    
    # Check if payment has expired
    if time.time() > expiry_at:
        c.execute("DELETE FROM pending_payments WHERE ref=?", (ref,))
        conn.commit()
        update.message.reply_text("⏰ Payment request expired. Use /pay to create a new one.")
        return
    
    # Check if message has photo
    if not update.message.photo:
        update.message.reply_text("❌ Please upload a screenshot of your payment receipt.")
        return
    
    # Get the highest quality photo
    photo_file = update.message.photo[-1].get_file()
    
    # Download photo data
    update.message.reply_text("🔍 Processing receipt... Please wait ⏳")
    
    try:
        # Download image data
        photo_data = io.BytesIO()
        photo_file.download(out=photo_data)
        photo_data.seek(0)
        
        # Extract text using OCR
        extracted_text = extract_text_from_image(photo_data.getvalue())
        
        if not extracted_text:
            update.message.reply_text(
                "❌ Could not read receipt text. Please ensure:\n\n"
                "• Screenshot is clear and readable\n"
                "• All text is visible\n"
                "• No important parts are cropped\n\n"
                "Try again with a better quality screenshot."
            )
            return
        
        # Extract amount from text
        detected_amount = extract_amount_from_text(extracted_text, expected_amount)
        
        if not detected_amount:
            update.message.reply_text(
                "❌ Could not find payment amount in receipt. Please ensure:\n\n"
                "• Amount is clearly visible\n"
                "• Receipt shows successful transaction\n"
                "• All text is readable\n\n"
                "Try again or contact support if problem persists."
            )
            return
        
        # Verify amount matches expected amount
        if abs(detected_amount - expected_amount) > 1:  # Allow small rounding differences
            update.message.reply_text(
                f"❌ Amount mismatch!\n\n"
                f"Expected: ₦{expected_amount:,}\n"
                f"Found in receipt: ₦{detected_amount:,}\n\n"
                f"Please ensure you sent exactly ₦{expected_amount:,}\n"
                f"Reference: {ref}"
            )
            return
        
        # Payment verified successfully!
        # Move from pending to verified
        c.execute("DELETE FROM pending_payments WHERE ref=?", (ref,))
        
        # Get user's real name from profile or use Telegram name
        real_name = get_user_profile(user_id) or user_name
        
        c.execute("INSERT INTO verified_payments VALUES (?,?,?,?,?,?,?,?)", 
                  (ref, user_id, expected_amount, time.time(), user_name, 
                   real_name, real_name, 'Opay/PalmPay'))
        conn.commit()
        
        print(f"✅ Payment verified: User {user_id}, Amount ₦{expected_amount}, Ref {ref}")
        
        # Send success message
        update.message.reply_text(
            f"✅ PAYMENT VERIFIED SUCCESSFULLY!\n\n"
            f"💰 Amount: ₦{expected_amount:,}\n"
            f"🔑 Reference: {ref}\n"
            f"👤 User: {user_name}\n"
            f"⏰ Verified at: {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"🎉 Welcome to TMZ BRAND VIP! 🚀"
        )
        
        # Send private access instructions (NO LINK SHARED)
        send_private_access(update, context, user_name, ref)
        
        # Notify admin
        if ADMIN_ID:
            try:
                context.bot.send_message(
                    ADMIN_ID,
                    f"💰 PAYMENT VERIFIED\n\n"
                    f"👤 User: {user_name}\n"
                    f"🆔 ID: {user_id}\n"
                    f"💰 Amount: ₦{expected_amount:,}\n"
                    f"🔑 Reference: {ref}\n"
                    f"⏰ Time: {datetime.now().strftime('%H:%M:%S')}"
                )
            except:
                pass
                
    except Exception as e:
        print(f"❌ Error processing receipt: {e}")
        update.message.reply_text("❌ Error processing receipt. Please try again or contact support.")

def handle_message(update, context):
    """Handle text messages - ONLY in private chats"""
    # Ignore group messages completely
    if update.effective_chat.type != 'private':
        return
    
    user_id = update.effective_user.id
    text = update.message.text
    
    # Check if message is a command
    if text.startswith('/'):
        return
    
    # Check if user has pending payment (might be sending reference or other info)
    c.execute("SELECT ref FROM pending_payments WHERE user_id=?", (user_id,))
    row = c.fetchone()
    
    if row:
        ref = row[0]
        update.message.reply_text(
            f"📸 Please upload a SCREENSHOT of your payment receipt for reference: {ref}\n\n"
            f"Ensure the screenshot shows:\n"
            f"• Amount: ₦{get_current_base_amount():,}\n"
            f"• Receiver: {RECEIVER_NAME}\n"
            f"• Reference: {ref}\n"
            f"• Transaction status: Successful"
        )
    else:
        update.message.reply_text(
            "🤖 TMZ BRAND VIP Payment Bot\n\n"
            "Use /pay to create a payment request\n"
            "Use /help for instructions\n"
            "Use /start to begin"
        )

def error_handler(update, context):
    """Handle errors"""
    print(f"❌ Error: {context.error}")
    if update and update.effective_message:
        update.effective_message.reply_text("❌ An error occurred. Please try again.")

# Flask webhook routes for deployment
@app.route('/')
def home():
    return "🤖 TMZ BRAND VIP Payment Bot is running!"

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle Telegram webhook updates"""
    update = telegram.Update.de_json(request.get_json(), bot)
    dispatcher.process_update(update)
    return 'OK'

def main():
    """Main function to start the bot"""
    print("🚀 Starting TMZ BRAND VIP Payment Bot...")
    
    # Import telegram components here to avoid circular imports
    from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, ChatJoinRequestHandler
    
    # Create updater and dispatcher
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher
    
    # Add handlers for private chats only
    private_filter = Filters.private
    
    dp.add_handler(CommandHandler("start", start, filters=private_filter))
    dp.add_handler(CommandHandler("pay", pay, filters=private_filter))
    dp.add_handler(CommandHandler("check", check, filters=private_filter))
    dp.add_handler(CommandHandler("history", history, filters=private_filter))
    dp.add_handler(CommandHandler("help", help_cmd, filters=private_filter))
    dp.add_handler(CommandHandler("stats", stats, filters=private_filter))
    dp.add_handler(CommandHandler("setprice", setprice, filters=private_filter))
    dp.add_handler(CommandHandler("pricesettings", pricesettings, filters=private_filter))
    dp.add_handler(CommandHandler("pendingrequests", pending_requests, filters=private_filter))
    dp.add_handler(CommandHandler("approve", approve_request, filters=private_filter))
    dp.add_handler(CommandHandler("decline", decline_request, filters=private_filter))
    
    # Handle join requests (this should work in groups)
    dp.add_handler(ChatJoinRequestHandler(handle_join_request))
    
    # Handle receipt images and text messages - private only
    dp.add_handler(MessageHandler(Filters.photo & private_filter, handle_receipt))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command & private_filter, handle_message))
    
    # Error handler
    dp.add_error_handler(error_handler)
    
    # Start polling
    updater.start_polling()
    print("✅ Bot is now running and polling for updates...")
    print("🔇 Bot will be silent in group chats")
    
    # Run until interrupted
    updater.idle()

if __name__ == '__main__':
    main()