import SwiftUI
import MWDATCore

private enum GlassPTPairingLink {
    static func handle(_ url: URL) -> Bool {
        guard url.scheme?.lowercased() == "raybanpt" else { return false }
        let host = url.host?.lowercased()
        let path = url.path.lowercased()
        guard host == "glasspt" || host == "pair" || path.contains("glasspt") else {
            return false
        }

        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            return false
        }
        let items = components.queryItems ?? []

        func value(_ names: String...) -> String {
            for name in names {
                if let raw = items.first(where: { $0.name == name })?.value?
                    .trimmingCharacters(in: .whitespacesAndNewlines),
                   !raw.isEmpty {
                    return raw
                }
            }
            return ""
        }

        let orgId = value("owner_org_id", "org_id")
        let providerPersonId = value("owner_provider_person_id", "provider_person_id")
        guard !orgId.isEmpty, !providerPersonId.isEmpty else {
            return false
        }

        UserDefaults.standard.set(orgId, forKey: "glasspt_owner_org_id")
        UserDefaults.standard.set(providerPersonId, forKey: "glasspt_owner_provider_person_id")
        NotificationCenter.default.post(
            name: Notification.Name("bridgeSettingsDidChange"),
            object: nil,
            userInfo: ["source": "glasspt_pairing_link"]
        )
        return true
    }
}

@main
struct RaybanPTApp: App {
    private let deviceManager: DeviceSessionManager

    init() {
        // Wearables.shared 접근 전에 반드시 configure() 먼저 호출
        do {
            try Wearables.configure()
        } catch {
            print("[MWDAT] configure 실패: \(error)")
        }
        deviceManager = DeviceSessionManager.shared
    }

    var body: some Scene {
        WindowGroup {
            M2_TestView()
                .environment(deviceManager)
                .onAppear {
                    deviceManager.start()
                }
                .onOpenURL { url in
                    if GlassPTPairingLink.handle(url) {
                        return
                    }
                    #if DEBUG
                    if url.scheme?.lowercased() == "raybanpt",
                       url.host?.lowercased() == "debug" {
                        let action = url.path.lowercased()
                        if action == "/toggle-recording" {
                            NotificationCenter.default.post(
                                name: .glassCaptouchRecordToggle, object: nil)
                            return
                        }
                        if action == "/show-insight" {
                            Task {
                                await GlassHUDManager.shared.showInsight(
                                    title: "차트 생성됨", body: "환자: 테스트 김철수")
                            }
                            return
                        }
                    }
                    #endif
                    Task {
                        do {
                            _ = try await Wearables.shared.handleUrl(url)
                        } catch {
                            print("[MWDAT] handleUrl 실패: \(error)")
                        }
                    }
                }
        }
    }
}
