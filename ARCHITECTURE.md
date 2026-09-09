# Course Schedule Builder Architecture

## Overview
A modular, pipeline-based system that dynamically scrapes university course listings, manages user course selections and section preferences across isolated academic semesters, computes conflict-free weekly schedule permutations (including linked lectures, discussions, and labs), and renders interactive visual calendars.

---

## System Pipeline & Data Flow

```
University Portal (Web/HTML)
        │
        ▼
[Scraper Module: scraper/client.py & parser.py]
  - Dynamically fetches semesters and subjects
  - Parses courses, sections, time blocks, locations, components, and linked discussions/labs
  - Caches catalogs to data/cache/{term_code}_{subject_code}.json
        │
        ▼
[Session & Multi-Semester Manager: core/session.py]
  - Manages active semester and partitions state by term_code
  - Tracks user-selected courses, section exclusions, and bookmarked schedules
  - Persists per-user multi-semester state to data/sessions/{session_id}/session_state.json
  - Enforces cross-semester data integrity guards
  - Generates safe session IDs and validates against directory traversal attacks
        │
        ▼
[Permutator & Collision Engine: solver/permutator.py & utils/time_utils.py]
  - Bundles primary lectures with eligible linked discussions/labs (get_course_options)
  - Recursive backtracking with early-exit branch pruning
  - Evaluates day/time interval collisions (do_sections_conflict)
  - Guarantees non-overlapping schedules
        │
        ▼
[Presentation & Visual Layer: app.py & utils/visualizer.py]
  - Streamlit multi-tab web application (Catalog, Filter Checklist, Permutator Viewer, Saved Explorer)
  - Per-user unique URL parameter synchronization (?session_id=...)
  - Coordinate-based Plotly weekly calendar with pastel blocks, location tags, and rich tooltips
```

### 1. Dynamic University Scraper Engine (`scraper/client.py`, `scraper/parser.py`)
* **Inputs:** Target university registrar search endpoint (`search.pl`), academic semester code, and academic subject.
* **Dynamic Options:** Scrapes active semesters and departments dynamically (`get_available_semesters`, `get_available_subjects`) from live registrar endpoints with graceful error fallback handling (returning empty lists on network/service failure).
* **Field Parsing:**
  * Extracts meeting days and parses time spans into validated `TimeBlock` lists.
  * Separates physical classroom locations from instructor names (`_parse_location_and_instructor`), robustly handling room numbers with digits, generational suffixes (e.g. `II`, `Jr.`), compound surnames (e.g. `Van Horn`, `De La Cruz`), and non-classroom keywords (`Online`, `Arranged`, `Off Campus`).
  * Extracts section component types (`Lecture`, `Discussion`, `Lab`, `Seminar`, etc.).
  * Parses comments to identify linked discussion and lab section IDs (`extract_linked_sections`).
* **Multi-Subject Queries:** Supports batch fetching across multiple subjects (`fetch_multiple_courses`).
* **Caching:** Results cached by session manager at `data/cache/{term_code}_{subject_code}.json`.

### 2. Session State & Multi-Semester Isolation (`core/session.py`)
* **State Management:** `ScheduleSessionManager` encapsulates all active user state.
* **Per-User Session Isolation:**
  * Unique session IDs are generated (`generate_session_id`) and validated (`is_valid_session_id`) against path traversal.
  * User states are persisted to isolated paths: `data/sessions/{session_id}/session_state.json` and per-semester snapshots in `data/sessions/{session_id}/semesters/{term_code}.json`.
* **Shared Catalog Cache:** Course catalogs scraped across subjects are stored centrally in `data/cache/{term_code}_{subject_code}.json` and shared across all user sessions.
* **Per-Semester Isolation:** Courses, section exclusions, and saved schedules are strictly segregated by `term_code` in `_semesters: Dict[str, Dict[str, Any]]`.
* **Capacity Limits:** Limits selections to 15 courses (`MAX_SELECTED_COURSES = 15`) and bookmarked schedules to 8 (`MAX_SAVED_SCHEDULES = 8`) per semester. UI buttons are disabled when thresholds are reached to cleanly block overflow without exposing limit text.
* **Cross-Semester Guards:** Prevents accidental mixing of courses across semesters and disallows bookmarking schedules into incompatible semesters.
* **Solver Pre-Filtering:** `get_active_courses(term_code)` filters out excluded sections and prepares clean `Course` bundles for permutation solving.
* **Decoupled Architecture:** The session ID is strictly used for URL synchronization and disk path routing; domain models, permutator algorithms, and visualizers remain completely headless and session-agnostic.

