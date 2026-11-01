# HTTPS on the LAN

Caddy issues the certificate itself (`tls internal`) from a local certificate authority it
creates on first use. The server side needs no key handling; each device needs to trust that
authority once. (Oct 6, 2026)

## Server, once

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

Then set `COOKIE_SECURE=true` in `.env` and restart the page so the session
cookie is marked Secure:

```bash
sudo systemctl restart jobagent-ui.service
```

The page is then at `https://<server address>/` (or `https://<hostname>.local/` where
mDNS resolves it). Plain `http://` redirects there.

## Each device, once: trust the local authority

Export the root certificate from the server:

```bash
sudo cp /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt <install dir>/data/caddy-root.crt && sudo chmod 644 <install dir>/data/caddy-root.crt
```

Once exported, every device can download it from the page itself at
`https://<server address>/caddy-root.crt` (click through the browser's warning once; the file is the
public root, nothing secret). Or copy it with scp / AirDrop / a USB stick. Then install it:

- **macOS:** double-click the file, add it to the *System* keychain, open it in Keychain
  Access and set "When using this certificate" to *Always Trust*.
- **Windows:** double-click the file → Install Certificate… → *Local Machine* → "Place all
  certificates in the following store" → Browse → *Trusted Root Certification Authorities* →
  Finish. Or in PowerShell as administrator:
  `Import-Certificate -FilePath .\caddy-root.crt -CertStoreLocation Cert:\LocalMachine\Root`.
  Restart the browser afterwards.
- **iPhone / iPad:** open the file, install the profile under Settings → General → VPN &
  Device Management, then enable full trust under Settings → General → About → Certificate
  Trust Settings.
- **Android:** Settings → Security → Encryption & credentials → Install a certificate → CA
  certificate.
- **Linux:** `sudo cp caddy-root.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates`
  (Firefox has its own store: Settings → Privacy & Security → Certificates → Import).

Chrome and Safari use the system store; Firefox needs the import above.

## When a public domain exists later

Replace the LAN block in the Caddyfile with the commented domain block (Caddy then fetches a
public certificate automatically), keep `COOKIE_SECURE=true`, and the device step is no longer
needed.
