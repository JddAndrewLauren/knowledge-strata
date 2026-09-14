"""strata.cli: ``strata init`` and ``strata index`` (design.md "cli"; issue
#33), plus dispatch to ``strata serve`` (:mod:`strata.server`, untouched).

``strata init --corpus P [--corpus P ...] [--manuscript M]`` sets up (or
refreshes) the project folder named in design.md "The user's experience":
``.strata/config.yaml``, ``.gitignore``, ``.mcp.json``, ``.claude/
settings.json``, ``notes/project.md``, the git repository, the user-level
skill and reader agent, then the first index. ``strata index`` runs the same
fresh-as-of-this-call sync (:mod:`strata.server`'s "Freshness") by hand and
prints drift.

Flags are the whole truth (CONTEXT.md): every ``strata init`` call that
carries a flag rewrites ``.strata/config.yaml`` from exactly what was typed
this run, corpus included even when empty. A bare call in an initialized
folder is the refresh - re-sync plus rewriting the user-level files - and
never touches ``config.yaml``, ``notes/project.md`` or an already-present
``.gitignore`` line. A bare call in a fresh folder has nothing to write and
is the one error case (design.md: "the one line to type").
"""

from __future__ import annotations

import json
import subprocess
import sys
from importlib import resources
from pathlib import Path

from strata import config
from strata.corpus import manuscript, notes, sources
from strata.embeddings import Embedder, FastEmbedEmbedder
from strata.index import Index
from strata.ledger import Ledger
from strata.record import Record

_GITIGNORE_LINE = ".strata/cache/"
_ALLOWED_PERMISSIONS = (
    "mcp__strata__search",
    "mcp__strata__read",
    "Bash(git add:*)",
    "Bash(git commit:*)",
)
_GIT_IDENTITY_FALLBACK = (("user.name", "strata"), ("user.email", "strata@localhost"))
_COMMIT_MESSAGE = "strata init"
_USAGE = "usage: strata init [--corpus PATH ...] [--manuscript PATH] | strata index | strata serve <project-folder>"


# -- entry point ---------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "serve":
        from strata import server

        return server.main(argv)
    if argv and argv[0] == "init":
        try:
            corpus, manuscript_path = _parse_init_args(argv[1:])
        except ValueError as error:
            print(f"strata: {error}", file=sys.stderr)
            return 2
        return cmd_init(Path.cwd(), corpus=corpus, manuscript=manuscript_path)
    if argv == ["index"]:
        return cmd_index(Path.cwd())
    print(_USAGE, file=sys.stderr)
    return 2


def _parse_init_args(argv: list[str]) -> tuple[list[str], str | None]:
    corpus: list[str] = []
    manuscript_path: str | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--corpus":
            i += 1
            if i >= len(argv):
                raise ValueError("--corpus needs a path")
            corpus.append(argv[i])
        elif arg == "--manuscript":
            i += 1
            if i >= len(argv):
                raise ValueError("--manuscript needs a path")
            manuscript_path = argv[i]
        else:
            raise ValueError(f"unrecognized argument: {arg!r}")
        i += 1
    return corpus, manuscript_path


# -- strata init -----------------------------------------------------------------


def cmd_init(
    folder: Path,
    *,
    corpus: list[str],
    manuscript: str | None,
    embedder: Embedder | None = None,
) -> int:
    """``strata init`` in ``folder`` (design.md "The user's experience"),
    with whatever ``--corpus``/``--manuscript`` flags were typed this run.
    ``embedder`` is a test seam (``None`` builds the real
    :class:`~strata.embeddings.FastEmbedEmbedder`, as `strata.server` does)."""
    config_path = folder / ".strata" / "config.yaml"
    initialized = config_path.exists()
    any_flag_given = bool(corpus) or manuscript is not None
    if not initialized and not any_flag_given:
        print(
            "strata: nothing to initialize here; run `strata init --corpus <path> "
            "[--corpus <path> ...] [--manuscript <path>]`",
            file=sys.stderr,
        )
        return 1

    if any_flag_given:
        config.write(folder, corpus=corpus, manuscript=manuscript)
    cfg = config.load(folder)

    _ensure_gitignore(folder)
    _merge_mcp_json(folder)
    _merge_claude_settings(folder)
    _ensure_project_note(folder)
    _install_user_level_assets()
    _ensure_git_repo(folder)

    status = _sync_project(folder, cfg, report_drift=False, embedder=embedder)
    _git_commit_if_changed(folder)
    return status


