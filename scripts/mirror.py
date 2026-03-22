#!/usr/bin/env python3
"""
scripts/mirror.py - Mirror URL rewriter for aqua-registry.

Two pipeline passes:
  1. rewrite_explicit_urls  -- rewrite `url:` fields via mirrors: list
  2. inject_github_urls     -- inject proxy url: for github_release/archive/content types

Requires: Python 3.8+, pyyaml, git (for --restore)
"""

from __future__ import annotations

import argparse
import difflib
import glob
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Mirror:
    original: str
    mirror: str


@dataclass
class LineDiff:
    """Records a single line-level URL rewrite (1-based lineno)."""
    lineno: int
    before: str
    after: str


@dataclass
class Config:
    mirrors: list = field(default_factory=list)  # list[Mirror]
    github_url_prefix: str = ''


@dataclass
class FileResult:
    path: Path
    original: str
    modified: str

    @property
    def changed(self):
        return self.original != self.modified


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(path: Path) -> Config:
    data = yaml.safe_load(path.read_text()) or {}
    raw = data.get('mirrors', []) or []
    mirrors = [
        Mirror(str(m['original']), str(m['mirror']))
        for m in raw
        if isinstance(m, dict)
        and 'original' in m and 'mirror' in m
        and str(m['original']) != str(m['mirror'])
    ]
    prefix = str(data.get('github_release_url_prefix', '') or '').strip()
    return Config(mirrors=mirrors, github_url_prefix=prefix)


# ---------------------------------------------------------------------------
# Pass 1: explicit url: rewriting
# ---------------------------------------------------------------------------

_URL_LINE_RE = re.compile(
    r'^(?P<indent>\s+url:\s+)(?P<q>["\']?)(?P<url>https?://[^\s"\']*)(?P=q)\s*$'
)


def _apply_mirrors(url, mirrors):
    for m in mirrors:
        if url.startswith(m.original):
            return m.mirror + url[len(m.original):]
    return url


def rewrite_explicit_urls(content: str, mirrors: list) -> str:
    """Rewrite indented url: lines whose value matches a mirror prefix."""
    if not mirrors:
        return content
    lines = []
    for line in content.splitlines(keepends=True):
        m = _URL_LINE_RE.match(line.rstrip('\n'))
        if m:
            new_url = _apply_mirrors(m.group('url'), mirrors)
            if new_url != m.group('url'):
                q = m.group('q')
                line = f"{m.group('indent')}{q}{new_url}{q}\n"
        lines.append(line)
    return ''.join(lines)


# ---------------------------------------------------------------------------
# Pass 2: GitHub proxy URL injection
# ---------------------------------------------------------------------------

_GITHUB_TYPES = frozenset({'github_release', 'github_archive', 'github_content'})
_VO_START_RE  = re.compile(r'^(?P<indent>\s+)- version_constraint:')
_HAS_URL_RE   = re.compile(r'^\s+url:\s+')
_HAS_ASSET_RE = re.compile(r'^\s+asset:\s+')


def _proxy(prefix, url):
    return prefix.rstrip('/') + '/' + url


def _github_url_template(prefix, pkg):
    """
    Build the proxy url: Go template for the given package dict.
    Returns None when the type is not supported.
    """
    t     = pkg.get('type', '')
    owner = pkg.get('repo_owner', '')
    repo  = pkg.get('repo_name', '')
    if not owner or not repo or t not in _GITHUB_TYPES:
        return None
    if t == 'github_release':
        base = f'https://github.com/{owner}/{repo}/releases/download/{{{{.Version}}}}/{{{{.Asset}}}}'
    elif t == 'github_archive':
        base = f'https://github.com/{owner}/{repo}/archive/refs/tags/{{{{.Version}}}}.tar.gz'
    elif t == 'github_content':
        path_tmpl = pkg.get('path', '') or '{{.Path}}'
        base = f'https://raw.githubusercontent.com/{owner}/{repo}/{{{{.Version}}}}/{path_tmpl}'
    else:
        return None
    return _proxy(prefix, base)


