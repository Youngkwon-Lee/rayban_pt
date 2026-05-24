import Foundation
import Observation

struct GlassExperienceLaunchContext: Equatable {
    let patientName: String?
    let sessionLabel: String?
    let physioClientId: String?
    let physioSessionId: String?
}

@Observable
@MainActor
final class GlassExperienceCoordinator {
    struct PendingLaunch {
        let token: UUID
        let context: GlassExperienceLaunchContext?
    }

    static let shared = GlassExperienceCoordinator()

    private(set) var pendingLaunchToken: UUID? = nil
    private(set) var pendingLaunchContext: GlassExperienceLaunchContext? = nil
    private(set) var lastLaunchURL: String? = nil
    private(set) var bannerMessage: String? = nil
    private(set) var isGuidedModeActive = false
    private(set) var activeLaunchContext: GlassExperienceLaunchContext? = nil

    private var bannerTask: Task<Void, Never>?

    private init() {}

    func requestLaunch(url: String) {
        lastLaunchURL = url
        pendingLaunchToken = UUID()
        let context = Self.parseContext(from: url)
        pendingLaunchContext = context
        activeLaunchContext = context
        isGuidedModeActive = true
        bannerMessage = Self.makeBannerMessage(context: context)

        bannerTask?.cancel()
        bannerTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 5_000_000_000)
            guard let self, !Task.isCancelled else { return }
            self.bannerMessage = nil
        }
    }

    func consumePendingLaunch() -> PendingLaunch? {
        guard let token = pendingLaunchToken else { return nil }
        let launch = PendingLaunch(token: token, context: pendingLaunchContext)
        pendingLaunchToken = nil
        pendingLaunchContext = nil
        return launch
    }

    func endGuidedMode() {
        isGuidedModeActive = false
        activeLaunchContext = nil
        pendingLaunchToken = nil
        pendingLaunchContext = nil
        bannerTask?.cancel()
        bannerMessage = nil
    }

    private static func parseContext(from urlString: String) -> GlassExperienceLaunchContext {
        guard let url = URL(string: urlString),
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            return GlassExperienceLaunchContext(
                patientName: nil,
                sessionLabel: nil,
                physioClientId: nil,
                physioSessionId: nil
            )
        }

        let items = components.queryItems ?? []

        func value(_ names: String...) -> String? {
            for name in names {
                if let raw = items.first(where: { $0.name.caseInsensitiveCompare(name) == .orderedSame })?.value?
                    .trimmingCharacters(in: .whitespacesAndNewlines),
                   !raw.isEmpty {
                    return raw
                }
            }
            return nil
        }

        return GlassExperienceLaunchContext(
            patientName: value("patient_name", "patient", "patientName", "client_name", "clientName"),
            sessionLabel: value("session_type", "session", "sessionName", "session_name", "program"),
            physioClientId: value("physio_client_id", "client_id", "clientId"),
            physioSessionId: value("physio_session_id", "session_id", "encounter_id", "sessionId")
        )
    }

    private static func makeBannerMessage(context: GlassExperienceLaunchContext) -> String {
        if let patientName = context.patientName, let sessionLabel = context.sessionLabel {
            return "\(patientName) · \(sessionLabel) 세션 시작"
        }
        if let patientName = context.patientName {
            return "\(patientName) 환자 세션 시작"
        }
        if let sessionLabel = context.sessionLabel {
            return "\(sessionLabel) 세션 시작"
        }
        return "Care Live에서 세션 시작"
    }
}