def _ensure_gitignore(folder: Path) -> None:
    path = folder / ".gitignore"
    if not path.exists():
        path.write_text(_GITIGNORE_LINE + "\n", encoding="utf-8")
        return
    text = path.read_text(encoding="utf-8")
    if _GITIGNORE_LINE in text.splitlines():
        return
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + _GITIGNORE_LINE + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _merge_mcp_json(folder: Path) -> None:
    path = folder / ".mcp.json"
    data = _read_json(path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers["strata"] = {"command": "strata", "args": ["serve", str(folder.resolve())]}
    data["mcpServers"] = servers
    _write_json(path, data)


def _merge_claude_settings(folder: Path) -> None:
    path = folder / ".claude" / "settings.json"
    data = _read_json(path)
    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
    allow = permissions.get("allow")
    if not isinstance(allow, list):
        allow = []
    for permission in _ALLOWED_PERMISSIONS:
        if permission not in allow:
            allow.append(permission)
    permissions["allow"] = allow
    data["permissions"] = permissions
    _write_json(path, data)


def _ensure_project_note(folder: Path) -> None:
    path = folder / "notes" / "project.md"
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {folder.name}\n", encoding="utf-8")


def _write_if_changed(path: Path, content: str) -> None:
    """Rewrite ``path`` only when its content differs, so an identical file
    keeps its mtime (design.md "refresh"; issue #33's acceptance: an
    unaltered installed skill is left untouched, an altered one restored)."""
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _install_user_level_assets() -> None:
    assets = resources.files("strata") / "assets"
    skill_text = (assets / "skill" / "SKILL.md").read_text(encoding="utf-8")
    reader_text = (assets / "agents" / "strata-reader.md").read_text(encoding="utf-8")
    _write_if_changed(Path.home() / ".claude" / "skills" / "strata" / "SKILL.md", skill_text)
    _write_if_changed(Path.home() / ".claude" / "agents" / "strata-reader.md", reader_text)


def _git(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _ensure_git_repo(folder: Path) -> None:
    """``git init`` only when ``folder`` is not already part of a repository
    (a parent's, a worktree's, or its own) - design.md "git": init if not
    one. A repo-local ``user.name``/``user.email`` fallback covers the
    (also per-repo) case where no global identity is configured, so the
    commit below never fails on that account."""
    inside = _git(["rev-parse", "--is-inside-work-tree"], cwd=folder)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        _git(["init"], cwd=folder)
    for key, fallback in _GIT_IDENTITY_FALLBACK:
        result = _git(["config", "--global", "--get", key])
        if result.returncode != 0 or not result.stdout.strip():
            _git(["config", key, fallback], cwd=folder)


def _git_commit_if_changed(folder: Path) -> None:
    """Stage everything under ``folder`` (never outside it - corpus roots
    are external, CONTEXT.md "Corpus") and commit only if that changed
    something (design.md "git": one commit first run, "commit only if
    something changed" on a refresh)."""
    _git(["add", "-A", "."], cwd=folder)
    status = _git(["status", "--porcelain"], cwd=folder)
    if status.stdout.strip():
        _git(["commit", "-m", _COMMIT_MESSAGE], cwd=folder)


# -- strata index ------------------------------------------------------------------


def cmd_index(folder: Path, *, embedder: Embedder | None = None) -> int:
    """``strata index``: sync ``folder`` by hand (design.md "cli")."""
    try:
        cfg = config.load(folder)
    except config.ConfigError as error:
        print(f"strata: {error}", file=sys.stderr)
        return 1
    return _sync_project(folder, cfg, report_drift=True, embedder=embedder)


def _resolve(folder: Path, maybe_relative: str) -> Path:
    path = Path(maybe_relative)
    return path if path.is_absolute() else folder / path


def _sync_project(
    folder: Path,
    cfg: config.ProjectConfig,
    *,
    report_drift: bool,
    embedder: Embedder | None,
) -> int:
    """Fresh-as-of-this-call sync (design.md "Freshness"), driven by the CLI
    instead of a live server call, mirroring :mod:`strata.server`'s own
    catch-and-report shape: a failure during a still-incomplete first index
    hands whatever was already collected to ``index.sync(..., complete=
    False)`` - real, ledger-durable progress, not just a flag, and resumable
    because :meth:`~strata.ledger.Ledger.align` is a per-unit no-op on
    unchanged content next time - while a failure after the first index has
    already finished never retracts that last-good, complete index.

    Prints progress (units seen, converted, embedded paragraphs) while the
    first index is not yet complete; once it is, ``strata index`` prints one
    drift line per changed source record and nothing else on success
    (CONTEXT.md "Drift") when ``report_drift`` is set - ``strata init``'s own
    refresh sync stays quiet, its output reserved for setup.
    """
    ledger = Ledger(folder / ".strata" / "ledger.db")
    index_path = folder / ".strata" / "cache" / "index.db"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index = Index(index_path, ledger=ledger, embedder=embedder or FastEmbedEmbedder(), chunk_tokens=cfg.chunk_tokens)
    was_complete = index.indexing_state == "complete"
    records: list[Record] = []
    source_report: sources.SyncReport | None = None
    try:
        try:
            roots = [_resolve(folder, root) for root in cfg.corpus]
            source_report = sources.sync(roots, ledger)
            records.extend(source_report.records)
            notes_folder = folder / "notes"
            if notes_folder.is_dir():
                records.extend(notes.read(notes_folder))
            if cfg.manuscript:
                records.extend(manuscript.read(_resolve(folder, cfg.manuscript)).records)
        except Exception as error:
            if was_complete:
                print(f"strata: sync failed, serving the last complete index: {error}", file=sys.stderr)
            else:
                index.sync(records, complete=False)
                print(f"strata: indexing incomplete: {error}", file=sys.stderr)
            return 1

        index.sync(records, complete=True)
        if not was_complete:
            seen = len(source_report.records) + len(source_report.skipped)
            embedded = sum(len(record.paragraphs) for record in records)
            print(
                f"strata: first index complete: {seen} units seen, "
                f"{len(source_report.records)} converted, {embedded} embedded"
            )
        elif report_drift:
            for aligned in source_report.aligned:
                if aligned.changed:
                    print(aligned.line())
        return 0
    finally:
        index.close()
        ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
