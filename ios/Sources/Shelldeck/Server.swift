import CryptoKit
import Foundation

/// The shelldeck server's API, every call through the PC's end-to-end encrypted channel (Channel.swift).
@MainActor
final class Server {
    static let agent = "ShelldeckApp/1"  // with X-Shelldeck-Client: ios, logins are "remote": they never take over the PC browser

    let base: URL
    let channel: Channel
    init(base: URL, key: Curve25519.KeyAgreement.PublicKey) {
        self.base = base
        channel = Channel(base: base, key: key)
    }

    var isTunnel: Bool { base.scheme == "https" }
    var keyText: String { channel.serverKey.rawRepresentation.base64EncodedString() }

    @discardableResult
    func request(_ path: String, method: String = "GET", body: [String: Any]? = nil, timeout: TimeInterval = 15) async throws -> Data {
        var headers = ["user-agent": Server.agent, "x-shelldeck-client": "ios"]
        var payload: Data?
        if let body {
            payload = try JSONSerialization.data(withJSONObject: body)
            headers["content-type"] = "application/json"
        }
        let reply = try await withTimeout(timeout) { try await self.channel.http(method, path, headers: headers, body: payload) }
        let (data, status) = (reply.body, reply.status)
        if status >= 400 {
            let code = (try? JSONDecoder().decode([String: String].self, from: data))?["error"] ?? "http_\(status)"
            throw APIError(code: code, status: status)
        }
        return data
    }

    func get<T: Decodable>(_ path: String, as _: T.Type = T.self) async throws -> T {
        try decoder.decode(T.self, from: await request(path))
    }

    func post<T: Decodable>(_ path: String, _ body: [String: Any] = [:], as _: T.Type = T.self) async throws -> T {
        try decoder.decode(T.self, from: await request(path, method: "POST", body: body))
    }

    func put<T: Decodable>(_ path: String, _ body: [String: Any], as _: T.Type = T.self) async throws -> T {
        try decoder.decode(T.self, from: await request(path, method: "PUT", body: body))
    }

    func delete(_ path: String) async throws {
        _ = try await request(path, method: "DELETE")
    }

    func send(_ path: String, _ body: [String: Any] = [:]) async throws {
        _ = try await request(path, method: "POST", body: body)
    }

    /// Is a shelldeck server answering here? (For a share, the device cookie must already be set.)
    func reachable() async -> Bool {
        guard let data = try? await request("/api/health", timeout: 4) else { return false }
        return (try? JSONDecoder().decode([String: String].self, from: data))?["app"] == "shelldeck"
    }

    /// /api/health's HTTP status, or nil when nothing answered. 403 through a share: the gate refused this device
    /// (its trust was revoked, or replaced by setting it up again).
    func probe() async -> Int? {
        do {
            try await request("/api/health", timeout: 6)
            return 200
        } catch let e as APIError {
            return e.status
        } catch {
            return nil
        }
    }

    /// A WebSocket on the server (/ws/<sid>, /ws/alarms), inside the channel.
    func socket(_ path: String) -> ChannelSocket { channel.socket(path) }

    /// The PC's end-to-end key, asked over home Wi-Fi when this iPhone has none pinned yet (trust on first use;
    /// a share QR carries it instead).
    static func lanKey(_ base: URL) async -> Curve25519.KeyAgreement.PublicKey? {
        var req = URLRequest(url: URL(string: "/e2e/key", relativeTo: base)!)
        req.timeoutInterval = 4
        guard let (data, _) = try? await URLSession.shared.data(for: req),
              let text = (try? JSONDecoder().decode([String: String].self, from: data))?["key"] else { return nil }
        return e2eKey(text)
    }
}

func withTimeout<T: Sendable>(_ seconds: TimeInterval, _ work: @escaping @MainActor () async throws -> T) async throws -> T {
    try await withThrowingTaskGroup(of: T.self) { group in
        group.addTask { try await work() }
        group.addTask {
            try await Task.sleep(for: .seconds(seconds))
            throw URLError(.timedOut)
        }
        defer { group.cancelAll() }
        return try await group.next()!
    }
}

private let decoder: JSONDecoder = {
    let d = JSONDecoder()
    d.keyDecodingStrategy = .convertFromSnakeCase
    return d
}()

struct APIError: LocalizedError {
    let code: String
    let status: Int
    var locked: Bool { status == 401 || code == "in_use" }
    var errorDescription: String? {
        switch code {
        case "invalid_password": "Wrong password."
        case "too_many_attempts": "Too many attempts. Wait a minute and try again."
        case "setup_required": "Create a password in shelldeck on your PC first."
        case "locked": "Signed out. Log in again."
        default: "Server error: \(code)"
        }
    }
}

struct AuthStatus: Decodable {
    let hasPassword: Bool
    let authenticated: Bool
}

struct Project: Decodable, Identifiable {
    let id: String
    let name: String
    let path: String
    let remoteId: String?
    let sessions: [Session]
}

struct Session: Decodable, Identifiable, Hashable {
    let id: String
    let name: String?
    let nick: String?
    let alive: Bool?
    let parent: String?
    var title: String { name ?? nick ?? id }
}

/// An SSH machine saved on the PC; its projects carry its id (the sidebar's machine picker).
struct Remote: Decodable, Identifiable {
    let id: String
    let name: String
    let kind: String
}

struct Stats: Decodable {
    struct System: Decodable {
        let cpu: Double
        let memUsed: Double
        let memTotal: Double
        let gpu: GPU?
    }
    struct GPU: Decodable {
        let name: String
        let util: Double
        let memUsed: Double
        let memTotal: Double
    }
    struct Usage: Decodable {
        let cpu: Double
        let mem: Double
        let procs: Int
        let top: String?
        let agent: Agent?
        let ports: [Int]?
    }
    struct Agent: Decodable {
        let label: String
        let model: String?
        let context: Context?
    }
    struct Context: Decodable {
        let used: Double?
        let window: Double?
        let model: String?
    }
    let system: System
    let sessions: [String: Usage]
}

/// The server-owned agent lifecycle (agent_state.py): unknown | idle | working | blocked | done | exited.
struct AgentStatus: Decodable {
    let state: String
    let reason: String?
    let since: Double?
}

struct Job: Decodable, Identifiable {
    let id: String
    let name: String
    let projectId: String
    let command: String
    let cron: String
    let enabled: Bool
    let nextRunOn: String?
    let lastRunOn: String?
    let lastStatus: String?
    let lastError: String?
}

struct TaskItem: Decodable, Identifiable {
    let id: String
    let title: String
    let description: String?
    let column: String
    let priority: String?
    let dueAt: String?
    let reminderAt: String?
}

struct Note: Decodable, Identifiable {
    let id: String
    var body: String
    var title: String {
        let first = body.split(separator: "\n", omittingEmptySubsequences: true).first.map(String.init) ?? ""
        return first.trimmingCharacters(in: CharacterSet(charactersIn: "# ")).isEmpty ? "Untitled" : first.trimmingCharacters(in: CharacterSet(charactersIn: "# "))
    }
}

func bytes(_ n: Double) -> String {
    let f = ByteCountFormatter()
    f.countStyle = .memory
    f.allowsNonnumericFormatting = false  // "0 bytes", not "Zero KB"
    return f.string(fromByteCount: Int64(n))
}
