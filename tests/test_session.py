import os
from pathlib import Path
import pytest
from core.session import (
    ScheduleSessionManager,
    generate_session_id,
    is_valid_session_id,
    get_user_session_filepath,
)
from models.schema import Course, Section, Schedule
from utils.time_utils import parse_time_blocks


@pytest.fixture
def mock_courses():
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
    return [c1, c2]


def test_catalog_caching(tmp_path, mock_courses):
    # Setup session manager with temp cache dir
    cache_dir = tmp_path / "cache"
    manager = ScheduleSessionManager(cache_dir=str(cache_dir))
    
    term_code = "202610"
    subject_code = "CS"
    
    # Check that there is no cache initially
    assert manager.get_cached_catalog(term_code, subject_code) is None
    
    # Save cache
    manager.cache_catalog(term_code, subject_code, mock_courses)
    
    # Assert file exists
    expected_file = cache_dir / f"{term_code}_{subject_code}.json"
    assert expected_file.exists()
    
    # Load cache back and assert equality
    cached_courses = manager.get_cached_catalog(term_code, subject_code)
    assert cached_courses is not None
    assert len(cached_courses) == 2
    assert cached_courses[0].course_id == "CS101"
    assert cached_courses[1].course_id == "MATH101"
    assert len(cached_courses[0].sections) == 2
    assert cached_courses[0].sections[0].section_id == "A1"


def test_course_selection_management(mock_courses):
    manager = ScheduleSessionManager()
    
    # Add courses
    c1, c2 = mock_courses
    manager.add_course(c1)
    assert len(manager.selected_courses) == 1
    assert "CS101" in manager.selected_courses
    
    # Add duplicate (should overwrite / avoid duplicate entry in dict)
    manager.add_course(c1)
    assert len(manager.selected_courses) == 1
    
    # Add second course
    manager.add_course(c2)
    assert len(manager.selected_courses) == 2
    assert "MATH101" in manager.selected_courses
    
    # Remove course
    manager.remove_course("CS101")
    assert len(manager.selected_courses) == 1
    assert "CS101" not in manager.selected_courses
    assert "MATH101" in manager.selected_courses
    
    # Clear courses
    manager.clear_courses()
    assert len(manager.selected_courses) == 0


def test_section_toggling_and_active_courses(mock_courses):
    manager = ScheduleSessionManager()
    c1, c2 = mock_courses
    manager.add_course(c1)
    manager.add_course(c2)
    
    # Initially active courses have all sections
    active = manager.get_active_courses()
    assert len(active) == 2
    # Ensure sections are copied/intact
    cs101_active = next(c for c in active if c.course_id == "CS101")
    assert len(cs101_active.sections) == 2
    
    # Exclude section A1
    manager.toggle_section("A1", is_active=False)
    assert "A1" in manager.excluded_section_ids
    
    # Verify section A1 is stripped out
    active = manager.get_active_courses()
    cs101_active = next(c for c in active if c.course_id == "CS101")
    assert len(cs101_active.sections) == 1
    assert cs101_active.sections[0].section_id == "A2"
    
    # Toggle A1 back to active
    manager.toggle_section("A1", is_active=True)
    assert "A1" not in manager.excluded_section_ids
    
    # Verify section A1 is back
    active = manager.get_active_courses()
    cs101_active = next(c for c in active if c.course_id == "CS101")
    assert len(cs101_active.sections) == 2


def test_solve_schedule(mock_courses):
    manager = ScheduleSessionManager()
    c1, c2 = mock_courses
    manager.add_course(c1)
    manager.add_course(c2)
    
    # Normal run without exclusion: A1 (9-10) and B1 (10:30-11:30) work.
    schedules = manager.solve_schedule()
    assert len(schedules) == 1
    assert {s.section_id for s in schedules[0].sections} == {"A1", "B1"}
    
    # Exclude A1, so only A2 is available. A2 conflicts with B1.
    manager.toggle_section("A1", is_active=False)
    schedules_excluded = manager.solve_schedule()
    assert len(schedules_excluded) == 0


def test_save_and_load_session_state(tmp_path, mock_courses):
    filepath = tmp_path / "session_state.json"
    manager = ScheduleSessionManager()
    c1, c2 = mock_courses
    manager.add_course(c1)
    manager.add_course(c2)
    manager.toggle_section("A1", is_active=False)
    
    # Save
    manager.save_session_state(str(filepath))
    assert filepath.exists()
    
    # Load into a new session manager
    new_manager = ScheduleSessionManager()
    new_manager.load_session_state(str(filepath))
    
    assert len(new_manager.selected_courses) == 2
    assert "CS101" in new_manager.selected_courses
    assert "MATH101" in new_manager.selected_courses
    assert "A1" in new_manager.excluded_section_ids
    
    # Check that loaded state operates correctly
    schedules = new_manager.solve_schedule()
    assert len(schedules) == 0  # Since A1 is excluded, they conflict


