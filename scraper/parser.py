import re
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from models.schema import Course, Section, TimeBlock
from utils.time_utils import parse_time_blocks

def parse_semesters(html_content: str) -> List[Dict[str, str]]:
    """
    Extracts available semester names and values from search form dropdown or list.
    Supports both the client-rendered <select id="semmesterselector"> and the raw HTML unordered list.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    semesters = []
    
    # 1. Check for select dropdown
    select = soup.find("select", id="semmesterselector")
    if select:
        for option in select.find_all("option"):
            val = option.get("value")
            if val:  # Skip option with empty value (e.g. "select a semester")
                semesters.append({
                    "value": val.strip(),
                    "name": option.get_text().strip()
                })
        if semesters:
            return semesters

    # 2. Check for unordered list format in raw server HTML: <li>0007 | Spring 2026</li>
    for li in soup.find_all("li"):
        txt = li.get_text().strip()
        if "|" in txt:
            parts = txt.split("|", 1)
            code = parts[0].strip()
            name = parts[1].strip()
            if re.match(r'^\d{4}$', code):
                semesters.append({
                    "value": code,
                    "name": name
                })
    return semesters

def parse_subjects(html_content: str) -> List[Dict[str, str]]:
    """
    Extracts available subjects (e.g. code 'BACC', label 'Accounting')
    from the HTML content. Supports both the raw `<select>` element and 
    the rendered jQuery UI autocomplete options.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    subjects = []
    
    # Try parsing from the <select name="Course_Subject"> first (Live HTML)
    select = soup.find("select", {"name": "Course_Subject"})
    if select:
        for option in select.find_all("option"):
            val = option.get("value", "").strip()
            text = option.get_text().strip()
            if val and text != "All Subjects":
                if " - " in text:
                    parts = text.split(" - ", 1)
                    subjects.append({"code": parts[0].strip(), "label": parts[1].strip()})
                else:
                    subjects.append({"code": val, "label": text})
        if subjects:
            return subjects

    # Fallback to autocomplete options (Test Fixture HTML)
    divs = soup.find_all(class_="ui-menu-item-wrapper")
    for div in divs:
        text = div.get_text().strip()
        if text == "All Subjects":
            continue
        if " - " in text:
            parts = text.split(" - ", 1)
            code = parts[0].strip()
            label = parts[1].strip()
            subjects.append({
                "code": code,
                "label": label
            })
    return subjects

def _parse_location_and_instructor(text: str) -> tuple[str, str]:
    """
    Splits the trailing meeting info text into location and instructor.
    UAlbany instructor names typically format as 'Last,First' or 'Last,First Middle'.
    """
    text = text.strip()
    if not text:
        return "TBD", "Arranged"

    tokens = text.split()
    comma_idx = -1
    for i, tok in enumerate(tokens):
        if "," in tok:
            comma_idx = i
            break

    if comma_idx != -1:
        loc = " ".join(tokens[:comma_idx]).strip() or "TBD"
        inst = " ".join(tokens[comma_idx:]).strip() or "Arranged"
        return loc, inst

    if len(tokens) == 1:
        if tokens[0].lower() in ("online", "arranged", "remote", "tbd"):
            return tokens[0], "Arranged"
        return "TBD", tokens[0]

    return " ".join(tokens[:-1]).strip() or "TBD", tokens[-1].strip() or "Arranged"


def extract_linked_sections(comment: str) -> List[str]:
    """
    Extracts linked discussion or lab section IDs from comments.
    Handles single IDs (e.g. '4835, 4836 or 6198') and ranges (e.g. '9388-9391').
    """
    if not comment:
        return []
    linked = []
    m = re.search(
        r'(?:Discussion|DISC|Lab|LAB)(?:\s+section|\s+sections)?[:\s]+([0-9\s,or\-]+)',
        comment,
        re.IGNORECASE
    )
    if m:
        target_str = m.group(1)
        ranges = re.findall(r'(\d{4,5})\s*-\s*(\d{4,5})', target_str)
        for r_start, r_end in ranges:
            start_num = int(r_start)
            end_num = int(r_end)
            if start_num <= end_num and end_num - start_num <= 50:
                for num in range(start_num, end_num + 1):
                    linked.append(str(num))
        clean_str = re.sub(r'\d{4,5}\s*-\s*\d{4,5}', '', target_str)
        singles = re.findall(r'\b(\d{4,5})\b', clean_str)
        linked.extend(singles)
    return sorted(list(set(linked)))


