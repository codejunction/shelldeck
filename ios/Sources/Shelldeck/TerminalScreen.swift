import SwiftTerm
import SwiftUI
import UIKit

/// One shelldeck terminal: SwiftTerm draws it, /ws/{sid} carries it (same JSON messages as the web UI).
struct TerminalScreen: View {
    let server: Server
    let session: Session
    let locked: () -> Void

    var body: some View {
        TerminalHost(server: server, sid: session.id, locked: locked)
            .background(Palette.background)
            .navigationTitle(session.title)
            .navigationBarTitleDisplayMode(.inline)
    }
}

/// The web UI's tiled panes, for iPad: 1 fills, 2 side by side (stacked when taller than wide), 3-4 a 2x2 grid.
/// Each pane is its own terminal and socket; the focused one has the accent border.
struct PaneGrid: View {
    @ObservedObject var model: AppModel
    let panes: [String]
    @Binding var focused: String?
    let remove: (String) -> Void

    var body: some View {
        GeometryReader { geo in
            let wide = geo.size.width >= geo.size.height
            Group {
                switch panes.count {
                case 0: EmptyView()
                case 1: pane(panes[0])
                case 2:
                    if wide { HStack(spacing: 6) { pane(panes[0]); pane(panes[1]) } } else { VStack(spacing: 6) { pane(panes[0]); pane(panes[1]) } }
                default:
                    VStack(spacing: 6) {
                        HStack(spacing: 6) { pane(panes[0]); pane(panes[1]) }
                        HStack(spacing: 6) { pane(panes[2]); if panes.count > 3 { pane(panes[3]) } }
                    }
                }
            }
            .padding(6)
        }
        .background(Color.black.opacity(0.35))
    }

    private func pane(_ sid: String) -> some View {
        let session = model.session(sid)?.0
        let state = model.statuses[sid]?.state
        let agent = model.stats?.sessions[sid]?.agent
        return VStack(spacing: 0) {
            HStack(spacing: 8) {
                Circle().fill(session?.alive == true ? Color.green : Color.secondary.opacity(0.4)).frame(width: 7)
                Text(session?.title ?? sid).font(.caption.weight(.semibold)).lineLimit(1)
                if let agent { Text(agent.label).font(.caption2).foregroundStyle(.secondary) }
                if agent != nil, let state { StateChip(state: state) }
                Spacer()
                if panes.count > 1 {
                    Button { remove(sid) } label: { Image(systemName: "xmark").font(.caption) }
                        .buttonStyle(.borderless).help("Take out of the grid (the terminal keeps running)")
                }
            }
            .padding(.horizontal, 10).padding(.vertical, 6)
            .background(focused == sid ? Palette.accent.opacity(0.16) : Color.white.opacity(0.04))
            TerminalHost(server: model.server, sid: sid, locked: model.locked)
                .id(sid)
                .background(Palette.background)
        }
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(focused == sid ? Palette.accent : Color.white.opacity(0.08), lineWidth: 1))
        .simultaneousGesture(TapGesture().onEnded { focused = sid })
    }
}

struct TerminalHost: UIViewRepresentable {
    let server: Server
    let sid: String
    let locked: () -> Void

    func makeCoordinator() -> TermLink { TermLink(server: server, sid: sid, locked: locked) }

    func makeUIView(context: Context) -> TerminalView {
        let size: CGFloat = UIDevice.current.userInterfaceIdiom == .pad ? 15 : 13  // more room on an iPad
        let view = TerminalView(frame: .zero, font: UIFont(name: "Menlo", size: size))
        view.nativeBackgroundColor = UIColor(Palette.background)
        view.nativeForegroundColor = UIColor(white: 0.9, alpha: 1)
        view.terminalDelegate = context.coordinator
        context.coordinator.view = view
        context.coordinator.connect()
        DispatchQueue.main.async { _ = view.becomeFirstResponder() }
        return view
    }

    func updateUIView(_: TerminalView, context _: Context) {}

    static func dismantleUIView(_: TerminalView, coordinator: TermLink) { coordinator.close() }
}

