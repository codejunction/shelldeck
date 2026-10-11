import SwiftUI

/// Scheduled jobs (cron commands run on the PC) and the personal task board.
struct ScheduleView: View {
    @ObservedObject var model: AppModel
    @State private var tab = 0
    @State private var jobs: [Job] = []
    @State private var tasks: [TaskItem] = []
    @State private var editingJob = false
    @State private var editingTask: TaskItem?
    @State private var addingTask = false
    @State private var problem: String?

    private static let columns = [("todo", "To do"), ("inprogress", "In progress"), ("done", "Done")]

    var body: some View {
        NavigationStack {
            List {
                if let problem { Text(problem).foregroundStyle(.red).font(.footnote) }
                if tab == 0 { jobList } else { taskList }
            }
            .scrollContentBackground(.hidden)
            .background(Palette.background)
            .navigationTitle("Schedule")
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Picker("", selection: $tab) {
                        Text("Jobs").tag(0)
                        Text("Tasks").tag(1)
                    }
                    .pickerStyle(.segmented).frame(width: 200)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { if tab == 0 { editingJob = true } else { addingTask = true } } label: { Image(systemName: "plus") }
                }
            }
            .refreshable { await load() }
            .task { await load() }
            .sheet(isPresented: $editingJob) { JobForm(model: model) { editingJob = false; Task { await load() } } }
            .sheet(isPresented: $addingTask) { TaskForm(server: model.server, task: nil) { addingTask = false; Task { await load() } } }
            .sheet(item: $editingTask) { t in TaskForm(server: model.server, task: t) { editingTask = nil; Task { await load() } } }
        }
    }

    @ViewBuilder private var jobList: some View {
        if jobs.isEmpty { Text("No scheduled jobs").foregroundStyle(.secondary) }
        ForEach(jobs) { job in
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(job.name).font(.body.weight(.medium))
                    Spacer()
                    Toggle("", isOn: Binding(get: { job.enabled }, set: { _ in act("/api/schedule/jobs/\(job.id)/toggle") }))
                        .labelsHidden()
                }
                Text(job.command).font(.caption.monospaced()).lineLimit(2)
                Text(["\(job.cron)", model.projects.first { $0.id == job.projectId }?.name,
                      job.nextRunOn.map { "next \(when($0))" }, job.lastStatus.map { "last: \($0.replacingOccurrences(of: "_", with: " "))" }]
                    .compactMap { $0 }.joined(separator: " · "))
                    .font(.caption).foregroundStyle(job.lastStatus == "failed" ? .red : .secondary)
            }
            .swipeActions {
                Button("Delete", role: .destructive) { remove("/api/schedule/jobs/\(job.id)") }
                Button("Run now") { act("/api/schedule/jobs/\(job.id)/run") }.tint(Palette.accent)
            }
        }
    }

    @ViewBuilder private var taskList: some View {
        ForEach(Self.columns, id: \.0) { col, label in
            Section(label) {
                ForEach(tasks.filter { $0.column == col }) { task in
                    Button { editingTask = task } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(task.title).foregroundStyle(.primary).strikethrough(col == "done")
                            if let due = task.dueAt { Text("due \(when(due))").font(.caption).foregroundStyle(.secondary) }
                        }
                    }
                    .swipeActions {
                        Button("Delete", role: .destructive) { remove("/api/tasks/\(task.id)") }
                        ForEach(Self.columns.filter { $0.0 != col }, id: \.0) { to, name in
                            Button(name) { act("/api/tasks/\(task.id)/move", ["column": to]) }.tint(to == "done" ? .green : Palette.accent)
                        }
                    }
                }
            }
        }
    }

    private func load() async {
        do {
            jobs = try await model.server.get("/api/schedule/jobs", as: Jobs.self).jobs
            tasks = try await model.server.get("/api/tasks", as: Tasks.self).tasks
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }

    private func act(_ path: String, _ body: [String: Any] = [:]) {
        Task { try? await model.server.send(path, body); await load() }
    }

    private func remove(_ path: String) {
        Task { try? await model.server.delete(path); await load() }
    }

    private struct Jobs: Decodable { let jobs: [Job] }
    private struct Tasks: Decodable { let tasks: [TaskItem] }
}

/// "2026-10-12T09:00:00+00:00" -> "Oct 12, 9:00 AM" in local time.
func when(_ iso: String) -> String {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let date = f.date(from: iso) ?? { f.formatOptions = [.withInternetDateTime]; return f.date(from: iso) }()
    return date?.formatted(date: .abbreviated, time: .shortened) ?? iso
}

struct JobForm: View {
    @ObservedObject var model: AppModel
    let done: () -> Void
    @State private var name = ""
    @State private var command = ""
    @State private var cron = "0 9 * * *"
    @State private var project = ""
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            Form {
                TextField("Name", text: $name)
                Picker("Project", selection: $project) {
                    ForEach(model.projects) { Text($0.name).tag($0.id) }
                }
                TextField("Command", text: $command).font(.body.monospaced())
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                Section {
                    TextField("Cron", text: $cron).font(.body.monospaced()).textInputAutocapitalization(.never)
                } footer: { Text("minute hour day month weekday, in the PC's time zone (e.g. 0 9 * * 1-5)") }
                if let problem { Text(problem).foregroundStyle(.red) }
            }
            .navigationTitle("New job")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel", action: done) }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { save() }.disabled(name.isEmpty || command.isEmpty || cron.isEmpty || project.isEmpty)
                }
            }
            .onAppear { project = project.isEmpty ? model.projects.first?.id ?? "" : project }
        }
    }

    private func save() {
        Task {
            do {
                try await model.server.send("/api/schedule/jobs", ["name": name, "project_id": project, "command": command, "cron": cron,
                                                                   "timezone": TimeZone.current.identifier])
                done()
            } catch {
                problem = error.localizedDescription
            }
        }
    }
}

struct TaskForm: View {
    let server: Server
    let task: TaskItem?
    let done: () -> Void
    @State private var title = ""
    @State private var notes = ""
    @State private var hasReminder = false
    @State private var reminder = Date().addingTimeInterval(3600)
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            Form {
                TextField("Title", text: $title)
                TextField("Notes", text: $notes, axis: .vertical).lineLimit(3...8)
                Toggle("Remind me", isOn: $hasReminder)
                if hasReminder { DatePicker("At", selection: $reminder) }
                if let problem { Text(problem).foregroundStyle(.red) }
            }
            .navigationTitle(task == nil ? "New task" : "Task")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel", action: done) }
                ToolbarItem(placement: .confirmationAction) { Button("Save") { save() }.disabled(title.isEmpty) }
            }
            .onAppear {
                guard let task else { return }
                title = task.title
                notes = task.description ?? ""
                if let r = task.reminderAt, let date = ISO8601DateFormatter().date(from: r) { hasReminder = true; reminder = date }
            }
        }
    }

    private func save() {
        var body: [String: Any] = ["title": title, "description": notes]
        body["reminder_at"] = hasReminder ? ISO8601DateFormatter().string(from: reminder) : NSNull()
        Task {
            do {
                if let task {
                    try await server.request("/api/tasks/\(task.id)", method: "PUT", body: body)
                } else {
                    try await server.send("/api/tasks", body.merging(["column": "todo"]) { $1 })
                }
                done()
            } catch {
                problem = error.localizedDescription
            }
        }
    }
}
