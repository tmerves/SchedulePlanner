from datetime import time
import pytest
from models.schema import TimeBlock, Section
from utils.time_utils import (
    parse_single_time,
    parse_time_blocks,
    is_time_conflict,
    do_sections_conflict,
    format_meeting_times,
)

def test_parse_single_time_12h():
    assert parse_single_time("09:30 AM") == time(9, 30)
    assert parse_single_time("9:30 AM") == time(9, 30)
    assert parse_single_time("12:00 PM") == time(12, 0)
    assert parse_single_time("12:00 AM") == time(0, 0)
    assert parse_single_time("1:15 PM") == time(13, 15)
    assert parse_single_time("01:15 PM") == time(13, 15)
    assert parse_single_time("11:59 pm") == time(23, 59)
    assert parse_single_time("9:30pm") == time(21, 30)

def test_parse_single_time_24h():
    assert parse_single_time("14:00") == time(14, 0)
    assert parse_single_time("09:30") == time(9, 30)
    assert parse_single_time("9:30") == time(9, 30)
    assert parse_single_time("00:00") == time(0, 0)

def test_parse_single_time_invalid():
    with pytest.raises(ValueError):
        parse_single_time("25:00")
    with pytest.raises(ValueError):
        parse_single_time("12:60 AM")
    with pytest.raises(ValueError):
        parse_single_time("abc")

def test_parse_time_blocks_basic():
    # 12-hour format with spaces
    blocks = parse_time_blocks("MWF", "09:30 AM - 10:45 AM")
    assert len(blocks) == 3
    assert blocks[0] == TimeBlock(day="M", start_time=time(9, 30), end_time=time(10, 45))
    assert blocks[1] == TimeBlock(day="W", start_time=time(9, 30), end_time=time(10, 45))
    assert blocks[2] == TimeBlock(day="F", start_time=time(9, 30), end_time=time(10, 45))

    # 24-hour format
    blocks_24 = parse_time_blocks("TR", "14:00 - 15:15")
    assert len(blocks_24) == 2
    assert blocks_24[0] == TimeBlock(day="T", start_time=time(14, 0), end_time=time(15, 15))
    assert blocks_24[1] == TimeBlock(day="R", start_time=time(14, 0), end_time=time(15, 15))

def test_parse_time_blocks_formatting():
    # mixed case, extra spaces
    blocks = parse_time_blocks(" m w f ", " 09:30 am-10:45 am ")
    assert len(blocks) == 3
    assert [b.day for b in blocks] == ["M", "W", "F"]
    assert blocks[0].start_time == time(9, 30)
    assert blocks[0].end_time == time(10, 45)

def test_is_time_conflict():
    # Different days, same times -> NO CONFLICT
    block1 = TimeBlock(day="M", start_time=time(9, 0), end_time=time(10, 0))
    block2 = TimeBlock(day="T", start_time=time(9, 0), end_time=time(10, 0))
    assert not is_time_conflict(block1, block2)

    # Same day, non-overlapping -> NO CONFLICT
    block3 = TimeBlock(day="M", start_time=time(10, 15), end_time=time(11, 30))
    assert not is_time_conflict(block1, block3)

    # Same day, back-to-back/adjacent -> NO CONFLICT
    block4 = TimeBlock(day="M", start_time=time(10, 0), end_time=time(11, 0))
    assert not is_time_conflict(block1, block4)

    # Same day, overlap starts during block1 -> CONFLICT
    block5 = TimeBlock(day="M", start_time=time(9, 30), end_time=time(10, 30))
    assert is_time_conflict(block1, block5)

    # Same day, exact overlap -> CONFLICT
    block6 = TimeBlock(day="M", start_time=time(9, 0), end_time=time(10, 0))
    assert is_time_conflict(block1, block6)

    # Same day, fully contained -> CONFLICT
    block7 = TimeBlock(day="M", start_time=time(9, 15), end_time=time(9, 45))
    assert is_time_conflict(block1, block7)

def test_do_sections_conflict():
    # Multi-day sections
    # sec_a: MWF 9:00 - 10:00
    sec_a = Section(
        section_id="A1",
        course_id="CS101",
        instructor="Prof A",
        meeting_times=parse_time_blocks("MWF", "09:00 - 10:00")
    )

    # sec_b: TR 9:00 - 10:00 (no overlapping days) -> NO CONFLICT
    sec_b = Section(
        section_id="B1",
        course_id="CS102",
        instructor="Prof B",
        meeting_times=parse_time_blocks("TR", "09:00 - 10:00")
    )
    assert not do_sections_conflict(sec_a, sec_b)

    # sec_c: WF 9:30 - 10:30 (overlaps on W and F) -> CONFLICT
    sec_c = Section(
        section_id="C1",
        course_id="CS103",
        instructor="Prof C",
        meeting_times=parse_time_blocks("WF", "09:30 - 10:30")
    )
    assert do_sections_conflict(sec_a, sec_c)

    # sec_d: W 10:00 - 11:00 (adjacent on W) -> NO CONFLICT
    sec_d = Section(
        section_id="D1",
        course_id="CS104",
        instructor="Prof D",
        meeting_times=parse_time_blocks("W", "10:00 - 11:00")
    )
    assert not do_sections_conflict(sec_a, sec_d)


def test_parse_time_blocks_thursday_variations():
    # Thursday as "TH" (e.g. UAlbany ICSI 213 section 5014)
    blocks_th = parse_time_blocks("TH", "04:30 PM - 05:25 PM")
    assert len(blocks_th) == 1
    assert blocks_th[0] == TimeBlock(day="R", start_time=time(16, 30), end_time=time(17, 25))

    # Tuesday and Thursday as "TTH"
    blocks_tth = parse_time_blocks("TTH", "09:00 AM - 10:20 AM")
    assert len(blocks_tth) == 2
    assert blocks_tth[0] == TimeBlock(day="T", start_time=time(9, 0), end_time=time(10, 20))
    assert blocks_tth[1] == TimeBlock(day="R", start_time=time(9, 0), end_time=time(10, 20))


def test_format_meeting_times_thursday_display():
    # Thursday meeting time (ICSI 213 section 5014) must display "TH", not "R"
    sec_5014_blocks = [
        TimeBlock(day="R", start_time=time(16, 30), end_time=time(17, 25))
    ]
    assert format_meeting_times(sec_5014_blocks) == "TH 4:30 PM - 5:25 PM"

    # Multi-day section with Tuesday and Thursday
    tth_blocks = [
        TimeBlock(day="T", start_time=time(9, 0), end_time=time(10, 20)),
        TimeBlock(day="R", start_time=time(9, 0), end_time=time(10, 20)),
    ]
    assert format_meeting_times(tth_blocks) == "T 9:00 AM - 10:20 AM, TH 9:00 AM - 10:20 AM"

    # MWF section
    mwf_blocks = [
        TimeBlock(day="M", start_time=time(9, 30), end_time=time(10, 45)),
        TimeBlock(day="W", start_time=time(9, 30), end_time=time(10, 45)),
        TimeBlock(day="F", start_time=time(9, 30), end_time=time(10, 45)),
    ]
    assert format_meeting_times(mwf_blocks) == "M 9:30 AM - 10:45 AM, W 9:30 AM - 10:45 AM, F 9:30 AM - 10:45 AM"

    # Arranged / Online
    assert format_meeting_times([]) == "Arranged / Online"
    assert format_meeting_times(None) == "Arranged / Online"

