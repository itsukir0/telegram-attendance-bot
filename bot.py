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

# Your exact 15 attendance status options with matching icons
STATUS_CONFIG = {
    "MC":      {"emoji": "🤒"},
    "RSO":     {"emoji": "🏥"},
    "RSI":     {"emoji": "🩺"},
    "MA":      {"emoji": "💉"},
    "LL":      {"emoji": "📝"},
    "OL":      {"emoji": "📄"},
    "OS":      {"emoji": "✈️"},
    "OC":      {"emoji": "🌊"},
    "OFF":     {"emoji": "🌴"},
    "CCL/CSL": {"emoji": "🎓"},
    "CL":      {"emoji": "🏠"},
    "PL":      {"emoji": "👶"},
    "OML":     {"emoji": "🎖️"},
    "Others":  {"emoji": "❓"},
    "Late":    {"emoji": "⏰"}
}

attendance_records = {}

def build_poll_keyboard():
    """Generates inline buttons in a 2-column layout with live counters."""
    counts = {key: 0 for key in STATUS_CONFIG}
    for record in attendance_records.values():
        if record["status"] in counts:
            counts[record["status"]] += 1

    keyboard = []
    items = list(STATUS_CONFIG.items())
    
    # Grid construction: 2 buttons per row
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
        "📊 **DAILY ATTENDANCE DECLARATION**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "> Please select your attendance status by tapping an option below.\n\n"
        "⚡ *Live counts update on the buttons in real time.*"
    )

    await context.bot.send_message(
        chat_id=TARGET_CHAT_ID,
        text=poll_text,
        reply_markup=build_poll_keyboard(),
        parse_mode="Markdown"
    )

async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles button clicks, maps names, and updates button counters."""
    query = update.callback_query
    user = query.from_user
    
    display_name = NAME_MAP.get(user.id, f"{user.first_name} {user.last_name or ''}".strip())
    status = query.data.replace("att_", "")
    
    attendance_records[user.id] = {"name": display_name, "status": status}
    
    emoji = STATUS_CONFIG.get(status, {}).get("emoji", "👍")
    await query.answer(text=f"{emoji} {display_name}: Logged as {status}", show_alert=False)

    try:
        await query.edit_message_reply_markup(reply_markup=build_poll_keyboard())
    except Exception:
        pass

async def send_consolidated_summary(context: ContextTypes.DEFAULT_TYPE):
    """Sends a formatted summary following your exact list order."""
    if not attendance_records:
        await context.bot.send_message(
            chat_id=TARGET_CHAT_ID,
            text="⚠️ **ATTENDANCE SUMMARY**\n\n> No responses recorded for this roll call.",
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
            summary += f"{emoji} **{status_key}: {count}** ({name_list})\n"
        else:
            summary += f"{emoji} **{status_key}: 0**\n"

    summary += (
        f"\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 **Total Submissions:** `{len(attendance_records)}`"
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
