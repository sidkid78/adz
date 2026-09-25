"""
signed_in_proof.py — render every page as a signed-in user with real rows

THE GAP THIS CLOSES
-------------------
runtime_proof.py requested `/` signed out. For any app behind a login
that is a redirect to /login, so the check proved the login page renders
and nothing else. The first time a person signed in to a finished build,
the dashboard crashed:

    Attempted to call mapRowToAction() from the server but
    mapRowToAction is on the client.

Two conditions hid it, and this module supplies both:
  - a SESSION. Signed out, the page never ran past its redirect;
  - DATA. `rows.map(mapRowToAction)` over an empty list never calls the
    function, so even a signed-in render of an empty database passes.

HOW
---
  1. Create a throwaway user through the Supabase admin API.
  2. Seed one row in every table, in foreign-key order, from PostgREST's
     own OpenAPI description: keys, required columns, enums, defaults.
     Seeding is best effort — a table it cannot fill is reported, never
     failed on. The pages are the gate, not the seeder.
  3. Sign in, build the exact cookie @supabase/ssr writes
     (sb-<ref>-auth-token = "base64-" + base64url(session JSON), chunked
     past 3180 characters), and request every static page route.
  4. Fail on a 5xx, a bounce to a login page, or an empty body, naming
     the page's SOURCE FILE — so blame_tickets can route the failure to
     the ticket that owns it — plus the server's own log lines.
  5. Delete the user; the seeded rows cascade with it.

Only runs when the app uses @supabase/ssr and .env.local carries the URL,
anon key and service key. Otherwise it says why it skipped.
"""

import base64
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

REQUEST_TIMEOUT = 20
COOKIE_CHUNK = 3180          # @supabase/ssr's MAX_CHUNK_SIZE
LOGIN_HINTS = ("login", "signin", "sign-in", "auth")


# ---- HTTP -------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_NO_REDIRECT = urllib.request.build_opener(_NoRedirect)