def parse_courses(html_content: str, subject: str, term: Optional[str] = None) -> List[Course]:
    """
    Parses search results HTML content and converts table rows (represented by key-value structures)
    into a list of Course objects with their associated Sections and TimeBlocks.
    Optionally assigns the semester term_code to each Course and Section.
    """
    # Split by horizontal rule (<hr>) tags representing individual section records
    blocks = re.split(r'<hr\s*/?>', html_content, flags=re.IGNORECASE)
    courses_dict = {}

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # Extract all "Key: <b>Value</b>" elements from the block
        lines = re.split(r'<br\s*/?>', block, flags=re.IGNORECASE)
        kvs = {}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            match = re.search(r'([^:]+):\s*<b>(.*?)</b>', line, re.DOTALL | re.IGNORECASE)
            if match:
                k = match.group(1).strip()
                v = match.group(2).strip()
                kvs[k] = v

        # A valid section block must contain at least Class Number and Course Info
        if "Class Number" not in kvs or "Course Info" not in kvs:
            continue

        class_num = kvs["Class Number"].strip()
        course_info = kvs["Course Info"].strip()

        # Parse course_id and title from course_info (e.g., "BACC  211 Financial Accounting")
        course_match = re.match(r'^([A-Z]+)\s*(\d+[A-Z]*)\s+(.*)$', course_info, re.IGNORECASE)
        if course_match:
            subj_code = course_match.group(1).strip().upper()
            catalog_num = course_match.group(2).strip().upper()
            course_title = course_match.group(3).strip()
            course_id = f"{subj_code} {catalog_num}"
        else:
            parts = course_info.split(None, 2)
            if len(parts) >= 3:
                subj_code = parts[0].strip().upper()
                catalog_num = parts[1].strip().upper()
                course_title = parts[2].strip()
                course_id = f"{subj_code} {catalog_num}"
            else:
                subj_code = subject.upper()
                course_id = course_info
                course_title = course_info

        # Parse Meeting Info to get day/times, location, and instructor
        meeting_info = kvs.get("Meeting Info", "").strip()
        meeting_times = []
        location = "TBD"
        instructor = "Arranged"

        if meeting_info:
            # Find the time range pattern: e.g. "09:00_AM-10:20_AM"
            time_match = re.search(r'(\d{1,2}:\d{2}_[AP]M)-(\d{1,2}:\d{2}_[AP]M)', meeting_info, re.IGNORECASE)
            if time_match:
                time_range = time_match.group(0)
                start_idx = time_match.start()
                end_idx = time_match.end()

                days_str = meeting_info[:start_idx].strip().upper()
                # Map TH to R for Thursday representation
                days_str = days_str.replace("TH", "R")
                rest = meeting_info[end_idx:].strip()

                location, instructor = _parse_location_and_instructor(rest)

                if days_str:
                    try:
                        clean_time_range = time_range.replace('_', ' ')
                        meeting_times = parse_time_blocks(days_str, clean_time_range)
                    except Exception:
                        meeting_times = []
            else:
                location, instructor = _parse_location_and_instructor(meeting_info)

        component = kvs.get("Component is blank if lecture", "").strip() or "Lecture"
        comments = kvs.get("Comments", "").strip()
        linked_sections = extract_linked_sections(comments)

        section_obj = Section(
            section_id=class_num,
            course_id=course_id,
            instructor=instructor or "Arranged",
            meeting_times=meeting_times,
            location=location or "TBD",
            term_code=term,
            component=component,
            linked_sections=linked_sections,
        )

        if course_id not in courses_dict:
            courses_dict[course_id] = {
                "course_id": course_id,
                "title": course_title,
                "subject": subj_code,
                "term_code": term,
                "sections": []
            }
        courses_dict[course_id]["sections"].append(section_obj)

    return [Course(**data) for data in courses_dict.values()]
