import SwiftUI

/// The PC's CPU, memory and GPU, and what each terminal uses (the web UI's Task manager).
struct SystemView: View {
    @ObservedObject var model: AppModel
    @Environment(\.horizontalSizeClass) private var width

    var body: some View {
        NavigationStack {
            List {
                if let s = model.stats {
                    Section("This PC") {
                        // iPad (regular width): the meters two to a row
                        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 24), count: width == .regular ? 2 : 1),
                                  spacing: 14) {
                            Meter(label: "CPU", value: s.system.cpu / 100, detail: String(format: "%.0f%%", s.system.cpu))
                            Meter(label: "Memory", value: s.system.memUsed / max(s.system.memTotal, 1),
                                  detail: "\(bytes(s.system.memUsed)) of \(bytes(s.system.memTotal))")
                            if let gpu = s.system.gpu {
                                Meter(label: "GPU", value: gpu.util / 100, detail: String(format: "%.0f%% · %@", gpu.util, gpu.name))
                                Meter(label: "GPU memory", value: gpu.memUsed / max(gpu.memTotal, 1),
                                      detail: "\(bytes(gpu.memUsed)) of \(bytes(gpu.memTotal))")
                            }
                        }
                        .padding(.vertical, 4)
                    }
                    Section("Terminals") {
                        let rows = s.sessions.sorted { $0.value.cpu > $1.value.cpu }
                        if rows.isEmpty { Text("Nothing running").foregroundStyle(.secondary) }
                        ForEach(rows, id: \.key) { sid, u in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(model.session(sid)?.0.title ?? sid).font(.body.weight(.medium))
                                    Spacer()
                                    Text(String(format: "%.1f%%", u.cpu)).monospacedDigit()
                                    Text(bytes(u.mem)).monospacedDigit().foregroundStyle(.secondary)
                                }
                                Text([model.session(sid)?.1.name, u.top, "\(u.procs) process\(u.procs == 1 ? "" : "es")",
                                      u.ports?.isEmpty == false ? "ports " + u.ports!.map(String.init).joined(separator: ", ") : nil]
                                    .compactMap { $0 }.joined(separator: " · "))
                                    .font(.caption).foregroundStyle(.secondary)
                                if let agent = u.agent, let used = agent.context?.used, let window = agent.context?.window, window > 0 {
                                    Meter(label: "\(agent.label) context", value: used / window,
                                          detail: "\(Int(used / 1000))k of \(Int(window / 1000))k")
                                }
                            }
                        }
                    }
                } else {
                    ProgressView()
                }
            }
            .scrollContentBackground(.hidden)
            .background(Palette.background)
            .navigationTitle("System")
        }
    }
}

struct Meter: View {
    let label: String
    let value: Double
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(label).font(.subheadline)
                Spacer()
                Text(detail).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
            ProgressView(value: min(max(value, 0), 1)).tint(value > 0.85 ? .orange : Palette.accent)
        }
    }
}
