import os
from typing import Dict, List, Optional
import streamlit as st
import pandas as pd
from models.schema import Course, Section, Schedule
from scraper.client import ScraperClient
from core.session import ScheduleSessionManager
from utils.visualizer import create_schedule_calendar, get_course_color_map

# Application-wide state storage path
STATE_FILE = "data/session_state.json"

st.set_page_config(
    page_title="Course Schedule Builder",
    page_icon="📅",
    layout="wide",
    initial_sidebar_state="expanded",
)


def get_manager() -> ScheduleSessionManager:
    """
    Retrieves or initializes the ScheduleSessionManager from Streamlit session_state,
    automatically loading persisted state from data/session_state.json on startup.
    """
    if "session_manager" not in st.session_state:
        manager = ScheduleSessionManager()
        if os.path.exists(STATE_FILE):
            try:
                manager.load_session_state(STATE_FILE)
            except Exception as e:
                st.sidebar.warning(f"Note: Could not restore previous state ({e})")
        st.session_state.session_manager = manager
    return st.session_state.session_manager


def save_state():
    """
    Persists the current state of selections, excluded sections, and saved schedules to disk.
    """
    if "session_manager" in st.session_state:
        st.session_state.session_manager.save_session_state(STATE_FILE)


@st.cache_data(show_spinner=False, ttl=1800)
def fetch_semesters() -> List[Dict[str, str]]:
    """Cached fetch of available academic semesters."""
    with ScraperClient() as client:
        return client.get_available_semesters()


@st.cache_data(show_spinner=False, ttl=1800)
def fetch_subjects(term_code: str) -> List[Dict[str, str]]:
    """Cached fetch of available academic subjects for the chosen term."""
    with ScraperClient(term=term_code) as client:
        return client.get_available_subjects(term=term_code)


def format_meeting_times(meeting_times) -> str:
    """Formats a list of TimeBlocks into a concise human-readable string."""
    if not meeting_times:
        return "Arranged / Online"
    formatted_blocks = []
    for tb in meeting_times:
        start_str = tb.start_time.strftime("%I:%M %p").lstrip("0")
        end_str = tb.end_time.strftime("%I:%M %p").lstrip("0")
        formatted_blocks.append(f"{tb.day} {start_str} - {end_str}")
    return ", ".join(formatted_blocks)


# Initialize session manager
manager = get_manager()

# Sidebar: Controls & Selected Courses List
st.sidebar.title("Course Planner")
st.sidebar.caption("University at Albany Schedule Generator")

# Semester Selection
semesters = fetch_semesters()
semester_options = {s["name"]: s["value"] for s in semesters} if semesters else {"Fall 2026": "0009"}
selected_sem_name = st.sidebar.selectbox("Academic Semester", list(semester_options.keys()), index=0)
selected_term_code = semester_options[selected_sem_name]

# Subject Selection
subjects = fetch_subjects(selected_term_code)
subject_map = {f"{s['code']} - {s['label']}": s["code"] for s in subjects} if subjects else {"BACC - Accounting": "BACC"}
default_selection = [list(subject_map.keys())[0]] if subject_map else []
selected_subject_labels = st.sidebar.multiselect(
    "Academic Subjects",
    options=list(subject_map.keys()),
    default=default_selection,
    help="Select one or more subjects to query courses for."
)
selected_subject_codes = [subject_map[lbl] for lbl in selected_subject_labels]

# Fetch Catalog Action
if st.sidebar.button("📥 Fetch Course Catalog", type="primary", use_container_width=True):
    if not selected_subject_codes:
        st.sidebar.warning("Please select at least one subject first.")
    else:
        with st.spinner(f"Loading courses for {len(selected_subject_codes)} subject(s)..."):
            combined_courses: List[Course] = []
            with ScraperClient(term=selected_term_code) as client:
                for code in selected_subject_codes:
                    # Check disk cache first
                    cached = manager.get_cached_catalog(selected_term_code, code)
                    if cached is not None:
                        combined_courses.extend(cached)
                    else:
                        try:
                            courses = client.fetch_courses(code, term=selected_term_code)
                            if courses:
                                manager.cache_catalog(selected_term_code, code, courses)
                                combined_courses.extend(courses)
                        except Exception as e:
                            st.sidebar.error(f"Error fetching {code}: {e}")
            st.session_state.catalog_courses = combined_courses
            st.sidebar.success(f"Loaded {len(combined_courses)} courses!")

