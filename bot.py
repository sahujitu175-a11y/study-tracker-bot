
import os
import calendar
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, request
import telebot

# Configuration
TOKEN = os.environ["BOT_TOKEN"]
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

IST = ZoneInfo("Asia/Kolkata")
CUTOFF_HOUR = 3
TARGET_HOURS = 12

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

TABLE_URL = f"{SUPABASE_URL}/rest/v1/study_records"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

# Temporary input awaiting study hours
pending = {}


def get_study_date():
    now = datetime.now(IST)

    if now.hour < CUTOFF_HOUR:
        now -= timedelta(days=1)

    return now.date()


def get_record(user_id, date):
    response = requests.get(
        TABLE_URL,
        headers=HEADERS,
        params={
            "user_id": f"eq.{user_id}",
            "study_date": f"eq.{date}",
            "select": "*"
        },
        timeout=20
    )
    response.raise_for_status()
    records = response.json()
    return records[0] if records else None


def get_records(user_id, start_date, end_date):
    response = requests.get(
        TABLE_URL,
        headers=HEADERS,
        params={
            "user_id": f"eq.{user_id}",
            "study_date": f"gte.{start_date}",
            "study_date": f"lte.{end_date}",
            "select": "*",
            "order": "study_date.desc"
        },
        timeout=20
    )
    response.raise_for_status()
    return response.json()


def save_record(user_id, date, hours, file_id):
    data = {
        "user_id": user_id,
        "study_date": str(date),
        "study_hours": hours,
        "screenshot_file_id": file_id
    }

    response = requests.post(
        TABLE_URL,
        headers=HEADERS,
        json=data,
        timeout=20
    )

    if response.status_code == 409:
        return False

    response.raise_for_status()
    return True


def result_text(hours):
    if hours >= TARGET_HOURS:
        return f"🟢 SUCCESS! {hours:g} hours completed."
    return f"🔴 TARGET NOT COMPLETED: {hours:g} hours."


@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(
        message,
        "📚 Welcome to your 12-Hour Study Tracker!\n\n"
        "📸 Send one study screenshot each day.\n"
        "✍️ Then enter your total study hours.\n\n"
        "🟢 12 hours or more = Green tick\n"
        "🔴 Below 12 hours = Red cross\n\n"
        "📅 Your study day changes at 3:00 AM IST.\n\n"
        "Commands:\n"
        "/today - Today's record\n"
        "/month - Monthly calendar\n"
        "/history - Recent records"
    )


@bot.message_handler(commands=["today"])
def today(message):
    date = get_study_date()

    try:
        record = get_record(message.from_user.id, date)

        if not record:
            bot.reply_to(
                message,
                f"📅 Study date: {date}\n"
                "No screenshot submitted yet."
            )
            return

        hours = float(record["study_hours"])
        status = result_text(hours)

        bot.send_photo(
            message.chat.id,
            record["screenshot_file_id"],
            caption=f"📅 {date}\n⏱ {hours:g} hours\n{status}"
        )

    except Exception:
        bot.reply_to(message, "Database error. Please try again.")


@bot.message_handler(commands=["month"])
def month(message):
    today_date = get_study_date()
    year, month_number = today_date.year, today_date.month

    first_day = today_date.replace(day=1)
    last_day = today_date.replace(
        day=calendar.monthrange(year, month_number)[1]
    )

    try:
        records = get_records(
            message.from_user.id, first_day, last_day
        )

        saved = {
            record["study_date"]: float(record["study_hours"])
            for record in records
        }

        lines = [
            f"📅 {calendar.month_name[month_number]} {year}",
            "Mo Tu We Th Fr Sa Su"
        ]

        for week in calendar.monthcalendar(year, month_number):
            cells = []

            for day in week:
                if day == 0:
                    cells.append("  ")
                else:
                    date_key = f"{year}-{month_number:02d}-{day:02d}"

                    if date_key in saved:
                        symbol = "🟢" if saved[date_key] >= TARGET_HOURS else "🔴"
                        cells.append(f"{day:02d}{symbol}")
                    else:
                        cells.append(f"{day:02d} ")

            lines.append(" ".join(cells))

        lines.append("\n🟢 12 hours or more")
        lines.append("🔴 Below 12 hours")
        lines.append("Blank = No record")

        bot.reply_to(message, "\n".join(lines))

    except Exception:
        bot.reply_to(message, "Could not load the calendar. Try again.")


@bot.message_handler(commands=["history"])
def history(message):
    today_date = get_study_date()
    start_date = today_date - timedelta(days=30)

    try:
        records = get_records(
            message.from_user.id, start_date, today_date
        )

        if not records:
            bot.reply_to(message, "No study records found yet.")
            return

        lines = ["📚 Your recent study history:"]

        for record in records[:15]:
            hours = float(record["study_hours"])
            symbol = "🟢" if hours >= TARGET_HOURS else "🔴"
            lines.append(
                f"{symbol} {record['study_date']} - {hours:g} hours"
            )

        bot.reply_to(message, "\n".join(lines))

    except Exception:
        bot.reply_to(message, "Could not load history. Try again.")


@bot.message_handler(content_types=["photo"])
def receive_photo(message):
    user_id = message.from_user.id
    date = get_study_date()

    try:
        existing = get_record(user_id, date)

        if existing:
            bot.reply_to(
                message,
                f"⚠️ Screenshot already saved for {date}.\n"
                "Only one screenshot is allowed per study day."
            )
            return

        if user_id in pending:
            bot.reply_to(
                message,
                "You already have a screenshot waiting for study hours. "
                "Please enter the hours first."
            )
            return

        file_id = message.photo[-1].file_id

        pending[user_id] = {
            "date": date,
            "file_id": file_id
        }

        bot.reply_to(
            message,
            f"📅 Study date: {date}\n\n"
            "✍️ Enter your total study hours.\n"
            "Example: 12 or 12.5"
        )

    except Exception:
        bot.reply_to(message, "Could not check your record. Try again.")


@bot.message_handler(
    content_types=["text"],
    func=lambda message: not message.text.startswith("/")
)
def receive_hours(message):
    user_id = message.from_user.id

    if user_id not in pending:
        bot.reply_to(
            message,
            "First send your study screenshot 📸"
        )
        return

    try:
        hours = float(message.text.strip())

        if not 0 <= hours <= 24:
            raise ValueError

    except ValueError:
        bot.reply_to(
            message,
            "Please enter a valid number from 0 to 24.\n"
            "Example: 11.5 or 12"
        )
        return

    item = pending[user_id]

    try:
        saved = save_record(
            user_id,
            item["date"],
            hours,
            item["file_id"]
        )

        if not saved:
            pending.pop(user_id, None)
            bot.reply_to(
                message,
                "⚠️ A record already exists for this date."
            )
            return

        pending.pop(user_id, None)

        bot.send_photo(
            message.chat.id,
            item["file_id"],
            caption=(
                f"📅 Study date: {item['date']}\n"
                f"⏱ Study time: {hours:g} hours\n\n"
                f"{result_text(hours)}"
            )
        )

    except Exception:
        bot.reply_to(
            message,
            "❌ Could not save your record. Please try again."
        )


@app.get("/")
def home():
    return "Study Tracker Bot is running!"


@app.post("/telegram")
def telegram_webhook():
    update = telebot.types.Update.de_json(request.get_json())
    bot.process_new_updates([update])
    return "OK", 200


webhook_url = os.environ.get("RENDER_EXTERNAL_URL")

if webhook_url:
    bot.set_webhook(url=f"{webhook_url}/telegram")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
    
