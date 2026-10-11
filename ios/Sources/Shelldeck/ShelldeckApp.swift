import SwiftUI
import UIKit

/// Native shelldeck for iPhone. It finds the chosen PC (its home Wi-Fi address first, then the share URL the
/// PC announced to this trusted device), logs in, and shows the PC's terminals, usage, schedule and notes.
@main
struct ShelldeckApp: App {
    init() {
        PC.migrate()
        Alerts.setUp()
    }

    var body: some Scene {
        WindowGroup {
            RootView().preferredColorScheme(.dark).tint(Palette.accent)
        }
    }
}

enum Palette {
    static let background = Color(red: 0.035, green: 0.035, blue: 0.043)  // zinc-950, as the web UI
    static let accent = Color(red: 0.655, green: 0.545, blue: 0.98)  // violet-400
}

enum Stage {
    case finding
    case address
    case join(ShareLink)
    case unreachable
    case login(Server)
    case main(AppModel)
}

struct RootView: View {
    @AppStorage("host") private var host = ""  // a Wi-Fi address typed in, until that PC trusts this iPhone
    @State private var stage = Stage.finding
    @State private var known: PC?  // the trusted PC this connection came from; nil = enrol after login
    @State private var note: String?  // why the address screen is showing

    var body: some View {
        ZStack {
            Palette.background.ignoresSafeArea()
            switch stage {
            case .finding:
                ProgressView(PC.current.map { "Finding \($0.name)…" } ?? "Finding your PC…").task { await find() }
            case .address:
                AddressView(note: note, save: { host = $0.absoluteString; stage = .finding },
                            join: { host = ""; stage = .join($0) },
                            cancel: PC.all().isEmpty ? nil : { stage = .finding })
            case .join(let link):
                ShareJoinView(link: link, joined: { known = nil; stage = .login($0) }, cancel: { stage = .address })
            case .unreachable:
                Unreachable(retry: { stage = .finding }, change: { stage = .address }, switchTo: switchTo)
            case .login(let server):
                LoginView(server: server) { open(server) }
            case .main(let model):
                MainTabs(model: model, switchTo: switchTo, addPC: { model.stop(); stage = .address })
            }
        }
    }

    private func switchTo(_ pc: PC) {
        if case .main(let model) = stage { model.stop() }
        PC.select(pc.id)
        host = ""
        stage = .finding
    }

    private func open(_ server: Server) {
        Task {
            // reuse a still-valid trust for this PC (same key) instead of enrolling again; enrol only without one
            if let pc = PC.all().first(where: { $0.e2e == server.keyText }) {
                pc.admit(server)
                if (try? await server.get("/api/trusted/me", as: Me.self))?.trusted == true {
                    known = pc
                    return
                }
            }
            if let pc = await enrol(server, lan: server.isTunnel ? nil : host) {
                known = pc
                host = ""
            }
        }
        let model = AppModel(server: server) { [server] in stage = .login(server) }
        stage = .main(model)
    }

    /// A typed-in Wi-Fi address first; else the chosen PC on home Wi-Fi, else its newest share URL. Every
    /// connection is end-to-end encrypted to the PC's pinned key (Channel.swift).
    private func find() async {
        var server: Server?
        known = nil
        note = nil
        if let url = URL(string: host), !host.isEmpty {
            if let key = await Server.lanKey(url) { server = await reach(Server(base: url, key: key)) }  // trust on first use
        } else if let pc = PC.current {
            known = pc
            guard let key = pc.e2eKey else {  // trusted before end-to-end encryption existed
                note = "Scan \(pc.name)'s share QR once more to turn on end-to-end encryption."
                stage = .address
                return
            }
            if let lan = pc.lan.flatMap(URL.init(string:)) { server = await reach(Server(base: lan, key: key)) }
            if server == nil, let url = await pc.latestShare() {
                let s = Server(base: url, key: key)
                pc.admit(s)
                switch await s.probe() {
                case 200?: server = s
                case 403?:  // the PC answered, but no longer trusts this device's token
                    note = "\(pc.name) no longer trusts this device (revoked, or set up again elsewhere). Scan its share QR to trust it again."
                    stage = .address
                    return
                default: break
                }
            }
        }
        guard let server else {
            stage = host.isEmpty && PC.current == nil ? .address : .unreachable
            return
        }
        let status = try? await server.get("/api/auth/status", as: AuthStatus.self)
        if status?.authenticated == true { open(server) } else { stage = .login(server) }
    }
}

private func reach(_ server: Server) async -> Server? { await server.reachable() ? server : nil }

private struct Me: Decodable { let trusted: Bool }

/// After logging in to a PC this iPhone isn't trusted by yet: become a trusted device of it, pinning the key.
@MainActor
func enrol(_ server: Server, lan: String?) async -> PC? {
    guard var pc = try? await server.post("/api/trusted", ["name": UIDevice.current.name], as: PC.self) else { return nil }
    pc.lan = lan
    pc.e2e = server.keyText
    PC.add(pc)
    PC.select(pc.id)
    return pc
}

