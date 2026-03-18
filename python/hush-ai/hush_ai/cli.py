"""hush CLI — project scaffolding for Hush AI workflows."""

import os
from pathlib import Path

import click

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _read_template(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


def _write_file(path: Path, content: str, overwrite: bool = False) -> bool:
    """Write file if it doesn't exist (or overwrite=True). Returns True if written."""
    if path.exists() and not overwrite:
        click.echo(f"  skip {path.relative_to(Path.cwd())} (already exists)")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    click.echo(f"  create {path.relative_to(Path.cwd())}")
    return True


def _prompt_api_keys() -> dict:
    """Prompt for commonly used API keys. Returns {VAR_NAME: value}."""
    click.echo()
    click.echo("API Keys (leave blank to skip):")
    click.echo("-" * 40)

    keys = {}

    openai = click.prompt("  OpenAI API key", default="", show_default=False).strip()
    if openai:
        keys["OPENAI_API_KEY"] = openai

    langfuse_pub = click.prompt("  Langfuse public key", default="", show_default=False).strip()
    if langfuse_pub:
        keys["LANGFUSE_PUBLIC_KEY"] = langfuse_pub
        langfuse_sec = click.prompt("  Langfuse secret key", default="", show_default=False).strip()
        if langfuse_sec:
            keys["LANGFUSE_SECRET_KEY"] = langfuse_sec

    return keys


def _fill_env_template(api_keys: dict) -> str:
    """Fill .env template with prompted API keys."""
    content = _read_template("env.template")
    for key, value in api_keys.items():
        # Replace placeholder values like "sk-..." or empty values
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                break
        content = "\n".join(lines)
    return content


PYPROJECT_TEMPLATE = """\
[project]
name = "{name}"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "hush-ai>=0.1.5",
    "python-dotenv>=1.0",
]

[tool.uv.sources]
hush-ai = {{ path = "../python/hush-ai", editable = true }}
"""


@click.group()
@click.version_option(package_name="hush-ai")
def main():
    """Hush AI — workflow engine CLI."""
    pass


@main.command()
@click.argument("directory", default=".", required=False)
@click.option("--rust", is_flag=True, help="Include Rust backend scaffolding")
@click.option("--no-prompt", is_flag=True, help="Skip API key prompts")
def init(directory: str, rust: bool, no_prompt: bool):
    """Initialize a Hush AI project.

    Creates project scaffolding with workflow template, environment config,
    resource definitions, and AI assistant guide for vibe coding.
    """
    target = Path(directory).resolve()
    is_new = directory != "." and not target.exists()

    if is_new:
        target.mkdir(parents=True, exist_ok=True)

    # Switch to target directory for relative path display
    original_cwd = Path.cwd()
    os.chdir(target)

    click.echo(f"Initializing Hush project in {target}")
    click.echo()

    # Prompt for API keys
    api_keys = {} if no_prompt else _prompt_api_keys()
    click.echo()

    # pyproject.toml — only for new projects without one
    if not (target / "pyproject.toml").exists():
        project_name = target.name
        content = PYPROJECT_TEMPLATE.format(name=project_name)
        # Remove uv.sources for standalone projects (not in monorepo)
        content = "\n".join(
            line
            for line in content.split("\n")
            if "uv.sources" not in line and "editable" not in line
        )
        _write_file(target / "pyproject.toml", content)

    # .env
    env_content = _fill_env_template(api_keys)
    _write_file(target / ".env", env_content)

    # resources.yaml
    _write_file(target / "resources.yaml", _read_template("resources.template"))

    # workflow.py
    _write_file(target / "workflow.py", _read_template("workflow.template"))

    # .gitignore
    _write_file(target / ".gitignore", _read_template("gitignore.template"))

    # .claude/CLAUDE.md — vibe coding guide
    _write_file(target / ".claude" / "CLAUDE.md", _read_template("claude.template"))

    # Rust scaffolding
    if rust:
        click.echo()
        click.echo("Rust backend scaffolding:")
        _write_file(
            target / "rust_ops" / "Cargo.toml",
            _read_template("rust/Cargo.toml"),
        )
        _write_file(
            target / "rust_ops" / "src" / "main.rs",
            _read_template("rust/main.rs"),
        )

    os.chdir(original_cwd)

    click.echo()
    click.echo("Done! Next steps:")
    click.echo(f"  cd {target.relative_to(original_cwd) if is_new else '.'}")
    click.echo("  uv run python workflow.py")
    click.echo()
    click.echo("For vibe coding, the AI guide is at .claude/CLAUDE.md")


if __name__ == "__main__":
    main()
