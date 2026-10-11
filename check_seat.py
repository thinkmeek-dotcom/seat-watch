"""
Checks Lanier Tech's public class search for an open seat in one section
and sends a phone notification through ntfy when a seat (or waitlist) opens.

You only need to change the four values under SETTINGS if you want to watch
a different class.
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
TERM = "202714"        # Spring Semester 2027
SUBJECT = "BIOL"
COURSE_NUMBER = "2113"
CRN = "41122"
# ------------------------------

BASE = "https://bannerss.laniertech.edu/StudentRegistrationSsb/ssb"
REGISTER_LINK = BASE + "/registration/registration"

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
TEST_ALERT = os.environ.get("TEST_ALERT", "").lower() == "true"


def notify(title, message, priority="high", tags="tada"):
    if not NTFY_TOPIC:
        print("NTFY_TOPIC secret is missing, so no notification was sent.")
        sys.exit(1)
    req = urllib.request.Request(
        "https://ntfy.sh/" + NTFY_TOPIC,
        data=message.encode("utf-8"),
        headers={
            "Title": title,
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


def get_section():
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
    for section in results.get("data") or []:
        if section.get("courseReferenceNumber") == CRN:
            return section
    return None


def main():
    try:
        section = get_section()
    except Exception as err:
        print("Could not read Lanier's class search:", err)
        if TEST_ALERT:
            notify("Seat watch test FAILED",
                   "GitHub couldn't reach Lanier's class search: " + str(err)[:150],
                   priority="default", tags="warning")
        sys.exit(1 if TEST_ALERT else 0)  # stay quiet on scheduled runs

    if section is None:
        print("CRN", CRN, "was not found in the search results.")
        if TEST_ALERT:
            notify("Seat watch test FAILED", "CRN " + CRN + " wasn't found.",
                   priority="default", tags="warning")
        return

    seats = section.get("seatsAvailable") or 0
    enrolled = section.get("enrollment")
    maximum = section.get("maximumEnrollment")
    wait_cap = section.get("waitCapacity") or 0
    wait_open = section.get("waitAvailable") or 0
    print(f"CRN {CRN}: {enrolled}/{maximum} enrolled, {seats} seat(s) open, "
          f"waitlist {wait_open}/{wait_cap} open")

    if TEST_ALERT:
        notify("Seat watch test worked",
               f"GitHub reached Lanier. {SUBJECT} {COURSE_NUMBER} (CRN {CRN}) is "
               f"{enrolled}/{maximum} enrolled. You'll get an alert like this when a seat opens.",
               priority="default", tags="white_check_mark")
    elif seats > 0:
        notify(f"Seat open in {SUBJECT} {COURSE_NUMBER}!",
               f"CRN {CRN}: {seats} seat(s) available ({enrolled}/{maximum}). "
               "Register now in BannerWeb!")
    elif wait_cap > 0 and wait_open > 0:
        notify(f"Waitlist open for {SUBJECT} {COURSE_NUMBER}",
               f"CRN {CRN} now has a waitlist. Join it in BannerWeb!",
               tags="clipboard")


if __name__ == "__main__":
    main()
