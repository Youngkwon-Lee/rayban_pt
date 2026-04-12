import SwiftUI
import MWDATCore

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
                    Task {
                        do {
                            try await Wearables.shared.handleUrl(url)
                        } catch {
                            print("[MWDAT] handleUrl 실패: \(error)")
                        }
                    }
                }
        }
    }
}
