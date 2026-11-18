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
)
from core.storage import SessionUnavailable, build_store, needs_touch
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
        st.session_state.known_new_session = session_id  # nothing to load, skip the storage read

    # If the session_id changed in the URL or this is a fresh connection, reset session state
    if st.session_state.get("current_session_id") != session_id:
        st.session_state.current_session_id = session_id
        if "session_manager" in st.session_state:
            del st.session_state["session_manager"]
        if "catalog_courses" in st.session_state:
            del st.session_state["catalog_courses"]
        clear_checkbox_state()
        if "computed_schedules" in st.session_state:
            del st.session_state["computed_schedules"]
        if "schedule_idx" in st.session_state:
            st.session_state.schedule_idx = 0

    return session_id


@st.cache_resource(show_spinner=False)
def get_store():
    """
    Process-wide session store. Uses the private GitHub data repo when [session_store] is set in
    the Streamlit secrets, otherwise local disk (which Streamlit Cloud wipes on redeploy).
    """
    try:
        config = st.secrets["session_store"] if "session_store" in st.secrets else None
    except Exception:
        config = None
    return build_store({"session_store": config} if config else None)


def get_manager(session_id: str) -> ScheduleSessionManager:
    """
    Retrieves or initializes the ScheduleSessionManager from Streamlit session_state,
    loading the persisted state once per browser connection.
    Course catalogs remain shared in data/cache.
    """
    if "session_manager" not in st.session_state:
        manager = ScheduleSessionManager(cache_dir="data/cache", default_term="0009")
        store = get_store()
        st.session_state.persist_blocked = False
        try:
            if st.session_state.get("known_new_session") == session_id:
                state = None
            else:
                state = store.load(session_id)
            if state is None:
                # Brand new session: check the data repo has room (cached, rate limited)
                try:
                    store.guard_new_session()
                except Exception:
                    pass
            else:
                manager.import_state(state)
                if needs_touch(manager.last_active):
                    store.save(session_id, manager.export_state(), force=True)
        except SessionUnavailable as e:
            # Storage is unreachable: the saved session is fine, so do not treat this as a new one
            manager = ScheduleSessionManager(cache_dir="data/cache", default_term="0009")
            st.session_state.persist_blocked = True
            st.session_state.load_retryable = True
            st.session_state.load_error = str(e)
        except Exception as e:
            # Never overwrite a session we could not read: keep the old copy and block saving
            manager = ScheduleSessionManager(cache_dir="data/cache", default_term="0009")
            st.session_state.persist_blocked = True
            st.session_state.load_retryable = False
            st.session_state.load_error = str(e)
            try:
                store.quarantine(session_id)
            except Exception:
                pass
        st.session_state.session_manager = manager
    return st.session_state.session_manager


def save_state():
    """
    Persists selections, excluded sections, and saved schedules for the active session.
    The store coalesces and rate limits remote writes; this call never blocks on the network.
    """
    if st.session_state.get("persist_blocked"):
        return
    if "session_manager" in st.session_state and "current_session_id" in st.session_state:
        session_id = st.session_state.current_session_id
        get_store().save(session_id, st.session_state.session_manager.export_state())


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


def format_schedule_breakdown(schedule: Schedule) -> List[tuple]:
    """
    Returns (Section, row dict) pairs for a schedule, ordered hierarchically,
    placing discussions underneath their parent lecture with visual indentation.
    """
    def row(sec: Section, show_course: bool = True, label: Optional[str] = None) -> tuple:
        return (sec, {
            "Course ID": sec.course_id if show_course else "",
            "Section": label if label is not None else f"{sec.section_id} ({sec.component})",
            "Instructor": sec.instructor,
            "Location": sec.location,
            "Meeting Times": format_meeting_times(sec.meeting_times),
        })

    courses_in_sched: Dict[str, List[Section]] = {}
    for s in schedule.sections:
        courses_in_sched.setdefault(s.course_id, []).append(s)

    rows = []
    for cid, secs in courses_in_sched.items():
        sec_map = {s.section_id: s for s in secs}
        parents = [s for s in secs if s.linked_sections and any(lid in sec_map for lid in s.linked_sections)]
        if parents:
            parent = parents[0]
            rows.append(row(parent))
            for lid in parent.linked_sections:
                if lid in sec_map:
                    child = sec_map[lid]
                    rows.append(row(child, show_course=False, label=f"   ↳ {child.section_id} ({child.component})"))
            for s in secs:
                if s.section_id != parent.section_id and s.section_id not in parent.linked_sections:
                    rows.append(row(s))
        else:
            for s in secs:
                comp_tag = f" ({s.component})" if s.component and s.component != "Lecture" else ""
                rows.append(row(s, label=f"{s.section_id}{comp_tag}"))
    return rows

