import os
from unittest.mock import MagicMock
import pytest
from scraper.parser import parse_semesters, parse_subjects, parse_courses
from scraper.client import ScraperClient
from models.schema import Course, Section, TimeBlock

@pytest.fixture
def search_page_html():
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "searchPage.html")
    with open(fixture_path, "r", encoding="utf-8") as f:
        return f.read()

@pytest.fixture
def search_results_html():
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "searchResults.html")
    with open(fixture_path, "r", encoding="utf-8") as f:
        return f.read()

@pytest.fixture
def discussion_example_html():
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "discussionExample.txt")
    with open(fixture_path, "r", encoding="utf-8") as f:
        return f.read()

def test_parse_semesters(search_page_html):
    semesters = parse_semesters(search_page_html)
    assert len(semesters) > 0
    # Verify specific semesters exist
    values = [s["value"] for s in semesters]
    names = [s["name"] for s in semesters]
    assert "0009" in values
    assert "Fall 2026" in names
    assert "0007" in values
    assert "Spring 2026" in names

def test_parse_subjects(search_page_html):
    subjects = parse_subjects(search_page_html)
    assert len(subjects) > 0
    # Verify specific subjects exist
    codes = [s["code"] for s in subjects]
    labels = [s["label"] for s in subjects]
    assert "BACC" in codes
    assert "Accounting" in labels
    assert "ICSI" in codes
    assert "Computer Science" in labels

def test_parse_courses(search_results_html):
    courses = parse_courses(search_results_html, "BACC")
    assert len(courses) > 0
    
    # Let's verify details for course 'BACC 211'
    bacc_211 = next((c for c in courses if c.course_id == "BACC 211"), None)
    assert bacc_211 is not None
    assert bacc_211.title == "Financial Accounting"
    assert bacc_211.subject == "BACC"
    
    # Verify section details
    sec_2259 = next((s for s in bacc_211.sections if s.section_id == "2259"), None)
    assert sec_2259 is not None
    assert sec_2259.instructor == "Moshier,Michelle"
    assert sec_2259.location == "Lecture Center 7"
    assert len(sec_2259.meeting_times) > 0
    
    # " TTH 09:00_AM-10:20_AM" -> Days should be parsed to T and R (Tuesday and Thursday)
    days = [tb.day for tb in sec_2259.meeting_times]
    assert "T" in days
    assert "R" in days
    
    # Verify start and end times
    assert sec_2259.meeting_times[0].start_time.hour == 9
    assert sec_2259.meeting_times[0].start_time.minute == 0
    assert sec_2259.meeting_times[0].end_time.hour == 10
    assert sec_2259.meeting_times[0].end_time.minute == 20

def test_client_fetch_courses(search_results_html):
    # Mock httpx.Client post response
    client = ScraperClient(term="0009")
    
    mock_response = MagicMock()
    mock_response.text = search_results_html
    mock_response.raise_for_status = MagicMock()
    
    client.client.post = MagicMock(return_value=mock_response)
    
    courses = client.fetch_courses("BACC")
    assert len(courses) > 0
    
    # Assert post was called with correct parameters
    client.client.post.assert_called_once()
    args, kwargs = client.client.post.call_args
    content = kwargs.get("content", "")
    
    import urllib.parse
    # Verify some key-values in form payload
    data_dict = dict(urllib.parse.parse_qsl(content))
    assert data_dict["USER"] == "0009"
    assert data_dict["Course_Subject"] == "BACC"
    assert data_dict["DELIMITER"] == "\\t"
    
    # Close client
    client.close()
def test_client_get_available_semesters_and_subjects(search_page_html):
    client = ScraperClient(term="0009")
    mock_response = MagicMock()
    mock_response.text = search_page_html
    mock_response.raise_for_status = MagicMock()
    client.client.get = MagicMock(return_value=mock_response)

    semesters = client.get_available_semesters()
    assert len(semesters) > 0
    assert any(s["value"] == "0009" for s in semesters)

    subjects = client.get_available_subjects()
    assert len(subjects) > 0
    assert any(s["code"] == "BACC" for s in subjects)

    client.close()

def test_client_error_handling_on_network_error():
    client = ScraperClient(term="0009")
    client.client.get = MagicMock(side_effect=Exception("Connection refused"))

    # When network error occurs, methods must NOT catch exceptions 
    # to prevent Streamlit from caching silent empty list failures.
    import pytest
    with pytest.raises(Exception, match="Connection refused"):
        client.get_available_semesters()

    with pytest.raises(Exception, match="Connection refused"):
        client.get_available_subjects()

    client.close()


def test_parse_courses_with_discussions(discussion_example_html):
    courses = parse_courses(discussion_example_html, "ICSI")
    assert len(courses) == 1
    icsi_311 = courses[0]
    assert icsi_311.course_id == "ICSI 311"
    assert len(icsi_311.sections) == 4

    lecture_sec = next((s for s in icsi_311.sections if s.section_id == "4834"), None)
    assert lecture_sec is not None
    assert lecture_sec.component == "Lecture"
    assert set(lecture_sec.linked_sections) == {"4835", "4836", "6198"}

    disc_sec = next((s for s in icsi_311.sections if s.section_id == "4835"), None)
    assert disc_sec is not None
    assert disc_sec.component == "Discussion"
    assert disc_sec.linked_sections == []


