from typing import List, Set, Optional
from models.schema import Course, Section, Schedule
from utils.time_utils import do_sections_conflict

class ScheduleSolver:
    @staticmethod
    def get_course_options(
        course: Course, 
        excluded_section_ids: Set[str]
    ) -> List[List[Section]]:
        """
        Generates valid selectable bundles of sections for a course.
        For standalone lectures, each bundle is [lecture].
        For lectures requiring a discussion/lab, each bundle is [lecture, discussion].
        Prunes any options involving sections in excluded_section_ids.
        """
        all_linked_targets = {
            l_id 
            for s in course.sections 
            for l_id in (s.linked_sections or [])
        }

        available_map = {
            s.section_id: s 
            for s in course.sections 
            if s.section_id not in excluded_section_ids
        }

        primary_sections = [
            s for s in course.sections 
            if s.section_id not in all_linked_targets
        ]

        if not primary_sections:
            primary_sections = list(course.sections)

        options: List[List[Section]] = []

        for sec in primary_sections:
            if sec.section_id not in available_map:
                continue

            valid_linked_ids = [
                l_id for l_id in sec.linked_sections 
                if any(s.section_id == l_id for s in course.sections)
            ]

            if valid_linked_ids:
                for l_id in valid_linked_ids:
                    if l_id in available_map:
                        linked_sec = available_map[l_id]
                        if not do_sections_conflict(sec, linked_sec):
                            options.append([sec, linked_sec])
            else:
                options.append([sec])

        return options

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
        Supports bundled sections (e.g. Lecture + Discussion).
        """
        if not courses:
            return []

        # Check that courses do not span across multiple different semesters
        terms = {c.term_code for c in courses if getattr(c, "term_code", None) is not None}
        if len(terms) > 1:
            raise ValueError(
                f"Cannot generate schedules with courses from different semesters: {sorted(list(terms))}"
            )
        schedule_term = list(terms)[0] if terms else None

        if excluded_section_ids is None:
            excluded_section_ids = set()

        # Generate options for each course
        course_options: List[List[List[Section]]] = []
        for course in courses:
            options = ScheduleSolver.get_course_options(course, excluded_section_ids)
            if not options:
                return []
            course_options.append(options)

        schedules: List[Schedule] = []

        def backtrack(course_idx: int, current_sections: List[Section]) -> None:
            if course_idx == len(course_options):
                schedules.append(
                    Schedule(
                        sections=list(current_sections), 
                        has_overlap=False, 
                        term_code=schedule_term
                    )
                )
                return

            for bundle in course_options[course_idx]:
                conflict = False
                for sec in bundle:
                    for existing_sec in current_sections:
                        if do_sections_conflict(existing_sec, sec):
                            conflict = True
                            break
                    if conflict:
                        break

                if not conflict:
                    current_sections.extend(bundle)
                    backtrack(course_idx + 1, current_sections)
                    for _ in bundle:
                        current_sections.pop()

        backtrack(0, [])
        return schedules
