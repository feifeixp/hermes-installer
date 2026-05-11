// ─────────────────────────────────────────────────────────────────────────────
// Phase 2 M3 — instance keep-alive heartbeat.
//
// Runs only on chat-<userId>.neowow.studio (Phase 2 spawned instances).
// Pings app.neowow.studio/api/me/instance/heartbeat on:
//
//   - Page load (initial signal)
//   - Tab focus / visibility change → visible
//   - Every chat message send (hooked into send())
//   - Every 2 minutes (catch-all so a tab left open keeps the instance
//     alive for as long as the cron's IDLE_TIMEOUT_MIN tolerates — but
//     IDLE_TIMEOUT_MIN is conservative because we DON'T want a forever-
//     open idle tab to keep paying for an instance the user isn't
//     actually using)
//
// On Phase 1 chat.neowow.studio (shared instance, no per-user owner)
// the heartbeat is a no-op — the endpoint returns {hasInstance: false}
// and we silently skip future pings.
//
// Auth: reads the `neoToken` cookie set at Domain=.neowow.studio by
// app.neowow.studio's OAuth callback, then forwards it as
// `Authorization: Bearer <jwt>` to the heartbeat endpoint. Cookies
// alone wouldn't work (resolveCaller in the dashboard reads from the
// Authorization header).
//
// Failures are silent — the heartbeat is "nice to have", not required.
// If app.neowow.studio is down or returns 401, we just keep trying
// on the next trigger. Dashboard cron is the final safety net.
// ─────────────────────────────────────────────────────────────────────────────

(function () {
  // Only run on subdomains that LOOK like Phase 2 per-user instances.
  // Skip on the shared Phase 1 chat.neowow.studio (no instance row to
  // heartbeat for) and on any non-neowow host (local dev, etc.).
  const host = window.location.hostname;
  if (!/^chat-[a-z0-9-]+\.neowow\.studio$/.test(host)) {
    return;
  }

  const HEARTBEAT_URL = 'https://app.neowow.studio/api/me/instance/heartbeat';
  const PERIODIC_MS   = 2 * 60 * 1000;   // every 2 minutes

  // Skip future pings after the API tells us this isn't a tracked
  // instance (e.g. user destroyed it from another tab). Saves CPU +
  // network for no benefit.
  let _disabled = false;
  let _lastPing = 0;
  const PING_DEBOUNCE_MS = 5000;          // don't ping more than once per 5s

  function readNeoTokenCookie() {
    const m = document.cookie.match(/(?:^|;\s*)neoToken=([^;]+)/);
    if (!m) return null;
    try {
      return decodeURIComponent(m[1]);
    } catch {
      return m[1];
    }
  }

  async function ping(reason) {
    if (_disabled) return;
    const now = Date.now();
    if (now - _lastPing < PING_DEBOUNCE_MS) return;
    _lastPing = now;

    const token = readNeoTokenCookie();
    if (!token) {
      // No cookie → user isn't logged in. The chat won't load anyway
      // (auth.py redirects), so this branch shouldn't normally fire.
      return;
    }

    try {
      const r = await fetch(HEARTBEAT_URL, {
        method:  'POST',
        headers: {
          'Authorization': 'Bearer ' + token,
          'Content-Type':  'application/json',
        },
        credentials: 'include',           // also send cookies (belt and suspenders)
        body:        '{}',
        cache:       'no-store',
      });

      if (r.ok) {
        const d = await r.json().catch(() => null);
        if (d && d.hasInstance === false) {
          // No instance row → no point pinging. Stop heartbeat for
          // this page lifetime. Reload will re-evaluate (e.g. user
          // recreated the instance).
          _disabled = true;
        }
      } else if (r.status === 401 || r.status === 403) {
        // Auth issue — likely cookie expired or owner mismatch.
        // Whatever happens, retrying won't help — stop.
        _disabled = true;
      }
      // 5xx → silently fall through; next trigger retries.
    } catch {
      // Network error / CORS / etc — silent. Retry on next trigger.
    }
  }

  // ── Triggers ──────────────────────────────────────────────────────

  // 1. Page load — fire one immediately so the dashboard knows the
  //    user has the chat open, even before they send a message.
  ping('load');

  // 2. Tab focus / visibility — when user comes back to the tab,
  //    reset the idle counter.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') ping('visible');
  });
  window.addEventListener('focus', () => ping('focus'));

  // 3. Periodic catch-all. 2 min cadence so a single ping miss doesn't
  //    immediately accumulate against the 15 min idle threshold.
  setInterval(() => ping('periodic'), PERIODIC_MS);

  // 4. Chat send — hook the global send() function if it exists.
  //    messages.js defines `async function send()`; we wrap it after
  //    a small delay so the original definition is in place.
  const hookSend = () => {
    if (typeof window.send !== 'function') return false;
    const _orig = window.send;
    window.send = async function (...args) {
      ping('chat-send');                  // fire-and-forget
      return _orig.apply(this, args);
    };
    return true;
  };
  // messages.js may not have run yet at the time this script executes.
  // Try immediately, then on DOMContentLoaded, then once more on full
  // page load — one of those will be after messages.js evaluates.
  if (!hookSend()) {
    document.addEventListener('DOMContentLoaded', () => {
      if (!hookSend()) {
        window.addEventListener('load', () => { hookSend(); });
      }
    });
  }

  // Expose for debugging — `window.neowowHeartbeatPing()` in console
  // triggers a manual heartbeat. Useful for sanity-checking auth.
  window.neowowHeartbeatPing = () => ping('manual');
})();
