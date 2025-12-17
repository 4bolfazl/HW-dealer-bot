import math
import re
import random
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN, InvalidOperation

from dateutil import tz
from telegram import Update, constants
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

from bot.db.Database import Database
from bot.parsing import parse_volunteer
from bot.services.allocation_service import AllocationService


def trunc3(x: float) -> float:
    return math.trunc(x * 1000) / 1000.0


async def admin_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    cfg = context.bot_data["cfg"]
    admin_ids = set(cfg["admin_user_ids"])
    uid = update.effective_user.id if update.effective_user else 0
    return uid in admin_ids


async def in_allowed_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    cfg = context.bot_data["cfg"]
    chat_id = update.effective_chat.id if update.effective_chat else None
    return chat_id == cfg.get("allowed_chat_id")


def is_user_banned(update: Update, cfg: dict) -> bool:
    banned_ids = set(cfg.get("banned_user_ids", []))
    uid = update.effective_user.id if update.effective_user else None
    return uid in banned_ids if uid is not None else False


def is_student_banned(student_id, cfg: dict) -> bool:
    if student_id is None:
        return False
    banned_sids = set(str(s) for s in cfg.get("banned_student_ids", []))
    return str(student_id) in banned_sids


async def cmd_set_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /set_chat <chat_id>")
        return
    chat_id = int(context.args[0])
    context.bot_data["cfg"]["allowed_chat_id"] = chat_id
    db: Database = context.bot_data["db"]
    db.set_meta("allowed_chat_id", str(chat_id))
    await update.message.reply_text(f"✔️ گروه مجاز به {chat_id} تغییر یافت.")


async def cmd_ruok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if not await in_allowed_chat(update, context):
        return
    await update.message.reply_text("imok")


async def cmd_new_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /new_week <week_id>")
        return
    week_id = int(context.args[0])
    svc: AllocationService = context.bot_data["svc"]
    svc.new_week(week_id)
    await update.message.reply_text(f"✔️ هفته‌ی شماره‌ی {week_id} ایجاد شد.")


async def cmd_add_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /add_question <week_id> <q_number> <title in Persian>")
        return
    week_id = int(context.args[0])
    q_number = int(context.args[1])
    title = " ".join(context.args[2:])
    svc: AllocationService = context.bot_data["svc"]
    svc.add_question(week_id, q_number, title)
    await update.message.reply_text(
        f"✔️ اطلاعات زیر ثبت شد:\n\n- عنوان سوال: {title}\n- هفته: {week_id}\n- شماره‌ی سوال: {q_number}")


async def cmd_list_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /list_questions <week_id>")
        return
    week_id = int(context.args[0])
    db: Database = context.bot_data["db"]
    rows = db.get_questions_of_week(week_id)
    if not rows:
        await update.message.reply_text(f"هیچ سوالی برای هفته‌ای با شناسه‌ی {week_id} ثبت نشده است.")
        return
    lines = [f"سوال {r['q_number']} - {r['title']}" for r in rows]
    await update.message.reply_text(f"سوالات ثبت‌شده برای هفته‌ی {week_id}:\n\n" + "\n".join(lines))


