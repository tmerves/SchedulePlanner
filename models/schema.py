from datetime import time
from typing import Literal, List, Optional
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
    term_code: Optional[str] = None
    component: str = "Lecture"
    linked_sections: List[str] = []

class Course(BaseModel):
    course_id: str
    title: str
    subject: str
    sections: List[Section]
    term_code: Optional[str] = None

class Schedule(BaseModel):
    sections: List[Section]
    has_overlap: bool = False
    term_code: Optional[str] = None

