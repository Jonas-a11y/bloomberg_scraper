"""Auth gate for the Scraper panel and its data-loading endpoints.

The Scraper UI exposes triggers for jobs that hit Bloomberg / Wikidata /
Kaggle / GDELT — heavy and easy to abuse if the deployed instance is
publicly reachable. We gate them behind a simple shared-password
challenge.

Design:
  - Password lives in the SCRAPER_PASSWORD env var. If unset, auth is
    DISABLED and the panel works as before (local dev / single-user
    setups stay frictionless).
  - On successful POST to /api/scraper/auth the server sets an
    HttpOnly cookie containing sha256(server_secret). The cookie can't
    be read by JS so a stolen XSS payload can't lift the password,
    only ride along.
  - The server secret defaults to a random 64-char string generated at
    process start — invalidating all cookies on restart. For
    multi-worker deployments set SCRAPER_SESSION_SECRET in env so all
    workers share one.
  - Constant-time comparisons everywhere via secrets.compare_digest.

Protected endpoints attach require_scraper_auth() as a FastAPI
Depends; unprotected (read-only) endpoints don't change.
"""
import hashlib
import os
import secrets

from fastapi import HTTPException, Request, Response, status


SCRAPER_PASSWORD = os.environ.get("SCRAPER_PASSWORD")

# Server-side secret used to derive the cookie value. Random per
# process unless overridden — see module docstring.
_SESSION_SECRET = (
    os.environ.get("SCRAPER_SESSION_SECRET") or secrets.token_hex(32)
)

COOKIE_NAME = "scraper_session"
COOKIE_MAX_AGE_SEC = 30 * 24 * 3600  # 30 days


def is_auth_required() -> bool:
    """Auth is on iff SCRAPER_PASSWORD is set in the env. Lets local
    dev keep working without an env var."""
    return bool(SCRAPER_PASSWORD)


def _expected_cookie() -> str:
    """The cookie value that proves auth. Recomputed from the server
    secret on each request — no shared state between workers required
    if SCRAPER_SESSION_SECRET is set."""
    return hashlib.sha256(_SESSION_SECRET.encode()).hexdigest()


def check_password(password: str) -> bool:
    """Constant-time password comparison. Returns True when auth is
    disabled (no SCRAPER_PASSWORD) so the auth endpoint stays usable."""
    if not is_auth_required():
        return True
    if not password:
        return False
    return secrets.compare_digest(password, SCRAPER_PASSWORD or "")


def issue_cookie(response: Response) -> None:
    """Set the auth cookie on the response. Idempotent — re-issuing
    just refreshes max-age."""
    response.set_cookie(
        COOKIE_NAME,
        _expected_cookie(),
        httponly=True,
        samesite="strict",
        max_age=COOKIE_MAX_AGE_SEC,
        # Secure flag: only set if behind https. We can't reliably
        # detect that from inside FastAPI on every deployment, so
        # leave it off — the same-origin SameSite=strict still
        # prevents CSRF; the proxy is expected to terminate TLS.
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, samesite="strict")


def is_authed(request: Request) -> bool:
    """Public helper: check whether the incoming request carries a
    valid auth cookie. Used by status endpoints and the dependency."""
    if not is_auth_required():
        return True
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return False
    return secrets.compare_digest(cookie, _expected_cookie())


def require_scraper_auth(request: Request) -> None:
    """FastAPI dependency: gate any endpoint that should require the
    Scraper password. Drops to a no-op when SCRAPER_PASSWORD isn't
    set so local development isn't disturbed."""
    if not is_authed(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Scraper actions require authentication",
        )
