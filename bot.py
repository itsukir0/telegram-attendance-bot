import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# 1. Dummy Web Server to keep Render's Free Web Service happy
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is online.")

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
    6298329418: "Kenneth Khor",  # Replace with actual Telegram User ID
}

# 15 Attendance Status Options
STATUS_CONFIG = {
    "MC":      {"emoji": "🤒", "category": "medical"},
    "RSO":     {"emoji": "🏥", "category": "medical"},
    "RSI":     {"emoji": "🩺", "category": "medical"},
    "MA":      {"emoji": "💉", "category": "medical"},
    "LL":      {"emoji": "📝", "category": "leave"},
    "OL":      {"emoji": "📄", "category": "leave"},
    "OS":      {"emoji": "✈️", "category": "leave"},
    "OC":      {"emoji": "🌊", "category": "leave"},
    "OFF":     {"emoji": "🌴", "category": "leave"},
    "CCL/CSL": {"emoji": "🎓", "category": "leave"},
    "CL":      {"emoji": "🏠", "category": "leave"},
    "PL":      {"emoji": "👶", "category": "leave"},
    "OML":     {"emoji": "🎖️", "category": "leave"},
    "Others":  {"emoji": "❓", "category": "leave"},
    "Late":    {"emoji": "⏰", "category": "medical"}
}

attendance_records = {}

def build_poll_keyboard():
    """Generates inline buttons in a clean 2-column layout with live counters."""
    counts = {key: 0 for key in STATUS_CONFIG}
    for record in attendance_records.values():
        if record["status"] in counts:
            counts[record["status"]] += 1

    keyboard = []
    items = list(STATUS_CONFIG.items())
    
    for i in range(0, len(items), 2):
        row = []
        key1, cfg1 = items[i]
        row.append(
            InlineKeyboardButton(
                f"{cfg1['emoji']} {key1} ({counts[key1]})", 
                callback_data=f"att_{key1}"
            )
        )
        if i + 1 < len(items):
            key2, cfg2 = items[i + 1]
            row.append(
                InlineKeyboardButton(
                    f"{cfg2['emoji']} {key2} ({counts[key2]})", 
                    callback_data=f"att_{key2}"
                )
            )
        keyboard.append(row)

    return InlineKeyboardMarkup(keyboard)

async def send_attendance_poll(context: ContextTypes.DEFAULT_TYPE):
    """Sends the daily attendance poll."""
    attendance_records.clear()
    
    poll_text = (
        "📌 **DAILY ATTENDANCE DECLARATION**\n"
        "───────────────────────────\n"
        "Please select your status for tomorrow by tapping an option below:"
    )

    await context.bot.send_message(
        chat_id=TARGET_CHAT_ID,
        text=poll_text,
        reply_markup=build_poll_keyboard(),
        parse_mode="Markdown"
    )

async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles button clicks and updates button counters."""
    query = update.callback_query
    user = query.from_user
    
    display_name = NAME_MAP.get(user.id, f"{user.first_name} {user.last_name or ''}".strip())
    status = query.data.replace("att_", "")
    
    attendance_records[user.id] = {"name": display_name, "status": status}
    
    emoji = STATUS_CONFIG.get(status, {}).get("emoji", "👍")
    await query.answer(text=f"{emoji} Logged: {status} ({display_name})", show_alert=False)

    try:
        await query.edit_message_reply_markup(reply_markup=build_poll_keyboard())
    except Exception:
        pass

async def send_consolidated_summary(context: ContextTypes.DEFAULT_TYPE):
    """Sends a clean, easy-to-read summary report."""
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
    total_responses = len(attendance_records)
    total_personnel = len(NAME_MAP) if NAME_MAP else total_responses
    pending_count = max(0, total_personnel - total_responses)

    summary = (
        f"📋 **ATTENDANCE SUMMARY — {today_str}**\n"
        f"───────────────────────────\n\n"
    )

    medical_entries = []
    leave_entries = []
    nil_list = []

    for status_key, config in STATUS_CONFIG.items():
        names = categorized[status_key]
        count = len(names)
        emoji = config["emoji"]
        
        if names:
            name_str = ", ".join(names)
            entry = f"{emoji} **{status_key} ({count})**: {name_str}"
            if config["category"] == "medical":
                medical_entries.append(entry)
            else:
                leave_entries.append(entry)
        else:
            nil_list.append(f"{status_key}: 0")

    # Display active statuses cleanly
    if medical_entries:
        summary += "🔴 **MEDICAL / ABSENT**\n" + "\n".join(medical_entries) + "\n\n"

    if leave_entries:
        summary += "🟢 **LEAVE / DUTY**\n" + "\n".join(leave_entries) + "\n\n"

    # Compact NIL report line at bottom
    if nil_list:
        summary += "⚪ **NIL:** " + " • ".join(nil_list) + "\n\n"

    summary += (
        f"───────────────────────────\n"
        f"👥 **Total Submitted:** {total_responses}/{total_personnel} "
        f"| **Pending:** {pending_count}"
    )

    await context.bot.send_message(
        chat_id=TARGET_CHAT_ID, 
        text=summary, 
        parse_mode="Markdown"
    )

# 3. Command Handlers for Manual Testing
async def test_poll_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual trigger: /testpoll"""
    await send_attendance_poll(context)

async def test_summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual trigger: /testsummary"""
    await send_consolidated_summary(context)

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CallbackQueryHandler(handle_button_click, pattern="^att_"))
    app.add_handler(CommandHandler("testpoll", test_poll_cmd))
    app.add_handler(CommandHandler("testsummary", test_summary_cmd))

    # Production Scheduler (Sunday 8:00 PM Poll, Monday 8:00 AM Summary SGT)
    scheduler = AsyncIOScheduler()
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
