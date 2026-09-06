from datetime import time
import pytest
from models.schema import Course, Section, Schedule
from utils.time_utils import parse_time_blocks
from solver.permutator import ScheduleSolver

def test_zero_valid_schedules():
    # Two mandatory courses with sections that all mutually conflict.
    # Course 1 has two sections on Monday
    c1 = Course(
        course_id="CS101",
        title="Intro to CS",
        subject="CS",
        sections=[
            Section(
                section_id="A1",
                course_id="CS101",
                instructor="Prof A",
                meeting_times=parse_time_blocks("M", "09:00 - 10:00")
            ),
            Section(
                section_id="A2",
                course_id="CS101",
                instructor="Prof A",
                meeting_times=parse_time_blocks("M", "10:00 - 11:00")
            )
        ]
    )

    # Course 2 has one section that overlaps both of Course 1's sections
    c2 = Course(
        course_id="MATH101",
        title="Calculus",
        subject="MATH",
        sections=[
            Section(
                section_id="B1",
                course_id="MATH101",
                instructor="Prof B",
                meeting_times=parse_time_blocks("M", "09:15 - 10:45")
            )
        ]
    )

    schedules = ScheduleSolver.find_valid_schedules([c1, c2])
    assert len(schedules) == 0

def test_single_valid_schedule():
    # Exactly one non-conflicting path exists among multiple sections.
    c1 = Course(
        course_id="CS101",
        title="Intro to CS",
        subject="CS",
        sections=[
            Section(
                section_id="A1",
                course_id="CS101",
                instructor="Prof A",
                meeting_times=parse_time_blocks("M", "09:00 - 10:00")
            ),
            Section(
                section_id="A2",
                course_id="CS101",
                instructor="Prof A",
                meeting_times=parse_time_blocks("M", "10:00 - 11:00")
            )
        ]
    )

    # B1 conflicts with both A1 and A2.
    # B2 (10:30 - 11:30) conflicts with A2 (10-11) but NOT with A1 (9-10).
    c2 = Course(
        course_id="MATH101",
        title="Calculus",
        subject="MATH",
        sections=[
            Section(
                section_id="B1",
                course_id="MATH101",
                instructor="Prof B",
                meeting_times=parse_time_blocks("M", "09:30 - 10:30")
            ),
            Section(
                section_id="B2",
                course_id="MATH101",
                instructor="Prof B",
                meeting_times=parse_time_blocks("M", "10:30 - 11:30")
            )
        ]
    )

    schedules = ScheduleSolver.find_valid_schedules([c1, c2])
    assert len(schedules) == 1
    
    # The only valid schedule should contain A1 and B2
    sections = schedules[0].sections
    sec_ids = {s.section_id for s in sections}
    assert sec_ids == {"A1", "B2"}
    assert schedules[0].has_overlap is False

def test_multiple_valid_schedules():
    # Multiple viable combinations are returned correctly.
    # Both sections of c1 and c2 are on different days.
    c1 = Course(
        course_id="CS101",
        title="Intro to CS",
        subject="CS",
        sections=[
            Section(
                section_id="A1",
                course_id="CS101",
                instructor="Prof A",
                meeting_times=parse_time_blocks("M", "09:00 - 10:00")
            ),
            Section(
                section_id="A2",
                course_id="CS101",
                instructor="Prof A",
                meeting_times=parse_time_blocks("T", "09:00 - 10:00")
            )
        ]
    )

    c2 = Course(
        course_id="MATH101",
        title="Calculus",
        subject="MATH",
        sections=[
            Section(
                section_id="B1",
                course_id="MATH101",
                instructor="Prof B",
                meeting_times=parse_time_blocks("W", "09:00 - 10:00")
            ),
            Section(
                section_id="B2",
                course_id="MATH101",
                instructor="Prof B",
                meeting_times=parse_time_blocks("R", "09:00 - 10:00")
            )
        ]
    )

    schedules = ScheduleSolver.find_valid_schedules([c1, c2])
    # 2 * 2 = 4 possible valid combinations
    assert len(schedules) == 4
    for sched in schedules:
        assert len(sched.sections) == 2
        assert sched.has_overlap is False