def _parse_top_pkg(content):
    """Return the first package's relevant fields, or empty dict on error."""
    try:
        data = yaml.safe_load(content) or {}
    except yaml.YAMLError:
        return {}
    pkgs = data.get('packages', [])
    if not pkgs or not isinstance(pkgs[0], dict):
        return {}
    f = pkgs[0]
    return {
        'type':       f.get('type', ''),
        'repo_owner': f.get('repo_owner', ''),
        'repo_name':  f.get('repo_name', ''),
        'path':       f.get('path', ''),
    }


def _inject_into_version_overrides(content, url_tmpl, pkg_type):
    """
    Walk version_overrides list items and inject url: into each block that
    has no existing url:.
    For github_release: only inject when the block has an asset: field.
    For github_archive / github_content: always inject (no asset: needed).
    """
    lines = content.splitlines(keepends=True)
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        mo = _VO_START_RE.match(line)
        if not mo:
            result.append(line)
            i += 1
            continue

        block_indent     = mo.group('indent')
        block_indent_len = len(block_indent)
        result.append(line)
        i += 1

        block = []
        while i < len(lines):
            nxt     = lines[i]
            stripped = nxt.rstrip()
            if nxt.startswith(block_indent + '- '):
                break
            if stripped:
                nxt_indent = len(nxt) - len(nxt.lstrip())
                if nxt_indent <= block_indent_len:
                    break
            block.append(nxt)
            i += 1

        block_text = ''.join(block)
        has_url   = bool(_HAS_URL_RE.search(block_text))
        has_asset = bool(_HAS_ASSET_RE.search(block_text))

        should_inject = (
            not has_url and (
                pkg_type in ('github_archive', 'github_content') or
                (pkg_type == 'github_release' and has_asset)
            )
        )
        if should_inject:
            result.append(f'{block_indent}  url: {url_tmpl}\n')

        result.extend(block)

    return ''.join(result)


def inject_github_urls(content: str, cfg: Config) -> str:
    """Inject proxy url: for all supported GitHub-backed package types."""
    if not cfg.github_url_prefix:
        return content
    pkg      = _parse_top_pkg(content)
    url_tmpl = _github_url_template(cfg.github_url_prefix, pkg)
    if not url_tmpl:
        return content
    return _inject_into_version_overrides(content, url_tmpl, pkg.get('type', ''))


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def process_content(content: str, cfg: Config) -> str:
    """
    Full rewrite pipeline:
      content
        -> rewrite_explicit_urls   (pass 1)
        -> inject_github_urls      (pass 2)
    """
    return inject_github_urls(
        rewrite_explicit_urls(content, cfg.mirrors),
        cfg,
    )


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def process_file(path: Path, cfg: Config) -> FileResult:
    """Read, transform, and return FileResult (no side-effects)."""
    original = path.read_text()
    return FileResult(path=path, original=original, modified=process_content(original, cfg))


def apply_result(result: FileResult, dry_run: bool, repo_root: Path) -> bool:
    """Write or diff the result. Returns True when changed."""
    if not result.changed:
        return False
    rel = result.path.relative_to(repo_root)
    if dry_run:
        diff = difflib.unified_diff(
            result.original.splitlines(keepends=True),
            result.modified.splitlines(keepends=True),
            fromfile=f'a/{rel}',
            tofile=f'b/{rel}',
            n=2,
        )
        sys.stdout.writelines(diff)
    else:
        result.path.write_text(result.modified)
        print(f'Modified: {rel}')
    return True


def find_registry_files(repo_root: Path) -> list:
    return sorted(Path(p) for p in glob.glob(
        str(repo_root / 'pkgs' / '**' / 'registry.yaml'),
        recursive=True,
    ))


def find_all_registry_files(repo_root: Path) -> list:
    """Return all registry.yaml files: root registry.yaml + pkgs/**/registry.yaml."""
    root_reg = repo_root / 'registry.yaml'
    pkg_regs = find_registry_files(repo_root)
    extras = [root_reg] if root_reg.is_file() else []
    return extras + pkg_regs


# ---------------------------------------------------------------------------
# Compatibility shims for tests
# ---------------------------------------------------------------------------

def load_mirrors(config_path: Path) -> list:
    """Load active Mirror entries from a mirror.yaml config file."""
    if not config_path.is_file():
        return []
    cfg = load_config(config_path)
    return cfg.mirrors