struct MainTabs: View {
    @ObservedObject var model: AppModel
    let switchTo: (PC) -> Void
    let addPC: () -> Void

    var body: some View {
        TabView {
            TerminalsView(model: model).tabItem { Label("Terminals", systemImage: "terminal") }
                .badge(model.statuses.values.filter { $0.state == "blocked" }.count)
            SystemView(model: model).tabItem { Label("System", systemImage: "gauge.with.dots.needle.33percent") }
            ScheduleView(model: model).tabItem { Label("Schedule", systemImage: "calendar.badge.clock") }
            ScratchView(model: model).tabItem { Label("Scratchpad", systemImage: "note.text") }
            SettingsView(model: model, switchTo: switchTo, addPC: addPC).tabItem { Label("Settings", systemImage: "gearshape") }
        }
        .onAppear { model.start() }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.willEnterForegroundNotification)) { _ in
            model.start()
            Task { await model.refresh() }
        }
    }
}

/// "192.168.1.20:5455" -> http://192.168.1.20:5455; nil unless http(s) with a host.
func serverURL(_ text: String) -> URL? {
    var s = text.trimmingCharacters(in: .whitespaces)
    if !s.contains("://") { s = "http://" + s }
    guard let url = URL(string: s), ["http", "https"].contains(url.scheme), url.host() != nil else { return nil }
    return URL(string: "\(url.scheme!)://\(url.host()!)\(url.port.map { ":\($0)" } ?? "")")
}

struct AddressView: View {
    let note: String?
    let save: (URL) -> Void
    let join: (ShareLink) -> Void
    let cancel: (() -> Void)?
    @State private var text = ""
    @State private var scanning = false

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "apple.terminal").font(.system(size: 44)).foregroundStyle(Palette.accent)
            Text("shelldeck").font(.largeTitle.bold())
            if let note { Text(note).font(.callout).foregroundStyle(.orange).multilineTextAlignment(.center) }
            Button { scanning = true } label: { Label("Scan share QR", systemImage: "qrcode.viewfinder").frame(maxWidth: .infinity) }
                .buttonStyle(.borderedProminent).controlSize(.large).disabled(!QRScanner.available)
            Text("Run `sd share` on the PC and scan its code. Once allowed, this iPhone is trusted and finds the PC by itself.")
                .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            Divider().padding(.vertical, 8)
            Text("Or, on the same Wi-Fi, the PC's address (`sd serve --host 0.0.0.0 --insecure-http`), or paste a share link.")
                .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            TextField("192.168.1.20:5455", text: $text)
                .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                .textFieldStyle(.roundedBorder).onSubmit(connect)
            Button("Connect", action: connect).buttonStyle(.bordered).disabled(shareLink(text) == nil && serverURL(text) == nil)
            if let cancel { Button("Cancel", action: cancel).padding(.top, 8) }
        }
        .padding(24)
        .frame(maxWidth: 460)  // a form-sized column on iPad
        .sheet(isPresented: $scanning) {
            QRScanner { link in scanning = false; join(link) }.ignoresSafeArea()
        }
    }

    private func connect() {
        if let link = shareLink(text) { join(link) } else if let url = serverURL(text) { save(url) }
    }
}

struct Unreachable: View {
    let retry: () -> Void
    let change: () -> Void
    let switchTo: (PC) -> Void

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "wifi.exclamationmark").font(.system(size: 40)).foregroundStyle(.secondary)
            Text("Can't reach \(PC.current?.name ?? "your PC")").font(.headline)
            Text("Away from home, start a share on the PC (`sd share`); this iPhone finds it by itself.")
                .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            Button("Retry", action: retry).buttonStyle(.borderedProminent)
            let others = PC.all().filter { $0.id != PC.current?.id }
            if !others.isEmpty {
                Menu("Switch PC") { ForEach(others) { pc in Button(pc.name) { switchTo(pc) } } }
            }
            Button("Scan a share QR or add a PC", action: change)
        }
        .padding(24)
        .frame(maxWidth: 460)  // a form-sized column on iPad
    }
}

struct LoginView: View {
    let server: Server
    let done: () -> Void
    @State private var password = ""
    @State private var problem: String?
    @State private var busy = false

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "lock").font(.system(size: 40)).foregroundStyle(Palette.accent)
            Text("Log in").font(.title.bold())
            Text(server.base.host() ?? "").font(.footnote.monospaced()).foregroundStyle(.secondary)
            SecureField("Password", text: $password)
                .textContentType(.password).textFieldStyle(.roundedBorder).onSubmit(login)
            if let problem { Text(problem).font(.footnote).foregroundStyle(.red) }
            Button(action: login) { busy ? AnyView(ProgressView()) : AnyView(Text("Log in")) }
                .buttonStyle(.borderedProminent).disabled(password.isEmpty || busy)
        }
        .padding(24)
        .frame(maxWidth: 460)  // a form-sized column on iPad
    }

    private func login() {
        busy = true
        Task {
            defer { busy = false }
            do {
                try await server.request("/api/auth/login", method: "POST", body: ["password": password])
                done()
            } catch {
                problem = error.localizedDescription
            }
        }
    }
}
