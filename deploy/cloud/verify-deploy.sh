#!/usr/bin/env bash
# verify-deploy.sh — sanity check a chat.neowow.studio deployment.
#
# Run this AFTER bootstrap-cloud.sh reports "WebUI is up!" to confirm
# the auth + reverse-proxy chain actually works end to end.
#
# Usage:
#   bash deploy/cloud/verify-deploy.sh chat.neowow.studio
#
# Exit code is non-zero if any step fails — usable in CI / cron.

set -euo pipefail

DOMAIN="${1:-chat.neowow.studio}"
ERRORS=0

ok()    { echo "  ✓ $*"; }
fail()  { echo "  ✗ $*" >&2; ERRORS=$((ERRORS + 1)); }
warn()  { echo "  ⚠ $*"; }
info()  { echo "→ $*"; }

# ── 1. Local WebUI process ─────────────────────────────────────────────
info "Local WebUI process (loopback :7891)"
if curl -fsS --max-time 5 http://127.0.0.1:7891/health 2>/dev/null | grep -q '"ok":true' \
   || curl -fsS --max-time 5 http://127.0.0.1:7891/health 2>/dev/null | grep -q 'ok'; then
    ok "GET /health returned 200"
else
    fail "GET http://127.0.0.1:7891/health failed"
    fail "Check: sudo systemctl status hermes-webui"
fi

# ── 2. systemd unit state ──────────────────────────────────────────────
info "systemd unit state"
if systemctl is-active --quiet hermes-webui; then
    ok "hermes-webui is active"
else
    fail "hermes-webui is NOT active — sudo systemctl status hermes-webui"
fi
if systemctl is-active --quiet caddy; then
    ok "caddy is active"
else
    fail "caddy is NOT active — sudo systemctl status caddy"
fi

# ── 3. Auth mode ───────────────────────────────────────────────────────
info "Auth mode (expecting neodomain)"
AUTH_MODE=$(systemctl show hermes-webui -p Environment --value 2>/dev/null \
    | tr ' ' '\n' | grep '^HERMES_WEBUI_AUTH_MODE=' | cut -d= -f2)
if [[ "$AUTH_MODE" == "neodomain" ]]; then
    ok "HERMES_WEBUI_AUTH_MODE=neodomain"
else
    warn "HERMES_WEBUI_AUTH_MODE=$AUTH_MODE (expected: neodomain)"
fi

# ── 4. Public TLS endpoint ─────────────────────────────────────────────
info "Public HTTPS endpoint (https://$DOMAIN)"
if ! command -v dig >/dev/null 2>&1; then
    apt-get install -y -qq dnsutils >/dev/null 2>&1 || true
fi
if command -v dig >/dev/null 2>&1; then
    DNS_IP=$(dig +short "$DOMAIN" | head -1)
    SELF_IP=$(curl -fsS --max-time 3 https://api.ipify.org 2>/dev/null || echo "?")
    if [[ -z "$DNS_IP" ]]; then
        fail "$DOMAIN does not resolve — set A record to $SELF_IP"
    elif [[ "$DNS_IP" != "$SELF_IP" && "$SELF_IP" != "?" ]]; then
        warn "$DOMAIN resolves to $DNS_IP but this server is $SELF_IP"
        warn "(might be Cloudflare proxy etc. — only a problem if HTTPS check fails)"
    else
        ok "DNS: $DOMAIN → $DNS_IP"
    fi
fi

# Curl the public domain. We expect 302 → app.neowow.studio (no cookie
# on this fresh request → neodomain auth redirects to OAuth start).
HTTP_CODE=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 \
    "https://$DOMAIN/" 2>/dev/null || echo "000")
case "$HTTP_CODE" in
    302|303|301)
        ok "GET https://$DOMAIN/ returned $HTTP_CODE (redirect to login)"
        REDIR=$(curl -sS -o /dev/null -w '%{redirect_url}' --max-time 10 \
            "https://$DOMAIN/" 2>/dev/null || echo "")
        if [[ "$REDIR" == *"app.neowow.studio/api/oauth/start"* ]]; then
            ok "Redirect target is OAuth start: $REDIR"
        else
            warn "Redirect goes to $REDIR (expected app.neowow.studio/api/oauth/start)"
        fi
        ;;
    200)
        warn "GET https://$DOMAIN/ returned 200 (auth not enforced?)"
        warn "Check HERMES_WEBUI_AUTH_MODE and recent journalctl"
        ;;
    000)
        fail "Could not connect to https://$DOMAIN/"
        fail "Check: DNS, port 443 open, Caddy logs (journalctl -u caddy)"
        ;;
    *)
        fail "GET https://$DOMAIN/ returned unexpected $HTTP_CODE"
        ;;
esac

# ── 5. TLS cert sanity ─────────────────────────────────────────────────
info "TLS certificate"
CERT_INFO=$(echo | openssl s_client -servername "$DOMAIN" \
    -connect "$DOMAIN:443" 2>/dev/null \
    | openssl x509 -noout -subject -issuer -enddate 2>/dev/null || echo "")
if [[ -n "$CERT_INFO" ]]; then
    ok "TLS cert present:"
    echo "$CERT_INFO" | sed 's/^/      /'
else
    fail "Could not fetch TLS cert from $DOMAIN:443"
fi

# ── 6. Dashboard reachability (sanity) ─────────────────────────────────
info "Dashboard sanity (the OAuth target)"
if curl -fsS --max-time 5 -o /dev/null -w '%{http_code}' \
        https://app.neowow.studio/api/me/whoami 2>/dev/null | grep -q '401'; then
    ok "app.neowow.studio reachable (401 with no token = expected)"
else
    warn "app.neowow.studio not reachable or returning unexpected status"
    warn "(WebUI auth flow needs this; check it's still up)"
fi

# ── Summary ────────────────────────────────────────────────────────────
echo
if [[ $ERRORS -eq 0 ]]; then
    cat <<EOF
════════════════════════════════════════════════════════════════════
  ✓ All checks passed — chat.neowow.studio looks healthy
════════════════════════════════════════════════════════════════════

  Manual end-to-end test:
    1. Open https://$DOMAIN in a fresh browser
    2. Should redirect to app.neowow.studio for Neodomain login
    3. After login, should land back on https://$DOMAIN/ authenticated

  If step 2 or 3 fails, look at:
    sudo journalctl -u hermes-webui -f  # WebUI side
    sudo journalctl -u caddy -f         # TLS / proxy side
    Browser DevTools → Network          # Cookie + redirect chain
EOF
    exit 0
else
    cat >&2 <<EOF
════════════════════════════════════════════════════════════════════
  ✗ $ERRORS check(s) failed
════════════════════════════════════════════════════════════════════

  Diagnostic commands:
    sudo systemctl status hermes-webui caddy
    sudo journalctl -u hermes-webui --no-pager -n 100
    sudo journalctl -u caddy --no-pager -n 50
EOF
    exit 1
fi
