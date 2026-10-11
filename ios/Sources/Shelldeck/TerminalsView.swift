import SwiftUI

/// Terminals grouped by project, as in the PC's sidebar: only projects added on the PC (no folders from here).
/// A split view: on iPad (and wide iPhones in landscape) the list stays beside the open terminal; on a phone
/// it collapses to push navigation. Swipe to close, + for a new one; the agent in each terminal shows its state.
struct TerminalsView: View {
    @ObservedObject var model: AppModel
    @State private var selected: Session?
    @State private var columns = NavigationSplitViewVisibility.all
    @Environment(\.horizontalSizeClass) private var width
    // iPad: the tiled panes, as in the web UI (terminal ids, kept between launches)
    @AppStorage("panes") private var paneList = ""
    @AppStorage("paneFocus") private var paneFocus = ""
    private var panes: [String] { paneList.split(separator: ",").map(String.init) }
    private var tiled: Bool { width == .regular }

    var body: some View {
        NavigationSplitView(columnVisibility: $columns) {
            List(selection: $selected) {
                if let problem = model.problem { Text(problem).foregroundStyle(.red).font(.footnote) }
                if model.visibleProjects.isEmpty {
                    Text("No projects. Add folders in shelldeck on the PC.").foregroundStyle(.secondary)
                }
                ForEach(model.visibleProjects) { project in
                    Section(project.name) {
                        ForEach(project.sessions) { session in
                            row(session).tag(session)
                                .swipeActions { Button("Close", role: .destructive) { close(session) } }
                                .contextMenu {
                                    if tiled && !panes.contains(session.id) {
                                        Button { open(session.id, newPane: true) } label: { Label("Open in new pane", systemImage: "rectangle.split.2x1") }
                                            .disabled(panes.count >= 4)
                                    }
                                    Button("Close terminal", role: .destructive) { close(session) }
                                }
                        }
                        Button { create(in: project) } label: { Label("New terminal", systemImage: "plus") }
                            .font(.footnote)
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background(Palette.background)
            .refreshable { await model.refresh() }
            .navigationTitle("Terminals")
            .navigationSplitViewColumnWidth(min: 260, ideal: 320, max: 420)
            .toolbar {
                if !model.sshMachines.isEmpty {
                    ToolbarItem(placement: .topBarLeading) {
                        Picker("Machine", selection: $model.machine) {
                            Label("This PC", systemImage: "desktopcomputer").tag("")
                            ForEach(model.sshMachines) { Label($0.name, systemImage: "server.rack").tag($0.id) }
                        }
                        .pickerStyle(.menu)
                    }
                }
            }
        } detail: {
            if tiled && !panes.isEmpty {
                PaneGrid(model: model, panes: panes, focused: Binding(get: { paneFocus }, set: { paneFocus = $0 ?? "" }),
                         remove: { sid in setPanes(panes.filter { $0 != sid }) })
                    .navigationTitle(model.session(paneFocus)?.0.title ?? "Terminals")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar { fullWidthButton }
            } else if let session = selected {
                TerminalScreen(server: model.server, session: session, locked: model.locked)
                    .id(session.id)  // a new terminal view and socket per terminal
                    .toolbar { fullWidthButton }
            } else {
                ContentUnavailableView("No terminal open", systemImage: "terminal", description: Text("Pick one from the list, or add one with New terminal."))
                    .background(Palette.background)
            }
        }
        .navigationSplitViewStyle(.balanced)
        .onChange(of: selected) {
            if tiled, let s = selected, s.id != paneFocus { open(s.id, newPane: false) }
            model.watching = tiled ? paneFocus : selected?.id
        }
        .onChange(of: paneFocus) {
            model.watching = paneFocus
            if tiled, let s = model.session(paneFocus)?.0, s != selected { selected = s }
        }
        .onChange(of: model.projects.map { $0.sessions.map(\.id) }) {  // closed elsewhere: drop it
            guard !model.projects.isEmpty else { return }
            if let s = selected, model.session(s.id) == nil { selected = nil }
            setPanes(panes.filter { model.session($0) != nil })
        }
    }

    /// Full width: hides the sidebar. Only shown while it's visible; iPadOS's own sidebar button brings it back
    /// (a second sidebar button here would duplicate that one).
    @ToolbarContentBuilder private var fullWidthButton: some ToolbarContent {
        ToolbarItem(placement: .topBarTrailing) {
            if columns != .detailOnly {
                Button { columns = .detailOnly } label: { Image(systemName: "arrow.up.left.and.arrow.down.right") }
                    .help("Full width")
            }
        }
    }

    /// iPad: show a terminal. Already in a pane: focus it. Else in a new pane (up to 4), or in place of the focused one.
    private func open(_ sid: String, newPane: Bool) {
        var list = panes
        if !list.contains(sid) {
            if list.isEmpty || (newPane && list.count < 4) {
                list.append(sid)
            } else {
                list[list.firstIndex(of: paneFocus) ?? list.count - 1] = sid
            }
        }
        setPanes(list)
        paneFocus = sid
    }

    private func setPanes(_ list: [String]) {
        paneList = list.joined(separator: ",")
        if !list.contains(paneFocus) { paneFocus = list.last ?? "" }
    }

    private func row(_ session: Session) -> some View {
        let usage = model.stats?.sessions[session.id]
        let state = model.statuses[session.id]?.state
        return HStack(spacing: 10) {
            Circle().fill(session.alive == true ? Color.green : Color.secondary.opacity(0.4)).frame(width: 8)
            VStack(alignment: .leading, spacing: 2) {
                Text(session.title)
                if let agent = usage?.agent {
                    Text([agent.label, agent.context?.model ?? agent.model].compactMap { $0 }.joined(separator: " · "))
                        .font(.caption).foregroundStyle(.secondary)
                } else if let top = usage?.top {
                    Text(top).font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
            if usage?.agent != nil, let state { StateChip(state: state) }
        }
    }

    private func create(in project: Project) {
        Task {
            if let s = try? await model.server.post("/api/sessions", ["project_id": project.id], as: Session.self) {
                await model.refresh()
                if tiled { open(s.id, newPane: true) }
                selected = s
            }
        }
    }

    private func close(_ session: Session) {
        Task {
            if selected == session { selected = nil }
            setPanes(panes.filter { $0 != session.id })
            try? await model.server.delete("/api/sessions/\(session.id)")
            await model.refresh()
        }
    }
}

/// An agent's lifecycle state, as the sidebar chip on the PC.
struct StateChip: View {
    let state: String

    var body: some View {
        let (text, color): (String, Color) = switch state {
        case "blocked": ("needs you", .orange)
        case "working": ("working", Palette.accent)
        case "done": ("done", .green)
        case "exited": ("exited", .secondary)
        default: ("idle", .secondary)
        }
        Text(text).font(.caption2.bold()).padding(.horizontal, 7).padding(.vertical, 3)
            .background(color.opacity(0.18), in: Capsule()).foregroundStyle(color)
    }
}
