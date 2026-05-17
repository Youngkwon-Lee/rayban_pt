import Foundation
import Observation
import MWDATCore
import MWDATDisplay

extension Notification.Name {
    static let glassCaptouchRecordToggle = Notification.Name("glassCaptouchRecordToggle")
}

// Manages the Ray-Ban Display HUD — context overlay, recording status, and AI insights.
// Display capability is attached to the DeviceSession provided by StreamViewModel
// so camera and display share one session (SDK 1:1 constraint).
@Observable
@MainActor
final class GlassHUDManager {
    static let shared = GlassHUDManager()

    private(set) var isDisplayConnected = false
    /// Non-nil in demo mode — mirrors what would be shown on the real glass display.
    private(set) var demoHUDSummary: String? = nil

    private var display: Display?
    private var isSimulated = false
    private var stateListenerToken: AnyListenerToken?
    private var displayStateContinuation: AsyncStream<DisplayState>.Continuation?
    private var displayStateTask: Task<Void, Never>?
    private var elapsedTask: Task<Void, Never>?
    private var insightTask: Task<Void, Never>?

    private enum HUDMode {
        case off
        case context
        case recording
        indirect case insight(title: String, body: String, returnTo: HUDMode)
    }

    private var hudMode: HUDMode = .off
    private var activePatient: String? = nil
    private var sessionCount = 0
    private var recordingStart: Date? = nil

    private init() {}

    // MARK: - Display lifecycle (called by StreamViewModel)

    func attachDisplay(to session: DeviceSession) async {
        guard display == nil else { return }
        do {
            let capability = try session.addDisplay()
            let (stream, continuation) = AsyncStream.makeStream(of: DisplayState.self)
            displayStateContinuation = continuation
            stateListenerToken = capability.statePublisher.listen { state in
                continuation.yield(state)
            }
            displayStateTask = Task { [weak self] in
                for await state in stream {
                    guard let self, !Task.isCancelled else { return }
                    switch state {
                    case .started:
                        self.isDisplayConnected = true
                        await self.pushHUD()
                    case .stopping, .stopped:
                        self.isDisplayConnected = false
                        self.stateListenerToken = nil
                        self.displayStateContinuation?.finish()
                        self.displayStateContinuation = nil
                        self.display = nil
                    default:
                        break
                    }
                }
            }
            await capability.start()
            display = capability
        } catch {
            print("[GlassHUD] attachDisplay failed: \(error)")
        }
    }

    /// Demo mode — treats as connected without a real DeviceSession.
    func attachSimulatedDisplay() async {
        isSimulated = true
        isDisplayConnected = true
        await pushHUD()
    }

    func detachDisplay() async {
        elapsedTask?.cancel()
        elapsedTask = nil
        insightTask?.cancel()
        insightTask = nil
        stateListenerToken = nil
        displayStateContinuation?.finish()
        displayStateContinuation = nil
        displayStateTask?.cancel()
        displayStateTask = nil
        await display?.stop()
        display = nil
        isSimulated = false
        isDisplayConnected = false
        demoHUDSummary = nil
        hudMode = .off
        activePatient = nil
        sessionCount = 0
        recordingStart = nil
    }

    // MARK: - Context HUD (shown while streaming, not recording)

    func startContext(patient: String?) async {
        activePatient = patient
        sessionCount = 0
        hudMode = .context
        await pushHUD()
    }

    func updateContextPatient(_ patient: String?) async {
        activePatient = patient
        if case .context = hudMode {
            await pushHUD()
        }
    }

    func stopContext() async {
        hudMode = .off
        activePatient = nil
        sessionCount = 0
        await clearHUD()
    }

    // MARK: - Recording HUD

    func startRecording(patient: String?) async {
        insightTask?.cancel()
        insightTask = nil
        activePatient = patient
        sessionCount += 1
        recordingStart = Date()
        hudMode = .recording
        await pushHUD()
        startElapsedTimer()
    }

    func stopRecording() async {
        elapsedTask?.cancel()
        elapsedTask = nil
        insightTask?.cancel()
        insightTask = nil
        recordingStart = nil
        hudMode = .context
        await pushHUD()
    }

    // MARK: - AI Insight HUD (auto-dismisses after 8 s)

