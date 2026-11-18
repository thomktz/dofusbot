// AstraBot ships this hook for Windows (ws2_32!connect).  macOS exposes the
// same BSD socket ABI through libsystem_kernel.dylib.  Redirect only the
// fixed Dofus connection-server port; shard endpoints are rewritten by the
// local connection proxy after login.
const TARGET_PORT = 5555;

recv(function (message) {
    const proxyPort = message.port;
    // Keep the lookup shape identical to AstraBot's Windows hook. Frida's
    // static Module helpers changed across releases; module instances expose
    // getExportByName consistently.
    const connectPtr = Process.getModuleByName("libsystem_kernel.dylib").getExportByName("connect");
    Interceptor.attach(connectPtr, {
        onEnter(args) {
            try {
                const sockaddr = args[1];
                const family = sockaddr.readU16();
                if (family !== 2 && family !== 30) return; // AF_INET / AF_INET6 on Darwin
                const port = (sockaddr.add(2).readU8() << 8) | sockaddr.add(3).readU8();
                if (port !== TARGET_PORT) return;
                sockaddr.add(2).writeU8((proxyPort >> 8) & 0xff);
                sockaddr.add(3).writeU8(proxyPort & 0xff);
                if (family === 2) {
                    sockaddr.add(4).writeByteArray([127, 0, 0, 1]);
                } else {
                    // sockaddr_in6 address starts at byte 8; use ::ffff:127.0.0.1
                    sockaddr.add(8).writeByteArray([0,0,0,0, 0,0,0,0, 0,0,0xff,0xff, 127,0,0,1]);
                }
            } catch (error) {
                send({type: "redirect-error", message: String(error)});
            }
        }
    });
});