INFO_ICON = ":material/info:"


def _detail_value(value: Optional[str]) -> str:
    return value if value else "—"


@st.dialog("Class Details", width="large", on_dismiss="rerun")
def show_section_details(section: Section) -> None:
    """Popup with the full registrar information captured for one section."""
    d = section.details
    comp = f" ({section.component})" if section.component else ""
    st.subheader(f"{section.course_id} — Section {section.section_id}{comp}")
    st.write(
        f"**Instructor:** {section.instructor}  \n"
        f"**Location:** {section.location}  \n"
        f"**Meeting Times:** {format_meeting_times(section.meeting_times) or 'TBA'}"
    )
    if d is None:
        st.info("No detailed information was saved for this section. Re-fetch the subject from **Fetch Course Catalog** to load it.")
        return

    st.divider()
    col_a, col_b = st.columns(2)
    col_a.write(
        f"**Level:** {_detail_value(d.level)}  \n"
        f"**College / School:** {_detail_value(d.college)}  \n"
        f"**Department:** {_detail_value(d.department)}  \n"
        f"**Grading:** {_detail_value(d.grading)}  \n"
        f"**Credits:** {_detail_value(d.credit_range)}  \n"
        f"**Topic:** {_detail_value(d.topic)}"
    )
    col_b.write(
        f"**Seats remaining:** {_detail_value(d.seats_remaining)}  \n"
        f"**Session:** {_detail_value(d.session)}  \n"
        f"**Delivery method:** {_detail_value(d.delivery_method)}  \n"
        f"**Special restriction:** {_detail_value(d.special_restriction)}"
    )

    flag_labels = [
        ("General Education", d.gen_ed),
        ("Honors College", d.honors),
        ("IT Commons", d.it_commons),
        ("Writing Intensive", d.writing_intensive),
        ("Oral Discourse", d.oral_discourse),
        ("Information Literacy", d.info_literacy),
        ("COIL", d.coil),
        ("OER", d.oer),
        ("Liberal Arts", d.liberal_arts),
    ]
    active_flags = [
        f"{label}: {value}" if value.upper() not in ("Y", "YES") else label
        for label, value in flag_labels
        if value and value.upper() not in ("N", "NO", "NONE")
    ]
    st.write("**Attributes:** " + (", ".join(active_flags) if active_flags else "—"))

    if d.comments:
        st.write(f"**Comments:** {d.comments}")
    if d.description:
        st.divider()
        st.write("**Course description**")
        st.write(d.description)


def clear_checkbox_state() -> None:
    """Drops cached checkbox widget state so it is rebuilt from the manager (new session or semester)."""
    for key in [k for k in st.session_state if str(k).startswith("sec_chk_")]:
        del st.session_state[key]


def toggle_section_cb(section_id: str) -> None:
    """Checkbox callback: apply the widget's new value to the manager and save."""
    manager.toggle_section(section_id, is_active=st.session_state[f"sec_chk_{section_id}"])
    save_state()


def section_checkbox(container, label: str, section_id: str) -> None:
    """
    A section on/off checkbox whose state lives in session_state under `sec_chk_<id>`.
    It is seeded from the manager once, so no default `value` is passed alongside the key.
    """
    key = f"sec_chk_{section_id}"
    if key not in st.session_state:
        st.session_state[key] = section_id not in manager.excluded_section_ids
    container.checkbox(label, key=key, on_change=toggle_section_cb, args=(section_id,))


