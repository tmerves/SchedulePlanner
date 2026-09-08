import os
import importlib
from typing import Dict, List, Optional
import streamlit as st
import pandas as pd
from models.schema import Course, Section, Schedule
from scraper.client import ScraperClient
import core.session
if not hasattr(core.session, "generate_session_id"):
    importlib.reload(core.session)
from core.session import (
    ScheduleSessionManager,
    generate_session_id,
    is_valid_session_id,
    get_user_session_filepath,
)
from utils.visualizer import create_schedule_calendar, get_course_color_map
from utils.time_utils import format_meeting_times

st.set_page_config(
    page_title="Course Schedule Builder",
    page_icon="📅",
    layout="wide",
    initial_sidebar_state="expanded",
)


def init_user_session() -> str:
    """
    Synchronizes the session ID with Streamlit's URL query parameters.
    Reads 'session_id' from query parameters or generates a new random one,
    appending it to the URL so the user can return to their session anytime.
    """
    query_sid = st.query_params.get("session_id")
    if is_valid_session_id(query_sid):
        session_id = query_sid
    else:
        session_id = generate_session_id()
        st.query_params["session_id"] = session_id

    # If the session_id changed in the URL or this is a fresh connection, reset session state
    if st.session_state.get("current_session_id") != session_id:
        st.session_state.current_session_id = session_id
        if "session_manager" in st.session_state:
            del st.session_state["session_manager"]
        if "catalog_courses" in st.session_state:
            del st.session_state["catalog_courses"]
        if "computed_schedules" in st.session_state:
            del st.session_state["computed_schedules"]
        if "schedule_idx" in st.session_state:
            st.session_state.schedule_idx = 0

    return session_id


def get_manager(session_id: str) -> ScheduleSessionManager:
    """
    Retrieves or initializes the ScheduleSessionManager from Streamlit session_state,
    automatically loading persisted state from data/sessions/{session_id}/session_state.json.
    Course catalogs remain shared in data/cache.
    """
    if "session_manager" not in st.session_state:
        manager = ScheduleSessionManager(cache_dir="data/cache", default_term="0009")
        state_file = get_user_session_filepath(session_id)
        if state_file.exists():
            try:
                manager.load_session_state(str(state_file))
            except Exception as e:
                st.sidebar.warning(f"Note: Could not restore previous state ({e})")
        st.session_state.session_manager = manager
    return st.session_state.session_manager


def save_state():
    """
    Persists the current state of selections, excluded sections, and saved schedules to disk
    under the active user's session state file.
    """
    if "session_manager" in st.session_state and "current_session_id" in st.session_state:
        session_id = st.session_state.current_session_id
        state_file = get_user_session_filepath(session_id)
        st.session_state.session_manager.save_session_state(str(state_file))


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


def get_organized_course_sections(course: Course):
    """
    Groups and orders sections so that primary lectures appear first,
    with any linked discussions/labs nested immediately beneath them.
    Returns a list of dicts: [{"primary": Section, "discussions": [Section, ...]}]
    """
    all_linked_targets = {
        l_id
        for s in course.sections
        for l_id in (s.linked_sections or [])
    }
    sec_map = {s.section_id: s for s in course.sections}

    primary_sections = [
        s for s in course.sections
        if s.section_id not in all_linked_targets
    ]
    if not primary_sections:
        primary_sections = list(course.sections)

    seen_ids = set()
    groups = []

    for prim in primary_sections:
        seen_ids.add(prim.section_id)
        linked = []
        for l_id in (prim.linked_sections or []):
            if l_id in sec_map:
                linked.append(sec_map[l_id])
                seen_ids.add(l_id)
        groups.append({"primary": prim, "discussions": linked})

    for s in course.sections:
        if s.section_id not in seen_ids:
            groups.append({"primary": s, "discussions": []})
            seen_ids.add(s.section_id)

    return groups