async def cmd_set_window(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if len(context.args) not in (1, 2):
        await update.message.reply_text("Usage: /set_window <week_id> [YYYY-MM-DDTHH:MM:SS]")
        return
    week_id = int(context.args[0])

    for job in context.job_queue.get_jobs_by_name(f"finalize_week_{week_id}"):
        job.schedule_removal()

    cfg = context.bot_data["cfg"]
    tzinfo = tz.gettz(cfg["timezone"])
    start_dt = None
    if len(context.args) == 2:
        start_dt = datetime.fromisoformat(context.args[1]).replace(tzinfo=tzinfo)
    svc: AllocationService = context.bot_data["svc"]
    start_ts, end_ts = svc.set_window(week_id, start_dt)
    start_str = datetime.fromtimestamp(start_ts, tzinfo).strftime("%Y-%m-%d %H:%M:%S")
    end_str = datetime.fromtimestamp(end_ts, tzinfo).strftime("%Y-%m-%d %H:%M:%S")

    when_dt = datetime.fromtimestamp(end_ts, tzinfo)
    context.job_queue.run_once(finalize_job, when=when_dt, data={"week_id": week_id, "my_bot_update": update},
                               name=f"finalize_week_{week_id}")

    await update.message.reply_text(
        f"🟢 پنجره ثبت داوطلبی برای هفته‌ی {week_id} در بازه‌ی زمانی زیر باز خواهد بود:\n\n- شروع: {start_str}\n- پایان: {end_str}")


async def cmd_force_finalize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /finalize <week_id>")
        return
    week_id = int(context.args[0])
    await do_finalize_and_post(update, context, week_id)


async def do_finalize_and_post(update: Update, context: ContextTypes.DEFAULT_TYPE, week_id: int):
    svc: AllocationService = context.bot_data["svc"]
    db: Database = context.bot_data["db"]
    cfg: dict = context.bot_data["cfg"]
    if not svc.is_window_open(week_id):
        return
    db.set_end_ts(week_id,
                  int((datetime.now(tz.gettz(context.bot_data["cfg"]["timezone"])) - timedelta(minutes=1)).timestamp()))

    for job in context.job_queue.get_jobs_by_name(f"finalize_week_{week_id}"):
        job.schedule_removal()

    finals = svc.finalize_week(week_id)
    if not finals:
        await context.bot.send_message(chat_id=cfg["allowed_chat_id"],
                                       text=f"📌 برای هفته‌ی {week_id} هیچ تخصیصی ثبت نشد!")
        return

    lines = [f"🏁 خلاصه‌ی تخصیص هفته‌ی {week_id}:\n"]
    for row in finals:
        uid = row["telegram_user_id"]
        mention = f"<a href=\"tg://user?id={uid}\">{row['full_name']}</a>" if uid else row["full_name"]
        lines.append(
            f"سوال {row['q_number']}: {row['title']}\n" +
            f"{mention} ({row['student_id']})\n" +
            f"تعداد داوطلبی موفق تا کنون: {row['successful_count'] + 1}\n"
        )
    await context.bot.send_message(chat_id=cfg["allowed_chat_id"], text="\n".join(lines),
                                   parse_mode=constants.ParseMode.HTML)


async def cmd_reserved_price_auction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return

    if len(context.args) != 1:
        await update.message.reply_text("Usage: /start_reserved_price_auction <auction_name>")
        return

    auction_name = context.args[0].strip()
    cfg: dict = context.bot_data["cfg"]
    tzinfo = tz.gettz(cfg["timezone"])
    auctions: dict = context.bot_data["auctions"]

    old = auctions.get(auction_name)
    if old and not old.get("is_closed", False):
        for job in context.job_queue.get_jobs_by_name(f"auction_finish_{auction_name}"):
            job.schedule_removal()
        old["is_closed"] = True

    window_min = int(cfg["auction_window_minutes"])
    reserved_price = Decimal(cfg["reserved_price"])
    min_decrement = Decimal(cfg["min_decrement"])

    start_dt = datetime.now(tzinfo)
    end_dt = start_dt + timedelta(minutes=window_min)

    auctions[auction_name] = {
        "name": auction_name,
        "start_dt": start_dt,
        "end_dt": end_dt,
        "reserved_price": reserved_price,
        "min_decrement": min_decrement,
        "current_price": reserved_price,
        "winner_user_id": None,
        "winner_display_name": None,
        "is_closed": False,
    }

    context.job_queue.run_once(
        auction_finalize_job,
        when=end_dt,
        data={"auction_name": auction_name, "my_bot_update": update},
        name=f"auction_finish_{auction_name}",
    )

    await context.bot.send_message(
        chat_id=cfg["allowed_chat_id"],
        text=(
            f"🔔 <b>مناقصه‌ی {auction_name} شروع شد</b>!\n"
            f"• <b>قیمت شروع:</b> {reserved_price} نمره\n"
            f"• <b>حداقل کاهش هر پیشنهاد:</b> {min_decrement} نمره نسبت به قیمت فعلی\n\n"
            f"📜 <b>قوانین مناقصه:</b>\n"
            f"از زمان پذیرفته شدن هر پیشنهاد، یک <b>پنجره‌ی ۳۰ ثانیه‌ای</b> برای ارسال پیشنهادهای جدید آغاز می‌شود.\n"
            f"هر <b>۱۰ ثانیه</b> وضعیت اعلام می‌شود.\n"
            f"اگر تا پایان ۳۰ ثانیه هیچ پیشنهاد معتبری ثبت نشود، همان پیشنهاد <b>برنده</b> خواهد بود.\n"
            f"همچنین اگر بیشترین پیشنهاد ممکنِ بعدی منجر به <b>سود نامثبت</b> شود، مناقصه همان‌جا خاتمه می‌یابد.\n\n"
            f"📝 <b>فرمت ارسال پیشنهاد:</b>\n"
            f"<code>/bid {auction_name} &lt;student_id&gt; &lt;price&gt;</code>\n\n"
            f"توجه: هر پیشنهاد باید حداقل به اندازه‌ی <b>{min_decrement}</b> نمره از قیمت فعلی کمتر باشد؛ "
            f"در غیر این صورت پذیرفته نخواهد شد."
        ),
        parse_mode=constants.ParseMode.HTML,
    )


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await in_allowed_chat(update, context):
        return

    cfg: dict = context.bot_data["cfg"]
    if is_user_banned(update, cfg):
        return

    db: Database = context.bot_data["db"]

    week_id = db.get_active_week(datetime.now(tz.gettz(context.bot_data["cfg"]["timezone"])))
    if week_id is None:
        return

    if not update.message or not update.message.text:
        return

    svc: AllocationService = context.bot_data["svc"]
    text = update.message.text
    vmsg = parse_volunteer(text, week_id)
    if not vmsg:
        return

    if is_student_banned(vmsg.student_id, cfg):
        return

    msg_ts = int(update.message.date.timestamp())
    res = svc.try_assign(vmsg, update.effective_user.id, msg_ts)
    if res:
        vol_count = res["vol_count"]
        ordinal_map = {
            0: "اول",
            1: "دوم",
            2: "سوم",
            3: "چهارم",
            4: "پنجم",
            5: "ششم",
            6: "هفتم",
            7: "هشتم",
            8: "نهم",
            9: "دهم",
        }

        ordinal = ordinal_map.get(vol_count, "-")
        await update.message.reply_text(
            f"✅ سوال {res['q_number']} - {res['title']} موقتاً به شما اختصاص یافت.\n"
            f"در صورت نهایی شدن، این {ordinal}ین داوطلبی موفق شما خواهد بود."
        )

        if svc.maybe_early_stop(week_id):
            await do_finalize_and_post(update, context, week_id)


# --- JobQueue callbacks ---
async def finalize_job(context: ContextTypes.DEFAULT_TYPE):
    """Auto-finalize at scheduled end time."""
    job = context.job
    week_id = job.data["week_id"]
    update = job.data["my_bot_update"]
    await do_finalize_and_post(update, context, week_id)


# -------------------------- START AUCTION --------------------------
COUNTDOWN_INTERVAL_SEC = 10


def _cancel_countdown_jobs(job_queue, auction_name: str):
    # We use a single job name per auction for the whole countdown chain.
    for job in job_queue.get_jobs_by_name(f"auction_countdown_{auction_name}"):
        job.schedule_removal()


def schedule_auction_countdown(context: ContextTypes.DEFAULT_TYPE, auction_name: str):
    """
    Cancels any previous countdown for this auction and starts a fresh one.
    Uses a monotonically increasing countdown_id to ignore stale jobs
    that might slip through before cancellation.
    """
    auctions: dict = context.bot_data["auctions"]
    st = auctions.get(auction_name)
    if not st or st.get("is_closed", False):
        return

    # bump a version to invalidate old jobs (extra safety beyond cancel)
    st["countdown_id"] = int(st.get("countdown_id", 0)) + 1

    _cancel_countdown_jobs(context.job_queue, auction_name)

    context.job_queue.run_once(
        auction_countdown_job,
        when=timedelta(seconds=COUNTDOWN_INTERVAL_SEC),
        data={
            "auction_name": auction_name,
            "step": 1,
            "countdown_id": st["countdown_id"],
        },
        name=f"auction_countdown_{auction_name}",
    )


async def auction_countdown_job(context: ContextTypes.DEFAULT_TYPE):
    """
    step=1  -> post 'count 1'
    step=2  -> post 'count 2'
    step=3  -> finalize auction (no post needed beyond winner announcement)
    Any new accepted bid increments st['countdown_id'] and reschedules,
    so stale jobs auto-abort when ids don't match.
    """
    data = context.job.data or {}
    auction_name: str = data.get("auction_name")
    step: int = int(data.get("step", 1))
    countdown_id: int = int(data.get("countdown_id", -1))

    cfg: dict = context.bot_data["cfg"]
    auctions: dict = context.bot_data["auctions"]
    st = auctions.get(auction_name)

    # state checks
    if not st or st.get("is_closed", False):
        return
    if st.get("countdown_id") != countdown_id:
        # stale job; a newer bid reset the countdown
        return

    # double-check window expiry; if expired, finalize (upper bound safety)
    tzinfo = tz.gettz(cfg["timezone"])
    now = datetime.now(tzinfo)
    if now >= st["end_dt"]:
        st["is_closed"] = True
        # cancel any running countdown job (this one will end anyway)
        _cancel_countdown_jobs(context.job_queue, auction_name)
        await announce_auction_winner(context, auction_name)
        return

    # Prepare mention + status text for steps 1 and 2.
    if step in (1, 2):
        if st["winner_user_id"] is not None:
            mention = f'<a href="tg://user?id={st["winner_user_id"]}">{st["winner_display_name"]}</a>'
        else:
            mention = st.get("winner_display_name") or "—"

        await context.bot.send_message(
            chat_id=cfg["allowed_chat_id"],
            text=(
                f"📣 وضعیت فعلی مناقصه «{auction_name}»\n\n"
                f"رهبر فعلی: {mention}\n"
                f"قیمت فعلی: {st['current_price']}\n"
                f"شمارش: {step}"
            ),
            parse_mode=constants.ParseMode.HTML,
        )

        # schedule next step
        context.job_queue.run_once(
            auction_countdown_job,
            when=timedelta(seconds=COUNTDOWN_INTERVAL_SEC),
            data={
                "auction_name": auction_name,
                "step": step + 1,
                "countdown_id": countdown_id,
            },
            name=f"auction_countdown_{auction_name}",
        )
        return

    # step == 3  → finalize
    # close and cancel the "window" finalizer to avoid double announce
    st["is_closed"] = True
    _cancel_countdown_jobs(context.job_queue, auction_name)
    for job in context.job_queue.get_jobs_by_name(f"auction_finish_{auction_name}"):
        job.schedule_removal()
    await announce_auction_winner(context, auction_name)


async def cmd_bid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await in_allowed_chat(update, context):
        return

    if len(context.args) != 3:
        return

    PERSIAN_ARABIC_DIGITS = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789"
    )
    raw = context.args[1].strip()
    normalized = raw.translate(PERSIAN_ARABIC_DIGITS)
    NUMBER_PATTERN = re.compile(r"^\d+$")

    if not NUMBER_PATTERN.match(normalized):
        return

    auction_name = context.args[0].strip()
    try:
        bid_raw = context.args[2].strip()
        bid_normalized = bid_raw.translate(PERSIAN_ARABIC_DIGITS)
        bid_value = Decimal(bid_normalized)
    except InvalidOperation:
        return

    bid_value = bid_value.quantize(Decimal("0.001"), rounding=ROUND_DOWN)
    cfg: dict = context.bot_data["cfg"]
    tzinfo = tz.gettz(cfg["timezone"])
    auctions: dict = context.bot_data["auctions"]
    st = auctions.get(auction_name)

    if is_user_banned(update, cfg):
        return

    if not st:
        return
    if st.get("is_closed", False):
        return

    now = datetime.now(tzinfo)
    if now >= st["end_dt"]:
        st["is_closed"] = True
        return

    current_price = Decimal(str(st["current_price"]))
    if "min_decrement" not in st:
        return
    min_dec = Decimal(str(st["min_decrement"]))

    acceptable_max = (current_price - min_dec).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
    if bid_value > acceptable_max:
        return

    if bid_value > acceptable_max:
        return

    # --- accepted bid: record winner and notify
    st["current_price"] = bid_value
    st["winner_user_id"] = update.effective_user.id if update.effective_user else None
    disp = update.effective_user.full_name if update.effective_user else "کاربر"
    st["winner_display_name"] = disp

    await update.message.reply_text(
        f"✅ پیشنهاد پذیرفته شد: {bid_value}\n\n در صورتی که تا ۳۰ ثانیه‌ی آینده پیشنهادی معتبری ارسال نشود، شما برنده‌ی این مناقصه خواهید بود.",
        parse_mode=constants.ParseMode.HTML,
    )

    # --- NEW: reset/start the countdown after an accepted bid
    schedule_auction_countdown(context, auction_name)

    # If next possible bid would be non-positive, finish immediately.
    next_possible = (bid_value - min_dec).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
    if next_possible <= 0:
        # cancel countdown + any scheduled time-window finalizer
        _cancel_countdown_jobs(context.job_queue, auction_name)
        for job in context.job_queue.get_jobs_by_name(f"auction_finish_{auction_name}"):
            job.schedule_removal()
        st["is_closed"] = True
        await announce_auction_winner(context, auction_name)


