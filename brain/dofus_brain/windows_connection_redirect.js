// Redirect only Dofus's fixed login connection (TCP/5555) to the local
// ManagedConnectionProxy.  This is the Windows equivalent of the Darwin
// hook and is intentionally limited to the one known login port.
const TARGET_PORT = 5555;

recv(function (message) {
    const proxyPort = message.port;
    const connectPtr = Process.getModuleByName("ws2_32.dll").getExportByName("connect");
    Interceptor.attach(connectPtr, {
        onEnter(args) {
            try {
                const sockaddr = args[1];
                const family = sockaddr.readU16();
                if (family !== 2 && family !== 23) return; // AF_INET / AF_INET6 on Windows
                const port = (sockaddr.add(2).readU8() << 8) | sockaddr.add(3).readU8();
                if (port !== TARGET_PORT) return;
                sockaddr.add(2).writeU8((proxyPort >> 8) & 0xff);
                sockaddr.add(3).writeU8(proxyPort & 0xff);
                if (family === 2) {
                    sockaddr.add(4).writeByteArray([127, 0, 0, 1]);
                } else {
                    // sockaddr_in6 address starts at byte 8; use ::ffff:127.0.0.1.
                    sockaddr.add(8).writeByteArray([0, 0, 0, 0, 0, 0, 0, 0,
                                                      0, 0, 0xff, 0xff, 127, 0, 0, 1]);
                }
            } catch (error) {
                send({type: "redirect-error", message: String(error)});
            }
        }
    });
});
