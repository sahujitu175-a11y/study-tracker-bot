import hashlib
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from functools import wraps

import requests
import telebot
from flask import Flask, abort, request

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("study-bot")

# ---------- Config (set these as environment variables on Render) ----------
BOT_TOKEN = os.environ["BOT_TOKEN"]
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]  # service_role key (keep secret)
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
ALLOWED_USER_ID = os.environ.get("ALLOWED_USER_ID")  # optional: lock bot to you

IST = timezone(timedelta(hours=5, minutes=30))  # IST has no daylight saving
CUTOFF_HOUR = 3  # a "study day" ends at 3:00 AM IST
GOAL_HOURS = 12
TABLE = "study_logs"
WEBHOOK_SECRET = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:32]

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = Flask(__name__)

# user_id -> {"date": date, "file_id": str}; waiting for the hours reply
PENDING = {}


# ---------- Helpers ----------
def study_date(now=None):
    """Study date with the 3:00 AM IST cutoff (1:30 AM on the 26th counts as the 25th)."""
    now = now or datetime.now(IST)
    return (now - timedelta(hours=CUTOFF_HOUR)).date()


def icon(hours):
    return "🟢" if float(hours) >= GOAL_HOURS else "🔴"


def fmt(hours):
    return f"{float(hours):g}h"


def parse_hours(text):
    """Accepts 12, 11.5, 12h, 12 hrs, 11:30. Returns float in [0, 24] or None."""
    if not text:
        return None
    t = text.strip().lower()
    m = re.fullmatch(r"(\d{1,2})\s*:\s*([0-5]\d)", t)
    if m:
        val = int(m.group(1)) + int(m.group(2)) / 60
    else:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)?", t)
        if not m:
            return None
        val = float(m.group(1))
    if 0 <= val <= 24:
        return round(val, 2)
    return None


# ---------- Supabase (plain REST, no extra library) ----------
def _headers(extra=None):
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def db_select(params):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{TABLE}", headers=_headers(), params=params, timeout=15
    )
    r.raise_for_status()
    return r.json()


def db_insert(row):
    """Returns False if that user+date already exists (unique constraint)."""
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{TABLE}",
        headers=_headers({"Prefer": "return=minimal"}),
        json=row,
        timeout=15,
    )
    if r.status_code == 409:
        return False
    r.raise_for_status()
    return True


def get_entry(user_id, d):
    rows = db_select(
        {"user_id": f"eq.{user_id}", "study_date": f"eq.{d.isoformat()}", "select": "*"}
    )
    return rows[0] if rows else None


# ---------- Handler guard ----------
def guarded(fn):
    @wraps(fn)
    def wrapper(message):
        if ALLOWED_USER_ID and str(message.from_user.id) != ALLOWED_USER_ID:
            return
        try:
            fn(message)
        except Exception:
            log.exception("handler failed")
            bot.reply_to(message, "⚠️ Something went wrong. Please try again.")

    return wrapper


def duplicate_text(d, entry):
    return (
        f"⛔ {d:%d %b %Y} is already logged: {icon(entry['hours'])} {fmt(entry['hours'])}.\n"
        "Only one screenshot is allowed per study date."
    )


def finish(message, uid, d, hours, file_id):
    ok = db_insert(
        {
            "user_id": uid,
            "study_date": d.isoformat(),
            "hours": hours,
            "file_id": file_id,
        }
    )
    if not ok:
        entry = get_entry(uid, d)
        bot.reply_to(message, duplicate_text(d, entry) if entry else "⛔ Already logged.")
        return
    note = "Goal reached! 🎉" if hours >= GOAL_HOURS else f"{GOAL_HOURS - hours:g}h short of the {GOAL_HOURS}h goal."
    bot.reply_to(message, f"{icon(hours)} Saved {d:%d %b %Y}: {fmt(hours)}\n{note}")


def handle_screenshot(message, file_id):
    uid = message.from_user.id
    d = study_date()
    entry = get_entry(uid, d)
    if entry:
        PENDING.pop(uid, None)
        bot.reply_to(message, duplicate_text(d, entry))
        return
    hours = parse_hours(message.caption)
    if hours is not None:
        finish(message, uid, d, hours, file_id)
        return
    PENDING[uid] = {"date": d, "file_id": file_id}
    bot.reply_to(
        message,
        f"📸 Screenshot received for {d:%d %b %Y}.\n"
        "How many hours did you study? Reply with a number like 12, 11.5 or 11:30.\n"
        "(/cancel to discard)",
    )


