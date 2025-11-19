PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS students (
  student_id TEXT PRIMARY KEY,
  full_name TEXT NOT NULL,
  telegram_user_id INTEGER,
  successful_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS weeks (
  week_id INTEGER PRIMARY KEY,
  start_ts INTEGER,
  end_ts INTEGER
);

CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  week_id INTEGER NOT NULL,
  q_number INTEGER NOT NULL,
  title TEXT NOT NULL,
  UNIQUE(week_id, q_number),
  FOREIGN KEY(week_id) REFERENCES weeks(week_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  week_id INTEGER NOT NULL,
  q_number INTEGER NOT NULL,
  student_id TEXT NOT NULL,
  msg_ts INTEGER NOT NULL,
  UNIQUE(week_id, q_number, student_id),
  FOREIGN KEY(week_id) REFERENCES weeks(week_id) ON DELETE CASCADE,
  FOREIGN KEY(student_id) REFERENCES students(student_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS temp_allocations (
  week_id INTEGER NOT NULL,
  q_number INTEGER NOT NULL,
  student_id TEXT NOT NULL,
  msg_ts INTEGER NOT NULL,
  PRIMARY KEY(week_id, q_number),
  FOREIGN KEY(week_id) REFERENCES weeks(week_id) ON DELETE CASCADE,
  FOREIGN KEY(student_id) REFERENCES students(student_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS final_allocations (
  week_id INTEGER NOT NULL,
  q_number INTEGER NOT NULL,
  student_id TEXT NOT NULL,
  PRIMARY KEY(week_id, q_number),
  FOREIGN KEY(week_id) REFERENCES weeks(week_id) ON DELETE CASCADE,
  FOREIGN KEY(student_id) REFERENCES students(student_id) ON DELETE CASCADE
);
