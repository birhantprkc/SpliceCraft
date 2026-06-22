"""splicecraft_net — shared SSRF-hardened network-fetch primitives (Phase D, L0).

A small, dependency-light toolkit for SAFE outbound fetches, lifted out of the
hub so every network subsystem (NCBI Entrez/datasets, Kazusa codon tables,
HMM-DB downloads, the PyPI update check) shares ONE hardened path instead of
re-deriving it:
  * _build_hardened_url_opener — a urllib OpenerDirector with an explicit SSL
    context, a bounded redirect chain, and refusal of https->http downgrades.
    Calls the web-demo egress gate FIRST via _state._demo_block_network_hook
    (fail-closed: an unregistered hook raises, so no fetch runs unguarded).
  * _hmm_db_assert_content_type_ok — rejects HTML/JSON/XML bodies a hijacked
    mirror might return in place of the expected binary.
  * _sanitize_accession — clamp a user-supplied NCBI accession to a safe charset.
Re-exported by the hub so sc.<name> + every existing call site resolves
unchanged. SECURITY-sensitive: changes here touch the SSRF/egress boundary.
"""
from __future__ import annotations

import re as _re_mod

import splicecraft_state as _state


# Response-size caps for upstream fetches: defends against a compromised /
# misconfigured / man-in-the-middled upstream that streams gigabytes at us.
# NCBI esearch / esummary XML for a 200-id batch is ~50 KB in practice;
# 4 MB is wildly generous. Kazusa showcodon HTML is ~30 KB; 1 MB is plenty.
_NCBI_MAX_RESPONSE_BYTES   = 4 * 1024 * 1024
# NCBI socket timeout (seconds) — shared by the Entrez fetchers (fileio) + the
# online-BLAST poll loop (search). The UI worker can't otherwise cancel a hang.
_NCBI_TIMEOUT_S = 30


_HMM_DB_RETRY_BACKOFF_S = 0.25      # one retry, 250 ms backoff


_HMM_DB_MAX_REDIRECTS = 5


_HMM_DB_BAD_CONTENT_TYPES: frozenset[str] = frozenset({
    "text/html", "application/json", "application/xml",
})


def _assert_public_host(url: str) -> None:
    """Resolve the host in `url` and refuse private / loopback / link-local /
    reserved / multicast / unspecified addresses — blocking SSRF to internal
    services before any socket opens. The motivating vector: a user/agent-
    supplied HMM-DB download URL pointing at the cloud-metadata endpoint
    (``169.254.169.254``) or an intranet host. Raises `urllib.error.URLError`
    on a non-public target or an unresolvable host.

    Best-effort against DNS rebinding: this resolves the name and the
    subsequent connect resolves it again, so a hostile resolver could return a
    public IP here and a private one at connect time. For a localhost,
    single-tenant tool that residual TOCTOU window is acceptable — the common
    attacks (a literal private IP, or a name with a static private record) are
    blocked, and redirect targets are re-checked in the redirect handler."""
    import socket
    import ipaddress
    import urllib.error
    from urllib.parse import urlsplit
    host = urlsplit(url).hostname
    if not host:
        raise urllib.error.URLError(f"no host in URL {url!r}")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise urllib.error.URLError(f"cannot resolve host {host!r}: {exc}")
    for info in infos:
        ip = str(info[4][0])  # sockaddr address (str-coerce for the type checker)
        try:
            addr = ipaddress.ip_address(ip.split("%", 1)[0])  # strip IPv6 zone
        except ValueError:
            continue
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast
                or addr.is_unspecified):
            raise urllib.error.URLError(
                f"refusing fetch to non-public address {ip} (host {host!r})"
            )


