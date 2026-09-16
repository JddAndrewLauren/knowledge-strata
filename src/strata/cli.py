"""strata.cli: ``strata init`` and ``strata index`` (design.md "cli"; issue
#33), plus dispatch to ``strata serve`` (:mod:`strata.server`, untouched).

``strata init --corpus P [--corpus P ...] [--manuscript M]`` sets up (or
refreshes) the project folder named in design.md "The user's experience":
``.strata/config.yaml``, ``.gitignore``, ``.mcp.json``, ``.claude/
settings.json``, ``notes/project.md``, the git repository, the user-level
skill and reader agent, then the first index. ``strata index`` runs the same
fresh-as-of-this-call sync the server runs per call (:meth:`strata.project.Project.current`)
by hand and prints drift.

Flags are the whole truth (CONTEXT.md): every ``strata init`` call that
carries a flag rewrites ``.strata/config.yaml`` from what was typed this
run - ``--corpus`` replaces the list, a ``--manuscript`` not re-typed is
dropped - except that ``--manuscript`` alone keeps the corpus list already
on file, since an empty corpus cannot be typed. A bare call in an initialized
folder is the refresh - re-sync plus rewriting the user-level files - and
never touches ``config.yaml``, ``notes/project.md`` or an already-present
``.gitignore`` line. A bare call in a fresh folder has nothing to write and
is the one error case (design.md: "the one line to type").
"""

from __future__ import annotations

import json
import os
import tempfile
import yaml
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

from strata import config
from strata.embeddings import Embedder
from strata.project import Project, RefreshFailed, project_lock, resolve

_GIT_IDENTITY_FALLBACK = (("user.name", "strata"), ("user.email", "strata@localhost"))
_COMMIT_MESSAGE = "strata init"
_USAGE = "usage: strata init [--corpus PATH ...] [--manuscript PATH] | strata index | strata serve <project-folder>"


def _atomic_write(path: Path, text: str):
    if path.exists() and path.read_text(encoding='utf-8') == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _json(path: Path):
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'{path} must contain a JSON object')
    return value


def _resource(name: str):
    assets = resources.files('strata') / 'assets'
    path = assets / ('skill/SKILL.md' if name == 'SKILL.md' else 'agents/strata-reader.md')
    return path.read_text(encoding='utf-8')