def format_schedule_breakdown(schedule: Schedule) -> List[Dict[str, str]]:
    """
    Formats the list of sections in a schedule hierarchically,
    placing discussions underneath their parent lecture with visual indentation.
    """
    courses_in_sched: Dict[str, List[Section]] = {}
    for s in schedule.sections:
        courses_in_sched.setdefault(s.course_id, []).append(s)

    rows = []
    for cid, secs in courses_in_sched.items():
        sec_map = {s.section_id: s for s in secs}
        parents = [s for s in secs if s.linked_sections and any(lid in sec_map for lid in s.linked_sections)]
        if parents:
            parent = parents[0]
            rows.append({
                "Course ID": parent.course_id,
                "Section": f"{parent.section_id} ({parent.component})",
                "Instructor": parent.instructor,
                "Location": parent.location,
                "Meeting Times": format_meeting_times(parent.meeting_times),
            })
            for lid in parent.linked_sections:
                if lid in sec_map:
                    child = sec_map[lid]
                    rows.append({
                        "Course ID": "",
                        "Section": f"   ↳ {child.section_id} ({child.component})",
                        "Instructor": child.instructor,
                        "Location": child.location,
                        "Meeting Times": format_meeting_times(child.meeting_times),
                    })
            for s in secs:
                if s.section_id != parent.section_id and s.section_id not in parent.linked_sections:
                    rows.append({
                        "Course ID": s.course_id,
                        "Section": f"{s.section_id} ({s.component})",
                        "Instructor": s.instructor,
                        "Location": s.location,
                        "Meeting Times": format_meeting_times(s.meeting_times),
                    })
        else:
            for s in secs:
                comp_tag = f" ({s.component})" if s.component and s.component != "Lecture" else ""
                rows.append({
                    "Course ID": s.course_id,
                    "Section": f"{s.section_id}{comp_tag}",
                    "Instructor": s.instructor,
                    "Location": s.location,
                    "Meeting Times": format_meeting_times(s.meeting_times),
                })
    return rows


# Initialize per-user session and session manager
current_session_id = init_user_session()
manager = get_manager(current_session_id)

# Sidebar: Controls & Selected Courses List
st.sidebar.title("Course Planner")
st.sidebar.caption("University at Albany Schedule Generator")

# Session & Share URL controls
with st.sidebar.expander("🔗 Session & Unique URL", expanded=False):
    st.write(f"**Session ID:** `{current_session_id}`")
    st.caption("Your courses and schedules are tied to this URL session. Bookmark or share this link to return anytime.")
    if st.button("🆕 Start Fresh Session", use_container_width=True, key="btn_fresh_session"):
        new_sid = generate_session_id()
        st.query_params["session_id"] = new_sid
        st.rerun()

# Semester Selection
semesters = fetch_semesters()
semester_options = {s["name"]: s["value"] for s in semesters} if semesters else {"Fall 2026": "0009"}
selected_sem_name = st.sidebar.selectbox("Academic Semester", list(semester_options.keys()), index=0)
selected_term_code = semester_options[selected_sem_name]

# Synchronize manager's active semester and isolate catalog/schedules per semester
if manager.active_term_code != selected_term_code:
    manager.set_term(selected_term_code)
    if st.session_state.get("catalog_term") != selected_term_code:
        st.session_state.catalog_courses = []
        st.session_state.catalog_term = selected_term_code
    if "computed_schedules" in st.session_state:
        del st.session_state["computed_schedules"]
    if "schedule_idx" in st.session_state:
        st.session_state.schedule_idx = 0

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
            st.session_state.catalog_term = selected_term_code
            st.sidebar.success(f"Loaded {len(combined_courses)} courses!")

# Sidebar: Selected Courses List
st.sidebar.markdown("---")
st.sidebar.subheader(f"📋 Selected Courses for {selected_sem_name} ({len(manager.selected_courses)})")

if manager.selected_courses:
    if st.sidebar.button(f"Clear All ({selected_sem_name})", use_container_width=True):
        manager.clear_courses()
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
    st.sidebar.info(f"No courses selected for {selected_sem_name}. Search and add courses in **🔍 Catalog Browser**.")



# Main Area: 4 Interactive Tabs
tab_catalog, tab_filters, tab_viewer, tab_saved = st.tabs([
    "🔍 Catalog Browser",
    "⚙️ Section Filter Checklist",
    "🗓️ Schedule Permutator Viewer",
    "💾 Saved Schedules Explorer"
])

