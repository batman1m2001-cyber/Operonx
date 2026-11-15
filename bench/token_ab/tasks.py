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

# Round 3 (COST_PLAN): the project grows. Eight tasks run after T9 in the same workspace
# (``run.py --tasks T10,...,T17 --ws TRACK=DIR``), each building on what is there.
GROWTH = [
    ("T10", "Add support agents. `POST /agents` with `{\"name\", \"categories\"}` (a list of ticket categories) "
            "answers 201 with `{\"id\", \"name\", \"categories\"}`; `GET /agents` lists them. Tickets get two "
            "fields: `status` (`open` when created) and `agent` (an agent's id, or null). A new ticket is assigned "
            "to an agent whose categories include the ticket's category: the one with the fewest open tickets, a "
            "tie going to the agent created first; null when no agent covers it. Add `GET /tickets/{id}` (404 for "
            "an unknown id) and `POST /tickets/{id}/close`, which sets `status` to `closed` and answers the "
            "ticket. Make sure it works before you stop."),
    ("T11", "Add reply drafts. `POST /tickets/{id}/draft` asks the model to draft a reply to the customer (the "
            "prompt asks for a draft reply and says the word \"draft\", not \"summary\"; the ticket's text is in the messages) "
            "and answers `{\"ticket\", \"draft\"}` with the ticket's id and the model's text. The latest draft "
            "is kept and `GET /tickets/{id}` shows it as `draft`. `GET /tickets/{id}/draft/stream` does the same "
            "with `\"stream\": true` and streams it as Server-Sent Events the way `/summaries/{date}/stream` "
            "does (one `data:` event per piece, then `event: done`), keeping the whole text as the draft. Both "
            "answer 404 for an unknown ticket. Make sure it works before you stop."),
    ("T12", "Add urgency. When a ticket is created, a second model call rates its urgency: the prompt says the "
            "word \"urgency\", the ticket's text is the last user message, and the model answers `high` or "
            "`low`. A ticket gets `urgency`. `GET /tickets` takes optional query filters `category`, `status`, "
            "`urgency` and `agent`, in any combination, each an exact match, still newest first. Make sure it "
            "works before you stop."),
    ("T13", "Add a job `escalate`: every open ticket older than `$SLA_MINUTES` minutes (default 240) that is not "
            "escalated yet gets an escalation note from the model (the prompt asks for an escalation note and "
            "says the word \"escalation\", not \"summary\"; the ticket's text is in the messages), and the "
            "ticket gets `escalated: true`. `GET /escalations` answers `[{\"ticket\", \"note\", \"at\"}]` "
            "newest first. A ticket is escalated at most once; when the model is down part-way, the next run "
            "escalates the rest. Make sure it works before you stop."),
    ("T14", "Add a webhook. When `$WEBHOOK_URL` is set, every ticket created or closed is sent there: a `POST` "
            "with JSON `{\"event\": \"created\" or \"closed\", \"ticket\": {...}}`. An answer of 5xx, or "
            "none, is retried, 3 tries in all. The webhook failing never fails the request, nor slows it by more "
            "than a second. Make sure it works before you stop."),
    ("T15", "Add search. `GET /tickets/search?q=TEXT` answers the tickets whose text contains TEXT, ignoring "
            "case, newest first (`[]` when none does). Add a page `GET /search` with a search box that lists the "
            "matching tickets, linked from `GET /`. Make sure it works before you stop."),
    ("T16", "Two additions. (1) `GET /tickets.csv`: a CSV download (`text/csv`) with the header "
            "`id,created,category,urgency,status,agent,text` and one row per ticket, newest first, quoted where "
            "CSV needs it. (2) A job `agent_digest`: for every agent with open tickets, the model summarises "
            "that agent's open tickets and the digest is kept; `GET /agents/{id}/digest` answers "
            "`{\"agent\", \"summary\", \"count\"}`, 404 when there is none. Make sure it works before you stop."),
    ("T17", "Keep a history of every ticket. Each change is recorded as `{\"event\", \"at\"}`, `event` being one "
            "of `created`, `assigned` (only when an agent was assigned), `drafted`, `escalated`, `closed`. "
            "`GET /tickets/{id}/history` answers the list oldest first, 404 for an unknown ticket. Make sure it "
            "works before you stop."),
]

FIX = ("A hidden acceptance check of this task failed:\n\n{why}\n\nFix it. Make sure it works before you stop.")
