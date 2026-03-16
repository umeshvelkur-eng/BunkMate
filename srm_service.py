from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin
import base64
import copy
from datetime import date, datetime, timedelta
import json
import re
import secrets
import time

from playwright.sync_api import Browser, BrowserContext, Frame, Page, Playwright, TimeoutError as PlaywrightTimeoutError, sync_playwright


PORTAL_URL = "https://academia.srmist.edu.in/"
ATTENDANCE_URL = "https://academia.srmist.edu.in/#Page:My_Attendance"
TIMETABLE_URL = "https://academia.srmist.edu.in/#Page:My_Time_Table_2023_24"
PROFILE_URL = "https://academia.srmist.edu.in/#Report:Student_Profile_Report"
PLANNER_URL = "https://academia.srmist.edu.in/#Page:Academic_Planner_2025_26_EVEN"
DEBUG_DIR = Path(__file__).parent / "data" / "live_debug"
TRACE_FILE = DEBUG_DIR / "login-trace.log"

PERIOD_TIMES = [
    ("08:00", "08:50"),
    ("08:50", "09:40"),
    ("09:45", "10:35"),
    ("10:40", "11:30"),
    ("11:35", "12:25"),
    ("12:30", "01:20"),
    ("01:25", "02:15"),
    ("02:20", "03:10"),
    ("03:10", "04:00"),
    ("04:00", "04:50"),
    ("04:50", "05:30"),
    ("05:30", "06:10"),
]

DAY_ORDER_SLOT_MAP = {
    "1": {
        "1": ["A", "A", "F", "F", "G", "P6", "P7", "P8", "P9", "P10", "L11", "L12"],
        "2": ["P1", "P2", "P3", "P4", "P5", "A", "A", "F", "F", "G", "L11", "L12"],
        "3": ["C", "C", "A", "D", "B", "P26", "P27", "P28", "P29", "P30", "L31", "L32"],
        "4": ["D", "D", "B", "E", "C", "P36", "P37", "P38", "P39", "P40", "L41", "L42"],
        "5": ["E", "E", "C", "F", "D", "P46", "P47", "P48", "P49", "P50", "L51", "L52"],
    },
    "2": {
        "1": ["P1", "P2", "P3", "P4", "P5", "A", "A", "F", "F", "G", "L11", "L12"],
        "2": ["B", "B", "G", "G", "A", "P16", "P17", "P18", "P19", "P20", "L21", "L22"],
        "3": ["P21", "P22", "P23", "P24", "P25", "C", "C", "A", "D", "B", "L31", "L32"],
        "4": ["D", "D", "B", "E", "C", "P36", "P37", "P38", "P39", "P40", "L41", "L42"],
        "5": ["P41", "P42", "P43", "P44", "P45", "E", "E", "C", "F", "D", "L51", "L52"],
    }
}

PLANNER_EVENTS_2026 = {
    "2026-01-01": ("Holiday", "New Year's Day"),
    "2026-01-05": ("Academic", "Enrolment Day - B.Tech / M.Tech"),
    "2026-01-08": ("Academic", "Commencement of Classes"),
    "2026-01-15": ("Holiday", "Pongal"),
    "2026-01-16": ("Holiday", "Thiruvalluvar Day"),
    "2026-01-17": ("Holiday", "Uzhavar Thirunal"),
    "2026-01-26": ("Holiday", "Republic Day"),
    "2026-02-01": ("Holiday", "Thaipoosam"),
    "2026-03-04": ("Holiday", "Holi"),
    "2026-03-19": ("Holiday", "Telugu New Year's Day"),
    "2026-03-21": ("Holiday", "Ramzan"),
    "2026-04-03": ("Holiday", "Good Friday"),
    "2026-04-14": ("Holiday", "Tamil New Year's Day / Dr. B.R. Ambedkar's Birthday"),
    "2026-05-01": ("Holiday", "May Day"),
    "2026-05-06": ("Academic", "Last working Day"),
    "2026-05-28": ("Holiday", "Bakrid"),
    "2026-06-26": ("Holiday", "Muharram"),
}


@dataclass
class PreloginContext:
    prelogin_id: str
    browser_context: BrowserContext
    page: Page
    created_at: float