# ----------------------------------------------------------------------
# CATALOG BROWSER
# ----------------------------------------------------------------------
with tab_catalog:
    st.header(f"Search & Add Courses — {selected_sem_name}")
    # Verify catalog matches active term
    catalog_term = st.session_state.get("catalog_term")
    if catalog_term != selected_term_code:
        catalog_courses: List[Course] = []
    else:
        catalog_courses = st.session_state.get("catalog_courses", [])

    if not catalog_courses and not manager.selected_courses:
        st.info(f"👈 Choose subjects in the sidebar, then click **'Fetch Course Catalog'** to load courses for {selected_sem_name}.")
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

        st.caption(f"Showing {len(display_courses)} courses for {selected_sem_name}")

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
                    can_add = len(manager.selected_courses) < manager.MAX_SELECTED_COURSES
                    if btn_col.button(f"Add {course.course_id}", key=f"cat_add_{course.course_id}", type="primary", disabled=not can_add):
                        try:
                            manager.add_course(course)
                            save_state()
                            st.rerun()
                        except ValueError as err:
                            st.error(f"❌ {err}")

                # Display table of sections
                sec_rows = []
                groups = get_organized_course_sections(course)
                for grp in groups:
                    prim = grp["primary"]
                    sec_rows.append({
                        "Section": f"{prim.section_id} ({prim.component})",
                        "Instructor": prim.instructor,
                        "Location": prim.location,
                        "Meeting Times": format_meeting_times(prim.meeting_times),
                    })
                    for disc in grp["discussions"]:
                        sec_rows.append({
                            "Section": f"   ↳ {disc.section_id} ({disc.component})",
                            "Instructor": disc.instructor,
                            "Location": disc.location,
                            "Meeting Times": format_meeting_times(disc.meeting_times),
                        })
                if sec_rows:
                    st.dataframe(pd.DataFrame(sec_rows), hide_index=True, use_container_width=True)

# ----------------------------------------------------------------------
# SECTION FILTER CHECKLIST
# ----------------------------------------------------------------------
with tab_filters:
    st.header(f"Section Filter Checklist — {selected_sem_name}")
    st.caption(f"Semester: **{selected_sem_name}** — Customize your preferences by disabling sections that do not fit your personal schedule.")

    if not manager.selected_courses:
        st.info(f"No courses selected yet for {selected_sem_name}. Add courses in **🔍 Catalog Browser** first.")
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
                groups = get_organized_course_sections(course)
                for grp in groups:
                    prim = grp["primary"]
                    discs = grp["discussions"]

                    is_active_prim = prim.section_id not in manager.excluded_section_ids
                    chk_label_prim = (
                        f"Section **{prim.section_id}** ({prim.component}) | "
                        f"Instructor: {prim.instructor} | Location: {prim.location} | "
                        f"Times: {format_meeting_times(prim.meeting_times)}"
                    )
                    new_val_prim = st.checkbox(chk_label_prim, value=is_active_prim, key=f"sec_chk_{prim.section_id}")
                    if new_val_prim != is_active_prim:
                        manager.toggle_section(prim.section_id, is_active=new_val_prim)
                        save_state()
                        st.rerun()

                    if discs:
                        for disc in discs:
                            col_indent, col_chk = st.columns([0.06, 0.94])
                            with col_chk:
                                is_active_disc = disc.section_id not in manager.excluded_section_ids
                                chk_label_disc = (
                                    f"↳ Discussion Section **{disc.section_id}** | "
                                    f"Instructor: {disc.instructor} | Location: {disc.location} | "
                                    f"Times: {format_meeting_times(disc.meeting_times)}"
                                )
                                new_val_disc = st.checkbox(chk_label_disc, value=is_active_disc, key=f"sec_chk_{disc.section_id}")
                                if new_val_disc != is_active_disc:
                                    manager.toggle_section(disc.section_id, is_active=new_val_disc)
                                    save_state()
                                    st.rerun()



