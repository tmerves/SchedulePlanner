import argparse
import sys
from pathlib import Path

# Add project root to sys.path to support execution from any directory
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.session import ScheduleSessionManager
from scraper.client import UAlbanyScraperClient

from typing import List, Dict
from models.schema import Course


def select_semester_interactive(client: UAlbanyScraperClient) -> str:
    print("\n--- Step 1: Select Semester ---")
    print("Fetching available semesters...")
    semesters = client.get_available_semesters()
    
    for idx, sem in enumerate(semesters):
        print(f"  [{idx + 1}] {sem['name']} (Code: {sem['value']})")
    
    while True:
        choice = input(f"\nSelect semester [1-{len(semesters)}] (default 1): ").strip()
        if not choice:
            selected = semesters[0]["value"]
            print(f"Selected default: {semesters[0]['name']}")
            return selected
        if choice.isdigit() and 1 <= int(choice) <= len(semesters):
            selected = semesters[int(choice) - 1]["value"]
            print(f"Selected: {semesters[int(choice) - 1]['name']}")
            return selected
        match = next((s for s in semesters if s["value"] == choice), None)
        if match:
            print(f"Selected: {match['name']}")
            return match["value"]
        print("Invalid selection. Please enter a valid number or semester code.")


def select_subjects_interactive(client: UAlbanyScraperClient, semester: str) -> List[str]:
    print("\n--- Step 2: Select Subjects ---")
    print("Loading available subjects...")
    available_subjects = client.get_available_subjects(term=semester)
    subject_map = {s["code"].upper(): s["label"] for s in available_subjects}

    selected_subjects: List[str] = []
    
    print(f"Loaded {len(available_subjects)} academic subjects.")
    print("Commands:")
    print("  - Type subject codes directly (e.g., 'ICSI AMAT' or 'BACC')")
    print("  - Type '/search <keyword>' to search subjects by keyword (e.g., '/search math')")
    print("  - Type '/list' to see first 25 subjects")
    print("  - Type 'done' or press Enter when finished adding subjects")
    
    while True:
        if selected_subjects:
            names = [f"{c} ({subject_map.get(c, '')})" for c in selected_subjects]
            print(f"\nCurrently selected subjects: {', '.join(names)}")
        else:
            print("\nNo subjects selected yet.")
        
        user_input = input("Enter subject code(s), '/search <keyword>', or 'done': ").strip()
        
        if not user_input or user_input.lower() == "done":
            if selected_subjects:
                break
            print("Please select at least one subject before continuing.")
            continue

        if user_input.startswith("/search "):
            kw = user_input.split(" ", 1)[1].strip().lower()
            matches = [s for s in available_subjects if kw in s["label"].lower() or kw in s["code"].lower()]
            if matches:
                print(f"\nMatching subjects for '{kw}':")
                for s in matches[:15]:
                    print(f"  {s['code']:<6} - {s['label']}")
                if len(matches) > 15:
                    print(f"  ... and {len(matches) - 15} more matches.")
            else:
                print(f"No subjects found matching '{kw}'.")
            continue

        if user_input.lower() == "/list":
            print("\nFirst 25 available subjects:")
            for s in available_subjects[:25]:
                print(f"  {s['code']:<6} - {s['label']}")
            continue

        tokens = [t.strip().upper() for t in user_input.replace(",", " ").split() if t.strip()]
        for token in tokens:
            if token in subject_map:
                if token not in selected_subjects:
                    selected_subjects.append(token)
                    print(f"  Added: {token} - {subject_map[token]}")
                else:
                    print(f"  {token} is already in your selected list.")
            else:
                print(f"  Warning: '{token}' is not a recognized subject code. Type '/search {token}' to find it.")

    return selected_subjects


def browse_and_select_courses(
    session: ScheduleSessionManager,
    client: UAlbanyScraperClient,
    semester: str,
    subjects: List[str]
) -> None:
    print("\n" + "=" * 60)
    print("Step 3: Browse Courses & Add to Selection")
    print("=" * 60)

    for subj in subjects:
        print(f"\n--- Subject: {subj} ---")
        cached = session.get_cached_catalog(semester, subj)
        if cached is not None:
            print(f"[CACHE HIT] Loaded catalog for {subj} from cache ({len(cached)} courses).")
            courses = cached
        else:
            print(f"[FETCHING] Querying schedule for {subj} (Semester {semester})...")
            try:
                courses = client.fetch_courses(subj, term=semester)
                print(f"Successfully retrieved {len(courses)} courses.")
                session.cache_catalog(semester, subj, courses)
            except Exception as e:
                print(f"Error fetching catalog for {subj}: {e}")
                courses = []

        if not courses:
            print(f"No courses available for subject {subj}.")
            continue

        courses_with_sections = [c for c in courses if len(c.sections) >= 1]
        print(f"\nAvailable courses in {subj} ({len(courses_with_sections)} courses with active sections):")
        for i, c in enumerate(courses_with_sections[:20]):
            print(f"  [{i+1}] {c.course_id:<10} - {c.title} ({len(c.sections)} sections)")
        if len(courses_with_sections) > 20:
            print(f"  ... showing first 20 of {len(courses_with_sections)} courses.")

        while True:
            choice = input(
                f"\nSelect courses for {subj} by typing index numbers (e.g. '1, 3'), "
                f"course IDs (e.g. '{courses_with_sections[0].course_id}'), "
                f"or press Enter to skip/finish {subj}: "
            ).strip()

            if not choice:
                break

            parts = [p.strip() for p in choice.replace(",", " ").split() if p.strip()]
            added_any = False
            for p in parts:
                course_to_add = None
                if p.isdigit():
                    idx = int(p) - 1
                    if 0 <= idx < len(courses_with_sections):
                        course_to_add = courses_with_sections[idx]
                    else:
                        print(f"Index {p} is out of range.")
                else:
                    course_to_add = next(
                        (c for c in courses_with_sections if c.course_id.upper() == p.upper()),
                        None
                    )
                    if not course_to_add:
                        print(f"Could not find course with ID '{p}'.")

                if course_to_add:
                    session.add_course(course_to_add)
                    print(f"  [ADDED] {course_to_add.course_id} - {course_to_add.title}")
                    added_any = True

            if added_any:
                break



