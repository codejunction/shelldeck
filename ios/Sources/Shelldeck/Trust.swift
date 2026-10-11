import CryptoKit
import Foundation
import Security

/// A PC this iPhone is a trusted device of: no share link or host approval, ever (the password still is).
/// The PC posts each new share URL to `topic` on ntfy.sh, sealed with `key` (AES-256-GCM). All PCs live in
/// one Keychain item, never synced or backed up.
struct PC: Codable, Identifiable, Equatable {
    let id: String  // the PC's trusted-device id for this iPhone
    let token: String
    let topic: String
    let key: String
    let relay: String
    let cookie: String
    var pc: String?  // the PC's hostname
    var lan: String?  // its address on home Wi-Fi, when it was added there
    var e2e: String?  // its end-to-end public key (base64), pinned from the QR code or home Wi-Fi
    var name: String { pc ?? "PC" }
    var e2eKey: Curve25519.KeyAgreement.PublicKey? {
        e2e.flatMap { Data(base64Encoded: $0) }.flatMap { try? Curve25519.KeyAgreement.PublicKey(rawRepresentation: $0) }
    }

    // MARK: store

    private static let account = "trusted-pcs"
    // iCloud Keychain: your iPhone and iPad (same Apple ID, same app) share the trusted PCs, so a PC set up on
    // one is there on the other with no QR code. Apple encrypts it end to end. Logins (the cookie jar) stay
    // per device. ponytail: both devices are one trusted device to the PC, so revoking it revokes both.
    private static let item: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrAccount as String: account,
                                              kSecAttrSynchronizable as String: kSecAttrSynchronizableAny]

    static func all() -> [PC] {
        var out: CFTypeRef?
        guard SecItemCopyMatching(item.merging([kSecReturnData as String: true]) { $1 } as CFDictionary, &out) == errSecSuccess,
              let data = out as? Data else { return [] }
        return (try? JSONDecoder().decode([PC].self, from: data)) ?? []
    }

    static func save(_ pcs: [PC]) {
        SecItemDelete(item as CFDictionary)  // the synced copy and any older device-only one
        SecItemAdd([kSecClass as String: kSecClassGenericPassword, kSecAttrAccount as String: account,
                    kSecAttrSynchronizable as String: true,
                    kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
                    kSecValueData as String: try! JSONEncoder().encode(pcs)] as CFDictionary, nil)
    }

    /// Adds or replaces (same end-to-end key: the same shelldeck, added again) and returns the PC. The key, not
    /// the hostname: two shelldecks on one machine (separate data folders) are different PCs.
    @discardableResult
    static func add(_ pc: PC) -> PC {
        save(all().filter { $0.id != pc.id && ($0.e2e == nil || $0.e2e != pc.e2e) } + [pc])
        return pc
    }

    static func remove(_ id: String) { save(all().filter { $0.id != id }) }

    /// The first build kept one PC under "trusted-device", and later ones kept the list on this device only:
    /// move both into the synced list.
    static func migrate() {
        let local: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrAccount as String: account,
                                    kSecAttrSynchronizable as String: false, kSecReturnData as String: true]
        var found: CFTypeRef?
        if SecItemCopyMatching(local as CFDictionary, &found) == errSecSuccess, let data = found as? Data,
           let pcs = try? JSONDecoder().decode([PC].self, from: data) {
            save(pcs)
        }
        let old: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrAccount as String: "trusted-device"]
        var out: CFTypeRef?
        guard SecItemCopyMatching(old.merging([kSecReturnData as String: true]) { $1 } as CFDictionary, &out) == errSecSuccess,
              let data = out as? Data, let pc = try? JSONDecoder().decode(PC.self, from: data) else { return }
        add(pc)
        SecItemDelete(old as CFDictionary)
    }

    static var current: PC? {
        let all = all()
        return all.first { $0.id == UserDefaults.standard.string(forKey: "pc") } ?? all.first
    }

    static func select(_ id: String) { UserDefaults.standard.set(id, forKey: "pc") }

    // MARK: finding it

    /// The newest share URL this PC announced, or nil. Messages that don't open with our key are ignored.
    func latestShare() async -> URL? {
        guard let url = URL(string: "\(relay)/\(topic)/json?poll=1&since=all"),
              let (data, _) = try? await URLSession.shared.data(from: url),
              let raw = Data(base64Encoded: key) else { return nil }
        let key = SymmetricKey(data: raw)
        var best: (at: Double, url: URL)?
        for line in data.split(separator: UInt8(ascii: "\n")) {
            guard let msg = try? JSONDecoder().decode(Message.self, from: Data(line)), msg.event == "message",
                  let sealed = msg.message.flatMap({ Data(base64Encoded: $0) }),
                  let box = try? AES.GCM.SealedBox(combined: sealed),
                  let plain = try? AES.GCM.open(box, using: key),
                  let share = try? JSONDecoder().decode(Share.self, from: plain),
                  let found = URL(string: share.url), found.scheme == "https" else { continue }
            if share.at > best?.at ?? 0 { best = (share.at, found) }
        }
        return best?.url
    }

    /// Lets this device through the share's gate (the token rides inside the encrypted channel).
    @MainActor
    func admit(_ server: Server) { server.channel.jar[cookie] = token }

    private struct Message: Decodable { let event: String; let message: String? }
    private struct Share: Decodable { let url: String; let at: Double }
}
