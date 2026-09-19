import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Timezone setup
SGT = pytz.timezone('Asia/Singapore')

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

TIME_OFF_PRESETS = ["Until 10 AM", "Until 11 AM", "Until 12 PM", "Until 2 PM", "Until 4 PM", "Until 5 PM"]

attendance_records = {}
awaiting_custom_time = set()
awaiting_custom_others = set()

def build_poll_keyboard():
    """Generates inline buttons in a clean 2-column layout with live counters."""
    counts = {}
    for record in attendance_records.values():
        st = record["status_key"]
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
    """Generates quick selection buttons for private Time Off selection."""
    keyboard = []
    for i in range(0, len(TIME_OFF_PRESETS), 2):
        row = [InlineKeyboardButton(TIME_OFF_PRESETS[i], callback_data=f"timeoff_{TIME_OFF_PRESETS[i]}")]
        if i + 1 < len(TIME_OFF_PRESETS):
            row.append(InlineKeyboardButton(TIME_OFF_PRESETS[i+1], callback_data=f"timeoff_{TIME_OFF_PRESETS[i+1]}"))
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("✍️ Custom Time", callback_data="timeoff_custom")])
    return InlineKeyboardMarkup(keyboard)

async def send_attendance_poll(context: ContextTypes.DEFAULT_TYPE):
    """Sends the daily attendance poll targeting the NEXT day's attendance."""
    attendance_records.clear()
    awaiting_custom_time.clear()
    awaiting_custom_others.clear()
    
    # Calculate target date (Tomorrow SGT)
    target_date = (datetime.now(SGT) + timedelta(days=1)).strftime("%A, %d %b %Y").upper()
    
    poll_text = (
        f"📌 **DAILY ATTENDANCE DECLARATION**\n"
        f"📅 **FOR: {target_date}**\n"
        f"═══════════════════════════\n"
        f"Please select your status by tapping an option below:"
    )

    await context.bot.send_message(
        chat_id=TARGET_CHAT_ID,
        text=poll_text,
        reply_markup=build_poll_keyboard(),
        parse_mode="Markdown"
    )

async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles button clicks and updates button counters without editing the main poll message."""
    query = update.callback_query
    user = query.from_user
    data = query.data
    
    display_name = NAME_MAP.get(user.id, f"{user.first_name} {user.last_name or ''}".strip())

    # User clicked "TIME OFF" -> Send them a private DM
    if data == "att_TIME OFF":
        try:
            await context.bot.send_message(
                chat_id=user.id,
                text="⏱️ **Select your Time Off duration:**",
                reply_markup=build_time_off_keyboard(),
                parse_mode="Markdown"
            )
            await query.answer("📩 Sent options to your private chat with the bot!", show_alert=True)
        except Exception:
            await query.answer(
                "⚠️ Bot couldn't DM you! Please tap the bot's profile and press Start.\nAlternatively, reply here with: /time 11 AM", 
                show_alert=True
            )
        return

    # User clicked "Others" -> Prompt for custom status reason via DM
    if data == "att_Others":
        awaiting_custom_others.add(user.id)
        try:
            await context.bot.send_message(
                chat_id=user.id,
                text=(
                    f"❓ **Custom Status for {display_name}:**\n\n"
                    f"Please reply with **ANY reason** you want (e.g. `In-Camp Training`, `Off-in-Lieu`, `Personal Matters`)."
                ),
                parse_mode="Markdown"
            )
            await query.answer("📩 Sent prompt to your private chat with the bot!", show_alert=True)
        except Exception:
            await query.answer(
                "⚠️ Bot couldn't DM you! Please tap the bot's profile and press Start.\nAlternatively, reply here with: /other Your Reason", 
                show_alert=True
            )
        return

    # User selected a Time Off option in private chat
    if data.startswith("timeoff_"):
        sub_action = data.replace("timeoff_", "")

        if sub_action == "custom":
            awaiting_custom_time.add(user.id)
            await query.answer()
            await query.edit_message_text(
                text=(
                    f"⏱️ **Custom Time Off for {display_name}:**\n\n"
                    f"Please reply here or in the group with your time (e.g., `11:30 AM` or `/time 10:00`)."
                ),
                parse_mode="Markdown"
            )
            return

        time_detail = f"TIME OFF ({sub_action})"
        attendance_records[user.id] = {
            "name": display_name,
            "status_key": "TIME OFF",
            "status_display": time_detail
        }
        await query.answer(text=f"⏱️ Logged: {time_detail}", show_alert=False)
        await query.edit_message_text(f"✅ **Recorded:** `{time_detail}`", parse_mode="Markdown")

        # Refresh group poll counters silently
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=TARGET_CHAT_ID,
                message_id=query.message.message_id,
                reply_markup=build_poll_keyboard()
            )
        except Exception:
            pass
        return

    # Standard Button Click in Group
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

async def handle_private_text_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles direct plain-text messages sent to the bot for custom 'Others' or 'Time Off' reasons."""
    user = update.message.from_user
    text = update.message.text.strip()
    display_name = NAME_MAP.get(user.id, f"{user.first_name} {user.last_name or ''}".strip())

    # Handle pending "Others" entry
    if user.id in awaiting_custom_others or update.message.chat.type == "private":
        other_detail = f"Others ({text})"
        attendance_records[user.id] = {
            "name": display_name,
            "status_key": "Others",
            "status_display": other_detail
        }
        if user.id in awaiting_custom_others:
            awaiting_custom_others.remove(user.id)

        await update.message.reply_text(
            f"✅ **Recorded for {display_name}:** `{other_detail}`",
            parse_mode="Markdown"
        )
        return

    # Handle pending "Custom Time Off" entry
    if user.id in awaiting_custom_time:
        time_detail = f"TIME OFF (Until {text})"
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

