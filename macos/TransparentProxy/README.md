# Native macOS transparent proxy

This is the supported macOS replacement for the Frida launch hook.  The
extension receives only Dofus's TCP/5555 login connection, forwards it to the
existing loopback `ManagedConnectionProxy`, and returns all other traffic to
macOS untouched.  It never injects code into Dofus and needs neither Screen
Recording nor disabled SIP.

## What is already wired

The Python backend now supports this launch mode.  When the extension is
active, set:

```sh
export DOFUS_TRANSPARENT_PROXY_PORT=39155
./run.sh
```

It binds the existing bridge to `127.0.0.1:39155` and launches Dofus normally,
without importing or invoking Frida.  Do not set that variable until the
extension has been built and approved; without the extension Dofus would
connect to its normal login server instead of the local bridge.

## One-time signing and approval

The complete Xcode project is included as
`DofusAgent.xcodeproj`.  It builds on an Apple-silicon Mac with Xcode 15.4;
the only remaining prerequisite is an Apple signing team, because macOS will
not activate an unsigned Network Extension.

1. Open `DofusAgent.xcodeproj` in Xcode.
2. In **Xcode → Settings → Accounts**, add the Apple ID that owns the signing
   team, if it is not already listed.
3. Select the **DofusAgent** target → **Signing & Capabilities** and choose
   that team.  Do the same for **DofusTransparentProxy**.
4. Build once.  If Xcode says the Network Extension capability is unavailable,
   the team needs an Apple Developer membership/capability grant; that is an
   Apple signing restriction, not a Dofus or Frida restriction.
5. When the host asks, approve the named **Dofus Agent Transparent Proxy**
   system extension in System Settings.  That is the only OS-wide approval.

The host and extension must be signed by the same team.  The bundled project
embeds the extension in `Contents/Library/SystemExtensions`, the macOS system
extension location.

The entitlement used is Apple's `app-proxy-provider-systemextension`; it is
for a transparent TCP/UDP proxy.  It is not a kernel extension and it does not
disable SIP.  Keep the host's Team ID and the extension's Team ID identical.

## Scope and safety

The provider deliberately handles only TCP destination port `5555`, which is
the Dofus login port our launcher supplies.  It does not claim port 443,
arbitrary browsers, or other game traffic.  If a future Dofus version changes
that behaviour, the extension should be updated deliberately rather than
broadened silently.
