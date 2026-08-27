"""Test-deployment harness: all ten components behind one ASGI app.

WHAT THIS IS
    A way to give the company one URL where every component's API can be
    exercised. Each component is mounted under its own path prefix and keeps its
    own routes, its own settings and its own composition root.

WHAT THIS IS NOT
    **It is not the coaching router.** The router (integration brief §7.1) reads
    session mode and type and decides which component handles a learner's turn.
    Nothing here decides anything - it mounts ten independent applications side
    by side. A case-linked question sent to /uc03 will be answered by UC-03 with
    no disclaimer, because no routing rule exists yet to prevent it.

    It is also not the topology-B composition root described in brief §5. No
    port is wired to a sibling component: UC-03's LearnerContextProvider is
    still its own mock, not UC-02. Every port in every component resolves to a
    mock or in-memory adapter, exactly as it does in the test suites.

CONSEQUENCES FOR ANYONE TESTING AGAINST THIS
    * All data is fabricated. Courses, case files, NARIC levels and legal
      authorities come from each component's fixtures.
    * Identity is the development header adapter. Whoever sets `X-User-Id` is
      that user. Brief §9.1 requires this be replaced before any deployment
      touching real data - so do not point this at real data.
    * State is in-memory and per-process. It resets on every redeploy, and with
      more than one replica two requests can land on different state.
    * The legal content is illustrative (Decision 2, unresolved). It is not
      authored by a qualified lawyer and no jurisdiction is confirmed.

A component that fails to construct is mounted as a 503 that says why. That is
deliberate: a loud, visible failure, never a silent fall back to something that
looks like it works.
"""

from __future__ import annotations

import logging
import os
import sys
import traceback
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"

log = logging.getLogger("platform.deploy")

#: prefix -> (import path of the module holding create_app, sys.path entry, title)
COMPONENTS: tuple[tuple[str, str, Path, str], ...] = (
    ("uc01", "uc01.api.app", PACKAGES / "uc01", "Coaching session initiation"),
    ("uc02", "uc02.main", PACKAGES / "uc02", "Contextual awareness setup"),
    ("uc03", "uc03.api", PACKAGES / "uc03", "Legal concept Q&A"),
    ("uc04", "uc04.api.app", PACKAGES / "uc04" / "src", "Course content coaching"),
    ("uc05", "uc05.api.app", PACKAGES / "uc05", "Socratic method coaching"),
    ("uc06", "uc06.api.app", PACKAGES / "uc06", "Case-linked coaching"),
    ("uc07", "uc07.api.app", PACKAGES / "uc07", "Knowledge gap report"),
    ("uc08", "uc08.api.app", PACKAGES / "uc08", "Streaks and milestones"),
    ("uc09", "uc09_summary.api.app", PACKAGES / "uc09", "Session summary and CPD export"),
    ("uc10", "uc10.api.app", PACKAGES / "uc10", "Feedback and content review"),
)


def _ensure_importable() -> None:
    for _, _, path, _ in COMPONENTS:
        entry = str(path)
        if entry not in sys.path:
            sys.path.insert(0, entry)


def _absolutise_cwd_relative_config() -> None:
    """UC-07 resolves its topic-description registry relative to the working
    directory (`uc07/config/topic_descriptions.json`, application/config.py:57).
    That holds when the component is run from its own directory, as its tests
    do, and breaks anywhere else - including here.

    The fix is a config value, not a code change: the path is already a setting,
    so point it at the file. This is the only per-component override the harness
    applies, and it is set only if the operator has not set it themselves.

    The name is UC-07's alone, so it does not collide with the eight shared
    setting names in PLATFORM_CONTRACT.md §11.
    """
    registry = PACKAGES / "uc07" / "uc07" / "config" / "topic_descriptions.json"
    if "TOPIC_DESCRIPTION_REGISTRY_PATH" not in os.environ and registry.is_file():
        os.environ["TOPIC_DESCRIPTION_REGISTRY_PATH"] = str(registry)


def _failed_app(prefix: str, error: str) -> FastAPI:
    """A component that would not construct. It says so; it does not pretend."""
    stub = FastAPI(title=f"{prefix} (failed to start)")

    @stub.api_route("/{_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def _unavailable(_path: str) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": "component_failed_to_start",
                "component": prefix,
                "detail": error,
                "note": "This component did not construct. It has not been replaced "
                        "by a stand-in that returns plausible data.",
            },
        )

    return stub


