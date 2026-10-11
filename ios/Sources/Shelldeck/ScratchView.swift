import SwiftUI

/// The PC's scratchpad: notes, the first line is the title. Edits save themselves after a pause.
/// A split view: notes beside the editor on iPad, push navigation on a phone.
struct ScratchView: View {
    @ObservedObject var model: AppModel
    @State private var notes: [Note] = []
    @State private var selected: String?

    var body: some View {
        NavigationSplitView {
            List(selection: $selected) {
                if notes.isEmpty { Text("No notes").foregroundStyle(.secondary) }
                ForEach(notes) { note in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(note.title).lineLimit(1)
                        Text(note.body.split(separator: "\n").dropFirst().first.map(String.init) ?? "")
                            .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                    .tag(note.id)
                    .swipeActions { Button("Delete", role: .destructive) { remove(note.id) } }
                    .contextMenu { Button("Delete", role: .destructive) { remove(note.id) } }
                }
            }
            .scrollContentBackground(.hidden)
            .background(Palette.background)
            .navigationTitle("Scratchpad")
            .navigationSplitViewColumnWidth(min: 240, ideal: 300, max: 400)
            .toolbar {
                Button {
                    Task {
                        if let n = try? await model.server.post("/api/scratch", ["body": ""], as: Note.self) {
                            await load()
                            selected = n.id
                        }
                    }
                } label: { Image(systemName: "square.and.pencil") }
            }
            .refreshable { await load() }
            .task { await load() }
        } detail: {
            if let id = selected {
                NoteEditor(server: model.server, note: notes.first { $0.id == id } ?? Note(id: id, body: ""))
                    .id(id)
                    .onDisappear { Task { await load() } }
            } else {
                ContentUnavailableView("No note open", systemImage: "note.text")
                    .background(Palette.background)
            }
        }
    }

    private func remove(_ id: String) {
        Task {
            if selected == id { selected = nil }
            try? await model.server.delete("/api/scratch/\(id)")
            await load()
        }
    }

    private func load() async {
        if let list = try? await model.server.get("/api/scratch", as: Notes.self) { notes = list.notes }
    }

    private struct Notes: Decodable { let notes: [Note] }
}

struct NoteEditor: View {
    let server: Server
    @State var note: Note
    @State private var saving: Task<Void, Never>?

    var body: some View {
        TextEditor(text: $note.body)
            .font(.body.monospaced())
            .scrollContentBackground(.hidden)
            .padding(.horizontal, 8)
            .background(Palette.background)
            .navigationTitle(note.title)
            .navigationBarTitleDisplayMode(.inline)
            .onChange(of: note.body) {
                saving?.cancel()
                saving = Task {
                    try? await Task.sleep(for: .milliseconds(600))
                    if !Task.isCancelled { save() }
                }
            }
            .onDisappear { save() }
    }

    private func save() {
        let body = note.body
        Task { _ = try? await server.request("/api/scratch/\(note.id)", method: "PUT", body: ["body": body]) }
    }
}
