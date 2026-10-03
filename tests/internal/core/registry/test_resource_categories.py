"""How a resource key's category finds the package that registers it.

A key is ``<category>:<name>``. The category's config class is registered
by a package — operonx's own providers, telemetry and jobs, or a third
party such as a knowledge base. The hub used to fall back to the raw dict
when no class was registered yet, and cached it: a later
``REGISTRY.register`` never took, and the error that eventually surfaced
named the wrong cause. Now:

* operonx's own categories resolve in any import order;
* a third-party package declares its categories as ``operonx.resources``
  entry points, which the hub loads on first use of the category;
* an unknown category, an unknown ``type:``, or a config that does not
  parse raises a ``ResourceCategoryError``/``KeyError`` that says which,
  and nothing is cached, so registering the category later works.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from importlib.metadata import EntryPoint
from typing import ClassVar

import pytest

from operonx.core.registry import REGISTRY, ResourceCategoryError, ResourceHub
from operonx.core.registry import resource_hub as hub_module
from operonx.core.utils.yaml_model import YamlModel


class WidgetConfig(YamlModel):
    _category: ClassVar[str] = "widget"
    colour: str = "red"


class Widget:
    def __init__(self, config: WidgetConfig):
        self.config = config


def register_widget() -> None:
    REGISTRY.register(WidgetConfig, Widget)


@pytest.fixture(autouse=True)
def clean_registry():
    """Each test starts without a ``widget`` category; outer state is restored.

    operonx's own categories are loaded first, so the snapshot holds them:
    their modules register once per process, and a restore to a snapshot
    taken before that would drop them for every later test."""
    hub_module._load_builtin_categories()
    saved = dict(REGISTRY._entries), dict(REGISTRY._class_entries)
    REGISTRY._entries.pop("widget", None)
    REGISTRY._class_entries.pop("WidgetConfig", None)
    hub_module._plugin_entry_points.cache_clear()
    yield
    REGISTRY._entries, REGISTRY._class_entries = saved
    hub_module._plugin_entry_points.cache_clear()


@pytest.fixture
def hub(tmp_path):
    path = tmp_path / "resources.yaml"
    path.write_text(
        textwrap.dedent("""\
            widget:thing:
              colour: green
            llm:broken:
              api_type: not-a-provider
              model: m
            custom:typed:
              type: NoSuchConfig
              x: 1
        """),
        encoding="utf-8",
    )
    return ResourceHub.from_yaml(path)


def _entry_points(monkeypatch, *eps: EntryPoint) -> None:
    """Make the hub see *eps* as the installed ``operonx.resources`` group."""
    monkeypatch.setattr(
        hub_module,
        "_installed_entry_points",
        lambda: [ep for ep in eps if ep.group == hub_module.RESOURCE_ENTRY_POINT_GROUP],
    )
    hub_module._plugin_entry_points.cache_clear()


def _ep(name: str, value: str) -> EntryPoint:
    return EntryPoint(name=name, value=value, group="operonx.resources")


# -- an unknown category ----------------------------------------------------------------


def test_an_unknown_category_raises_instead_of_returning_the_raw_dict(hub):
    with pytest.raises(ResourceCategoryError, match="no package registers category 'widget'"):
        hub.get_config("widget:thing")


def test_the_error_names_every_way_to_register_one(hub):
    with pytest.raises(ResourceCategoryError) as exc:
        hub.get("widget:thing")
    msg = str(exc.value)
    assert "REGISTRY.register" in msg and "operonx.resources" in msg
    assert isinstance(exc.value, KeyError)  # callers catching KeyError keep working


def test_a_category_registered_after_a_failed_lookup_resolves(hub):
    """The old fallback cached the raw dict, so this stayed broken for the
    life of the hub: has() / get_config() before the project's import."""
    assert hub.has("widget:thing")
    with pytest.raises(ResourceCategoryError):
        hub.get_config("widget:thing")
    register_widget()  # the project's import, later
    assert hub.get("widget:thing").config.colour == "green"


def test_presence_checks_do_not_parse(hub):
    """has() and keys() answer "is it declared", which needs no class."""
    assert hub.has("widget:thing") and hub.declares("widget:thing")
    assert not hub.has("widget:nope")
    assert set(hub.keys()) == {"widget:thing", "llm:broken", "custom:typed"}


def test_an_unknown_type_field_is_named(hub):
    with pytest.raises(ResourceCategoryError, match="type 'NoSuchConfig'"):
        hub.get_config("custom:typed")


def test_a_config_that_does_not_parse_says_so_rather_than_not_found(hub):
    with pytest.raises(KeyError) as exc:
        hub.get("llm:broken")
    msg = str(exc.value)
    assert "invalid config" in msg and "not-a-provider" in msg
    assert "not found" not in msg


def test_health_check_reports_each_cause(hub):
    result = hub.health_check()
    assert result.results == {"widget:thing": False, "llm:broken": False, "custom:typed": False}
    assert "no package registers category 'widget'" in result.errors["widget:thing"]
    assert "invalid config" in result.errors["llm:broken"]