# Sidebar: Selected Courses List
st.sidebar.markdown("---")
st.sidebar.subheader(f"📋 Selected Courses ({len(manager.selected_courses)})")

if manager.selected_courses:
    if st.sidebar.button("Clear All Selected Courses", use_container_width=True):
        manager.selected_courses.clear()
        manager.excluded_section_ids.clear()
        save_state()
        st.rerun()

    for course_id, course in list(manager.selected_courses.items()):
        col_name, col_del = st.sidebar.columns([4, 1])
        col_name.markdown(f"**{course.course_id}**")
        if col_del.button("❌", key=f"side_remove_{course_id}", help=f"Remove {course_id}"):
            manager.remove_course(course_id)
            save_state()
            st.rerun()
else:
    st.sidebar.info("No courses selected. Search and add courses in Tab 1.")



# Main Area: 4 Interactive Tabs
tab_catalog, tab_filters, tab_viewer, tab_saved = st.tabs([
    "🔍 Catalog Browser",
    "⚙️ Section Filter Checklist",
    "🗓️ Schedule Permutator Viewer",
    "💾 Saved Schedules Explorer"
])

# ----------------------------------------------------------------------
# TAB 1: CATALOG BROWSER
# ----------------------------------------------------------------------
with tab_catalog:
    st.header("Search & Add Courses")
    catalog_courses: List[Course] = st.session_state.get("catalog_courses", [])

    if not catalog_courses and not manager.selected_courses:
        st.info("👈 Choose a semester and subjects in the sidebar, then click **'Fetch Course Catalog'** to load courses.")
    else:
        # Search / filter bar
        search_query = st.text_input(
            "Filter catalog by Course ID or Title (e.g. '211' or 'Accounting')",
            placeholder="Type here to filter..."
        ).strip().lower()

        display_courses = catalog_courses
        if not display_courses:
            display_courses = list(manager.selected_courses.values())

        if search_query:
            display_courses = [
                c for c in display_courses
                if search_query in c.course_id.lower() or search_query in c.title.lower()
            ]

        st.caption(f"Showing {len(display_courses)} courses")

        for course in display_courses:
            is_selected = course.course_id in manager.selected_courses
            badge = "✅ Selected" if is_selected else ""
            with st.expander(f"**{course.course_id}**: {course.title} ({len(course.sections)} sections) {badge}"):
                btn_col, info_col = st.columns([2, 5])
                if is_selected:
                    if btn_col.button(f"Remove {course.course_id}", key=f"cat_remove_{course.course_id}"):
                        manager.remove_course(course.course_id)
                        save_state()
                        st.rerun()
                else:
                    if btn_col.button(f"Add {course.course_id}", key=f"cat_add_{course.course_id}", type="primary"):
                        manager.add_course(course)
                        save_state()
                        st.rerun()

                # Display table of sections
                sec_rows = []
                for s in course.sections:
                    sec_rows.append({
                        "Section": s.section_id,
                        "Instructor": s.instructor,
                        "Location": s.location,
                        "Meeting Times": format_meeting_times(s.meeting_times),
                    })
                if sec_rows:
                    st.dataframe(pd.DataFrame(sec_rows), hide_index=True, use_container_width=True)