async def auction_finalize_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    auction_name = job.data["auction_name"]
    auctions: dict = context.bot_data["auctions"]
    st = auctions.get(auction_name)
    if not st:
        return
    if st.get("is_closed", False):
        return
    st["is_closed"] = True
    await announce_auction_winner(context, auction_name)


async def announce_auction_winner(context: ContextTypes.DEFAULT_TYPE, auction_name: str):
    cfg: dict = context.bot_data["cfg"]
    auctions: dict = context.bot_data["auctions"]
    st = auctions.get(auction_name)
    if not st:
        return
    if st.get("is_closed", False) is not True:
        st["is_closed"] = True

    if st["winner_user_id"] is None:
        await context.bot.send_message(
            chat_id=cfg["allowed_chat_id"],
            text=f"📌 مناقصه‌ی {auction_name} بدون برنده به پایان رسید.",
        )
        return

    mention = f'<a href="tg://user?id={st["winner_user_id"]}">{st["winner_display_name"]}</a>'
    caption = (
        f"🏁 مناقصه‌ی «{auction_name}» به پایان رسید!\n\n"
        f"برنده: {mention}\n"
        f"قیمت نهایی: {st['current_price']}"
    )

    await context.bot.send_animation(
        chat_id=cfg["allowed_chat_id"],
        animation="https://gifdb.com/images/high/sold-dancing-chihuahua-7o32vsm28i7116a2.gif",
        caption=caption,
        parse_mode=constants.ParseMode.HTML,
    )


