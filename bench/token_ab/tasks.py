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

FIX = ("A hidden acceptance check of this task failed:\n\n{why}\n\nFix it. Make sure it works before you stop.")
