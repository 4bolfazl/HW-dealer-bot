from datetime import datetime, timedelta
from typing import Optional, Tuple

from dateutil import tz

from bot.db.Database import Database
from bot.models import VolunteerMessage


class AllocationService:
    def __init__(self, db: Database, tz_name: str, window_minutes: int):
        self.db = db
        self.tz = tz.gettz(tz_name)
        self.window_minutes = window_minutes

    # --- Admin ops ---
    def new_week(self, week_id: int):
        self.db.new_week(week_id)

    def add_question(self, week_id: int, q_number: int, title: str):
        self.db.add_question_to_week(week_id, q_number, title)

    def set_window(self, week_id: int, start_dt_local: Optional[datetime] = None) -> Tuple[int, int]:
        if start_dt_local is None:
            start_dt_local = datetime.now(self.tz)
        end_dt_local = start_dt_local + timedelta(minutes=self.window_minutes)
        start_ts = int(start_dt_local.timestamp())
        end_ts = int(end_dt_local.timestamp())
        self.db.set_window(week_id, start_ts, end_ts)
        return start_ts, end_ts

    def is_window_open(self, week_id: int, now_ts: Optional[int] = None) -> bool:
        w = self.db.get_week(week_id)
        if now_ts is None:
            now_ts = int(datetime.now(self.tz).timestamp())
        return w["start_ts"] <= now_ts <= w["end_ts"]

    # --- Core allocation logic ---
    def try_assign(self, msg: VolunteerMessage, telegram_user_id: int, msg_ts: int) -> Optional[dict]:
        if not self.is_window_open(msg.week_id, now_ts=msg_ts):
            return None
        q_title = self.db.get_question_title(msg.week_id, msg.q_number)
        if q_title is None:
            return None
        if q_title != msg.title:
            return None

        self.db.upsert_student(msg.student_id, msg.full_name, telegram_user_id)
        st = self.db.get_student(msg.student_id)
        if st is None:
            return None
        prior = int(st["successful_count"])
        if prior >= 3:
            return None

        if self.db.student_has_temp(msg.week_id, msg.student_id):
            return None

        self.db.record_attempt(msg.week_id, msg.q_number, msg.student_id, msg_ts)

        current = self.db.get_temp_allocation(msg.week_id, msg.q_number)
        if current is None:
            self.db.set_temp_allocation(msg.week_id, msg.q_number, msg.student_id, msg_ts)
            return {"assigned": True, "q_number": msg.q_number, "title": q_title, "vol_count": prior}

        cur_student = self.db.get_student(current["student_id"])
        cur_prior = int(cur_student["successful_count"]) if cur_student else 99

        if prior < cur_prior:
            self.db.set_temp_allocation(msg.week_id, msg.q_number, msg.student_id, msg_ts)
            return {"assigned": True, "q_number": msg.q_number, "title": q_title, "vol_count": prior}
        else:
            return None

    def maybe_early_stop(self, week_id: int) -> bool:
        temps = self.db.get_all_temp_allocations(week_id)
        qs = self.db.get_questions_of_week(week_id)
        if not qs:
            return False
        if len(temps) < len(qs):
            return False
        for row in temps:
            if int(row["successful_count"]) != 0:
                return False
        return True

    def finalize_week(self, week_id: int) -> list:
        self.db.finalize_allocations(week_id)
        finals = self.db.get_final_allocations(week_id)
        for row in finals:
            self.db.inc_success(row["student_id"], 1)
        return finals
