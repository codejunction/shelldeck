import CryptoKit
import Foundation
import Security

/// The end-to-end encrypted channel to one PC (server side: shelldeck/e2e.py and /e2e in server.py).
/// Everything — API calls, terminal and alarm sockets, the password — goes through it, so the share's tunnel
/// (Cloudflare decrypts TLS at its edge) and the home Wi-Fi only carry ciphertext.
///
/// Handshake: send an ephemeral X25519 key, get the server's ephemeral key back; keys are
/// HKDF-SHA256(eph x pinned static || eph x server eph, salt: both ephemerals, info: "shelldeck e2e v1"),
/// 32 bytes per direction. Only the PC holding the pinned key's private half can derive them. Frames are
/// AES-256-GCM with a per-direction counter nonce, so nothing can be replayed, reordered or forged.
@MainActor
final class Channel {
    let base: URL
    let serverKey: Curve25519.KeyAgreement.PublicKey
    var jar: [String: String] { didSet { saveJar() } }  // cookies, carried inside the channel

    private var task: URLSessionWebSocketTask?
    private var opening: Task<Void, Error>?
    private var sendKey: SymmetricKey?
    private var recvKey: SymmetricKey?
    private var nOut: UInt64 = 0
    private var nIn: UInt64 = 0
    private var nextId = 0
    private var calls: [Int: CheckedContinuation<Reply, Error>] = [:]
    private var sockets: [Int: ChannelSocket] = [:]
    private var ping: Timer?

    struct Reply {
        let status: Int
        let headers: [[String]]
        let body: Data
    }

    init(base: URL, key: Curve25519.KeyAgreement.PublicKey) {
        self.base = base
        serverKey = key
        jar = Channel.loadJar(key)
    }

    // MARK: requests

    func http(_ method: String, _ path: String, headers: [String: String], body: Data?) async throws -> Reply {
        try await open()
        let id = take()
        var msg: [String: Any] = ["id": id, "t": "http", "method": method, "path": path, "headers": withCookies(headers)]
        if let body { msg["body"] = body.base64EncodedString() }
        let reply = try await withCheckedThrowingContinuation { (c: CheckedContinuation<Reply, Error>) in
            calls[id] = c
            send(msg)
        }
        for pair in reply.headers where pair.count == 2 && pair[0].lowercased() == "set-cookie" {
            let first = pair[1].split(separator: ";").first.map(String.init) ?? ""
            let parts = first.split(separator: "=", maxSplits: 1).map(String.init)
            guard parts.count == 2 else { continue }
            let expired = pair[1].lowercased().contains("max-age=0") || parts[1].isEmpty || parts[1] == "\"\""
            jar[parts[0]] = expired ? nil : parts[1]
        }
        return reply
    }

    /// A WebSocket inside the channel; it reports close code 1006 if the channel itself drops.
    func socket(_ path: String) -> ChannelSocket {
        let s = ChannelSocket(channel: self, id: take())
        sockets[s.id] = s
        Task {
            do {
                try await open()
                send(["id": s.id, "t": "ws", "path": path, "headers": withCookies(["user-agent": Server.agent])])
            } catch {
                finish(s.id, code: 1006)
            }
        }
        return s
    }

    func sendSocket(_ id: Int, _ text: String) { if sockets[id] != nil { send(["id": id, "t": "msg", "data": text]) } }

    func closeSocket(_ id: Int) {
        guard sockets.removeValue(forKey: id) != nil else { return }
        send(["id": id, "t": "close", "code": 1000])
    }

    func shutdown() {
        fail(URLError(.cancelled))
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
    }

    // MARK: channel

    private func open() async throws {
        if sendKey != nil, task != nil { return }
        if let opening { return try await opening.value }
        let t = Task { try await handshake() }
        opening = t
        defer { opening = nil }
        try await t.value
    }

    private func handshake() async throws {
        var parts = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        parts.scheme = base.scheme == "https" ? "wss" : "ws"
        parts.path = "/e2e"
        var req = URLRequest(url: parts.url!)
        req.timeoutInterval = 10
        req.setValue(Server.agent, forHTTPHeaderField: "User-Agent")
        let task = URLSession.shared.webSocketTask(with: req)
        task.maximumMessageSize = 64 << 20
        task.resume()
        let eph = Curve25519.KeyAgreement.PrivateKey()
        let hello = eph.publicKey.rawRepresentation
        try await task.send(.data(hello))
        guard case .data(let reply) = try await task.receive(), reply.count == 32 else { throw URLError(.badServerResponse) }
        let peer = try Curve25519.KeyAgreement.PublicKey(rawRepresentation: reply)
        let ikm = try raw(eph.sharedSecretFromKeyAgreement(with: serverKey)) + raw(eph.sharedSecretFromKeyAgreement(with: peer))
        let okm = HKDF<SHA256>.deriveKey(inputKeyMaterial: SymmetricKey(data: ikm), salt: hello + reply,
                                         info: Data("shelldeck e2e v1".utf8), outputByteCount: 64)
        let bytes = okm.withUnsafeBytes { Data($0) }
        sendKey = SymmetricKey(data: bytes.prefix(32))
        recvKey = SymmetricKey(data: bytes.suffix(32))
        nOut = 0
        nIn = 0
        self.task = task
        receive(task)
        ping?.invalidate()
        ping = Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { [weak task] _ in task?.sendPing { _ in } }
    }

