import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, request
import telebot

TOKEN = os.environ["BOT_TOKEN"]
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

IST = ZoneInfo("Asia/Kolkata")
CUTOFF_HOUR = 3

@app.get("/")
def home():
    return "Study Tracker Bot is running!"

def study_date():
    now = datetime.now(IST)
    if now.hour < CUTOFF_HOUR:
        now -= timedelta(days=1)
    return now.date().isoformat()

@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(
        message,
        "📚 Welcome to your 12-hour Study Tracker!\n\n"
        "Send one screenshot each study day.\n"
        "I will ask you to enter your study hours.\n\n"
        "Commands:\n/today - Today's record\n"
    )

@bot.message_handler(commands=["today"])
def today(message):
    bot.reply_to(message, "Your study date is: " + study_date())

@bot.message_handler(content_types=["photo"])
def photo(message):
    bot.reply_to(
        message,
        "✍️ Enter your total study hours for this screenshot.\n"
        "Example: 12 or 12.5"
    )

@app.post("/webhook")
def webhook():
    update = telebot.types.Update.de_json(request.get_json())
    bot.process_new_updates([update])
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