# -- entry points -----------------------------------------------------------------------


def test_an_entry_point_named_after_the_category_registers_it(hub, monkeypatch):
    _entry_points(monkeypatch, _ep("widget", f"{__name__}:register_widget"))
    assert hub.get("widget:thing").config.colour == "green"


def test_only_the_entry_point_for_the_missing_category_is_loaded(hub, monkeypatch):
    """A broken plugin for another category must not break this one."""
    _entry_points(
        monkeypatch,
        _ep("gadget", "no_such_module_anywhere:register"),
        _ep("widget", f"{__name__}:register_widget"),
    )
    assert hub.get("widget:thing").config.colour == "green"


def test_a_plugin_that_fails_to_load_is_named(hub, monkeypatch):
    _entry_points(monkeypatch, _ep("widget", "no_such_module_anywhere:register"))
    with pytest.raises(ResourceCategoryError, match="no_such_module_anywhere:register") as exc:
        hub.get("widget:thing")
    assert isinstance(exc.value.__cause__, ModuleNotFoundError)


def test_a_plugin_that_registers_nothing_is_named(hub, monkeypatch):
    _entry_points(monkeypatch, _ep("widget", "builtins:dict"))
    with pytest.raises(ResourceCategoryError, match="did not register category 'widget'"):
        hub.get("widget:thing")


def test_an_entry_point_that_is_not_callable_is_refused(hub, monkeypatch):
    _entry_points(monkeypatch, _ep("widget", f"{__name__}:WidgetConfig.__doc__"))
    with pytest.raises(ResourceCategoryError, match="not a function"):
        hub.get("widget:thing")


def test_two_packages_claiming_one_category_is_an_error(hub, monkeypatch):
    _entry_points(
        monkeypatch,
        _ep("widget", f"{__name__}:register_widget"),
        _ep("widget", "other_pkg.registry:register"),
    )
    with pytest.raises(ResourceCategoryError, match="2 entry points"):
        hub.get("widget:thing")


def test_an_installed_distribution_is_discovered_in_a_fresh_process(tmp_path):
    """The real mechanism end to end: a package with an ``operonx.resources``
    entry point in its metadata, never imported by the script."""
    site = tmp_path / "site"
    pkg = site / "widgetpkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        textwrap.dedent("""\
            from typing import ClassVar
            from operonx.core.registry import REGISTRY
            from operonx.core.utils.yaml_model import YamlModel

            class GadgetConfig(YamlModel):
                _category: ClassVar[str] = "gadget"
                size: int = 1

            class Gadget:
                def __init__(self, config):
                    self.config = config

            def register():
                REGISTRY.register(GadgetConfig, Gadget)
        """),
        encoding="utf-8",
    )
    dist = site / "widgetpkg-0.1.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: widgetpkg\nVersion: 0.1\n")
    (dist / "entry_points.txt").write_text("[operonx.resources]\ngadget = widgetpkg:register\n")
    (tmp_path / "resources.yaml").write_text("gadget:big:\n  size: 9\n", encoding="utf-8")
    script = textwrap.dedent("""\
        import sys
        import operonx
        hub = operonx.bootstrap(resources="resources.yaml", env=False)
        assert "widgetpkg" not in sys.modules
        print(hub.get("gadget:big").config.size)
    """)
    out = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={"PYTHONPATH": str(site), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr[-1500:]
    assert out.stdout.strip() == "9"


# -- operonx's own categories -----------------------------------------------------------


def test_operonx_categories_outside_providers_resolve_in_a_bare_script(tmp_path):
    """run_store:, source:, trace_*: and langfuse: register in operonx.telemetry
    and operonx.app.jobs. A script that bootstraps and asks for one before
    importing those got "no provider is registered", pointing at
    operonx.providers. Fresh interpreter: inside pytest they are imported."""
    (tmp_path / "resources.yaml").write_text(
        textwrap.dedent("""\
            run_store:main:
              backend: sqlite
              path: runs.sqlite
            source:calls:
              kind: jsonl
              path: calls.jsonl
            trace_local:dev:
              root: traces
        """),
        encoding="utf-8",
    )
    (tmp_path / "calls.jsonl").write_text('{"id": 1}\n', encoding="utf-8")
    script = textwrap.dedent("""\
        import sys
        import operonx
        assert "operonx.telemetry.runs" not in sys.modules
        assert "operonx.app.jobs" not in sys.modules
        hub = operonx.bootstrap(resources="resources.yaml", env=False)
        for key in ("run_store:main", "source:calls", "trace_local:dev"):
            print(key, type(hub.get(key)).__name__)
    """)
    out = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr[-1500:]
    got = dict(line.split() for line in out.stdout.strip().splitlines())
    assert set(got) == {"run_store:main", "source:calls", "trace_local:dev"}
