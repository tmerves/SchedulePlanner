import json
import os
from pathlib import Path
from typing import List, Set, Optional, Dict
from models.schema import Course, Section, Schedule
from solver.permutator import ScheduleSolver


class ScheduleSessionManager:
    def __init__(self, cache_dir: str = "data/cache"):
        """
        Initializes the session manager.
        """
        self.cache_dir = Path(cache_dir)
        # Internal state tracking selected courses across different subjects by course_id
        self.selected_courses: Dict[str, Course] = {}
        # Keep track of excluded section ids
        self.excluded_section_ids: Set[str] = set()
        # Keep track of saved schedules
        self.saved_schedules: List[Schedule] = []

    def _course_to_json_data(self, course: Course) -> dict:
        """
        Helper to convert a Course object to standard JSON-compatible dictionary.
        """
        if hasattr(course, "model_dump_json"):
            return json.loads(course.model_dump_json())
        return json.loads(course.json())

    def _course_from_dict(self, data: dict) -> Course:
        """
        Helper to convert a dictionary to a Course object.
        """
        if hasattr(Course, "model_validate"):
            return Course.model_validate(data)
        return Course(**data)

    def _schedule_to_json_data(self, schedule: Schedule) -> dict:
        """
        Helper to convert a Schedule object to standard JSON-compatible dictionary.
        """
        if hasattr(schedule, "model_dump_json"):
            return json.loads(schedule.model_dump_json())
        return json.loads(schedule.json())

    def _schedule_from_dict(self, data: dict) -> Schedule:
        """
        Helper to convert a dictionary to a Schedule object.
        """
        if hasattr(Schedule, "model_validate"):
            return Schedule.model_validate(data)
        return Schedule(**data)

    # --- Catalog Caching ---

    def cache_catalog(self, term_code: str, subject_code: str, courses: List[Course]) -> None:
        """
        Saves parsed courses to data/cache/{term_code}_{subject_code}.json.
        """
        if not courses:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        filepath = self.cache_dir / f"{term_code}_{subject_code}.json"
        
        serialized_courses = [self._course_to_json_data(course) for course in courses]
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(serialized_courses, f, indent=4, ensure_ascii=False)

    def get_cached_catalog(self, term_code: str, subject_code: str, force_refresh: bool = False) -> Optional[List[Course]]:
        """
        Retrieves cached courses if the file exists on disk.
        """
        if force_refresh:
            return None

        filepath = self.cache_dir / f"{term_code}_{subject_code}.json"
        if not filepath.exists():
            return None
        
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list) or not data:
                filepath.unlink(missing_ok=True)
                return None
            return [self._course_from_dict(course_data) for course_data in data]
        except Exception:
            filepath.unlink(missing_ok=True)
            return None

    # --- Course Selection Management ---

    def add_course(self, course: Course) -> None:
        """
        Adds a course to the user's working selection (avoiding duplicates by course_id).
        """
        self.selected_courses[course.course_id] = course

    def remove_course(self, course_id: str) -> None:
        """
        Removes a course from selection.
        """
        if course_id in self.selected_courses:
            del self.selected_courses[course_id]

    def clear_courses(self) -> None:
        """
        Resets the selected list.
        """
        self.selected_courses.clear()

    # --- Section Toggling / Exclusion ---

    def toggle_section(self, section_id: str, is_active: bool) -> None:
        """
        Enables or excludes a specific section from consideration.
        If is_active is True, the section is active (not excluded).
        If is_active is False, the section is excluded.
        """
        if is_active:
            self.excluded_section_ids.discard(section_id)
        else:
            self.excluded_section_ids.add(section_id)

    # --- Solver Preparation ---

    def get_active_courses(self) -> List[Course]:
        """
        Returns the selected Course objects, stripping out any Section
        whose section_id is present in excluded_section_ids.
        """
        active_courses = []
        for course in self.selected_courses.values():
            active_sections = [
                sec for sec in course.sections
                if sec.section_id not in self.excluded_section_ids
            ]
            new_course = Course(
                course_id=course.course_id,
                title=course.title,
                subject=course.subject,
                sections=active_sections
            )
            active_courses.append(new_course)
        return active_courses

    def solve_schedule(self) -> List[Schedule]:
        """
        Feeds get_active_courses() directly into ScheduleSolver.find_valid_schedules() and returns the results.
        """
        return ScheduleSolver.find_valid_schedules(self.get_active_courses())

    # --- Saved Schedules Management ---

    def save_schedule(self, schedule: Schedule) -> None:
        """
        Saves a generated Schedule to the user's saved list, avoiding exact duplicates.
        Two schedules are duplicates if they have the exact same section IDs.
        """
        new_sec_ids = {sec.section_id for sec in schedule.sections}
        for saved in self.saved_schedules:
            saved_sec_ids = {sec.section_id for sec in saved.sections}
            if new_sec_ids == saved_sec_ids:
                return  # Duplicate schedule already saved
        self.saved_schedules.append(schedule)

    def remove_saved_schedule(self, index: int) -> None:
        """
        Removes a saved schedule by its index.
        """
        if 0 <= index < len(self.saved_schedules):
            self.saved_schedules.pop(index)

    def clear_saved_schedules(self) -> None:
        """
        Clears all saved schedules.
        """
        self.saved_schedules.clear()

    def get_saved_schedules(self) -> List[Schedule]:
        """
        Returns the list of saved schedules.
        """
        return self.saved_schedules

    # --- JSON Persistence ---

    def save_session_state(self, filepath: str) -> None:
        """
        Serializes the user's selected courses, excluded sections, and saved schedules to a file.
        """
        filepath_path = Path(filepath)
        filepath_path.parent.mkdir(parents=True, exist_ok=True)
        
        serialized_courses = [self._course_to_json_data(course) for course in self.selected_courses.values()]
        serialized_saved_schedules = [self._schedule_to_json_data(sched) for sched in self.saved_schedules]
        state = {
            "selected_courses": serialized_courses,
            "excluded_section_ids": list(self.excluded_section_ids),
            "saved_schedules": serialized_saved_schedules
        }
        with open(filepath_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4, ensure_ascii=False)

    def load_session_state(self, filepath: str) -> None:
        """
        Deserializes the user's selected courses, excluded sections, and saved schedules from a file.
        """
        filepath_path = Path(filepath)
        if not filepath_path.exists():
            return
        
        with open(filepath_path, "r", encoding="utf-8") as f:
            state = json.load(f)
            
        self.selected_courses.clear()
        for course_data in state.get("selected_courses", []):
            course_obj = self._course_from_dict(course_data)
            self.selected_courses[course_obj.course_id] = course_obj
            
        self.excluded_section_ids = set(state.get("excluded_section_ids", []))
        
        self.saved_schedules = []
        for sched_data in state.get("saved_schedules", []):
            self.saved_schedules.append(self._schedule_from_dict(sched_data))
