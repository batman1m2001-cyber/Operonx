# Board

One card per task (LEAN_PROJECT_PLAN §5). The assistant moves the cards; edit a card's fields, not its state.

## E · One engineering environment — menu, workflow, knowledge, tokens · open
plan: docs/ENGINEERING_ENV_PLAN.md · opened: 2026-11-14

### C25 · E1 Five items in the menu (studio) · done
done: operonx-studio:tests/js/choreo.test.mjs
Shipped as studio #184 (bff376a).

### C26 · E2 Project mode is the Project hub (studio) · done
done: operonx-studio:tests/js/choreo.test.mjs
Shipped as studio #184 (bff376a).

### C27 · E3 The assistant: tone, models, colours (studio) · done
done: operonx-studio:tests/studio/test_assistant.py
Shipped as studio #185 (005223d).

### C28 · E4 The workflow every project starts with (BRD 1) · done
files: operonx/cli/templates/_common/AGENTS.md.tmpl, operonx/guide/__init__.py
done: tests/internal/lint/test_shape.py, packages/operonx-project/tests/test_board.py::test_a_move_rewrites_only_that_card

### C29 · E5 Knowledge the workflow keeps (BRD 2) · done
files: operonx/cli/templates/_brd/knowledge.md.tmpl, operonx/guide/__init__.py
done: tests/internal/guide/test_catalog.py, tests/internal/lint/test_shape.py::test_stale_knowledge_is_a_warning

### C31 · E6 The project is the memory: save, start fresh, search (BRD 4) · doing
files: packages/operonx-project/operonx_project/search.py, operonx-studio:operonx_studio/assistant.py
Studio half shipped as studio #187; gates 1 (continuity, 2/2) and 2 (10.2 KB from 22.1 KB) met. Open: gate 3, the context meter over the next 20 real turns.

### C32 · E8 Lint after every step (owner) · done
files: operonx-studio:operonx_studio/stophook.py
done: operonx-studio:tests/studio/test_stophook.py
Shipped as studio #186.

### C30 · E7 Does OperonX save tokens? (BRD 3) · todo
files: bench/token_ab/

## Backlog

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