def _call(method: str, url: str, headers: dict[str, str], body: object = None,
          follow: bool = True) -> tuple[int, dict[str, str], str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={**headers, **({"Content-Type": "application/json"} if data else {})})
    opener = urllib.request.build_opener() if follow else _NO_REDIRECT
    try:
        with opener.open(req, timeout=REQUEST_TIMEOUT) as r:
            return r.status, dict(r.headers), r.read(200_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read(200_000).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        return 0, {}, str(e)


# ---- Setup ------------------------------------------------------------------
def read_env(repo: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    path = repo / ".env.local"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*\"?([^\"\n]*)\"?\s*$", line)
            if m:
                env[m.group(1)] = m.group(2)
    return env


def uses_supabase_auth(repo: Path) -> bool:
    try:
        manifest = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    deps = {**manifest.get("dependencies", {}), **manifest.get("devDependencies", {})}
    return "@supabase/ssr" in deps


def page_routes(repo: Path) -> tuple[dict[str, str], list[str]]:
    """{route: source file} for static pages, and the dynamic ones skipped.
    Route groups `(x)` and parallel slots `@x` do not appear in the URL."""
    routes: dict[str, str] = {}
    dynamic: list[str] = []
    app = repo / "src" / "app"
    for page in sorted([*app.rglob("page.tsx"), *app.rglob("page.ts")]):
        parts = page.relative_to(app).parts[:-1]
        rel = page.relative_to(repo).as_posix()
        if any(p.startswith("[") for p in parts):
            dynamic.append(rel)
            continue
        segments = [p for p in parts if not p.startswith(("(", "@"))]
        routes.setdefault("/" + "/".join(segments), rel)
    return routes, dynamic


# ---- Seeding ----------------------------------------------------------------
_FK = re.compile(r"Foreign Key to `(\w+)\.(\w+)`")


def _value(prop: dict, user_id: str) -> object:
    fmt = (prop.get("format") or "").lower()
    if prop.get("enum"):
        return prop["enum"][0]
    if fmt == "uuid":
        return str(uuid.uuid4())
    if fmt in ("jsonb", "json"):
        return {}
    if fmt.endswith("[]"):
        return []
    if fmt.startswith("timestamp"):
        return datetime.now(UTC).isoformat()
    if fmt == "date":
        return datetime.now(UTC).date().isoformat()
    if fmt in ("integer", "bigint", "smallint", "numeric", "real", "double precision") or fmt.startswith("numeric"):
        return 1
    if fmt == "boolean":
        return False
    if fmt in ("text", "character varying", "character", "citext") or prop.get("type") == "string":
        return "runtime-proof"
    raise ValueError(f"no value for column type {fmt or '?'}")


def seed_rows(url: str, service_key: str, user_id: str) -> list[str]:
    """Insert one row per table, parents before children. Returns notes."""
    auth = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
    status, _, body = _call("GET", f"{url}/rest/v1/", {**auth, "Accept": "application/openapi+json"})
    if status != 200:
        return [f"seed: could not read the schema (HTTP {status})"]
    tables = json.loads(body).get("definitions", {})
    seeded: dict[str, dict] = {}
    notes: list[str] = []
    pending = dict(tables)

    for _ in range(len(tables) + 1):
        progressed = False
        for name, table in list(pending.items()):
            props = table.get("properties", {})
            required = set(table.get("required", []))
            fks = {col: _FK.search(p.get("description", "")) for col, p in props.items()}
            # Wait for required parents; optional ones are filled if present.
            if any(m and m.group(1) != name and col in required and m.group(1) not in seeded
                   for col, m in fks.items()):
                continue
            row: dict[str, object] = {}
            try:
                for col, prop in props.items():
                    fk = fks[col]
                    is_pk = "Primary Key" in prop.get("description", "")
                    if fk and fk.group(1) in seeded:
                        row[col] = seeded[fk.group(1)].get(fk.group(2))
                    elif col not in required or "default" in prop:
                        continue
                    elif is_pk and (prop.get("format") == "uuid") and not fk:
                        # A uuid key nobody generates is keyed on the user
                        # (profiles reference auth.users, outside public).
                        row[col] = user_id
                    else:
                        row[col] = _value(prop, user_id)
            except ValueError as exc:
                notes.append(f"seed: skipped {name} ({exc})")
                del pending[name]
                continue
            status, _, body = _call(
                "POST", f"{url}/rest/v1/{name}",
                {**auth, "Prefer": "return=representation,resolution=ignore-duplicates"}, row)
            inserted = json.loads(body) if status in (200, 201) and body.strip().startswith("[") else []
            if not inserted and status in (200, 201):
                # Already there — e.g. a sign-up trigger made the profile.
                pk = next((c for c, p in props.items() if "Primary Key" in p.get("description", "")), None)
                if pk and row.get(pk):
                    _, _, got = _call("GET", f"{url}/rest/v1/{name}?{pk}=eq.{row[pk]}", auth)
                    inserted = json.loads(got) if got.strip().startswith("[") else []
            if inserted:
                seeded[name] = inserted[0]
            else:
                notes.append(f"seed: skipped {name} (HTTP {status}: {body[:120].strip()})")
            del pending[name]
            progressed = True
        if not pending or not progressed:
            break
    notes.extend(f"seed: skipped {n} (a required parent could not be seeded)" for n in pending)
    notes.insert(0, f"seed: {len(seeded)}/{len(tables)} table(s) have a row")
    return notes


# ---- Session ----------------------------------------------------------------
def session_cookies(url: str, session: dict) -> dict[str, str]:
    """The cookie(s) @supabase/ssr itself writes, byte for byte in format."""
    ref = urllib.parse.urlparse(url).hostname.split(".")[0]
    name = f"sb-{ref}-auth-token"
    raw = json.dumps(session, separators=(",", ":"))
    value = "base64-" + base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    if len(value) <= COOKIE_CHUNK:
        return {name: value}
    return {f"{name}.{i}": value[i * COOKIE_CHUNK:(i + 1) * COOKIE_CHUNK]
            for i in range((len(value) + COOKIE_CHUNK - 1) // COOKIE_CHUNK)}


# ---- The check --------------------------------------------------------------
def _noop() -> None:
    return None


def signed_in_check(repo: Path, base: str, server_log: list[str]
                    ) -> tuple[list[str], list[str], dict[str, str] | None, Callable[[], None]]:
    """(log lines, failures, cookies for a signed-in screenshot, cleanup).

    The caller runs `cleanup` — it deletes the test user — AFTER any
    screenshot. Deleting it here first made the "signed-in" screenshot a
    picture of the login page: the session belonged to a user that no
    longer existed."""
    if not uses_supabase_auth(repo):
        return ["  signed-in: skipped — the app does not use @supabase/ssr"], [], None, _noop
    env = read_env(repo)
    url, anon, service = (env.get("NEXT_PUBLIC_SUPABASE_URL"), env.get("NEXT_PUBLIC_SUPABASE_ANON_KEY"),
                          env.get("SUPABASE_SERVICE_ROLE_KEY"))
    if not (url and anon and service):
        return ["  signed-in: skipped — .env.local lacks the Supabase URL/anon/service keys"], [], None, _noop

    admin = {"apikey": service, "Authorization": f"Bearer {service}"}
    email = f"runtime-proof-{int(time.time())}-{secrets.token_hex(3)}@factory.local"
    password = secrets.token_urlsafe(18)
    status, _, body = _call("POST", f"{url}/auth/v1/admin/users", admin,
                            {"email": email, "password": password, "email_confirm": True})
    if status not in (200, 201):
        return [f"  signed-in: skipped — could not create a test user (HTTP {status}: {body[:160]})"], [], None, _noop
    user_id = json.loads(body)["id"]
    log: list[str] = []
    failures: list[str] = []
    cookies: dict[str, str] | None = None

    def cleanup() -> None:
        status, _, _ = _call("DELETE", f"{url}/auth/v1/admin/users/{user_id}", admin)
        if status not in (200, 204):
            print(f"  signed-in: could not delete test user {email} (HTTP {status})")

    try:
        log.extend(f"  {n}" for n in seed_rows(url, service, user_id))
        status, _, body = _call("POST", f"{url}/auth/v1/token?grant_type=password",
                                {"apikey": anon}, {"email": email, "password": password})
        if status != 200:
            cleanup()
            return log + [f"  signed-in: skipped — sign-in failed (HTTP {status})"], [], None, _noop
        cookies = session_cookies(url, json.loads(body))
        header = {"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())}

        routes, dynamic = page_routes(repo)
        for route, source in routes.items():
            mark = len(server_log)
            status, headers, html = _call("GET", base + route, header, follow=False)
            location = headers.get("Location") or headers.get("location") or ""
            log.append(f"  {route} (signed in) -> HTTP {status}" + (f" -> {location}" if location else ""))
            if 300 <= status < 400:
                if any(h in location.lower() for h in LOGIN_HINTS) and not any(h in route for h in LOGIN_HINTS):
                    failures.append(
                        f"{route} ({source}) redirected a signed-in user to {location}: the session "
                        f"was not accepted. Were NEXT_PUBLIC_SUPABASE_* set when the app was built?")
                continue
            if status != 200 or len(html.strip()) < 200:
                errors = [line for line in server_log[mark:] if line.strip()][-12:]
                failures.append(
                    f"{route} ({source}) failed for a signed-in user with seeded data: HTTP {status}"
                    + ("\n    server log:\n      " + "\n      ".join(errors) if errors else ""))
        if dynamic:
            log.append(f"  signed-in: {len(dynamic)} dynamic route(s) not requested: {', '.join(dynamic[:4])}")
    except Exception:
        cleanup()
        raise
    return log, failures, (cookies if not failures else None), cleanup
