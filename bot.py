import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# 1. Dummy Web Server to keep Render's Free Web Service happy
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot status: Healthy & Operational")

def run_health_check_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_check_server, daemon=True).start()

# 2. Configuration & State Management
BOT_TOKEN = os.getenv("BOT_TOKEN")
TARGET_CHAT_ID = int(os.getenv("TARGET_CHAT_ID", "0"))

# Map Telegram User IDs (integers) to real display names
NAME_MAP = {
    123456789: "Kenneth Khor",  # Replace with actual Telegram User ID
}

# Status definitions with corresponding UI icons
STATUS_CONFIG = {
    "Present": {"emoji": "✅", "label": "Present"},
    "Off":     {"emoji": "🌴", "label": "Off"},
    "MC":      {"emoji": "🤒", "label": "MC"},
    "MA":      {"emoji": "🏥", "label": "MA"},
    "OS":      {"emoji": "✈️", "label": "OS"},
    "Others":  {"emoji": "❓", "label": "Others"}
}

attendance_records = {}

def build_poll_keyboard():
    """Generates inline buttons with live submission counters."""
    counts = {key: 0 for key in STATUS_CONFIG}
    for record in attendance_records.values():
        if record["status"] in counts:
            counts[record["status"]] += 1

    keyboard = [
        [
            InlineKeyboardButton(
                f"{STATUS_CONFIG['Present']['emoji']} Present ({counts['Present']})", 
                callback_data="att_Present"
            ),
            InlineKeyboardButton(
                f"{STATUS_CONFIG['Off']['emoji']} Off ({counts['Off']})", 
                callback_data="att_Off"
            )
        ],
        [
            InlineKeyboardButton(
                f"{STATUS_CONFIG['MC']['emoji']} MC ({counts['MC']})", 
                callback_data="att_MC"
            ),
            InlineKeyboardButton(
                f"{STATUS_CONFIG['MA']['emoji']} MA ({counts['MA']})", 
                callback_data="att_MA"
            )
        ],
        [
            InlineKeyboardButton(
                f"{STATUS_CONFIG['OS']['emoji']} OS ({counts['OS']})", 
                callback_data="att_OS"
            ),
            InlineKeyboardButton(
                f"{STATUS_CONFIG['Others']['emoji']} Others ({counts['Others']})", 
                callback_data="att_Others"
            )
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def send_attendance_poll(context: ContextTypes.DEFAULT_TYPE):
    """Sends the daily attendance poll with enhanced formatting."""
    attendance_records.clear()
    
    poll_text = (
        "📊 **DAILY ATTENDANCE DECLARATION**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "> Please select your attendance status for tomorrow by tapping one of the options below.\n\n"
        "⚡ *Live responses will update on the buttons in real time.*"
    )

    await context.bot.send_message(
        chat_id=TARGET_CHAT_ID,
        text=poll_text,
        reply_markup=build_poll_keyboard(),
        parse_mode="Markdown"
    )

async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles button clicks, maps names, and updates the poll message UI dynamically."""
    query = update.callback_query
    user = query.from_user
    
    display_name = NAME_MAP.get(user.id, f"{user.first_name} {user.last_name or ''}".strip())
    status = query.data.replace("att_", "")
    
    attendance_records[user.id] = {"name": display_name, "status": status}
    
    # Send quick confirmation pop-up alert to user
    emoji = STATUS_CONFIG.get(status, {}).get("emoji", "👍")
    await query.answer(text=f"{emoji} {display_name}: Logged as {status}", show_alert=False)

    # Dynamically update the live button counters on the poll message
    try:
        await query.edit_message_reply_markup(reply_markup=build_poll_keyboard())
    except Exception:
        pass  # Ignore if no change in markup

async def send_consolidated_summary(context: ContextTypes.DEFAULT_TYPE):
    """Sends a cleanly formatted consolidated summary report."""
    if not attendance_records:
        await context.bot.send_message(
            chat_id=TARGET_CHAT_ID,
            text="⚠️ **ATTENDANCE SUMMARY**\n\n> No responses recorded for today's roll call.",
            parse_mode="Markdown"
        )
        return

    categorized = {key: [] for key in STATUS_CONFIG}
    for entry in attendance_records.values():
        if entry["status"] in categorized:
            categorized[entry["status"]].append(entry["name"])

    today_str = datetime.now().strftime("%d %b %Y")
    
    summary = (
        f"📋 **CONSOLIDATED ATTENDANCE REPORT**\n"
        f"📅 **Date:** `{today_str}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    for status_key, config in STATUS_CONFIG.items():
        names = categorized[status_key]
        count = len(names)
        emoji = config["emoji"]
        
        if names:
            name_list = ", ".join(names)
            summary += f"{emoji} **{status_key} ({count})**\n└ _{name_list}_\n\n"
        else:
            summary += f"{emoji} **{status_key} (0)**\n└ _None_\n\n"

    summary += (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 **Total Submissions:** `{len(attendance_records)}`"
    )

    await context.bot.send_message(
        chat_id=TARGET_CHAT_ID, 
        text=summary, 
        parse_mode="Markdown"
    )

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(handle_button_click, pattern="^att_"))

    scheduler = AsyncIOScheduler()

    # Production Schedule (Sunday 8:00 PM Poll, Monday 8:00 AM Summary)
    scheduler.add_job(
        send_attendance_poll, 
        CronTrigger(day_of_week='sun', hour=20, minute=0, timezone='Asia/Singapore'), 
        args=[app]
    )
    scheduler.add_job(
        send_consolidated_summary, 
        CronTrigger(day_of_week='mon', hour=8, minute=0, timezone='Asia/Singapore'), 
        args=[app]
    )

    scheduler.start()
    app.run_polling()

if __name__ == "__main__":
    main()