def test_cache_catalog_empty_does_not_create_file(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = ScheduleSessionManager(cache_dir=str(cache_dir))
    term_code = "202610"
    subject_code = "CS"
    
    manager.cache_catalog(term_code, subject_code, [])
    
    expected_file = cache_dir / f"{term_code}_{subject_code}.json"
    assert not expected_file.exists()


def test_get_cached_catalog_handles_empty_or_corrupted_data(tmp_path):
    cache_dir = tmp_path / "cache"
    manager = ScheduleSessionManager(cache_dir=str(cache_dir))
    term_code = "202610"
    subject_code = "CS"
    
    # 1. Test empty list []
    cache_dir.mkdir(parents=True, exist_ok=True)
    expected_file = cache_dir / f"{term_code}_{subject_code}.json"
    with open(expected_file, "w", encoding="utf-8") as f:
        f.write("[]")
    
    assert expected_file.exists()
    assert manager.get_cached_catalog(term_code, subject_code) is None
    assert not expected_file.exists()
    
    # 2. Test corrupted JSON data
    with open(expected_file, "w", encoding="utf-8") as f:
        f.write("{invalid json")
        
    assert expected_file.exists()
    assert manager.get_cached_catalog(term_code, subject_code) is None
    assert not expected_file.exists()


def test_get_cached_catalog_force_refresh(tmp_path, mock_courses):
    cache_dir = tmp_path / "cache"
    manager = ScheduleSessionManager(cache_dir=str(cache_dir))
    term_code = "202610"
    subject_code = "CS"
    
    # Save a valid cache
    manager.cache_catalog(term_code, subject_code, mock_courses)
    expected_file = cache_dir / f"{term_code}_{subject_code}.json"
    assert expected_file.exists()
    
    # Reading with force_refresh=True should bypass the cache and return None (but not unlink it)
    assert manager.get_cached_catalog(term_code, subject_code, force_refresh=True) is None
    assert expected_file.exists()
    
    # Verify that reading without force_refresh still works
    assert manager.get_cached_catalog(term_code, subject_code) is not None



def test_saved_schedules_management_and_persistence(tmp_path, mock_courses):
    filepath = tmp_path / "saved_schedules_state.json"
    manager = ScheduleSessionManager()
    
    c1, c2 = mock_courses
    s1 = Schedule(sections=[c1.sections[0], c2.sections[0]])
    s2 = Schedule(sections=[c1.sections[1]])
    
    # 1. Initially saved schedules should be empty
    assert len(manager.get_saved_schedules()) == 0
    
    # 2. Add s1
    manager.save_schedule(s1)
    saved = manager.get_saved_schedules()
    assert len(saved) == 1
    assert saved[0].sections[0].section_id == "A1"
    
    # 3. Add duplicate schedule s1 again (should be ignored)
    manager.save_schedule(s1)
    assert len(manager.get_saved_schedules()) == 1
    
    # 4. Add different schedule s2
    manager.save_schedule(s2)
    assert len(manager.get_saved_schedules()) == 2
    assert manager.get_saved_schedules()[1].sections[0].section_id == "A2"
    
    # 5. Save state
    manager.save_session_state(str(filepath))
    assert filepath.exists()
    
    # 6. Load into a new session manager and verify
    new_manager = ScheduleSessionManager()
    new_manager.load_session_state(str(filepath))
    
    loaded_saved = new_manager.get_saved_schedules()
    assert len(loaded_saved) == 2
    assert loaded_saved[0].sections[0].section_id == "A1"
    assert loaded_saved[0].sections[1].section_id == "B1"
    assert loaded_saved[1].sections[0].section_id == "A2"
    
    # 7. Test removing a schedule
    new_manager.remove_saved_schedule(0)
    assert len(new_manager.get_saved_schedules()) == 1
    assert new_manager.get_saved_schedules()[0].sections[0].section_id == "A2"
    
    # 8. Test clearing saved schedules
    new_manager.clear_saved_schedules()
    assert len(new_manager.get_saved_schedules()) == 0



def test_semester_isolation(mock_courses):
    c1, c2 = mock_courses
    manager = ScheduleSessionManager()

    # Set term to Fall 2026 (0009)
    manager.set_term("0009")
    manager.add_course(c1)
    manager.toggle_section("A1", is_active=False)
    s1 = Schedule(sections=[c1.sections[1]])
    manager.save_schedule(s1)

    assert len(manager.selected_courses) == 1
    assert "CS101" in manager.selected_courses
    assert "A1" in manager.excluded_section_ids
    assert len(manager.get_saved_schedules()) == 1

    # Switch term to Spring 2026 (0007)
    manager.set_term("0007")
    assert len(manager.selected_courses) == 0
    assert len(manager.excluded_section_ids) == 0
    assert len(manager.get_saved_schedules()) == 0

    # Add different course to Spring 2026
    manager.add_course(c2)
    assert len(manager.selected_courses) == 1
    assert "MATH101" in manager.selected_courses
    assert "CS101" not in manager.selected_courses

    # Switch back to Fall 2026 (0009) - state must be completely preserved
    manager.set_term("0009")
    assert len(manager.selected_courses) == 1
    assert "CS101" in manager.selected_courses
    assert "MATH101" not in manager.selected_courses
    assert "A1" in manager.excluded_section_ids
    assert len(manager.get_saved_schedules()) == 1


def test_prevent_adding_cross_semester_course(mock_courses):
    c1, c2 = mock_courses
    manager = ScheduleSessionManager()

    manager.set_term("0009")

    # c1 is explicitly tagged for Spring 2026 (0007)
    c1_spring = Course(
        course_id=c1.course_id,
        title=c1.title,
        subject=c1.subject,
        sections=c1.sections,
        term_code="0007"
    )

    # Attempting to add Spring course to Fall session must raise ValueError
    with pytest.raises(ValueError, match="Cannot add course 'CS101' from semester '0007' to semester '0009'"):
        manager.add_course(c1_spring)

    assert "CS101" not in manager.selected_courses

    # Attempting to add with conflicting explicit term_code must also raise ValueError
    with pytest.raises(ValueError, match="Cannot add course 'CS101' from semester '0007' to semester '0008'"):
        manager.add_course(c1_spring, term_code="0008")


def test_prevent_saving_cross_semester_schedule(mock_courses):
    c1, _ = mock_courses
    manager = ScheduleSessionManager()
    manager.set_term("0009")

    # Schedule tagged for semester 0007
    sched_spring = Schedule(sections=[c1.sections[0]], term_code="0007")

    with pytest.raises(ValueError, match="Cannot save schedule from semester '0007' to semester '0009'"):
        manager.save_schedule(sched_spring)

    assert len(manager.get_saved_schedules()) == 0


def test_semester_separate_persistence(tmp_path, mock_courses):
    c1, c2 = mock_courses
    filepath = tmp_path / "session_state.json"
    manager = ScheduleSessionManager()

    # Populate 0009
    manager.set_term("0009")
    manager.add_course(c1)
    s1 = Schedule(sections=[c1.sections[0]])
    manager.save_schedule(s1)

    # Populate 0007
    manager.set_term("0007")
    manager.add_course(c2)
    s2 = Schedule(sections=[c2.sections[0]])
    manager.save_schedule(s2)

    # Save multi-semester state
    manager.save_session_state(str(filepath))
    assert filepath.exists()

    # Verify per-semester separate files were also saved
    semesters_dir = tmp_path / "semesters"
    file_0009 = semesters_dir / "0009.json"
    file_0007 = semesters_dir / "0007.json"
    assert file_0009.exists()
    assert file_0007.exists()

    # Verify loading into new manager restores both semesters independently
    new_manager = ScheduleSessionManager()
    new_manager.load_session_state(str(filepath))

    new_manager.set_term("0009")
    assert "CS101" in new_manager.selected_courses
    assert "MATH101" not in new_manager.selected_courses
    assert len(new_manager.get_saved_schedules()) == 1

    new_manager.set_term("0007")
    assert "MATH101" in new_manager.selected_courses
    assert "CS101" not in new_manager.selected_courses
    assert len(new_manager.get_saved_schedules()) == 1


def test_dedicated_semester_file_save_and_load(tmp_path, mock_courses):
    c1, _ = mock_courses
    manager = ScheduleSessionManager()
    manager.set_term("0009")
    manager.add_course(c1)

    # Save dedicated semester file
    sem_file = tmp_path / "0009_state.json"
    manager.save_semester_state("0009", filepath=str(sem_file))
    assert sem_file.exists()

    # Load into separate manager
    new_manager = ScheduleSessionManager()
    new_manager.load_semester_state(str(sem_file), term_code="0009")
    new_manager.set_term("0009")
    assert "CS101" in new_manager.selected_courses


def test_clear_courses_and_schedules_scoped_to_semester(mock_courses):
    c1, c2 = mock_courses
    manager = ScheduleSessionManager()

    manager.set_term("0009")
    manager.add_course(c1)
    manager.save_schedule(Schedule(sections=[c1.sections[0]]))

    manager.set_term("0007")
    manager.add_course(c2)
    manager.save_schedule(Schedule(sections=[c2.sections[0]]))

    # Clear 0009 courses and schedules
    manager.set_term("0009")
    manager.clear_courses()
    manager.clear_saved_schedules()

    assert len(manager.selected_courses) == 0
    assert len(manager.get_saved_schedules()) == 0

    # 0007 must remain untouched
    manager.set_term("0007")
    assert len(manager.selected_courses) == 1
    assert "MATH101" in manager.selected_courses
    assert len(manager.get_saved_schedules()) == 1




def test_generate_session_id():
    sid1 = generate_session_id()
    sid2 = generate_session_id()
    assert isinstance(sid1, str)
    assert len(sid1) == 12
    assert sid1 != sid2
    assert is_valid_session_id(sid1)


def test_is_valid_session_id():
    assert is_valid_session_id("abc123def456") is True
    assert is_valid_session_id("user_123-test") is True
    assert is_valid_session_id("a1b2c3d4e5f6") is True

    # Invalid cases
    assert is_valid_session_id("") is False
    assert is_valid_session_id(None) is False
    assert is_valid_session_id("short") is False  # < 6 chars
    assert is_valid_session_id("a" * 65) is False  # > 64 chars
    assert is_valid_session_id("../traversal") is False
    assert is_valid_session_id("..\\traversal") is False
    assert is_valid_session_id("user/session") is False
    assert is_valid_session_id("user session") is False
    assert is_valid_session_id("user@session!") is False


def test_get_user_session_filepath(tmp_path):
    base_dir = tmp_path / "sessions"
    sid = "abc123def456"
    path = get_user_session_filepath(sid, base_dir=str(base_dir))
    assert path == base_dir / sid / "session_state.json"

    # Malicious IDs must raise ValueError
    with pytest.raises(ValueError, match="Invalid session ID"):
        get_user_session_filepath("../../etc/passwd", base_dir=str(base_dir))

    with pytest.raises(ValueError, match="Invalid session ID"):
        get_user_session_filepath("invalid sid with spaces", base_dir=str(base_dir))


def test_per_user_session_isolation_with_shared_cache(tmp_path, mock_courses):
    c1, c2 = mock_courses
    shared_cache_dir = tmp_path / "shared_cache"
    sessions_dir = tmp_path / "sessions"

    user_a_sid = generate_session_id()
    user_b_sid = generate_session_id()

    user_a_file = get_user_session_filepath(user_a_sid, base_dir=str(sessions_dir))
    user_b_file = get_user_session_filepath(user_b_sid, base_dir=str(sessions_dir))

    # User A and User B managers pointing to shared cache
    manager_a = ScheduleSessionManager(cache_dir=str(shared_cache_dir), default_term="0009")
    manager_b = ScheduleSessionManager(cache_dir=str(shared_cache_dir), default_term="0009")

    # 1. User A caches catalog data for 0009 ICSI
    manager_a.cache_catalog("0009", "ICSI", [c1, c2])

    # 2. User B can immediately access the shared catalog cache
    cached_for_b = manager_b.get_cached_catalog("0009", "ICSI")
    assert cached_for_b is not None
    assert len(cached_for_b) == 2

    # 3. User A selects course 1 and saves state
    manager_a.add_course(c1)
    manager_a.save_schedule(Schedule(sections=[c1.sections[0]]))
    manager_a.save_session_state(str(user_a_file))

    # 4. User B selects course 2 and saves state
    manager_b.add_course(c2)
    manager_b.save_schedule(Schedule(sections=[c2.sections[0]]))
    manager_b.save_session_state(str(user_b_file))

    # 5. Load fresh managers for each user to verify total isolation
    fresh_manager_a = ScheduleSessionManager(cache_dir=str(shared_cache_dir), default_term="0009")
    fresh_manager_a.load_session_state(str(user_a_file))

    fresh_manager_b = ScheduleSessionManager(cache_dir=str(shared_cache_dir), default_term="0009")
    fresh_manager_b.load_session_state(str(user_b_file))

    # User A only has CS101
    assert "CS101" in fresh_manager_a.selected_courses
    assert "MATH101" not in fresh_manager_a.selected_courses
    assert len(fresh_manager_a.get_saved_schedules()) == 1
    assert fresh_manager_a.get_saved_schedules()[0].sections[0].course_id == "CS101"

    # User B only has MATH101
    assert "MATH101" in fresh_manager_b.selected_courses
    assert "CS101" not in fresh_manager_b.selected_courses
    assert len(fresh_manager_b.get_saved_schedules()) == 1
    assert fresh_manager_b.get_saved_schedules()[0].sections[0].course_id == "MATH101"


def test_max_selected_courses_limit():
    manager = ScheduleSessionManager()
    manager.set_term("0009")

    # Add 15 courses
    for i in range(1, 16):
        c = Course(
            course_id=f"COURSE{i:02d}",
            title=f"Course {i}",
            subject="SUBJ",
            sections=[
                Section(
                    section_id=f"SEC{i:02d}",
                    course_id=f"COURSE{i:02d}",
                    instructor="Prof",
                    meeting_times=parse_time_blocks("M", "09:00 - 10:00")
                )
            ]
        )
        manager.add_course(c)

    assert len(manager.selected_courses) == 15
    assert "COURSE15" in manager.selected_courses

    # Attempt to add a 16th course
    c16 = Course(
        course_id="COURSE16",
        title="Course 16",
        subject="SUBJ",
        sections=[
            Section(
                section_id="SEC16",
                course_id="COURSE16",
                instructor="Prof",
                meeting_times=parse_time_blocks("M", "09:00 - 10:00")
            )
        ]
    )
    manager.add_course(c16)

    # 16th course should be blocked
    assert len(manager.selected_courses) == 15
    assert "COURSE16" not in manager.selected_courses

    # Updating an existing course should still work
    c1_updated = Course(
        course_id="COURSE01",
        title="Course 1 Updated",
        subject="SUBJ",
        sections=[]
    )
    manager.add_course(c1_updated)
    assert len(manager.selected_courses) == 15
    assert manager.selected_courses["COURSE01"].title == "Course 1 Updated"

    # Removing a course drops count to 14
    manager.remove_course("COURSE01")
    assert len(manager.selected_courses) == 14

    # Now adding COURSE16 should succeed
    manager.add_course(c16)
    assert len(manager.selected_courses) == 15
    assert "COURSE16" in manager.selected_courses

    # Multi-semester isolation: another term can still add courses
    manager.set_term("0007")
    assert len(manager.selected_courses) == 0
    c_spring = Course(
        course_id="COURSE_SPRING",
        title="Course Spring",
        subject="SUBJ",
        sections=[
            Section(
                section_id="SEC_SP",
                course_id="COURSE_SPRING",
                instructor="Prof",
                meeting_times=parse_time_blocks("M", "09:00 - 10:00")
            )
        ]
    )
    manager.add_course(c_spring)
    assert len(manager.selected_courses) == 1


def test_max_saved_schedules_limit():
    manager = ScheduleSessionManager()
    manager.set_term("0009")

    # Save 8 distinct schedules
    for i in range(1, 9):
        sec = Section(
            section_id=f"SEC_{i}",
            course_id=f"COURSE_{i}",
            instructor="Prof",
            meeting_times=parse_time_blocks("M", "09:00 - 10:00")
        )
        manager.save_schedule(Schedule(sections=[sec]))

    assert len(manager.get_saved_schedules()) == 8

    # Attempt to save a 9th distinct schedule
    sec9 = Section(
        section_id="SEC_9",
        course_id="COURSE_9",
        instructor="Prof",
        meeting_times=parse_time_blocks("M", "09:00 - 10:00")
    )
    manager.save_schedule(Schedule(sections=[sec9]))

    # 9th schedule should be blocked
    assert len(manager.get_saved_schedules()) == 8
    saved_ids = [s.sections[0].section_id for s in manager.get_saved_schedules()]
    assert "SEC_9" not in saved_ids

    # Remove one saved schedule -> drops count to 7
    manager.remove_saved_schedule(0)
    assert len(manager.get_saved_schedules()) == 7

    # Now saving 9th schedule succeeds
    manager.save_schedule(Schedule(sections=[sec9]))
    assert len(manager.get_saved_schedules()) == 8
    assert "SEC_9" in [s.sections[0].section_id for s in manager.get_saved_schedules()]

    # Multi-semester isolation: another term can still save schedules
    manager.set_term("0007")
    assert len(manager.get_saved_schedules()) == 0
    sec_sp = Section(
        section_id="SEC_SP_1",
        course_id="COURSE_SP_1",
        instructor="Prof",
        meeting_times=parse_time_blocks("M", "09:00 - 10:00")
    )
    manager.save_schedule(Schedule(sections=[sec_sp]))
    assert len(manager.get_saved_schedules()) == 1