    func showInsight(title: String, body: String) async {
        insightTask?.cancel()
        let previousMode = hudMode
        hudMode = .insight(title: title, body: body, returnTo: previousMode)
        await pushHUD()
        insightTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 8_000_000_000)
            guard let self, !Task.isCancelled else { return }
            self.hudMode = previousMode
            await self.pushHUD()
        }
    }

    // MARK: - Private

    private func startElapsedTimer() {
        elapsedTask?.cancel()
        elapsedTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                guard let self, !Task.isCancelled else { break }
                guard case .recording = self.hudMode else { break }
                await self.pushHUD()
            }
        }
    }

    private func pushHUD() async {
        if isSimulated {
            demoHUDSummary = buildDemoSummary()
            return
        }
        guard let display, isDisplayConnected else { return }
        try? await display.send(buildView())
    }

    private func clearHUD() async {
        if isSimulated {
            demoHUDSummary = nil
            return
        }
        guard let display else { return }
        try? await display.send(FlexBox(direction: .column) {})
    }

    private func buildView() -> FlexBox {
        switch hudMode {
        case .off:
            return FlexBox(direction: .column) {}
        case .context:
            return buildContextView()
        case .recording:
            return buildRecordingView()
        case .insight(let title, let body, _):
            return buildInsightView(title: title, body: body)
        }
    }

    // Patient name + session counter + REC start button
    private func buildContextView() -> FlexBox {
        let patient = activePatient ?? "환자 미선택"
        let sessionLine = sessionCount > 0 ? "세션 \(sessionCount)회 완료" : "녹화 대기"
        return FlexBox(direction: .column, spacing: 8) {
            FlexBox(direction: .row, spacing: 8, crossAlignment: .center) {
                Icon(name: .person)
                Text(patient, style: .body)
            }
            .padding(16)
            .background(.card)
            FlexBox(direction: .column) {
                Text(sessionLine, style: .meta, color: .secondary)
            }
            .padding(16)
            Button(label: "녹화 시작", style: .primary, iconName: .videoCamera, onClick: {
                Task { @MainActor in
                    NotificationCenter.default.post(
                        name: .glassCaptouchRecordToggle,
                        object: nil
                    )
                }
            })
        }
    }

    // REC timer + session info + STOP button
    private func buildRecordingView() -> FlexBox {
        let elapsed = elapsedString()
        let patient = activePatient
        return FlexBox(direction: .column, spacing: 8) {
            FlexBox(direction: .row, spacing: 8, crossAlignment: .center) {
                Text("● REC  \(elapsed)", style: .body)
            }
            .padding(16)
            .background(.card)
            if let patient {
                FlexBox(direction: .column) {
                    Text("세션 \(sessionCount) · \(patient)", style: .meta, color: .secondary)
                }
                .padding(16)
            }
            Button(label: "녹화 중지", style: .secondary, iconName: .x, onClick: {
                Task { @MainActor in
                    NotificationCenter.default.post(
                        name: .glassCaptouchRecordToggle,
                        object: nil
                    )
                }
            })
        }
    }

    // AI chart summary — shown for 8 seconds then returns to previous mode
    private func buildInsightView(title: String, body: String) -> FlexBox {
        return FlexBox(direction: .column, spacing: 8) {
            FlexBox(direction: .row, spacing: 8, crossAlignment: .center) {
                Icon(name: .lightBulb)
                Text(title, style: .body)
            }
            .padding(16)
            .background(.card)
            FlexBox(direction: .column) {
                Text(body, style: .meta, color: .secondary)
            }
            .padding(16)
        }
    }

    private func buildDemoSummary() -> String {
        switch hudMode {
        case .off:
            return "HUD: 꺼짐"
        case .context:
            let patient = activePatient ?? "환자 미선택"
            let status = sessionCount > 0 ? "세션 \(sessionCount)회 완료" : "녹화 대기"
            return "👤 \(patient)  ·  \(status)  [REC↗]"
        case .recording:
            let elapsed = elapsedString()
            let suffix = activePatient.map { " · \($0)" } ?? ""
            return "🔴 REC \(elapsed)  세션 \(sessionCount)\(suffix)  [■]"
        case .insight(let title, let body, _):
            return "💡 \(title)  ·  \(body)"
        }
    }

    private func elapsedString() -> String {
        guard let start = recordingStart else { return "00:00" }
        let secs = Int(Date().timeIntervalSince(start))
        return String(format: "%02d:%02d", secs / 60, secs % 60)
    }
}