# ----------------------------------------------------------------------
# TAB 2: SECTION FILTER CHECKLIST
# ----------------------------------------------------------------------
with tab_filters:
    st.header("Section Filter Checklist")
    st.caption("Customize your preferences by disabling sections that do not fit your personal schedule.")

    if not manager.selected_courses:
        st.info("No courses selected yet. Add courses in **Tab 1: 🔍 Catalog Browser** first.")
    else:
        for course_id, course in manager.selected_courses.items():
            active_count = sum(1 for s in course.sections if s.section_id not in manager.excluded_section_ids)
            with st.expander(f"**{course.course_id}**: {course.title} — Active Sections: {active_count}/{len(course.sections)}", expanded=True):
                btn_col1, btn_col2, _ = st.columns([1, 1, 4])
                
                if btn_col1.button("Select All", key=f"select_all_{course_id}"):
                    for s in course.sections:
                        manager.toggle_section(s.section_id, is_active=True)
                    save_state()
                    st.rerun()
                
                if btn_col2.button("Deselect All", key=f"deselect_all_{course_id}"):
                    for s in course.sections:
                        manager.toggle_section(s.section_id, is_active=False)
                    save_state()
                    st.rerun()

                st.markdown("---")
                for s in course.sections:
                    is_active = s.section_id not in manager.excluded_section_ids
                    chk_label = f"Section **{s.section_id}** | Instructor: {s.instructor} | Location: {s.location} | Times: {format_meeting_times(s.meeting_times)}"
                    new_val = st.checkbox(chk_label, value=is_active, key=f"sec_chk_{s.section_id}")
                    if new_val != is_active:
                        manager.toggle_section(s.section_id, is_active=new_val)
                        save_state()
                        st.rerun()



# ----------------------------------------------------------------------
# TAB 3: SCHEDULE PERMUTATOR VIEWER
# ----------------------------------------------------------------------
with tab_viewer:
    st.header("Generate & Explore Conflict-Free Schedules")

    if not manager.selected_courses:
        st.info("No courses selected. Add courses in **Tab 1: 🔍 Catalog Browser** first.")
    else:
        col_calc, col_clear_calc = st.columns([2, 1])
        if col_calc.button("🚀 Compute All Conflict-Free Schedules", type="primary", key="btn_compute_schedules"):
            with st.spinner("Finding all valid schedule combinations..."):
                results = manager.solve_schedule()
                st.session_state.computed_schedules = results
                st.session_state.schedule_idx = 0

        computed_schedules: Optional[List[Schedule]] = st.session_state.get("computed_schedules")

        if computed_schedules is not None:
            if len(computed_schedules) == 0:
                st.error("❌ **No Conflict-Free Schedules Found**")
                st.markdown(
                    """
                    **Possible reasons and solutions:**
                    - Check **Tab 2: Section Filter Checklist**: You may have excluded sections that are necessary to avoid overlapping times.
                    - Two or more selected courses may only have overlapping meeting hours.
                    - Try toggling more sections on or substituting one course with another.
                    """
                )
            else:
                total_schedules = len(computed_schedules)
                st.success(f"🎉 Generated **{total_schedules}** valid conflict-free schedule permutations!")

                # Index bounds check
                current_idx = st.session_state.get("schedule_idx", 0)
                current_idx = max(0, min(current_idx, total_schedules - 1))
                st.session_state.schedule_idx = current_idx

                # Navigation and Action Toolbar
                nav_prev, nav_info, nav_next, nav_save = st.columns([1, 2, 1, 2])
                
                if nav_prev.button("⬅️ Previous", disabled=(current_idx <= 0), use_container_width=True, key="btn_schedule_prev"):
                    st.session_state.schedule_idx = max(0, current_idx - 1)
                    st.rerun()

                nav_info.markdown(
                    f"<div style='text-align:center; font-weight:600; padding-top:8px;'>Schedule {current_idx + 1} of {total_schedules}</div>",
                    unsafe_allow_html=True
                )

                if nav_next.button("Next ➡️", disabled=(current_idx >= total_schedules - 1), use_container_width=True, key="btn_schedule_next"):
                    st.session_state.schedule_idx = min(total_schedules - 1, current_idx + 1)
                    st.rerun()

                current_schedule = computed_schedules[current_idx]
                
                # Check if current schedule is already bookmarked
                saved_list = manager.get_saved_schedules()
                curr_sec_ids = {s.section_id for s in current_schedule.sections}
                already_saved = any({s.section_id for s in saved.sections} == curr_sec_ids for saved in saved_list)

                if already_saved:
                    nav_save.button("⭐ Bookmarked", disabled=True, use_container_width=True, key="btn_bookmark_disabled")
                else:
                    if nav_save.button("💾 Bookmark Schedule", type="secondary", use_container_width=True, key="btn_bookmark_active"):
                        manager.save_schedule(current_schedule)
                        save_state()
                        st.toast("Schedule bookmarked to Saved Schedules!")
                        st.rerun()

                # Jump slider if many schedules
                if total_schedules > 5:
                    jump_val = st.slider("Jump to Schedule #", min_value=1, max_value=total_schedules, value=current_idx + 1)
                    if jump_val - 1 != current_idx:
                        st.session_state.schedule_idx = jump_val - 1
                        st.rerun()

                # Render weekly timetable calendar
                color_map = get_course_color_map(manager.get_active_courses())
                fig = create_schedule_calendar(current_schedule, course_colors=color_map)
                st.plotly_chart(fig, use_container_width=True, key=f"schedule_viewer_chart_{current_idx}")

                # Detailed course list for active schedule
                with st.expander("📋 Course Details in this Schedule", expanded=False):
                    details = []
                    for s in current_schedule.sections:
                        details.append({
                            "Course ID": s.course_id,
                            "Section": s.section_id,
                            "Instructor": s.instructor,
                            "Location": s.location,
                            "Meeting Times": format_meeting_times(s.meeting_times),
                        })
                    st.dataframe(pd.DataFrame(details), hide_index=True, use_container_width=True)



