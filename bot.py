import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
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

# Attendance Status Options
STATUS_CONFIG = {
    "PRESENT":  {"emoji": "🟢", "category": "present"},
    "AM OFF":   {"emoji": "🌅", "category": "leave"},
    "PM OFF":   {"emoji": "🌇", "category": "leave"},
    "TIME OFF": {"emoji": "⏱️", "category": "leave"},
    "OFF":      {"emoji": "🌴", "category": "leave"},
    "MC":       {"emoji": "🤒", "category": "medical"},
    "RSO":      {"emoji": "🏥", "category": "medical"},
    "RSI":      {"emoji": "🩺", "category": "medical"},
    "MA":       {"emoji": "💉", "category": "medical"},
    "LL":       {"emoji": "📝", "category": "leave"},
    "OL":       {"emoji": "📄", "category": "leave"},
    "OS":       {"emoji": "✈️", "category": "leave"},
    "OC":       {"emoji": "🌊", "category": "leave"},
    "CCL/CSL":  {"emoji": "🎓", "category": "leave"},
    "CL":       {"emoji": "🏠", "category": "leave"},
    "PL":       {"emoji": "👶", "category": "leave"},
    "OML":      {"emoji": "🎖️", "category": "leave"},
    "Others":   {"emoji": "❓", "category": "leave"},
    "Late":     {"emoji": "⏰", "category": "medical"}
}

# Standard time off presets
TIME_OFF_PRESETS = ["Until 10 AM", "Until 11 AM", "Until 12 PM", "Until 2 PM", "Until 4 PM", "Until 5 PM"]

attendance_records = {}
awaiting_custom_time = set()  # Track user IDs waiting to enter a custom time

def build_poll_keyboard():
    """Generates inline buttons in a clean 2-column layout with live counters."""
    counts = {}
    for record in attendance_records.values():
        st = record["status_display"]
        counts[st] = counts.get(st, 0) + 1

    keyboard = []
    items = list(STATUS_CONFIG.items())
    
    for i in range(0, len(items), 2):
        row = []
        key1, cfg1 = items[i]
        c1 = counts.get(key1, 0)
        row.append(
            InlineKeyboardButton(
                f"{cfg1['emoji']} {key1} ({c1})" if c1 > 0 else f"{cfg1['emoji']} {key1}", 
                callback_data=f"att_{key1}"
            )
        )
        if i + 1 < len(items):
            key2, cfg2 = items[i + 1]
            c2 = counts.get(key2, 0)
            row.append(
                InlineKeyboardButton(
                    f"{cfg2['emoji']} {key2} ({c2})" if c2 > 0 else f"{cfg2['emoji']} {key2}", 
                    callback_data=f"att_{key2}"
                )
            )
        keyboard.append(row)

    return InlineKeyboardMarkup(keyboard)

def build_time_off_keyboard():
    """Generates quick selection buttons for Time Off timing."""
    keyboard = []
    for i in range(0, len(TIME_OFF_PRESETS), 2):
        row = [InlineKeyboardButton(TIME_OFF_PRESETS[i], callback_data=f"timeoff_{TIME_OFF_PRESETS[i]}")]
        if i + 1 < len(TIME_OFF_PRESETS):
            row.append(InlineKeyboardButton(TIME_OFF_PRESETS[i+1], callback_data=f"timeoff_{TIME_OFF_PRESETS[i+1]}"))
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("✍️ Custom Time", callback_data="timeoff_custom")])
    keyboard.append([InlineKeyboardButton("⬅️ Back to Roll Call", callback_data="timeoff_back")])
    return InlineKeyboardMarkup(keyboard)

async def send_attendance_poll(context: ContextTypes.DEFAULT_TYPE):
    """Sends the daily attendance poll."""
    attendance_records.clear()
    awaiting_custom_time.clear()
    
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
    data = query.data
    
    display_name = NAME_MAP.get(user.id, f"{user.first_name} {user.last_name or ''}".strip())

    # User clicked "TIME OFF"
    if data == "att_TIME OFF":
        await query.answer()
        await query.edit_message_text(
            text=f"⏱️ **SELECT TIME OFF DURATION FOR {display_name}:**",
            reply_markup=build_time_off_keyboard(),
            parse_mode="Markdown"
        )
        return

    # User selected a preset Time Off value
    if data.startswith("timeoff_"):
        sub_action = data.replace("timeoff_", "")
        
        if sub_action == "back":
            await query.edit_message_text(
                text=(
                    "📌 **DAILY ATTENDANCE DECLARATION**\n"
                    "───────────────────────────\n"
                    "Please select your status for tomorrow by tapping an option below:"
                ),
                reply_markup=build_poll_keyboard(),
                parse_mode="Markdown"
            )
            return

        if sub_action == "custom":
            awaiting_custom_time.add(user.id)
            await query.answer()
            await query.edit_message_text(
                text=(
                    f"⏱️ **CUSTOM TIME OFF FOR {display_name}:**\n\n"
                    f"Please reply to this group with your timing in this format:\n"
                    f"`/time 11:30 AM` or `/time 10:00`"
                ),
                parse_mode="Markdown"
            )
            return

        # Preset selected
        time_detail = f"TIME OFF ({sub_action})"
        attendance_records[user.id] = {
            "name": display_name,
            "status_key": "TIME OFF",
            "status_display": time_detail
        }
        await query.answer(text=f"⏱️ Logged: {time_detail}", show_alert=False)
        
        # Return to main poll view
        await query.edit_message_text(
            text=(
                "📌 **DAILY ATTENDANCE DECLARATION**\n"
                "───────────────────────────\n"
                "Please select your status for tomorrow by tapping an option below:"
            ),
            reply_markup=build_poll_keyboard(),
            parse_mode="Markdown"
        )
        return

    # Normal Status Click
    status = data.replace("att_", "")
    attendance_records[user.id] = {
        "name": display_name,
        "status_key": status,
        "status_display": status
    }
    
    emoji = STATUS_CONFIG.get(status, {}).get("emoji", "👍")
    await query.answer(text=f"{emoji} Logged: {status} ({display_name})", show_alert=False)

    try:
        await query.edit_message_reply_markup(reply_markup=build_poll_keyboard())
    except Exception:
        pass

