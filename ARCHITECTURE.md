# Course Schedule Builder Architecture

## Overview
A pipeline-based system that scrapes university course listings, allows users to filter/select courses and sections, and computes non-overlapping schedule permutations.

## System Pipeline & Data Flow

1. Scraper Module (`scraper/`)
   - Inputs: University portal endpoint / filters (subject, semester).
   - Dynamic options: Scrapes valid semesters and subjects rather than hardcoding.
   - Output: `raw_courses.json` containing standardized course and section models.

2. Course Selector Module (`selector/courses.py`)
   - Input: Course catalog from scraper.
   - Functionality: UI/CLI prompt for users to pick courses. Supports continuous updates/re-selection across different subjects.
   - Output: `selected_courses.json` (filtered subset of catalog courses).

3. Section Selector Module (`selector/sections.py`)
   - Input: `selected_courses.json`.
   - Functionality: Filter out unwanted times/professors/formats per course.
   - Output: `candidate_sections.json` (eligible sections per course).

4. Permutator & Solver Module (`solver/permutator.py`)
   - Input: Grouped eligible sections per course.
   - Functionality: Cartesian product + overlap rejection algorithm (time interval collision detection).
   - Output: List of valid, non-overlapping weekly schedule matrices.

5. Presentation Layer (`cli/` or `ui/`)
   - Display rendered schedules with course IDs, section codes, meeting times, and locations.

6. Session State & Persistence Module (`core/session.py`)
   - Input: User course selections, section toggle actions, solver output, and user-saved schedules.
   - Functionality:
     - Caches scraped university catalogs to avoid redundant network requests.
     - Tracks user's chosen courses and custom section filters.
     - **Maintains saved schedules:** Allows users to bookmark/save preferred valid schedules.
     - Serializes and deserializes the entire application state (selections, exclusions, and saved schedules) to/from a local JSON state file (`session_state.json`).
   - Output: Filtered inputs for solver; list of saved, non-overlapping weekly schedules.

## Shared Data Contracts (Pydantic Models)

All modules must communicate using strict models (`models/schema.py`):

- `TimeBlock`:
  - `day`: Literal["M", "T", "W", "R", "F", "S", "U"]
  - `start_time`: `time` (e.g., 09:00)
  - `end_time`: `time` (e.g., 10:15)
- `Section`:
  - `section_id`: str
  - `course_id`: str
  - `instructor`: str
  - `meeting_times`: list[TimeBlock]
- `Course`:
  - `course_id`: str (e.g., "CS 101")
  - `title`: str
  - `subject`: str
  - `sections`: list[Section]
- `Schedule`:
  - `sections`: list[Section]
  - `has_overlap`: bool (always False in final outputs)

## Technical Constraints & Guidelines
- Language: Python 3.10+
- Type Safety: Full type annotations, validated via `pydantic`.
- Time Parsing: Convert strings (e.g., "9:30 AM - 10:45 AM") to integer minutes from midnight or `datetime.time` objects for collision checks.
- Testing: Pytest test suite covering interval overlap edge cases (adjacent times, exact overlaps, partial overlaps).