def build() -> tuple[FastAPI, dict[str, str | None]]:
    _ensure_importable()
    _absolutise_cwd_relative_config()
    from importlib import import_module

    root = FastAPI(
        title="AI Coaching Agent - component test deployment",
        docs_url=None,
        redoc_url=None,
    )
    status: dict[str, str | None] = {}

    mounted: dict[str, FastAPI] = {}
    for prefix, module_path, _, title in COMPONENTS:
        try:
            module = import_module(module_path)
            sub = module.create_app()
            root.mount(f"/{prefix}", sub, name=prefix)
            mounted[prefix] = sub
            status[prefix] = None
            log.info("mounted %s (%s)", prefix, title)
        except Exception as exc:  # noqa: BLE001 - the reason is the payload
            detail = f"{type(exc).__name__}: {exc}"
            log.error("component %s failed to start: %s", prefix, detail)
            log.debug("%s", traceback.format_exc())
            root.mount(f"/{prefix}", _failed_app(prefix, detail), name=prefix)
            status[prefix] = detail

    @root.get("/healthz")
    async def healthz() -> JSONResponse:
        broken = {k: v for k, v in status.items() if v}
        return JSONResponse(
            status_code=200 if not broken else 503,
            content={
                "status": "ok" if not broken else "degraded",
                "mounted": len(status) - len(broken),
                "of": len(status),
                "failed": broken,
            },
        )

    @root.get("/", response_class=HTMLResponse)
    async def index() -> str:
        rows = []
        for prefix, _, _, title in COMPONENTS:
            err = status[prefix]
            state = (
                '<span class="ok">mounted</span>'
                if err is None
                else f'<span class="bad">failed</span><br><code>{err}</code>'
            )
            if err is not None:
                docs = "&mdash;"
            elif mounted[prefix].docs_url:
                docs = f'<a href="/{prefix}{mounted[prefix].docs_url}">/{prefix}{mounted[prefix].docs_url}</a>'
            else:
                # UC-06 disables docs, redoc and openapi on purpose: it is the
                # component that handles privileged case material. That decision
                # is its own and is not overridden here - the routes are listed
                # instead, so a tester can still exercise it.
                paths = sorted(
                    {
                        getattr(r, "path", "")
                        for r in mounted[prefix].routes
                        if getattr(r, "path", "").startswith("/")
                    }
                )
                listed = "<br>".join(f"<code>/{prefix}{q}</code>" for q in paths if q != "/")
                docs = (
                    '<em>docs disabled by the component</em><br>' + listed
                    if listed
                    else "<em>docs disabled by the component</em>"
                )
            rows.append(
                f"<tr><td><code>{prefix}</code></td><td>{title}</td>"
                f"<td>{docs}</td><td>{state}</td></tr>"
            )
        return _INDEX.replace("{{ROWS}}", "\n".join(rows))

    return root, status


