#!/usr/bin/env python3
"""
tests/test_mirror.py

Unit tests for scripts/mirror.py

Run with:
    python3 -m pytest tests/test_mirror.py -v
    # or without pytest:
    python3 tests/test_mirror.py
"""

from __future__ import annotations

import sys
import os
import tempfile
import textwrap
import unittest
from pathlib import Path

# Allow importing from scripts/ without installing
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
from mirror import (
    Mirror,
    LineDiff,
    load_mirrors,
    load_github_release_prefix,
    compute_diffs,
    inject_github_release_urls,
    apply_to_file,
    find_registry_files,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_mirror_yaml(content: str, dir: Path) -> Path:
    p = dir / 'mirror.yaml'
    p.write_text(textwrap.dedent(content))
    return p


def make_registry_yaml(content: str, dir: Path, rel: str = 'pkgs/foo/bar/registry.yaml') -> Path:
    p = dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content))
    return p


# ---------------------------------------------------------------------------
# load_mirrors
# ---------------------------------------------------------------------------

class TestLoadMirrors(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_parses_double_quoted_values(self):
        cfg = make_mirror_yaml("""
            mirrors:
              - original: "https://github.com/"
                mirror: "https://gh-proxy.org/https://github.com/"
        """, self.tmp)
        mirrors = load_mirrors(cfg)
        self.assertEqual(len(mirrors), 1)
        self.assertEqual(mirrors[0].original, 'https://github.com/')
        self.assertEqual(mirrors[0].mirror, 'https://gh-proxy.org/https://github.com/')

    def test_parses_single_quoted_values(self):
        cfg = make_mirror_yaml("""
            mirrors:
              - original: 'https://nodejs.org/dist/'
                mirror: 'https://mirrors.ustc.edu.cn/node/'
        """, self.tmp)
        mirrors = load_mirrors(cfg)
        self.assertEqual(mirrors[0].original, 'https://nodejs.org/dist/')
        self.assertEqual(mirrors[0].mirror, 'https://mirrors.ustc.edu.cn/node/')

    def test_skips_disabled_entries_where_original_equals_mirror(self):
        cfg = make_mirror_yaml("""
            mirrors:
              - original: "https://releases.hashicorp.com/"
                mirror: "https://releases.hashicorp.com/"
              - original: "https://nodejs.org/dist/"
                mirror: "https://mirrors.ustc.edu.cn/node/"
        """, self.tmp)
        mirrors = load_mirrors(cfg)
        # Only the Node.js entry should remain
        self.assertEqual(len(mirrors), 1)
        self.assertEqual(mirrors[0].original, 'https://nodejs.org/dist/')

    def test_skips_comment_and_blank_lines(self):
        cfg = make_mirror_yaml("""
            # This is a comment
            mirrors:

              # Another comment
              - original: "https://github.com/"
                mirror: "https://gh-proxy.org/https://github.com/"
        """, self.tmp)
        mirrors = load_mirrors(cfg)
        self.assertEqual(len(mirrors), 1)

    def test_returns_empty_list_when_no_active_mirrors(self):
        cfg = make_mirror_yaml("""
            mirrors:
              - original: "https://example.com/"
                mirror: "https://example.com/"
        """, self.tmp)
        self.assertEqual(load_mirrors(cfg), [])

    def test_multiple_mirrors_preserved_in_order(self):
        cfg = make_mirror_yaml("""
            mirrors:
              - original: "https://github.com/"
                mirror: "https://gh-proxy.org/https://github.com/"
              - original: "https://nodejs.org/dist/"
                mirror: "https://mirrors.ustc.edu.cn/node/"
              - original: "https://downloads.haskell.org/"
                mirror: "https://mirrors.ustc.edu.cn/hackage/"
        """, self.tmp)
        mirrors = load_mirrors(cfg)
        self.assertEqual(len(mirrors), 3)
        self.assertEqual(mirrors[0].original, 'https://github.com/')
        self.assertEqual(mirrors[2].original, 'https://downloads.haskell.org/')


# ---------------------------------------------------------------------------
# compute_diffs
# ---------------------------------------------------------------------------

class TestComputeDiffs(unittest.TestCase):

    MIRRORS = [
        Mirror('https://nodejs.org/dist/', 'https://mirrors.ustc.edu.cn/node/'),
        Mirror('https://downloads.haskell.org/', 'https://mirrors.ustc.edu.cn/hackage/'),
    ]

    def _lines(self, text: str) -> list[str]:
        return textwrap.dedent(text).splitlines(keepends=True)

    def test_replaces_plain_url_line(self):
        lines = self._lines("""
            packages:
              - type: http
                url: https://nodejs.org/dist/v20.0.0/node-v20.0.0-linux-x64.tar.gz
        """)
        diffs = compute_diffs(lines, self.MIRRORS)
        self.assertEqual(len(diffs), 1)
        self.assertIn('mirrors.ustc.edu.cn/node/', diffs[0].after)
        self.assertIn('node-v20.0.0-linux-x64.tar.gz', diffs[0].after)

    def test_replaces_double_quoted_url_line(self):
        # Must preserve leading spaces so _URL_LINE_RE matches (\s+ required)
        lines = ['        url: "https://nodejs.org/dist/v20.0.0/node.tar.gz"\n']
        diffs = compute_diffs(lines, self.MIRRORS)
        self.assertEqual(len(diffs), 1)
        self.assertIn('mirrors.ustc.edu.cn/node/', diffs[0].after)
        self.assertTrue(diffs[0].after.strip().startswith('url: "https://mirrors.ustc.edu.cn/node/'))

    def test_does_not_touch_unmatched_url(self):
        lines = self._lines("""
              url: https://releases.hashicorp.com/terraform/1.0.0/terraform_1.0.0_linux_amd64.zip
        """)
        diffs = compute_diffs(lines, self.MIRRORS)
        self.assertEqual(diffs, [])

    def test_does_not_touch_asset_lines(self):
        # Only `url:` lines are processed; `asset:` lines are unchanged
        lines = self._lines("""
              asset: some-asset-{{.OS}}_{{.Arch}}.tar.gz
        """)
        diffs = compute_diffs(lines, self.MIRRORS)
        self.assertEqual(diffs, [])

    def test_does_not_touch_non_http_url(self):
        lines = self._lines("""
              url: ftp://example.com/file.tar.gz
        """)
        diffs = compute_diffs(lines, self.MIRRORS)
        self.assertEqual(diffs, [])

    def test_first_matching_mirror_wins(self):
        mirrors = [
            Mirror('https://example.com/', 'https://mirror1.com/'),
            Mirror('https://example.com/', 'https://mirror2.com/'),
        ]
        lines = ['        url: https://example.com/file.zip\n']
        diffs = compute_diffs(lines, mirrors)
        self.assertEqual(len(diffs), 1)
        self.assertIn('mirror1.com', diffs[0].after)
        self.assertNotIn('mirror2.com', diffs[0].after)

    def test_haskell_url_prefix_replacement(self):
        # Must have leading spaces so _URL_LINE_RE \s+ matches
        lines = ['          url: https://downloads.haskell.org/~cabal/cabal-install-3.10.1.0/SHA256SUMS\n']
        diffs = compute_diffs(lines, self.MIRRORS)
        self.assertEqual(len(diffs), 1)
        self.assertIn('mirrors.ustc.edu.cn/hackage/', diffs[0].after)
        self.assertIn('~cabal/cabal-install-3.10.1.0/SHA256SUMS', diffs[0].after)

    def test_lineno_is_one_based(self):
        lines = [
            '# comment\n',
            '        url: https://nodejs.org/dist/v20.tar.gz\n',
        ]
        diffs = compute_diffs(lines, self.MIRRORS)
        self.assertEqual(diffs[0].lineno, 2)

    def test_before_and_after_captured(self):
        line = '        url: https://nodejs.org/dist/v20.tar.gz\n'
        diffs = compute_diffs([line], self.MIRRORS)
        self.assertEqual(diffs[0].before, line)
        self.assertNotEqual(diffs[0].after, line)

    def test_empty_file_produces_no_diffs(self):
        self.assertEqual(compute_diffs([], self.MIRRORS), [])

    def test_file_with_no_url_lines_produces_no_diffs(self):
        lines = self._lines("""
            packages:
              - type: github_release
                repo_owner: cli
                repo_name: cli
                asset: gh_{{.Version}}_{{.OS}}_{{.Arch}}.tar.gz
        """)
        self.assertEqual(compute_diffs(lines, self.MIRRORS), [])


# ---------------------------------------------------------------------------
# apply_to_file
# ---------------------------------------------------------------------------

class TestApplyToFile(unittest.TestCase):

    MIRRORS = [Mirror('https://nodejs.org/dist/', 'https://mirrors.ustc.edu.cn/node/')]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _make_registry(self, content: str) -> Path:
        return make_registry_yaml(content, self.tmp)

    def test_modifies_file_when_match_found(self):
        p = self._make_registry("""\
            packages:
              - type: http
                url: https://nodejs.org/dist/v20.0.0/node-v20.0.0.tar.gz
        """)
        changed = apply_to_file(p, self.MIRRORS, gh_prefix='', dry_run=False, repo_root=self.tmp)
        self.assertTrue(changed)
        self.assertIn('mirrors.ustc.edu.cn/node/', p.read_text())

    def test_dry_run_does_not_modify_file(self):
        original = textwrap.dedent("""\
            packages:
              - type: http
                url: https://nodejs.org/dist/v20.0.0/node.tar.gz
        """)
        p = self._make_registry(original)
        apply_to_file(p, self.MIRRORS, gh_prefix='', dry_run=True, repo_root=self.tmp)
        self.assertEqual(p.read_text(), original)

    def test_returns_false_when_no_match(self):
        p = self._make_registry("""\
            packages:
              - type: github_release
                repo_owner: cli
                repo_name: cli
        """)
        changed = apply_to_file(p, self.MIRRORS, gh_prefix='', dry_run=False, repo_root=self.tmp)
        self.assertFalse(changed)

    def test_idempotent_second_apply_makes_no_changes(self):
        p = self._make_registry("""\
            packages:
              - type: http
                url: https://nodejs.org/dist/v20.0.0/node.tar.gz
        """)
        apply_to_file(p, self.MIRRORS, gh_prefix='', dry_run=False, repo_root=self.tmp)
        content_after_first = p.read_text()
        apply_to_file(p, self.MIRRORS, gh_prefix='', dry_run=False, repo_root=self.tmp)
        self.assertEqual(p.read_text(), content_after_first)


# ---------------------------------------------------------------------------
# find_registry_files
# ---------------------------------------------------------------------------

class TestFindRegistryFiles(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_finds_nested_registry_yaml_files(self):
        make_registry_yaml('', self.tmp, 'pkgs/a/b/registry.yaml')
        make_registry_yaml('', self.tmp, 'pkgs/c/d/registry.yaml')
        files = find_registry_files(self.tmp)
        names = [f.name for f in files]
        self.assertEqual(names.count('registry.yaml'), 2)

    def test_ignores_non_registry_yaml(self):
        make_registry_yaml('', self.tmp, 'pkgs/a/b/pkg.yaml')
        files = find_registry_files(self.tmp)
        self.assertEqual(files, [])

    def test_returns_sorted_paths(self):
        make_registry_yaml('', self.tmp, 'pkgs/z/z/registry.yaml')
        make_registry_yaml('', self.tmp, 'pkgs/a/a/registry.yaml')
        files = find_registry_files(self.tmp)
        self.assertEqual(files, sorted(files))


# ---------------------------------------------------------------------------
# main() integration
# ---------------------------------------------------------------------------

class TestMainIntegration(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _setup_env(self, mirror_content: str, registry_content: str):
        cfg = make_mirror_yaml(mirror_content, self.tmp)
        reg = make_registry_yaml(registry_content, self.tmp)
        return cfg, reg

    def test_apply_and_restore_roundtrip(self):
        registry_orig = textwrap.dedent("""\
            packages:
              - type: http
                url: https://nodejs.org/dist/v20.0.0/node.tar.gz
        """)
        cfg, reg = self._setup_env("""\
            mirrors:
              - original: "https://nodejs.org/dist/"
                mirror: "https://mirrors.ustc.edu.cn/node/"
        """, registry_orig)
        # Apply
        rc = main(['-c', str(cfg), '--dry-run'])
        self.assertEqual(rc, 0)
        # Dry-run should not change file
        self.assertEqual(reg.read_text(), registry_orig)

    def test_no_active_mirrors_exits_cleanly(self):
        cfg = make_mirror_yaml("""
            mirrors:
              - original: "https://example.com/"
                mirror: "https://example.com/"
        """, self.tmp)
        rc = main(['-c', str(cfg)])
        self.assertEqual(rc, 0)

    def test_missing_config_returns_error(self):
        rc = main(['-c', str(self.tmp / 'nonexistent.yaml')])
        self.assertEqual(rc, 1)

    def test_dry_run_does_not_write(self):
        original = textwrap.dedent("""\
            packages:
              - type: http
                url: https://nodejs.org/dist/v20.0.0/node.tar.gz
        """)
        cfg, reg = self._setup_env("""\
            mirrors:
              - original: "https://nodejs.org/dist/"
                mirror: "https://mirrors.ustc.edu.cn/node/"
        """, original)
        main(['-c', str(cfg), '-d'])
        self.assertEqual(reg.read_text(), original)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):

    MIRRORS = [Mirror('https://nodejs.org/dist/', 'https://mirrors.ustc.edu.cn/node/')]

    def test_template_variables_preserved_after_replacement(self):
        """Go template syntax in URLs must survive the replacement unchanged."""
        lines = [
            '        url: https://nodejs.org/dist/{{.Version}}/node-{{.Version}}-{{.OS}}-{{.Arch}}.tar.gz\n'
        ]
        diffs = compute_diffs(lines, self.MIRRORS)
        self.assertEqual(len(diffs), 1)
        self.assertIn('{{.Version}}', diffs[0].after)
        self.assertIn('{{.OS}}', diffs[0].after)
        self.assertIn('{{.Arch}}', diffs[0].after)

    def test_url_at_top_level_indentation_not_matched(self):
        """Only indented `url:` lines (inside a package block) are matched."""
        # Top-level `url:` (no leading spaces) should NOT be rewritten
        lines = ['url: https://nodejs.org/dist/v20.tar.gz\n']
        diffs = compute_diffs(lines, self.MIRRORS)
        self.assertEqual(diffs, [])

    def test_suffix_preserved_exactly(self):
        """The path suffix after the matched prefix must be preserved exactly."""
        lines = ['        url: https://nodejs.org/dist/v20.0.0/release/node-v20.0.0.tar.gz\n']
        diffs = compute_diffs(lines, self.MIRRORS)
        self.assertIn('v20.0.0/release/node-v20.0.0.tar.gz', diffs[0].after)

    def test_longer_prefix_does_not_accidentally_match_shorter_prefix(self):
        """https://nodejs.org/dist/ should not match https://nodejs.org/ (different mirror)."""
        mirrors = [
            Mirror('https://nodejs.org/', 'https://some-other-mirror.com/'),
            Mirror('https://nodejs.org/dist/', 'https://mirrors.ustc.edu.cn/node/'),
        ]
        lines = ['        url: https://nodejs.org/dist/v20.tar.gz\n']
        diffs = compute_diffs(lines, mirrors)
        # First match wins: nodejs.org/ matches before nodejs.org/dist/
        self.assertIn('some-other-mirror.com', diffs[0].after)


# ---------------------------------------------------------------------------
# load_github_release_prefix
# ---------------------------------------------------------------------------

class TestLoadGithubReleasePrefix(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_returns_prefix_when_set(self):
        cfg = make_mirror_yaml("""
            github_release_url_prefix: https://gh-proxy.org
            mirrors:
              - original: "https://github.com/"
                mirror: "https://github.com/"
        """, self.tmp)
        prefix = load_github_release_prefix(cfg)
        self.assertEqual(prefix, 'https://gh-proxy.org')

    def test_returns_empty_when_not_set(self):
        cfg = make_mirror_yaml("""
            mirrors:
              - original: "https://example.com/"
                mirror: "https://mirror.example.com/"
        """, self.tmp)
        self.assertEqual(load_github_release_prefix(cfg), '')

    def test_ignores_commented_out_prefix(self):
        cfg = make_mirror_yaml("""
            # github_release_url_prefix: https://gh-proxy.org
            mirrors:
              - original: "https://example.com/"
                mirror: "https://mirror.example.com/"
        """, self.tmp)
        self.assertEqual(load_github_release_prefix(cfg), '')

    def test_returns_quoted_prefix(self):
        cfg = make_mirror_yaml("""
            github_release_url_prefix: "https://gh-proxy.org"
            mirrors: []
        """, self.tmp)
        self.assertEqual(load_github_release_prefix(cfg), 'https://gh-proxy.org')


# ---------------------------------------------------------------------------
# inject_github_release_urls
# ---------------------------------------------------------------------------

class TestInjectGithubReleaseUrls(unittest.TestCase):

    PREFIX = 'https://gh-proxy.org'
    OWNER  = 'cli'
    REPO   = 'cli'

    def _inject(self, content: str, prefix: str = None, owner: str = None, repo: str = None) -> str:
        return inject_github_release_urls(
            textwrap.dedent(content),
            prefix or self.PREFIX,
            owner  or self.OWNER,
            repo   or self.REPO,
        )

    def test_injects_url_into_block_with_asset(self):
        content = """\
            version_constraint: "false"
            version_overrides:
              - version_constraint: "true"
                asset: cli_{{.Version}}_{{.OS}}_{{.Arch}}.tar.gz
                format: tar.gz
        """
        result = self._inject(content)
        self.assertIn('url: https://gh-proxy.org/https://github.com/cli/cli/releases/download/', result)
        self.assertIn('{{.Version}}/{{.Asset}}', result)

    def test_does_not_inject_when_url_already_present(self):
        content = """\
            version_constraint: "false"
            version_overrides:
              - version_constraint: "true"
                url: https://existing.example.com/download/file.tar.gz
                asset: file.tar.gz
        """
        result = self._inject(content)
        # Should not add a second url: line
        self.assertEqual(result.count('url:'), 1)
        self.assertIn('existing.example.com', result)

    def test_does_not_inject_when_no_asset_field(self):
        content = """\
            version_constraint: "false"
            version_overrides:
              - version_constraint: "true"
                format: raw
        """
        result = self._inject(content)
        self.assertNotIn('url:', result)

    def test_injects_into_multiple_blocks(self):
        content = """\
            version_constraint: "false"
            version_overrides:
              - version_constraint: semver("<= 1.0.0")
                asset: cli_{{.Version}}_{{.OS}}_old.tar.gz
              - version_constraint: "true"
                asset: cli_{{.Version}}_{{.OS}}_new.tar.gz
        """
        result = self._inject(content)
        self.assertEqual(result.count('url: https://gh-proxy.org/'), 2)

    def test_idempotent_second_injection_no_change(self):
        content = """\
            version_constraint: "false"
            version_overrides:
              - version_constraint: "true"
                asset: cli_{{.Version}}_{{.OS}}.tar.gz
        """
        first_result  = self._inject(content)
        second_result = self._inject(first_result)
        self.assertEqual(first_result, second_result)

    def test_url_contains_owner_and_repo(self):
        content = """\
            version_constraint: "false"
            version_overrides:
              - version_constraint: "true"
                asset: mytool.tar.gz
        """
        result = inject_github_release_urls(
            textwrap.dedent(content), 'https://proxy.example.com', 'myowner', 'myrepo'
        )
        self.assertIn('github.com/myowner/myrepo/releases/download/', result)

    def test_prefix_trailing_slash_normalised(self):
        """Prefix with trailing slash should not produce double slash."""
        content = """\
            version_constraint: "false"
            version_overrides:
              - version_constraint: "true"
                asset: tool.tar.gz
        """
        result = inject_github_release_urls(
            textwrap.dedent(content), 'https://gh-proxy.org/', 'a', 'b'
        )
        self.assertNotIn('org//https', result)
        self.assertIn('org/https://', result)

    def test_returns_unchanged_when_prefix_empty(self):
        content = "version_constraint: \"true\"\n  asset: tool.tar.gz\n"
        self.assertEqual(inject_github_release_urls(content, '', 'a', 'b'), content)

    def test_returns_unchanged_when_owner_empty(self):
        content = "version_constraint: \"true\"\n  asset: tool.tar.gz\n"
        self.assertEqual(inject_github_release_urls(content, 'https://p.example.com', '', 'b'), content)


# ---------------------------------------------------------------------------
# apply_to_file with gh_prefix
# ---------------------------------------------------------------------------

class TestApplyToFileGithubRelease(unittest.TestCase):

    PREFIX = 'https://gh-proxy.org'

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _make_registry(self, content: str) -> Path:
        return make_registry_yaml(content, self.tmp)

    def test_injects_url_for_github_release_package(self):
        p = self._make_registry("""\
            # yaml-language-server: $schema=...
            packages:
              - type: github_release
                repo_owner: cli
                repo_name: cli
                version_constraint: "false"
                version_overrides:
                  - version_constraint: "true"
                    asset: gh_{{.Version}}_{{.OS}}_{{.Arch}}.tar.gz
                    format: tar.gz
        """)
        changed = apply_to_file(p, [], gh_prefix=self.PREFIX, dry_run=False, repo_root=self.tmp)
        self.assertTrue(changed)
        text = p.read_text()
        self.assertIn('url: https://gh-proxy.org/https://github.com/cli/cli/', text)
        self.assertIn('{{.Version}}/{{.Asset}}', text)

    def test_no_change_when_gh_prefix_empty(self):
        p = self._make_registry("""\
            packages:
              - type: github_release
                repo_owner: cli
                repo_name: cli
                version_constraint: "false"
                version_overrides:
                  - version_constraint: "true"
                    asset: gh_{{.Version}}.tar.gz
        """)
        changed = apply_to_file(p, [], gh_prefix='', dry_run=False, repo_root=self.tmp)
        self.assertFalse(changed)

    def test_dry_run_does_not_write_github_release_injection(self):
        original = """\
packages:
  - type: github_release
    repo_owner: cli
    repo_name: cli
    version_constraint: "false"
    version_overrides:
      - version_constraint: "true"
        asset: gh_{{.Version}}.tar.gz
"""
        p = self._make_registry(original)
        apply_to_file(p, [], gh_prefix=self.PREFIX, dry_run=True, repo_root=self.tmp)
        self.assertEqual(p.read_text(), original)

    def test_idempotent_github_release_injection(self):
        p = self._make_registry("""\
            packages:
              - type: github_release
                repo_owner: cli
                repo_name: cli
                version_constraint: "false"
                version_overrides:
                  - version_constraint: "true"
                    asset: gh_{{.Version}}.tar.gz
        """)
        apply_to_file(p, [], gh_prefix=self.PREFIX, dry_run=False, repo_root=self.tmp)
        after_first = p.read_text()
        apply_to_file(p, [], gh_prefix=self.PREFIX, dry_run=False, repo_root=self.tmp)
        self.assertEqual(p.read_text(), after_first)


if __name__ == '__main__':
    unittest.main()
