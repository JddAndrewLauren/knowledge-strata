"""``strata init`` and ``strata index`` (design.md "cli"; issue #33).

Every test gets its own fake ``$HOME`` (``cli.Path.home`` and the real
``HOME`` env var both point at it, so ``git config --global`` and the
user-level skill/agent installs never touch the machine running the suite)
and its own project folder under ``tmp_path``, never the clone itself
(CONTEXT.md "Hermetic"). The embedder is always the fake one; a real
:class:`~strata.embeddings.FastEmbedEmbedder` is never constructed here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from strata import cli, config
from strata.embeddings import FakeEmbedder

ROOT = Path(__file__).resolve().parent.parent
WEST_DESK = ROOT / "examples" / "west-desk"
SKILL_ASSET = ROOT / "src" / "strata" / "assets" / "skill" / "SKILL.md"
READER_ASSET = ROOT / "src" / "strata" / "assets" / "agents" / "strata-reader.md"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake, empty ``$HOME``: no ``.gitconfig`` (exercises the repo-local
    identity fallback by default, the way a fresh CI runner would too), no
    ``~/.claude``. Both the Python-level and the subprocess-level notion of
    "home" point here, so nothing this test does can reach the real one."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(cli.Path, "home", lambda: fake_home)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    return fake_home


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    return folder


def _make_source(folder: Path, name: str = "a.txt", text: str = "16 May 2001\n\nOne dated paragraph.\n") -> Path:
    sources_dir = folder / "sources"
    sources_dir.mkdir(exist_ok=True)
    (sources_dir / name).write_text(text, encoding="utf-8")
    return sources_dir


def _init(project: Path, *, corpus=(), manuscript=None) -> int:
    return cli.cmd_init(project, corpus=list(corpus), manuscript=manuscript, embedder=FakeEmbedder())


def _index(project: Path) -> tuple[int, str, str]:
    """Run ``cmd_index`` capturing exactly what it prints (its whole
    contract on success), independent of pytest's own stdout capture."""
    import io
    import contextlib

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        status = cli.cmd_index(project, embedder=FakeEmbedder())
    return status, out.getvalue(), err.getvalue()