_INDEX = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Coaching Agent - component test deployment</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 16px/1.55 system-ui, -apple-system, Segoe UI, sans-serif;
         max-width: 60rem; margin: 2rem auto; padding: 0 1rem; }
  h1 { font-size: 1.5rem; margin-bottom: .25rem; }
  .sub { color: #666; margin-top: 0; }
  table { border-collapse: collapse; width: 100%; margin: 1.5rem 0; }
  th, td { text-align: left; padding: .5rem .6rem; border-bottom: 1px solid #8883;
           vertical-align: top; }
  th { font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; color: #666; }
  code { font-size: .9em; }
  pre { background: #8881; padding: .8rem 1rem; border-radius: .4rem;
        overflow-x: auto; font-size: .85rem; }
  h2 { font-size: 1.1rem; margin-top: 2rem; }
  .ok { color: #1a7f37; font-weight: 600; }
  .bad { color: #b3261e; font-weight: 600; }
  .warn { border: 1px solid #b3261e; border-radius: .4rem; padding: .9rem 1.1rem;
          margin: 1.5rem 0; }
  .warn h2 { font-size: 1rem; margin: 0 0 .5rem; color: #b3261e; }
  .warn ul { margin: .4rem 0 0; padding-left: 1.1rem; }
  li { margin: .3rem 0; }
</style></head><body>
<h1>AI Coaching Agent &mdash; component test deployment</h1>
<p class="sub">Ten backend components, each mounted under its own prefix.
Open a component's <code>/docs</code> to exercise its API.</p>

<div class="warn">
<h2>Read before testing</h2>
<ul>
<li><strong>This is not the coaching router.</strong> Nothing here decides which component
handles a learner's turn &mdash; that is unbuilt (brief &sect;7.1). A case-linked question
sent to <code>/uc03</code> is answered by UC-03 <em>without a disclaimer</em>, because no
routing rule exists yet to stop it.</li>
<li><strong>No component is wired to another.</strong> Every port resolves to a mock or
in-memory adapter. UC-03's learner context is its own fixture, not UC-02.</li>
<li><strong>All data is fabricated.</strong> Courses, case files, NARIC levels and legal
authorities are fixtures.</li>
<li><strong>Identity is a development shim.</strong> Whoever sets the <code>X-User-Id</code>
header is that user. Do not send real data here.</li>
<li><strong>State is in-memory</strong> and resets on redeploy.</li>
<li><strong>The legal content is illustrative</strong> &mdash; not written by a qualified
lawyer, jurisdiction unconfirmed. It is Decision 2 in the decisions register and is
unresolved.</li>
</ul>
</div>

<table>
<tr><th>Prefix</th><th>Component</th><th>API docs</th><th>Status</th></tr>
{{ROWS}}
</table>

<h2>How to authenticate</h2>
<p class="sub">The ten components were built independently and resolve identity four different
ways. There is no single sign-in: each needs its own header. This is itself an open platform
gap &mdash; see <code>PLATFORM_CONTRACT.md</code>.</p>

<table>
<tr><th>Prefix</th><th>Send</th><th>Working demo values</th></tr>
<tr><td><code>uc01</code></td><td><code>X-Dev-User</code></td><td><code>dev-alice</code></td></tr>
<tr><td><code>uc02</code></td><td><code>X-User-Id</code></td><td>any id</td></tr>
<tr><td><code>uc03</code></td><td><code>Authorization: Bearer &hellip;</code></td>
    <td><code>dev-token-alice</code> / <code>dev-token-bob</code><br>
        sessions <code>session-alice-1</code>, <code>session-alice-2</code>,
        <code>session-bob-1</code></td></tr>
<tr><td><code>uc04</code></td><td><code>x-user-id</code></td><td>any id</td></tr>
<tr><td><code>uc05</code></td><td><code>X-User-Id</code></td><td>any id</td></tr>
<tr><td><code>uc06</code></td><td><code>x-uc06-user-id</code></td>
    <td>case files <code>CASE-FULL-001</code>, <code>CASE-SPARSE-002</code>,
        <code>CASE-DENIED-004</code>, <code>CASE-UNAVAILABLE-006</code> and others for the
        degradation paths</td></tr>
<tr><td><code>uc07</code></td><td><code>X-User-Id</code></td><td>any id</td></tr>
<tr><td><code>uc08</code></td><td><code>X-UC08-Subject</code></td><td>any id</td></tr>
<tr><td><code>uc09</code></td><td><code>X-User-Id</code></td><td>any id</td></tr>
<tr><td><code>uc10</code></td><td><code>X-User-Id</code>; admin routes also
    <code>X-Admin-Id</code> + <code>X-Admin-Token</code></td><td>any id</td></tr>
</table>

<h2>Two calls worth making first</h2>
<pre><code># UC-03: a four-part answer with a verified authority
curl -X POST $BASE/uc03/uc03/questions   -H 'content-type: application/json'   -H 'Authorization: Bearer dev-token-alice'   -d '{"question":"What is negligence in tort law?","session_id":"session-alice-1"}'

# UC-06: a case-linked answer. Check the disclaimer field is present and verbatim,
# then ask "Will my client win this case?" and watch it redirect to the legal test
# instead of predicting an outcome.
curl -X POST $BASE/uc06/api/v1/case-coaching/questions   -H 'content-type: application/json'   -H 'x-uc06-user-id: user-alice'   -d '{"session_id":"s1","question":"How does the defence of duress apply here?",
       "case_file_id":"CASE-FULL-001"}'</code></pre>

<h2>One thing to look at while testing</h2>
<p>Ask UC-01 to open a session and ask UC-03 a question, then compare the qualification level
in the two responses. UC-01 returns <code>"naric_level": 8</code> &mdash; an integer &mdash;
with <code>"naric_level_source": "naric"</code>. UC-03 returns <code>"LEVEL_7"</code> with
<code>"retrieved"</code>. Same concept, two incompatible types and two vocabularies. Nothing
fails at build time; it fails at runtime on data that looks plausible. It is the largest open
item in the contract register, and it is the reason these components are not yet wired to each
other.</p>

<p class="sub">Health: <a href="/healthz"><code>/healthz</code></a></p>
</body></html>
"""

app, _status = build()
