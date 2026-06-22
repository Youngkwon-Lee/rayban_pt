import Foundation
import Observation
import MWDATCore
import MWDATDisplay

extension Notification.Name {
    static let glassCaptouchRecordToggle = Notification.Name("glassCaptouchRecordToggle")
}

enum GlassRecordToggleSource: String {
    case glassDisplayButton = "glass_display_button"
    case bridgeCommand = "bridge_command"
    case neuralBandDeepLink = "neural_band_deeplink"
    case debugDeepLink = "debug_deeplink"
    case unknown = "unknown"

    init(notification: Notification) {
        let raw = (notification.userInfo?["source"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        self = GlassRecordToggleSource(rawValue: raw) ?? .unknown
    }

    var isHandsFreeGlassPath: Bool {
        switch self {
        case .glassDisplayButton, .bridgeCommand, .neuralBandDeepLink:
            return true
        case .debugDeepLink, .unknown:
            return false
        }
    }

    var toastLabel: String {
        switch self {
        case .glassDisplayButton:
            return "안경 HUD"
        case .bridgeCommand:
            return "브리지 명령"
        case .neuralBandDeepLink:
            return "Neural Band"
        case .debugDeepLink:
            return "디버그"
        case .unknown:
            return "안경 입력"
        }
    }
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
        case standby
        case ready
        case recording
        case uploading
        case analyzing
        case patientSelect
        case captureHistory
        case recommendations
        indirect case result(kind: HUDResultKind, title: String, body: String, returnTo: HUDMode)
    }

    private enum HUDResultKind {
        case success
        case error
    }

    private var hudMode: HUDMode = .standby
    private var activePatient: String? = nil
    private var patientCandidates: [String] = []
    private var captureHistorySummaries: [String] = []
    private var selectedAssessment: String? = nil
    private var sessionCount = 0
    private var recordingStart: Date? = nil

    private init() {}

    // MARK: - Display lifecycle (called by StreamViewModel)

    func attachDisplay(to session: DeviceSession) async {
        if display != nil, isDisplayConnected {
            return
        }

        if display != nil, !isDisplayConnected {
            await resetDisplayTransport()
        }

        guard display == nil else { return }
        do {
            let capability = try session.addDisplay()
            display = capability
            isSimulated = false
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
            await pushHUD()
        } catch {
            print("[GlassHUD] attachDisplay failed: \(error)")
            await resetDisplayTransport()
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
        await resetDisplayTransport()
        isSimulated = false
        demoHUDSummary = nil
        hudMode = .standby
        activePatient = nil
        sessionCount = 0
        recordingStart = nil
    }

    private func resetDisplayTransport() async {
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
        isDisplayConnected = false
    }

    // MARK: - Context HUD (shown while streaming, not recording)

    func startContext(patient: String?) async {
        activePatient = patient
        sessionCount = 0
        hudMode = .ready
        await pushHUD()
    }

    func updateContextPatient(_ patient: String?) async {
        activePatient = patient
        if case .ready = hudMode {
            await pushHUD()
        }
    }

    func stopContext() async {
        hudMode = .standby
        activePatient = nil
        sessionCount = 0
        await pushHUD()
    }

    func showStandby(patient: String?) async {
        activePatient = patient
        patientCandidates = []
        hudMode = .standby
        await pushHUD()
    }

    func showPatientSelection(current patient: String?, candidates: [String]) async {
        activePatient = patient
        patientCandidates = Array(
            candidates
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
                .prefix(3)
        )
        hudMode = .patientSelect
        await pushHUD()
    }

    func showCaptureHistory(patient: String?, summaries: [String]) async {
        insightTask?.cancel()
        activePatient = patient
        captureHistorySummaries = Array(
            summaries
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
                .prefix(3)
        )
        hudMode = .captureHistory
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
        hudMode = .ready
        await pushHUD()
    }

    // MARK: - Processing HUD

    func showUploading(patient: String?) async {
        insightTask?.cancel()
        activePatient = patient
        hudMode = .uploading
        await pushHUD()
    }

    func showAnalyzing(patient: String?) async {
        insightTask?.cancel()
        activePatient = patient
        hudMode = .analyzing
        await pushHUD()
    }

    func showRecommendations(patient: String?) async {
        insightTask?.cancel()
        activePatient = patient
        hudMode = .recommendations
        await pushHUD()
    }

    func selectAssessment(_ title: String, patient: String?) async {
        selectedAssessment = title.trimmingCharacters(in: .whitespacesAndNewlines)
        activePatient = patient
        hudMode = .recommendations
        await pushHUD()
    }

    // MARK: - Result HUD

    func showSuccess(title: String, body: String) async {
        insightTask?.cancel()
        let returnMode = resolvedReturnModeAfterResult()
        hudMode = .result(kind: .success, title: title, body: body, returnTo: returnMode)
        await pushHUD()
        scheduleReturn(to: returnMode, afterNanoseconds: 2_200_000_000)
    }

    func showError(title: String, body: String) async {
        insightTask?.cancel()
        let returnMode = resolvedReturnModeAfterResult()
        hudMode = .result(kind: .error, title: title, body: body, returnTo: returnMode)
        await pushHUD()
        scheduleReturn(to: returnMode, afterNanoseconds: 8_000_000_000)
    }

    private func scheduleReturn(to returnMode: HUDMode, afterNanoseconds delay: UInt64) {
        insightTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: delay)
            guard let self, !Task.isCancelled else { return }
            self.hudMode = returnMode
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

    private func resolvedReturnModeAfterResult() -> HUDMode {
        activePatient == nil ? .standby : .ready
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
        if case .result(_, let t, let b, _) = hudMode {
            insight = BridgeClient.GlassInsight(id: t + b, title: t, body: b)
        }
        let summary = bridgeModeSummary()
        await client.pushGlassState(
            patient: activePatient,
            mode: summary.mode,
            message: summary.message,
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
                if let cmd = await client.pollGlassCommand() {
                    postGlassCommandNotification(cmd)
                }
            }
        }
    }

    private func postGlassCommandNotification(_ command: String) {
        let userInfo = ["source": GlassRecordToggleSource.bridgeCommand.rawValue]
        switch command {
        case "toggle_recording":
            NotificationCenter.default.post(
                name: .glassCaptouchRecordToggle,
                object: nil,
                userInfo: userInfo
            )
        case "start_live":
            NotificationCenter.default.post(
                name: .glassStandbyStartRequested,
                object: nil,
                userInfo: userInfo
            )
        case "open_capture_history":
            NotificationCenter.default.post(
                name: .openCaptureHistoryRequested,
                object: nil,
                userInfo: userInfo
            )
        case "primary_action":
            NotificationCenter.default.post(
                name: .glassPrimaryActionRequested,
                object: nil,
                userInfo: userInfo
            )
        case "select_patient":
            NotificationCenter.default.post(
                name: .glassPatientPickerRequested,
                object: nil,
                userInfo: userInfo
            )
        case "show_recommendations":
            NotificationCenter.default.post(
                name: .glassRecommendedAssessmentRequested,
                object: nil,
                userInfo: userInfo
            )
        default:
            break
        }
    }

    private func postHUDNotification(_ name: Notification.Name, userInfo extraUserInfo: [String: String] = [:]) {
        var userInfo = extraUserInfo
        userInfo["source"] = GlassRecordToggleSource.glassDisplayButton.rawValue
        NotificationCenter.default.post(
            name: name,
            object: nil,
            userInfo: userInfo
        )
    }

    private func buildView() -> FlexBox {
        switch hudMode {
        case .standby:
            return buildStandbyView()
        case .ready:
            return buildContextView()
        case .recording:
            return buildRecordingView()
        case .uploading:
            return buildUploadingView()
        case .analyzing:
            return buildAnalyzingView()
        case .patientSelect:
            return buildPatientSelectionView()
        case .captureHistory:
            return buildCaptureHistoryView()
        case .recommendations:
            return buildRecommendedAssessmentView()
        case .result(let kind, let title, let body, _):
            return buildResultView(kind: kind, title: title, body: body)
        }
    }

    private func buildStandbyView() -> FlexBox {
        let hasPatient = !(activePatient?.isEmpty ?? true)
        return FlexBox(direction: .column, spacing: 8) {
            buildHeaderCard(
                title: "Kinelo AR",
                subtitle: hasPatient ? "환자 선택 완료" : "환자 선택 필요"
            )
            if let activePatient, !activePatient.isEmpty {
                buildPatientCard(
                    patient: activePatient,
                    detail: "선택 환자"
                )
            }
            if hasPatient {
                buildActionCard(
                    marker: "LIVE",
                    title: "라이브 시작",
                    body: "선택하면 카메라 라이브 화면으로 들어갑니다.",
                    buttonLabel: "라이브 시작",
                    buttonStyle: .primary,
                    iconName: .videoCamera
                ) {
                    Task { @MainActor in
                        self.postHUDNotification(.glassStandbyStartRequested)
                    }
                }
            } else {
                buildActionCard(
                    marker: "PATIENT",
                    title: "환자 먼저 선택",
                    body: "폰에서 환자를 선택하면 바로 기록을 시작할 수 있습니다.",
                    buttonLabel: "환자 선택",
                    buttonStyle: .primary,
                    iconName: .person
                ) {
                    Task { @MainActor in
                        self.postHUDNotification(.glassPatientPickerRequested)
                    }
                }
            }
            FlexBox(direction: .row, spacing: 8, wrap: true) {
                Button(label: hasPatient ? "환자 변경" : "환자 선택", style: .secondary, iconName: .person, onClick: {
                    Task { @MainActor in
                        self.postHUDNotification(.glassPatientPickerRequested)
                    }
                })
                Button(label: "기록 보기", style: .secondary, iconName: .lightBulb, onClick: {
                    Task { @MainActor in
                        self.postHUDNotification(.openCaptureHistoryRequested)
                    }
                })
            }
        }
    }

    // Patient name + session counter + REC start button
    private func buildContextView() -> FlexBox {
        let hasPatient = !(activePatient?.isEmpty ?? true)
        let patient = activePatient ?? "환자 미선택"
        let sessionLine = sessionCount > 0 ? "세션 \(sessionCount)회 완료" : "첫 녹화 대기"
        return FlexBox(direction: .column, spacing: 8) {
            buildHeaderCard(
                title: "Kinelo AR",
                subtitle: hasPatient ? "라이브 연결됨" : "환자 선택 필요"
            )
            buildPatientCard(
                patient: patient,
                detail: sessionLine
            )
            if hasPatient {
                buildActionCard(
                    marker: "START",
                    title: "녹화 시작",
                    body: "손 제스처로 선택하면 세션 기록이 시작됩니다.",
                    buttonLabel: "녹화 시작",
                    buttonStyle: .primary,
                    iconName: .videoCamera
                ) {
                    Task { @MainActor in
                        NotificationCenter.default.post(
                            name: .glassCaptouchRecordToggle,
                            object: nil,
                            userInfo: ["source": GlassRecordToggleSource.glassDisplayButton.rawValue]
                        )
                    }
                }
            } else {
                buildActionCard(
                    marker: "PATIENT",
                    title: "환자 선택",
                    body: "환자 없이 녹화하면 자동 저장과 차트 연결이 불안정합니다.",
                    buttonLabel: "환자 선택",
                    buttonStyle: .primary,
                    iconName: .person
                ) {
                    Task { @MainActor in
                        self.postHUDNotification(.glassPatientPickerRequested)
                    }
                }
            }
            FlexBox(direction: .row, spacing: 8, wrap: true) {
                Button(label: "추천 평가", style: .secondary, iconName: .checkmarkCircle, onClick: {
                    Task { @MainActor in
                        self.postHUDNotification(.glassRecommendedAssessmentRequested)
                    }
                })
                Button(label: "기록 보기", style: .secondary, iconName: .lightBulb, onClick: {
                    Task { @MainActor in
                        self.postHUDNotification(.openCaptureHistoryRequested)
                    }
                })
            }
        }
    }

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
                    detail: "움직임, 보조, 수행 상태 수집 중"
                )
            }
            buildActionCard(
                marker: "STOP",
                title: "녹화 중지",
                body: "중지하면 저장, 업로드, 분석으로 이어집니다.",
                buttonLabel: "녹화 중지",
                buttonStyle: .secondary,
                iconName: .x
            ) {
                Task { @MainActor in
                    NotificationCenter.default.post(
                        name: .glassCaptouchRecordToggle,
                        object: nil,
                        userInfo: ["source": GlassRecordToggleSource.glassDisplayButton.rawValue]
                    )
                }
            }
            buildInfoCard(
                title: "현재 상태",
                body: "중지는 한 번, 저장과 업로드는 앱이 이어서 처리합니다."
            )
        }
    }

    private func buildPatientSelectionView() -> FlexBox {
        let hasCurrentPatient = !(activePatient?.isEmpty ?? true)
        return FlexBox(direction: .column, spacing: 8) {
            buildHeaderCard(
                title: "환자 선택",
                subtitle: patientCandidates.isEmpty ? "iPhone에서 검색" : "최근 환자 빠른 선택"
            )
            if let activePatient, !activePatient.isEmpty {
                buildPatientCard(
                    patient: activePatient,
                    detail: "현재 선택됨"
                )
            }
            if patientCandidates.isEmpty {
                buildInfoCard(
                    title: "최근 환자 없음",
                    body: "iPhone에 열린 환자 선택 화면에서 검색하거나 새 환자를 추가하세요."
                )
            } else {
                FlexBox(direction: .column, spacing: 8) {
                    for candidate in patientCandidates {
                        Button(label: candidate, style: .primary, iconName: .person, onClick: {
                            Task { @MainActor in
                                self.postHUDNotification(
                                    .glassPatientSelectedFromHUD,
                                    userInfo: ["patient_name": candidate]
                                )
                            }
                        })
                    }
                }
            }
            FlexBox(direction: .row, spacing: 8, wrap: true) {
                Button(label: "iPhone 검색", style: .secondary, iconName: .person, onClick: {
                    Task { @MainActor in
                        self.postHUDNotification(.glassPatientPickerRequested)
                    }
                })
                if hasCurrentPatient {
                    Button(label: "돌아가기", style: .secondary, iconName: .checkmarkCircle, onClick: {
                        Task { @MainActor in
                            await self.showStandby(patient: self.activePatient)
                        }
                    })
                }
            }
        }
    }

    private func buildCaptureHistoryView() -> FlexBox {
        return FlexBox(direction: .column, spacing: 8) {
            buildHeaderCard(
                title: "기록 보기",
                subtitle: captureHistorySummaries.isEmpty ? "저장 기록 없음" : "최근 저장 \(captureHistorySummaries.count)개"
            )
            if let activePatient, !activePatient.isEmpty {
                buildPatientCard(
                    patient: activePatient,
                    detail: "현재 선택 환자"
                )
            }
            if captureHistorySummaries.isEmpty {
                buildInfoCard(
                    title: "아직 기록이 없습니다",
                    body: "녹화 후 저장하거나 전송하면 여기에 최근 기록이 표시됩니다."
                )
            } else {
                FlexBox(direction: .column, spacing: 8) {
                    for summary in captureHistorySummaries {
                        buildInfoCard(
                            title: summary,
                            body: "상세 재생과 공유는 iPhone에서 확인"
                        )
                    }
                }
            }
            FlexBox(direction: .row, spacing: 8, wrap: true) {
                Button(label: "iPhone 상세", style: .primary, iconName: .lightBulb, onClick: {
                    Task { @MainActor in
                        self.postHUDNotification(
                            .openCaptureHistoryRequested,
                            userInfo: ["open_phone": "true"]
                        )
                    }
                })
                Button(label: "추천 평가", style: .secondary, iconName: .checkmarkCircle, onClick: {
                    Task { @MainActor in
                        self.postHUDNotification(.glassRecommendedAssessmentRequested)
                    }
                })
            }
        }
    }

    private func buildRecommendedAssessmentView() -> FlexBox {
        let patient = activePatient ?? "환자 미선택"
        return FlexBox(direction: .column, spacing: 8) {
            buildHeaderCard(
                title: "추천 평가",
                subtitle: selectedAssessment?.isEmpty == false ? "선택됨" : "다음 관찰 항목"
            )
            buildPatientCard(
                patient: patient,
                detail: "현재 세션 기준"
            )
            if let selectedAssessment, !selectedAssessment.isEmpty {
                buildInfoCard(
                    title: "오늘 평가",
                    body: selectedAssessment
                )
            }
            FlexBox(direction: .column, spacing: 8) {
                for item in [
                    "자세/정렬",
                    "기능 과제",
                    "안전 신호"
                ] {
                    Button(label: item, style: item == selectedAssessment ? .primary : .secondary, iconName: .checkmarkCircle, onClick: {
                        Task { @MainActor in
                            self.postHUDNotification(
                                .glassAssessmentSelectedFromHUD,
                                userInfo: ["assessment": item]
                            )
                        }
                    })
                }
            }
            buildInfoCard(
                title: "관찰 힌트",
                body: "정렬, 보상 움직임, 보조량, 피로/호흡 신호를 함께 확인"
            )
            FlexBox(direction: .row, spacing: 8, wrap: true) {
                Button(label: "기록 보기", style: .secondary, iconName: .lightBulb, onClick: {
                    Task { @MainActor in
                        self.postHUDNotification(.openCaptureHistoryRequested)
                    }
                })
                Button(label: "돌아가기", style: .secondary, iconName: .person, onClick: {
                    Task { @MainActor in
                        await self.showStandby(patient: self.activePatient)
                    }
                })
            }
        }
    }

    private func buildUploadingView() -> FlexBox {
        FlexBox(direction: .column, spacing: 8) {
            buildHeaderCard(
                title: "Kinelo AR",
                subtitle: "업로드 중"
            )
            if let activePatient, !activePatient.isEmpty {
                buildPatientCard(
                    patient: activePatient,
                    detail: "캡처 업로드 진행 중"
                )
            }
            buildInfoCard(
                title: "브리지 전송 중",
                body: "캡처를 안전하게 올리고 있습니다."
            )
        }
    }

    private func buildAnalyzingView() -> FlexBox {
        FlexBox(direction: .column, spacing: 8) {
            buildHeaderCard(
                title: "Kinelo AR",
                subtitle: "분석 중"
            )
            if let activePatient, !activePatient.isEmpty {
                buildPatientCard(
                    patient: activePatient,
                    detail: "SOAP 초안 생성 중"
                )
            }
            buildInfoCard(
                title: "자동 기록 생성",
                body: "이미지, 영상, 음성을 정리하고 있습니다."
            )
        }
    }

    private func buildResultView(kind: HUDResultKind, title: String, body: String) -> FlexBox {
        let subtitle = kind == .success ? "기록 완료" : "확인 필요"
        return FlexBox(direction: .column, spacing: 8) {
            buildHeaderCard(
                title: "Kinelo AR",
                subtitle: subtitle
            )
            FlexBox(direction: .row, spacing: 8, crossAlignment: .center) {
                Icon(name: .lightBulb)
                Text(title, style: .body)
            }
            .padding(16)
            .background(.card)
            buildInfoCard(
                title: kind == .success ? "저장 결과" : "오류 안내",
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

    private func buildActionCard(
        marker: String,
        title: String,
        body: String,
        buttonLabel: String,
        buttonStyle: ButtonStyle,
        iconName: IconName,
        onClick: @escaping @Sendable () -> Void
    ) -> FlexBox {
        FlexBox(direction: .column, spacing: 10) {
            FlexBox(direction: .row, spacing: 8, crossAlignment: .center) {
                Icon(name: iconName)
                Text(marker, style: .body)
            }
            Text(title, style: .body)
            Text(body, style: .meta, color: .secondary)
            Button(label: buttonLabel, style: buttonStyle, iconName: iconName, onClick: onClick)
        }
        .padding(16)
        .background(.card)
        .onTap(onClick)
    }

    private func buildDemoSummary() -> String {
        switch hudMode {
        case .standby:
            let patient = activePatient.map { " · \($0)" } ?? ""
            return "🟦 준비 완료\(patient)"
        case .ready:
            let patient = activePatient ?? "환자 미선택"
            let status = sessionCount > 0 ? "세션 \(sessionCount)회 완료" : "첫 녹화 대기"
            return "🟢 라이브  \(patient) · \(status)"
        case .recording:
            let elapsed = elapsedString()
            let suffix = activePatient.map { " · \($0)" } ?? ""
            return "🔴 REC \(elapsed) · 세션 \(max(sessionCount, 1))\(suffix)"
        case .uploading:
            return "🟠 업로드 중"
        case .analyzing:
            return "🟣 분석 중"
        case .patientSelect:
            if patientCandidates.isEmpty {
                return "👤 환자 선택 · iPhone 검색"
            }
            return "👤 환자 선택 · \(patientCandidates.prefix(3).joined(separator: ", "))"
        case .captureHistory:
            return captureHistorySummaries.isEmpty
                ? "📚 기록 보기 · 저장 기록 없음"
                : "📚 기록 보기 · 최근 \(captureHistorySummaries.count)개"
        case .recommendations:
            if let selectedAssessment, !selectedAssessment.isEmpty {
                return "🧭 추천 평가 · \(selectedAssessment)"
            }
            return "🧭 추천 평가 · 자세/기능/안전"
        case .result(let kind, let title, let body, _):
            return "\(kind == .success ? "✅" : "⚠️") \(title) · \(body)"
        }
    }

    private func bridgeModeSummary() -> (mode: String, message: String) {
        switch hudMode {
        case .standby:
            return ("standby", activePatient == nil ? "라이브 연결을 기다리는 중" : "선택 환자 대기")
        case .ready:
            if let activePatient, !activePatient.isEmpty {
                return ("ready", "하단 버튼으로 바로 시작")
            }
            return ("ready", "환자 선택 후 바로 기록할 수 있습니다.")
        case .recording:
            if let activePatient, !activePatient.isEmpty {
                return ("recording", "\(activePatient) · 세션 \(max(sessionCount, 1)) 저장 준비")
            }
            return ("recording", "세션 \(max(sessionCount, 1)) 저장 준비")
        case .uploading:
            return ("uploading", "캡처를 브리지로 안전하게 전송 중")
        case .analyzing:
            return ("analyzing", "SOAP 초안과 요약을 생성 중")
        case .patientSelect:
            if patientCandidates.isEmpty {
                return ("patient_select", "iPhone에서 환자를 검색하거나 새로 추가하세요")
            }
            return ("patient_select", "최근 환자 \(patientCandidates.count)명 중 선택")
        case .captureHistory:
            if captureHistorySummaries.isEmpty {
                return ("history", "저장된 캡처 기록이 없습니다")
            }
            return ("history", "최근 저장 기록 \(captureHistorySummaries.count)개 확인 중")
        case .recommendations:
            if let selectedAssessment, !selectedAssessment.isEmpty {
                return ("recommendations", "\(selectedAssessment) 평가 선택됨")
            }
            return ("recommendations", "자세/기능/안전 평가를 확인 중")
        case .result(.success, _, let body, _):
            return ("success", body)
        case .result(.error, _, let body, _):
            return ("error", body)
        }
    }

    private func elapsedString() -> String {
        guard let start = recordingStart else { return "00:00" }
        let secs = Int(Date().timeIntervalSince(start))
        return String(format: "%02d:%02d", secs / 60, secs % 60)
    }
}