def _git(args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


# -- bare init in a fresh folder: the one error case ---------------------------


def test_bare_init_in_fresh_folder_is_an_error(project, home, capsys):
    status = _init(project)
    assert status != 0
    err = capsys.readouterr().err
    assert "strata init" in err
    assert not (project / ".strata").exists()


# -- first run: every row of the table -----------------------------------------


def test_first_run_writes_every_row_of_the_table(project, home):
    sources_dir = _make_source(project)
    status = _init(project, corpus=["./sources"])
    assert status == 0

    cfg = config.load(project)
    assert cfg.corpus == ("./sources",)
    assert cfg.manuscript is None

    assert (project / ".strata" / "ledger.db").exists()
    assert (project / ".strata" / "cache" / "index.db").exists()

    gitignore = (project / ".gitignore").read_text(encoding="utf-8")
    assert ".strata/cache/" in gitignore.splitlines()

    mcp = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["strata"] == {"command": "strata", "args": ["serve", str(project.resolve())]}

    settings = json.loads((project / ".claude" / "settings.json").read_text(encoding="utf-8"))
    allow = settings["permissions"]["allow"]
    for permission in (
        "mcp__strata__search",
        "mcp__strata__read",
        "Bash(git add:*)",
        "Bash(git commit:*)",
    ):
        assert permission in allow

    project_note = project / "notes" / "project.md"
    assert project_note.read_text(encoding="utf-8") == f"# {project.name}\n"

    assert (project / ".git").is_dir()
    log = _git(["log", "--oneline"], cwd=project)
    assert len(log.stdout.strip().splitlines()) == 1

    skill = home / ".claude" / "skills" / "strata" / "SKILL.md"
    reader = home / ".claude" / "agents" / "strata-reader.md"
    assert skill.read_text(encoding="utf-8") == SKILL_ASSET.read_text(encoding="utf-8")
    assert reader.read_text(encoding="utf-8") == READER_ASSET.read_text(encoding="utf-8")

    assert sources_dir.exists()  # nothing under the corpus root was touched
    assert {p.name for p in sources_dir.iterdir()} == {"a.txt"}

    assert (home / ".strata" / "cache" / "store.db").exists()  # reused, not this issue's to create


# -- other .mcp.json servers and settings.json rules survive --------------------


def test_other_mcp_servers_and_settings_rules_survive(project, home):
    (project / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "other-server"}}}), encoding="utf-8"
    )
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(ls:*)"]}}, indent=2), encoding="utf-8"
    )

    _make_source(project)
    assert _init(project, corpus=["./sources"]) == 0
    assert _init(project) == 0  # a bare refresh too

    mcp = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["other"] == {"command": "other-server"}
    assert "strata" in mcp["mcpServers"]

    settings = json.loads((project / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "Bash(ls:*)" in settings["permissions"]["allow"]
    assert "mcp__strata__search" in settings["permissions"]["allow"]


# -- flags are the whole truth --------------------------------------------------


def test_second_flagged_run_replaces_the_corpus_list(project, home):
    _make_source(project, "a.txt")
    other_dir = project / "other"
    other_dir.mkdir()
    (other_dir / "b.txt").write_text("16 May 2001\n\nOther paragraph.\n", encoding="utf-8")

    assert _init(project, corpus=["./sources"]) == 0
    assert config.load(project).corpus == ("./sources",)

    assert _init(project, corpus=["./other"]) == 0
    assert config.load(project).corpus == ("./other",)


def test_bare_refresh_leaves_config_untouched(project, home):
    _make_source(project)
    assert _init(project, corpus=["./sources"], manuscript=None) == 0
    before = (project / ".strata" / "config.yaml").read_text(encoding="utf-8")

    assert _init(project) == 0  # no flags: untouched

    after = (project / ".strata" / "config.yaml").read_text(encoding="utf-8")
    assert before == after


# -- bare refresh re-syncs and rewrites the user-level files -------------------


def test_bare_refresh_resyncs_and_restores_an_altered_skill_leaving_an_identical_one_untouched(project, home):
    _make_source(project)
    assert _init(project, corpus=["./sources"]) == 0

    skill = home / ".claude" / "skills" / "strata" / "SKILL.md"
    reader = home / ".claude" / "agents" / "strata-reader.md"
    reader_mtime_before = reader.stat().st_mtime_ns

    skill.write_text("tampered content\n", encoding="utf-8")
    assert _init(project) == 0  # bare refresh

    assert skill.read_text(encoding="utf-8") == SKILL_ASSET.read_text(encoding="utf-8")
    # An identical file (the reader agent, untouched by this test) keeps its
    # mtime - "rewritten only when content differs" (design.md).
    assert reader.stat().st_mtime_ns == reader_mtime_before


def test_project_note_is_never_rewritten(project, home):
    _make_source(project)
    assert _init(project, corpus=["./sources"]) == 0
    note = project / "notes" / "project.md"
    note.write_text("# hand-edited\n\nSomething the user wrote.\n", encoding="utf-8")

    assert _init(project) == 0  # bare refresh
    assert note.read_text(encoding="utf-8") == "# hand-edited\n\nSomething the user wrote.\n"


def test_gitignore_created_once_and_other_lines_survive(project, home):
    _make_source(project)
    assert _init(project, corpus=["./sources"]) == 0
    gitignore_path = project / ".gitignore"
    gitignore_path.write_text(gitignore_path.read_text(encoding="utf-8") + "*.log\n", encoding="utf-8")

    assert _init(project) == 0  # bare refresh: no change beyond what the user added
    text = gitignore_path.read_text(encoding="utf-8")
    assert text.count(".strata/cache/") == 1
    assert "*.log" in text.splitlines()


# -- git: init only outside a repo, local identity fallback, one commit --------


def test_git_init_only_outside_an_existing_repo(tmp_path, home):
    outer = tmp_path / "outer"
    outer.mkdir()
    _git(["init"], cwd=outer)
    project = outer / "sub-project"
    project.mkdir()
    _make_source(project)

    assert _init(project, corpus=["./sources"]) == 0

    assert not (project / ".git").exists()  # no nested repo created
    assert (outer / ".git").is_dir()
    log = _git(["log", "--oneline"], cwd=outer)
    assert len(log.stdout.strip().splitlines()) == 1


def test_no_global_identity_uses_a_local_fallback_and_commits(project, home):
    # `home` has no .gitconfig, so git has no global identity to find.
    _make_source(project)
    assert _init(project, corpus=["./sources"]) == 0

    name = _git(["config", "user.name"], cwd=project)
    email = _git(["config", "user.email"], cwd=project)
    assert name.stdout.strip()
    assert email.stdout.strip()
    log = _git(["log", "--oneline"], cwd=project)
    assert len(log.stdout.strip().splitlines()) == 1


def test_second_run_commits_only_if_something_changed(project, home):
    _make_source(project)
    assert _init(project, corpus=["./sources"]) == 0
    first_log = _git(["log", "--oneline"], cwd=project).stdout.strip().splitlines()

    assert _init(project) == 0  # bare refresh, nothing changed
    second_log = _git(["log", "--oneline"], cwd=project).stdout.strip().splitlines()
    assert len(second_log) == len(first_log)  # no empty commit


# -- config on a copy of examples/west-desk (acceptance criterion) -------------


def test_west_desk_config_has_exactly_the_typed_paths(tmp_path, home):
    project = tmp_path / "project"
    shutil.copytree(WEST_DESK / "sources", project / "sources")
    shutil.copytree(WEST_DESK / "manuscript", project / "manuscript")

    assert _init(project, corpus=["./sources"], manuscript="./manuscript") == 0

    raw = yaml.safe_load((project / ".strata" / "config.yaml").read_text(encoding="utf-8"))
    assert raw == {"corpus": ["./sources"], "manuscript": "./manuscript"}

    (project / "more").mkdir()
    assert _init(project, corpus=["./sources", "./more"]) == 0
    raw2 = yaml.safe_load((project / ".strata" / "config.yaml").read_text(encoding="utf-8"))
    assert raw2 == {"corpus": ["./sources", "./more"]}  # manuscript dropped: not given this run


def test_config_with_an_unknown_key_names_it(tmp_path, home):
    project = tmp_path / "project"
    project.mkdir()
    _make_source(project)
    assert _init(project, corpus=["./sources"]) == 0
    config_path = project / ".strata" / "config.yaml"
    config_path.write_text(config_path.read_text(encoding="utf-8") + "bogus: true\n", encoding="utf-8")

    with pytest.raises(config.ConfigError, match="bogus"):
        config.load(project)

    status, _, err = _index(project)
    assert status != 0
    assert "bogus" in err


# -- interruption, resumption, and the server reporting incomplete in between --


def test_interrupted_first_index_resumes_and_a_server_call_in_between_reports_incomplete(project, home, monkeypatch):
    import asyncio

    from mcp import Client

    from strata import dating
    from strata.ledger import Ledger
    from strata.server import Project, build_server

    # Config is written directly, bypassing `cmd_init`: this test is about
    # `_sync_project`'s own interruption handling, not the rest of setup.
    # Three units, sorted so "a.txt" (SRC-000001) is aligned before the
    # failure on "b.txt" - real, ledger-durable progress, not just a flag
    # (ADR-0001: alignment commits per unit).
    sources_dir = _make_source(project, "a.txt", "16 May 2001\n\nFirst paragraph.\n")
    (sources_dir / "b.txt").write_text("16 May 2001\n\nSecond paragraph.\n", encoding="utf-8")
    (sources_dir / "c.txt").write_text("16 May 2001\n\nThird paragraph.\n", encoding="utf-8")
    config_path = project / ".strata" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("corpus:\n  - ./sources\n", encoding="utf-8")

    should_fail = [True]
    real_date = dating.date

    def _boom(raw_unit):
        if should_fail[0] and raw_unit.path == "b.txt":
            raise RuntimeError("injected failure mid-sync")
        return real_date(raw_unit)

    monkeypatch.setattr(dating, "date", _boom)

    status = cli.cmd_index(project, embedder=FakeEmbedder())
    assert status != 0  # the CLI call itself is interrupted

    # "a.txt" (sorted before the failing "b.txt") already has a live anchor,
    # durably committed - the resumable state, independent of index.db.
    ledger = Ledger(project / ".strata" / "ledger.db")
    try:
        assert ledger.live_anchors("SRC-000001") == [1, 2]  # "16 May 2001" and "First paragraph."
    finally:
        ledger.close()

    # A server call "in between" - same injected failure still active -
    # reports the project as incomplete rather than crashing or pretending.
    server_project = Project(folder=project, embedder=FakeEmbedder(), cache_db=project / "store.db")

    async def _search():
        async with Client(build_server(server_project)) as mcp_client:
            return await mcp_client.call_tool("search", {})

    result = asyncio.run(_search())
    assert not result.is_error
    text = result.content[0].text
    assert "incomplete" in text

    should_fail[0] = False
    status2 = cli.cmd_index(project, embedder=FakeEmbedder())
    assert status2 == 0  # the next run reports the remainder and finishes


# -- `strata index`: one drift line per changed record, nothing else -----------


def test_index_prints_one_drift_line_per_changed_record_and_nothing_else(project, home):
    sources_dir = _make_source(project, "a.txt", "16 May 2001\n\nOriginal paragraph text.\n")
    (sources_dir / "b.txt").write_text("16 May 2001\n\nUnrelated other paragraph.\n", encoding="utf-8")
    config_path = project / ".strata" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("corpus:\n  - ./sources\n", encoding="utf-8")

    status, out, err = _index(project)  # first index: progress, not drift
    assert status == 0
    assert "first index complete" in out
    assert err == ""

    status, out, err = _index(project)  # nothing changed: nothing printed
    assert status == 0
    assert out == ""
    assert err == ""

    (sources_dir / "a.txt").write_text("16 May 2001\n\nEdited paragraph text.\n", encoding="utf-8")
    status, out, err = _index(project)
    assert status == 0
    assert err == ""
    lines = out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("SRC-000001:")
    assert "1 retired" in lines[0] and "1 added" in lines[0]


# -- dispatch ---------------------------------------------------------------------


def test_main_dispatches_init_index_and_serve_and_rejects_unknown(project, home, monkeypatch, capsys):
    monkeypatch.chdir(project)
    assert cli.main([]) == 2
    assert "usage" in capsys.readouterr().err

    assert cli.main(["bogus"]) == 2

    assert cli.main(["init"]) != 0  # bare, fresh folder: the error case
    capsys.readouterr()

    assert cli.main(["serve"]) == 2  # delegates to strata.server.main, which rejects this shape
    assert "usage: strata serve" in capsys.readouterr().err
