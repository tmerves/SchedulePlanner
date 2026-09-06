# Master Implementation Roadmap: Visual Course Schedule Generator

## Project Principles & Cline Rules
* **Environment:** Execute scripts using `.venv\Scripts\python.exe` (Windows).
* **Headless-First:** The engine (`solver/`, `models/`, `scraper/`) must operate completely independently of the visual layer. No UI logic inside the solver.
* **Testing Contract:** Every phase must pass 100% of its automated `pytest` suite before moving to the next.
* **Cline Context Control:** Start a fresh Cline task session (`+ New Task`) at the start of every phase to prevent token drift and instruction decay.

---

## Phase 1: Domain Models & Collision Math Engine (Headless)
**Goal:** Establish strict Pydantic schemas and collision detection for weekly time blocks without needing real course data or UI.

* **Tasks:**
  - Create directory layout: `models/`, `utils/`, `tests/`.
  - In `models/schema.py`, define `TimeBlock`, `Section`, `Course`, and `Schedule` using Pydantic v2.
  - In `utils/time_utils.py`, build:
    - String parser converting format strings (e.g., `"09:30 AM - 10:45 AM"`, `"14:00 - 15:15"`) into minutes-from-midnight integers (`start_min`, `end_min`).
    - Collision function: `is_time_conflict(block_a: TimeBlock, block_b: TimeBlock) -> bool`.
    - Section conflict check: `do_sections_conflict(sec_a: Section, sec_b: Section) -> bool`.
  - In `tests/test_time_utils.py`, write tests covering:
    - Identical day and overlapping times.
    - Identical day, back-to-back times (e.g., 10:00–10:50 and 10:50–11:40 — verify no conflict).
    - Different days, identical times (no conflict).
    - Multi-day blocks (e.g., MWF vs. TR).
* **Acceptance Criteria:** `pytest tests/test_time_utils.py` runs with 100% pass rate.
* **Cline Prompt to Run:**
  > "Read `@ARCHITECTURE.md`. We are implementing Phase 1. Create the project directory structure, `models/schema.py`, `utils/time_utils.py`, and `tests/test_time_utils.py`. Implement string-to-minute time parsing, day mapping, and overlap logic. Run pytest using our virtual environment and confirm all tests pass."

---

## Phase 2: Permutation Solver & Conflict Pruner (Headless)
**Goal:** Given a list of selected courses and their available sections, compute all valid, non-overlapping weekly schedules.

* **Tasks:**
  - In `solver/permutator.py`, implement `ScheduleSolver`:
    - Method `find_valid_schedules(courses: list[Course]) -> list[Schedule]`.
    - Use recursive backtracking or early-exit Cartesian product to prune invalid branches immediately upon encountering a conflicting section rather than generating all combinations upfront.
    - Support section filtering (e.g., exclude specific instructors, times, or section IDs).
  - In `tests/test_permutator.py`, create fixture mock courses (e.g., CS 101 with 3 sections, MATH 201 with 2 sections, PHYS 150 with conflicting times).
  - Verify that edge cases work: zero valid combinations, exactly 1 combination, and dozens of valid combinations.
* **Acceptance Criteria:** Solver generates expected schedules on mock data within <500ms; all tests pass.
* **Cline Prompt to Run:**
  > "Read `@ARCHITECTURE.md`. We are implementing Phase 2. Implement `solver/permutator.py` to calculate all conflict-free schedules using early-pruning backtracking. Write comprehensive unit tests in `tests/test_permutator.py` with mock course datasets. Run pytest."

---

## Phase 3: Dynamic University Scraper Engine
**Goal:** Extract course metadata, terms, and sections into the standard Phase 1 models.

* **Tasks:**
  - In `scraper/client.py`, create the base HTTP client using `httpx` or `requests` (with session caching where appropriate).
  - In `scraper/parser.py`:
    - Implement `get_available_semesters() -> list[dict]` to scrape active terms dynamically instead of hardcoding.
    - Implement `get_available_subjects(term_id: str) -> list[str]` to scrape active departments.
    - Implement `fetch_courses(term_id: str, subject: str) -> list[Course]` to parse raw markup/JSON into validated `Course` and `Section` Pydantic models.
  - In `tests/test_scraper.py`, write tests using saved HTML/JSON response fixtures (so tests do not hit live university servers during CI).
* **Acceptance Criteria:** Scraper transforms target web payload into valid `Course` objects conforming to `models/schema.py`.
* **Cline Prompt to Run:**
  > "Read `@ARCHITECTURE.md`. We are implementing Phase 3. Build the scraper module in `scraper/` to dynamically retrieve semesters, subjects, and course catalog listings. Standardize the output into our Pydantic `Course` models. Include mock response fixtures in `tests/fixtures/` and verify with `tests/test_scraper.py`."

---

## Phase 4: Application State & Data Cache
**Goal:** Provide a local file/session state manager so the user can repeatedly add/remove courses, filter sections, and manage saved schedules across different subjects without re-scraping.

* **Tasks:**
  - In `core/session.py`, implement `ScheduleSessionManager`:
    - Local cache for scraped catalog results (`data/cache/`).
    - Stored list of user-selected courses (`data/selected_courses.json`).
    - Stored map of excluded/included sections per course.
    - **Saved Schedules State:** Store a collection of selected/saved complete `Schedule` structures.
    - Methods:
      - Course & Section selection: `add_course(course)`, `remove_course(course_id)`, `toggle_section(section_id, is_active: bool)`, `get_active_courses() -> list[Course]`.
      - Saved Schedules: `save_schedule(schedule: Schedule)`, `remove_saved_schedule(index: int)`, `get_saved_schedules() -> list[Schedule]`, `clear_saved_schedules()`.
      - Persistence: `save_session_state(filepath: str)` and `load_session_state(filepath: str)` to serialize/deserialize selected courses, excluded sections, and saved schedules into a unified JSON structure.
  - In `tests/test_session.py`, verify state persistence across additions, removals, section toggles, and saved schedule management.
