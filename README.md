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
| [`.github/workflows/mirror-upstream.yaml`](.github/workflows/mirror-upstream.yaml) | Scheduled daily: rebase onto upstream, re-apply mirrors, push `mirror-YYYYMMDD` tag, create GitHub Release |

### Quick start

In your `aqua.yaml`, add the mirror registry.

You can pin to a specific mirror tag (recommended for reproducibility):

```yaml
registries:
  - type: standard
    ref: v4.486.0 # renovate: depName=aquaproj/aqua-registry
  - name: mirror
    type: github_content
    repo_owner: lvyuemeng
    repo_name: aqua-registry-mirror
    ref: mirror-20260322
    path: registry.yaml
```

Then `aqua install` as normal. All downloads are routed through the configured mirrors.

### Policy as Code (required for aqua v2+)

From aqua v2, only the Standard Registry is allowed by default. Because this mirror is a `github_content` or `github_release` registry, you must create a Policy file to allow it.

**Step 1 — Create `aqua-policy.yaml`**

The registry entry in the policy file **must match the `type` you declared in `aqua.yaml`**.

```yaml
---
# aqua Policy
# https://aquaproj.github.io/
registries:
  - type: standard
    ref: semver(">= 3.0.0")
  - name: mirror
    type: github_content  # must match the type in aqua.yaml
    repo_owner: lvyuemeng
    repo_name: aqua-registry-mirror
    # ref is optional; omitting it allows any ref
    path: registry.yaml
packages:
  - registry: standard
  - registry: mirror
```

**Step 2 — Register the policy file**

Choose one of the two approaches below depending on your setup.

---

#### Option A: User-space / global (recommended, no `.git` required)

Set the `AQUA_POLICY_CONFIG` environment variable to the absolute path of your policy file.
aqua trusts files listed here **without requiring `aqua policy allow`**.

**Linux / macOS** (add to your shell profile for persistence):

```sh
export AQUA_POLICY_CONFIG="/path/to/aqua-policy.yaml:$AQUA_POLICY_CONFIG"
```

**Windows PowerShell** (add to your `$PROFILE` for persistence):

```powershell
$env:AQUA_POLICY_CONFIG = "C:\path\to\aqua-policy.yaml"
```

That is all. `aqua install` will work immediately — no `aqua policy allow` step needed.

---

#### Option B: Inside a Git repository

If your `aqua.yaml` lives inside a git repository, place `aqua-policy.yaml` at the repository root alongside `.git`, then run:

```sh
aqua policy allow "/absolute/path/to/aqua-policy.yaml"
```

If you modify `aqua-policy.yaml` later (even adding a blank line), run `aqua policy allow` again.

---

> **CI usage:** If you use `aquaproj/aqua-installer` in GitHub Actions, add `policy_allow: "true"` to avoid the manual allow step:
>
> ```yaml
> - uses: aquaproj/aqua-installer@11dd79b4e498d471a9385aa9fb7f62bb5f52a73c # v4.0.4
>   with:
>     aqua_version: v2.48.3
>     policy_allow: "true"
> ```

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