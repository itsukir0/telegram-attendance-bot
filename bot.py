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
        self.wfile.write(b"Bot is live!")

def run_health_check_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# Start dummy server in a background thread
threading.Thread(target=run_health_check_server, daemon=True).start()

# 2. Main Telegram Bot Code
BOT_TOKEN = os.getenv("BOT_TOKEN")
TARGET_CHAT_ID = int(os.getenv("TARGET_CHAT_ID", "0"))

attendance_records = {}
STATUS_OPTIONS = ["Present", "Off", "MC", "MA", "OS", "Others"]

async def send_attendance_poll(context: ContextTypes.DEFAULT_TYPE):
    attendance_records.clear()
    keyboard = [
        [InlineKeyboardButton("Present", callback_data="att_Present"), InlineKeyboardButton("Off", callback_data="att_Off")],
        [InlineKeyboardButton("MC", callback_data="att_MC"), InlineKeyboardButton("MA", callback_data="att_MA")],
        [InlineKeyboardButton("OS", callback_data="att_OS"), InlineKeyboardButton("Others", callback_data="att_Others")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=TARGET_CHAT_ID,
        text="📌 **DAILY ATTENDANCE CHECK**\nPlease select your attendance status for tomorrow:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    full_name = f"{user.first_name} {user.last_name or ''}".strip()
    status = query.data.replace("att_", "")
    
    attendance_records[user.id] = {"name": full_name, "status": status}
    await query.answer(text=f"Logged: {status}", show_alert=False)

async def send_consolidated_summary(context: ContextTypes.DEFAULT_TYPE):
    if not attendance_records:
        await context.bot.send_message(
            chat_id=TARGET_CHAT_ID,
            text="⚠️ **Attendance Summary**: No members recorded their status."
        )
        return

    categorized = {opt: [] for opt in STATUS_OPTIONS}
    for entry in attendance_records.values():
        categorized[entry["status"]].append(entry["name"])

    today_str = datetime.now().strftime("%d %b %Y")
    summary = f"📋 **CONSOLIDATED ATTENDANCE — {today_str}**\n───────────────────────────\n"

    for status in STATUS_OPTIONS:
        names = categorized[status]
        count = len(names)
        name_list = ", ".join(names) if names else "None"
        summary += f"• **{status} ({count})**: {name_list}\n"

    summary += f"───────────────────────────\n**Total Submissions:** {len(attendance_records)}"
    await context.bot.send_message(chat_id=TARGET_CHAT_ID, text=summary, parse_mode="Markdown")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(handle_button_click, pattern="^att_"))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_attendance_poll, CronTrigger(day_of_week='sun', hour=20, minute=0), args=[app])
    scheduler.add_job(send_consolidated_summary, CronTrigger(day_of_week='mon', hour=8, minute=0), args=[app])

    scheduler.start()
    app.run_polling()

if __name__ == "__main__":
    main()
