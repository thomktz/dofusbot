# Managed game transport

The dashboard action API is protocol-first. It creates typed `GameIntent`
objects and dispatches them only to an attached local proxy session:

```text
webapp / LLM -> ManagedTransport -> AstraCodec -> local MITM proxy -> Dofus server
```

`ManagedTransport` has no coordinate fallback. A request when no proxy session
is attached fails safely. `AstraCodec` directly loads AstraBot's generated
protobuf descriptors and mapping JSON without importing its Windows-only
runtime. Set `ASTRABOT_HOME` to the cloned source directory; it defaults to
`/private/tmp/AstraBot` for development.

Supported typed requests now have encoders for NPC dialogue, NPC actions, map
movement, interactives, fight casts, and turn finish.

Launching Dofus from the dashboard uses the same two-stage route as AstraBot:

1. A short Frida hook redirects only the initial Dofus port-5555 connection to
   `ManagedConnectionProxy` on localhost.
2. That transparent login bridge changes `SelectServerResponse` to an
   ephemeral local game bridge, which forwards the selected shard connection
   and attaches `ManagedTransport`.

The game bridge observes the actual client request UID before it injects a
request, so dashboard requests share the server's expected UID sequence.
`SOCKET · ready` in the dashboard is the only state in which protocol actions
may be sent. Existing manually launched clients are observable but cannot be
controlled through this path; launch them through the dashboard instead.
