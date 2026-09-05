from datetime import time
from typing import Literal, List
from pydantic import BaseModel

class TimeBlock(BaseModel):
    day: Literal["M", "T", "W", "R", "F", "S", "U"]
    start_time: time
    end_time: time

class Section(BaseModel):
    section_id: str
    course_id: str
    instructor: str
    meeting_times: List[TimeBlock]
    location: str = "TBD"

class Course(BaseModel):
    course_id: str
    title: str
    subject: str
    sections: List[Section]

class Schedule(BaseModel):
    sections: List[Section]
    has_overlap: bool = False