### 3. Permutation Solver & Collision Engine (`solver/permutator.py`, `utils/time_utils.py`)
* **Collision Detection:** `utils/time_utils.py` converts time strings to minutes-from-midnight integers and performs interval intersection checks (`is_time_conflict`, `do_sections_conflict`).
* **Linked Bundle Generation:** `ScheduleSolver.get_course_options()` constructs candidate section bundles:
  * Standalone courses: `[lecture]`.
  * Courses with linked requirements: `[lecture, discussion]` or `[lecture, lab]`, verifying that the paired sections do not internally conflict.
* **Pruning Backtracker:** `find_valid_schedules()` traverses candidate course bundles using early-pruning recursive backtracking. Branches encountering a time conflict are aborted immediately.
* **Semester Verification:** Rejects course sets that span multiple distinct semesters (`ValueError`).

### 4. Presentation & Visualization Layer (`app.py`, `utils/visualizer.py`)
* **Streamlit Application (`app.py`):**
  * **Per-User URL Synchronization:** Synchronizes `st.query_params["session_id"]` with user sessions, generating random unique IDs on initial visit and enabling session recovery on return.
  * **Sidebar:** Dynamic semester dropdown and searchable multi-subject picker. Includes Session & Unique URL section with quick session ID copy and fresh session initialization. Switching semesters synchronizes active term and resets computed permutations.
  * **Tab 1: 🔍 Catalog Browser:** Searchable course list, showing section counts, instructors, locations, and nested linked discussions (`get_organized_course_sections`).
  * **Tab 2: ⚙️ Section Filter Checklist:** Per-course section checkboxes with indented discussion sections, plus bulk "Select All" and "Deselect All" controls.
  * **Tab 3: 🗓️ Schedule Permutator Viewer:** Solves valid schedules, provides navigation pagination and jump slider, displays bookmarked status, renders the Plotly calendar, and presents hierarchical breakdown tables.
  * **Tab 4: 💾 Saved Schedules Explorer:** Browse saved schedules for any semester, view visual timetable grids, inspect course breakdowns, or remove/clear saved schedules.
* **Visualizer Module (`utils/visualizer.py`):**
  * Coordinate system: X-axis represents day of the week (Mon–Fri, auto-expanding to Sat/Sun if needed); Y-axis represents decimal hour (reversed so mornings start at the top).
  * Column dividers: Vertical gridlines run in between day columns rather than down the column centers.
  * Quarter-hour horizontal gridlines: Divides each hour into 4 rows (15-minute intervals) with clean hourly labels on the Y-axis.
  * Rounded pastel block shapes with darker border tones (`_get_border_color`).
  * Displays course code, section number, component badge, meeting time, and physical classroom location.
  * Rich hover tooltips with complete section details and duration.

---

## Shared Data Contracts (Pydantic Models)

All modules communicate through strict Pydantic v2 schemas in `models/schema.py`:

```python
class TimeBlock(BaseModel):
    day: Literal["M", "T", "W", "R", "F", "S", "U"]
    start_time: time
    end_time: time

class Section(BaseModel):
    section_id: str
    course_id: str
    instructor: str
    meeting_times: List[TimeBlock]
    location: str = "TBD"
    term_code: Optional[str] = None
    component: str = "Lecture"
    linked_sections: List[str] = []

class Course(BaseModel):
    course_id: str
    title: str
    subject: str
    sections: List[Section]
    term_code: Optional[str] = None

class Schedule(BaseModel):
    sections: List[Section]
    has_overlap: bool = False
    term_code: Optional[str] = None
```

---

## Technical Constraints & Guidelines
- **Python Version:** Python 3.10+ (tested through 3.14).
- **Environment:** Dedicated virtual environment (`.venv`). Always run scripts via `.venv\Scripts\python.exe` (Windows).
- **Type Safety & Schema Validation:** Full type hints throughout, validated via `pydantic`.
- **Headless-First Engine:** `models/`, `solver/`, `scraper/`, and `core/` operate independently of Streamlit or presentation logic.
- **Time Parsing & Collision:** Parse time range strings into minutes-from-midnight integers for overlap detection (`utils/time_utils.py`).
- **Automated Verification:** Comprehensive `pytest` test suite covering time collisions, early-exit pruning, discussion/lab bundles, HTML parsing, multi-semester isolation, and visualizer generation.