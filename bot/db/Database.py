import sqlite3
from pathlib import Path
from typing import Optional, List


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self):
        with open(Path(__file__).with_name("schema.sql"), encoding="utf-8") as f:
            schema = f.read()
        with self._conn:
            self._conn.executescript(schema)

    def get_meta(self, key: str) -> Optional[str]:
        cur = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,))
        row = cur.fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str):
        with self._conn:
            self._conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))

    def new_week(self, week_id: int):
        with self._conn:
            self._conn.execute("INSERT INTO weeks(week_id) VALUES(?) ON CONFLICT(week_id) DO NOTHING",
                               (week_id,))

    def set_window(self, week_id: int, start_ts: int, end_ts: int):
        with self._conn:
            self._conn.execute("UPDATE weeks SET start_ts=?, end_ts=? WHERE week_id=?",
                               (start_ts, end_ts, week_id))

    def set_end_ts(self, week_id: int, end_ts: int):
        with self._conn:
            self._conn.execute("UPDATE weeks SET end_ts=? WHERE week_id=?",
                               (end_ts, week_id))

    def get_week(self, week_id: int):
        cur = self._conn.execute("SELECT * FROM weeks WHERE week_id=?", (week_id,))
        return cur.fetchone()

    def add_question_to_week(self, week_id: int, q_number: int, title: str):
        with self._conn:
            self._conn.execute(
                "INSERT INTO questions(week_id,q_number,title) VALUES(?,?,?) ON CONFLICT(week_id,q_number) DO UPDATE SET title=excluded.title",
                (week_id, q_number, title))

    def get_questions_of_week(self, week_id: int) -> List[sqlite3.Row]:
        cur = self._conn.execute("SELECT q_number,title FROM questions WHERE week_id=? ORDER BY q_number", (week_id,))
        return cur.fetchall()

    def get_question_title(self, week_id: int, q_number: int) -> Optional[str]:
        cur = self._conn.execute("SELECT title FROM questions WHERE week_id=? AND q_number=?", (week_id, q_number))
        r = cur.fetchone()
        return r[0] if r else None

    def upsert_student(self, student_id: str, full_name: str, telegram_user_id: Optional[int]):
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO students(student_id, full_name, telegram_user_id, successful_count)
                VALUES (?, ?, ?, 0) ON CONFLICT(student_id) DO
                UPDATE SET full_name=excluded.full_name,
                    telegram_user_id= COALESCE (excluded.telegram_user_id, students.telegram_user_id)
                """,
                (student_id, full_name, telegram_user_id),
            )

    def get_student(self, student_id: str):
        cur = self._conn.execute("SELECT * FROM students WHERE student_id=?", (student_id,))
        return cur.fetchone()

    def inc_success(self, student_id: str, delta: int = 1):
        with self._conn:
            self._conn.execute("UPDATE students SET successful_count=successful_count+? WHERE student_id=?",
                               (delta, student_id))

    def record_attempt(self, week_id: int, q_number: int, student_id: str, msg_ts: int):
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO attempts(week_id,q_number,student_id,msg_ts) VALUES(?,?,?,?)",
                (week_id, q_number, student_id, msg_ts),
            )

    def get_temp_allocation(self, week_id: int, q_number: int):
        cur = self._conn.execute("SELECT * FROM temp_allocations WHERE week_id=? AND q_number=?", (week_id, q_number))
        return cur.fetchone()

    def set_temp_allocation(self, week_id: int, q_number: int, student_id: str, msg_ts: int):
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO temp_allocations(week_id, q_number, student_id, msg_ts)
                VALUES (?, ?, ?, ?) ON CONFLICT(week_id,q_number) DO
                UPDATE SET student_id=excluded.student_id, msg_ts=excluded.msg_ts
                """,
                (week_id, q_number, student_id, msg_ts),
            )

    def get_all_temp_allocations(self, week_id: int) -> List[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT t.week_id,t.q_number,t.student_id,t.msg_ts,s.successful_count,s.full_name,s.telegram_user_id,q.title FROM temp_allocations t "
            "JOIN students s ON s.student_id=t.student_id "
            "JOIN questions q ON q.week_id=t.week_id AND q.q_number=t.q_number "
            "WHERE t.week_id=? ORDER BY t.q_number",
            (week_id,),
        )
        return cur.fetchall()

    def finalize_allocations(self, week_id: int):
        temps = self.get_all_temp_allocations(week_id)
        with self._conn:
            for row in temps:
                self._conn.execute(
                    "INSERT OR REPLACE INTO final_allocations(week_id,q_number,student_id) VALUES(?,?,?)",
                    (week_id, row["q_number"], row["student_id"]),
                )

    def get_final_allocations(self, week_id: int) -> List[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT f.week_id,f.q_number,f.student_id,s.full_name,s.telegram_user_id,s.successful_count,q.title FROM final_allocations f "
            "JOIN students s ON s.student_id=f.student_id "
            "JOIN questions q ON q.week_id=f.week_id AND q.q_number=f.q_number "
            "WHERE f.week_id=? ORDER BY f.q_number",
            (week_id,),
        )
        return cur.fetchall()

    def student_has_temp(self, week_id: int, student_id: str) -> bool:
        cur = self._conn.execute("SELECT 1 FROM temp_allocations WHERE week_id=? AND student_id=? LIMIT 1",
                                 (week_id, student_id))
        return cur.fetchone() is not None

    def get_active_week(self, now) -> Optional[int]:
        now = int(now.timestamp())
        cur = self._conn.execute(
            """
            SELECT week_id
            FROM weeks
            WHERE start_ts IS NOT NULL
              AND end_ts IS NOT NULL
              AND start_ts <= ?
              AND end_ts > ? LIMIT 1
            """,
            (now, now),
        )
        row = cur.fetchone()
        return row["week_id"] if row else None