def initialize(project: str | Path, *, corpus=None, manuscript=None, host_home=None, replace_paths=False):
    project = Path(project).resolve()
    project.mkdir(parents=True, exist_ok=True)
    with project_lock(project):
        config_path = project / '.strata' / 'config.yaml'
        settings = (yaml.safe_load(config_path.read_text(encoding='utf-8')) or {}) if config_path.exists() else {}
        if config_path.exists():
            config.load(project)
        if corpus is not None:
            settings['corpus'] = list(corpus)
        if replace_paths:
            settings.pop('manuscript', None)
        if manuscript is not None:
            settings['manuscript'] = manuscript
        config.validate(settings)
        if not settings.get('corpus'):
            raise ValueError('first initialization requires --corpus PATH')
        for raw in settings['corpus']:
            root = resolve(project, raw)
            if not root.is_dir():
                raise ValueError(f'corpus root is unavailable: {root}')
            if project.is_relative_to(root):
                raise ValueError('the project cannot be inside a corpus root (its own notes would become sources)')
        if settings.get('manuscript') and not resolve(project, settings['manuscript']).is_dir():
            raise ValueError('manuscript must name an existing folder')
        mcp_path, permissions_path = project / '.mcp.json', project / '.claude' / 'settings.json'
        mcp, permissions = _json(mcp_path), _json(permissions_path)
        servers = mcp.setdefault('mcpServers', {})
        if not isinstance(servers, dict):
            raise ValueError('mcpServers must be an object')
        servers['strata'] = {'command': 'strata', 'args': ['serve', str(project)]}
        rules = permissions.setdefault('permissions', {})
        if not isinstance(rules, dict) or not isinstance(rules.setdefault('allow', []), list):
            raise ValueError('permissions.allow must be a list')
        for rule in ('mcp__strata__search', 'mcp__strata__read', 'Bash(git add:*)', 'Bash(git commit:*)'):
            if rule not in rules['allow']:
                rules['allow'].append(rule)
        if settings.get('manuscript'):
            directories = rules.setdefault('additionalDirectories', [])
            if not isinstance(directories, list):
                raise ValueError('permissions.additionalDirectories must be a list')
            directory = str(resolve(project, settings['manuscript']))
            if directory not in directories:
                directories.append(directory)
        enabled = permissions.setdefault('enabledMcpjsonServers', [])
        if not isinstance(enabled, list):
            raise ValueError('enabledMcpjsonServers must be a list')
        if 'strata' not in enabled:
            enabled.append('strata')
        skill, reader = _resource('SKILL.md'), _resource('strata-reader.md')
        existing = subprocess.run(['git', '-C', str(project), 'rev-parse', '--show-toplevel'],
                                  capture_output=True, text=True)
        if existing.returncode:
            subprocess.run(['git', 'init', str(project)], check=True, capture_output=True)
        _atomic_write(config_path, yaml.safe_dump(settings, sort_keys=False))
        _atomic_write(mcp_path, json.dumps(mcp, indent=2) + '\n')
        _atomic_write(permissions_path, json.dumps(permissions, indent=2) + '\n')
        ignore = project / '.gitignore'
        contents = ignore.read_text(encoding='utf-8') if ignore.exists() else ''
        for entry in ('.strata/cache/', '.strata/refresh.lock'):
            if entry not in contents.splitlines():
                contents = contents.rstrip('\n') + '\n' + entry + '\n'
        _atomic_write(ignore, contents.lstrip('\n'))
        overview = project / 'notes' / 'project.md'
        if not overview.exists():
            _atomic_write(overview, f'# {project.name}\n')
        host = Path(host_home) if host_home else Path.home() / '.claude'
        _atomic_write(host / 'skills' / 'strata' / 'SKILL.md', skill)
        _atomic_write(host / 'agents' / 'strata-reader.md', reader)
    return project



# -- entry point ---------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except KeyboardInterrupt:
        print('strata: indexing interrupted; run strata index to retry', file=sys.stderr)
        return 130
    except (ValueError, OSError, RefreshFailed) as error:
        message = f'strata: {error}'.encode('ascii', errors='backslashreplace').decode('ascii')
        print(message, file=sys.stderr)
        return 1


def _main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ['--help'] or argv == ['-h']:
        print(_USAGE)
        return 0
    folder = Path.cwd()
    argv = list(argv)
    if '--project' in argv:
        position = argv.index('--project')
        if position + 1 >= len(argv):
            print('--project needs a path', file=sys.stderr)
            return 2
        folder = Path(argv[position + 1]).resolve()
        del argv[position:position + 2]
        if argv == ['serve']:
            argv.append(str(folder))
    legacy_roots = []
    while '--legacy-root' in argv:
        position = argv.index('--legacy-root')
        if position + 1 >= len(argv):
            print('--legacy-root needs a path', file=sys.stderr)
            return 2
        legacy_roots.append(resolve(folder, argv[position + 1]))
        del argv[position:position + 2]
    if argv and argv[0] == "serve":
        from strata import server

        return server.main(argv)
    if argv and argv[0] == "init":
        try:
            corpus, manuscript_path = _parse_init_args(argv[1:])
        except ValueError as error:
            print(f"strata: {error}", file=sys.stderr)
            return 2
        return cmd_init(folder, corpus=corpus, manuscript=manuscript_path, legacy_roots=legacy_roots or None)
    if argv == ["index"]:
        return cmd_index(folder, legacy_roots=legacy_roots or None)
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
    legacy_roots=None,
) -> int:
    """``strata init`` in ``folder`` (design.md "The user's experience"),
    with whatever ``--corpus``/``--manuscript`` flags were typed this run.
    ``embedder`` is a test seam (``None`` builds the real
    :class:`~strata.embeddings.FastEmbedEmbedder`, as `strata.server` does)."""
    folder.mkdir(parents=True, exist_ok=True)
    owned = ['.gitignore', '.mcp.json', '.claude/settings.json', '.strata/config.yaml',
             '.strata/ledger.db', 'notes/project.md']
    before = {name: (folder / name).read_bytes() if (folder / name).exists() else None for name in owned}
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
    if shutil.which("git") is None:
        print("strata: git was not found on PATH; install git and rerun strata init", file=sys.stderr)
        return 1

    initialize(folder, corpus=list(corpus) if corpus else None,
               manuscript=manuscript, replace_paths=any_flag_given)
    cfg = config.load(folder)

    _ensure_git_repo(folder)
    status = _sync_project(folder, cfg, report_drift=False, embedder=embedder, legacy_roots=legacy_roots)
    _git_commit_if_changed(folder, before)
    return status


