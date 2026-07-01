# ─────────────────────────────────────────────────────────────────────────
# sign.ps1 — Authenticode-sign a Windows file IF signing is configured,
# otherwise a clean no-op (unsigned builds keep working until a cert is set up).
#
# Universal signer: jsign (https://ebourg.github.io/jsign/) — ONE tool, many
# backends, so switching CA/service later only means changing GitHub Secrets,
# never this script or the workflow. Pick a backend via WINDOWS_SIGN_STORETYPE:
#
#   SSL.com eSigner      → ESIGNER
#   DigiCert KeyLocker   → DIGICERTONE
#   Azure Trusted Signing→ TRUSTEDSIGNING
#   Azure Key Vault      → AZUREKEYVAULT
#   Certum / USB / HSM   → PKCS11
#   plain .pfx (legacy)  → PKCS12
#
# GitHub Secrets (ALL empty by default → signing is skipped):
#   WINDOWS_SIGN_STORETYPE    jsign --storetype (the switch above). Empty = skip.
#   WINDOWS_SIGN_KEYSTORE     jsign --keystore  (vault / endpoint / token cfg / .pfx path)
#   WINDOWS_SIGN_STOREPASS    jsign --storepass (service credentials / token PIN)
#   WINDOWS_SIGN_ALIAS        jsign --alias     (cert / key name in the store)
#   WINDOWS_SIGN_KEYPASS      jsign --keypass   (optional)
#   WINDOWS_SIGN_CERTFILE_B64 base64 of the cert-chain file (optional; some
#                             backends need --certfile)
#   WINDOWS_SIGN_TSA          RFC-3161 timestamp URL (optional; defaults below)
#
# Exact secret meaning is backend-specific — map your chosen service's
# credentials onto these when you set the secrets; the jsign docs list each
# backend's keystore/storepass semantics.
# ─────────────────────────────────────────────────────────────────────────

param([Parameter(Mandatory = $true)][string]$Target)
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($env:WINDOWS_SIGN_STORETYPE)) {
  Write-Host "info: Windows signing not configured (WINDOWS_SIGN_STORETYPE empty) - shipping UNSIGNED. Skipping '$Target'."
  exit 0
}
if (-not (Test-Path $Target)) { Write-Error "sign target not found: $Target"; exit 1 }

if (-not (Get-Command jsign -ErrorAction SilentlyContinue)) {
  Write-Host "Installing jsign ..."
  choco install jsign -y --no-progress | Out-Null
}

$tsa = if ([string]::IsNullOrWhiteSpace($env:WINDOWS_SIGN_TSA)) { "http://timestamp.sectigo.com" } else { $env:WINDOWS_SIGN_TSA }

$jsignArgs = @(
  "--storetype", $env:WINDOWS_SIGN_STORETYPE,
  "--tsaurl",    $tsa,
  "--tsmode",    "RFC3161",
  "--name",      "Neowow Studio",
  "--url",       "https://neowow.studio"
)
if ($env:WINDOWS_SIGN_KEYSTORE)  { $jsignArgs += @("--keystore",  $env:WINDOWS_SIGN_KEYSTORE) }
if ($env:WINDOWS_SIGN_STOREPASS) { $jsignArgs += @("--storepass", $env:WINDOWS_SIGN_STOREPASS) }
if ($env:WINDOWS_SIGN_ALIAS)     { $jsignArgs += @("--alias",     $env:WINDOWS_SIGN_ALIAS) }
if ($env:WINDOWS_SIGN_KEYPASS)   { $jsignArgs += @("--keypass",   $env:WINDOWS_SIGN_KEYPASS) }
if ($env:WINDOWS_SIGN_CERTFILE_B64) {
  $certPath = Join-Path $env:RUNNER_TEMP "signcert.pem"
  [IO.File]::WriteAllBytes($certPath, [Convert]::FromBase64String($env:WINDOWS_SIGN_CERTFILE_B64))
  $jsignArgs += @("--certfile", $certPath)
}
$jsignArgs += $Target

Write-Host "Signing '$Target' via jsign (storetype=$($env:WINDOWS_SIGN_STORETYPE)) ..."
& jsign @jsignArgs
if ($LASTEXITCODE -ne 0) { Write-Error "jsign failed (exit $LASTEXITCODE)"; exit 1 }

$sig = Get-AuthenticodeSignature $Target
Write-Host "-> signature status: $($sig.Status)  subject: $($sig.SignerCertificate.Subject)"
if ($sig.Status -eq "NotSigned") { Write-Error "'$Target' is NOT signed after jsign"; exit 1 }
Write-Host "OK signed: $Target"
