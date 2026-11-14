# Board

One card per task (LEAN_PROJECT_PLAN §5). The assistant moves the cards; edit a card's fields, not its state.

## T · Cost: an operonx project costs less than an empty folder · open
plan: docs/COST_PLAN.md · opened: 2026-11-15

### C33 · K5+K1 Recipes in AGENTS.md, BRD opt-in, then a pilot · done
files: operonx/cli/templates/_common/AGENTS.md.tmpl, operonx/cli/init.py, docs/cost/K5_K1.md
done: tests/internal/cli/test_init.py::TestForCodingAssistants::test_every_guide_reference_in_agents_md_names_a_page_and_a_heading

### C34 · K3 operonx check: one verify call · done
files: operonx/cli/check.py, operonx/app/serve/app.py, operonx/app/serve/runner.py, operonx/cli/templates/_common/AGENTS.md.tmpl, docs/cost/K3.md
done: tests/internal/cli/test_check_cli.py, tests/internal/app/serve/test_path_and_status.py

### C35 · K2 operonx add: reuse as one command · done
files: operonx/cli/add.py,operonx/cli/templates/_common/AGENTS.md.tmpl,docs/cost/K2.md
done: tests/internal/cli/test_add_cli.py

### C36 · K4 One file per feature · done
files: operonx/cli/templates/hello/src/greeting.py.tmpl, operonx/cli/templates/chat/src/chat.py.tmpl, operonx/cli/templates/agent/src/assistant.py.tmpl, operonx/cli/add.py, operonx/lint/shape.py, operonx/app/serve/app.py, docs/cost/K4.md
done: tests/internal/cli/test_init.py, tests/internal/cli/test_add_cli.py, tests/internal/app/serve/test_path_and_status.py

### C37 · E7b Tasks where reuse should win (gate 2) · todo

## Backlog

### C38 · Pooled listeners break on uvicorn 0.54 · todo
files: operonx/app/serve/balance.py
done: tests/internal/app/test_declare.py::test_serve_runs_a_pooled_listener_as_worker_processes

## E · One engineering environment — menu, workflow, knowledge, tokens · closed
plan: docs/ENGINEERING_ENV_PLAN.md · opened: 2026-11-14 · closed: 2026-11-15

### C25 · E1 Five items in the menu (studio) · done

### C26 · E2 Project mode is the Project hub (studio) · done

### C27 · E3 The assistant: tone, models, colours (studio) · done

### C28 · E4 The workflow every project starts with (BRD 1) · done

### C29 · E5 Knowledge the workflow keeps (BRD 2) · done

### C30 · E7 Does OperonX save tokens? (BRD 3) · done

### C31 · E6 The project is the memory: save, start fresh, search (BRD 4) · done

### C32 · E8 Lint after every step (owner) · done

## P · Project mode — the studio as the place a project is built from · closed
plan: docs/PROJECT_MODE_PLAN.md · opened: 2026-10-23 · closed: 2026-10-23

### C1 · P0 The studio reaches the engine · done

### C2 · P1 Plans carry their status · done

### C3 · P2 One status for the whole project · done

### C4 · P3 Project mode · done

### C5 · P4 The pages behind the icons · done

### C6 · P5 The assistant works by the conventions · done

### C7 · P6 Gate on OneWealth and tipcalc · done

## B · A lighter board and BRD folder · closed
plan: docs/BOARD_SIMPLIFY_PLAN.md · opened: 2026-10-30 · closed: 2026-10-30

### C8 · B1 A card is a few lines · done

### C9 · B2 The BRD scaffold keeps what is read · done

### C10 · B3 The boarded projects rewritten · done

### C11 · B4 OneWealth's board from its plans · done

## V · A card's round pays for the full suite once · closed
plan: docs/PROJECT_SPEED_PLAN.md · opened: 2026-10-31 · closed: 2026-10-31

### C12 · V1 Review before the full checks · done

### C13 · V2 Quick checks — the acceptance tests and the tests near the change · done

### C14 · V3 The owner's check edits stick (withdrawn) · done

### C15 · V4 Sonnet and Haiku by default · done

## L · One lean plan — the project's shape and its board · closed
plan: docs/LEAN_PROJECT_PLAN.md · opened: 2026-11-11 · closed: 2026-11-11

### C16 · L1 Cut first · done

### C17 · L2 operonx init: three templates, three flags · done

### C18 · L3 Four lint checks and a baseline · done

### C19 · L4 The board · done

### C20 · L5 mr-finance on the board and the ratchet · done

## K · One board, cut into cycles · closed
plan: docs/CYCLES_PLAN.md · opened: 2026-11-12 · closed: 2026-11-12

### C21 · K1 Cut what links to no card · done

### C22 · K2 Cycles in the engine; plan status blocks go · done

### C23 · K3 The Board page is the cycles · done

### C24 · K4 The boards rebuilt from the plans · done
