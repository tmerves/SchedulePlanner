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

def test_client_fallback_on_network_error():
    client = ScraperClient(term="0009")
    client.client.get = MagicMock(side_effect=Exception("Connection refused"))

    # Fallback should return default constants instead of raising
    semesters = client.get_available_semesters()
    assert len(semesters) > 0

    subjects = client.get_available_subjects()
    assert len(subjects) > 0

    client.close()

