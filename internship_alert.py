"""Push a phone alert (via ntfy) when a new Data Science / AI / ML internship
appears in SimplifyJobs/Summer2027-Internships.

Runs in two places that share de-duplication:
  * GitHub Actions (cloud mode, default): state in seen_ids.json, committed by the workflow.
  * This PC (--local): state in local_seen_ids.json, config in config.json.

A role counts as already alerted if its id is in our own seen file, in the
cloud's seen file (local mode only), or tagged on an ntfy message from the
last 12 hours (whichever runner sent it).

Standard library only.
"""

import argparse
import gzip
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from logging.handlers import RotatingFileHandler
from pathlib import Path

LISTINGS_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/"
    "dev/.github/scripts/listings.json"
)
SECTION_URL = (
    "https://github.com/SimplifyJobs/Summer2027-Internships/blob/dev/README.md"
    "#-data-science-ai--machine-learning-internship-roles"
)
NTFY_SERVER = "https://ntfy.sh"

# Which listings count. Widen TERMS (e.g. add "Fall 2027") to get more alerts.
CATEGORIES = {"AI/ML/Data", "Data Science, AI & Machine Learning"}
TERMS = {"Summer 2027"}

MAX_INDIVIDUAL_PUSHES = 10
USER_AGENT = "ml-internship-alerts/1.0"

HERE = Path(__file__).resolve().parent
log = logging.getLogger("alert")


def http_get(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return body, resp.headers


def fetch_listings(etag):
    """Return (listings, new_etag), or (None, etag) if unchanged since last fetch."""
    headers = {"Accept-Encoding": "gzip"}
    if etag:
        headers["If-None-Match"] = etag
    try:
        body, resp_headers = http_get(LISTINGS_URL, headers)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, etag
        raise
    return json.loads(body), resp_headers.get("ETag")


def matches(listing):
    return (
        listing.get("category") in CATEGORIES
        and listing.get("is_visible", True)
        and listing.get("active", False)
        and any(t in TERMS for t in listing.get("terms", []))
    )


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_atomic(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def remote_seen_ids(url):
    try:
        body, _ = http_get(url, timeout=20)
        return set(json.loads(body))
    except Exception as e:  # cloud file missing/unreachable shouldn't block alerts
        log.warning("could not read cloud seen file: %s", e)
        return set()


def ntfy_recent_ids(topic):
    """Ids tagged on messages this topic received in the last 12h."""
    try:
        body, _ = http_get(f"{NTFY_SERVER}/{topic}/json?poll=1&since=12h", timeout=20)
    except Exception as e:
        log.warning("could not read ntfy history: %s", e)
        return set()
    ids = set()
    for line in body.decode("utf-8").splitlines():
        if line.strip():
            ids.update(json.loads(line).get("tags", []))
    return ids


def ntfy_publish(topic, title, message, click, tags, priority=4):
    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "click": click,
        "tags": tags,
        "priority": priority,
    }
    req = urllib.request.Request(
        NTFY_SERVER,
        data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def describe(listing):
    locations = ", ".join(listing.get("locations") or []) or "Location N/A"
    return f"{listing.get('title', 'Internship')} · {locations}"


def notify(topic, new):
    """Send pushes for new listings; return the ids that were delivered."""
    if len(new) > MAX_INDIVIDUAL_PUSHES:
        lines = [f"• {x.get('company_name', '?')}: {x.get('title', '')}" for x in new[:15]]
        if len(new) > 15:
            lines.append(f"…and {len(new) - 15} more")
        ntfy_publish(
            topic,
            f"🤖 {len(new)} new DS/AI/ML internships",
            "\n".join(lines),
            SECTION_URL,
            tags=["summary"],
        )
        return [x["id"] for x in new]

    sent = []
    for x in new:
        try:
            ntfy_publish(
                topic,
                f"🤖 {x.get('company_name', 'New role')}",
                describe(x),
                x.get("url") or SECTION_URL,
                tags=[x["id"]],
            )
            sent.append(x["id"])
        except Exception as e:  # not marked seen, so it retries next run
            log.error("push failed for %s: %s", x["id"], e)
    return sent


def setup_logging(local):
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    if local:  # pythonw has no console, so log to a small file
        handler = RotatingFileHandler(HERE / "alert.log", maxBytes=500_000, backupCount=1, encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)
    log.addHandler(handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--local", action="store_true", help="run as the PC runner (config.json, local_* state)")
    parser.add_argument("--force", action="store_true", help="ignore the cached ETag and re-check everything")
    parser.add_argument("--no-remote-dedupe", action="store_true", help="only use our own seen file (testing)")
    args = parser.parse_args()
    setup_logging(args.local)

    if args.local:
        config = load_json(HERE / "config.json", {})
        topic = config.get("ntfy_topic")
        cloud_seen_url = config.get("cloud_seen_url")
        seen_path, etag_path = HERE / "local_seen_ids.json", HERE / "local_etag.txt"
    else:
        topic = os.environ.get("NTFY_TOPIC")
        cloud_seen_url = None
        seen_path, etag_path = HERE / "seen_ids.json", None  # cloud always downloads fresh
    if not topic:
        log.error("no ntfy topic configured")
        return 1

    etag = None
    if etag_path and not args.force and etag_path.exists():
        etag = etag_path.read_text(encoding="utf-8").strip() or None

    listings, new_etag = fetch_listings(etag)
    if listings is None:
        return 0  # unchanged since last check

    current = [x for x in listings if matches(x)]
    first_run = not seen_path.exists()
    seen = set(load_json(seen_path, []))

    all_sent = True
    if first_run:
        seen = {x["id"] for x in current}
        log.info("first run: baselined %d roles, no alerts sent", len(seen))
    else:
        new = [x for x in current if x["id"] not in seen]
        if new and not args.no_remote_dedupe:
            already = ntfy_recent_ids(topic)
            if cloud_seen_url:
                already |= remote_seen_ids(cloud_seen_url)
            seen |= {x["id"] for x in new if x["id"] in already}
            new = [x for x in new if x["id"] not in already]
        if new:
            sent = notify(topic, new)
            seen.update(sent)
            all_sent = len(sent) == len(new)
            log.info("alerted %d new role(s): %s", len(sent), ", ".join(
                f"{x.get('company_name')} - {x.get('title')}" for x in new if x["id"] in sent))

    write_atomic(seen_path, json.dumps(sorted(seen), indent=0))
    if etag_path and new_etag and all_sent:
        write_atomic(etag_path, new_etag)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log.exception("run failed")
        sys.exit(1)