def set_all_sections(course: Course, active: bool) -> None:
    """
    Callback for Select All / Deselect All. Checkboxes keep their own widget state under
    `sec_chk_<id>` and would override the manager on the next run, so update both.
    """
    for sec in course.sections:
        manager.toggle_section(sec.section_id, is_active=active)
        st.session_state[f"sec_chk_{sec.section_id}"] = active
    save_state()


st.markdown(
    """
    <style>
    [class*="st-key-info_"] button { min-height: 1.75rem; padding: 0 0.4rem; }
    [data-testid="stExpander"] hr { margin: 0.25rem 0; }
    [data-testid="stExpander"] [data-testid="stVerticalBlock"] { gap: 0.5rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


def section_info_button(section: Section, key: str) -> None:
    """Info icon button that opens the details popup for a section."""
    if st.button(INFO_ICON, key=key, help="Show class details"):
        show_section_details(section)


def render_schedule_breakdown(schedule: Schedule, key_prefix: str) -> None:
    """Schedule breakdown as rows with the info button on the left, matching the other tabs."""
    widths = [0.6, 1.4, 2.8, 2.6, 3, 4]
    headers = ["Info", "Course ID", "Section", "Instructor", "Location", "Meeting Times"]
    for col, title in zip(st.columns(widths), headers):
        col.markdown(f"**{title}**")
    for sec, row in format_schedule_breakdown(schedule):
        c_info, c_course, c_sec, c_inst, c_loc, c_time = st.columns(widths, vertical_alignment="center")
        with c_info:
            section_info_button(sec, key=f"{key_prefix}_{sec.section_id}")
        c_course.write(row["Course ID"])
        c_sec.write(row["Section"].strip())
        c_inst.write(row["Instructor"])
        c_loc.write(row["Location"])
        c_time.write(row["Meeting Times"])


def render_clickable_calendar(schedule: Schedule, color_map: Dict[str, str], chart_id: str) -> None:
    """
    Draws the calendar and opens the details popup when a class block is clicked.
    The chart key carries a counter that is bumped after each click so the selection
    resets and the popup does not reopen on later reruns.
    """
    counter_key = f"chart_nonce_{chart_id}"
    nonce = st.session_state.get(counter_key, 0)
    fig = create_schedule_calendar(schedule, course_colors=color_map)
    event = st.plotly_chart(
        fig,
        use_container_width=True,
        key=f"{chart_id}_{nonce}",
        on_select="rerun",
        selection_mode="points",
    )
    points = event.selection.points if event and event.selection else []
    if not points:
        return
    custom = points[0].get("customdata")
    section_id = custom[0] if isinstance(custom, (list, tuple)) and custom else custom
    section = next((s for s in schedule.sections if s.section_id == section_id), None)
    if section is not None:
        st.session_state[counter_key] = nonce + 1
        show_section_details(section)


# Initialize per-user session and session manager
current_session_id = init_user_session()
manager = get_manager(current_session_id)

if st.session_state.get("persist_blocked"):
    if st.session_state.get("load_retryable"):
        st.sidebar.warning(
            "Saved sessions could not be reached right now. Your data is safe and nothing "
            f"will be saved until it loads. ({st.session_state.get('load_error', 'unknown error')})"
        )
        if st.sidebar.button("Retry loading my session", key="btn_retry_load"):
            del st.session_state["session_manager"]
            clear_checkbox_state()
            st.rerun()
    else:
        st.sidebar.warning(
            "Your saved session could not be read, so changes are not being saved and your "
            f"previous data was kept aside. ({st.session_state.get('load_error', 'unknown error')})"
        )
        if st.sidebar.button("Start fresh and overwrite", key="btn_unblock_persist"):
            st.session_state.persist_blocked = False
            clear_checkbox_state()
            st.rerun()

# Sidebar: Controls & Selected Courses List
st.sidebar.title("Course Planner")
st.sidebar.caption("University at Albany Schedule Generator")

# Session & Share URL controls
with st.sidebar.expander("🔗 Session & Unique URL", expanded=False):
    st.write(f"**Session ID:** `{current_session_id}`")
    st.caption("Your courses and schedules are tied to this URL session. Bookmark to return, but sessions are deleted after 3 days of inactivity.")
    if st.button("🆕 Start Fresh Session", use_container_width=True, key="btn_fresh_session"):
        new_sid = generate_session_id()
        st.query_params["session_id"] = new_sid
        st.rerun()

# Semester Selection
semesters = fetch_semesters()
if not semesters:
    st.sidebar.error("⚠️ Connection error: Unable to load academic semesters from registrar. An active internet connection is required to fetch course schedules.")
    semester_options = {}
    selected_sem_name = ""
    selected_term_code = None
else:
    semester_options = {s["name"]: s["value"] for s in semesters}
    selected_sem_name = st.sidebar.selectbox("Academic Semester", list(semester_options.keys()), index=0)
    selected_term_code = semester_options[selected_sem_name]

# Synchronize manager's active semester and isolate catalog/schedules per semester
if selected_term_code:
    if manager.active_term_code != selected_term_code:
        manager.set_term(selected_term_code)
        clear_checkbox_state()
        if st.session_state.get("catalog_term") != selected_term_code:
            st.session_state.catalog_courses = []
            st.session_state.catalog_term = selected_term_code
        if "computed_schedules" in st.session_state:
            del st.session_state["computed_schedules"]
        if "schedule_idx" in st.session_state:
            st.session_state.schedule_idx = 0

    # Subject Selection
    subjects = fetch_subjects(selected_term_code)
    if not subjects:
        st.sidebar.warning("⚠️ Could not load subjects for the selected semester. Please check your connection or retry.")
    subject_map = {f"{s['code']} - {s['label']}": s["code"] for s in subjects} if subjects else {}
    selected_subject_labels = st.sidebar.multiselect(
        "Academic Subjects",
        options=list(subject_map.keys()),
        default=[],
        help="Select one or more subjects to query courses for.",
        select_all=False,
    )
    selected_subject_codes = [subject_map[lbl] for lbl in selected_subject_labels]
else:
    selected_subject_codes = []

# Fetch Catalog Action
if st.sidebar.button("📥 Fetch Course Catalog", type="primary", use_container_width=True, disabled=not selected_term_code):
    if not selected_subject_codes:
        st.sidebar.warning("Please select at least one subject first.")
    else:
        with st.spinner(f"Loading courses for {len(selected_subject_codes)} subject(s)..."):
            combined_courses: List[Course] = []
            fetch_errors: List[str] = []
            with ScraperClient(term=selected_term_code) as client:
                for code in selected_subject_codes:
                    # Check disk cache first
                    cached = manager.get_cached_catalog(selected_term_code, code)
                    # Caches written before section details existed have none; refetch once.
                    if cached is not None and not any(s.details for c in cached for s in c.sections):
                        cached = None
                    if cached is not None:
                        combined_courses.extend(cached)
                    else:
                        try:
                            courses = client.fetch_courses(code, term=selected_term_code)
                            if courses:
                                manager.cache_catalog(selected_term_code, code, courses)
                                combined_courses.extend(courses)
                            else:
                                fetch_errors.append(f"{code}: no courses returned")
                        except Exception as e:
                            fetch_errors.append(f"{code}: {e}")
            if fetch_errors:
                for err in fetch_errors:
                    st.sidebar.error(f"⚠️ Error fetching {err}")
            st.session_state.catalog_courses = combined_courses
            st.session_state.catalog_term = selected_term_code
            if combined_courses:
                st.sidebar.success(f"Loaded {len(combined_courses)} courses!")

# Sidebar: Selected Courses List
display_sem_name = selected_sem_name if selected_sem_name else "Active Semester"
st.sidebar.markdown("---")
st.sidebar.subheader(f"📋 Selected Courses for {display_sem_name} ({len(manager.selected_courses)})")

if manager.selected_courses:
    if st.sidebar.button(f"Clear All ({display_sem_name})", use_container_width=True):
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
    st.sidebar.info(f"No courses selected for {display_sem_name}. Search and add courses in **🔍 Catalog Browser**.")



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
    st.header(f"Search & Add Courses — {display_sem_name}")
    # Verify catalog matches active term
    catalog_term = st.session_state.get("catalog_term")
    if catalog_term != selected_term_code:
        catalog_courses: List[Course] = []
    else:
        catalog_courses = st.session_state.get("catalog_courses", [])

    if not catalog_courses and not manager.selected_courses:
        st.info(f"👈 Choose subjects in the sidebar, then click **'Fetch Course Catalog'** to load courses for {display_sem_name}.")
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

                # Section rows with an info icon that opens the details popup
                sec_rows = []
                for grp in get_organized_course_sections(course):
                    sec_rows.append((grp["primary"], f"{grp['primary'].section_id} ({grp['primary'].component})"))
                    for disc in grp["discussions"]:
                        sec_rows.append((disc, f"↳ {disc.section_id} ({disc.component})"))
                if sec_rows:
                    widths = [0.6, 2, 3, 3, 4]
                    for col, title in zip(st.columns(widths), ["Info", "Section", "Instructor", "Location", "Meeting Times"]):
                        col.markdown(f"**{title}**")
                    for sec, label in sec_rows:
                        c_info, c_sec, c_inst, c_loc, c_time = st.columns(widths, vertical_alignment="center")
                        with c_info:
                            section_info_button(sec, key=f"info_cat_{course.course_id}_{sec.section_id}")
                        c_sec.write(label)
                        c_inst.write(sec.instructor)
                        c_loc.write(sec.location)
                        c_time.write(format_meeting_times(sec.meeting_times))

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
                btn_col1, btn_col2, _ = st.columns([1, 1, 6], gap="small")
                btn_col1.button("Select All", key=f"select_all_{course_id}", on_click=set_all_sections, args=(course, True))
                btn_col2.button("Deselect All", key=f"deselect_all_{course_id}", on_click=set_all_sections, args=(course, False))

                st.divider()
                groups = get_organized_course_sections(course)
                for grp in groups:
                    prim = grp["primary"]
                    discs = grp["discussions"]

                    chk_label_prim = (
                        f"Section **{prim.section_id}** ({prim.component}) | "
                        f"Instructor: {prim.instructor} | Location: {prim.location} | "
                        f"Times: {format_meeting_times(prim.meeting_times)}"
                    )
                    col_info_prim, col_chk_prim = st.columns([0.04, 0.96], vertical_alignment="center", gap="small")
                    with col_info_prim:
                        section_info_button(prim, key=f"info_chk_{course_id}_{prim.section_id}")
                    section_checkbox(col_chk_prim, chk_label_prim, prim.section_id)

                    if discs:
                        for disc in discs:
                            col_info, col_indent, col_chk = st.columns([0.04, 0.04, 0.92], vertical_alignment="center", gap="small")
                            with col_info:
                                section_info_button(disc, key=f"info_chk_{course_id}_{disc.section_id}")
                            with col_chk:
                                chk_label_disc = (
                                    f"↳ Discussion Section **{disc.section_id}** | "
                                    f"Instructor: {disc.instructor} | Location: {disc.location} | "
                                    f"Times: {format_meeting_times(disc.meeting_times)}"
                                )
                                section_checkbox(st, chk_label_disc, disc.section_id)



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
                render_clickable_calendar(current_schedule, color_map, chart_id=f"schedule_viewer_chart_{current_idx}")

                # Detailed course list for active schedule
                with st.expander("📋 Course Details in this Schedule", expanded=False):
                    render_schedule_breakdown(current_schedule, key_prefix=f"info_view_{current_idx}")



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
        render_clickable_calendar(chosen_saved, saved_color_map, chart_id=f"saved_schedule_chart_{view_term_code}_{selected_saved_idx}")

        with st.expander("📋 Saved Schedule Breakdown", expanded=True):
            render_schedule_breakdown(chosen_saved, key_prefix=f"info_saved_{view_term_code}_{selected_saved_idx}")