@dataclass
class LiveSession:
    session_id: str
    browser_context: BrowserContext
    page: Page
    dashboard: dict
    created_at: float


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables = []
        self._inside_table = False
        self._inside_row = False
        self._inside_cell = False
        self._cell_tag = ""
        self._current_table = []
        self._current_row = []
        self._current_cell = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._inside_table = True
            self._current_table = []
        elif self._inside_table and tag == "tr":
            self._inside_row = True
            self._current_row = []
        elif self._inside_row and tag in {"th", "td"}:
            self._inside_cell = True
            self._cell_tag = tag
            self._current_cell = []

    def handle_data(self, data):
        if self._inside_cell:
            stripped = data.strip()
            if stripped:
                self._current_cell.append(stripped)

    def handle_endtag(self, tag):
        if self._inside_row and self._inside_cell and tag == self._cell_tag:
            self._current_row.append(" ".join(self._current_cell))
            self._inside_cell = False
            self._cell_tag = ""
            self._current_cell = []
        elif self._inside_row and tag == "tr":
            if self._current_row:
                self._current_table.append(self._current_row)
            self._inside_row = False
            self._current_row = []
        elif self._inside_table and tag == "table":
            if self._current_table:
                self.tables.append(self._current_table)
            self._inside_table = False
            self._current_table = []


