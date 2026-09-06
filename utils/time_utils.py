import re
from datetime import time
from typing import List, Tuple, Optional
from models.schema import TimeBlock, Section

def parse_single_time(t_str: str) -> time:
    """Parses a single time string (12-hour with AM/PM or 24-hour) into datetime.time."""
    t_str = t_str.strip().upper()
    
    # 12-hour AM/PM format (e.g., "09:30 AM", "9:30 AM", "9:30PM")
    am_pm_match = re.match(r'^(\d{1,2}):(\d{2})\s*(AM|PM)$', t_str)
    if am_pm_match:
        hour = int(am_pm_match.group(1))
        minute = int(am_pm_match.group(2))
        period = am_pm_match.group(3)
        if period == "PM" and hour != 12:
            hour += 12
        elif period == "AM" and hour == 12:
            hour = 0
        return time(hour, minute)
    
    # 24-hour format (e.g., "14:00", "09:30", "9:30")
    h24_match = re.match(r'^(\d{1,2}):(\d{2})$', t_str)
    if h24_match:
        hour = int(h24_match.group(1))
        minute = int(h24_match.group(2))
        return time(hour, minute)
        
    raise ValueError(f"Invalid time format: {t_str}")

DAY_DISPLAY_MAP: dict[str, str] = {
    "M": "M",
    "T": "T",
    "W": "W",
    "R": "TH",
    "F": "F",
    "S": "S",
    "U": "U",
}

def format_meeting_times(meeting_times: Optional[List[TimeBlock]]) -> str:
    """
    Formats a list of TimeBlocks into a concise human-readable string.
    Translates internal single-letter 'R' day representation into 'TH' for display.
    """
    if not meeting_times:
        return "Arranged / Online"
    formatted_blocks = []
    for tb in meeting_times:
        start_str = tb.start_time.strftime("%I:%M %p").lstrip("0")
        end_str = tb.end_time.strftime("%I:%M %p").lstrip("0")
        day_label = DAY_DISPLAY_MAP.get(tb.day, tb.day)
        formatted_blocks.append(f"{day_label} {start_str} - {end_str}")
    return ", ".join(formatted_blocks)

def parse_time_blocks(days_str: str, time_str: str) -> List[TimeBlock]:
    """
    Parses a day abbreviation string (e.g. 'MWF', 'TR', 'TTH', 'TH') and time range string
    (e.g. '09:30 AM - 10:45 AM' or '14:00 - 15:15') into a list of TimeBlock objects.
    """
    days_str = days_str.strip().upper()
    # Normalize UAlbany's 'TH' to internal single-letter 'R' representation
    days_str = days_str.replace("TH", "R")
    days = [char for char in days_str if char in "MTWRFSU"]
    
    parts = re.split(r'\s*-\s*|\s+to\s+', time_str, flags=re.IGNORECASE)
    if len(parts) != 2:
        raise ValueError(f"Invalid time range: {time_str}")
        
    start_time = parse_single_time(parts[0])
    end_time = parse_single_time(parts[1])
    
    return [
        TimeBlock(day=day, start_time=start_time, end_time=end_time)
        for day in days
    ]

def time_to_minutes(t: time) -> int:
    """Helper to convert datetime.time into integer minutes from midnight."""
    return t.hour * 60 + t.minute

def is_time_conflict(block_a: TimeBlock, block_b: TimeBlock) -> bool:
    """
    Determines if two TimeBlock objects conflict.
    Conflict only exists if the blocks are on the same day and their time intervals overlap.
    Adjacent/back-to-back times do NOT conflict.
    """
    if block_a.day != block_b.day:
        return False
        
    start_a = time_to_minutes(block_a.start_time)
    end_a = time_to_minutes(block_a.end_time)
    start_b = time_to_minutes(block_b.start_time)
    end_b = time_to_minutes(block_b.end_time)
    
    # Conflict if start_a < end_b AND start_b < end_a
    return start_a < end_b and start_b < end_a

def do_sections_conflict(sec_a: Section, sec_b: Section) -> bool:
    """
    Determines if two Section objects conflict.
    Conflict exists if any time block from sec_a conflicts with any time block from sec_b.
    """
    for block_a in sec_a.meeting_times:
        for block_b in sec_b.meeting_times:
            if is_time_conflict(block_a, block_b):
                return True
    return False
