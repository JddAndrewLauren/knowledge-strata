"""One project setup command, refresh, and stdio server entry point."""
from __future__ import annotations

import argparse
from importlib.resources import files
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import yaml

from strata.project import Project, config, project_lock, resolve


def _atomic_write(path: Path, text: str):
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
    resource = files('strata').joinpath('resources', name)
    if resource.is_file():
        return resource.read_text(encoding='utf-8')
    # Source/editable checkout; wheel builds include the same canonical files.
    drafts = Path(__file__).resolve().parents[2] / 'docs' / 'drafts'
    path = drafts / ('skill/SKILL.md' if name == 'SKILL.md' else 'agents/strata-reader.md')
    return path.read_text(encoding='utf-8')


def initialize(project: str | Path, *, corpus=None, manuscript=None, host_home=None):
    project = Path(project).resolve()
    project.mkdir(parents=True, exist_ok=True)
    with project_lock(project):
        config_path = project / '.strata' / 'config.yaml'
        settings = config(project) if config_path.exists() else {}
        if corpus is not None:
            settings['corpus'] = list(corpus)
        if manuscript is not None:
            settings['manuscript'] = manuscript
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
        servers['strata'] = {'command': sys.executable,
                             'args': ['-m', 'strata', 'serve', '--project', str(project)]}
        rules = permissions.setdefault('permissions', {})
        if not isinstance(rules, dict) or not isinstance(rules.setdefault('allow', []), list):
            raise ValueError('permissions.allow must be a list')
        for rule in ('mcp__strata__search', 'mcp__strata__read', 'Bash(git add *)', 'Bash(git commit *)'):
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
            _atomic_write(overview, '# Project\n\nNo findings saved yet.\n')
        host = Path(host_home) if host_home else Path.home() / '.claude'
        _atomic_write(host / 'skills' / 'strata' / 'SKILL.md', skill)
        _atomic_write(host / 'agents' / 'strata-reader.md', reader)
    return project


def main(argv=None):
    parser = argparse.ArgumentParser(prog='strata', description='Persistent cited memory for a local archive.')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'index', 'serve'):
        child = commands.add_parser(name)
        child.add_argument('--project', default='.', help='Project folder (default: current directory)')
        if name == 'init':
            child.add_argument('--corpus', action='append')
            child.add_argument('--manuscript')
        if name != 'serve':
            child.add_argument('--legacy-root', action='append', help='Original ordered roots for a legacy ledger; repeat in original order')
    args = parser.parse_args(argv)
    def progress(message):
        print(message.encode('ascii', errors='backslashreplace').decode('ascii'), file=sys.stderr, flush=True)
    try:
        if args.command == 'serve':
            from strata.server import run
            run(Project(args.project, progress=progress))
        else:
            if args.command == 'init':
                initialize(args.project, corpus=args.corpus, manuscript=args.manuscript)
            runtime = Project(args.project, progress=progress)
            legacy = [resolve(runtime.path, path) for path in args.legacy_root] if args.legacy_root else None
            runtime.refresh(legacy_roots=legacy)
            for item in runtime.report.skipped:
                progress(f'Skipped {item.path}: {item.reason}')
            if args.command == 'init':
                progress('Ready. Open Claude Code in this project folder.')
        return 0
    except KeyboardInterrupt:
        progress('Indexing interrupted. Run strata index to retry; saved citations and notes are retained.')
        return 130
    except Exception as error:
        progress(f'Error: {error}')
        return 1