def test_section_exclusion():
    # Verifying that a known working section is skipped when placed in excluded_section_ids.
    c1 = Course(
        course_id="CS101",
        title="Intro to CS",
        subject="CS",
        sections=[
            Section(
                section_id="A1",
                course_id="CS101",
                instructor="Prof A",
                meeting_times=parse_time_blocks("M", "09:00 - 10:00")
            ),
            Section(
                section_id="A2",
                course_id="CS101",
                instructor="Prof A",
                meeting_times=parse_time_blocks("M", "10:00 - 11:00")
            )
        ]
    )

    c2 = Course(
        course_id="MATH101",
        title="Calculus",
        subject="MATH",
        sections=[
            Section(
                section_id="B1",
                course_id="MATH101",
                instructor="Prof B",
                meeting_times=parse_time_blocks("M", "10:30 - 11:30")
            )
        ]
    )

    # Normal run without exclusion: A1 (9-10) and B1 (10:30-11:30) work.
    schedules = ScheduleSolver.find_valid_schedules([c1, c2])
    assert len(schedules) == 1
    assert {s.section_id for s in schedules[0].sections} == {"A1", "B1"}

    # Run with exclusion of A1: Since A1 is excluded, only A2 is available.
    # But A2 (10-11) conflicts with B1 (10:30-11:30).
    # Thus, we expect 0 valid schedules.
    schedules_excluded = ScheduleSolver.find_valid_schedules([c1, c2], excluded_section_ids={"A1"})
    assert len(schedules_excluded) == 0

def test_courses_with_multiple_sections_on_same_different_days():
    # Complex combinations verifying multi-day meeting times.
    c1 = Course(
        course_id="CS101",
        title="Intro to CS",
        subject="CS",
        sections=[
            Section(
                section_id="A1",
                course_id="CS101",
                instructor="Prof A",
                meeting_times=parse_time_blocks("MWF", "09:00 - 10:00")
            ),
            Section(
                section_id="A2",
                course_id="CS101",
                instructor="Prof A",
                meeting_times=parse_time_blocks("TR", "09:00 - 10:00")
            )
        ]
    )

    c2 = Course(
        course_id="MATH101",
        title="Calculus",
        subject="MATH",
        sections=[
            Section(
                section_id="B1",
                course_id="MATH101",
                instructor="Prof B",
                meeting_times=parse_time_blocks("MW", "10:00 - 11:00")
            ),
            Section(
                section_id="B2",
                course_id="MATH101",
                instructor="Prof B",
                meeting_times=parse_time_blocks("T", "09:30 - 10:30")
            )
        ]
    )

    # Let's trace combinations:
    # 1. (A1, B1): A1 (MWF 9-10) vs B1 (MW 10-11) -> Adjacent times, valid!
    # 2. (A1, B2): A1 (MWF 9-10) vs B2 (T 9:30-10:30) -> Different days, valid!
    # 3. (A2, B1): A2 (TR 9-10) vs B1 (MW 10-11) -> Different days, valid!
    # 4. (A2, B2): A2 (TR 9-10) vs B2 (T 9:30-10:30) -> Overlap on T (9:30-10:00), invalid!
    # Valid combinations should be: (A1, B1), (A1, B2), (A2, B1) => 3 valid schedules.
    schedules = ScheduleSolver.find_valid_schedules([c1, c2])
    assert len(schedules) == 3
    
    # Verify that (A2, B2) is not among them
    for sched in schedules:
        sec_ids = {s.section_id for s in sched.sections}
        assert sec_ids != {"A2", "B2"}