class SrmPortalService:
    def __init__(self, sample_dashboard_path: Path) -> None:
        self.sample_dashboard_path = sample_dashboard_path
        self.prelogins: dict[str, PreloginContext] = {}
        self.sessions: dict[str, LiveSession] = {}
        self._playwright: Playwright = sync_playwright().start()
        self._browser: Browser = self._playwright.chromium.launch(headless=True)
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    def create_auth_context(self) -> dict:
        self._trace("create_auth_context:start")
        browser_context = self._browser.new_context()
        page = browser_context.new_page()
        page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        frame = self._get_login_frame(page)
        if frame is None:
            self._trace("create_auth_context:error:no_login_frame")
            raise ValueError("Could not locate the Academia login frame.")

        prelogin_id = secrets.token_hex(24)
        self.prelogins[prelogin_id] = PreloginContext(
            prelogin_id=prelogin_id,
            browser_context=browser_context,
            page=page,
            created_at=time.time()
        )

        captcha_image = self._capture_visible_captcha(frame)
        self._trace(f"create_auth_context:ok captcha_required={bool(captcha_image)}")
        return {
            "mode": "academia",
            "preloginId": prelogin_id,
            "usernameLabel": "SRM Email Address",
            "usernamePlaceholder": "yourid@srmist.edu.in",
            "usernameHint": "Use the email address expected by the Academia portal.",
            "captchaRequired": bool(captcha_image),
            "captchaImage": captcha_image
        }

    def login(self, username: str, password: str, captcha: str, prelogin_id: str) -> tuple[str, dict]:
        self._trace(f"login:start user={username} prelogin_id={prelogin_id[:8]}")
        context = self.prelogins.get(prelogin_id)
        if context is None:
            self._trace("login:error:expired_prelogin")
            raise ValueError("Login session expired. Refresh and try again.")

        page = context.page
        frame = self._get_login_frame(page)
        if frame is None:
            self._trace("login:error:no_login_frame")
            raise ValueError("Could not access the Academia login frame.")

        try:
            self._run_login_flow(frame, username, password, captcha)
        except PlaywrightTimeoutError:
            self._trace("login:error:timeout")
            raise ValueError("Academia login timed out. Try again.")
        except ValueError as error:
            self._trace(f"login:error:{str(error)}")
            raise

        page.wait_for_timeout(2500)
        html = page.content()
        self._write_debug_page("landing", html)
        self._trace(f"login:post_submit url={page.url}")

        portal_ready = self._resolve_post_login_interstitials(page)
        if not portal_ready:
            self._trace(f"login:error:post_login_portal_not_reached url={page.url}")
            raise ValueError("Academia sign-in did not reach the student portal. Another session or approval step is blocking access.")

        frame = self._get_login_frame(page)
        if frame is not None and self._is_still_on_login(frame):
            error_text = self._extract_visible_error(frame)
            captcha_image = self._capture_visible_captcha(frame)
            self._trace(f"login:still_on_login error={error_text!r} captcha_required={bool(captcha_image)}")
            if captcha_image:
                raise ValueError(error_text or "Academia rejected the login. Check email address, password, or CAPTCHA.")
            raise ValueError(error_text or "Academia rejected the login. Check email address and password.")

        dashboard = self._build_dashboard(context.browser_context, page, username)
        self._trace("login:success")
        session_id = secrets.token_hex(24)
        self.sessions[session_id] = LiveSession(
            session_id=session_id,
            browser_context=context.browser_context,
            page=page,
            dashboard=dashboard,
            created_at=time.time()
        )
        self.prelogins.pop(prelogin_id, None)
        return session_id, dashboard

    def get_session_dashboard(self, session_id: Optional[str]) -> Optional[dict]:
        if not session_id:
            return None
        session = self.sessions.get(session_id)
        return copy.deepcopy(session.dashboard) if session else None

    def logout(self, session_id: Optional[str]) -> None:
        if not session_id:
            return
        session = self.sessions.pop(session_id, None)
        if session:
            session.browser_context.close()

    def _run_login_flow(self, frame: Frame, username: str, password: str, captcha: str) -> None:
        self._trace("login_flow:fill_username")
        frame.locator("#login_id").fill(username)
        self._click_visible_primary(frame)
        self._trace("login_flow:clicked_primary_after_username")
        self._wait_for_login_state_change(frame, expect_password=True)

        error_text = self._extract_visible_error(frame)
        if error_text:
            raise ValueError(error_text)

        if not self._is_visible(frame, "#password"):
            raise ValueError("Academia did not open the password step. Check the email address format or try again.")

        self._trace("login_flow:fill_password")
        frame.locator("#password").fill(password)

        if self._is_visible(frame, "#captcha"):
            if not captcha:
                raise ValueError("Captcha is required for Academia login.")
            self._trace("login_flow:fill_captcha")
            frame.locator("#captcha").fill(captcha)

        self._click_visible_primary(frame)
        self._trace("login_flow:clicked_primary_after_password")
        self._wait_for_post_password_state(frame)

        error_text = self._extract_visible_error(frame)
        if error_text:
            raise ValueError(error_text)

        if self._is_visible(frame, "#otp") or self._is_visible(frame, "#mfa_otp") or self._is_visible(frame, "#mfa_totp"):
            raise ValueError("Academia requested OTP or MFA verification. That step is not automated yet.")

    def _wait_for_login_state_change(self, frame: Frame, expect_password: bool) -> None:
        deadline = time.time() + 15
        while time.time() < deadline:
            if self._extract_visible_error(frame):
                return
            if expect_password and self._is_visible(frame, "#password"):
                return
            if self._is_visible(frame, "#captcha"):
                return
            if self._is_visible(frame, "#otp") or self._is_visible(frame, "#mfa_otp") or self._is_visible(frame, "#mfa_totp"):
                self._trace("login_flow:otp_or_mfa_visible")
                return
            frame.page.wait_for_timeout(300)
        raise ValueError("Academia login did not advance to the next step in time.")

    def _wait_for_post_password_state(self, frame: Frame) -> None:
        deadline = time.time() + 20
        while time.time() < deadline:
            if self._extract_visible_error(frame):
                return
            if not self._is_still_on_login(frame):
                self._trace("login_flow:left_login_screen")
                return
            if self._is_visible(frame, "#otp") or self._is_visible(frame, "#mfa_otp") or self._is_visible(frame, "#mfa_totp"):
                self._trace("login_flow:otp_or_mfa_visible_after_password")
                return
            frame.page.wait_for_timeout(400)
        raise ValueError("Academia login is taking too long after password submission.")

    def _build_dashboard(self, browser_context: BrowserContext, page: Page, username: str) -> dict:
        pages = {}

        try:
            self._open_portal_view(page, ATTENDANCE_URL)
            pages["attendance"] = page.content()
            self._write_debug_page("attendance", pages["attendance"])
        except Exception:
            pages["attendance"] = page.content()

        try:
            self._open_portal_view(page, TIMETABLE_URL)
            pages["timetable"] = page.content()
            self._write_debug_page("timetable", pages["timetable"])
        except Exception:
            pages["timetable"] = page.content()

        try:
            self._open_portal_view(page, PROFILE_URL)
            pages["profile"] = page.content()
            self._write_debug_page("profile", pages["profile"])
        except Exception:
            pages["profile"] = page.content()

        try:
            self._open_portal_view(page, PLANNER_URL)
            pages["planner"] = page.content()
            self._write_debug_page("planner", pages["planner"])
        except Exception:
            pages["planner"] = page.content()

        pages["landing"] = page.content()
        self._write_debug_page("landing-post-login", pages["landing"])

        student = self._parse_student(pages, username)
        attendance = self._parse_attendance(pages)
        courses = self._parse_courses(pages)
        planner = self._parse_planner(pages)
        day_order = self._current_day_order(planner)
        day_order_timetables = self._build_all_day_order_timetables(courses, student.get("batch", ""))
        today_timetable = day_order_timetables.get(self._normalize_day_order(day_order), [])

        return {
            "student": student,
            "lastSynced": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "timetable": today_timetable or self._parse_timetable(pages, student.get("batch", ""), day_order, page),
            "attendance": attendance,
            "marks": self._parse_marks(pages),
            "courses": courses,
            "planner": planner,
            "currentDayOrder": day_order,
            "dayOrderTimetables": day_order_timetables,
            "cgpaCourses": []
        }

    def _get_login_frame(self, page: Page) -> Optional[Frame]:
        for frame in page.frames:
            if "accounts/p/" in frame.url and "signin" in frame.url:
                return frame
        return None

    def _is_still_on_login(self, frame: Frame) -> bool:
        return self._is_visible(frame, "#login_id") or self._is_visible(frame, "#password") or self._is_visible(frame, "#captcha")

    def _is_visible(self, frame: Frame, selector: str) -> bool:
        try:
            locator = frame.locator(selector)
            return locator.count() > 0 and locator.first.is_visible()
        except Exception:
            return False

    def _click_visible_primary(self, frame: Frame) -> None:
        for selector in ["#nextbtn", "button:has-text('Sign In')", "button:has-text('Next')", "button:has-text('Verify')"]:
            try:
                locator = frame.locator(selector)
                if locator.count() > 0 and locator.first.is_visible():
                    locator.first.click()
                    return
            except Exception:
                continue
        raise ValueError("Could not find the active Academia login button.")

    def _capture_visible_captcha(self, frame: Frame) -> str:
        for selector in ["img.captcha_img", "img[src*='captcha']", "img[alt*='CAPTCHA']", "img[alt*='Captcha']"]:
            try:
                locator = frame.locator(selector)
                if locator.count() > 0 and locator.first.is_visible():
                    return "data:image/png;base64," + base64.b64encode(locator.first.screenshot(type="png")).decode("ascii")
            except Exception:
                continue
        return ""

    def _extract_visible_error(self, frame: Frame) -> str:
        selectors = [
            "#error_space",
            ".fielderror",
            ".errorlabel",
            ".fielderror .error-msg",
            ".alert_message",
            ".form_error",
            ".service_error_msg"
        ]
        for selector in selectors:
            try:
                locator = frame.locator(selector)
                if locator.count() > 0 and locator.first.is_visible():
                    text = locator.first.inner_text().strip()
                    if text:
                        return text
            except Exception:
                continue

        try:
            body_text = frame.locator("body").inner_text()
        except Exception:
            return ""

        for pattern in [
            r"Incorrect CAPTCHA\. Please try again\.",
            r"Invalid email address or mobile number\.[^\n]*",
            r"Please enter your password",
            r"Please enter the CAPTCHA\.",
            r"Error occurred",
            r"Incorrect OTP\. Please try again\."
        ]:
            match = re.search(pattern, body_text, flags=re.IGNORECASE)
            if match:
                return match.group(0).strip()
        return ""

    def _parse_timetable(self, pages: dict[str, str], batch: str, current_day_order: str, page: Page) -> list[dict]:
        courses = self._parse_courses(pages)
        mapped = self._build_day_order_timetable(courses, batch, current_day_order)
        if mapped:
            return mapped

        unified = self._parse_unified_timetable(pages, current_day_order, page)
        if unified:
            return unified

        table = self._find_table(
            pages,
            "timetable",
            {
                "course code",
                "course title",
                "faculty name",
                "slot"
            }
        )
        if not table:
            return []

        headers = self._normalize_headers(table[0])
        rows = []
        for row in table[1:]:
            mapping = self._row_dict(headers, row)
            subject_code = mapping.get("course code", "").strip()
            subject_name = mapping.get("course title", "").strip()
            if not subject_code or not subject_name:
                continue
            rows.append(
                {
                    "day": "Slot",
                    "startTime": mapping.get("slot", "").strip(),
                    "endTime": "",
                    "subjectCode": subject_code,
                    "subjectName": subject_name,
                    "room": mapping.get("room no.", mapping.get("room no", "")).strip(),
                    "faculty": mapping.get("faculty name", "").strip()
                }
            )
        return rows

    def _build_day_order_timetable(self, courses: list[dict], batch: str, current_day_order: str) -> list[dict]:
        batch_key = self._normalize_batch(batch)
        day_key = self._normalize_day_order(current_day_order)
        if not batch_key or not day_key:
            return []

        slot_sequence = DAY_ORDER_SLOT_MAP.get(batch_key, {}).get(day_key)
        if not slot_sequence:
            return []

        course_by_slot = []
        for course in courses:
            slot_value = course.get("hoursPerWeek", "").strip()
            if not slot_value:
                continue
            course_by_slot.append((self._expand_slot_tokens(slot_value), course))

        rows = []
        for index, slot_code in enumerate(slot_sequence):
            matched_course = None
            for tokens, course in course_by_slot:
                if slot_code in tokens:
                    matched_course = course
                    break
            if matched_course is None:
                continue
            start_time, end_time = PERIOD_TIMES[index]
            rows.append(
                {
                    "day": f"Period {index + 1}",
                    "startTime": start_time,
                    "endTime": end_time,
                    "subjectCode": matched_course.get("subjectCode", ""),
                    "subjectName": matched_course.get("subjectName", ""),
                    "room": matched_course.get("room", ""),
                    "faculty": matched_course.get("faculty", ""),
                    "slotCode": slot_code,
                    "dayOrder": day_key
                }
            )
        return rows

    def _build_all_day_order_timetables(self, courses: list[dict], batch: str) -> dict[str, list[dict]]:
        return {
            day_order: self._build_day_order_timetable(courses, batch, day_order)
            for day_order in ["1", "2", "3", "4", "5"]
        }

    def _parse_attendance(self, pages: dict[str, str]) -> list[dict]:
        table = self._find_table(
            pages,
            "attendance",
            {
                "course code",
                "course title",
                "faculty name",
                "hours conducted",
                "hours absent",
                "attn %"
            }
        )
        if not table:
            return []

        headers = self._normalize_headers(table[0])
        rows = []
        for row in table[1:]:
            mapping = self._row_dict(headers, row)
            subject_code = mapping.get("course code", "").strip()
            subject_name = mapping.get("course title", "").strip()
            if not subject_code or not subject_name:
                continue
            conducted = self._extract_number(mapping.get("hours conducted", ""))
            absent = self._extract_number(mapping.get("hours absent", ""))
            rows.append(
                {
                    "subjectCode": subject_code,
                    "subjectName": subject_name,
                    "faculty": mapping.get("faculty name", "").strip(),
                    "attendedClasses": max(0, conducted - absent),
                    "conductedClasses": conducted
                }
            )
        return rows

    def _parse_marks(self, pages: dict[str, str]) -> list[dict]:
        table = self._find_table(
            pages,
            "attendance",
            {
                "course code",
                "course type",
                "test performance"
            }
        )
        if not table:
            return []

        headers = self._normalize_headers(table[0])
        rows = []
        for row in table[1:]:
            mapping = self._row_dict(headers, row)
            subject_code = mapping.get("course code", "").strip()
            course_type = mapping.get("course type", "").strip()
            performance = mapping.get("test performance", "").strip()
            if not subject_code:
                continue

            assessments = re.findall(r"([A-Z-]+I{0,2}|[A-Z]{2,}-I|[A-Z]{1,3}-II|[A-Z]{1,3}-I|FT-II|FT-I|FJ-I|LLJ-I|FL-I|FML-I)\/(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)", performance)
            if not assessments:
                generic = re.findall(r"([A-Za-z0-9-]+)\/(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)", performance)
                assessments = generic

            max_marks = 0.0
            obtained_marks = 0.0
            labels = []
            for label, maximum, score in assessments:
                max_marks += float(maximum)
                obtained_marks += float(score)
                labels.append(f"{label}: {score}/{maximum}")

            rows.append(
                {
                    "subjectCode": subject_code,
                    "subjectName": subject_code,
                    "courseType": course_type or "Not detected",
                    "assessments": ", ".join(labels) if labels else performance or "Not updated yet",
                    "total": f"{obtained_marks:.2f}/{max_marks:.2f}" if max_marks else "Not updated yet"
                }
            )
        return rows

    def _parse_courses(self, pages: dict[str, str]) -> list[dict]:
        table = self._find_table(
            pages,
            "timetable",
            {
                "course code",
                "course title",
                "credit",
                "faculty name",
                "slot"
            }
        )
        if not table:
            return []

        headers = self._normalize_headers(table[0])
        rows = []
        for row in table[1:]:
            mapping = self._row_dict(headers, row)
            subject_code = mapping.get("course code", "").strip()
            subject_name = mapping.get("course title", "").strip()
            if not subject_code or not subject_name:
                continue
            rows.append(
                {
                    "subjectCode": subject_code,
                    "subjectName": subject_name,
                    "faculty": mapping.get("faculty name", "").strip(),
                    "credits": mapping.get("credit", "").strip(),
                    "hoursPerWeek": mapping.get("slot", "").strip(),
                    "room": mapping.get("room no.", mapping.get("room no", "")).strip()
                }
            )
        return rows

    def _parse_student(self, pages: dict[str, str], username: str) -> dict:
        attendance_html = pages.get("attendance", "")
        timetable_html = pages.get("timetable", "")
        profile_html = pages.get("profile", "")

        profile_name = self._match_first(profile_html, [r"([A-Z0-9]+)\s*-\s*([A-Z][A-Z ]+)"])
        registration_name = re.search(
            r"Registration Number\s*:\s*</td>\s*<td><strong>(RA\d+)</strong>.*?Name\s*:\s*</td>\s*<td><strong>([^<]+)</strong>",
            timetable_html,
            flags=re.IGNORECASE | re.DOTALL
        )
        attendance_profile = {
            "registrationNumber": self._match_first(attendance_html, [r"Registration Number:\s*</td>\s*<td><strong>(RA\d+)</strong>"]),
            "name": self._match_first(attendance_html, [r"Name:\s*</td>\s*<td><strong>([^<]+)</strong>"]),
            "program": self._match_first(attendance_html, [r"Program:\s*</td>\s*<td><strong>([^<]+)</strong>"]),
            "department": self._match_first(attendance_html, [r"Department:\s*</td>\s*<td><strong>([^<]+)</strong>"]),
            "specialisation": self._match_first(attendance_html, [r"Specialization:\s*</td>\s*<td><strong>([^<]+)</strong>"]),
            "semester": self._match_first(attendance_html, [r"Semester:\s*</td>\s*<td><strong>([^<]+)</strong>"]),
            "batch": self._match_first(attendance_html, [r"Batch\s*</td>\s*<td>\s*:\s*</td>\s*<td><strong>([^<]+)</strong>"])
        }

        timetable_department = self._match_first(
            timetable_html,
            [r"Department:\s*</td>\s*<td><strong>(.*?)</strong>"],
        )
        section = self._match_first(timetable_department, [r"\(([^()]*Section)\)"])
        cleaned_department = re.sub(r"<[^>]+>", "", timetable_department).strip()
        cleaned_department = re.sub(r"\([^()]*Section\)", "", cleaned_department).strip()
        cleaned_department = re.sub(r"\([A-Z]+\)\s*-?$", "", cleaned_department).strip(" -")

        faculty_block = re.search(
            r"<strong>([^<]+)<br>\s*Faculty Advisor</strong><br><font color=\"blue\">([^<]+)</font><br><font color=\"green\">([^<]+)</font>",
            timetable_html,
            flags=re.IGNORECASE
        )

        registration_number = attendance_profile["registrationNumber"]
        name = attendance_profile["name"]
        if registration_name:
            registration_number = registration_number or registration_name.group(1).strip()
            name = name or registration_name.group(2).strip()
        elif not registration_number and profile_name:
            parts = re.match(r"([A-Z0-9]+)\s*-\s*(.+)", profile_name)
            if parts:
                registration_number = parts.group(1).strip()
                name = name or parts.group(2).strip()

        return {
            "name": name or username,
            "registrationNumber": registration_number or "Not detected",
            "specialisation": attendance_profile["specialisation"] or "Not detected",
            "department": attendance_profile["department"] or cleaned_department or "Not detected",
            "section": section or "Not detected",
            "facultyAdvisor": faculty_block.group(1).strip() if faculty_block else "Not detected",
            "email": username,
            "program": attendance_profile["program"] or "Not detected",
            "semester": attendance_profile["semester"] or "Not detected",
            "batch": attendance_profile["batch"] or "Not detected",
            "mobile": self._match_first(timetable_html, [r"Mobile:\s*</td>\s*<td><strong>([^<]+)</strong>"]) or "Not detected",
            "classRoom": self._match_first(timetable_html, [r"Class Room:\s*</td>\s*<td><strong>(.*?)</strong>"]) and re.sub(
                r"<[^>]+>",
                "",
                self._match_first(timetable_html, [r"Class Room:\s*</td>\s*<td><strong>(.*?)</strong>"])
            ).strip() or "Not detected",
            "facultyAdvisorEmail": faculty_block.group(2).strip() if faculty_block else "Not detected",
            "facultyAdvisorPhone": faculty_block.group(3).strip() if faculty_block else "Not detected"
        }

    def _parse_planner(self, pages: dict[str, str]) -> list[dict]:
        planner_html = pages.get("planner", "")
        planner_entries = []

        for table in self._extract_tables(planner_html):
            if not table:
                continue
            headers = set(self._normalize_headers(table[0]))
            if "date" not in headers:
                continue

            normalized_headers = self._normalize_headers(table[0])
            for row in table[1:]:
                mapping = self._row_dict(normalized_headers, row)
                date_value = mapping.get("date", "").strip()
                if not date_value:
                    continue
                planner_entries.append(
                    {
                        "date": date_value,
                        "dayOrder": mapping.get("day order", mapping.get("dayorder", "")).strip() or "Not detected",
                        "title": mapping.get("event", mapping.get("description", mapping.get("details", ""))).strip() or "Academic Planner",
                        "kind": mapping.get("type", mapping.get("category", "")).strip() or "Planner"
                    }
                )

        seen = set()
        unique_entries = []
        for entry in planner_entries:
            key = (entry["date"], entry["dayOrder"], entry["title"])
            if key in seen:
                continue
            seen.add(key)
            unique_entries.append(entry)
        return unique_entries or self._fallback_planner_entries()

    def _current_day_order(self, planner: list[dict]) -> str:
        if not planner:
            return ""

        today = date.today()
        for entry in planner:
            entry_date = self._parse_date_value(entry.get("date", ""))
            if entry_date == today:
                return entry.get("dayOrder", "")
        return planner[0].get("dayOrder", "")

    def _fallback_planner_entries(self) -> list[dict]:
        entries = []
        current = date(2026, 1, 1)
        end = date(2026, 6, 30)
        next_day_order = 1

        while current <= end:
            iso_value = current.isoformat()
            weekday_name = current.strftime("%a")
            event_type, event_title = PLANNER_EVENTS_2026.get(iso_value, ("Class", "Working Day"))

            if current.weekday() >= 5:
                day_order = "-"
                if event_title == "Working Day":
                    event_type = "Weekend"
                    event_title = "Weekend"
            elif current < date(2026, 1, 8) or current > date(2026, 5, 6):
                day_order = "-"
                if event_title == "Working Day":
                    event_type = "No Class"
                    event_title = "No Scheduled Classes"
            elif event_type == "Holiday" or iso_value == "2026-01-05":
                day_order = "-"
            else:
                day_order = str(next_day_order)
                next_day_order = 1 if next_day_order == 5 else next_day_order + 1

            entries.append(
                {
                    "date": iso_value,
                    "displayDate": current.strftime("%d %b %Y"),
                    "weekday": weekday_name,
                    "dayOrder": day_order,
                    "title": event_title,
                    "kind": event_type
                }
            )
            current += timedelta(days=1)

        return entries

    def _parse_unified_timetable(self, pages: dict[str, str], current_day_order: str, page: Page) -> list[dict]:
        if not current_day_order:
            return []

        planner_html = pages.get("planner", "") + pages.get("landing", "")
        batch_match = re.search(r"Unified_Time_Table_2025[_-]batch[_-](\d)", planner_html, flags=re.IGNORECASE)
        if not batch_match:
            return []

        batch = batch_match.group(1)
        unified_url = f"https://academia.srmist.edu.in/#Page:Unified_Time_Table_2025_batch_{batch}"
        try:
            self._open_portal_view(page, unified_url)
            html = page.content()
            self._write_debug_page(f"unified-timetable-batch-{batch}", html)
        except Exception:
            return []

        for table in self._extract_tables(html):
            if not table or len(table) < 2:
                continue
            headers = self._normalize_headers(table[0])
            if not headers:
                continue
            day_index = self._find_day_order_index(headers, current_day_order)
            if day_index is None:
                continue

            rows = []
            for row in table[1:]:
                if len(row) <= day_index:
                    continue
                period = row[0].strip() if row else ""
                cell = row[day_index].strip()
                if not period or not cell:
                    continue
                parts = [part.strip() for part in re.split(r"\s{2,}|\n|,", cell) if part.strip()]
                subject_name = parts[0] if parts else cell
                rows.append(
                    {
                        "day": current_day_order,
                        "startTime": period,
                        "endTime": "",
                        "subjectCode": self._match_first(subject_name, [r"([A-Z0-9]{6,})"]) or subject_name,
                        "subjectName": subject_name,
                        "room": parts[1] if len(parts) > 1 else "",
                        "faculty": parts[2] if len(parts) > 2 else ""
                    }
                )
            if rows:
                return rows
        return []

    def _find_day_order_index(self, headers: list[str], current_day_order: str) -> Optional[int]:
        normalized_target = re.sub(r"[^a-z0-9]", "", current_day_order.lower())
        if not normalized_target:
            return None
        for index, header in enumerate(headers):
            normalized_header = re.sub(r"[^a-z0-9]", "", header)
            if normalized_target in normalized_header or normalized_header in normalized_target:
                return index
        return None

    def _normalize_batch(self, value: str) -> str:
        match = re.search(r"(\d+)", value or "")
        if not match:
            return ""
        return match.group(1)

    def _normalize_day_order(self, value: str) -> str:
        match = re.search(r"(\d+)", value or "")
        if not match:
            return ""
        return match.group(1)

    def _parse_date_value(self, value: str) -> Optional[date]:
        raw = (value or "").strip()
        if not raw:
            return None

        for fmt in ("%Y-%m-%d", "%d %b %Y", "%d-%b-%Y", "%d-%b-%y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None

    def _expand_slot_tokens(self, slot_value: str) -> set[str]:
        tokens = set()
        for raw_part in re.split(r"[-,\s]+", slot_value):
            part = raw_part.strip().upper()
            if not part:
                continue
            part = part.replace("/X", "")
            part = part.replace("/", "")
            if part:
                tokens.add(part)
        return tokens

    def _extract_tables(self, html: str) -> list[list[list[str]]]:
        parser = TableParser()
        parser.feed(html)
        return parser.tables

    def _extract_all_tables(self, pages: dict[str, str], preferred_label: str) -> list[list[list[str]]]:
        ordered = [preferred_label] + [label for label in pages if label != preferred_label]
        tables = []
        for label in ordered:
            html = pages.get(label, "")
            if html:
                tables.extend(self._extract_tables(html))
        return tables

    def _find_table(self, pages: dict[str, str], preferred_label: str, required_headers: set[str]) -> list[list[str]]:
        for table in self._extract_all_tables(pages, preferred_label):
            if not table:
                continue
            headers = set(self._normalize_headers(table[0]))
            if required_headers.issubset(headers):
                return table
        return []

    def _normalize_headers(self, headers: list[str]) -> list[str]:
        normalized = []
        for header in headers:
            lowered = header.lower().strip()
            lowered = re.sub(r"\s+", " ", lowered)
            normalized.append(lowered)
        return normalized

    def _row_dict(self, headers: list[str], row: list[str]) -> dict:
        return {headers[index]: row[index] if index < len(row) else "" for index in range(len(headers))}

    def _split_time_range(self, value: str) -> tuple[str, str]:
        if "-" in value:
            parts = [part.strip() for part in value.split("-", 1)]
            return parts[0], parts[1]
        return value, ""

    def _extract_number(self, value: str) -> int:
        match = re.search(r"(\d+)", value or "")
        return int(match.group(1)) if match else 0

    def _combined_text(self, pages: dict[str, str]) -> str:
        return "\n".join(value for value in pages.values() if value)

    def _resolve_post_login_interstitials(self, page: Page) -> bool:
        deadline = time.time() + 20
        while time.time() < deadline:
            current_url = page.url
            lowered = current_url.lower()
            if "academia-academic-services" in lowered and "/portal/" in lowered:
                self._trace(f"login:portal_ready url={current_url}")
                page.wait_for_timeout(3000)
                return True

            if lowered.rstrip("/") == "https://academia.srmist.edu.in":
                self._trace(f"login:portal_root_after_handoff url={current_url}")
                page.wait_for_timeout(2000)
                return True

            if "block-sessions" in lowered:
                self._trace("login:handling_block_sessions")
                self._handle_block_sessions(page)
                continue

            if "sessions-reminder" in lowered:
                self._trace("login:handling_sessions_reminder")
                self._handle_sessions_reminder(page)
                continue

            page.wait_for_timeout(500)
        return False

    def _handle_block_sessions(self, page: Page) -> None:
        page.wait_for_load_state("domcontentloaded")
        page.locator("#continue_button").click()
        page.wait_for_timeout(500)
        if page.locator(".confirm-delete_btn").count() > 0:
            page.locator(".confirm-delete_btn").click()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)

    def _handle_sessions_reminder(self, page: Page) -> None:
        page.wait_for_load_state("domcontentloaded")
        if page.locator("#continue_button").count() > 0:
            page.locator("#continue_button").click()
            page.wait_for_timeout(500)
            if page.locator(".confirm-delete_btn").count() > 0:
                page.locator(".confirm-delete_btn").click()
        elif page.locator("a.remind_me_later").count() > 0:
            page.locator("a.remind_me_later").click()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)

    def _open_portal_view(self, page: Page, url: str) -> None:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeoutError:
            pass

    def _match_first(self, text: str, patterns: list[str]) -> str:
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return (match.group(1) if match.lastindex else match.group(0)).strip()
        return ""

    def _write_debug_page(self, label: str, html: str) -> None:
        timestamp = int(time.time())
        (DEBUG_DIR / f"{timestamp}-{label}.html").write_text(html, encoding="utf-8")

    def _trace(self, message: str) -> None:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with TRACE_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