def main():
    parser = argparse.ArgumentParser(description="CLI tool for course schedule planning.")
    parser.add_argument("--semester", type=str, default=None, help="Semester code (e.g. '0009')")
    parser.add_argument("--subjects", nargs="+", default=None, help="Subject codes (e.g. 'ICSI AMAT')")
    parser.add_argument("--demo", action="store_true", help="Demo mode: auto-selects first course from each subject")
    args = parser.parse_args()

    print("=" * 60)
    print("  University at Albany - Course Schedule Planner (CLI)")
    print("=" * 60)

    session = ScheduleSessionManager()
    client = UAlbanyScraperClient()

    # Step 1: Select Semester
    if args.semester:
        semester = args.semester
        print(f"Semester: {semester}")
    else:
        semester = select_semester_interactive(client)
    client.term = semester

    # Step 2: Select Subjects
    if args.subjects:
        subjects = [s.upper() for s in args.subjects]
        print(f"Subjects: {', '.join(subjects)}")
    elif args.demo:
        subjects = ["ICSI", "AMAT"]
        print(f"Demo Subjects: {', '.join(subjects)}")
    else:
        subjects = select_subjects_interactive(client, semester)

    # Step 3: Browse & Select Courses
    if args.demo:
        print("\n[DEMO MODE] Auto-selecting first course with active sections...")
        for subj in subjects:
            courses = session.get_cached_catalog(semester, subj)
            if courses is None:
                courses = client.fetch_courses(subj, term=semester)
                session.cache_catalog(semester, subj, courses)
            for c in courses:
                if len(c.sections) >= 1:
                    session.add_course(c)
                    print(f"  Auto-selected: {c.course_id} - {c.title}")
                    break
    else:
        browse_and_select_courses(session, client, semester, subjects)


    # --- Step C: Section Inspection & Toggling ---
    print("\n" + "=" * 60)
    print("Step C: Section Inspection & Toggling")
    print("=" * 60)

    # Retrieve selected courses from session
    selected_courses = list(session.selected_courses.values())
    if not selected_courses:
        print("No courses selected. Exiting.")
        sys.exit(0)

    def display_selected_courses_and_sections():
        print("\n--- Selected Courses & Sections ---")
        for course in selected_courses:
            print(f"\nCourse: {course.course_id} - {course.title}")
            for section in course.sections:
                status = "[ACTIVE]" if section.section_id not in session.excluded_section_ids else "[EXCLUDED]"
                meeting_strs = []
                for tb in section.meeting_times:
                    meeting_strs.append(f"{tb.day} {tb.start_time.strftime('%H:%M')} - {tb.end_time.strftime('%H:%M')}")
                meetings_formatted = ", ".join(meeting_strs) if meeting_strs else "No meeting times"
                print(f"  Section ID: {section.section_id:<10} | Instructor: {section.instructor:<25} | Location: {section.location:<25} | Meetings: {meetings_formatted} {status}")

    display_selected_courses_and_sections()

    # Ask if they want to toggle any sections
    print("\nSection Toggling Test:")
    while True:
        toggle_input = input("Enter a Section ID to toggle (exclude/include), or press Enter to proceed to solver: ").strip()
        if not toggle_input:
            break
        
        all_sections = {sec.section_id: sec for c in selected_courses for sec in c.sections}
        if toggle_input in all_sections:
            is_currently_excluded = toggle_input in session.excluded_section_ids
            new_active_status = is_currently_excluded
            session.toggle_section(toggle_input, is_active=new_active_status)
            action = "included (ACTIVE)" if new_active_status else "excluded (EXCLUDED)"
            print(f"Section {toggle_input} is now {action}.")
            display_selected_courses_and_sections()
        else:
            print(f"Section ID '{toggle_input}' not found in selected courses. Please try again.")

    # --- Step D: Solve & Display ---
    print("\n" + "=" * 60)
    print("Step D: Solve & Display")
    print("=" * 60)

    schedules = session.solve_schedule()
    print(f"Total non-overlapping schedules found: {len(schedules)}")

    if schedules:
        for idx, schedule in enumerate(schedules):
            print(f"\nDisplaying Schedule #{idx + 1} (Breakdown):")
            print("-" * 80)
            for section in schedule.sections:
                course_id = section.course_id
                instructor = section.instructor
                meeting_strs = []
                for tb in section.meeting_times:
                    meeting_strs.append(f"{tb.day} {tb.start_time.strftime('%H:%M')} - {tb.end_time.strftime('%H:%M')}")
                meetings_formatted = ", ".join(meeting_strs) if meeting_strs else "No meeting times"
                print(f"[{course_id}] Section [{section.section_id}] | {instructor} | {meetings_formatted}")
            print("-" * 80)
    else:
        print("\nExplanation: 0 valid schedules were found. All possible combinations of sections for the selected courses resulted in time conflicts (overlapping meeting times). Try toggling/including excluded sections, or selecting different courses.")

if __name__ == "__main__":
    main()
