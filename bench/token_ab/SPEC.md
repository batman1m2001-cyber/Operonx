# Support-ticket triager

A small service for a support team. Build it in this folder.

## How it runs

- `./run.sh` starts the HTTP server on the port in `$PORT` and keeps running until it is stopped.
- `./job.sh NAME` runs the job `NAME` once and exits 0 when it succeeded.
- Everything the service keeps (tickets, summaries, reports) lives under the folder in `$DATA_DIR`
  (create it when missing). Nothing else is persistent: a fresh `$DATA_DIR` is an empty service.
- `./run.sh` and `./job.sh` set up whatever they need (a virtualenv, dependencies) or expect it set up
  already; they must work from a fresh shell in this folder.

## The model

The service calls one language model through an OpenAI-compatible chat-completions API:
`POST $LLM_BASE_URL/chat/completions` with `{"model": $LLM_MODEL, "messages": [...]}` and the header
`Authorization: Bearer $LLM_API_KEY`. The answer is the first choice's message content, plain text.
While you work, `$LLM_BASE_URL` points at a test model you may call; it is not a smart model, so do
not judge your prompts by its answers.

- To classify a ticket, the ticket's text is the **last user message** of the call, and the model
  answers one category.
- To summarise, the prompt says it asks for a summary ("summarise" / "summary"), and the tickets
  are in the messages.

## Tickets

- A ticket is `{"id", "text", "category", "created"}`; `created` is an ISO-8601 timestamp (UTC).
- The categories are exactly `billing`, `account`, `technical`, `other`.
