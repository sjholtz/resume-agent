"""Resolve the candidate workspace independently of LangChain/Deep Agents.

Python 3.11+ (standard library only).

Workspace precedence:
    1. Explicit ``workspace`` argument (e.g. --workspace)
    2. RESUME_AGENT_WORKSPACE environment variable
    3. [storage].candidate_root in config.toml
    4. Current directory, only if it contains .resume-agent-workspace
    5. Interactive prompt (TTY only, if enabled)

No file is written on import or by load_config(). Call
save_initial_config() explicitly after a first-run prompt if the user wants
to remember that root.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

WORKSPACE_MARKER = ".resume-agent-workspace"
WORKSPACE_ENV = "RESUME_AGENT_WORKSPACE"
CONFIG_DIR_NAME = "resume-agent"
SCHEMA_VERSION = 1


class ConfigurationError(ValueError):
    """An absent, malformed, or unusable candidate workspace configuration."""


Source = Literal["argument", "environment", "config", "cwd", "prompt"]


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """Validated, absolute paths for one agent session."""

    app_root: Path
    workspace_root: Path
    config_path: Path
    workspace_source: Source


def default_config_path(*, environ: Mapping[str, str] | None = None) -> Path:
    """Use XDG_CONFIG_HOME if absolute; otherwise use ~/.config."""
    env = os.environ if environ is None else environ
    xdg = env.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        base = Path(xdg).expanduser()
        if not base.is_absolute():
            raise ConfigurationError("XDG_CONFIG_HOME must be an absolute path")
    else:
        base = Path.home() / ".config"
    return base / CONFIG_DIR_NAME / "config.toml"


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        if path.exists():
            raise ConfigurationError(f"Configuration path is not a file: {path}")
        return {}
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(
            f"Cannot read TOML configuration {path}: {exc}"
        ) from exc
    version = data.get("schema_version", SCHEMA_VERSION)
    if type(version) is not int or version != SCHEMA_VERSION:
        raise ConfigurationError(
            f"Unsupported schema_version {version!r} in {path}; expected {SCHEMA_VERSION}"
        )
    for group in ("storage", "behavior"):
        if group in data and not isinstance(data[group], dict):
            raise ConfigurationError(f"[{group}] in {path} must be a table")
    return data


def _bool_setting(table: dict, key: str, default: bool) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigurationError(f"[behavior].{key} must be true or false")
    return value


def _workspace_path(value: str | Path, *, basedir: Path, label: str) -> Path:
    """Validate the directory; relative CLI/env paths use the launch CWD.

    TOML candidate_root must be absolute (a leading '~' is accepted), so
    the meaning of a saved configuration does not depend on CWD.
    """
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ConfigurationError(f"{label} must specify a nonempty directory path")
    raw = Path(value).expanduser()
    if label == "config.toml" and not raw.is_absolute():
        raise ConfigurationError(
            "[storage].candidate_root must be absolute (or begin with '~')"
        )
    if not raw.is_absolute():
        raw = basedir / raw
    try:
        resolved = raw.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ConfigurationError(
            f"Cannot resolve {label} workspace {raw}: {exc}"
        ) from exc
    if not resolved.is_dir():
        raise ConfigurationError(f"{label} workspace is not a directory: {resolved}")
    return resolved


def load_config(
    *,
    workspace: str | Path | None = None,
    config_path: str | Path | None = None,
    cwd: str | Path | None = None,
    app_root: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    interactive: bool | None = None,
    input_fn: Callable[[str], str] = input,
) -> AgentConfig:
    """Resolve and validate paths; never create or edit files.

    ``interactive=None`` prompts only when stdin is a TTY and TOML allows
    it.  Call this before constructing a Deep Agent or its backend.

    Invalid configured roots are errors, not silent fallbacks to another
    directory: this avoids accidentally operating on the wrong candidate.
    """
    env = os.environ if environ is None else environ
    launch_dir = Path.cwd() if cwd is None else Path(cwd).expanduser()
    launch_dir = launch_dir.resolve(strict=True)
    if not launch_dir.is_dir():
        raise ConfigurationError(f"Current working directory is invalid: {launch_dir}")

    physical_app_root = (
        Path(__file__).resolve().parent
        if app_root is None
        else Path(app_root).expanduser().resolve(strict=True)
    )
    if not physical_app_root.is_dir():
        raise ConfigurationError(
            f"Application root is not a directory: {physical_app_root}"
        )

    location = (
        default_config_path(environ=env)
        if config_path is None
        else Path(config_path).expanduser()
    )
    if not location.is_absolute():
        location = launch_dir / location
    location = location.resolve(strict=False)
    settings = _read_toml(location)
    behavior = settings.get("behavior", {})
    use_cwd = _bool_setting(behavior, "allow_cwd_workspace", True)
    allow_prompt = _bool_setting(behavior, "prompt_if_missing", True)

    if workspace is not None:
        choice: str | Path = workspace
        source: Source = "argument"
    elif env.get(WORKSPACE_ENV, "").strip():
        choice = env[WORKSPACE_ENV]
        source = "environment"
    elif settings.get("storage", {}).get("candidate_root") is not None:
        choice = settings["storage"]["candidate_root"]
        source = "config"
    elif use_cwd and (launch_dir / WORKSPACE_MARKER).is_file():
        choice = launch_dir
        source = "cwd"
    else:
        should_prompt = sys.stdin.isatty() if interactive is None else interactive
        if not (allow_prompt and should_prompt):
            raise ConfigurationError(
                "No candidate workspace configured. Pass --workspace PATH, "
                f"set {WORKSPACE_ENV}, add [storage].candidate_root to "
                f"{location}, or place a file named {WORKSPACE_MARKER} in "
                "the current directory."
            )
        try:
            choice = input_fn("Candidate workspace directory: ").strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise ConfigurationError("Workspace selection cancelled") from exc
        source = "prompt"

    root = _workspace_path(
        choice,
        basedir=launch_dir,
        label="config.toml" if source == "config" else source,
    )
    return AgentConfig(
        app_root=physical_app_root,
        workspace_root=root,
        config_path=location,
        workspace_source=source,
    )


def save_initial_config(config: AgentConfig) -> Path:
    """Create config.toml only if it does not exist; never overwrite.

    Call after obtaining the user's consent to remember the workspace.
    Creates ~/.config/resume-agent with mode 0700 (where supported) and
    config.toml with mode 0600. The path is configuration, not a secret.
    """
    target = config.config_path
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    content = (
        f"schema_version = {SCHEMA_VERSION}\n\n"
        "[storage]\n"
        f"candidate_root = {json.dumps(str(config.workspace_root))}\n\n"
        "[behavior]\n"
        "prompt_if_missing = true\n"
        "allow_cwd_workspace = true\n"
    )
    # Exclusive create prevents accidentally replacing user-edited settings.
    try:
        with target.open("x", encoding="utf-8") as stream:
            os.chmod(target, 0o600)
            stream.write(content)
    except FileExistsError as exc:
        raise ConfigurationError(
            f"Configuration already exists; edit it explicitly: {target}"
        ) from exc
    return target
