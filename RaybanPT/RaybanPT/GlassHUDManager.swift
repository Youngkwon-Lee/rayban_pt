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
    private var commandPollTask: Task<Void, Never>?

    private var bridgeClient: BridgeClient? {
        let stored = UserDefaults.standard.string(forKey: "bridge_base_url")?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !stored.isEmpty, let url = URL(string: stored) else { return nil }
        return BridgeClient(baseURL: url)
    }

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
                        self.startCommandPolling()
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
        startCommandPolling()
        await pushHUD()
    }

    func detachDisplay() async {
        elapsedTask?.cancel()
        elapsedTask = nil
        insightTask?.cancel()
        insightTask = nil
        commandPollTask?.cancel()
        commandPollTask = nil
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
        await pushHUD()
    }

    func showStandby(patient: String?) async {
        activePatient = patient
        hudMode = .off
        await pushHUD()
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
        } else if let display, isDisplayConnected {
            try? await display.send(buildView())
        }
        await bridgePushState()
    }

    private func clearHUD() async {
        if isSimulated {
            demoHUDSummary = nil
            return
        }
        guard let display else { return }
        try? await display.send(FlexBox(direction: .column) {})
    }

    private func bridgePushState() async {
        guard let client = bridgeClient else { return }
        var isRec = false
        if case .recording = hudMode { isRec = true }
        var insight: BridgeClient.GlassInsight? = nil
        if case .insight(let t, let b, _) = hudMode {
            insight = BridgeClient.GlassInsight(id: t + b, title: t, body: b)
        }
        await client.pushGlassState(
            patient: activePatient,
            isRecording: isRec,
            recordingStart: recordingStart,
            sessionCount: sessionCount,
            lastInsight: insight
        )
    }

    private func startCommandPolling() {
        commandPollTask?.cancel()
        commandPollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                guard let self, !Task.isCancelled else { break }
                guard let client = self.bridgeClient else { continue }
                if let cmd = await client.pollGlassCommand(), cmd == "toggle_recording" {
                    NotificationCenter.default.post(
                        name: .glassCaptouchRecordToggle,
                        object: nil
                    )
                }
            }
        }
    }

    private func buildView() -> FlexBox {
        switch hudMode {
        case .off:
            return buildStandbyView()
        case .context:
            return buildContextView()
        case .recording:
            return buildRecordingView()
        case .insight(let title, let body, _):
            return buildInsightView(title: title, body: body)
        }
    }

    private func buildStandbyView() -> FlexBox {
        FlexBox(direction: .column, spacing: 8) {
            buildHeaderCard(
                title: "Care Live",
                subtitle: "세션 준비 완료"
            )
            if let activePatient, !activePatient.isEmpty {
                buildPatientCard(
                    patient: activePatient,
                    detail: "선택 환자"
                )
            }
            buildInfoCard(
                title: "라이브 시작 준비",
                body: "iPhone에서 라이브를 시작하거나 안경 버튼으로 바로 진행하세요."
            )
        }
    }

    // Patient name + session counter + REC start button
    private func buildContextView() -> FlexBox {
        let patient = activePatient ?? "환자 미선택"
        let sessionLine = sessionCount > 0 ? "세션 \(sessionCount)회 완료" : "첫 녹화 대기"
        return FlexBox(direction: .column, spacing: 8) {
            buildHeaderCard(
                title: "Care Live Session",
                subtitle: "라이브 연결됨"
            )
            buildPatientCard(
                patient: patient,
                detail: sessionLine
            )
            buildInfoCard(
                title: "핸즈프리 기록",
                body: "관찰이 시작되면 안경에서 바로 녹화를 시작할 수 있습니다."
            )
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
            buildHeaderCard(
                title: "REC \(elapsed)",
                subtitle: "실시간 기록 중"
            )
            if let patient {
                buildPatientCard(
                    patient: patient,
                    detail: "실시간 기록 수집 중"
                )
            }
            buildInfoCard(
                title: "기록 진행 중",
                body: "중지하면 저장과 분석으로 바로 이어집니다."
            )
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
            buildHeaderCard(
                title: "Care Live Insight",
                subtitle: "분석 결과 도착"
            )
            FlexBox(direction: .row, spacing: 8, crossAlignment: .center) {
                Icon(name: .lightBulb)
                Text(title, style: .body)
            }
            .padding(16)
            .background(.card)
            buildInfoCard(
                title: "요약",
                body: body
            )
        }
    }

    private func buildHeaderCard(title: String, subtitle: String) -> FlexBox {
        FlexBox(direction: .column, spacing: 4) {
            Text(title, style: .body)
            Text(subtitle, style: .meta, color: .secondary)
        }
        .padding(16)
        .background(.card)
    }

    private func buildPatientCard(patient: String, detail: String) -> FlexBox {
        FlexBox(direction: .column, spacing: 6) {
            FlexBox(direction: .row, spacing: 8, crossAlignment: .center) {
                Icon(name: .person)
                Text(patient, style: .body)
            }
            Text(detail, style: .meta, color: .secondary)
        }
        .padding(16)
        .background(.card)
    }

    private func buildInfoCard(title: String, body: String) -> FlexBox {
        FlexBox(direction: .column, spacing: 4) {
            Text(title, style: .body)
            Text(body, style: .meta, color: .secondary)
        }
        .padding(16)
    }

    private func buildDemoSummary() -> String {
        switch hudMode {
        case .off:
            let patient = activePatient.map { " · \($0)" } ?? ""
            return "🟦 준비 완료\(patient)"
        case .context:
            let patient = activePatient ?? "환자 미선택"
            let status = sessionCount > 0 ? "세션 \(sessionCount)회 완료" : "첫 녹화 대기"
            return "🟢 라이브  \(patient) · \(status)"
        case .recording:
            let elapsed = elapsedString()
            let suffix = activePatient.map { " · \($0)" } ?? ""
            return "🔴 REC \(elapsed) · 세션 \(max(sessionCount, 1))\(suffix)"
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