# ----------------------------------------------------------------------
# TAB 4: SAVED SCHEDULES EXPLORER
# ----------------------------------------------------------------------
with tab_saved:
    st.header("Saved Schedules Explorer")
    saved_schedules = manager.get_saved_schedules()

    if not saved_schedules:
        st.info("📂 No saved schedules yet. Bookmark schedules from **Tab 3: 🗓️ Schedule Permutator Viewer** to view them here.")
    else:
        st.write(f"You have **{len(saved_schedules)}** saved schedule(s).")
        
        saved_options = list(range(len(saved_schedules)))
        selected_saved_idx = st.selectbox(
            "Choose a saved schedule to view:",
            options=saved_options,
            format_func=lambda i: f"Saved Schedule #{i + 1} — ({len(saved_schedules[i].sections)} courses: {', '.join([s.course_id for s in saved_schedules[i].sections])})",
            key="saved_schedules_selectbox",
        )

        col_del, col_clear = st.columns([2, 2])
        if col_del.button("🗑️ Remove Selected Schedule", key="btn_remove_selected_saved"):
            manager.remove_saved_schedule(selected_saved_idx)
            save_state()
            st.rerun()

        if col_clear.button("⚠️ Clear All Saved Schedules", key="btn_clear_all_saved"):
            manager.clear_saved_schedules()
            save_state()
            st.rerun()

        chosen_saved = saved_schedules[selected_saved_idx]
        saved_color_map = get_course_color_map(chosen_saved)
        saved_fig = create_schedule_calendar(chosen_saved, course_colors=saved_color_map)
        st.plotly_chart(saved_fig, use_container_width=True, key=f"saved_schedule_chart_{selected_saved_idx}")

        with st.expander("📋 Saved Schedule Breakdown", expanded=True):
            breakdown_rows = []
            for s in chosen_saved.sections:
                breakdown_rows.append({
                    "Course ID": s.course_id,
                    "Section": s.section_id,
                    "Instructor": s.instructor,
                    "Location": s.location,
                    "Meeting Times": format_meeting_times(s.meeting_times),
                })
            st.dataframe(pd.DataFrame(breakdown_rows), hide_index=True, use_container_width=True)

