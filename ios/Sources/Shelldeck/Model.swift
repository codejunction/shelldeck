import Combine
import Foundation
import UIKit
import UserNotifications

/// Everything the tabs show for the connected PC: projects, machines, usage and agent states.
/// Polls while the app is open, and listens on /ws/alarms for agent states and task reminders.
@MainActor
final class AppModel: ObservableObject {
    let server: Server
    let locked: () -> Void
    @Published var projects: [Project] = []
    @Published var remotes: [Remote] = []
    @Published var stats: Stats?
    @Published var statuses: [String: AgentStatus] = [:]
    @Published var problem: String?
    @Published var machine = UserDefaults.standard.string(forKey: "machine") ?? "" {  // "" = this PC
        didSet { UserDefaults.standard.set(machine, forKey: "machine") }
    }
    var watching: String?  // the terminal on screen: no alert for it

    private var alarms: ChannelSocket?
    private var loops: [Task<Void, Never>] = []
    private var ping: Timer?

    init(server: Server, locked: @escaping () -> Void) {
        self.server = server
        self.locked = locked
    }

    var visibleProjects: [Project] { projects.filter { ($0.remoteId ?? "") == machine } }
    var sshMachines: [Remote] { remotes.filter { $0.kind == "ssh" } }

    func session(_ sid: String) -> (Session, Project)? {
        for p in projects { if let s = p.sessions.first(where: { $0.id == sid }) { return (s, p) } }
        return nil
    }

    func start() {
        guard loops.isEmpty else { return }
        loops = [
            Task { while !Task.isCancelled { await refresh(); try? await Task.sleep(for: .seconds(5)) } },
            Task { while !Task.isCancelled { await loadStats(); try? await Task.sleep(for: .seconds(3)) } },
        ]
        listen()
    }

    func stop() {
        loops.forEach { $0.cancel() }
        loops = []
        ping?.invalidate()
        alarms?.close()
        server.channel.shutdown()
        alarms = nil
    }

    func refresh() async {
        do {
            projects = try await server.get("/api/projects", as: ProjectList.self).projects
            remotes = (try? await server.get("/api/remotes", as: RemoteList.self).remotes) ?? []
            problem = nil
        } catch let e as APIError where e.locked {
            stop()
            locked()
        } catch {
            problem = error.localizedDescription
        }
    }

    func loadStats() async {
        if let s = try? await server.get("/api/stats", as: Stats.self) { stats = s }
    }

    // MARK: alarm socket

    private func listen() {
        let socket = server.socket("/ws/alarms")
        alarms = socket
        socket.onMessage = { [weak self] text in self?.handle(text) }
        socket.onClose = { [weak self, weak socket] code in
            guard let self, socket === self.alarms, code != 1008, code != 4423 else { return }
            Task { @MainActor in
                try? await Task.sleep(for: .seconds(3))
                if self.alarms === socket, !self.loops.isEmpty { self.listen() }
            }
        }
        ping?.invalidate()
        ping = Timer.scheduledTimer(withTimeInterval: 25, repeats: true) { [weak socket] _ in
            Task { @MainActor in socket?.send("{\"type\":\"ping\"}") }
        }
    }

    private func handle(_ text: String) {
        guard let data = text.data(using: .utf8),
              let msg = try? JSONDecoder.snake.decode(AlarmMessage.self, from: data) else { return }
        switch msg.type {
        case "agent_state":
            guard let sid = msg.sessionId, let status = msg.status else { return }
            let was = statuses[sid]?.state
            statuses[sid] = status
            if let was, was != status.state { alertIfNeeded(sid, from: was, to: status.state) }
        case "alarm":
            if let task = msg.task { Alerts.post(title: "Reminder", body: task.title, id: "task-\(task.id)") }
        default:
            break
        }
    }

    /// Waiting (blocked) and finished (done, or working -> idle) agents ring, sub-agents don't.
    private func alertIfNeeded(_ sid: String, from was: String, to now: String) {
        guard let (session, project) = session(sid), session.parent == nil else { return }
        let onScreen = sid == watching && UIApplication.shared.applicationState == .active
        let label = stats?.sessions[sid]?.agent?.label ?? "Agent"
        let who = "\(session.nick ?? session.title) · \(project.name)"
        if now == "blocked", Alerts.waitingOn, !onScreen {
            Alerts.post(title: "\(label) needs you", body: who, id: "agent-\(sid)")
        } else if now == "done" || (was == "working" && now == "idle"), Alerts.doneOn, !onScreen {
            Alerts.post(title: "\(label) finished", body: who, id: "agent-\(sid)")
        }
    }

    private struct ProjectList: Decodable { let projects: [Project] }
    private struct RemoteList: Decodable { let remotes: [Remote] }
    private struct AlarmMessage: Decodable {
        let type: String
        let sessionId: String?
        let status: AgentStatus?
        let task: TaskItem?
    }
}

extension JSONDecoder {
    static let snake: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()
}

/// Local notifications with sound. ponytail: they come from the open socket, so they ring while the app is
/// open or just backgrounded; a locked phone needs push, which a free Apple ID can't sign.
enum Alerts {
    static var waitingOn: Bool { UserDefaults.standard.object(forKey: "alertWaiting") as? Bool ?? true }
    static var doneOn: Bool { UserDefaults.standard.object(forKey: "alertDone") as? Bool ?? true }

    static func setUp() {
        let center = UNUserNotificationCenter.current()
        center.delegate = Presenter.shared
        center.requestAuthorization(options: [.alert, .sound, .badge]) { _, _ in }
    }

    static func post(title: String, body: String, id: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        content.interruptionLevel = .timeSensitive
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: id, content: content, trigger: nil))
    }

    /// Shows banners (and plays the sound) while the app is in front too.
    final class Presenter: NSObject, UNUserNotificationCenterDelegate {
        static let shared = Presenter()
        func userNotificationCenter(_: UNUserNotificationCenter, willPresent _: UNNotification,
                                    withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
            done([.banner, .sound, .list])
        }
    }
}
