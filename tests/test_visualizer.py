from datetime import time
import pytest
import plotly.graph_objects as go
from models.schema import Course, Section, Schedule, TimeBlock
from utils.visualizer import (
    create_schedule_calendar,
    get_course_color_map,
    time_to_decimal_hour,
    format_hour_label,
    PASTEL_COLORS,
)


@pytest.fixture
def mock_schedule():
    # Course 1: CS 101, MWF 9:00 AM - 10:00 AM
    sec1 = Section(
        section_id="1001",
        course_id="CS 101",
        instructor="Dr. Turing",
        location="Lecture Center 7",
        meeting_times=[
            TimeBlock(day="M", start_time=time(9, 0), end_time=time(10, 0)),
            TimeBlock(day="W", start_time=time(9, 0), end_time=time(10, 0)),
            TimeBlock(day="F", start_time=time(9, 0), end_time=time(10, 0)),
        ],
    )
    # Course 2: MATH 201, TR 1:00 PM - 2:20 PM
    sec2 = Section(
        section_id="2002",
        course_id="MATH 201",
        instructor="Dr. Lovelace",
        location="Taconic 122",
        meeting_times=[
            TimeBlock(day="T", start_time=time(13, 0), end_time=time(14, 20)),
            TimeBlock(day="R", start_time=time(13, 0), end_time=time(14, 20)),
        ],
    )
    return Schedule(sections=[sec1, sec2])


def test_time_to_decimal_hour():
    assert time_to_decimal_hour(time(9, 0)) == 9.0
    assert time_to_decimal_hour(time(9, 30)) == 9.5
    assert time_to_decimal_hour(time(14, 15)) == 14.25


def test_format_hour_label():
    assert format_hour_label(8) == "8:00 AM"
    assert format_hour_label(12) == "12:00 PM"
    assert format_hour_label(13) == "1:00 PM"
    assert format_hour_label(20) == "8:00 PM"


def test_get_course_color_map_from_courses():
    courses = [
        Course(course_id="CS 101", title="Intro to CS", subject="CS", sections=[]),
        Course(course_id="MATH 201", title="Calculus", subject="MATH", sections=[]),
    ]
    color_map = get_course_color_map(courses)
    assert "CS 101" in color_map
    assert "MATH 201" in color_map
    assert color_map["CS 101"] in PASTEL_COLORS
    assert color_map["MATH 201"] in PASTEL_COLORS
    assert color_map["CS 101"] != color_map["MATH 201"]


def test_get_course_color_map_from_schedule(mock_schedule):
    color_map = get_course_color_map(mock_schedule)
    assert "CS 101" in color_map
    assert "MATH 201" in color_map


def test_create_schedule_calendar_empty():
    empty_sched = Schedule(sections=[])
    fig = create_schedule_calendar(empty_sched)
    assert isinstance(fig, go.Figure)
    assert list(fig.layout.yaxis.range) == [20.0, 8.0]
    assert list(fig.layout.xaxis.range) == [-0.5, 4.5]


def test_create_schedule_calendar_standard(mock_schedule):
    custom_colors = {"CS 101": "#A0C4FF", "MATH 201": "#FFC6FF"}
    fig = create_schedule_calendar(mock_schedule, course_colors=custom_colors)

    assert isinstance(fig, go.Figure)
    # Total shapes: 3 blocks for MWF + 2 blocks for TR = 5
    assert len(fig.layout.shapes) == 5

    # Check scatter trace for course labels
    assert len(fig.data) >= 1
    scatter_trace = fig.data[0]
    assert scatter_trace.mode == "text"
    assert len(scatter_trace.x) == 5
    assert any("CS 101 - 1001" in txt for txt in scatter_trace.text)
    assert any("9:00 AM - 10:00 AM" in txt for txt in scatter_trace.text)
    assert any("Lecture Center 7" in txt for txt in scatter_trace.text)
    assert any("MATH 201 - 2002" in txt for txt in scatter_trace.text)
    assert any("1:00 PM - 2:20 PM" in txt for txt in scatter_trace.text)
    assert any("Taconic 122" in txt for txt in scatter_trace.text)

    # Check hover text contains location
    assert any("<b>Location:</b> Lecture Center 7" in h for h in scatter_trace.hovertext)
    assert any("<b>Location:</b> Taconic 122" in h for h in scatter_trace.hovertext)


