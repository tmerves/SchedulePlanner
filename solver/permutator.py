from typing import List, Set, Optional
from models.schema import Course, Section, Schedule
from utils.time_utils import do_sections_conflict

class ScheduleSolver:
    @staticmethod
    def find_valid_schedules(
        courses: List[Course], 
        excluded_section_ids: Optional[Set[str]] = None
    ) -> List[Schedule]:
        """
        Finds all valid, non-overlapping schedules from the list of courses.
        Uses recursive backtracking with early pruning to avoid evaluating
        conflicting section branches.
        
        Filters out any sections whose ID is present in excluded_section_ids.
        """
        if not courses:
            return []

        if excluded_section_ids is None:
            excluded_section_ids = set()

        # Filter the sections for each course
        filtered_courses: List[List[Section]] = []
        for course in courses:
            valid_sections = [
                sec for sec in course.sections 
                if sec.section_id not in excluded_section_ids
            ]
            filtered_courses.append(valid_sections)

        schedules: List[Schedule] = []

        def backtrack(course_idx: int, current_sections: List[Section]) -> None:
            if course_idx == len(filtered_courses):
                # Found a valid schedule. Build a Schedule object.
                schedules.append(Schedule(sections=list(current_sections), has_overlap=False))
                return

            for sec in filtered_courses[course_idx]:
                # Check for conflicts with already selected sections in the schedule
                conflict = False
                for existing_sec in current_sections:
                    if do_sections_conflict(existing_sec, sec):
                        conflict = True
                        break

                if not conflict:
                    current_sections.append(sec)
                    backtrack(course_idx + 1, current_sections)
                    current_sections.pop()

        backtrack(0, [])
        return schedules