# -------------------------- END   AUCTION --------------------------

# ----------------------- SECOND PRICE AUCTION ----------------------

async def second_auction_finalize_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    auction_name = job.data["auction_name"]
    auctions: dict = context.bot_data["auctions"]
    st = auctions.get(auction_name)
    if not st:
        return
    if st.get("is_closed", False):
        return
    st["is_closed"] = True
    await second_announce_auction_winner(context, auction_name)


async def second_announce_auction_winner(context: ContextTypes.DEFAULT_TYPE, auction_name: str):
    cfg: dict = context.bot_data["cfg"]
    auctions: dict = context.bot_data["auctions"]
    st = auctions.get(auction_name)
    if not st:
        return
    if st.get("is_closed", False) is not True:
        st["is_closed"] = True

    bids_dict: dict = st["bids"]
    if not bids_dict:
        await context.bot.send_message(
            chat_id=cfg["allowed_chat_id"],
            text=f"📌 مناقصه‌ی {auction_name} بدون برنده به پايان رسيد.",
        )
        return


    # bids_dict: student_id -> [bid_value, telegram_user_id, display_name]
    bids = []
    for student_id, bid_info in bids_dict.items():
        bid_value, user_id, display_name = bid_info
        bids.append(
            {
                "student_id": student_id,
                "bid_value": Decimal(bid_value),
                "user_id": user_id,
                "display_name": display_name,
            }
        )

    # مقادير بيد يکتا و مرتب شده (از کم به زياد)
    distinct_values = sorted([b["bid_value"] for b in bids])
    if not distinct_values:
        await context.bot.send_message(
            chat_id=cfg["allowed_chat_id"],
            text=f"📌 مناقصه‌ی {auction_name} بدون برنده به پايان رسيد.",
        )
        return

    min_bid = distinct_values[0]
    # همه کساني که کمترين بيد را داده اند
    min_candidates = [b for b in bids if b["bid_value"] == min_bid]

    # انتخاب يکنواخت تصادفي بين کساني که کمترين بيد را داده اند
    winner = random.choice(min_candidates)

    # قيمت نهايي: دومين قيمت کمتر بعد از حذف مقادير تکراري
    if len(distinct_values) >= 2:
        final_price = distinct_values[1]
    else:
        # اگر فقط يک قيمت يکتا وجود دارد، قيمت نهايي را همان کمترين قيمت در نظر ميگيريم
        final_price = min_bid

    # به روز کردن وضعيت در st (در صورت نياز جاهاي ديگه استفاده شود)
    st["winner_user_id"] = winner["user_id"]
    st["winner_display_name"] = winner["display_name"]
    st["winner_student_id"] = winner["student_id"]
    st["current_price"] = final_price
    st["min_bid_value"] = min_bid

    mention = f'<a href="tg://user?id={winner["user_id"]}">{winner["display_name"]}</a>'

    caption = (
        f"🏁 مناقصه‌ی قيمت دوم «{auction_name}» به پايان رسيد!\n\n"
        f"برنده: {mention}\n"
        f"شماره‌ی دانشجويی برنده: <code>{winner['student_id']}</code>\n"
        f"کمترين بيد ثبت شده: {min_bid:.3f}\n"
        f"قيمت نهايی (دومين کمترین قیمت): {final_price:.3f}"
    )

    await context.bot.send_animation(
        chat_id=cfg["allowed_chat_id"],
        animation="https://media1.tenor.com/m/UVdyTjo2DHAAAAAC/leonardo-dicaprio-sold-gif.gif",
        caption=caption,
        parse_mode=constants.ParseMode.HTML,
    )

    #------------------