def test_solver_rejects_cross_semester_courses():
    c1 = Course(
        course_id="CS101",
        title="Intro to CS",
        subject="CS",
        term_code="0009",
        sections=[
            Section(
                section_id="A1",
                course_id="CS101",
                instructor="Prof A",
                term_code="0009",
                meeting_times=parse_time_blocks("M", "09:00 - 10:00")
            )
        ]
    )

    c2 = Course(
        course_id="MATH101",
        title="Calculus",
        subject="MATH",
        term_code="0007",
        sections=[
            Section(
                section_id="B1",
                course_id="MATH101",
                instructor="Prof B",
                term_code="0007",
                meeting_times=parse_time_blocks("M", "10:30 - 11:30")
            )
        ]
    )

    with pytest.raises(ValueError, match="Cannot generate schedules with courses from different semesters"):
        ScheduleSolver.find_valid_schedules([c1, c2])



def test_course_with_linked_discussions():
    # Course with a lecture and two discussion choices
    # Lec 4834 requires either 4835 (Tuesday) or 4836 (Friday)
    course_cs = Course(
        course_id="ICSI 311",
        title="Principles of Programming Languages",
        subject="ICSI",
        sections=[
            Section(
                section_id="4834",
                course_id="ICSI 311",
                instructor="Phipps,Michael",
                component="Lecture",
                linked_sections=["4835", "4836"],
                meeting_times=parse_time_blocks("TR", "10:30 - 11:50")
            ),
            Section(
                section_id="4835",
                course_id="ICSI 311",
                instructor="Phipps,Michael",
                component="Discussion",
                meeting_times=parse_time_blocks("T", "15:00 - 15:55")
            ),
            Section(
                section_id="4836",
                course_id="ICSI 311",
                instructor="Phipps,Michael",
                component="Discussion",
                meeting_times=parse_time_blocks("F", "09:30 - 10:25")
            ),
        ]
    )

    # Standalone Course meeting Tuesday afternoon, conflicting with Discussion 4835
    course_math = Course(
        course_id="AMAT 220",
        title="Linear Algebra",
        subject="AMAT",
        sections=[
            Section(
                section_id="9001",
                course_id="AMAT 220",
                instructor="Prof M",
                component="Lecture",
                meeting_times=parse_time_blocks("T", "15:00 - 16:00")
            )
        ]
    )

    # Solver should only find 1 valid schedule: [4834 (Lec), 4836 (Disc F), 9001]
    schedules = ScheduleSolver.find_valid_schedules([course_cs, course_math])
    assert len(schedules) == 1
    schedule_sec_ids = {s.section_id for s in schedules[0].sections}
    assert schedule_sec_ids == {"4834", "4836", "9001"}


def test_course_with_linked_discussions_exclusions():
    course_cs = Course(
        course_id="ICSI 311",
        title="Principles of Programming Languages",
        subject="ICSI",
        sections=[
            Section(
                section_id="4834",
                course_id="ICSI 311",
                instructor="Phipps,Michael",
                component="Lecture",
                linked_sections=["4835", "4836"],
                meeting_times=parse_time_blocks("TR", "10:30 - 11:50")
            ),
            Section(
                section_id="4835",
                course_id="ICSI 311",
                instructor="Phipps,Michael",
                component="Discussion",
                meeting_times=parse_time_blocks("T", "15:00 - 15:55")
            ),
            Section(
                section_id="4836",
                course_id="ICSI 311",
                instructor="Phipps,Michael",
                component="Discussion",
                meeting_times=parse_time_blocks("F", "09:30 - 10:25")
            ),
        ]
    )

    # If 4835 is excluded, only bundle with 4836 remains
    schedules = ScheduleSolver.find_valid_schedules([course_cs], excluded_section_ids={"4835"})
    assert len(schedules) == 1
    assert {s.section_id for s in schedules[0].sections} == {"4834", "4836"}

    # If 4834 (the lecture) is excluded, no options remain for the course
    schedules_no_lec = ScheduleSolver.find_valid_schedules([course_cs], excluded_section_ids={"4834"})
    assert len(schedules_no_lec) == 0

