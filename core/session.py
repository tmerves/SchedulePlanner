import json
import os
import re
import secrets
from pathlib import Path
from typing import List, Set, Optional, Dict, Any
from models.schema import Course, Section, Schedule
from solver.permutator import ScheduleSolver


MAX_SELECTED_COURSES: int = 15
MAX_SAVED_SCHEDULES: int = 8


class ScheduleSessionManager:
    MAX_SELECTED_COURSES: int = MAX_SELECTED_COURSES
    MAX_SAVED_SCHEDULES: int = MAX_SAVED_SCHEDULES

    def __init__(self, cache_dir: str = "data/cache", default_term: Optional[str] = None):
        """
        Initializes the session manager with distinct per-semester storage.
        """
        self.cache_dir = Path(cache_dir)
        self._active_term_code: str = default_term if default_term is not None else "default"
        # Internal state tracking per-semester data:
        # term_code -> { "selected_courses": Dict[str, Course], "excluded_section_ids": Set[str], "saved_schedules": List[Schedule] }
        self._semesters: Dict[str, Dict[str, Any]] = {}
        self._ensure_term_exists(self._active_term_code)

    def _ensure_term_exists(self, term_code: str) -> None:
        """Helper to ensure internal data structure exists for a given term_code."""
        if term_code not in self._semesters:
            self._semesters[term_code] = {
                "selected_courses": {},
                "excluded_section_ids": set(),
                "saved_schedules": [],
            }

    # --- Active Semester & Properties for Backward Compatibility ---

    @property
    def active_term_code(self) -> str:
        return self._active_term_code

    @active_term_code.setter
    def active_term_code(self, value: str) -> None:
        self.set_term(value)

    @property
    def selected_courses(self) -> Dict[str, Course]:
        self._ensure_term_exists(self._active_term_code)
        return self._semesters[self._active_term_code]["selected_courses"]

    @selected_courses.setter
    def selected_courses(self, value: Dict[str, Course]) -> None:
        self._ensure_term_exists(self._active_term_code)
        self._semesters[self._active_term_code]["selected_courses"] = value

    @property
    def excluded_section_ids(self) -> Set[str]:
        self._ensure_term_exists(self._active_term_code)
        return self._semesters[self._active_term_code]["excluded_section_ids"]

    @excluded_section_ids.setter
    def excluded_section_ids(self, value: Set[str]) -> None:
        self._ensure_term_exists(self._active_term_code)
        self._semesters[self._active_term_code]["excluded_section_ids"] = value

    @property
    def saved_schedules(self) -> List[Schedule]:
        self._ensure_term_exists(self._active_term_code)
        return self._semesters[self._active_term_code]["saved_schedules"]

    @saved_schedules.setter
    def saved_schedules(self, value: List[Schedule]) -> None:
        self._ensure_term_exists(self._active_term_code)
        self._semesters[self._active_term_code]["saved_schedules"] = value

    def set_term(self, term_code: str) -> None:
        """
        Switches the active semester. Initializes state for the semester if not already present.
        """
        self._active_term_code = term_code
        self._ensure_term_exists(term_code)

    def get_term(self) -> str:
        """Returns the current active semester term code."""
        return self._active_term_code

    def get_known_terms(self) -> List[str]:
        """Returns all semester term codes currently stored in memory."""
        return sorted(list(self._semesters.keys()))

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

    def _serialize_semester(self, term_code: str) -> dict:
        """Serializes a single semester's data to a dict."""
        self._ensure_term_exists(term_code)
        state = self._semesters[term_code]
        return {
            "term_code": term_code,
            "selected_courses": [self._course_to_json_data(c) for c in state["selected_courses"].values()],
            "excluded_section_ids": list(state["excluded_section_ids"]),
            "saved_schedules": [self._schedule_to_json_data(s) for s in state["saved_schedules"]],
        }

    def _deserialize_semester(self, term_code: str, data: dict) -> None:
        """Deserializes a dictionary into a specific semester's state."""
        self._ensure_term_exists(term_code)
        courses = {}
        for c_data in data.get("selected_courses", []):
            c_obj = self._course_from_dict(c_data)
            if c_obj.term_code is None and term_code != "default":
                c_obj.term_code = term_code
            for sec in c_obj.sections:
                if sec.term_code is None and term_code != "default":
                    sec.term_code = term_code
            courses[c_obj.course_id] = c_obj

        excluded = set(data.get("excluded_section_ids", []))
        saved = []
        for s_data in data.get("saved_schedules", []):
            s_obj = self._schedule_from_dict(s_data)
            if s_obj.term_code is None and term_code != "default":
                s_obj.term_code = term_code
            saved.append(s_obj)

        self._semesters[term_code] = {
            "selected_courses": courses,
            "excluded_section_ids": excluded,
            "saved_schedules": saved,
        }

    # --- Catalog Caching ---

    def cache_catalog(self, term_code: str, subject_code: str, courses: List[Course]) -> None:
        """
        Saves parsed courses to data/cache/{term_code}_{subject_code}.json.
        Ensures each course's term_code is properly tagged.
        """
        if not courses:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        filepath = self.cache_dir / f"{term_code}_{subject_code}.json"
        
        for course in courses:
            if course.term_code is None:
                course.term_code = term_code
            for sec in course.sections:
                if sec.term_code is None:
                    sec.term_code = term_code

        serialized_courses = [self._course_to_json_data(course) for course in courses]
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(serialized_courses, f, indent=4, ensure_ascii=False)

    def get_cached_catalog(self, term_code: str, subject_code: str, force_refresh: bool = False) -> Optional[List[Course]]:
        """
        Retrieves cached courses if the file exists on disk.
        Ensures term_code is set on retrieved Course objects.
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
            courses = []
            for course_data in data:
                c = self._course_from_dict(course_data)
                if c.term_code is None:
                    c.term_code = term_code
                for sec in c.sections:
                    if sec.term_code is None:
                        sec.term_code = term_code
                courses.append(c)
            return courses
        except Exception:
            filepath.unlink(missing_ok=True)
            return None

    # --- Course Selection Management ---

    def add_course(self, course: Course, term_code: Optional[str] = None) -> None:
        """
        Adds a course to the user's working selection for the given or active semester.
        Strictly prevents adding courses from one semester to another.
        Limits the number of selected courses per semester to MAX_SELECTED_COURSES (15).
        """
        target_term = term_code or self._active_term_code
        self._ensure_term_exists(target_term)

        # Cross-semester check
        if course.term_code is not None and target_term != "default" and course.term_code != target_term:
            raise ValueError(
                f"Cannot add course '{course.course_id}' from semester '{course.term_code}' "
                f"to semester '{target_term}'."
            )

        # Course limit check: block addition if limit reached and course is not already selected
        if course.course_id not in self._semesters[target_term]["selected_courses"]:
            if len(self._semesters[target_term]["selected_courses"]) >= self.MAX_SELECTED_COURSES:
                return

        # Assign semester term_code if not already set
        if course.term_code is None and target_term != "default":
            course.term_code = target_term
            for sec in course.sections:
                if sec.term_code is None:
                    sec.term_code = target_term

        self._semesters[target_term]["selected_courses"][course.course_id] = course

    def remove_course(self, course_id: str, term_code: Optional[str] = None) -> None:
        """
        Removes a course from the given or active semester.
        """
        target_term = term_code or self._active_term_code
        self._ensure_term_exists(target_term)
        self._semesters[target_term]["selected_courses"].pop(course_id, None)

    def clear_courses(self, term_code: Optional[str] = None) -> None:
        """
        Clears all selected courses and exclusions for the given or active semester.
        """
        target_term = term_code or self._active_term_code
        self._ensure_term_exists(target_term)
        self._semesters[target_term]["selected_courses"].clear()
        self._semesters[target_term]["excluded_section_ids"].clear()

    # --- Section Toggling / Exclusion ---

    def toggle_section(self, section_id: str, is_active: bool, term_code: Optional[str] = None) -> None:
        """
        Enables or disables a section for schedule permutation solving in the given or active semester.
        """
        target_term = term_code or self._active_term_code
        self._ensure_term_exists(target_term)
        excluded = self._semesters[target_term]["excluded_section_ids"]
        if is_active:
            excluded.discard(section_id)
        else:
            excluded.add(section_id)

    # --- Solver Preparation ---

    def get_active_courses(self, term_code: Optional[str] = None) -> List[Course]:
        """
        Returns the selected courses for the given or active semester with excluded sections filtered out.
        """
        target_term = term_code or self._active_term_code
        self._ensure_term_exists(target_term)
        selected = self._semesters[target_term]["selected_courses"]
        excluded = self._semesters[target_term]["excluded_section_ids"]

        active_courses = []
        for course in selected.values():
            active_sections = [
                sec for sec in course.sections
                if sec.section_id not in excluded
            ]
            new_course = Course(
                course_id=course.course_id,
                title=course.title,
                subject=course.subject,
                sections=active_sections,
                term_code=course.term_code or (target_term if target_term != "default" else None)
            )
            active_courses.append(new_course)
        return active_courses

    def solve_schedule(self, term_code: Optional[str] = None) -> List[Schedule]:
        """
        Feeds get_active_courses() for the given or active semester into ScheduleSolver.find_valid_schedules().
        """
        return ScheduleSolver.find_valid_schedules(self.get_active_courses(term_code))

    # --- Saved Schedules Management ---

    def save_schedule(self, schedule: Schedule, term_code: Optional[str] = None) -> None:
        """
        Saves a generated Schedule to the user's saved list for the given or active semester.
        Strictly prevents saving schedules from one semester to another.
        Limits the number of saved schedules per semester to MAX_SAVED_SCHEDULES (8).
        """
        target_term = term_code or self._active_term_code
        self._ensure_term_exists(target_term)

        # Cross-semester check
        if schedule.term_code is not None and target_term != "default" and schedule.term_code != target_term:
            raise ValueError(
                f"Cannot save schedule from semester '{schedule.term_code}' to semester '{target_term}'."
            )
        if schedule.term_code is None and target_term != "default":
            schedule.term_code = target_term

        saved_list = self._semesters[target_term]["saved_schedules"]
        new_sec_ids = {sec.section_id for sec in schedule.sections}
        for saved in saved_list:
            saved_sec_ids = {sec.section_id for sec in saved.sections}
            if new_sec_ids == saved_sec_ids:
                return  # Duplicate schedule already saved

        # Saved schedules limit check: block saving if limit reached
        if len(saved_list) >= self.MAX_SAVED_SCHEDULES:
            return

        saved_list.append(schedule)

    def remove_saved_schedule(self, index: int, term_code: Optional[str] = None) -> None:
        """
        Removes a saved schedule by its index for the given or active semester.
        """
        target_term = term_code or self._active_term_code
        self._ensure_term_exists(target_term)
        saved_list = self._semesters[target_term]["saved_schedules"]
        if 0 <= index < len(saved_list):
            saved_list.pop(index)

    def clear_saved_schedules(self, term_code: Optional[str] = None) -> None:
        """
        Clears all saved schedules for the given or active semester.
        """
        target_term = term_code or self._active_term_code
        self._ensure_term_exists(target_term)
        self._semesters[target_term]["saved_schedules"].clear()

    def get_saved_schedules(self, term_code: Optional[str] = None) -> List[Schedule]:
        """
        Returns the list of saved schedules for the given or active semester.
        """
        target_term = term_code or self._active_term_code
        self._ensure_term_exists(target_term)
        return self._semesters[target_term]["saved_schedules"]

    # --- JSON Persistence (Separate Per-Semester & Unified Session) ---

    def save_semester_state(self, term_code: str, filepath: Optional[str] = None) -> str:
        """
        Serializes a single semester's state to a dedicated separate file.
        Default destination is data/semesters/{term_code}.json.
        """
        if filepath is None:
            semesters_dir = Path("data/semesters")
            filepath_path = semesters_dir / f"{term_code}.json"
        else:
            filepath_path = Path(filepath)

        filepath_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = self._serialize_semester(term_code)
        with open(filepath_path, "w", encoding="utf-8") as f:
            json.dump(serialized, f, indent=4, ensure_ascii=False)
        return str(filepath_path)

    def load_semester_state(self, filepath: str, term_code: Optional[str] = None) -> None:
        """
        Loads state from a single semester's dedicated JSON file.
        """
        filepath_path = Path(filepath)
        if not filepath_path.exists():
            return

        with open(filepath_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        target_term = term_code or data.get("term_code") or filepath_path.stem
        self._deserialize_semester(target_term, data)

    def save_session_state(self, filepath: str, term_code: Optional[str] = None) -> None:
        """
        Serializes application state.
        - If term_code is specified: saves only that semester's state to filepath.
        - If term_code is None: saves all distinct semesters separately under a 'semesters'
          map in filepath, AND saves individual per-semester files in data/semesters/{term_code}.json.
        """
        filepath_path = Path(filepath)
        filepath_path.parent.mkdir(parents=True, exist_ok=True)

        if term_code is not None:
            # Save single semester
            serialized = self._serialize_semester(term_code)
            with open(filepath_path, "w", encoding="utf-8") as f:
                json.dump(serialized, f, indent=4, ensure_ascii=False)
            return

        # Multi-semester state persistence: each semester stored distinctly
        semesters_dict = {}
        semesters_dir = filepath_path.parent / "semesters"
        for term in self._semesters:
            if term == "default" and self._active_term_code != "default" and not self._semesters[term]["selected_courses"] and not self._semesters[term]["saved_schedules"]:
                continue
            sem_data = self._serialize_semester(term)
            semesters_dict[term] = sem_data
            # Also save separate per-semester files
            if term != "default":
                sem_file = semesters_dir / f"{term}.json"
                sem_file.parent.mkdir(parents=True, exist_ok=True)
                with open(sem_file, "w", encoding="utf-8") as f:
                    json.dump(sem_data, f, indent=4, ensure_ascii=False)

        state = {
            "active_term": self._active_term_code,
            "semesters": semesters_dict,
        }
        with open(filepath_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4, ensure_ascii=False)

    def load_session_state(self, filepath: str, term_code: Optional[str] = None) -> None:
        """
        Deserializes session state from a file.
        Supports:
        1. Multi-semester format with distinct per-semester states.
        2. Dedicated per-semester files in data/semesters/.
        3. Legacy flat files (migrating them under their semester or active term).
        """
        filepath_path = Path(filepath)
        if filepath_path.exists():
            with open(filepath_path, "r", encoding="utf-8") as f:
                state = json.load(f)

            if "semesters" in state and isinstance(state["semesters"], dict):
                # Multi-semester format
                for term, term_data in state["semesters"].items():
                    target_term = term
                    # If legacy 'default' term data exists and we have an active real term, migrate it
                    if term == "default" and self._active_term_code != "default" and (self._active_term_code not in state["semesters"] or not state["semesters"][self._active_term_code]["selected_courses"]):
                        target_term = self._active_term_code
                    if term_code is None or target_term == term_code:
                        self._deserialize_semester(target_term, term_data)
                if term_code is None and "active_term" in state and state["active_term"] != "default":
                    self._active_term_code = state["active_term"]
                    self._ensure_term_exists(self._active_term_code)
            elif "selected_courses" in state:
                # Legacy flat format or single semester file
                target_term = term_code or state.get("term_code") or self._active_term_code
                self._deserialize_semester(target_term, state)
                if self._active_term_code == "default" and target_term != "default":
                    self._active_term_code = target_term

        # Check for any separate per-semester files in data/semesters/
        semesters_dir = filepath_path.parent / "semesters"
        if semesters_dir.exists() and semesters_dir.is_dir():
            for sem_file in semesters_dir.glob("*.json"):
                term = sem_file.stem
                if term not in self._semesters or not self._semesters[term]["selected_courses"]:
                    try:
                        self.load_semester_state(str(sem_file), term_code=term)
                    except Exception:
                        pass


# ----------------------------------------------------------------------
# Session ID & Per-User Storage Helpers
# ----------------------------------------------------------------------

SESSION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{6,64}$")


def generate_session_id(length: int = 12) -> str:
    """
    Generates a secure, URL-safe random alphanumeric session ID.
    Default length is 12 hex characters.
    """
    return secrets.token_hex(max(3, length // 2))


def is_valid_session_id(session_id: Optional[str]) -> bool:
    """
    Validates that a session_id is a safe alphanumeric string without path traversal risks.
    """
    if not session_id or not isinstance(session_id, str):
        return False
    return bool(SESSION_ID_REGEX.match(session_id))


def get_user_session_filepath(session_id: str, base_dir: str = "data/sessions") -> Path:
    """
    Resolves the filesystem path for a user's isolated session state.
    Raises ValueError if session_id contains invalid or traversal characters.
    """
    if not is_valid_session_id(session_id):
        raise ValueError(f"Invalid session ID: {session_id!r}")
    return Path(base_dir) / session_id / "session_state.json"