async def cmd_second_price_auction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return

    if len(context.args) != 1:
        await update.message.reply_text("Usage: /start_second_price_auction <auction_name>")
        return

    auction_name = context.args[0].strip()
    cfg: dict = context.bot_data["cfg"]
    tzinfo = tz.gettz(cfg["timezone"])
    auctions: dict = context.bot_data["auctions"]

    old = auctions.get(auction_name)
    if old and not old.get("is_closed", False):
        for job in context.job_queue.get_jobs_by_name(f"auction_finish_{auction_name}"):
            job.schedule_removal()
        old["is_closed"] = True

    window_min = int(cfg["second_auction_window_minutes"])
    reserved_price = Decimal(3)

    start_dt = datetime.now(tzinfo)
    end_dt = start_dt + timedelta(minutes=window_min)

    auctions[auction_name] = {
        "name": auction_name,
        "start_dt": start_dt,
        "end_dt": end_dt,
        "reserved_price": reserved_price,
        "current_price": reserved_price,
        "winner_user_id": None,
        "winner_display_name": None,
        "is_closed": False,
        "bids" : {}
    }

    context.job_queue.run_once(
        second_auction_finalize_job,
        when=end_dt,
        data={"auction_name": auction_name, "my_bot_update": update},
        name=f"auction_finish_{auction_name}",
    )

    await context.bot.send_message(
        chat_id=cfg["allowed_chat_id"],
        text=(
            f"🔔 <b>مناقصه‌ی قيمت دوم {auction_name} شروع شد</b>!\n\n"
            f"• <b>حداکثر پیشنهاد مجاز:</b> {reserved_price} نمره\n"
            f"• <b>حداقل پیشنهاد مجاز:</b> 0 نمره\n\n"   
            f"⏱️ <b>پنجره‌ی زمانی ارسال پیشنهاد:</b>\n"
            f"از اين لحظه يک پنجره‌ی <b>{window_min} دقيقه‌ای</b> برای ارسال پیشنهادها باز است.\n"
            f"در اين مدت هر دانشجو می‌تواند <b>يک پیشنهاد</b> ثبت کند.\n"
            f"<b>حتما</b> پیشنهاد خود را به صورت <b>خصوصی</b> برای ربات بفرستيد؛\n"
            f"ارسال پیشنهاد در گروه معتبر نيست و در نظر گرفته نمی‌شود.\n\n"
            f"📝 <b>فرمت ارسال پیشنهاد:</b>\n"
            f"<code>/pbid {auction_name} &lt;student_id&gt; &lt;price&gt;</code>\n\n"
            f"• مقدار <code>&lt;price&gt;</code> بايد با دقت <b>سه رقم اعشار</b> وارد شود.\n"
            f"• اگر بيش از سه رقم اعشار وارد کنيد، مقدار پیشنهاد شما به روش <b>قطع کردن</b>"
            f" تا سه رقم اعشار بريده می‌شود.\n"
            f"• در صورتی که پیشنهاد شما معتبر باشد، پیام تاییدیه‌ای برای شما ارسال خواهد شد.\n"
            f"• ملاک شناسايی شما <b>شماره‌ی دانشجويی</b> است؛ از درست وارد کردن آن مطمئن باشيد.\n"
            f"• پس از ارسال يک پیشنهاد با يک شماره‌ی دانشجويی، <b>امکان تغيير آن وجود ندارد</b>.\n\n"
            f"🏆 <b>نحوه‌ی تعيين برنده:</b>\n"
            f"• برنده کسی است که <b>کمترين پیشنهاد</b> معتبر را ثبت کرده باشد.\n"
            f"• قيمت پرداختی برنده برابر <b>دومين کمترین قیمت</b> ميان قیمت‌های معتبر است.\n"
            f"• در صورت مساوی بودن کمترين بيد بين چند دانشجو،"
            f" قيمت‌های تکراری يکسان به عنوان يک قيمت در نظر گرفته نمی‌شوند"
            f" و از بين دانشجويانی که کمترين قيمت را داده‌اند به صورت <b>تصادفی یکنواخت</b>"
            f" يک نفر به عنوان برنده انتخاب می‌شود و قيمت پرداختی او برابر <b>دومين قيمت کمينه</b>"
            f" (بدون حذف قيمت‌های تکراری) خواهد بود."
        ),
        parse_mode=constants.ParseMode.HTML
    )


