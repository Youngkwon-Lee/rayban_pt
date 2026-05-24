import SwiftUI
import MWDATCore

extension Notification.Name {
    static let glassExperienceLaunchRequested = Notification.Name("glassExperienceLaunchRequested")
}

private enum GlassPTPairingLink {
    static func handle(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased(),
              scheme == "raybanpt" || scheme == "carelive" else { return false }
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

private enum GlassPTDirectLaunchLink {
    static func handle(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased(),
              scheme == "raybanpt" || scheme == "carelive" else { return false }

        let host = (url.host ?? "").lowercased()
        let path = url.path.lowercased()
        let isLaunchLink = host == "launch" || host == "session" || path == "/launch" || path == "/start"
        guard isLaunchLink,
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            return false
        }

        let items = components.queryItems ?? []

        func value(_ names: String...) -> String {
            for name in names {
                if let raw = items.first(where: { $0.name.caseInsensitiveCompare(name) == .orderedSame })?.value?
                    .trimmingCharacters(in: .whitespacesAndNewlines),
                   !raw.isEmpty {
                    return raw
                }
            }
            return ""
        }

        let orgId = value("owner_org_id", "org_id")
        let providerPersonId = value("owner_provider_person_id", "provider_person_id")
        let physioClientId = value("physio_client_id", "client_id", "clientId")
        let physioSessionId = value("physio_session_id", "session_id", "encounter_id", "sessionId")

        if !orgId.isEmpty {
            UserDefaults.standard.set(orgId, forKey: "glasspt_owner_org_id")
        }
        if !providerPersonId.isEmpty {
            UserDefaults.standard.set(providerPersonId, forKey: "glasspt_owner_provider_person_id")
        }
        if !physioClientId.isEmpty {
            UserDefaults.standard.set(physioClientId, forKey: "glasspt_physio_client_id")
        }
        if !physioSessionId.isEmpty {
            UserDefaults.standard.set(physioSessionId, forKey: "glasspt_physio_session_id")
        }

        if !orgId.isEmpty || !providerPersonId.isEmpty {
            NotificationCenter.default.post(
                name: Notification.Name("bridgeSettingsDidChange"),
                object: nil,
                userInfo: ["source": "glasspt_direct_launch_link"]
            )
        }

        GlassExperienceCoordinator.shared.requestLaunch(url: url.absoluteString)
        NotificationCenter.default.post(
            name: .glassExperienceLaunchRequested,
            object: nil,
            userInfo: ["url": url.absoluteString, "source": "glasspt_direct_launch_link"]
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
                    if GlassPTDirectLaunchLink.handle(url) {
                        return
                    }
                    #if DEBUG
                    if let scheme = url.scheme?.lowercased(),
                       (scheme == "raybanpt" || scheme == "carelive"),
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
                        print("[MWDAT] onOpenURL 수신: \(url.absoluteString)")
                        do {
                            _ = try await Wearables.shared.handleUrl(url)
                            GlassExperienceCoordinator.shared.requestLaunch(url: url.absoluteString)
                            print("[MWDAT] handleUrl 성공 → glassExperienceLaunchRequested 발송")
                            NotificationCenter.default.post(
                                name: .glassExperienceLaunchRequested,
                                object: nil,
                                userInfo: ["url": url.absoluteString]
                            )
                        } catch {
                            print("[MWDAT] handleUrl 실패: \(error)")
                        }
                    }
                }
        }
    }
}