    private func receive(_ task: URLSessionWebSocketTask) {
        task.receive { [weak self] result in
            Task { @MainActor in
                guard let self, task === self.task else { return }
                switch result {
                case .success(.data(let frame)):
                    guard let plain = self.unseal(frame),
                          let msg = try? JSONSerialization.jsonObject(with: plain) as? [String: Any] else {
                        return self.fail(URLError(.secureConnectionFailed))  // tampered or out of order
                    }
                    self.dispatch(msg)
                    self.receive(task)
                case .success:
                    self.receive(task)
                case .failure(let error):
                    self.fail(error)
                }
            }
        }
    }

    private func dispatch(_ msg: [String: Any]) {
        guard let id = msg["id"] as? Int else { return }
        switch msg["t"] as? String {
        case "http":
            let reply = Reply(status: msg["status"] as? Int ?? 500, headers: msg["headers"] as? [[String]] ?? [],
                              body: Data(base64Encoded: msg["body"] as? String ?? "") ?? Data())
            calls.removeValue(forKey: id)?.resume(returning: reply)
        case "open":
            sockets[id]?.onOpen?()
        case "msg":
            sockets[id]?.onMessage?(msg["data"] as? String ?? "")
        case "close":
            finish(id, code: msg["code"] as? Int ?? 1000)
        default:
            break
        }
    }

    private func finish(_ id: Int, code: Int) {
        sockets.removeValue(forKey: id)?.onClose?(code)
    }

    private func fail(_ error: Error) {
        task?.cancel(with: .abnormalClosure, reason: nil)
        task = nil
        sendKey = nil
        ping?.invalidate()
        for c in calls.values { c.resume(throwing: error) }
        calls = [:]
        for id in Array(sockets.keys) { finish(id, code: 1006) }
    }

    private func send(_ msg: [String: Any]) {
        guard let task, let key = sendKey, let plain = try? JSONSerialization.data(withJSONObject: msg),
              let sealed = try? AES.GCM.seal(plain, using: key, nonce: nonce(nOut)) else { return }
        nOut += 1
        task.send(.data(sealed.ciphertext + sealed.tag)) { _ in }
    }

    private func unseal(_ frame: Data) -> Data? {
        guard let key = recvKey, frame.count >= 16,
              let box = try? AES.GCM.SealedBox(nonce: nonce(nIn), ciphertext: frame.dropLast(16), tag: frame.suffix(16)),
              let plain = try? AES.GCM.open(box, using: key) else { return nil }
        nIn += 1
        return plain
    }

    private func nonce(_ n: UInt64) -> AES.GCM.Nonce {
        try! AES.GCM.Nonce(data: Data(count: 4) + withUnsafeBytes(of: n.bigEndian) { Data($0) })
    }

    private func raw(_ s: SharedSecret) -> Data { s.withUnsafeBytes { Data($0) } }

    private func take() -> Int {
        nextId += 1
        return nextId
    }

    private func withCookies(_ headers: [String: String]) -> [String: String] {
        var h = headers
        if !jar.isEmpty { h["cookie"] = jar.map { "\($0.key)=\($0.value)" }.joined(separator: "; ") }
        return h
    }

    // MARK: cookie jar, per PC (keyed by its public key), in the Keychain

    private static func account(_ key: Curve25519.KeyAgreement.PublicKey) -> String {
        "jar-" + key.rawRepresentation.prefix(12).base64EncodedString()
    }

    private static func loadJar(_ key: Curve25519.KeyAgreement.PublicKey) -> [String: String] {
        var out: CFTypeRef?
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrAccount as String: account(key),
                                kSecReturnData as String: true]
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return [:] }
        return (try? JSONDecoder().decode([String: String].self, from: data)) ?? [:]
    }

    private func saveJar() {
        let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrAccount as String: Channel.account(serverKey)]
        SecItemDelete(base as CFDictionary)
        SecItemAdd(base.merging([kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
                                 kSecValueData as String: try! JSONEncoder().encode(jar)]) { $1 } as CFDictionary, nil)
    }
}

/// One WebSocket inside a channel.
@MainActor
final class ChannelSocket {
    let id: Int
    private weak var channel: Channel?
    var onOpen: (() -> Void)?
    var onMessage: ((String) -> Void)?
    var onClose: ((Int) -> Void)?

    init(channel: Channel, id: Int) {
        self.channel = channel
        self.id = id
    }

    func send(_ text: String) { channel?.sendSocket(id, text) }
    func close() { channel?.closeSocket(id) }
}

/// base64url (no padding) -> X25519 public key, as in the QR fragment (#k=...) and /e2e/key.
func e2eKey(_ text: String) -> Curve25519.KeyAgreement.PublicKey? {
    var s = text.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
    s += String(repeating: "=", count: (4 - s.count % 4) % 4)
    return Data(base64Encoded: s).flatMap { try? Curve25519.KeyAgreement.PublicKey(rawRepresentation: $0) }
}
