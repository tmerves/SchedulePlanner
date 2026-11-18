from datetime import time
from typing import Literal, List, Optional
from pydantic import BaseModel

class TimeBlock(BaseModel):
    day: Literal["M", "T", "W", "R", "F", "S", "U"]
    start_time: time
    end_time: time

class SectionDetails(BaseModel):
    """
    Descriptive registrar data captured from the search results page.
    Kept separate from Section's scheduling fields; the solver never reads it.
    Every field is optional so older cached data still loads.
    """
    level: Optional[str] = None
    college: Optional[str] = None
    department: Optional[str] = None
    grading: Optional[str] = None
    credit_range: Optional[str] = None
    topic: Optional[str] = None
    seats_remaining: Optional[str] = None
    session: Optional[str] = None
    delivery_method: Optional[str] = None
    gen_ed: Optional[str] = None
    honors: Optional[str] = None
    it_commons: Optional[str] = None
    writing_intensive: Optional[str] = None
    oral_discourse: Optional[str] = None
    info_literacy: Optional[str] = None
    special_restriction: Optional[str] = None
    coil: Optional[str] = None
    oer: Optional[str] = None
    liberal_arts: Optional[str] = None
    comments: Optional[str] = None
    description: Optional[str] = None

class Section(BaseModel):
    section_id: str
    course_id: str
    instructor: str
    meeting_times: List[TimeBlock]
    location: str = "TBD"
    term_code: Optional[str] = None
    component: str = "Lecture"
    linked_sections: List[str] = []
    details: Optional[SectionDetails] = None

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

