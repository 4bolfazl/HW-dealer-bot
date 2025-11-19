from dataclasses import dataclass


@dataclass
class VolunteerMessage:
    week_id: int
    q_number: int
    title: str
    full_name: str
    student_id: str
