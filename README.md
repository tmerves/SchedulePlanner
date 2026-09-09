The **Course Schedule Builder** is an open-source schedule optimization engine and interactive visual planner designed to eliminate the complexity of university course registration. Originally created to restore the functionality of the University at Albany's retired scheduling tool, the system dynamically scrapes live registrar course catalogs, isolates user preferences across different academic semesters, and solves for conflict-free weekly timetables.

It handles complex registration requirements automatically by bundling primary lectures with their required discussion or lab sections and pruning scheduling conflicts using early-exit backtracking algorithms. The application is built on a Python-based stack featuring a robust scraper with built-in process-wide rate limiting, persistent per-user session management tied to shareable URL parameters, and an interactive Streamlit web interface. Timetables are rendered onto customizable, coordinate-based Plotly calendars featuring 15-minute gridlines, location tags, and detailed inspection tooltips.

---

## Usage Options

You can run this project in two ways:

* **Live Web App:** Access the hosted application directly in your browser at [scheduleplanner.streamlit.app](https://scheduleplanner.streamlit.app/?session_id=680fc5f7450e).
* **Local Installation:** Run the application locally using Python by following the setup steps below.
## Quick Setup

### 1. Create a Virtual Environment

* **macOS / Linux:**
  ```bash
  python3 -m venv .venv
  ```
* **Windows:**
  ```bash
  python -m venv .venv
  ```

### 2. Activate the Virtual Environment

* **macOS / Linux:**
  ```bash
  source .venv/bin/activate
  ```
* **Windows:**
  ```bash
  .venv\Scripts\activate
  ```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Launch the Web Application

```bash
streamlit run app.py
```