# ---------- Commands ----------
@bot.message_handler(commands=["start", "help"])
@guarded
def cmd_start(message):
    bot.reply_to(
        message,
        "📚 Study Tracker Bot\n\n"
        "1. Send your study-time screenshot (one per day).\n"
        "2. Reply with the hours (e.g. 12.5), or put the hours in the photo caption.\n\n"
        f"🟢 = {GOAL_HOURS}+ hours   🔴 = under {GOAL_HOURS} hours\n"
        f"A study day ends at {CUTOFF_HOUR}:00 AM IST.\n\n"
        "/today – today's status\n"
        "/month – this month (or /month 2026-10)\n"
        "/history – last 20 entries\n"
        "/cancel – discard a pending screenshot",
    )


@bot.message_handler(commands=["cancel"])
@guarded
def cmd_cancel(message):
    if PENDING.pop(message.from_user.id, None):
        bot.reply_to(message, "Cancelled. Nothing was saved.")
    else:
        bot.reply_to(message, "Nothing to cancel.")


@bot.message_handler(commands=["today"])
@guarded
def cmd_today(message):
    d = study_date()
    entry = get_entry(message.from_user.id, d)
    if entry:
        bot.reply_to(message, f"{icon(entry['hours'])} {d:%d %b %Y}: {fmt(entry['hours'])}")
    else:
        bot.reply_to(
            message,
            f"🔴 {d:%d %b %Y}: not logged yet.\nSend your screenshot to log today.",
        )


@bot.message_handler(commands=["month"])
@guarded
def cmd_month(message):
    parts = (message.text or "").split()
    try:
        if len(parts) > 1:
            y, m = parts[1].split("-")
            start = date(int(y), int(m), 1)
        else:
            start = study_date().replace(day=1)
    except ValueError:
        bot.reply_to(message, "Usage: /month or /month 2026-10")
        return
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)

    rows = db_select(
        [
            ("user_id", f"eq.{message.from_user.id}"),
            ("study_date", f"gte.{start.isoformat()}"),
            ("study_date", f"lt.{end.isoformat()}"),
            ("order", "study_date.asc"),
            ("select", "study_date,hours"),
        ]
    )
    if not rows:
        bot.reply_to(message, f"📅 {start:%B %Y}\nNo entries yet.")
        return

    lines = [f"📅 {start:%B %Y}"]
    total = 0.0
    green = 0
    for r in rows:
        d = date.fromisoformat(r["study_date"])
        h = float(r["hours"])
        total += h
        green += h >= GOAL_HOURS
        lines.append(f"{icon(h)} {d:%d %b} – {fmt(h)}")
    lines.append("")
    lines.append(
        f"Days logged: {len(rows)} | 🟢 {green} | 🔴 {len(rows) - green}\n"
        f"Total: {total:g}h | Avg: {total / len(rows):.1f}h/day"
    )
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=["history"])
@guarded
def cmd_history(message):
    rows = db_select(
        [
            ("user_id", f"eq.{message.from_user.id}"),
            ("order", "study_date.desc"),
            ("limit", "20"),
            ("select", "study_date,hours"),
        ]
    )
    if not rows:
        bot.reply_to(message, "No history yet. Send your first screenshot!")
        return
    lines = ["🗂 Last entries"]
    for r in rows:
        d = date.fromisoformat(r["study_date"])
        lines.append(f"{icon(r['hours'])} {d:%d %b %Y} – {fmt(r['hours'])}")
    bot.reply_to(message, "\n".join(lines))


# ---------- Screenshots and hours ----------
@bot.message_handler(content_types=["photo"])
@guarded
def on_photo(message):
    handle_screenshot(message, message.photo[-1].file_id)


@bot.message_handler(
    content_types=["document"],
    func=lambda m: (m.document.mime_type or "").startswith("image/"),
)
@guarded
def on_image_document(message):
    handle_screenshot(message, message.document.file_id)


@bot.message_handler(
    func=lambda m: m.content_type == "text" and not (m.text or "").startswith("/")
)
@guarded
def on_text(message):
    uid = message.from_user.id
    pend = PENDING.get(uid)
    if not pend:
        bot.reply_to(message, "Send your study-time screenshot first 📸 (/help for commands).")
        return
    hours = parse_hours(message.text)
    if hours is None:
        bot.reply_to(message, "Please send hours as a number from 0 to 24, e.g. 12 or 11.5.")
        return
    PENDING.pop(uid, None)
    finish(message, uid, pend["date"], hours, pend["file_id"])


# ---------- Web app / webhook ----------
@app.route("/")
def home():
    return "Study Tracker Bot is running!"


@app.route("/webhook", methods=["POST"])
def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        abort(403)
    update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
    bot.process_new_updates([update])
    return "OK", 200


def setup_webhook():
    if not BASE_URL:
        log.warning("RENDER_EXTERNAL_URL not set; webhook not registered")
        return
    try:
        bot.remove_webhook()
        bot.set_webhook(url=f"{BASE_URL}/webhook", secret_token=WEBHOOK_SECRET)
        log.info("Webhook set to %s/webhook", BASE_URL)
    except Exception:
        log.exception("Failed to set webhook")


setup_webhook()