# ----------------------------------------------------------------------
# SCHEDULE PERMUTATOR VIEWER
# ----------------------------------------------------------------------
with tab_viewer:
    st.header(f"Generate & Explore Conflict-Free Schedules — {selected_sem_name}")
    st.caption(f"Generating conflict-free schedules for **{selected_sem_name}**.")

    if not manager.selected_courses:
        st.info(f"No courses selected for **{selected_sem_name}**. Add courses in **🔍 Catalog Browser** first.")
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
                    - Check **⚙️ Section Filter Checklist**: You may have excluded sections that are necessary to avoid overlapping times.
                    - Two or more selected courses may only have overlapping meeting hours.
                    - Try toggling more sections on or substituting one course with another.
                    """
                )
            else:
                total_schedules = len(computed_schedules)
                st.success(f"🎉 Generated **{total_schedules}** valid conflict-free schedule permutations for {selected_sem_name}!")

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
                    can_save = len(saved_list) < manager.MAX_SAVED_SCHEDULES
                    if nav_save.button(
                        "💾 Bookmark Schedule",
                        type="secondary",
                        use_container_width=True,
                        key="btn_bookmark_active",
                        disabled=not can_save,
                    ):
                        manager.save_schedule(current_schedule)
                        save_state()
                        st.toast(f"Schedule bookmarked to {selected_sem_name} Saved Schedules!")
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
                    details = format_schedule_breakdown(current_schedule)
                    st.dataframe(pd.DataFrame(details), hide_index=True, use_container_width=True)



# ----------------------------------------------------------------------
# SAVED SCHEDULES EXPLORER
# ----------------------------------------------------------------------
with tab_saved:
    st.header("Saved Schedules Explorer")

    term_name_map = {v: k for k, v in semester_options.items()}
    explorer_options = list(semester_options.values())
    default_explorer_idx = explorer_options.index(selected_term_code) if selected_term_code in explorer_options else 0
    view_term_code = st.selectbox(
        "Browse Saved Schedules for Semester:",
        options=explorer_options,
        index=default_explorer_idx,
        format_func=lambda code: f"{term_name_map.get(code, code)} ({len(manager.get_saved_schedules(code))} saved)",
        key="saved_explorer_term_selector"
    )
    view_sem_name = term_name_map.get(view_term_code, view_term_code)
    saved_schedules = manager.get_saved_schedules(view_term_code)

    if not saved_schedules:
        st.info(f"📂 No saved schedules for **{view_sem_name}** yet. Bookmark schedules from **🗓️ Schedule Permutator Viewer** when working in {view_sem_name}.")
    else:
        st.write(f"You have **{len(saved_schedules)}** saved schedule(s) for **{view_sem_name}**.")
        
        saved_options = list(range(len(saved_schedules)))
        selected_saved_idx = st.selectbox(
            f"Choose a saved schedule to view for {view_sem_name}:",
            options=saved_options,
            format_func=lambda i: f"Saved Schedule #{i + 1} — ({len(set(s.course_id for s in saved_schedules[i].sections))} courses: {', '.join(sorted(list(set(s.course_id for s in saved_schedules[i].sections))))})",
            key="saved_schedules_selectbox",
        )

        col_del, col_clear = st.columns([2, 2])
        if col_del.button("🗑️ Remove Selected Schedule", key="btn_remove_selected_saved"):
            manager.remove_saved_schedule(selected_saved_idx, term_code=view_term_code)
            save_state()
            st.rerun()

        if col_clear.button(f"⚠️ Clear All Saved Schedules for {view_sem_name}", key="btn_clear_all_saved"):
            manager.clear_saved_schedules(term_code=view_term_code)
            save_state()
            st.rerun()

        chosen_saved = saved_schedules[selected_saved_idx]
        saved_color_map = get_course_color_map(chosen_saved)
        saved_fig = create_schedule_calendar(chosen_saved, course_colors=saved_color_map)
        st.plotly_chart(saved_fig, use_container_width=True, key=f"saved_schedule_chart_{view_term_code}_{selected_saved_idx}")

        with st.expander("📋 Saved Schedule Breakdown", expanded=True):
            breakdown_rows = format_schedule_breakdown(chosen_saved)
            st.dataframe(pd.DataFrame(breakdown_rows), hide_index=True, use_container_width=True)