def test_extract_linked_sections_patterns():
    from scraper.parser import extract_linked_sections

    c1 = "Students registering for this section must FIRST register for a Discussion: 4835, 4836 or 6198"
    assert extract_linked_sections(c1) == ["4835", "4836", "6198"]

    c2 = "Students registering for this section must FIRST register for a DISC: 9388-9391. Discussion sessions will occur in The Learning Commons, LI-0036, Room 5 (LI0036H)"
    assert extract_linked_sections(c2) == ["9388", "9389", "9390", "9391"]

    c3 = "Students must register for Lab: 1001-1003 or 1005"
    assert extract_linked_sections(c3) == ["1001", "1002", "1003", "1005"]

    c4 = "No special restrictions"
    assert extract_linked_sections(c4) == []

    c5 = "Comments: Students Registering For This Section Must FIRST Register For One Disc From: 2607 - 2610, 2612, 3240, 3241 Students who do not advance register for this course cannot be given consideration for a permission number if the course closes."
    assert extract_linked_sections(c5) == ["2607", "2608", "2609", "2610", "2612", "3240", "3241"]



def test_parse_location_and_instructor_with_suffixes():
    from scraper.parser import _parse_location_and_instructor

    # Generational suffix II with classroom locations (as reported with Hono II,Daniel)
    loc, inst = _parse_location_and_instructor("Biology 248 Hono II,Daniel")
    assert loc == "Biology 248"
    assert inst == "Hono II,Daniel"

    loc, inst = _parse_location_and_instructor("Massry Schl of Business 231 Hono II,Daniel")
    assert loc == "Massry Schl of Business 231"
    assert inst == "Hono II,Daniel"

    loc, inst = _parse_location_and_instructor("Massry Schl of Business 141 Hono II,Daniel")
    assert loc == "Massry Schl of Business 141"
    assert inst == "Hono II,Daniel"

    loc, inst = _parse_location_and_instructor("Pine Bush 302 Hono II,Daniel")
    assert loc == "Pine Bush 302"
    assert inst == "Hono II,Daniel"

    # Space after comma
    loc, inst = _parse_location_and_instructor("Biology 248 Hono II, Daniel")
    assert loc == "Biology 248"
    assert inst == "Hono II, Daniel"

    # Other suffixes (Jr., III, etc.)
    loc, inst = _parse_location_and_instructor("Lecture Center 7 Smith Jr.,John")
    assert loc == "Lecture Center 7"
    assert inst == "Smith Jr.,John"

    loc, inst = _parse_location_and_instructor("Social Science 255 Doe III,Jane")
    assert loc == "Social Science 255"
    assert inst == "Doe III,Jane"


def test_parse_location_and_instructor_various_formats():
    from scraper.parser import _parse_location_and_instructor

    # Surnames with prefixes
    loc, inst = _parse_location_and_instructor("Lecture Center 7 Van Horn,David")
    assert loc == "Lecture Center 7"
    assert inst == "Van Horn,David"

    loc, inst = _parse_location_and_instructor("Massry Schl of Business 217 De La Cruz,Maria")
    assert loc == "Massry Schl of Business 217"
    assert inst == "De La Cruz,Maria"

    # Special locations
    loc, inst = _parse_location_and_instructor("Online Fernando,Guy")
    assert loc == "Online"
    assert inst == "Fernando,Guy"

    loc, inst = _parse_location_and_instructor("Online Hono II,Daniel")
    assert loc == "Online"
    assert inst == "Hono II,Daniel"

    loc, inst = _parse_location_and_instructor("Arranged Fernando,Guy")
    assert loc == "Arranged"
    assert inst == "Fernando,Guy"

    loc, inst = _parse_location_and_instructor("Off Campus Smith,John")
    assert loc == "Off Campus"
    assert inst == "Smith,John"

    loc, inst = _parse_location_and_instructor("Online")
    assert loc == "Online"
    assert inst == "Arranged"

    loc, inst = _parse_location_and_instructor("Arranged")
    assert loc == "Arranged"
    assert inst == "Arranged"

    # Classroom without instructor
    loc, inst = _parse_location_and_instructor("Lecture Center 7")
    assert loc == "Lecture Center 7"
    assert inst == "Arranged"

    loc, inst = _parse_location_and_instructor("Biology 248 Staff")
    assert loc == "Biology 248"
    assert inst == "Staff"

    # Lone instructor string without location
    loc, inst = _parse_location_and_instructor("Hono II,Daniel")
    assert loc == "TBD"
    assert inst == "Hono II,Daniel"


def test_parse_courses_hono_instructor():
    icsi_html = """
    <hr>
    Class Number: <b>4236</b><br>
    Course Info: <b>ICSI  333 System Fundamentals</b><br>
    Meeting Info: <b> TTH 01:30_PM-02:50_PM Biology 248 Hono II,Daniel</b><br>
    Comments: <b> Students registering for this section must FIRST register for a Lab: 4252, 4749 or 5284</b><br>
    <hr>
    Class Number: <b>4252</b><br>
    Course Info: <b>ICSI  333 System Fundamentals</b><br>
    Component is blank if lecture: <b>Lab</b><br>
    Meeting Info: <b> F 10:35_AM-11:30_AM Massry Schl of Business 231 Hono II,Daniel</b><br>
    Comments: <b></b><br>
    <hr>
    """
    courses = parse_courses(icsi_html, "ICSI", term="0007")
    assert len(courses) == 1
    c = courses[0]
    assert c.course_id == "ICSI 333"

    sec_4236 = next(s for s in c.sections if s.section_id == "4236")
    assert sec_4236.instructor == "Hono II,Daniel"
    assert sec_4236.location == "Biology 248"
    assert sec_4236.component == "Lecture"

    sec_4252 = next(s for s in c.sections if s.section_id == "4252")
    assert sec_4252.instructor == "Hono II,Daniel"
    assert sec_4252.location == "Massry Schl of Business 231"
    assert sec_4252.component == "Lab"

