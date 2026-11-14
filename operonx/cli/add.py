"""``operonx add``: a feature's wired code in one command (COST_PLAN K2).

    operonx add route NAME METHOD PATH [--in a,b] [--llm] [--feature F]
    operonx add job NAME [--in a,b] [--llm] [--every 5m | --at 07:00] [--feature F]
    operonx add llm NAME [--in text] [--feature F]
    operonx add page NAME [--data /path]

``route``, ``job`` and ``llm`` write an op and the graph that wires it into
the feature's file, ``src/F.py`` (created when missing), above its lists.
``route`` also adds a ``Service`` to its ``SERVICES``, and ``job`` a ``Job``
to its ``JOBS``. A feature split into a ``src/F/`` folder gets them in
``ops.py``, ``graph.py``, ``routes.py`` and ``jobs.py``. The feature's list
is wired into ``APP``, ahead of a mount at ``/``. With ``--llm`` a model call on ``llm:assistant`` comes before the op,
and the op gets its ``answer``. ``llm`` is that model call on its own, as a
graph to run from a job, a route or ``invoke``. Whatever calls a model turns
the resource on: ``resources.yaml``, the ``.env.example`` names, and the
``openai`` extra.

``page`` writes ``web/NAME/index.html``, served at ``/NAME/``, and
``web/pages/NAME.js``. The page reads ``--data`` through ``web/lib/api.js``
and draws it as a table. It adds a link from ``web/index.html``.

Every generated body runs as written: the op answers its inputs, so
``operonx check --call`` passes before anything is filled in. The command
then prints the ``TODO`` lines to fill in, and nothing else. It refuses a
name that is already taken and never rewrites code it did not write.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = ["AddError", "main"]

KEY = "assistant"
_PARAM = re.compile(r"\{(\w+)\}")
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")

LLM_YAML = f"""
llm:{KEY}:
  api_type: openai          # openai | azure | vllm | gemini | anthropic
  api_key: ${{LLM_API_KEY}}
  base_url: ${{LLM_BASE_URL:https://api.openai.com/v1}}
  model: ${{LLM_MODEL:gpt-4o-mini}}
  # retry: {{max_attempts: 3, initial: 1}}   # send a failed request again (429, 5xx)
"""
LLM_ENV = (
    "LLM_API_KEY=\n# Optional: any OpenAI-compatible endpoint and model.\n"
    "# LLM_BASE_URL=https://api.openai.com/v1\n# LLM_MODEL=gpt-4o-mini\n"
)


FAKE_CONFTEST = '''"""Tests run on a scripted model (`api_type: fake`): no key, no network. It
answers every request with "Echo: <the last user message>"; `script:` turns
can also be a string, `{status: 503}` or `{delay: 0.5, text: ...}`."""

import os

import operonx
import pytest

os.environ.setdefault("LLM_API_KEY", "test")  # resources.yaml resolves; the fake model answers

FAKE = """
llm:assistant:
  api_type: fake
  script:
    - echo: "Echo: "
"""


@pytest.fixture(scope="session", autouse=True)
def fake_model(tmp_path_factory):
    """`llm:assistant` for every test, answered by the fake model."""
    path = tmp_path_factory.mktemp("resources") / "resources.yaml"
    path.write_text(FAKE)
    operonx.bootstrap(resources=path, env=False)
    yield path
'''


class AddError(Exception):
    """What ``operonx add`` will not do, said in one line."""


# --------------------------------------------------------------------------- source edits


def _imported(tree: ast.Module) -> set:
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {a.asname or a.name.split(".")[0] for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def _offsets(src: str) -> List[int]:
    starts, at = [0], 0
    for line in src.splitlines(keepends=True):
        at += len(line)
        starts.append(at)
    return starts


def _at(src: str, lineno: int, col: int) -> int:
    """A character offset from an ast (line, utf-8 byte column)."""
    start = _offsets(src)[lineno - 1]
    line = src[start:].split("\n", 1)[0]
    return start + len(line.encode()[:col].decode())


def _splice(src: str, edits: List[Tuple[int, str]]) -> str:
    for at, text in sorted(edits, key=lambda e: e[0], reverse=True):
        src = src[:at] + text + src[at:]
    return src


def _add_imports(src: str, imports: Sequence[Tuple[str, str]]) -> str:
    """``from module import name`` for each name the module does not bind yet:
    into a one-line import of the same module, else a new line after the last
    top-level import."""
    tree = ast.parse(src)
    have = _imported(tree)
    wanted: Dict[str, List[str]] = {}
    for module, name in imports:
        bound = name.split(" as ")[-1]
        if bound not in have and name not in wanted.setdefault(module, []):
            wanted[module].append(name)
    edits, new = [], []
    tops = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    for module, names in wanted.items():
        if not names:
            continue
        same = next(
            (n for n in tops if isinstance(n, ast.ImportFrom) and n.module == module and n.level == 0
             and n.lineno == n.end_lineno and "(" not in ast.get_source_segment(src, n)),
            None,
        )  # fmt: skip
        if same is not None:
            old = [a.name + (f" as {a.asname}" if a.asname else "") for a in same.names]
            line = f"from {module} import {', '.join(sorted(set(old + names), key=str.lower))}"
            edits.append((same, line))
        else:
            new.append(f"from {module} import {', '.join(names)}")
    for node, line in edits:
        a, b = (
            _at(src, node.lineno, node.col_offset),
            _at(src, node.end_lineno, node.end_col_offset),
        )
        src = src[:a] + line + src[b:]
    if new:
        tree = ast.parse(src)
        tops = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        if tops:
            at = _at(src, tops[-1].end_lineno, tops[-1].end_col_offset)
            src = src[:at] + "\n" + "\n".join(new) + src[at:]
        else:
            doc = tree.body[0] if tree.body and isinstance(tree.body[0], ast.Expr) else None
            at = _at(src, doc.end_lineno, doc.end_col_offset) if doc else 0
            src = (
                src[:at]
                + ("\n\n" if doc else "")
                + "\n".join(new)
                + ("\n" if not doc else "")
                + src[at:]
            )
    return src


def _list_value(tree: ast.Module, name: str) -> Optional[ast.List]:
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
            and isinstance(node.value, ast.List)
        ):
            return node.value
    return None


def _append_to_list(src: str, node: ast.List, item: str, before: Optional[ast.AST] = None) -> str:
    """*item* into the list literal *node*: before the element *before*, else
    last. A list over several lines gets the item on its own lines."""
    if before is not None:
        return _splice(src, [(_at(src, before.lineno, before.col_offset), f"{item}, ")])
    close = _at(src, node.end_lineno, node.end_col_offset) - 1  # the "]"
    if not node.elts:
        return src[:close] + item + src[close:]
    last = node.elts[-1]
    end = _at(src, last.end_lineno, last.end_col_offset)
    if node.end_lineno == last.end_lineno:  # one line: [a, b] → [a, b, item]
        return src[:end] + f", {item}" + src[end:]
    indent = " " * last.col_offset
    body = "\n".join(indent + line if line else line for line in item.splitlines())
    comma = "" if "," in src[end:close] else ","
    line_start = src.rfind("\n", 0, close) + 1
    return src[:end] + comma + src[end:line_start] + body + ",\n" + src[line_start:]


def _write(path: Path, src: str, wrote: Dict[Path, str]) -> None:
    ast.parse(src)  # never leave a file that does not parse
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(src)
    wrote[path] = src


def _module(path: Path, doc: str) -> str:
    return path.read_text() if path.is_file() else f'"""{doc}"""\n'


def _above_lists(src: str, code: str) -> str:
    """*code* (op and graph defs) above the module's ``JOBS``/``SERVICES``, which
    name the graphs and so must come after them; at the end when it has none."""
    tree = ast.parse(src)
    lists = [
        n for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id in ("JOBS", "SERVICES") for t in n.targets)
    ]  # fmt: skip
    if not lists:
        return src.rstrip("\n") + "\n" + code
    at = _offsets(src)[lists[0].lineno - 1]
    return src[:at].rstrip("\n") + "\n" + code + "\n\n" + src[at:]


# --------------------------------------------------------------------------- the project


def _root(start: Path) -> Path:
    for d in [start, *start.parents]:
        if (d / "operonx.toml").is_file():
            return d
    raise AddError(f"no operonx.toml at or above {start}")


def _app_file(root: Path) -> Tuple[Path, str]:
    """The module ``operonx.toml``'s ``app = "pkg.mod:APP"`` names, and APP's name."""
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:  # pragma: no cover — Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]
    spec = tomllib.loads((root / "operonx.toml").read_text()).get("project", {}).get("app", "")
    module, _, attr = spec.partition(":")
    path = root.joinpath(*module.split(".")).with_suffix(".py")
    if not module or not path.is_file():
        raise AddError(f"operonx.toml names app = {spec!r}; no such file {path}")
    return path, attr or "APP"


def _toolchain(root: Path) -> str:
    agents = root / "AGENTS.md"
    if agents.is_file():
        return "uv" if "uv run " in agents.read_text() else "pip"
    return "uv"


def _feature_files(root: Path, feature: str) -> Dict[str, Path]:
    """Where each part of a feature lives: all of it in ``src/F.py`` (COST_PLAN
    K4), or, once the project split it into a ``src/F/`` folder, its
    ``ops.py``, ``graph.py``, ``routes.py`` and ``jobs.py``."""
    pkg = root / "src" / feature
    if not pkg.is_dir():
        return dict.fromkeys(("ops", "graph", "routes", "jobs"), pkg.with_suffix(".py"))
    return {
        "init": pkg / "__init__.py",
        **{p: pkg / f"{p}.py" for p in ("ops", "graph", "routes", "jobs")},
    }


def _module_of(root: Path, path: Path) -> str:
    """``src/billing.py`` → ``billing``; ``src/billing/routes.py`` → ``billing.routes``."""
    return ".".join(path.relative_to(root / "src").with_suffix("").parts)


def _wire(root: Path, feature: str, kind: str, wrote: Dict[Path, str]) -> None:
    """``from <feature>.<kind> import <LIST>`` in the app module and the list in
    ``APP``'s ``services=`` / ``jobs=``. Already there: nothing to do."""
    path, attr = _app_file(root)
    src = path.read_text()
    module = _module_of(root, _feature_files(root, feature)[kind])
    list_name = {"routes": "SERVICES", "jobs": "JOBS"}[kind]
    keyword = {"routes": "services", "jobs": "jobs"}[kind]
    tree = ast.parse(src)
    for node in tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module in (module, feature)  # a split feature may re-export it
            and any(a.name == list_name for a in node.names)
        ):
            return  # the feature's list is wired already; new entries ride along
    alias = list_name if list_name not in _imported(tree) else f"{feature.upper()}_{list_name}"
    src = _add_imports(
        src, [(module, list_name if alias == list_name else f"{list_name} as {alias}")]
    )
    tree = ast.parse(src)
    call = next(
        (n.value for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
         and any(isinstance(t, ast.Name) and t.id == attr for t in n.targets)),
        None,
    )  # fmt: skip
    if call is None:
        raise AddError(f"{path.relative_to(root)} has no `{attr} = Application(...)` to wire into")
    kw = next((k for k in call.keywords if k.arg == keyword), None)
    if kw is None:  # APP(...) gets the keyword, before its closing paren
        close = _at(src, call.end_lineno, call.end_col_offset) - 1
        last = (call.keywords or call.args)[-1]
        end = _at(src, last.end_lineno, last.end_col_offset)
        if call.end_lineno > last.end_lineno:
            comma = "" if "," in src[end:close] else ","
            line_start = src.rfind("\n", 0, close) + 1
            add = f"{' ' * last.col_offset}{keyword}=[*{alias}],\n"
            src = src[:end] + comma + src[end:line_start] + add + src[line_start:]
        else:
            src = src[:close] + f", {keyword}=[*{alias}]" + src[close:]
    elif isinstance(kw.value, ast.List):
        root_mount = _root_mounts(tree)
        before = next(
            (e for e in kw.value.elts if isinstance(e, ast.Name) and e.id in root_mount), None
        )
        src = _append_to_list(src, kw.value, f"*{alias}", before)
    else:  # jobs=JOBS → jobs=[*JOBS, *ALIAS]
        a = _at(src, kw.value.lineno, kw.value.col_offset)
        b = _at(src, kw.value.end_lineno, kw.value.end_col_offset)
        src = src[:a] + f"[*{src[a:b]}, *{alias}]" + src[b:]
    _write(path, src, wrote)


def _root_mounts(tree: ast.Module) -> set:
    """Names bound to ``Service(..., asgi("/" ...))``: a mount at / answers every
    path, so whatever is wired goes in front of it."""
    out = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            for arg in node.value.args:
                if (
                    isinstance(arg, ast.Call)
                    and getattr(arg.func, "id", "") == "asgi"
                    and arg.args
                    and isinstance(arg.args[0], ast.Constant)
                    and arg.args[0].value == "/"
                ):
                    out |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return out


def _turn_on_llm(root: Path, wrote: Dict[Path, str]) -> List[str]:
    """``llm:assistant`` in resources.yaml, its names in .env.example, the
    openai extra in pyproject.toml. Returns what the user still runs."""
    said = []
    res = root / "resources.yaml"
    text = res.read_text() if res.is_file() else ""
    if not re.search(rf"^llm:{KEY}:", text, re.M):
        res.write_text(text.rstrip("\n") + "\n" + LLM_YAML)
        wrote[res] = res.read_text()
    env = root / ".env.example"
    text = env.read_text() if env.is_file() else ""
    if not re.search(r"^LLM_API_KEY=", text, re.M):
        env.write_text(text.rstrip("\n") + ("\n" if text else "") + LLM_ENV)
        wrote[env] = env.read_text()
    py = root / "pyproject.toml"
    text = py.read_text() if py.is_file() else ""
    dep = re.search(r'"operonx(\[([^\]]*)\])?(?=[<>=~! ;"])', text)
    if dep and "openai" not in (dep.group(2) or ""):
        extras = sorted({*filter(None, (dep.group(2) or "").split(",")), "openai"})
        py.write_text(text[: dep.start()] + f'"operonx[{",".join(extras)}]' + text[dep.end() :])
        wrote[py] = py.read_text()
        said.append("uv sync" if _toolchain(root) == "uv" else 'pip install -e ".[test]"')
    conftest = root / "tests" / "conftest.py"
    text = conftest.read_text() if conftest.is_file() else ""
    if "api_type: fake" not in text:  # the tests never call a real model
        _write(conftest, (text.rstrip("\n") + "\n\n\n" if text else "") + FAKE_CONFTEST, wrote)
    if not (root / ".env").is_file() or "LLM_API_KEY=" not in (root / ".env").read_text():
        said.append("put LLM_API_KEY (and LLM_BASE_URL / LLM_MODEL) in .env")
    return said


# --------------------------------------------------------------------------- what is written


def _fields(text: Optional[str]) -> List[str]:
    out = [f.strip() for f in (text or "").split(",") if f.strip()]
    bad = [f for f in out if not _IDENT.match(f)]
    if bad:
        raise AddError(f"--in takes python names: {', '.join(bad)}")
    return out


def _step_code(
    name: str, inputs: List[str], llm: bool, route: bool
) -> Tuple[str, str, List[Tuple[str, str]]]:
    """The op, the graph, and the graph module's imports."""
    args = inputs + (["answer"] if llm else [])
    sig = ", ".join(f"{a}: str" for a in args)
    keys = ", ".join(f'"{a}": {a}' for a in args)
    status = '  # "$status": 201 or 404 sets the reply\'s code' if route else ""
    op_src = (
        f"\n\n@op\ndef {name}({sig}) -> dict:\n"
        f"    # TODO {name}: the logic. Its outputs are this dict's keys.\n"
        f"    return {{{keys}}}{status}\n"
    )
    flow = f"{name}_flow"
    pass_in = ", ".join(f"{a}={a}" for a in inputs)
    lines = [f"\n\n@graph\ndef {flow}({', '.join(inputs)}):"]
    imports = [("operonx", "END"), ("operonx", "START"), ("operonx", "graph")]
    if llm and not inputs:  # the model reads what an op gathers (today's rows, a file)
        op_src = (
            f"\n\n@op\ndef {name}_input() -> dict:\n"
            f"    # TODO {name}: what the model reads, as text.\n"
            f'    return {{"text": ""}}\n' + op_src
        )
        lines.append(f"    given = {name}_input()")
    if llm:
        imports.append(("operonx.providers.ops", "LLMOp"))
        lines += _ask_lines(name, inputs, 'given["text"]' if not inputs else None)
        step_in = ", ".join(x for x in (pass_in, 'answer=ask["content"]') if x)
        first = "given >> ask" if not inputs else "ask"
        lines += [f"    step = {name}({step_in})", f"    START >> {first} >> step >> END"]
    else:
        lines += [f"    step = {name}({pass_in})", "    START >> step >> END"]
    return op_src, "\n".join(lines) + "\n", imports


def _ask_lines(name: str, inputs: List[str], given: Optional[str] = None) -> List[str]:
    user = "\\n".join(f"{{{a}}}" for a in inputs) or "{text}"
    fill = "".join(f" {a}={a}," for a in inputs) or (f" text={given}," if given else "")
    return [
        "    ask = LLMOp.of(",
        f'        resource="{KEY}",',
        f'        prompt={{"system": "TODO {name}: what the model does, in one line.", "user": "{user}"}},',
        *([f"       {fill}"] if fill else []),
        '    )  # ask["content"]; ask["error"] is set when the call fails',
    ]


def _add_step(root: Path, feature: str, name: str, inputs: List[str], llm: bool, route: bool,
              wrote: Dict[Path, str]) -> str:  # fmt: skip
    files = _feature_files(root, feature)
    for part in ("ops", "graph"):
        if files[part].is_file() and {name, f"{name}_flow"} & _imported(
            ast.parse(files[part].read_text())
        ):
            raise AddError(f"{name} is already in {files[part].relative_to(root)}")
    _start(root, feature, files, wrote)
    op_src, graph_src, graph_imports = _step_code(name, inputs, llm, route)
    src = _module(files["ops"], f"The {feature} feature's logic. Every op returns a dict literal.")
    src = _add_imports(src, [("operonx", "op")])
    _write(files["ops"], _above_lists(src, op_src), wrote)
    src = _module(
        files["graph"], f"The {feature} feature's wiring: no logic here, only ops and edges."
    )
    ops = [name, *([f"{name}_input"] if llm and not inputs else [])]
    ops_module = _module_of(root, files["ops"])
    src = _add_imports(src, [*graph_imports, *((ops_module, o) for o in ops)])
    _write(files["graph"], _above_lists(src, graph_src), wrote)
    return f"{name}_flow"


def _start(root: Path, feature: str, files: Dict[str, Path], wrote: Dict[Path, str]) -> None:
    """A new feature's first file: ``src/F.py``, or a folder's ``__init__.py``."""
    first = files.get("init", files["ops"])
    if not first.is_file():
        what = "in one file: its ops, the graphs that wire them, its jobs and routes"
        doc = f"The {feature} feature." if "init" in files else f"The {feature} feature {what}."
        _write(first, f'"""{doc}"""\n', wrote)


def _add_llm_graph(
    root: Path, feature: str, name: str, inputs: List[str], wrote: Dict[Path, str]
) -> None:
    files = _feature_files(root, feature)
    if files["graph"].is_file() and f"{name}_flow" in _imported(
        ast.parse(files["graph"].read_text())
    ):
        raise AddError(f"{name}_flow is already in {files['graph'].relative_to(root)}")
    _start(root, feature, files, wrote)
    lines = [
        f"\n\n@graph\ndef {name}_flow({', '.join(inputs)}):",
        *_ask_lines(name, inputs),
        "    START >> ask >> END",
    ]
    src = _module(
        files["graph"], f"The {feature} feature's wiring: no logic here, only ops and edges."
    )
    src = _add_imports(src, [("operonx", "END"), ("operonx", "START"), ("operonx", "graph"),
                             ("operonx.providers.ops", "LLMOp")])  # fmt: skip
    _write(files["graph"], _above_lists(src, "\n".join(lines) + "\n"), wrote)


def _add_to_list(root: Path, feature: str, part: str, list_name: str, doc: str, imports, item: str,
                 wrote: Dict[Path, str]) -> None:  # fmt: skip
    path = _feature_files(root, feature)[part]
    src = _add_imports(_module(path, doc), imports)
    node = _list_value(ast.parse(src), list_name)
    if node is None:
        body = "\n".join("    " + x for x in item.splitlines())
        src = src.rstrip("\n") + f"\n\n{list_name} = [\n{body},\n]\n"
    else:
        src = _append_to_list(src, node, item)
    _write(path, src, wrote)


def add_route(root, name, method, path, inputs, llm, feature) -> Dict[Path, str]:
    method = method.upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        raise AddError(f"{method}: a route is GET, POST, PUT, PATCH or DELETE")
    if not path.startswith("/"):
        raise AddError(f"{path}: a path starts with /")
    config = root / "src" / "_shared" / "config.py"
    if not (config.is_file() and re.search(r"^PORT\b", config.read_text(), re.M)):
        raise AddError("no PORT in src/_shared/config.py: `operonx init . --http` adds serving")
    params = _PARAM.findall(path)
    feature = feature or next((s for s in path.split("/") if s and not s.startswith("{")), "")
    if not feature:
        raise AddError(f"{path} names no feature: pass --feature")
    feature = feature.replace("-", "_")
    wrote: Dict[Path, str] = {}
    flow = _add_step(
        root, feature, name, params + [i for i in inputs if i not in params], llm, True, wrote
    )
    item = (
        f'Service(\n    "{name}",\n    http("{method}", "{path}", port=PORT),\n    graph={flow},\n'
        f'    description="TODO {name}: what a caller sends and gets back.",\n)'
    )
    _add_to_list(root, feature, "routes", "SERVICES", f"The {feature} feature's routes: its graphs behind HTTP.",
                 [("operonx.app", "Service"), ("operonx.app", "http"), ("_shared.config", "PORT"),
                  (_module_of(root, _feature_files(root, feature)["graph"]), flow)], item, wrote)  # fmt: skip
    _wire(root, feature, "routes", wrote)
    return wrote


def add_job(root, name, inputs, llm, feature, every=None, at=None) -> Dict[Path, str]:
    feature = (feature or name).replace("-", "_")
    wrote: Dict[Path, str] = {}
    flow = _add_step(root, feature, name, inputs, llm, False, wrote)
    imports = [
        ("operonx.app.jobs", "Job"),
        (_module_of(root, _feature_files(root, feature)["graph"]), flow),
    ]
    if inputs:
        items = f'items=DATA / "{name}.jsonl",  # TODO {name}: one run per line (or a list, or a function)\n    key="{inputs[0]}",\n'
        imports.append(("_shared.config", "DATA"))
    else:
        items = f"items=[{{}}],  # one run; a list of dicts, a function that yields them, or a .jsonl path\n"
    clock = ""
    if every or at:
        # on the routes' port: a clock on its own port is a second listener
        # (a second server for `operonx serve`, and `APP.asgi()` refuses two)
        config = root / "src" / "_shared" / "config.py"
        served = config.is_file() and re.search(r"^PORT\b", config.read_text(), re.M)
        port = ", port=PORT" if served else ""
        clock = f'    schedule=schedule({"every" if every else "at"}="{every or at}"{port}),\n'
        imports.append(("operonx.app", "schedule"))
        if served:
            imports.append(("_shared.config", "PORT"))
    item = (
        f'Job(\n    "{name}",\n    graph={flow},\n    {items}{clock}'
        f'    description="TODO {name}: what one run does.",\n)'
    )
    _add_to_list(root, feature, "jobs", "JOBS", f"The {feature} feature's jobs: its graphs, run per item.",
                 imports, item, wrote)  # fmt: skip
    _wire(root, feature, "jobs", wrote)
    return wrote


PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="/assets/style.css">
</head>
<body>
  <main id="page">
    <p><a href="/">Home</a></p>
    <h1>{title}</h1>
  </main>
  <script type="module" src="/pages/{name}.js"></script>
</body>
</html>
"""

PAGE_JS = """// {title}: reads {data} through lib/api.js and draws it as a table.
import {{ get }} from "../lib/api.js";

const page = document.getElementById("page");

function table(rows) {{
  // TODO {name}: which columns, in which order; this shows every key of the first row.
  const t = document.createElement("table");
  const columns = Object.keys(rows[0] || {{}});
  t.append(row("th", columns), ...rows.map((r) => row("td", columns.map((c) => r[c]))));
  return t;
}}

function row(cell, values) {{
  const tr = document.createElement("tr");
  for (const v of values) {{
    const c = document.createElement(cell);
    c.textContent = typeof v === "object" ? JSON.stringify(v) : String(v ?? "");
    tr.append(c);
  }}
  return tr;
}}

try {{
  const reply = await get("{data}");
  const rows = Array.isArray(reply) ? reply : Object.values(reply).find(Array.isArray) || [reply];
  page.append(table(rows));
}} catch (error) {{
  page.append(error.message);
}}
"""

GET_JS = """
export async function get(path) {
  const response = await fetch(path);
  const reply = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(reply.error || `${response.status} ${response.statusText}`);
  return reply;
}
"""


def add_page(root, name, data) -> Dict[Path, str]:
    web = root / "web"
    if not web.is_dir():
        raise AddError("no web/ here: `operonx init . --web` adds it")
    if (web / name).exists() or (web / "pages" / f"{name}.js").exists():
        raise AddError(f"web/{name} is already there")
    wrote: Dict[Path, str] = {}
    title = name.replace("_", " ").replace("-", " ").capitalize()
    data = data or f"/{name}/data"
    for path, text in (
        (web / name / "index.html", PAGE_HTML.format(title=title, name=name)),
        (web / "pages" / f"{name}.js", PAGE_JS.format(title=title, name=name, data=data)),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        wrote[path] = text
    api = web / "lib" / "api.js"
    text = api.read_text() if api.is_file() else ""
    if "function get(" not in text:
        api.parent.mkdir(parents=True, exist_ok=True)
        api.write_text(
            text.rstrip("\n") + ("\n" if text else "") + GET_JS.lstrip("\n" if not text else "")
        )
        wrote[api] = api.read_text()
    home = web / "index.html"
    if home.is_file():
        text = home.read_text()
        link = f'<a href="/{name}/">{title}</a>'
        if link not in text:
            if "<nav>" in text:
                text = text.replace("<nav>", f"<nav>\n    {link}", 1)
            else:
                text = re.sub(
                    r"(<main[^>]*>)", rf"<nav>\n    {link}\n  </nav>\n  \1", text, count=1
                )
            home.write_text(text)
            wrote[home] = text
    return wrote


# --------------------------------------------------------------------------- the command


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="operonx add",
        description="add a route, a job, a model call or a page: the wired code, then what to fill in",
    )
    p.add_argument("--dir", default=".", help="the project (default: here)")
    sub = p.add_subparsers(dest="what", metavar="WHAT", required=True)
    r = sub.add_parser("route", help="an op and its graph behind HTTP (a Service in APP)")
    r.add_argument("name")
    r.add_argument("method")
    r.add_argument("path", help="/tickets or /tickets/{id}: a {param} is an input")
    j = sub.add_parser("job", help="an op and its graph, run per item (a Job in APP)")
    j.add_argument("name")
    clock = j.add_mutually_exclusive_group()
    clock.add_argument("--every", help="run it on a clock inside `operonx serve`: 5m, 1h")
    clock.add_argument("--at", help="run it daily at HH:MM inside `operonx serve`")
    m = sub.add_parser("llm", help="a model call on llm:assistant, as a graph")
    m.add_argument("name")
    for s in (r, j, m):
        s.add_argument("--in", dest="inputs", help="the inputs, comma-separated: text,priority")
        s.add_argument(
            "--feature", help="the src/ package (route: the path's first part; job/llm: NAME)"
        )
    for s in (r, j):
        s.add_argument(
            "--llm", action="store_true", help="a model call first; the op gets its answer"
        )
    pg = sub.add_parser(
        "page", help="web/NAME/: a table of what a GET route answers, linked from /"
    )
    pg.add_argument("name")
    pg.add_argument("--data", help="the GET route it reads (default /NAME/data)")
    return p


def _todo_lines(root: Path, wrote: Dict[Path, str], name: str) -> List[str]:
    out = []
    for path, text in wrote.items():
        for i, line in enumerate(text.splitlines(), 1):
            if f"TODO {name}:" in line:
                out.append(f"  {path.relative_to(root)}:{i}  {line.strip()}")
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        root = _root(Path(args.dir).resolve())
        name = args.name
        if args.what != "page" and not _IDENT.match(name):
            raise AddError(f"{name}: a name is a python name (lower case, _)")
        if args.what == "page" and not re.match(r"^[a-z0-9][a-z0-9_-]*$", name):
            raise AddError(f"{name}: a page name is lower case letters, digits, - and _")
        if getattr(args, "feature", None) and not _IDENT.match(args.feature.replace("-", "_")):
            raise AddError(f"--feature {args.feature}: a python name")
        llm = args.what == "llm" or getattr(args, "llm", False)
        if args.what == "route":
            wrote = add_route(
                root, name, args.method, args.path, _fields(args.inputs), args.llm, args.feature
            )
        elif args.what == "job":
            wrote = add_job(
                root, name, _fields(args.inputs), args.llm, args.feature, args.every, args.at
            )
        elif args.what == "llm":
            wrote = {}
            _add_llm_graph(
                root,
                (args.feature or name).replace("-", "_"),
                name,
                _fields(args.inputs) or ["text"],
                wrote,
            )
        else:
            wrote = add_page(root, name, args.data)
        then = _turn_on_llm(root, wrote) if llm else []
    except AddError as exc:
        sys.stderr.write(f"operonx add: {exc}\n")
        return 2
    run = "uv run " if _toolchain(root) == "uv" else ""
    lines = [f"wrote {', '.join(str(p.relative_to(root)) for p in wrote)}", "fill in:"]
    lines += _todo_lines(root, wrote, name) or ["  nothing"]
    lines += [f"then: {x}" for x in then] + [f"then: {run}operonx check --call"]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0
