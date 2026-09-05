import os
from pathlib import Path
import pytest
from core.session import ScheduleSessionManager
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
