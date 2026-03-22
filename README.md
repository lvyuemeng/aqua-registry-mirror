# aqua-registry

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/aquaproj/aqua-registry)

[aqua](https://aquaproj.github.io/)'s Standard Registry — mirror fork for restricted networks.

- [Upstream](https://github.com/aquaproj/aqua-registry)
- [Change Log](https://github.com/aquaproj/aqua-registry/releases)

## Mirror support

A pre-configured fork of [aquaproj/aqua-registry](https://github.com/aquaproj/aqua-registry) that routes
downloads through mirrors for users behind the Great Firewall of China
or other restricted networks.

| Package type | Mirror strategy |
|---|---|
| `type: github_release` | `url:` injected via `github_release_url_prefix` in `mirror.yaml` |
| `type: http` (Node.js, Haskell…) | `url:` fields rewritten to USTC mirrors |
| GitHub raw / checksum URLs | Routed through `https://gh-proxy.org` |

See [`mirror.yaml`](mirror.yaml) to change the proxy or add your own mirrors.

### How it works

| File | Purpose |
|---|---|
| [`mirror.yaml`](mirror.yaml) | URL prefix mappings and `github_release_url_prefix` |
| [`scripts/mirror.py`](scripts/mirror.py) | Rewrites `url:` fields; injects proxy URLs for `github_release` |
| [`tests/test_mirror.py`](tests/test_mirror.py) | 49 unit + integration tests |
| [`.github/workflows/mirror-test.yaml`](.github/workflows/mirror-test.yaml) | CI: tests on every relevant change |
| [`.github/workflows/mirror-upstream.yaml`](.github/workflows/mirror-upstream.yaml) | Scheduled daily: rebase onto upstream, re-apply mirrors |

### Quick start

In your `aqua.yaml`:

```yaml
registries:
  - name: standard
    type: github_content
    repo_owner: lvyuemeng
    repo_name: aqua-registry-mirror
    ref: main
    path: registry.yaml
```

Then `aqua install` as normal. All downloads are routed through the configured mirrors.

### Local mirror commands

```sh
python3 scripts/mirror.py              # apply
python3 scripts/mirror.py --dry-run    # preview
python3 scripts/mirror.py --restore    # restore via git
```

### Tests

```sh
pip install pytest
python3 -m pytest tests/test_mirror.py -v
```

## Contributors

[![contributors](https://contrib.rocks/image?repo=aquaproj/aqua-registry)](https://github.com/aquaproj/aqua-registry/graphs/contributors)

## License

[MIT](LICENSE)