def load_github_release_prefix(config_path: Path) -> str:
    """Return github_release_url_prefix from a mirror.yaml config file."""
    if not config_path.is_file():
        return ''
    cfg = load_config(config_path)
    return cfg.github_url_prefix


def compute_diffs(lines: list, mirrors: list) -> list:
    """
    Compute line-level URL diffs for explicit url: rewrites.
    Returns a list of LineDiff (1-based lineno, before, after).
    """
    result = []
    for idx, line in enumerate(lines, start=1):
        m = _URL_LINE_RE.match(line.rstrip('\n'))
        if m:
            new_url = _apply_mirrors(m.group('url'), mirrors)
            if new_url != m.group('url'):
                q = m.group('q')
                after = f"{m.group('indent')}{q}{new_url}{q}\n"
                result.append(LineDiff(lineno=idx, before=line, after=after))
    return result


def inject_github_release_urls(content: str, prefix: str, owner: str, repo: str) -> str:
    """
    Inject proxy url: fields for a github_release package.
    Convenience wrapper for tests: takes explicit prefix/owner/repo.
    """
    if not prefix or not owner or not repo:
        return content
    cfg = Config(mirrors=[], github_url_prefix=prefix)
    # Build a minimal pkg dict and get the url template
    pkg = {'type': 'github_release', 'repo_owner': owner, 'repo_name': repo}
    url_tmpl = _github_url_template(prefix, pkg)
    if not url_tmpl:
        return content
    return _inject_into_version_overrides(content, url_tmpl, 'github_release')


def apply_to_file(path: Path, mirrors: list, gh_prefix: str, dry_run: bool, repo_root: Path) -> bool:
    """
    Apply mirror rewrites to a single registry.yaml file.
    Returns True if the file was changed (or would be changed in dry_run).
    """
    cfg = Config(mirrors=mirrors, github_url_prefix=gh_prefix)
    result = process_file(path, cfg)
    return apply_result(result, dry_run, repo_root)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_restore(repo_root: Path) -> int:
    print('Restoring original URLs via git checkout pkgs/ ...')
    subprocess.run(['git', '-C', str(repo_root), 'checkout', '--', 'pkgs/'], check=True)
    print('Done.')
    return 0


def cmd_apply(config_path: Path, repo_root: Path, dry_run: bool) -> int:
    if not config_path.is_file():
        print(f'Error: mirror config not found: {config_path}', file=sys.stderr)
        return 1

    cfg = load_config(config_path)

    if not cfg.mirrors and not cfg.github_url_prefix:
        print('No active mirror mappings found. Check mirror.yaml.')
        return 0

    if cfg.mirrors:
        print(f'Loaded {len(cfg.mirrors)} mirror mapping(s)')
        for m in cfg.mirrors:
            print(f'  {m.original!r}  ->  {m.mirror!r}')
        print()

    if cfg.github_url_prefix:
        print(f'github_url_prefix: {cfg.github_url_prefix!r}')
        print('  Injecting proxy url: for github_release, github_archive, github_content types.')
        print()

    files    = find_all_registry_files(repo_root)
    results  = (process_file(f, cfg) for f in files)
    modified = sum(apply_result(r, dry_run, repo_root) for r in results)
    total    = len(files)

    print()
    if dry_run:
        print(f'Dry-run: {modified}/{total} files would be modified.')
    else:
        print(f'Done: {modified}/{total} files modified.')
        if modified:
            print('\nTo restore: python3 scripts/mirror.py --restore')
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(default_config: Path) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='scripts/mirror.py',
        description='Replace download URLs in pkgs/**/registry.yaml with mirror URLs.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument('-c', '--config', type=Path, default=default_config,
                   metavar='FILE', help='Mirror config YAML (default: mirror.yaml)')
    p.add_argument('-d', '--dry-run', action='store_true',
                   help='Print unified diff without modifying files')
    p.add_argument('-r', '--restore', action='store_true',
                   help='Restore originals via git checkout pkgs/')
    return p


def main(argv=None) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    args = build_parser(repo_root / 'mirror.yaml').parse_args(argv)
    if args.restore:
        return cmd_restore(repo_root)
    return cmd_apply(args.config, repo_root, args.dry_run)


if __name__ == '__main__':
    sys.exit(main())
