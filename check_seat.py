"""
Checks Lanier Tech's public class search and sends phone notifications
through ntfy when:
  1) a seat (or waitlist) opens in the section you want (SEAT_CRN), or
  2) the instructor changes on the section you're registered in (INSTRUCTOR_CRN).

You only need to change the values under SETTINGS to watch different classes.
"""

import http.cookiejar
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request

# ---------- SETTINGS ----------
TERM = "202714"          # Spring Semester 2027
SUBJECT = "BIOL"
COURSE_NUMBER = "2113"
SEAT_CRN = "41122"       # Stancea's MW 11:00 section: alert when a seat opens
INSTRUCTOR_CRN = "41371" # your MW 2:00 section: alert when the instructor changes
# ------------------------------

BASE = "https://bannerss.laniertech.edu/StudentRegistrationSsb/ssb"
REGISTER_LINK = BASE + "/registration/registration"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instructor_state.json")

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
TEST_ALERT = os.environ.get("TEST_ALERT", "").lower() == "true"


def notify(title, message, priority="high", tags="tada"):
    if not NTFY_TOPIC:
        print("NTFY_TOPIC is missing, so no notification was sent.")
        sys.exit(1)
    req = urllib.request.Request(
        "https://ntfy.sh/" + NTFY_TOPIC,
        data=message.encode("utf-8"),
        headers={
            "Title": title.encode("utf-8").decode("latin-1", "ignore"),
            "Priority": priority,
            "Tags": tags,
            "Click": REGISTER_LINK,
        },
    )
    urllib.request.urlopen(req, timeout=30)
    print("Notification sent:", title, "-", message)


def lanier_ssl_context():
    """Lanier's server leaves out part of its certificate chain. Browsers fill
    the gap automatically; this does the same for Python using the 'aia' library."""
    try:
        import aia
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "aia"], check=True)
        import aia
    try:
        return aia.AIASession().ssl_context_from_url(BASE + "/registration/registration")
    except Exception as err:
        # Seat counts are public and no login is sent, so as a last resort,
        # read them without certificate checking rather than miss a seat.
        print("Certificate fill-in failed (" + str(err)[:100] + "); continuing without it.")
        return ssl._create_unverified_context()


def get_sections():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=lanier_ssl_context()),
    )
    opener.addheaders = [("User-Agent", "Mozilla/5.0 (personal class seat check)")]

    def fetch(path, form=None):
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        with opener.open(BASE + path, data=data, timeout=30) as resp:
            return resp.read().decode("utf-8")

    fetch("/registration/registration")  # starts a browsing session
    session_id = "gh" + str(int(time.time() * 1000))
    fetch("/term/search?mode=search", {
        "term": TERM, "studyPath": "", "studyPathText": "",
        "startDatepicker": "", "endDatepicker": "", "uniqueSessionId": session_id,
    })
    query = urllib.parse.urlencode({
        "txt_subject": SUBJECT, "txt_courseNumber": COURSE_NUMBER, "txt_term": TERM,
        "pageOffset": 0, "pageMaxSize": 50, "sortColumn": "subjectDescription",
        "sortDirection": "asc", "uniqueSessionId": session_id,
    })
    results = json.loads(fetch("/searchResults/searchResults?" + query))
    return {s.get("courseReferenceNumber"): s for s in (results.get("data") or [])}


def instructor_of(section):
    names = [f.get("displayName", "").strip() for f in (section.get("faculty") or [])]
    names = [n for n in names if n and not n.upper().startswith("TBA")]
    return "; ".join(names) if names else "TBA"


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def check_seat(sections):
    """Returns a one-line status for the seat-watch section."""
    section = sections.get(SEAT_CRN)
    if section is None:
        print("CRN", SEAT_CRN, "was not found in the search results.")
        return f"CRN {SEAT_CRN} not found"

    seats = section.get("seatsAvailable") or 0
    enrolled = section.get("enrollment")
    maximum = section.get("maximumEnrollment")
    wait_cap = section.get("waitCapacity") or 0
    wait_open = section.get("waitAvailable") or 0
    print(f"CRN {SEAT_CRN}: {enrolled}/{maximum} enrolled, {seats} seat(s) open, "
          f"waitlist {wait_open}/{wait_cap} open")

    if not TEST_ALERT:
        if seats > 0:
            notify(f"Seat open in {SUBJECT} {COURSE_NUMBER}!",
                   f"CRN {SEAT_CRN}: {seats} seat(s) available ({enrolled}/{maximum}). "
                   "Register now in BannerWeb!")
        elif wait_cap > 0 and wait_open > 0:
            notify(f"Waitlist open for {SUBJECT} {COURSE_NUMBER}",
                   f"CRN {SEAT_CRN} now has a waitlist. Join it in BannerWeb!",
                   tags="clipboard")
    return f"CRN {SEAT_CRN} is {enrolled}/{maximum} enrolled"


def check_instructor(sections):
    """Returns a one-line status for the instructor-watch section."""
    section = sections.get(INSTRUCTOR_CRN)
    if section is None:
        print("CRN", INSTRUCTOR_CRN, "was not found in the search results.")
        return f"CRN {INSTRUCTOR_CRN} not found"

    current = instructor_of(section)
    state = load_state()
    previous = state.get(INSTRUCTOR_CRN)
    print(f"CRN {INSTRUCTOR_CRN}: instructor {current}")

    if previous is not None and current != previous and not TEST_ALERT:
        extra = " It's Stancea!" if "stancea" in current.lower() else ""
        notify(f"Instructor update: {SUBJECT} {COURSE_NUMBER}",
               f"Your section CRN {INSTRUCTOR_CRN} changed from {previous} to {current}.{extra}",
               tags="mortar_board")

    state[INSTRUCTOR_CRN] = current
    save_state(state)
    return f"CRN {INSTRUCTOR_CRN} instructor is {current}"


def main():
    try:
        sections = get_sections()
    except Exception as err:
        print("Could not read Lanier's class search:", err)
        if TEST_ALERT:
            notify("Seat watch test FAILED",
                   "Couldn't reach Lanier's class search: " + str(err)[:150],
                   priority="default", tags="warning")
        sys.exit(1 if TEST_ALERT else 0)  # stay quiet on scheduled runs

    seat_status = check_seat(sections)
    instructor_status = check_instructor(sections)

    if TEST_ALERT:
        notify("Seat watch test worked",
               f"Reached Lanier. {seat_status}. {instructor_status}. "
               "You'll be alerted when a seat opens or the instructor changes.",
               priority="default", tags="white_check_mark")


if __name__ == "__main__":
    main()
