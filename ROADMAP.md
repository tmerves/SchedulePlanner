# Master Implementation Roadmap: Visual Course Schedule Generator

## Project Principles & Cline Rules
* **Environment:** Execute scripts using `.venv\Scripts\python.exe` (Windows).
* **Headless-First:** The engine (`solver/`, `models/`, `scraper/`, `core/`) must operate completely independently of the visual layer. No UI logic inside the solver or session manager.
* **Testing Contract:** Every phase must pass 100% of its automated `pytest` suite before moving to the next.
* **Cline Context Control:** Start a fresh Cline task session (`+ New Task`) at the start of every phase to prevent token drift and instruction decay.

---

## Phase 1: Domain Models & Collision Math Engine (Headless) [COMPLETED]
**Goal:** Establish strict Pydantic schemas and collision detection for weekly time blocks without needing real course data or UI.

* **Implemented Tasks:**
  - Create directory layout: `models/`, `utils/`, `tests/`.
  - In `models/schema.py`, defined `TimeBlock`, `Section`, `Course`, and `Schedule` using Pydantic v2:
    - Added `location`, `term_code`, `component` (e.g., Lecture, Discussion, Lab), and `linked_sections` to `Section`.
    - Added `term_code` to `Course` and `Schedule` for multi-semester integrity.
  - In `utils/time_utils.py`:
    - String parser converting format strings (e.g., `"09:30 AM - 10:45 AM"`, `"14:00 - 15:15"`) into minutes-from-midnight integers (`start_min`, `end_min`).
    - Collision function: `is_time_conflict(block_a: TimeBlock, block_b: TimeBlock) -> bool`.
    - Section conflict check: `do_sections_conflict(sec_a: Section, sec_b: Section) -> bool`.
  - In `tests/test_time_utils.py`, test suite covering:
    - Identical day and overlapping times.
    - Identical day, back-to-back times (no conflict).
    - Different days, identical times (no conflict).
    - Multi-day blocks (e.g., MWF vs. TR).
* **Acceptance Criteria & Status:** Complete. `pytest tests/test_time_utils.py` passes with 100% success rate.

---

## Phase 2: Permutation Solver & Conflict Pruner with Linked Bundles (Headless) [COMPLETED]
**Goal:** Given a list of selected courses and their available sections, compute all valid, non-overlapping weekly schedules supporting linked lecture + discussion/lab pairs.

* **Implemented Tasks:**
  - In `solver/permutator.py`, implemented `ScheduleSolver`:
    - `get_course_options(course, excluded_section_ids)`: Generates valid bundles. Standalone lectures become `[lecture]`, while courses requiring linked discussions/labs become `[lecture, discussion/lab]`. Prunes conflicting lecture-discussion combinations upfront.
    - `find_valid_schedules(courses: list[Course], excluded_section_ids: set[str]) -> list[Schedule]`: Recursive backtracking with early branch pruning on bundle collision.
    - Multi-semester check: Strictly rejects course sets that span multiple semesters (`ValueError`).
  - In `tests/test_permutator.py`:
    - Fixture mock courses (e.g., CS 101, MATH 201, PHYS 150).
    - Edge cases: zero valid combinations, exactly 1 combination, dozens of valid combinations.
    - Dedicated test cases for linked discussion/lab bundles (`test_course_with_linked_discussions`).
* **Acceptance Criteria & Status:** Complete. Solver generates expected schedules on mock data within <500ms; `pytest tests/test_permutator.py` passes 100%.

---

## Phase 3: Dynamic University Scraper Engine & Parser [COMPLETED]
**Goal:** Extract course metadata, terms, sections, classroom locations, and linked sections into standard Pydantic models.

* **Implemented Tasks:**
  - In `scraper/client.py`:
    - Base HTTP client with headers modeled after browser requests (`User-Agent`, `Referer`, `Origin`).
    - `fetch_search_page(term)`: Retrieves registrar landing page.
    - `get_available_semesters() -> list[dict]`: Scrapes active terms dynamically with static fallback (`AVAILABLE_SEMESTERS`).
    - `get_available_subjects(term) -> list[dict]`: Scrapes active departments dynamically with static fallback (`AVAILABLE_SUBJECTS`).
    - `fetch_courses(subject, term) -> list[Course]`: Form payload POST query returning parsed course models.
    - `fetch_multiple_courses(subjects, term) -> list[Course]`: Batch fetch across multiple departments.
  - In `scraper/parser.py`:
    - `parse_semesters(html)` and `parse_subjects(html)`.
    - `_parse_location_and_instructor(text)`: Distinguishes physical building/room location from instructor names.
    - `extract_linked_sections(comment)`: Extracts linked discussion/lab IDs (including single IDs and numeric ranges) from section comments.
    - `parse_courses(html, subject, term)`: Transforms HTML key-value blocks into validated `Course` models with tagged `term_code`.
  - In `tests/test_scraper.py`: Automated tests using offline HTML fixtures (`tests/fixtures/searchPage.html`, `searchResults.html`).
* **Acceptance Criteria & Status:** Complete. Scraper transforms target web payload into valid `Course` objects conforming to `models/schema.py`; `pytest tests/test_scraper.py` passes 100%.

---

## Phase 4: Application State & Multi-Semester Isolation Manager [COMPLETED]
**Goal:** Provide local caching, per-semester state isolation, section exclusion management, and saved schedule persistence.

