# Camofox Browser Plugin

This bundled plugin exposes a separate `camofox_*` toolset for the
[jo-inc/camofox-browser](https://github.com/jo-inc/camofox-browser) REST API.
It is intentionally separate from Hermes `browser_*` tools so a local
Chrome/CDP browser and Camofox can be available in the same agent session.

## Setup

Run Camofox separately from Hermes, for example as its own Docker container,
Compose service, or network service. This plugin does not install Camofox and
does not start the Camofox server process inside the Hermes environment.

Configure Hermes:

```yaml
browser:
  cdp_url: "http://127.0.0.1:9222"

plugins:
  entries:
    camofox:
      url: "http://localhost:9377"
      vnc_url: "http://localhost:6080"  # optional noVNC URL to surface to agents
      api_key: ""
      managed_persistence: true
      adopt_existing_tab: true
```

Important: remove legacy `CAMOFOX_URL` from `~/.hermes/.env` when using this
plugin for split mode. `CAMOFOX_URL` still activates the old core integration
and hijacks `browser_*` tools. This plugin uses `plugins.entries.camofox.url`
or the optional `CAMOFOX_TOOL_URL` env var instead.

This bundled backend plugin auto-loads with Hermes. If split into a standalone
GitHub plugin repo, put `plugin.yaml` at the repository root. Hermes installs
GitHub plugins as `~/.hermes/plugins/<plugin-name>/`, where `<plugin-name>` is
the manifest `name`, so the enable key for the standalone repo would be
`browser-camofox` unless you change the manifest name.

Recovery is connect-only. If the Camofox HTTP server is reachable but reports
`browserRunning=false`, the plugin may call Camofox's `/start` endpoint to start
the browser process inside that existing service. If the HTTP server itself is
down, the plugin returns a clear error instead of trying to launch or install
Camofox.

## Tool Split

- Use `browser_*` for Chrome, Brave, Edge, or Chromium through CDP.
- Use `camofox_*` for Camofox anti-detection browsing.
- Avoid terminal `curl` calls to the Camofox server during normal agent use.

## Lazy-Loaded Pages

Some pages report navigation complete before JavaScript has populated records.
Use `delay_s` on navigation:

```text
camofox_navigate(url="https://example.com/search", delay_s=5)
```

The tool captures an initial snapshot, waits, captures a fresh snapshot, and
returns only the second snapshot.

## Large Snapshots

`camofox_snapshot` supports the Camofox offset pagination fields directly:

```text
camofox_snapshot(offset=80000)
camofox_snapshot(line_offset=501, line_limit=200)
camofox_snapshot(pattern="checkout", context=2)
```

The plugin does not silently truncate at 8000 characters or summarize through
an LLM unless `summarize=true` is explicitly provided.

## Visual Inspection

Use `camofox_vision` as the Camofox equivalent of `browser_vision`:

```text
camofox_vision(question="What is visible on this page?", annotate=true)
camofox_screenshot(full_page=true)
```

It captures the active Camofox screenshot, sends it to the configured Hermes
vision auxiliary model, and returns both `analysis` and `screenshot_path`.
Pass `full_page=true` to `camofox_vision` or `camofox_screenshot` to capture
the whole page top to bottom instead of only the current viewport.

If `plugins.entries.camofox.vnc_url` is configured, navigation and vision
results can also include the noVNC URL so the user can watch or take over the
browser visually. The plugin does not connect to raw VNC itself; it treats VNC
as a user-visible live-view URL.

## Recovery

When Camofox loses a tab or restarts the browser process, the plugin can:

- check `/health`
- call `/start` when the Camofox server is alive but the browser is stopped
- list live tabs for the configured `user_id`
- adopt a matching tab by `session_key`
- create a new tab for read-only operations when no live tab exists

Unsafe actions such as click, type, and key press are not blindly replayed
after creating a new tab. The tool returns a structured error asking the agent
to refresh refs and retry.
