import httpx
from typing import List, Optional, Dict
from models.schema import Course
from scraper.parser import parse_courses, parse_semesters, parse_subjects
from scraper.constants import AVAILABLE_SEMESTERS, AVAILABLE_SUBJECTS

class ScraperClient:
    """
    A university schedule scraper client that sends a POST request with parameters modeled from
    request_info.md, allowing dynamic subject and term substitution, and returning a list of parsed Course models.
    """
    def __init__(self, term: str = "0009", base_url: str = "https://www.albany.edu/cgi-bin/general-search/search.pl"):
        self.term = term
        self.base_url = base_url
        self.search_page_url = "https://www.albany.edu/registrar/schedule-classes"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.albany.edu/registrar/schedule-classes",
            "Origin": "https://www.albany.edu",
        }
        self.client = httpx.Client(headers=headers)

    def fetch_search_page(self, term: Optional[str] = None) -> str:
        """Fetches the main schedule classes HTML page to extract available semesters and subjects."""
        selected_term = term or self.term
        response = self.client.get(f"{self.search_page_url}?user={selected_term}")
        response.raise_for_status()
        return response.text

    def get_available_semesters(self) -> List[Dict[str, str]]:
        """
        Dynamically fetches available semesters from the university website.
        Falls back to AVAILABLE_SEMESTERS if offline or on network error.
        """
        try:
            html = self.fetch_search_page()
            semesters = parse_semesters(html)
            if semesters:
                return semesters
        except Exception:
            pass
        return list(AVAILABLE_SEMESTERS)

    def get_available_subjects(self, term: Optional[str] = None) -> List[Dict[str, str]]:
        """
        Dynamically fetches available academic subjects from the university website.
        Falls back to AVAILABLE_SUBJECTS if offline or on network error.
        """
        try:
            html = self.fetch_search_page(term=term)
            subjects = parse_subjects(html)
            if subjects:
                return subjects
        except Exception:
            pass
        return list(AVAILABLE_SUBJECTS)

    def fetch_courses(self, subject: str, term: Optional[str] = None) -> List[Course]:
        """
        Fetches schedule data for the given subject and term via POST request
        and parses it into a list of Course objects.
        """
        term_to_use = term or self.term
        
        # Build the exact form payload as url-encoded tuples
        payload = [
            ("USER", term_to_use),
            ("DELIMITER", "\\t"),
        ]
        for subst in [
            "G:Graduate",
            "U:Undergraduate",
            "L:Lab",
            "D:Discussion",
            "S:Seminar",
            "I:Independent Study",
            "GRD:A-E",
            "SUS:Satisfactory/Unsatisfactory",
            "GLU:Load Credit or Unsatisfactory",
            "GRU:Research Credit or Unsatisfactory"
        ]:
            payload.append(("SUBST_STR", subst))

        payload.extend([
            ("HEADING_FONT_FACE", "Arial"),
            ("HEADING_FONT_SIZE", "3"),
            ("HEADING_FONT_COLOR", "black"),
            ("RESULTS_PAGE_TITLE", ""),
            ("RESULTS_PAGE_BGCOLOR", "#F0F0F0"),
            ("RESULTS_PAGE_HEADING", ""),
            ("RESULTS_PAGE_FONT_FACE", "Arial"),
            ("RESULTS_PAGE_FONT_SIZE", "2"),
            ("RESULTS_PAGE_FONT_COLOR", "black"),
            ("NO_MATCHES_MESSAGE", "Sorry, no classes were found that match your criteria."),
        ])

        for np in ["3", "4", "6", "7", "8", "26"]:
            payload.append(("NO_PRINT", np))

        payload.extend([
            ("GREATER_THAN_EQ", "26"),
            ("Level", ""),
            ("College_or_School", ""),
            ("Department_or_Program", ""),
            ("Course_Subject", subject),
            ("Course_Number", ""),
            ("Class_Number", ""),
            ("Course_Title", ""),
            ("Days", ""),
            ("Instructor", ""),
            ("Grading", ""),
            ("Course_Info", ""),
            ("Meeting_Info", ""),
            ("Comments", ""),
            ("Credit_Range", ""),
            ("Component_is_blank_if_lecture", ""),
            ("Topic_if_applicable", ""),
            ("Seats_remaining_as_of_last_update", ""),
            ("Session", ""),
            ("IT_Commons", ""),
            ("Course_Delivery_Method", ""),
            ("General_Education_Course", ""),
            ("Honors_College_Course", ""),
            ("Writing_Intensive", ""),
            ("Oral_Discourse", ""),
            ("Info_Literacy", ""),
            ("Special_Restriction", ""),
            ("Seats_Available", ""),
            ("COIL_-_Collaborative_Online_International_Learning", ""),
            ("OER_-_Open_Educational_Resources", ""),
            ("Liberal_Arts_Course", ""),
            ("Course_Description", ""),
        ])

        import urllib.parse
        encoded_payload = urllib.parse.urlencode(payload)
        response = self.client.post(
            self.base_url,
            content=encoded_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        response.raise_for_status()
        
        return parse_courses(response.text, subject, term=term_to_use)

    def fetch_multiple_courses(self, subjects: List[str], term: Optional[str] = None) -> List[Course]:
        """
        Dynamically fetches and aggregates courses across multiple academic subjects.
        """
        aggregated: List[Course] = []
        for subject in subjects:
            try:
                courses = self.fetch_courses(subject=subject, term=term)
                aggregated.extend(courses)
            except Exception:
                pass
        return aggregated

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        """Closes the underlying HTTPX client."""
        self.client.close()


UAlbanyScraperClient = ScraperClient
