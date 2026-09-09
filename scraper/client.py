import httpx
import logging
from typing import List, Optional, Dict
from models.schema import Course
from scraper.parser import parse_courses, parse_semesters, parse_subjects
from scraper.rate_limiter import (
    BaseRateLimiter,
    get_global_rate_limiter,
    parse_retry_after,
)

logger = logging.getLogger(__name__)

class ScraperClient:
    """
    A university schedule scraper client that sends a POST request with parameters modeled from
    request_info.md, allowing dynamic subject and term substitution, and returning a list of parsed Course models.
    Coordinates requests through a process-wide rate limiter to avoid overwhelming university servers.
    Requires an active internet connection.
    """
    def __init__(
        self,
        term: str = "0009",
        base_url: str = "https://www.albany.edu/cgi-bin/general-search/search.pl",
        rate_limiter: Optional[BaseRateLimiter] = None,
    ):
        self.term = term
        self.base_url = base_url
        self.search_page_url = "https://www.albany.edu/registrar/schedule-classes"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.albany.edu/registrar/schedule-classes",
            "Origin": "https://www.albany.edu",
        }
        self.client = httpx.Client(headers=headers, timeout=15.0)
        self.rate_limiter = (
            rate_limiter if rate_limiter is not None else get_global_rate_limiter()
        )

    def _send_request_with_retry(self, method: str, url: str, **kwargs) -> httpx.Response:
        """
        Executes an HTTP request through the rate limiter.
        Handles HTTP 429 and 503 responses using exponential backoff and retries,
        triggering a process-wide cooldown window across all sessions.
        """
        max_retries = getattr(self.rate_limiter, "max_retries", 3)
        retries = 0

        while True:
            self.rate_limiter.acquire()

            if method.upper() == "GET":
                response = self.client.get(url, **kwargs)
            elif method.upper() == "POST":
                response = self.client.post(url, **kwargs)
            else:
                response = self.client.request(method, url, **kwargs)

            status = getattr(response, "status_code", 200)
            if isinstance(status, int) and status in (429, 503):
                retries += 1
                retry_after = None
                headers = getattr(response, "headers", {})
                if hasattr(headers, "get"):
                    retry_after_str = headers.get("Retry-After")
                    retry_after = parse_retry_after(retry_after_str)

                cooldown = self.rate_limiter.report_rate_limit_error(
                    status_code=status, retry_after=retry_after
                )

                if retries <= max_retries:
                    logger.warning(
                        "Received HTTP %d from %s. Process-wide cooldown active for %.2fs (retry %d/%d)...",
                        status,
                        url,
                        cooldown,
                        retries,
                        max_retries,
                    )
                    continue
                else:
                    logger.error(
                        "HTTP %d from %s exceeded max retries (%d). Raising error.",
                        status,
                        url,
                        max_retries,
                    )
                    if hasattr(response, "raise_for_status"):
                        response.raise_for_status()
                    return response

            self.rate_limiter.report_success()
            return response

    def fetch_search_page(self, term: Optional[str] = None) -> str:
        """Fetches the main schedule classes HTML page to extract available semesters and subjects."""
        selected_term = term or self.term
        response = self._send_request_with_retry("GET", f"{self.search_page_url}?user={selected_term}")
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        return response.text

    def get_available_semesters(self) -> List[Dict[str, str]]:
        """
        Dynamically fetches available semesters from the university website.
        """
        html = self.fetch_search_page()
        semesters = parse_semesters(html)
        return semesters if semesters else []

    def get_available_subjects(self, term: Optional[str] = None) -> List[Dict[str, str]]:
        """
        Dynamically fetches available academic subjects from the university website.
        """
        html = self.fetch_search_page(term=term)
        subjects = parse_subjects(html)
        return subjects if subjects else []

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
        response = self._send_request_with_retry(
            "POST",
            self.base_url,
            content=encoded_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        
        return parse_courses(response.text, subject, term=term_to_use)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        """Closes the underlying HTTPX client."""
        self.client.close()


UAlbanyScraperClient = ScraperClient