@MainActor
final class TermLink: NSObject, TerminalViewDelegate {
    let server: Server
    let sid: String
    let locked: () -> Void
    weak var view: TerminalView?
    private var socket: ChannelSocket?
    private var closed = false
    private var replaying = false  // SwiftTerm answers queries in replayed output; those answers must not reach the shell
    private var heartbeat: Timer?

    init(server: Server, sid: String, locked: @escaping () -> Void) {
        self.server = server
        self.sid = sid
        self.locked = locked
    }

    func connect() {
        guard !closed else { return }
        let socket = server.socket("/ws/\(sid)")
        self.socket = socket
        socket.onOpen = { [weak self] in
            self?.view?.getTerminal().resetToInitialState()  // the server replays the scrollback
            self?.sendSize()
        }
        socket.onMessage = { [weak self] text in self?.handle(text) }
        socket.onClose = { [weak self, weak socket] code in
            guard let self, socket === self.socket else { return }
            if code == 1008 || code == 4423 {  // logged out or idle-locked, or another device took over
                self.closed = true
                self.locked()
            } else if !self.closed {
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self.connect() }
            }
        }
        heartbeat?.invalidate()
        heartbeat = Timer.scheduledTimer(withTimeInterval: 25, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.send(["type": "ping"]) }
        }
    }

    func close() {
        closed = true
        heartbeat?.invalidate()
        socket?.close()
    }

    private func handle(_ text: String) {
        guard let view, let msg = try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any] else { return }
        switch msg["type"] as? String {
        case "output":
            let data = msg["data"] as? String ?? ""
            if msg["replay"] as? Bool == true {
                replaying = true
                view.feed(text: data)
                replaying = false
            } else {
                view.feed(text: data)
            }
        case "restored":  // the previous run's text, pushed into scrollback above the new shell
            let old = (msg["data"] as? String ?? "").replacingOccurrences(of: "\r?\n", with: "\r\n", options: .regularExpression)
            let rows = view.getTerminal().rows
            replaying = true
            view.feed(text: "\u{1b}[0m\(old)\r\n\u{1b}[2m--- restored \(msg["when"] as? String ?? "") ---\u{1b}[0m"
                + String(repeating: "\r\n", count: rows) + "\u{1b}[H")
            replaying = false
        case "exit":
            view.feed(text: "\r\n\u{1b}[2m[shell exited]\u{1b}[0m\r\n")
            close()
        case "error":
            view.feed(text: "\r\n\u{1b}[31m\(msg["message"] as? String ?? "error")\u{1b}[0m\r\n")
        default:
            break
        }
    }

    private func send(_ msg: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: msg), let text = String(data: data, encoding: .utf8) else { return }
        socket?.send(text)
    }

    private func sendSize() {
        guard let terminal = view?.getTerminal() else { return }
        send(["type": "resize", "cols": terminal.cols, "rows": terminal.rows])
    }

    // MARK: TerminalViewDelegate

    func send(source _: TerminalView, data: ArraySlice<UInt8>) {
        if !replaying { send(["type": "input", "data": String(decoding: data, as: UTF8.self)]) }
    }

    func sizeChanged(source _: TerminalView, newCols: Int, newRows: Int) {
        send(["type": "resize", "cols": newCols, "rows": newRows])
    }

    func requestOpenLink(source _: TerminalView, link: String, params _: [String: String]) {
        if let url = URL(string: link), ["http", "https"].contains(url.scheme) { UIApplication.shared.open(url) }
    }

    func clipboardCopy(source _: TerminalView, content: Data) {
        UIPasteboard.general.string = String(decoding: content, as: UTF8.self)
    }

    func clipboardRead(source _: TerminalView) -> Data? { UIPasteboard.general.string.map { Data($0.utf8) } }
    func setTerminalTitle(source _: TerminalView, title _: String) {}
    func hostCurrentDirectoryUpdate(source _: TerminalView, directory _: String?) {}
    func scrolled(source _: TerminalView, position _: Double) {}
    func bell(source _: TerminalView) { UIImpactFeedbackGenerator(style: .light).impactOccurred() }
    func iTermContent(source _: TerminalView, content _: ArraySlice<UInt8>) {}
    func rangeChanged(source _: TerminalView, startY _: Int, endY _: Int) {}
}
