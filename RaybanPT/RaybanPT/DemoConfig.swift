import Foundation

enum DemoConfig {
    static var isGlassDemoEnabled: Bool {
        let args = ProcessInfo.processInfo.arguments
        let env = ProcessInfo.processInfo.environment
        return args.contains("-glass_demo_connected") || env["GLASS_DEMO_CONNECTED"] == "1"
    }

    static var usesMaskedCaptureFrame: Bool {
        let args = ProcessInfo.processInfo.arguments
        let env = ProcessInfo.processInfo.environment
        return args.contains("-glass_demo_masked_capture") || env["GLASS_DEMO_MASKED_CAPTURE"] == "1"
    }

    /// 자동 HUD 시나리오: context → recording → stop → insight 순으로 자동 실행
    static var isHUDAutoTestEnabled: Bool {
        ProcessInfo.processInfo.arguments.contains("-glass_hud_autotest")
    }
}
