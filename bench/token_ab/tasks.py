"""The seven tasks of the token A/B (ENGINEERING_ENV_PLAN E7), word for word the same in both arms.

Each task is one fresh session; the arm is the only difference. The hidden check for each is in
accept.py, under the same name.
"""

TASKS = [
    ("T1", "Read SPEC.md. Build the first version of the service:\n"
           "- `POST /tickets` with `{\"text\": ...}` classifies the ticket with the model, stores it, and "
           "answers 201 with the ticket.\n"
           "- `GET /tickets` answers every ticket, newest first.\n"
           "- `GET /` is a page that lists the tickets with their category.\n"
           "Write `./run.sh` and `./job.sh` as SPEC.md says (no jobs yet). Make sure it works before you stop."),
    ("T2", "Add a job `daily_summary`: it summarises today's tickets (UTC day) with the model and keeps the "
           "summary. Add `GET /summaries/{date}` (date is YYYY-MM-DD) answering `{\"date\", \"summary\"}`, "
           "and 404 for a day with no summary. Make sure it works before you stop."),
    ("T3", "Add a job `category_report`: for every category that has tickets, it summarises that category's "
           "tickets with the model and keeps the report. Add `GET /reports/{category}` answering "
           "`{\"category\", \"summary\", \"count\"}`, and 404 for a category with no report. Make sure it "
           "works before you stop."),
    ("T4", "Change `daily_summary`: for a day with no tickets it keeps the summary `No tickets.` without "
           "calling the model. `GET /summaries/{date}` also answers `count`, the number of tickets the "
           "summary covers. Make sure it works before you stop."),
    ("T5a", "Bug report from the support team: when the model is briefly overloaded and answers HTTP 503 "
            "once, `POST /tickets` answers 500 and the ticket is lost. One 503 must not fail the request; "
            "fix it. Make sure it works before you stop."),
    ("T5b", "Bug report from the support team: when the model answers HTTP 503 once, `./job.sh "
            "category_report` fails and no report is kept. Fix it, and the same problem anywhere else the "
            "service calls the model. Make sure it works before you stop."),
    ("T6", "Add a page `GET /stats` with a table: one row per category, with the number of tickets. Link "
           "it from `GET /`. Make sure it works before you stop."),
]

# Gate 2 (COST_PLAN E7b): three tasks where reusing the framework should win, run after T6 in the
# same workspace (``run.py --tasks T7,T8,T9 --ws TRACK=DIR,...``).
E7B = [
    ("T7", "Support says `POST /tickets` is sometimes slow, and nobody can tell whether the model or the "
           "service is to blame. Add `GET /timings`: the last 20 `POST /tickets` requests, newest first, each "
           "`{\"id\", \"total_ms\", \"model_ms\"}`, where `id` is the ticket's id, `total_ms` the whole "
           "request and `model_ms` the time spent waiting on the model. Make sure it works before you stop."),
    ("T8", "Add `GET /summaries/{date}/stream`: it asks the model for a fresh summary of that day's tickets "
           "with `\"stream\": true` and passes the text on as the model generates it, as Server-Sent Events: "
           "one `data:` event per piece of text the model sends (the event's data is that text, as is), then "
           "one `event: done`. Make sure it works before you stop."),
    ("T9", "Two changes to the jobs. (1) While `./run.sh` runs, it runs `daily_summary` by itself every "
           "`$SUMMARY_EVERY` seconds (default 3600). (2) `./job.sh category_report` resumes: when a run stops "
           "part-way because the model is down, the next run calls the model only for the categories that "
           "have no report yet or have tickets newer than their report. Make sure it works before you stop."),
]

FIX = ("A hidden acceptance check of this task failed:\n\n{why}\n\nFix it. Make sure it works before you stop.")
