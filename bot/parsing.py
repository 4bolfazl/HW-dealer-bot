import re
from typing import Optional

from bot.models import VolunteerMessage

PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

HEADER = r"سوال"
SP = r" "
DASH = r"-"
NUM = r"(?P<num>[0-9۰-۹٠-٩]+)"
TITLE = r"(?P<title>.+)"
FULLNAME = r"(?P<name>[آ-یءئؤإأا-ی\s]+)"
SID = r"(?P<sid>[0-9۰-۹٠-٩]+)"

STRICT_PATTERN = re.compile(
    rf"^{HEADER}{SP}{NUM}{SP}{DASH}{SP}{TITLE}\n\n{FULLNAME}\n{SID}$"
)


def normalize_digits(s: str) -> str:
    return s.translate(PERSIAN_DIGITS).translate(ARABIC_DIGITS)


def parse_volunteer(text: str, week_id: int) -> Optional[VolunteerMessage]:
    m = STRICT_PATTERN.match(text.strip())
    if not m:
        return None
    num = int(normalize_digits(m.group("num")))
    sid = normalize_digits(m.group("sid"))
    title = m.group("title").strip()
    fullname = m.group("name").strip()
    return VolunteerMessage(week_id=week_id, q_number=num, title=title, full_name=fullname, student_id=sid)