async def handle_custom_time_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles custom time commands like /time 11:30 AM."""
    user = update.message.from_user
    display_name = NAME_MAP.get(user.id, f"{user.first_name} {user.last_name or ''}".strip())

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
    if user.id in awaiting_custom_time:
        awaiting_custom_time.remove(user.id)
    
    await update.message.reply_text(
        f"✅ **Recorded for {display_name}:** `{time_detail}`",
        parse_mode="Markdown"
    )

async def handle_custom_other_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles custom 'Others' commands like /other Off-in-Lieu."""
    user = update.message.from_user
    display_name = NAME_MAP.get(user.id, f"{user.first_name} {user.last_name or ''}".strip())

    if not context.args:
        await update.message.reply_text("⚠️ Please specify your status. Example: `/other Off-in-Lieu`", parse_mode="Markdown")
        return

    other_reason = " ".join(context.args)
    other_detail = f"Others ({other_reason})"
    
    attendance_records[user.id] = {
        "name": display_name,
        "status_key": "Others",
        "status_display": other_detail
    }
    if user.id in awaiting_custom_others:
        awaiting_custom_others.remove(user.id)
    
    await update.message.reply_text(
        f"✅ **Recorded for {display_name}:** `{other_detail}`",
        parse_mode="Markdown"
    )