def _git(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _ensure_git_repo(folder: Path) -> None:
    """``git init`` only when ``folder`` is not already part of a repository
    (a parent's, a worktree's, or its own) - design.md "git": init if not
    one. A repo-local ``user.name``/``user.email`` fallback covers the
    (also per-repo) case where git resolves no identity at all - system,
    global or local - so the commit below never fails on that account."""
    inside = _git(["rev-parse", "--is-inside-work-tree"], cwd=folder)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        _git(["init"], cwd=folder)
    for key, fallback in _GIT_IDENTITY_FALLBACK:
        result = _git(["config", "--get", key], cwd=folder)
        if result.returncode != 0 or not result.stdout.strip():
            _git(["config", key, fallback], cwd=folder)


def _git_commit_if_changed(folder: Path, before: dict[str, bytes | None]) -> None:
    """Commit only setup-owned changes and preserve all unrelated staging."""
    changed = [name for name, old in before.items()
               if (folder / name).exists() and (folder / name).read_bytes() != old]
    if not changed:
        return
    staged = _git(['diff', '--cached', '--name-only'], cwd=folder)
    if staged.stdout.strip():
        print('strata: setup saved; existing staged changes left untouched, no commit made', file=sys.stderr)
        return
    result = _git(['add', '--', *changed], cwd=folder)
    if result.returncode == 0:
        result = _git(['commit', '--only', '-m', _COMMIT_MESSAGE, '--', *changed], cwd=folder)
    if result.returncode:
        print('strata: setup saved but commit failed; changes retained', file=sys.stderr)


# -- strata index ------------------------------------------------------------------


def cmd_index(folder: Path, *, embedder: Embedder | None = None, legacy_roots=None) -> int:
    """``strata index``: sync ``folder`` by hand (design.md "cli")."""
    try:
        cfg = config.load(folder)
    except config.ConfigError as error:
        print(f"strata: {error}", file=sys.stderr)
        return 1
    return _sync_project(folder, cfg, report_drift=True, embedder=embedder, legacy_roots=legacy_roots)


def _sync_project(
    folder: Path,
    cfg: config.ProjectConfig,
    *,
    report_drift: bool,
    embedder: Embedder | None,
    legacy_roots=None,
) -> int:
    """CLI entry to the same serialized refresh used by the server.

    First sync reports progress; later successful index calls report only drift.
    Any refresh failure marks the index incomplete and keeps the last snapshot
    unserved until a successful retry reconciles it with the durable ledger.
    """
    proj = Project(folder=folder, embedder=embedder)
    def progress(message):
        if getattr(proj, 'was_complete', None) is not True:
            print(message.encode('ascii', errors='backslashreplace').decode('ascii'), file=sys.stderr)
    proj.progress = progress
    try:
        with proj.current(legacy_roots=legacy_roots) as index:
            source_report = proj.report
            embedded = index._conn.execute('SELECT COUNT(*) FROM paragraphs').fetchone()[0]
    except RefreshFailed as error:
        print(f'strata: {error}', file=sys.stderr)
        return 1
    if not proj.was_complete:
        seen = len(source_report.records) + len(source_report.skipped)
        print(
            f"strata: first index complete: {seen} units seen, "
            f"{len(source_report.records)} converted, {embedded} embedded"
        )
    elif report_drift:
        for aligned in source_report.aligned:
            if aligned.changed:
                print(aligned.line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