def _build_hardened_url_opener():
    """Return a urllib `OpenerDirector` with hardened settings:
      * explicit SSL context (system trust store via
        `ssl.create_default_context`)
      * redirect cap (`_HMM_DB_MAX_REDIRECTS`)
      * refusal of https→http redirect downgrades — the HTTPS-only policy
        must hold across the whole redirect chain, not just the initial
        URL. Without this a hostile/misconfigured mirror could 30x a
        validated https URL to plaintext and strip TLS mid-transfer.
    Use via `opener.open(req, timeout=...)`. Returned opener is stateless
    — safe to share across threads. Shared by the HMM-DB downloader and
    the PyPI update-check fetch."""
    _state._demo_block_network_hook("Remote downloads")
    import ssl
    import urllib.request
    import urllib.error

    ctx = ssl.create_default_context()

    class _BoundedRedirectHandler(urllib.request.HTTPRedirectHandler):
        max_redirections = _HMM_DB_MAX_REDIRECTS

        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if (req.get_full_url().lower().startswith("https://")
                    and not str(newurl).lower().startswith("https://")):
                raise urllib.error.HTTPError(
                    newurl, code,
                    "refusing https->http redirect downgrade",
                    headers, fp,
                )
            # SSRF guard: a hostile/misconfigured mirror could 30x a public URL
            # to an internal host. Re-check the redirect target's address.
            _assert_public_host(str(newurl))
            return super().redirect_request(
                req, fp, code, msg, headers, newurl,
            )

    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        _BoundedRedirectHandler(),
    )
    # SSRF guard on the INITIAL request: wrap `.open` so no call site can skip
    # the host check (every redirect target is re-checked in the handler above).
    # Forward *args/**kwargs verbatim so the wrapper is signature-agnostic (the
    # timeout sentinel default never needs naming).
    _orig_open = opener.open

    def _guarded_open(fullurl, *args, **kwargs):
        _u = (fullurl.get_full_url()
              if hasattr(fullurl, "get_full_url") else fullurl)
        _assert_public_host(_u)
        return _orig_open(fullurl, *args, **kwargs)

    # setattr (not `opener.open = …`) so a static type-checker doesn't flag the
    # instance-level shadow of the bound method.
    setattr(opener, "open", _guarded_open)
    return opener


def _hmm_db_assert_content_type_ok(resp, url: str) -> None:
    """Raise ValueError if Content-Type indicates an error page
    instead of the binary payload we asked for. Hostile CDNs / 503
    interstitials commonly serve HTML or JSON with a 200 status —
    pre-sweep that landed as `db.hmm.gz` and bewildered the user
    later. Compares against `_HMM_DB_BAD_CONTENT_TYPES`."""
    ct_raw = (resp.headers.get("Content-Type") or "").strip().lower()
    # Strip any `;charset=...` suffix.
    ct = ct_raw.split(";", 1)[0].strip()
    if ct in _HMM_DB_BAD_CONTENT_TYPES:
        raise ValueError(
            f"server returned Content-Type {ct!r} (likely an error "
            f"page, not the binary download)"
        )


_NCBI_ACCESSION_RE = _re_mod.compile(r"[A-Za-z0-9._\-]{1,32}")


def _sanitize_accession(s: "str | None") -> "str | None":
    """Validate an NCBI accession against the allowed charset; return
    None if it fails so callers can 400 the request. Defends against
    accessions like ``L09137; rm -rf /`` smuggled into the URL.
    Type-strict: non-string input → None."""
    if not isinstance(s, str) or not s:
        return None
    s = s.strip()
    if _NCBI_ACCESSION_RE.fullmatch(s):
        return s
    return None


def _redact_url_credentials(url: str) -> str:
    """Strip `user:pass@` from a URL for safe logging. Real-world
    URLs never carry credentials for our two builtins, but a user's
    custom URL might."""
    if not isinstance(url, str):
        return ""
    try:
        from urllib.parse import urlparse, urlunparse
        parts = urlparse(url)
        if parts.username or parts.password:
            netloc = parts.hostname or ""
            if parts.port:
                netloc = f"{netloc}:{parts.port}"
            scrubbed = parts._replace(netloc=netloc)
            return urlunparse(scrubbed)
    except (ValueError, AttributeError):
        pass
    return url