async def send_consolidated_summary(context: ContextTypes.DEFAULT_TYPE):
    """Sends a clean dashboard report showing total responses for today's attendance."""
    today_str = datetime.now(SGT).strftime("%A, %d %b %Y").upper()
    
    # Fetch live member count from Telegram (Subtract 1 for the Bot itself)
    try:
        chat_member_count = await context.bot.get_chat_member_count(TARGET_CHAT_ID)
        total_personnel = max(1, chat_member_count - 1)
    except Exception:
        total_personnel = len(NAME_MAP) if NAME_MAP else len(attendance_records)

    total_responses = len(attendance_records)
    no_response_count = max(0, total_personnel - total_responses)

    if not attendance_records:
        await context.bot.send_message(
            chat_id=TARGET_CHAT_ID,
            text=(
                f"🚨 **DAILY ATTENDANCE REPORT**\n"
                f"📅 **DATE: {today_str}**\n"
                f"═══════════════════════════\n\n"
                f"⚠️ **STATUS:** No declarations submitted for today's attendance.\n"
                f"👥 **Total Strength:** `{total_personnel}`\n"
                f"⚠️ **Unaccounted / No Response:** `{total_personnel}` (100%)"
            ),
            parse_mode="Markdown"
        )
        return

    # Group records by status display
    categorized = {}
    present_count = 0
    leave_count = 0
    medical_count = 0

    for entry in attendance_records.values():
        disp = entry["status_display"]
        if disp not in categorized:
            categorized[disp] = []
        categorized[disp].append(entry["name"])

        base_key = entry["status_key"]
        cat = STATUS_CONFIG.get(base_key, {}).get("category", "leave")
        if cat == "present":
            present_count += 1
        elif cat == "medical":
            medical_count += 1
        else:
            leave_count += 1

    unsubmitted_ids = set(NAME_MAP.keys()) - set(attendance_records.keys()) if NAME_MAP else set()
    unsubmitted_names = [NAME_MAP[uid] for uid in unsubmitted_ids]

    # Calculate percentages
    present_pct = int((present_count / total_personnel) * 100) if total_personnel else 0
    absent_pct = int(((total_personnel - present_count) / total_personnel) * 100) if total_personnel else 0
    no_response_pct = int((no_response_count / total_personnel) * 100) if total_personnel else 0

    summary = (
        f"📊 **DAILY ATTENDANCE REPORT**\n"
        f"📅 **DATE: {today_str}**\n"
        f"═══════════════════════════\n\n"
        f"📈 **STRENGTH OVERVIEW**\n"
        f"• **Total Roster Strength:** `{total_personnel}`\n"
        f"• **Present Strength:** `{present_count}/{total_personnel}` (`{present_pct}%`)\n"
        f"• **Absent / On Leave:** `{total_personnel - present_count}/{total_personnel}` (`{absent_pct}%`)\n"
        f"• **Poll Responses Received:** `{total_responses}/{total_personnel}`\n"
        f"• ⚠️ **Unaccounted / No Response:** `{no_response_count}/{total_personnel}` (`{no_response_pct}%`)\n"
        f"───────────────────────────\n\n"
    )

    present_entries = []
    leave_entries = []
    medical_entries = []
    recorded_keys = set()

    for disp_status, names in categorized.items():
        count = len(names)
        base_key = disp_status.split(" (")[0]
        recorded_keys.add(base_key)
        
        cfg = STATUS_CONFIG.get(base_key, {"emoji": "🔹", "category": "leave"})
        emoji = cfg["emoji"]
        name_str = ", ".join(f"`{n}`" for n in names)
        
        entry_str = f"  {emoji} **{disp_status}** ({count}):\n    └ {name_str}"

        if cfg["category"] == "present":
            present_entries.append(entry_str)
        elif cfg["category"] == "medical":
            medical_entries.append(entry_str)
        else:
            leave_entries.append(entry_str)

    # Render Active Status Sections
    if present_entries:
        summary += "🟢 **PRESENT / ON DUTY**\n" + "\n\n".join(present_entries) + "\n\n"

    if leave_entries:
        summary += "🌅 **LEAVE / OFF / OVERSEAS**\n" + "\n\n".join(leave_entries) + "\n\n"

    if medical_entries:
        summary += "🔴 **MEDICAL / UNEXCUSED ABSENCE**\n" + "\n\n".join(medical_entries) + "\n\n"

    # Highlight Unsubmitted Personnel
    if unsubmitted_names:
        pending_str = ", ".join(f"`{n}`" for n in unsubmitted_names)
        summary += f"⚠️ **PENDING SUBMISSION ({len(unsubmitted_names)}):**\n    └ {pending_str}\n\n"

    # Compact NIL Line
    nil_list = [key for key in STATUS_CONFIG if key not in recorded_keys]
    if nil_list:
        summary += f"⚪ **NIL REPORT:** `{', '.join(nil_list)}`\n\n"

    summary += (
        f"═══════════════════════════\n"
        f"⚡ *Generated automatically at {datetime.now(SGT).strftime('%H:%M SGT')}*"
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
    app.add_handler(CommandHandler("other", handle_custom_other_cmd))
    app.add_handler(CommandHandler("testpoll", test_poll_cmd))
    app.add_handler(CommandHandler("testsummary", test_summary_cmd))
    
    # Catch direct text messages in PM for "Others" or "Time Off"
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_private_text_reply))

    # Production Scheduler
    scheduler = AsyncIOScheduler(timezone=SGT)
    
    # 1. Send Poll at 7:00 PM SGT (Sunday to Thursday nights) for the next workday's attendance
    scheduler.add_job(
        send_attendance_poll,
        CronTrigger(day_of_week='sun-thu', hour=19, minute=0, timezone=SGT),
        kwargs={'context': app}
    )
    
    # 2. Send Summary at 8:00 AM SGT (Monday to Friday mornings)
    scheduler.add_job(
        send_consolidated_summary, 
        CronTrigger(day_of_week='mon-fri', hour=8, minute=0, timezone=SGT), 
        kwargs={'context': app}
    )

    scheduler.start()
    app.run_polling()

if __name__ == "__main__":
    main()