* **Implemented Tasks:**
  - In `core/session.py`, implemented `ScheduleSessionManager`:
    - Local disk cache for scraped catalogs in `data/cache/{term_code}_{subject_code}.json`.
    - **Multi-Semester Partitioning:** Isolated dictionaries per semester in `_semesters: Dict[str, Dict[str, Any]]` for courses, exclusions, and saved schedules.
    - Semester switching via `set_term(term_code)`, `get_term()`, and `get_known_terms()`.
    - Cross-semester safety guards: Disallows adding courses or bookmarking schedules across mismatched terms.
    - Course & Section controls: `add_course()`, `remove_course()`, `clear_courses()`, `toggle_section()`, `get_active_courses()`.
    - Saved Schedules: `save_schedule()`, `remove_saved_schedule()`, `get_saved_schedules()`, `clear_saved_schedules()`.
    - Dual Persistence: `save_session_state()` and `load_session_state()` save unified multi-semester state in `data/session_state.json` and per-semester snapshots in `data/semesters/{term_code}.json` with backward-compatible legacy migration.
  - In `tests/test_session.py`: Comprehensive tests verifying semester isolation, cross-semester error handling, section toggles, and multi-semester persistence.
* **Acceptance Criteria & Status:** Complete. State transitions and saved schedules accurately reflect user modifications, multi-semester isolation is enforced, persistence is reliable and backward-compatible; `pytest tests/test_session.py` passes 100%.

---

## Phase 5: Interactive Visual Interface (Streamlit & Plotly) [COMPLETED]
**Goal:** Build a robust, highly interactive visual calendar interface for course selection, section filtering, schedule permutation browsing, and saved schedules management.

* **Implemented Tasks:**
  - **Task 5.1: Package Installation & Requirements**
    - Installed visual dependencies: `streamlit`, `plotly`, `pandas`.
    - Added packages to `requirements.txt`.
  - **Task 5.2: Enhance Scraper Client with Dynamic Endpoints**
    - In `scraper/client.py`, implemented `fetch_search_page()` to query `https://www.albany.edu/registrar/schedule-classes`.
    - Implemented `get_available_semesters()` and `get_available_subjects()` with automatic fallback for offline resilience.
    - Added `fetch_multiple_courses()` for multi-subject catalog aggregation.
  - **Task 5.3: Build Isolated Visualizer Module**
    - In `utils/visualizer.py`, created coordinate-based schedule visualizer:
      - Coordinate system: X is day of week (0 to 4 for M-F, auto-expanding for weekends), Y is decimal hour (reversed so mornings start at the top).
      - `create_schedule_calendar(schedule, course_colors)`: Renders custom rounded pastel rectangle shapes via `fig.add_shape` with darker borders (`_get_border_color`).
      - Automatically expands Y-axis range if classes fall outside 8:00 AM – 8:00 PM.
      - Displays course ID, section ID, component badge (`Lecture`, `Discussion`, `Lab`), times, and classroom location.
      - Rich hover tooltips displaying full section details, instructor, component type, room, and duration.
      - `get_course_color_map()`: Consistently assigns pastel colors across unique courses.
    - Automated tests in `tests/test_visualizer.py` verifying figure generation and layout bounds.
  - **Task 5.4: Implement Streamlit Application Layout & Multi-Semester Logic**
    - In `app.py`:
      - **Multi-Semester Synchronization:** Sidebar academic semester dropdown synchronizes `ScheduleSessionManager.active_term_code`, isolating catalogs, working courses, and computed schedules.
      - **Tab 1: 🔍 Catalog Browser:** Searchable course catalog with instant search filtering, section counts, add/remove buttons, and hierarchical grouping of lectures and nested discussions (`get_organized_course_sections`).
      - **Tab 2: ⚙️ Section Filter Checklist:** Per-course section toggles with nested discussion checkboxes, Select All / Deselect All bulk actions, and auto-saving to session state.
      - **Tab 3: 🗓️ Schedule Permutator Viewer:** Compute conflict-free schedules button, pagination navigation with jump slider, duplicate-aware Bookmark button, interactive Plotly calendar chart, and hierarchical schedule breakdown dataframe.
      - **Tab 4: 💾 Saved Schedules Explorer:** Semester selector dropdown to browse saved schedules for any term, Remove Schedule and Clear All actions, Plotly calendar view, and schedule breakdown table.
* **Acceptance Criteria & Status:** Complete.
  - `pytest tests/test_visualizer.py` passes 100%.
  - `streamlit run app.py` launches cleanly with full state persistence to `data/session_state.json`.

---

## Phase 6: Polish, Export & Preferences [PENDING / NEXT UP]
**Goal:** Add user scheduling constraints, preference-based ranking, and export formats.

* **Planned Tasks:**
  - **Solver Constraints & Preferences:**
    - Avoid early mornings (e.g., "No classes before 9:30 AM").
    - Avoid Friday classes.
    - Maximize/minimize time gaps between classes (compact schedules vs. study breaks).
    - Instructor preference filters.
  - **Schedule Export Formats:**
    - Download schedule as `.ics` (iCalendar format importable into Google Calendar, Apple Calendar, Outlook).
    - Export schedule summary as PNG image or formatted Markdown / PDF summary table.
  - **UI Controls:**
    - Add preferences section in `app.py` before running solver.
    - Add download buttons in Schedule Permutator Viewer and Saved Schedules Explorer.
* **Acceptance Criteria:** Valid schedules can be filtered by user preferences and downloaded as `.ics` calendar files.