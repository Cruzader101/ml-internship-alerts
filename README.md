# ml-internship-alerts

Sends a phone push (via [ntfy](https://ntfy.sh)) when a new role shows up in the
[Data Science, AI & Machine Learning section](https://github.com/SimplifyJobs/Summer2027-Internships/blob/dev/README.md#-data-science-ai--machine-learning-internship-roles)
of SimplifyJobs/Summer2027-Internships.

It reads `.github/scripts/listings.json` from that repo and alerts on listings that are
active and visible, with category `AI/ML/Data` and term `Summer 2027`. You can edit
`CATEGORIES` / `TERMS` in `internship_alert.py` to change that.

## Where it runs

| Runner | How often | State |
| --- | --- | --- |
| GitHub Actions (`.github/workflows/check.yml`) | about every 5 min | `seen_ids.json` (committed) |
| Windows Task Scheduler, `pythonw internship_alert.py --local` | every 1 min while logged on | `local_seen_ids.json`, `local_etag.txt` |

The two runners share de-duplication through the cloud seen file and the ntfy message
history, so you get one push per role.

## Setup

1. Install the ntfy app (iOS/Android) and subscribe to your topic. The topic name is in
   `config.json` locally and in the repo secret `NTFY_TOPIC`. Anyone who knows it can read the alerts, so keep it private.
2. Cloud: `gh secret set NTFY_TOPIC`, then `gh workflow run check.yml`.
3. Local: copy `config.json` with `ntfy_topic` and `cloud_seen_url`, then register the scheduled task.

The first run in each place records the current roles without alerting. After that,
only new roles send a push. Local logs go to `alert.log`.