def test_create_schedule_calendar_early_class():
    early_sec = Section(
        section_id="7001",
        course_id="PHYS 101",
        instructor="Dr. Feynman",
        meeting_times=[
            TimeBlock(day="M", start_time=time(7, 15), end_time=time(8, 30))
        ],
    )
    sched = Schedule(sections=[early_sec])
    fig = create_schedule_calendar(sched)

    # Range is [max_hour, min_hour]
    y_range = fig.layout.yaxis.range
    assert y_range[1] <= 7.0  # min_hour should expand to at least 7.0


def test_create_schedule_calendar_late_class():
    late_sec = Section(
        section_id="9001",
        course_id="ASTR 101",
        instructor="Dr. Sagan",
        meeting_times=[
            TimeBlock(day="W", start_time=time(20, 0), end_time=time(21, 45))
        ],
    )
    sched = Schedule(sections=[late_sec])
    fig = create_schedule_calendar(sched)

    y_range = fig.layout.yaxis.range
    assert y_range[0] >= 22.0  # max_hour should expand to at least 22.0


def test_create_schedule_calendar_weekend():
    sat_sec = Section(
        section_id="6001",
        course_id="ART 100",
        instructor="Dr. DaVinci",
        meeting_times=[
            TimeBlock(day="S", start_time=time(10, 0), end_time=time(12, 0))
        ],
    )
    sched = Schedule(sections=[sat_sec])
    fig = create_schedule_calendar(sched)

    x_range = fig.layout.xaxis.range
    # Saturday index is 5 -> max_day + 0.5 = 5.5
    assert x_range[1] >= 5.5
    assert "Saturday" in fig.layout.xaxis.ticktext


def test_create_schedule_calendar_arranged():
    async_sec = Section(
        section_id="8001",
        course_id="ONLINE 101",
        instructor="Online Staff",
        meeting_times=[],
    )
    sched = Schedule(sections=[async_sec])
    fig = create_schedule_calendar(sched)

    assert isinstance(fig, go.Figure)
    # Check that an annotation for online/arranged course was added
    assert len(fig.layout.annotations) >= 1
    assert "ONLINE 101" in fig.layout.annotations[0].text


def test_location_labeled_on_schedule_viewer():
    sec = Section(
        section_id="2259",
        course_id="BACC 211",
        instructor="Moshier,Michelle",
        location="Massry Schl of Business B008",
        meeting_times=[
            TimeBlock(day="T", start_time=time(9, 0), end_time=time(10, 20)),
        ],
    )
    sched = Schedule(sections=[sec])
    fig = create_schedule_calendar(sched)

    scatter_trace = fig.data[0]
    assert any("BACC 211 - 2259" in txt for txt in scatter_trace.text)
    assert any("9:00 AM - 10:20 AM" in txt for txt in scatter_trace.text)
    assert any("Massry Schl of Business B008" in txt for txt in scatter_trace.text)
    assert any("Moshier,Michelle" in txt for txt in scatter_trace.text)
    assert any("<b>Location:</b> Massry Schl of Business B008" in h for h in scatter_trace.hovertext)


def test_arranged_section_with_location():
    sec = Section(
        section_id="2289",
        course_id="BACC 518",
        instructor="Fernando,Guy",
        location="Online",
        meeting_times=[],
    )
    sched = Schedule(sections=[sec])
    fig = create_schedule_calendar(sched)

    assert len(fig.layout.annotations) >= 1
    assert "BACC 518 (Sec 2289) [Online]" in fig.layout.annotations[0].text

