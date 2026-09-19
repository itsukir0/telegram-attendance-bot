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
        self.wfile.write(b"SYSTEM STATUS: ONLINE // ALL SYSTEMS NOMINAL")

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
    "MC":      {"emoji": "🤒", "type": "critical"},
    "RSO":     {"emoji": "🏥", "type": "critical"},
    "RSI":     {"emoji": "🩺", "type": "critical"},
    "MA":      {"emoji": "💉", "type": "critical"},
    "LL":      {"emoji": "📝", "type": "duty"},
    "OL":      {"emoji": "📄", "type": "duty"},
    "OS":      {"emoji": "✈️", "type": "duty"},
    "OC":      {"emoji": "🌊", "type": "duty"},
    "OFF":     {"emoji": "🌴", "type": "duty"},
    "CCL/CSL": {"emoji": "🎓", "type": "duty"},
    "CL":      {"emoji": "🏠", "type": "duty"},
    "PL":      {"emoji": "👶", "type": "duty"},
    "OML":     {"emoji": "🎖️", "type": "duty"},
    "Others":  {"emoji": "❓", "type": "duty"},
    "Late":    {"emoji": "⏰", "type": "critical"}
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
    """Sends the daily attendance poll with HUD styling."""
    attendance_records.clear()
    
    poll_text = (
        "🌐 **[ UNIT ATTENDANCE DISPATCH ]**\n"
        "<code>========================================</code>\n"
        "> 📡 **ACTION REQUIRED:** Submit your status for tomorrow.\n"
       
        "<code>========================================</code>"
    )

    await context.bot.send_message(
        chat_id=TARGET_CHAT_ID,
        text=poll_text,
        reply_markup=build_poll_keyboard(),
        parse_mode="HTML"
    )

async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles button clicks and updates button counters."""
    query = update.callback_query
    user = query.from_user
    
    display_name = NAME_MAP.get(user.id, f"{user.first_name} {user.last_name or ''}".strip())
    status = query.data.replace("att_", "")
    
    attendance_records[user.id] = {"name": display_name, "status": status}
    
    emoji = STATUS_CONFIG.get(status, {}).get("emoji", "👍")
    await query.answer(text=f"[{status}] ACKNOWLEDGED // {display_name}", show_alert=False)

    try:
        await query.edit_message_reply_markup(reply_markup=build_poll_keyboard())
    except Exception:
        pass

def generate_progress_bar(current, total, length=10):
    """Generates a visual HUD progress bar."""
    if total <= 0:
        return "░" * length
    percent = min(1.0, max(0.0, current / total))
    filled = int(round(length * percent))
    return "█" * filled + "░" * (length - filled)

async def send_consolidated_summary(context: ContextTypes.DEFAULT_TYPE):
    """Sends an advanced, futuristic HUD-style attendance report."""
    if not attendance_records:
        await context.bot.send_message(
            chat_id=TARGET_CHAT_ID,
            text=(
                "⚠️ <b>[ TELEMETRY ALERT ]</b>\n"
                "<code>========================================</code>\n"
                "❌ <b>NO RESPONSES RECORDED FOR THIS ROLL CALL CYCLE.</b>"
            ),
            parse_mode="HTML"
        )
        return

    categorized = {key: [] for key in STATUS_CONFIG}
    for entry in attendance_records.values():
        if entry["status"] in categorized:
            categorized[entry["status"]].append(entry["name"])

    today_str = datetime.now().strftime("%d %b %Y // %H:%M SGT")
    total_responses = len(attendance_records)
    total_personnel = len(NAME_MAP) if NAME_MAP else total_responses
    pending_count = max(0, total_personnel - total_responses)
    pct = int((total_responses / total_personnel) * 100) if total_personnel > 0 else 100
    bar = generate_progress_bar(total_responses, total_personnel)

    # FUTURISTIC HEADER
    summary = (
        f"<b>[ CONSOLIDATED MANPOWER REPORT ]</b>\n"
        f"<code>SYS.DATE : {today_str}</code>\n"
        f"<code>========================================</code>\n\n"
    )

    critical_list = []
    duty_list = []
    nil_list = []

    for status_key, config in STATUS_CONFIG.items():
        names = categorized[status_key]
        count = len(names)
        emoji = config["emoji"]
        
        if names:
            formatted_names = ", ".join([f"<code>{n}</code>" for n in names])
            entry_str = f"{emoji} <b>{status_key}</b> [{count}]\n   └ {formatted_names}"
            
            if config["type"] == "critical":
                critical_list.append(entry_str)
            else:
                duty_list.append(entry_str)
        else:
            nil_list.append(f"<code>{status_key}:0</code>")

    # SECTION 1: MEDICAL & CRITICAL ABSENCE
    if critical_list:
        summary += "🚨 <b>[ MEDICAL / CRITICAL STATUS ]</b>\n"
        summary += "\n".join(critical_list) + "\n\n"

    # SECTION 2: LEAVE & DUTY DISPATCH
    if duty_list:
        summary += "🔷 <b>[ ACTIVE DISPATCH / LEAVE ]</b>\n"
        summary += "\n".join(duty_list) + "\n\n"

    # SECTION 3: NIL LOGS
    if nil_list:
        summary += "<code>----------------------------------------</code>\n"
        summary += "⚪ <b>[ NIL REPORT ]</b>\n"
        summary += "<code>" + " • ".join(nil_list) + "</code>\n\n"

    # SECTION 4: HUD SYSTEM OVERVIEW & PROGRESS
    summary += (
        f"<code>========================================</code>\n"
        f"<b>[ SYSTEM TELEMETRY ]</b>\n"
        f"<b>SUBMISSION RATE :</b> [{bar}] <b>{pct}%</b>\n"
        f"• <b>LOGGED PERSONNEL   :</b> <code>{total_responses} / {total_personnel}</code>\n"
        f"• <b>PENDING TELEMETRY :</b> <code>{pending_count}</code>\n"
        f"<code>========================================</code>"
    )

    await context.bot.send_message(
        chat_id=TARGET_CHAT_ID, 
        text=summary, 
        parse_mode="HTML"
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
