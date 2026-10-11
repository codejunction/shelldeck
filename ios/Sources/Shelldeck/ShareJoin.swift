import CryptoKit
import SwiftUI
import VisionKit

/// `sd share`'s QR code: https://<tunnel>/share/<token>#k=<the PC's end-to-end key>.
struct ShareLink {
    let url: URL
    let key: Curve25519.KeyAgreement.PublicKey
}

/// A share link with its key, else nil (a link without one comes from an older shelldeck: update it).
func shareLink(_ text: String) -> ShareLink? {
    guard let url = URL(string: text.trimmingCharacters(in: .whitespaces)), url.scheme == "https", url.host() != nil,
          url.path().hasPrefix("/share/"), url.pathComponents.count == 3,
          let k = url.fragment()?.split(separator: "&").first(where: { $0.hasPrefix("k=") }),
          let key = e2eKey(String(k.dropFirst(2))) else { return nil }
    return ShareLink(url: url, key: key)
}

/// Camera QR scanner; hands back the first share link it sees.
struct QRScanner: UIViewControllerRepresentable {
    let found: (ShareLink) -> Void

    static var available: Bool { DataScannerViewController.isSupported && DataScannerViewController.isAvailable }

    func makeCoordinator() -> Coordinator { Coordinator(found: found) }

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let scanner = DataScannerViewController(recognizedDataTypes: [.barcode(symbologies: [.qr])], isHighlightingEnabled: true)
        scanner.delegate = context.coordinator
        try? scanner.startScanning()
        return scanner
    }

    func updateUIViewController(_: DataScannerViewController, context _: Context) {}

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        let found: (ShareLink) -> Void
        private var done = false
        init(found: @escaping (ShareLink) -> Void) { self.found = found }

        func dataScanner(_: DataScannerViewController, didAdd items: [RecognizedItem], allItems _: [RecognizedItem]) {
            for case .barcode(let code) in items {
                if !done, let link = code.payloadStringValue.flatMap(shareLink) {
                    done = true
                    found(link)
                }
            }
        }
    }
}

/// Opens the share link (the PC shows an Allow/Deny), waits for the answer, then hands over the server.
struct ShareJoinView: View {
    let link: ShareLink
    let joined: (Server) -> Void
    let cancel: () -> Void
    @State private var state = "Opening the link…"
    @State private var failed = false
    @State private var code: String?

    var body: some View {
        VStack(spacing: 16) {
            if failed {
                Image(systemName: "xmark.circle").font(.system(size: 40)).foregroundStyle(.red)
            } else {
                ProgressView()
            }
            Text(state).multilineTextAlignment(.center)
            if let code, !failed {
                Text(code).font(.system(size: 44, weight: .semibold, design: .monospaced)).kerning(10)
                Text("Allow it on the PC only if it shows this same code.")
                    .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            } else {
                Text("Allow it in shelldeck on the PC, or answer y in the `sd share` window.")
                    .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
            Button(failed ? "Back" : "Cancel", action: cancel)
        }
        .padding(24)
        .frame(maxWidth: 460)  // a form-sized column on iPad
        .task { await join() }
    }

    private func join() async {
        let server = Server(base: URL(string: "https://\(link.url.host()!)")!, key: link.key)
        do {
            _ = try await server.request(link.url.path())  // sets the sd_share cookie; the server makes a pending grant
        } catch {
            return fail("This link has expired or was already used. Make a new one with `sd share --new-link`.")
        }
        state = "Waiting for the PC to allow this iPhone…"
        while !Task.isCancelled {
            let status = try? await server.get("/share/status", as: [String: String].self)
            if let c = status?["code"] { code = c }
            switch status?["state"] {
            case "ok": return joined(server)  // login then makes this iPhone a trusted device of that PC
            case "denied": return fail("The PC denied this iPhone.")
            case "pending": try? await Task.sleep(for: .seconds(2))
            default: return fail("The request expired. Scan a new code.")
            }
        }
    }

    private func fail(_ text: String) {
        state = text
        failed = true
    }
}