async def handle_custom_time_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles custom time commands like /time 11:30 AM."""
    user = update.message.from_user
    display_name = NAME_MAP.get(user.id, f"{user.first_name} {user.last_name or ''}".strip())

    if user.id in awaiting_custom_time:
        if not context.args:
            await update.message.reply_text("⚠️ Please specify a time. Example: `/time 11:30 AM`", parse_mode="Markdown")
            return

        time_str = " ".join(context.args)
        time_detail = f"TIME OFF (Until {time_str})"
        
        attendance_records[user.id] = {
            "name": display_name,
            "status_key": "TIME OFF",
            "status_display": time_detail
        }
        awaiting_custom_time.remove(user.id)
        
        await update.message.reply_text(
            f"✅ **Recorded for {display_name}:** `{time_detail}`",
            parse_mode="Markdown"
        )

async def send_consolidated_summary(context: ContextTypes.DEFAULT_TYPE):
    """Sends a clean, easy-to-read summary report."""
    if not attendance_records:
        await context.bot.send_message(
            chat_id=TARGET_CHAT_ID,
            text="⚠️ **ATTENDANCE SUMMARY**\n\n> No responses recorded for today's roll call.",
            parse_mode="Markdown"
        )
        return

    # Group names by detailed status
    categorized = {}
    for entry in attendance_records.values():
        disp = entry["status_display"]
        if disp not in categorized:
            categorized[disp] = []
        categorized[disp].append(entry["name"])

    today_str = datetime.now().strftime("%d %b %Y")
    total_responses = len(attendance_records)
    total_personnel = len(NAME_MAP) if NAME_MAP else total_responses
    pending_count = max(0, total_personnel - total_responses)

    summary = (
        f"📋 **ATTENDANCE SUMMARY — {today_str}**\n"
        f"───────────────────────────\n\n"
    )

    present_entries = []
    medical_entries = []
    leave_entries = []
    
    # Track used status keys to identify NIL entries
    recorded_keys = set()

    for disp_status, names in categorized.items():
        count = len(names)
        base_key = disp_status.split(" (")[0]
        recorded_keys.add(base_key)
        
        cfg = STATUS_CONFIG.get(base_key, {"emoji": "🔹", "category": "leave"})
        emoji = cfg["emoji"]
        name_str = ", ".join(names)
        
        entry_str = f"{emoji} **{disp_status} ({count})**: {name_str}"

        if cfg["category"] == "present":
            present_entries.append(entry_str)
        elif cfg["category"] == "medical":
            medical_entries.append(entry_str)
        else:
            leave_entries.append(entry_str)

    # NIL list for options with zero submissions
    nil_list = [f"{key}: 0" for key in STATUS_CONFIG if key not in recorded_keys]

    # Render Sections
    if present_entries:
        summary += "🟢 **PRESENT**\n" + "\n".join(present_entries) + "\n\n"

    if leave_entries:
        summary += "🌅 **LEAVE / OFF / DUTY**\n" + "\n".join(leave_entries) + "\n\n"

    if medical_entries:
        summary += "🔴 **MEDICAL / ABSENT**\n" + "\n".join(medical_entries) + "\n\n"

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
    app.add_handler(CallbackQueryHandler(handle_button_click))
    app.add_handler(CommandHandler("time", handle_custom_time_cmd))
    app.add_handler(CommandHandler("testpoll", test_poll_cmd))
    app.add_handler(CommandHandler("testsummary", test_summary_cmd))

    # Production Scheduler (Daily Poll at 8:00 PM SGT, Daily Summary at 8:00 AM SGT)
    scheduler = AsyncIOScheduler()
    
    scheduler.add_job(
        send_attendance_poll, 
        CronTrigger(hour=20, minute=0, timezone='Asia/Singapore'), 
        kwargs={'context': app}
    )
    
    scheduler.add_job(
        send_consolidated_summary, 
        CronTrigger(hour=8, minute=0, timezone='Asia/Singapore'), 
        kwargs={'context': app}
    )

    scheduler.start()
    app.run_polling()

if __name__ == "__main__":
    main()
