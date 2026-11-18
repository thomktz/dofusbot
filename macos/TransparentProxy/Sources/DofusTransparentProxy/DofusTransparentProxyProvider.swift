import Foundation
import Network
import NetworkExtension

/// macOS-owned alternative to injecting Frida into the Dofus process.
///
/// The provider claims TCP flows, immediately returns unrecognised traffic to
/// macOS, and copies only the fixed Dofus login-server port to the local
/// `ManagedConnectionProxy`.  The Python proxy then forwards the connection
/// upstream and rewrites the selected game-server endpoint exactly as before.
/// No Dofus memory is read or modified.
final class DofusTransparentProxyProvider: NETransparentProxyProvider {
    private let queue = DispatchQueue(label: "com.dofusagent.transparent-proxy")
    private var bridgePort: UInt16 = 39155
    private var sessions: [ObjectIdentifier: FlowSession] = [:]

    override func startProxy(options: [String: Any]? = nil,
                             completionHandler: @escaping (Error?) -> Void) {
        if let configured = (protocolConfiguration as? NETunnelProviderProtocol)?
            .providerConfiguration?["bridgePort"] as? NSNumber,
           configured.uint16Value > 0 {
            bridgePort = configured.uint16Value
        }

        // We need to inspect the destination port before deciding whether to
        // claim a flow.  Returning false in handleNewFlow lets non-Dofus TCP
        // traffic continue normally, which is a feature of the transparent
        // provider API (unlike older app-proxy providers).
        let settings = NETransparentProxyNetworkSettings(tunnelRemoteAddress: "127.0.0.1")
        settings.includedNetworkRules = [
            NENetworkRule(remoteNetwork: nil, remotePrefix: 0,
                          localNetwork: nil, localPrefix: 0, protocol: .TCP,
                          direction: .outbound),
        ]
        setTunnelNetworkSettings(settings, completionHandler: completionHandler)
    }

    override func stopProxy(with reason: NEProviderStopReason,
                            completionHandler: @escaping () -> Void) {
        queue.async {
            self.sessions.values.forEach { $0.close() }
            self.sessions.removeAll()
            completionHandler()
        }
    }

    override func handleNewFlow(_ flow: NEAppProxyFlow) -> Bool {
        guard let tcpFlow = flow as? NEAppProxyTCPFlow,
              let endpoint = tcpFlow.remoteEndpoint as? NWHostEndpoint,
              endpoint.port == "5555" else {
            // Do not intercept 443 or arbitrary traffic.  Dofus is launched
            // with --connectionPort 5555 by our host, so this is deliberately
            // narrow and fails closed for unrelated apps.
            return false
        }

        let id = ObjectIdentifier(tcpFlow)
        let session = FlowSession(flow: tcpFlow, bridgePort: bridgePort, queue: queue) { [weak self] in
            self?.queue.async { self?.sessions.removeValue(forKey: id) }
        }
        queue.async {
            self.sessions[id] = session
            session.start()
        }
        return true
    }
}

private final class FlowSession {
    private let flow: NEAppProxyTCPFlow
    private let connection: NWConnection
    private let queue: DispatchQueue
    private let didClose: () -> Void
    private var closed = false

    init(flow: NEAppProxyTCPFlow, bridgePort: UInt16, queue: DispatchQueue,
         didClose: @escaping () -> Void) {
        self.flow = flow
        self.connection = NWConnection(
            host: NWEndpoint.Host("127.0.0.1"),
            port: NWEndpoint.Port(rawValue: bridgePort)!,
            using: .tcp
        )
        self.queue = queue
        self.didClose = didClose
    }

    func start() {
        connection.stateUpdateHandler = { [weak self] state in
            if case .failed = state { self?.close() }
        }
        connection.start(queue: queue)
        flow.open(withLocalEndpoint: nil) { [weak self] error in
            guard let self, error == nil else { self?.close(); return }
            self.copyClientToBridge()
            self.copyBridgeToClient()
        }
    }

    private func copyClientToBridge() {
        guard !closed else { return }
        flow.readData { [weak self] data, error in
            guard let self, error == nil, let data, !data.isEmpty else { self?.close(); return }
            self.connection.send(content: data, completion: .contentProcessed { [weak self] error in
                guard let self, error == nil else { self?.close(); return }
                self.copyClientToBridge()
            })
        }
    }

    private func copyBridgeToClient() {
        guard !closed else { return }
        connection.receive(minimumIncompleteLength: 1, maximumLength: 64 * 1024) { [weak self] data, _, complete, error in
            guard let self, error == nil, let data, !data.isEmpty else { self?.close(); return }
            self.flow.write(data) { [weak self] error in
                guard let self, error == nil, !complete else { self?.close(); return }
                self.copyBridgeToClient()
            }
        }
    }

    func close() {
        guard !closed else { return }
        closed = true
        connection.cancel()
        flow.closeReadWithError(nil)
        flow.closeWriteWithError(nil)
        didClose()
    }
}
