import SwiftUI
import MWDATCore

extension Notification.Name {
    static let glassExperienceLaunchRequested = Notification.Name("glassExperienceLaunchRequested")
    static let openServerSetupRequested = Notification.Name("openServerSetupRequested")
    static let glassStandbyStartRequested = Notification.Name("glassStandbyStartRequested")
    static let openCaptureHistoryRequested = Notification.Name("openCaptureHistoryRequested")
    static let glassPrimaryActionRequested = Notification.Name("glassPrimaryActionRequested")
    static let glassPatientPickerRequested = Notification.Name("glassPatientPickerRequested")
    static let glassPatientSelectedFromHUD = Notification.Name("glassPatientSelectedFromHUD")
    static let glassRecommendedAssessmentRequested = Notification.Name("glassRecommendedAssessmentRequested")
    static let glassAssessmentSelectedFromHUD = Notification.Name("glassAssessmentSelectedFromHUD")
}

private enum GlassPTPairingLink {
    static func handle(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased(),
              scheme == "kineloar" || scheme == "raybanpt" || scheme == "carelive" else { return false }
        let host = url.host?.lowercased()
        let path = url.path.lowercased()
        guard host == "glasspt" || host == "kineloar" || host == "pair" || path.contains("glasspt") || path.contains("kinelo") else {
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
        let subjectPersonId = value("subject_person_id", "person_id", "patient_person_id")
        guard !orgId.isEmpty, !providerPersonId.isEmpty else {
            return false
        }

        UserDefaults.standard.set(orgId, forKey: "glasspt_owner_org_id")
        UserDefaults.standard.set(providerPersonId, forKey: "glasspt_owner_provider_person_id")
        if !subjectPersonId.isEmpty {
            UserDefaults.standard.set(subjectPersonId, forKey: "glasspt_subject_person_id")
        }
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
              scheme == "kineloar" || scheme == "raybanpt" || scheme == "carelive" else { return false }

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
        let subjectPersonId = value("subject_person_id", "person_id", "patient_person_id")
        let physioClientId = value("physio_client_id", "client_id", "clientId")
        let physioSessionId = value("physio_session_id", "session_id", "encounter_id", "sessionId")

        if !orgId.isEmpty {
            UserDefaults.standard.set(orgId, forKey: "glasspt_owner_org_id")
        }
        if !providerPersonId.isEmpty {
            UserDefaults.standard.set(providerPersonId, forKey: "glasspt_owner_provider_person_id")
        }
        if !subjectPersonId.isEmpty {
            UserDefaults.standard.set(subjectPersonId, forKey: "glasspt_subject_person_id")
        }
        if !physioClientId.isEmpty {
            UserDefaults.standard.set(physioClientId, forKey: "glasspt_physio_client_id")
        }
        if !physioSessionId.isEmpty {
            UserDefaults.standard.set(physioSessionId, forKey: "glasspt_physio_session_id")
        }

        if !orgId.isEmpty || !providerPersonId.isEmpty || !subjectPersonId.isEmpty || !physioClientId.isEmpty {
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

private enum NeuralBandDeepLink {
    private static let supportedSchemes = ["kineloar", "raybanpt", "carelive"]
    private static let gestureToCommand: [String: String] = [
        "tap": "toggle_recording",
        "single_tap": "toggle_recording",
        "double_tap": "toggle_recording",
        "press": "toggle_recording",
        "squeeze": "toggle_recording",
        "down": "primary_action",
        "swipe_down": "primary_action",
        "downward": "primary_action",
        "select": "primary_action",
        "enter": "primary_action",
        "confirm": "primary_action",
        "open": "primary_action",
        "primary_action": "primary_action",
        "patient": "select_patient",
        "select_patient": "select_patient",
        "patient_select": "select_patient",
        "history": "open_capture_history",
        "records": "open_capture_history",
        "open_history": "open_capture_history",
        "recommend": "show_recommendations",
        "recommendations": "show_recommendations",
        "assessment": "show_recommendations",
        "evaluation": "show_recommendations",
        "show_recommendations": "show_recommendations",
        "toggle_recording": "toggle_recording",
    ]

    static func handle(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased(),
              supportedSchemes.contains(scheme),
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            return false
        }

        let host = (url.host ?? "").lowercased()
        let path = url.path.lowercased()
        let isNeuralBandLink =
            host == "neural-band"
            || host == "gesture"
            || host == "command"
            || path == "/neural-band"
            || path == "/gesture"
            || path == "/command"
            || path == "/toggle-recording"
        guard isNeuralBandLink else { return false }

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

        let gesture = value("gesture", "event", "action", "command").lowercased()
        let resolvedCommand = gestureToCommand[gesture]
            ?? (path == "/toggle-recording" ? "toggle_recording" : nil)
        guard let resolvedCommand,
              ["toggle_recording", "primary_action", "select_patient", "open_capture_history", "show_recommendations"].contains(resolvedCommand) else {
            return false
        }

        let orgId = value("owner_org_id", "org_id")
        let providerPersonId = value("owner_provider_person_id", "provider_person_id")
        let subjectPersonId = value("subject_person_id", "person_id", "patient_person_id")
        let physioClientId = value("physio_client_id", "client_id", "clientId")
        let physioSessionId = value("physio_session_id", "session_id", "encounter_id", "sessionId")
        let patientName = value("patient_name", "patient", "patientName", "client_name", "clientName")
        let sessionLabel = value("session_type", "session", "sessionName", "session_name", "program")
        let deviceId = value("device_id", "device", "band_id")

        if !orgId.isEmpty {
            UserDefaults.standard.set(orgId, forKey: "glasspt_owner_org_id")
        }
        if !providerPersonId.isEmpty {
            UserDefaults.standard.set(providerPersonId, forKey: "glasspt_owner_provider_person_id")
        }
        if !subjectPersonId.isEmpty {
            UserDefaults.standard.set(subjectPersonId, forKey: "glasspt_subject_person_id")
        }
        if !physioClientId.isEmpty {
            UserDefaults.standard.set(physioClientId, forKey: "glasspt_physio_client_id")
        }
        if !physioSessionId.isEmpty {
            UserDefaults.standard.set(physioSessionId, forKey: "glasspt_physio_session_id")
        }
        if !orgId.isEmpty || !providerPersonId.isEmpty || !subjectPersonId.isEmpty || !physioClientId.isEmpty {
            NotificationCenter.default.post(
                name: Notification.Name("bridgeSettingsDidChange"),
                object: nil,
                userInfo: ["source": "neural_band_deeplink"]
            )
        }

        if !patientName.isEmpty || !sessionLabel.isEmpty || !subjectPersonId.isEmpty || !physioClientId.isEmpty || !physioSessionId.isEmpty {
            GlassExperienceCoordinator.shared.requestLaunch(url: url.absoluteString)
            NotificationCenter.default.post(
                name: .glassExperienceLaunchRequested,
                object: nil,
                userInfo: ["url": url.absoluteString, "source": "neural_band_deeplink"]
            )
        }

        let banner = makeBannerMessage(
            gesture: gesture.isEmpty ? "toggle_recording" : gesture,
            deviceId: deviceId,
            patientName: patientName.isEmpty ? nil : patientName
        )
        GlassExperienceCoordinator.shared.showTransientBanner(banner)
        let userInfo = [
            "source": GlassRecordToggleSource.neuralBandDeepLink.rawValue,
            "gesture": gesture.isEmpty ? "toggle_recording" : gesture,
            "device_id": deviceId,
        ]
        let notificationName: Notification.Name
        switch resolvedCommand {
        case "primary_action":
            notificationName = .glassPrimaryActionRequested
        case "select_patient":
            notificationName = .glassPatientPickerRequested
        case "open_capture_history":
            notificationName = .openCaptureHistoryRequested
        case "show_recommendations":
            notificationName = .glassRecommendedAssessmentRequested
        default:
            notificationName = .glassCaptouchRecordToggle
        }
        NotificationCenter.default.post(name: notificationName, object: nil, userInfo: userInfo)
        return true
    }

    private static func makeBannerMessage(gesture: String, deviceId: String, patientName: String?) -> String {
        let prettyGesture = gesture
            .replacingOccurrences(of: "_", with: " ")
            .split(separator: " ")
            .map { $0.capitalized }
            .joined(separator: " ")
        var parts = ["Neural Band \(prettyGesture)"]
        if !deviceId.isEmpty {
            parts.append(deviceId)
        }
        if let patientName, !patientName.isEmpty {
            parts.append(patientName)
        }
        return parts.joined(separator: " · ")
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
                    if NeuralBandDeepLink.handle(url) {
                        return
                    }
                    #if DEBUG
                    if let scheme = url.scheme?.lowercased(),
                       (scheme == "kineloar" || scheme == "raybanpt" || scheme == "carelive"),
                       url.host?.lowercased() == "debug" {
                        let action = url.path.lowercased()
                        if action == "/toggle-recording" {
                            NotificationCenter.default.post(
                                name: .glassCaptouchRecordToggle,
                                object: nil,
                                userInfo: ["source": GlassRecordToggleSource.debugDeepLink.rawValue]
                            )
                            return
                        }
                        if action == "/show-insight" {
                            Task {
                                await GlassHUDManager.shared.showSuccess(
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