async def cmd_pbid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 3:
        return

    PERSIAN_ARABIC_DIGITS = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789"
    )
    raw = context.args[1].strip()
    normalized = raw.translate(PERSIAN_ARABIC_DIGITS)
    NUMBER_PATTERN = re.compile(r"^\d+$")

    if not NUMBER_PATTERN.match(normalized):
        return

    auction_name = context.args[0].strip()
    try:
        bid_raw = context.args[2].strip()
        bid_normalized = bid_raw.translate(PERSIAN_ARABIC_DIGITS)
        bid_value = Decimal(bid_normalized)
    except InvalidOperation:
        return

    bid_value = bid_value.quantize(Decimal("0.001"), rounding=ROUND_DOWN)
    cfg: dict = context.bot_data["cfg"]
    tzinfo = tz.gettz(cfg["timezone"])
    auctions: dict = context.bot_data["auctions"]
    st = auctions.get(auction_name)
    if "min_decrement" in st:
        return
    bids: dict = st["bids"]
    reserved_price = Decimal(st["reserved_price"])

    if is_user_banned(update, cfg):
        return

    if not st:
        return
    if st.get("is_closed", False):
        return

    now = datetime.now(tzinfo)
    if now >= st["end_dt"]:
        st["is_closed"] = True
        return

    if bid_value > reserved_price or bid_value < 0:
        return

    # --- accepted bid: record winner and notify
    # st["winner_user_id"] = update.effective_user.id if update.effective_user else None
    # disp = update.effective_user.full_name if update.effective_user else "کاربر"
    # st["winner_display_name"] = disp
    user_id = update.effective_user.id if update.effective_user else None
    disp = update.effective_user.full_name if update.effective_user else "کاربر"
    if str(normalized) not in bids:
        bids[str(normalized)] = [Decimal(bid_value), user_id, disp]
    else:
        return

    await update.message.reply_text(
        f"✅ پیشنهاد پذیرفته شد: {bid_value}",
        parse_mode=constants.ParseMode.HTML,
    )

    await update.message.forward(
        cfg["lab_chat_id"]
    )

    # --- NEW: reset/start the countdown after an accepted bid
    # schedule_auction_countdown(context, auction_name)

    # If next possible bid would be non-positive, finish immediately.
    # next_possible = (bid_value - min_dec).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
    # if next_possible <= 0:
        # cancel countdown + any scheduled time-window finalizer
        # _cancel_countdown_jobs(context.job_queue, auction_name)
        # for job in context.job_queue.get_jobs_by_name(f"auction_finish_{auction_name}"):
        #     job.schedule_removal()
        # st["is_closed"] = True
        # await announce_auction_winner(context, auction_name)


