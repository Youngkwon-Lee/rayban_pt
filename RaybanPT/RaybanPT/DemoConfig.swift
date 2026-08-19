import Foundation

enum DemoConfig {
    /// DAT MockDevice exercises the real Wearables/DeviceSession/Camera path
    /// on the Simulator. It is debug-only and never enabled on a physical
    /// device, so it cannot be mistaken for Ray-Ban hardware evidence.
    static var isDATMockEnabled: Bool {
#if DEBUG
#if targetEnvironment(simulator)
        let args = ProcessInfo.processInfo.arguments
        let env = ProcessInfo.processInfo.environment
        return args.contains("-rayban_dat_mock") || env["RAYBAN_DAT_MOCK"] == "1"
#else
        return false
#endif
#else
        return false
#endif
    }

    /// UI tests can opt into a deterministic local subject without changing
    /// the physical-device or production patient-selection flow.
    static var isDATMockPatientBootstrapEnabled: Bool {
#if DEBUG
#if targetEnvironment(simulator)
        return isDATMockEnabled
            && ProcessInfo.processInfo.arguments.contains("-rayban_dat_mock_qa_patient")
#else
        return false
#endif
#else
        return false
#endif
    }

    static var isGlassDemoEnabled: Bool {
#if targetEnvironment(simulator)
        if isDATMockEnabled { return false }
        // The DAT singleton is not available in the iOS Simulator. Keep the
        // camera surface and UI tests on the same simulated frame path unless
        // a physical device is running the app.
        return true
#else
        let args = ProcessInfo.processInfo.arguments
        let env = ProcessInfo.processInfo.environment
        return args.contains("-glass_demo_connected") || env["GLASS_DEMO_CONNECTED"] == "1"
#endif
    }

    static var usesMaskedCaptureFrame: Bool {
        let args = ProcessInfo.processInfo.arguments
        let env = ProcessInfo.processInfo.environment
        return args.contains("-glass_demo_masked_capture") || env["GLASS_DEMO_MASKED_CAPTURE"] == "1"
    }

    /// 자동 HUD 시나리오: context → recording → stop → insight 순으로 자동 실행
    static var isHUDAutoTestEnabled: Bool {
        let args = ProcessInfo.processInfo.arguments
        let env = ProcessInfo.processInfo.environment
        return args.contains("-glass_hud_autotest")
            || args.contains("-glass_media_upload_autotest")
            || args.contains("-session_auto_capture_autotest")
            || env["GLASS_HUD_AUTOTEST"] == "1"
            || env["GLASS_MEDIA_UPLOAD_AUTOTEST"] == "1"
            || env["SESSION_AUTO_CAPTURE_AUTOTEST"] == "1"
    }

    /// Demo MP4를 consent가 확인된 bridge에 업로드하는 명시적 E2E 테스트
    static var isMediaUploadAutoTestEnabled: Bool {
        ProcessInfo.processInfo.arguments.contains("-glass_media_upload_autotest")
            || ProcessInfo.processInfo.environment["GLASS_MEDIA_UPLOAD_AUTOTEST"] == "1"
    }

    /// 세션 자동기록의 영상 lifecycle만 합성 프레임으로 검증하는 명시적 테스트
    static var isSessionAutoCaptureAutoTestEnabled: Bool {
        ProcessInfo.processInfo.arguments.contains("-session_auto_capture_autotest")
            || ProcessInfo.processInfo.environment["SESSION_AUTO_CAPTURE_AUTOTEST"] == "1"
    }
}
