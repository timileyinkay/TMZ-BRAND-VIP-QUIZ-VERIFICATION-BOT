# TMZ BRAND VIP Payment Verification Bot

A secure Telegram bot for payment verification and automatic group access approval.

## Features

- ✅ **OCR Receipt Verification** - Automatically reads payment receipts
- 🔒 **Auto-Approval System** - No group links shared publicly
- 💰 **Exact Amount Verification** - Ensures correct payment amount
- 📊 **Admin Dashboard** - View statistics and manage payments
- ⏰ **Timeout System** - Payment requests expire after set time

## Setup

1. Clone the repository
2. Install requirements: `pip install -r requirements.txt`
3. Copy `env.example` to `.env` and configure your settings
4. Run: `python ocr_bot_fixed.py`

## Environment Variables

See `env.example` for all required environment variables.

## Security Features

- No group links ever shared publicly
- Payment required for group access
- Automatic join request approval after payment
- OCR verification prevents fraud

## Admin Commands

- `/stats` - View bot statistics
- `/setprice <amount>` - Change payment amount
- `/pendingrequests` - View pending join requests
- `/approve <user_id>` - Manually approve user
- `/decline <user_id>` - Decline join request