* **Acceptance Criteria:** State transitions and saved schedules accurately reflect user modifications, persistence is reliable and backward-compatible, and all tests pass.
* **Cline Prompt to Run:**
  > "Read `@ARCHITECTURE.md`. We are implementing Phase 4. Implement `core/session.py` to handle caching scraped catalog data, maintaining the user's active course selections, filtering sections, and managing saved schedules. Add tests in `tests/test_session.py` and run pytest."

---

## Phase 5: Interactive Visual Interface (Streamlit)
**Goal:** Build a robust, highly interactive visual calendar interface for course selection, section filtering, schedule permutation browsing, and saved schedules management.

* **Tasks:**
  - **Task 5.1: Package Installation & Requirements**
    - Install visual dependencies: `streamlit`, `plotly`, `pandas`.
    - Update `requirements.txt` to include these packages.
  - **Task 5.2: Enhance Scraper Client with Dynamic Endpoints**
    - In `scraper/client.py`, implement `fetch_search_page(self) -> str` to fetch the landing page `https://www.albany.edu/registrar/schedule-classes`.
    - Implement `get_available_semesters(self) -> list[dict]` and `get_available_subjects(self) -> list[dict]` to fetch and parse options dynamically.
    - Add robust try-except error handling with local fallback to ensure the client/app remains functional when offline.
  - **Task 5.3: Build Isolated Visualizer Module**
    - In `utils/visualizer.py`, create a coordinate-based schedule visualizer:
      - Coordinate system: X is day (0 to 4 for M-F), Y is decimal hour (reversed so 8 AM is at the top).
      - Function `create_schedule_calendar(schedule: Schedule, course_colors: dict[str, str] = None) -> plotly.graph_objects.Figure` which maps each `TimeBlock` to a custom rounded rectangle shape via `fig.add_shape` with pleasing pastel fills.
      - Automatically expand the Y-axis range if a class falls outside 8:00 AM – 8:00 PM.
      - Layer a transparent Scatter plot text trace to display course labels (Course ID, Section ID, Instructor) in the center of the blocks, with full hover tooltips.
      - Function `get_course_color_map(courses: list[Course] | list[Schedule]) -> dict[str, str]` to auto-assign consistent pastel colors to each unique course.
    - Write unit tests in `tests/test_visualizer.py` verifying that the figure generation is error-free.
  - **Task 5.4: Implement Streamlit Application Layout & Logic**
    - Create `app.py` as the application entry point.
    - **Session Integration:** Initialize `st.session_state.session_manager = ScheduleSessionManager()`. Automatically load persisted state on startup from `data/session_state.json`, and auto-save on any change.
    - **Interactive Tabs Structure:**
      1. **🔍 Catalog Browser:**
         - Sidebar: Dynamic Semester dropdown and searchable Subject multi-select.
         - Button: "Fetch Course Catalog" using a clean loading spinner (`st.spinner`).
         - Main Panel: Searchable course catalog grid. Expanding a course shows its sections and an "Add Course" button.
         - Display selected courses in an sidebar list with a "Clear All" action.
      2. **⚙️ Section Filter Checklist:**
         - Display an expander per selected course.
         - Inside each expander, list all sections in a table with section checkboxes (checked by default).
         - Unchecking a checkbox calls `session.toggle_section(section_id, is_active=False)` to exclude the section and auto-saves the state.
      3. **🗓️ Schedule Permutator Viewer:**
         - Button: "Compute Schedules".
         - Displays total valid schedules found. If > 0, show pagination controls (`Schedule 3 of 15`), a "Save Schedule" bookmark button, and render the Plotly weekly grid.
         - If 0, show a friendly explanation of the conflict and suggestions to resolve it.
      4. **💾 Saved Schedules Explorer:**
         - Dropdown to browse saved schedules.
         - Renders the Plotly weekly grid for the selected saved schedule.
         - Button to "Remove Saved Schedule" with instant state refresh.
* **Acceptance Criteria:**
  - `pytest tests/test_visualizer.py` passes with 100% success.
  - Running `streamlit run app.py` launches the UI successfully.
  - The catalog loads dynamically from the portal (or falls back gracefully if offline).
  - Adding/removing courses, excluding sections, and bookmarking/deleting schedules works interactively, with immediate canvas updates and visual state saved to `data/session_state.json`.
* **Cline Prompt to Run:**
  > "Read `@ARCHITECTURE.md` and `@ROADMAP.md`. We are implementing Phase 5. Add dynamic fetching methods to `scraper/client.py`. Build `utils/visualizer.py` with coordinate-based Plotly shapes and write tests in `tests/test_visualizer.py`. Implement `app.py` in Streamlit with 4 tabs and disk persistence. Verify all tests pass and run the Streamlit app."


---

## Phase 6: Polish, Export & Preferences
**Goal:** Add scheduling constraints and export formats.

* **Tasks:**
  - Add solver constraints:
    - Avoid early mornings (e.g., "No classes before 9:30 AM").
    - Avoid Friday classes.
    - Minimize time gaps between classes.
  - Add export options:
    - Download schedule as `.ics` (iCalendar file importable into Google Calendar / Apple Calendar).
    - Export schedule summary as PNG or formatted Markdown table.
* **Acceptance Criteria:** Valid schedules can be filtered by user preferences and downloaded as `.ics` files.