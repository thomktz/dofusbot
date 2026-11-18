import AppKit
import Foundation
import NetworkExtension
import SystemExtensions

/// Minimal host for the system extension.  It does not activate on launch:
/// activation is an explicit call from a future dashboard bridge/UI after the
/// Python listener is ready on the same loopback port.
@main
final class DofusAgentHost: NSObject, NSApplicationDelegate, OSSystemExtensionRequestDelegate {
    static let extensionIdentifier = "com.thomaskientz.dofus-agent.transparent-proxy"
    static let bridgePort: UInt16 = 39155

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSLog("Dofus Agent transparent-proxy host is ready; no extension activated automatically.")
    }

    func activateTransparentProxy() {
        let request = OSSystemExtensionRequest.activationRequest(
            forExtensionWithIdentifier: Self.extensionIdentifier,
            queue: .main
        )
        request.delegate = self
        OSSystemExtensionManager.shared.submitRequest(request)
    }

    func configureTransparentProxy(completion: @escaping (Error?) -> Void) {
        NETransparentProxyManager.loadAllFromPreferences { managers, error in
            if let error { completion(error); return }
            let manager = managers?.first ?? NETransparentProxyManager()
            let proto = NETunnelProviderProtocol()
            proto.providerBundleIdentifier = Self.extensionIdentifier
            proto.providerConfiguration = ["bridgePort": NSNumber(value: Self.bridgePort)]
            manager.protocolConfiguration = proto
            manager.localizedDescription = "Dofus Agent local game proxy"
            manager.isEnabled = true
            manager.saveToPreferences(completionHandler: completion)
        }
    }

    func request(_ request: OSSystemExtensionRequest,
                 didFinishWithResult result: OSSystemExtensionRequest.Result) {
        NSLog("System extension activation result: \(result.rawValue)")
    }

    func request(_ request: OSSystemExtensionRequest,
                 didFailWithError error: Error) {
        NSLog("System extension activation failed: \(error.localizedDescription)")
    }

    func requestNeedsUserApproval(_ request: OSSystemExtensionRequest) {
        NSLog("Approve Dofus Agent Transparent Proxy in System Settings to continue.")
    }

    func request(_ request: OSSystemExtensionRequest,
                 actionForReplacingExtension existing: OSSystemExtensionProperties,
                 withExtension ext: OSSystemExtensionProperties) -> OSSystemExtensionRequest.ReplacementAction {
        .replace
    }
}