# ----------------------- SECOND PRICE AUCTION ----------------------

async def build_application(cfg: dict, db: Database):
    app = ApplicationBuilder().token(cfg["bot_token"]).build()

    svc = AllocationService(db, cfg["timezone"], cfg["window_minutes"])

    app.bot_data["cfg"] = cfg
    app.bot_data["db"] = db
    app.bot_data["svc"] = svc
    app.bot_data["auctions"] = {}

    app.add_handler(CommandHandler("set_chat", cmd_set_chat))
    app.add_handler(CommandHandler("new_week", cmd_new_week))
    app.add_handler(CommandHandler("add_question", cmd_add_question))
    app.add_handler(CommandHandler("list_questions", cmd_list_questions))
    app.add_handler(CommandHandler("set_window", cmd_set_window))
    app.add_handler(CommandHandler("finalize", cmd_force_finalize))
    app.add_handler(CommandHandler("ruok", cmd_ruok))

    app.add_handler(CommandHandler("start_reserved_price_auction", cmd_reserved_price_auction))
    app.add_handler(CommandHandler("start_second_price_auction", cmd_second_price_auction))
    app.add_handler(CommandHandler("bid", cmd_bid, filters=(filters.UpdateType.MESSAGE & filters.ChatType.GROUPS)))
    app.add_handler(CommandHandler("pbid", cmd_pbid, filters=(filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE)))

    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.TEXT & filters.UpdateType.MESSAGE & ~filters.COMMAND,
                       on_message))

    return app
