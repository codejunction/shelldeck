import SwiftUI
import UserNotifications

/// PCs this iPhone is trusted by (switch, add, forget), alerts, and the connection.
struct SettingsView: View {
    @ObservedObject var model: AppModel
    let switchTo: (PC) -> Void
    let addPC: () -> Void
    @State private var pcs = PC.all()
    @AppStorage("alertWaiting") private var alertWaiting = true
    @AppStorage("alertDone") private var alertDone = true
    @State private var notificationsOff = false

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(pcs) { pc in
                        Button { switchTo(pc) } label: {
                            HStack {
                                Image(systemName: "desktopcomputer")
                                VStack(alignment: .leading) {
                                    Text(pc.name).foregroundStyle(.primary)
                                    Text(pc.lan.map { "Wi-Fi \(URL(string: $0)?.host() ?? $0) · share" } ?? "share")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                if pc.id == PC.current?.id { Image(systemName: "checkmark").foregroundStyle(Palette.accent) }
                            }
                        }
                        .swipeActions {
                            Button("Forget", role: .destructive) { PC.remove(pc.id); pcs = PC.all() }
                        }
                    }
                    Button(action: addPC) { Label("Add a PC", systemImage: "qrcode.viewfinder") }
                } header: { Text("Computers") } footer: {
                    Text("Each PC trusts this device once: after that, `sd share` on it is found without a QR code or approval. With iCloud Keychain on, your other iPhone or iPad (same Apple ID) gets the same PCs; each still logs in with the password.")
                }
                Section {
                    Toggle("When an agent needs you", isOn: $alertWaiting)
                    Toggle("When an agent finishes", isOn: $alertDone)
                    if notificationsOff {
                        Button("Notifications are off: open Settings") {
                            UIApplication.shared.open(URL(string: UIApplication.openSettingsURLString)!)
                        }
                    }
                } header: { Text("Alerts") } footer: {
                    Text("With sound. They ring while shelldeck is open or was just in use; a locked phone would need push, which needs a paid Apple developer account.")
                }
                Section("Connection") {
                    LabeledContent("Via", value: model.server.isTunnel ? "share (Cloudflare)" : "home Wi-Fi")
                    LabeledContent("Address", value: model.server.base.host() ?? "")
                }
            }
            .scrollContentBackground(.hidden)
            .background(Palette.background)
            .navigationTitle("Settings")
            .task {
                pcs = PC.all()
                notificationsOff = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus == .denied
            }
        }
    }
}
