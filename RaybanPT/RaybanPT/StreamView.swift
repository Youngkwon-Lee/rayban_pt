import SwiftUI
import MWDATCore
import UIKit
import AVKit

struct StreamView: View {
    private enum ProcessingPhase {
        case none
        case uploading
        case analyzing
    }

    private enum LiveStage: Equatable {
        case standby
        case ready
        case live
        case recording(frameCount: Int)
        case uploading
        case analyzing
        case completed

        var title: String {
            switch self {
            case .standby:
                return "대기"
            case .ready:
                return "준비 완료"
            case .live:
                return "라이브 연결됨"
            case .recording(let frameCount):
                return "녹화 중 · \(frameCount)f"
            case .uploading:
                return "업로드 중"
            case .analyzing:
                return "분석 중"
            case .completed:
                return "기록 완료"
            }
        }

        var compactTitle: String {
            switch self {
            case .standby:
                return "대기"
            case .ready:
                return "준비"
            case .live:
                return "라이브"
            case .recording:
                return "REC"
            case .uploading:
                return "업로드"
            case .analyzing:
                return "분석"
            case .completed:
                return "완료"
            }
        }

        var tint: Color {
            switch self {
            case .standby:
                return .white.opacity(0.6)
            case .ready, .uploading:
                return DS.ColorToken.warning
            case .live, .completed:
                return DS.ColorToken.success
            case .recording:
                return DS.ColorToken.danger
            case .analyzing:
                return DS.ColorToken.primary
            }
        }

        var iconName: String {
            switch self {
            case .standby:
                return "pause.circle"
            case .ready:
                return "checkmark.circle"
            case .live:
                return "dot.radiowaves.left.and.right"
            case .recording:
                return "record.circle.fill"
            case .uploading:
                return "arrow.up.circle"
            case .analyzing:
                return "sparkles"
            case .completed:
                return "checkmark.circle.fill"
            }
        }

        var showsPill: Bool {
            self != .standby
        }
    }

    private enum PhotoSource {
        case rayban
        case phone

        var analysisTitle: String {
            switch self {
            case .rayban:
                return "스마트 글라스 카메라"
            case .phone:
                return "iPhone 카메라"
            }
        }

        var uploadSource: String {
            switch self {
            case .rayban:
                return "rayban_dat_camera"
            case .phone:
                return "iphone_camera"
            }
        }
    }

    private struct PendingConsentAction: Identifiable {
        enum Kind {
            case photo(UIImage)
            case video(URL)
            case audio(URL)
            case sessionAutoCapture
        }

        let id = UUID()
        let patientName: String
        let kind: Kind
    }

    private enum RecordingTriggerSource {
        case phoneUI
        case glass(GlassRecordToggleSource)

        var shouldAutoProcess: Bool {
            switch self {
            case .phoneUI:
                return false
            case .glass(let source):
                return source.isHandsFreeGlassPath
            }
        }

        var startToastMessage: String {
            switch self {
            case .phoneUI:
                return "🔴 영상 녹화 시작"
            case .glass(let source):
                return "🔴 \(source.toastLabel) · 녹화 시작"
            }
        }

        var completionToastMessage: String {
            switch self {
            case .phoneUI:
                return "📼 영상 캡처 완료 — 업로드 또는 저장 가능"
            case .glass(let source):
                return "📼 \(source.toastLabel) · 녹화 종료"
            }
        }
    }

    enum SaveStatus: Equatable {
        case idle
        case saving(String)
        case saved(String)
        case failed(String)

        var message: String? {
            switch self {
            case .idle:
                return nil
            case .saving(let message), .saved(let message), .failed(let message):
                return message
            }
        }

        var tint: Color {
            switch self {
            case .idle:
                return .clear
            case .saving:
                return DS.ColorToken.warning
            case .saved:
                return DS.ColorToken.success
            case .failed:
                return DS.ColorToken.danger
            }
        }
    }

    @AppStorage("rayban_pt.auto_save_captures") private var autoSaveCaptures = false
    @AppStorage("rayban_pt.session_auto_capture") private var sessionAutoCaptureEnabled = false
    @AppStorage("rayban_pt.provider_role") private var providerRole = "physical_therapist"
    @State private var vm = StreamViewModel()
    @State private var deviceSession = DeviceSessionManager.shared
    @State private var glassHUD = GlassHUDManager.shared
    @State private var glassExperience = GlassExperienceCoordinator.shared
    @StateObject private var bridgeVm: AdapterViewModel
    @State private var store = PatientStore()
    @State private var captureStore = CaptureStore.shared

    @State private var currentPatient: Patient? = nil
    @State private var showPatientPicker = false
    @State private var isAnalyzing = false
    @State private var showPhotoSheet = false
    @State private var showChartSheet = false
    @State private var showLabelSheet = false
    @State private var showCaptureTagSheet = false
    @State private var showVideoSheet = false
    @State private var showPhoneCamera = false
    @State private var lastEventId: String? = nil
    @State private var analysisText: String = ""
    @State private var isCapturing = false
    @State private var processingPhase: ProcessingPhase = .none
    @State private var toastMessage: String? = nil
    @State private var saveStatus: SaveStatus = .idle
    @State private var showCaptureHistory = false
    @State private var showPhotoPermissionAlert = false
    @State private var photoPermissionMessage = "사진 보관함 접근 권한이 필요합니다."
    @State private var photoSource: PhotoSource = .rayban
    @State private var pendingConsentAction: PendingConsentAction?
    @State private var handsFreeRecordingSession = false
    @State private var sessionAutoCaptureActive = false
    @State private var sessionAutoAudioCaptureActive = false
    @State private var shouldAutoProcessNextRecordedVideo = false
    @State private var automaticCaptureProcessingTask: Task<Void, Never>? = nil
    @State private var isEndingVisitSession = false

    // STT
    @State private var audioRecorder = AudioRecorder()
    @State private var sttText: String = ""       // Whisper 변환 결과 (누적)
    @State private var isTranscribing = false
    @State private var toastTask: Task<Void, Never>? = nil
    @State private var handledLaunchToken: UUID? = nil
    @State private var hudCandidates: [BridgeClient.HudCandidate] = []
    @State private var isLoadingHudCandidates = false
    @State private var hudCandidateDecisionId: String? = nil

    private static let captureHistoryTimeFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "ko_KR")
        formatter.dateFormat = "M/d HH:mm"
        return formatter
    }()

    private var needsServerSetup: Bool {
        let stored = UserDefaults.standard.string(forKey: "bridge_base_url") ?? ""
        return stored.isEmpty
    }

    private var videoCaptureOrigin: String {
        DemoConfig.isGlassDemoEnabled ? "ios_demo_synthetic" : "rayban_dat_camera"
    }

    private var serverSettingsToolbarButton: some View {
        Button {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            NotificationCenter.default.post(name: .openServerSetupRequested, object: nil)
        } label: {
            ZStack(alignment: .topTrailing) {
                Image(systemName: "server.rack")
                    .foregroundStyle(.white)
                    .font(.system(size: 16, weight: .semibold))
                    .padding(4)
                
                if needsServerSetup {
                    Circle()
                        .fill(DS.ColorToken.warning)
                        .frame(width: 8, height: 8)
                        .offset(x: 2, y: -2)
                }
            }
        }
    }

    init(client: BridgeClient) {
        _bridgeVm = StateObject(wrappedValue: AdapterViewModel(client: client))
    }

    // MARK: - Body

    var body: some View {
        streamViewBody
    }

    private var streamViewBody: some View {
        applyAlerts(
            to: applyCaptureHistorySheet(
                to: applyGlassObservers(
                    to: applyCaptureObservers(
                        to: applyCaptureSheets(
                            to: applyLifecycleHandlers(
                                to: applyNavigationChrome(
                                    to: mainScene
                                )
                            )
                        )
                    )
                )
            )
        )
    }

    private var mainScene: some View {
        ZStack(alignment: .bottom) {
            DS.ColorToken.cameraBackground.ignoresSafeArea()

            // 카메라 피드
            cameraFeed
                .ignoresSafeArea()

            topOverlay
                .animation(.spring(response: 0.3), value: vm.isStreaming)

            // 토스트 (중앙)
            toastOverlay

            // 하단 컨트롤바
            controlBar
        }
    }

    private func applyNavigationChrome<Content: View>(to content: Content) -> some View {
        content
            .navigationTitle(isGuidedModeActive ? "Kinelo AR" : currentPatient.map { $0.name } ?? "스마트 글라스 카메라")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.ultraThinMaterial, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar(isGuidedModeActive ? .hidden : .visible, for: .navigationBar)
            .toolbar {
                streamToolbar
            }
            .sheet(isPresented: $showPatientPicker) {
                PatientPickerView(selectedPatient: $currentPatient, store: store)
            }
    }

    @ToolbarContentBuilder
    private var streamToolbar: some ToolbarContent {
        if !isGuidedModeActive {
            ToolbarItem(placement: .topBarLeading) {
                Button {
                    showPatientPicker = true
                } label: {
                    patientToolbarLabel
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                HStack(spacing: 16) {
                    serverSettingsToolbarButton
                    captureOptionsMenu
                }
            }
        }
    }

    private func applyLifecycleHandlers<Content: View>(to content: Content) -> some View {
        content
            .onChange(of: currentPatient?.id) { _, _ in
                handlePatientSelectionChanged()
            }
            .onChange(of: deviceSession.linkState) { _, newState in
                handleDeviceLinkStateChanged(newState)
            }
            .onChange(of: sessionAutoCaptureEnabled) { _, enabled in
                handleSessionAutoCaptureSettingChanged(enabled)
            }
            .onAppear(perform: handleViewAppear)
            .onAppear(perform: handleDeviceSessionAppear)
            .onAppear(perform: handleHUDAutoTestAppear)
            .onDisappear {
                Task { await vm.tearDown() }
            }
    }

    private func applyCaptureSheets<Content: View>(to content: Content) -> some View {
        content
            .sheet(isPresented: $showPhotoSheet) {
                if let photo = vm.capturedPhoto {
                    PhotoReviewSheet(
                        photo: photo,
                        isAnalyzing: $isAnalyzing,
                        analysisText: $analysisText,
                        saveStatus: saveStatus,
                        onSave: handlePhotoSave,
                        onSend: { await handlePhotoSend(photo) },
                        onViewChart: handlePhotoChartOpen
                    )
                }
            }
            .sheet(isPresented: $showVideoSheet) {
                if let url = vm.recordedVideoURL {
                    VideoReviewSheet(
                        videoURL: url,
                        isAnalyzing: $isAnalyzing,
                        analysisText: $analysisText,
                        saveStatus: saveStatus,
                        onSave: handleVideoSave,
                        onSend: { await handleVideoSend(url) },
                        onViewChart: handleVideoChartOpen
                    )
                }
            }
            .sheet(isPresented: $showPhoneCamera) {
                PhoneCameraPicker { image in
                    photoSource = .phone
                    vm.usePhoneCameraPhoto(image)
                }
                .ignoresSafeArea()
            }
            .sheet(isPresented: $showChartSheet) {
                if let eventId = lastEventId {
                    NavigationStack {
                        ChartDetailView(eventId: eventId, client: bridgeVm.client)
                    }
                }
            }
            .sheet(isPresented: $showLabelSheet) {
                if let eventId = lastEventId {
                    LabelingView(eventId: eventId, client: bridgeVm.client)
                }
            }
    }

    private func applyCaptureObservers<Content: View>(to content: Content) -> some View {
        content
            .onChange(of: vm.capturedPhoto) { _, newPhoto in
                handleCapturedPhotoChange(newPhoto)
            }
            .onChange(of: vm.recordedVideoURL) { _, newURL in
                handleRecordedVideoChange(newURL)
            }
            .onChange(of: vm.isStreaming) { _, streaming in
                handleStreamingStateChanged(streaming)
            }
            .onChange(of: vm.recorder.isRecording) { _, recording in
                handleRecorderStateChanged(recording)
            }
            .onChange(of: audioRecorder.isRecording) { _, recording in
                guard sessionAutoCaptureActive else { return }
                sessionAutoAudioCaptureActive = recording
            }
    }

    private func applyGlassObservers<Content: View>(to content: Content) -> some View {
        content
            .onReceive(NotificationCenter.default.publisher(for: .glassCaptouchRecordToggle)) { note in
                handleGlassCaptureToggleNotification(note)
            }
            .onReceive(NotificationCenter.default.publisher(for: .glassRecordingStateRequested)) { note in
                handleGlassRecordingStateNotification(note)
            }
            .onReceive(NotificationCenter.default.publisher(for: .glassCapturePhotoRequested)) { _ in
                handleGlassPhotoCaptureNotification()
            }
            .onReceive(NotificationCenter.default.publisher(for: .glassAudioStartRequested)) { _ in
                handleGlassAudioStartNotification()
            }
            .onReceive(NotificationCenter.default.publisher(for: .glassAudioStopRequested)) { _ in
                handleGlassAudioStopNotification()
            }
            .onReceive(NotificationCenter.default.publisher(for: .glassStandbyStartRequested)) { _ in
                handleGlassStandbyStartNotification()
            }
            .onReceive(NotificationCenter.default.publisher(for: .openCaptureHistoryRequested)) { note in
                handleOpenCaptureHistoryNotification(note)
            }
            .onReceive(NotificationCenter.default.publisher(for: .glassPrimaryActionRequested)) { note in
                handleGlassPrimaryActionNotification(note)
            }
            .onReceive(NotificationCenter.default.publisher(for: .glassPatientPickerRequested)) { _ in
                handleGlassPatientPickerNotification()
            }
            .onReceive(NotificationCenter.default.publisher(for: .glassPatientSelectedFromHUD)) { note in
                handleGlassPatientSelectedNotification(note)
            }
            .onReceive(NotificationCenter.default.publisher(for: .glassRecommendedAssessmentRequested)) { _ in
                handleGlassRecommendedAssessmentNotification()
            }
            .onReceive(NotificationCenter.default.publisher(for: .glassAssessmentSelectedFromHUD)) { note in
                handleGlassAssessmentSelectedNotification(note)
            }
            .onChange(of: glassExperience.pendingLaunchToken) { _, _ in
                Task { await handlePendingGlassLaunchIfNeeded() }
            }
    }

    private func applyCaptureHistorySheet<Content: View>(to content: Content) -> some View {
        content.sheet(isPresented: $showCaptureHistory) {
            NavigationStack {
                CaptureHistoryView()
            }
        }
    }

    private func applyAlerts<Content: View>(to content: Content) -> some View {
        content
            .alert("사진 접근 필요", isPresented: $showPhotoPermissionAlert) {
                Button("설정 열기") {
                    openAppSettings()
                }
                Button("닫기", role: .cancel) { }
            } message: {
                Text(photoPermissionMessage)
            }
            .alert(item: $pendingConsentAction) { action in
                Alert(
                    title: Text("환자 동의 확인"),
                    message: Text(consentPrompt(for: action.patientName)),
                    primaryButton: .default(Text("동의 기록 후 진행")) {
                        Task { await recordConsentAndContinue(action) }
                    },
                    secondaryButton: .cancel(Text("취소"))
                )
            }
    }

    private var isGuidedModeActive: Bool {
        glassExperience.isGuidedModeActive
    }

    private var liveStage: LiveStage {
        switch processingPhase {
        case .uploading:
            return .uploading
        case .analyzing:
            return .analyzing
        case .none:
            break
        }

        if vm.recorder.isRecording {
            return .recording(frameCount: vm.recorder.frameCount)
        }
        if vm.isStreaming {
            return .live
        }
        if lastEventId != nil {
            return .completed
        }
        if glassHUD.isDisplayConnected {
            return .ready
        }
        return .standby
    }

    private var topOverlay: some View {
        VStack(spacing: 8) {
            if isGuidedModeActive {
                guidedSessionPill
                    .padding(.top, 8)
                    .padding(.horizontal, 16)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }

            if liveStage.showsPill {
                statusPill
                    .padding(.top, 8)
                    .padding(.horizontal, 16)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }

            if let launchMessage = glassExperience.bannerMessage {
                launchStatusPill(message: launchMessage)
                    .padding(.horizontal, 16)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }

            if let hudSummary = glassHUD.demoHUDSummary {
                glassHUDPreviewPill(summary: "HUD · \(hudSummary)")
                    .padding(.horizontal, 16)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }

            if shouldShowSTTPill {
                sttPill
                    .padding(.horizontal, 16)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }

            if shouldShowHudCandidatePill {
                hudCandidatePill
                    .padding(.horizontal, 16)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }

            if let message = saveStatus.message {
                saveStatusPill(message: message, tint: saveStatus.tint)
                    .padding(.horizontal, 16)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }

            Spacer()
        }
    }

    private var guidedSessionPill: some View {
        HStack(spacing: DS.Spacing.xs) {
            Image(systemName: "eyeglasses")
                .font(.system(size: 11, weight: .bold))
                .foregroundStyle(DS.ColorToken.primary)
            VStack(alignment: .leading, spacing: 1) {
                Text("Kinelo AR")
                    .font(.system(size: DS.FontSize.caption, weight: .bold))
                    .foregroundStyle(.white)
                Text(guidedSessionSummary)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.white.opacity(0.74))
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, DS.Spacing.sm)
        .padding(.vertical, DS.Spacing.xs)
        .frame(minHeight: 40)
        .background(DS.ColorToken.primary.opacity(0.22), in: Capsule())
        .overlay(Capsule().stroke(DS.ColorToken.primary.opacity(0.32), lineWidth: 1))
    }

    private var guidedSessionSummary: String {
        let patientName = currentPatient?.name ?? glassExperience.activeLaunchContext?.patientName ?? "환자 대기"
        if let sessionLabel = glassExperience.activeLaunchContext?.sessionLabel,
           !sessionLabel.isEmpty {
            return "\(patientName) · \(sessionLabel)"
        }
        return patientName
    }

    @ViewBuilder
    private var toastOverlay: some View {
        if let msg = toastMessage {
            VStack {
                Spacer()
                Text(msg)
                    .font(.subheadline).fontWeight(.medium)
                    .foregroundStyle(.white)
                    .padding(.horizontal, 16).padding(.vertical, 10)
                    .background(.ultraThinMaterial, in: Capsule())
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
                Spacer().frame(height: 160)
            }
        }
    }

    private var shouldShowSTTPill: Bool {
        !sttText.isEmpty || isTranscribing
    }

    private var shouldShowHudCandidatePill: Bool {
        !hudCandidates.isEmpty || isLoadingHudCandidates
    }

    // MARK: - 카메라 피드

    private var cameraFeed: some View {
        GeometryReader { geo in
            ZStack {
                SmartGlassMotionBackdrop()
                    .ignoresSafeArea()

                if let frame = vm.currentFrame {
                    Image(uiImage: frame)
                        .resizable()
                        .scaledToFill()
                        .frame(width: geo.size.width, height: geo.size.height)
                        .clipped()
                        .accessibilityIdentifier("raybanDATFrame")
                } else {
                    EmptyCameraState(
                        isStreaming: vm.isStreaming,
                        hasActiveDevice: vm.hasActiveDevice
                    )
                }

                if DemoConfig.usesMaskedCaptureFrame {
                    maskedCaptureBadge
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                        .padding(.top, 184)
                        .padding(.leading, 24)
                }

                // 녹화 중 테두리
                if vm.recorder.isRecording {
                    RoundedRectangle(cornerRadius: 0)
                        .stroke(DS.ColorToken.danger, lineWidth: 3)
                        .ignoresSafeArea()
                        .animation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true),
                                   value: vm.recorder.isRecording)
                    }
                }
            }
            .sheet(isPresented: $showCaptureTagSheet) {
                CaptureTagSheet { option in
                    await recordCaptureTag(option)
                }
            }
    }

    private func consentPrompt(for patientName: String) -> String {
        "\(patientName) 환자/보호자의 촬영, 녹음, 분석, 차트 생성 동의를 확인한 뒤 진행하세요."
    }

    private var maskedCaptureBadge: some View {
        HStack(spacing: 8) {
            Image(systemName: "checkmark.shield.fill")
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(DS.ColorToken.success)
            Text("마스킹 적용")
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(.white)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(DS.ColorToken.surface, in: Capsule())
        .overlay {
            Capsule()
                .stroke(DS.ColorToken.success.opacity(0.45), lineWidth: 1)
        }
    }

    // MARK: - 툴바 라벨 (타입 추론 분리)

    @ViewBuilder
    private var patientToolbarLabel: some View {
        let iconName = currentPatient == nil ? "person.crop.circle.badge.plus" : "person.crop.circle.fill"
        let iconColor: Color = currentPatient == nil ? DS.ColorToken.warning : DS.ColorToken.success
        HStack(spacing: 5) {
            Image(systemName: iconName).foregroundStyle(iconColor)
            if let p = currentPatient {
                Text(p.name).font(.caption).foregroundStyle(.white)
            }
        }
    }

    // MARK: - 글라스 HUD 데모 미리보기 배지

    private func glassHUDPreviewPill(summary: String) -> some View {
        HStack(spacing: DS.Spacing.xs) {
            Image(systemName: "eyeglasses")
                .font(.system(size: 11, weight: .bold))
                .foregroundStyle(DS.ColorToken.primary)
            Text(summary)
                .font(.system(size: DS.FontSize.caption, weight: .medium))
                .foregroundStyle(.white)
                .lineLimit(1)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, DS.Spacing.sm)
        .padding(.vertical, DS.Spacing.xs)
        .frame(minHeight: 32)
        .background(DS.ColorToken.primary.opacity(0.18), in: Capsule())
        .overlay(Capsule().stroke(DS.ColorToken.primary.opacity(0.35), lineWidth: 1))
    }

    // MARK: - 상태 Pill

    private var statusPill: some View {
        HStack(spacing: DS.Spacing.xs) {
            Circle()
                .fill(liveStage.tint)
                .frame(width: 8, height: 8)
                .shadow(color: liveStage.tint, radius: 4)

            Text(liveStage.title)
                .font(.system(size: DS.FontSize.caption, weight: .semibold))
                .fontWeight(.medium)
                .foregroundStyle(.white)

            if liveStage == .uploading || liveStage == .analyzing {
                Spacer()
                ProgressView()
                    .progressViewStyle(.circular)
                    .scaleEffect(0.72)
                    .tint(.white)
            } else if case .recording = liveStage {
                Spacer()
                Image(systemName: liveStage.iconName)
                    .foregroundStyle(liveStage.tint)
                    .font(.caption)
            }
        }
        .padding(.horizontal, DS.Spacing.sm)
        .padding(.vertical, DS.Spacing.xs)
        .frame(minHeight: 32)
        .background(DS.ColorToken.surface, in: Capsule())
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func launchStatusPill(message: String) -> some View {
        HStack(spacing: DS.Spacing.xs) {
            Image(systemName: "sparkles.rectangle.stack")
                .font(.system(size: 11, weight: .bold))
                .foregroundStyle(DS.ColorToken.primary)
            Text(message)
                .font(.system(size: DS.FontSize.caption, weight: .semibold))
                .foregroundStyle(.white)
                .lineLimit(1)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, DS.Spacing.sm)
        .padding(.vertical, DS.Spacing.xs)
        .frame(minHeight: 32)
        .background(DS.ColorToken.primary.opacity(0.16), in: Capsule())
        .overlay(Capsule().stroke(DS.ColorToken.primary.opacity(0.32), lineWidth: 1))
    }

    private func saveStatusPill(message: String, tint: Color) -> some View {
        HStack(spacing: DS.Spacing.xs) {
            Circle()
                .fill(tint)
                .frame(width: 8, height: 8)
            Text(message)
                .font(.system(size: DS.FontSize.caption, weight: .medium))
                .foregroundStyle(.white)
            Spacer()
        }
        .padding(.horizontal, DS.Spacing.sm)
        .padding(.vertical, DS.Spacing.xs)
        .frame(minHeight: 32)
        .background(DS.ColorToken.surface, in: Capsule())
    }

    private var linkStatusPill: some View {
        HStack(spacing: DS.Spacing.xs) {
            Circle()
                .fill(deviceSession.linkState == .connected ? DS.ColorToken.success : DS.ColorToken.warning)
                .frame(width: 8, height: 8)
            Text(deviceSession.statusMessage)
                .font(.system(size: DS.FontSize.caption, weight: .medium))
                .foregroundStyle(.white)
            Button("DAT 업데이트") {
                Task { await vm.openDATGlassesAppUpdate() }
            }
            .font(.caption2)
            .buttonStyle(.bordered)
            .tint(DS.ColorToken.primary)
            if deviceSession.linkState != .connected {
                Button(deviceSession.registrationState == .registered ? "재연결" : "Meta AI 연결") {
                    deviceSession.retryConnection()
                }
                .font(.caption2)
                .buttonStyle(.bordered)
                .tint(DS.ColorToken.warning)
            }
        }
        .padding(.horizontal, DS.Spacing.sm)
        .padding(.vertical, DS.Spacing.xs)
        .frame(minHeight: 32)
        .background(DS.ColorToken.surfaceSoft, in: Capsule())
    }

    // MARK: - STT Pill

    private var sttPill: some View {
        HStack(spacing: DS.Spacing.xs) {
            if isTranscribing {
                ProgressView()
                    .progressViewStyle(.circular)
                    .scaleEffect(0.7)
                    .tint(.white)
                Text("변환 중...")
                    .font(.system(size: DS.FontSize.caption, weight: .medium))
                    .foregroundStyle(.white.opacity(0.8))
            } else {
                Image(systemName: "text.bubble.fill")
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.warning)
                Text(sttText)
                    .font(.system(size: DS.FontSize.caption, weight: .regular))
                    .foregroundStyle(.white)
                    .lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
                // 지우기
                Button {
                    withAnimation { sttText = "" }
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(.white.opacity(0.6))
                        .font(.caption)
                }
            }
        }
        .padding(.horizontal, DS.Spacing.sm)
        .padding(.vertical, DS.Spacing.xs)
        .background(DS.ColorToken.surface, in: RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous))
    }

    private var hudCandidatePill: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: DS.Spacing.xs) {
                Image(systemName: "checklist")
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.primary)
                Text("기록 후보")
                    .font(.system(size: DS.FontSize.caption, weight: .semibold))
                    .foregroundStyle(.white)
                Spacer()
                if isLoadingHudCandidates {
                    ProgressView()
                        .progressViewStyle(.circular)
                        .scaleEffect(0.65)
                        .tint(.white)
                }
                Button {
                    Task { await refreshHudCandidates() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.72))
                }
                Button {
                    withAnimation { hudCandidates = [] }
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.6))
                }
            }

            ForEach(hudCandidates.prefix(2), id: \.id) { candidate in
                hudCandidateRow(candidate)
            }
        }
        .padding(.horizontal, DS.Spacing.sm)
        .padding(.vertical, DS.Spacing.xs)
        .background(DS.ColorToken.surface, in: RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous)
                .stroke(DS.ColorToken.primary.opacity(0.24), lineWidth: 1)
        )
    }

    private func hudCandidateRow(_ candidate: BridgeClient.HudCandidate) -> some View {
        let summary = hudCandidateSummary(candidate)
        let candidateId = candidate.id ?? ""
        let isBusy = hudCandidateDecisionId == candidateId

        return HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text(summary)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.white)
                    .lineLimit(1)
                if let symptom = candidate.symptom, !symptom.isEmpty {
                    Text(symptom)
                        .font(.system(size: 10, weight: .regular))
                        .foregroundStyle(.white.opacity(0.7))
                        .lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            if isBusy {
                ProgressView()
                    .progressViewStyle(.circular)
                    .scaleEffect(0.65)
                    .tint(.white)
            } else {
                Button {
                    Task { await decideHudCandidate(candidate, approve: true) }
                } label: {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundStyle(DS.ColorToken.success)
                }
                .disabled(candidateId.isEmpty)

                Button {
                    Task { await decideHudCandidate(candidate, approve: false) }
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundStyle(DS.ColorToken.danger)
                }
                .disabled(candidateId.isEmpty)
            }
        }
        .padding(.vertical, 2)
    }

    // MARK: - 하단 컨트롤바

    private var controlBar: some View {
        VStack(spacing: 8) {
            if let err = vm.errorMessage {
                Text(err)
                    .font(.caption)
                    .foregroundStyle(.white)
                    .lineLimit(2)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 8)
                    .background(DS.ColorToken.danger.opacity(0.84), in: Capsule())
            }

            VStack(spacing: 8) {
                if isGuidedModeActive {
                    guidedControlHeader
                } else {
                    HStack(spacing: 10) {
                        Label(
                            currentPatient?.name ?? "환자 미선택",
                            systemImage: currentPatient == nil ? "person.crop.circle.badge.plus" : "person.crop.circle.fill"
                        )
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.white.opacity(currentPatient == nil ? 0.72 : 0.94))
                        .lineLimit(1)

                        Spacer(minLength: 8)

                        if currentPatient != nil {
                            Label("동의 확인", systemImage: "checkmark.shield.fill")
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(.white.opacity(0.72))
                                .lineLimit(1)
                        }

                        Label(liveStage.title, systemImage: liveStage.iconName)
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(liveStage.showsPill ? liveStage.tint : .white.opacity(0.72))
                            .lineLimit(1)
                    }
                }

                HStack(alignment: .center, spacing: 12) {
                    Button {
                        Task {
                            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                            if audioRecorder.isRecording {
                                await stopAndTranscribe()
                            } else {
                                await startAudioRecording()
                            }
                        }
                    } label: {
                        DockActionButton(
                            title: audioRecorder.isRecording ? "중지" : "음성",
                            systemImage: audioRecorder.isRecording ? "stop.fill" : "mic.fill",
                            tint: audioRecorder.isRecording ? DS.ColorToken.danger : .white,
                            isActive: audioRecorder.isRecording,
                            isBusy: isTranscribing
                        )
                    }
                    .buttonStyle(TactileScaleButtonStyle())
                    .disabled(isTranscribing)

                    Spacer(minLength: 0)

                    Button {
                        Task {
                            UIImpactFeedbackGenerator(style: .heavy).impactOccurred()
                            if vm.isStreaming {
                                isCapturing = true
                                photoSource = .rayban
                                vm.capturePhoto()
                                try? await Task.sleep(nanoseconds: 120_000_000)
                                isCapturing = false
                            } else if vm.hasActiveDevice {
                                if currentPatient == nil {
                                    showPatientPicker = true
                                } else {
                                    await vm.startStreaming()
                                }
                            } else {
                                openPhoneCamera()
                            }
                        }
                    } label: {
                        VStack(spacing: 6) {
                            CaptureButton(
                                isStreaming: vm.isStreaming,
                                isCapturing: isCapturing,
                                usesPhoneCameraFallback: !vm.hasActiveDevice
                            )
                            Text(centerButtonTitle)
                                .font(.system(size: 12, weight: .bold))
                                .foregroundStyle(.white)
                        }
                    }
                    .buttonStyle(TactileScaleButtonStyle())

                    Spacer(minLength: 0)

                    rightActionButton
                }

                Text(controlHintText)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.white.opacity(0.68))
                    .lineLimit(1)
                    .minimumScaleFactor(0.86)
                    .frame(maxWidth: .infinity, alignment: .center)
            }
            .padding(.horizontal, 14)
            .padding(.top, 9)
            .padding(.bottom, 10)
            .background(DS.ColorToken.controlSurface, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 24, style: .continuous)
                    .stroke(DS.ColorToken.controlStroke, lineWidth: 1)
            }
            .shadow(color: DS.ColorToken.primary.opacity(0.16), radius: 18, y: 10)
            .padding(.horizontal, 14)
            .padding(.bottom, controlBarBottomPadding)
        }
    }

    private var guidedControlHeader: some View {
        HStack(spacing: 10) {
            Label(
                currentPatient?.name ?? glassExperience.activeLaunchContext?.patientName ?? "환자 대기",
                systemImage: "person.crop.circle.fill"
            )
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(.white.opacity(0.94))
            .lineLimit(1)

            Spacer(minLength: 8)

            if let sessionLabel = glassExperience.activeLaunchContext?.sessionLabel,
               !sessionLabel.isEmpty {
                Label(sessionLabel, systemImage: "waveform.path.ecg")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.72))
                    .lineLimit(1)
            }

            Label(liveStage.compactTitle, systemImage: liveStage.iconName)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(liveStage.showsPill ? liveStage.tint : .white.opacity(0.72))
                .lineLimit(1)
        }
    }

    private func handlePendingGlassLaunchIfNeeded() async {
        guard let pendingToken = glassExperience.pendingLaunchToken else { return }
        guard pendingToken != handledLaunchToken else { return }

        guard let launch = glassExperience.consumePendingLaunch() else { return }
        handledLaunchToken = launch.token

        if let patientName = launch.context?.patientName?.trimmingCharacters(in: .whitespacesAndNewlines),
           !patientName.isEmpty,
           currentPatient?.name != patientName {
            currentPatient = store.touch(name: patientName)
        }

        bridgeVm.client.updatePhysioContext(
            clientId: launch.context?.physioClientId ?? "",
            sessionId: launch.context?.physioSessionId ?? "",
            subjectPersonId: launch.context?.subjectPersonId
        )
        await ensureVisitSessionForCurrentPatient(historySummary: launch.context?.sessionLabel ?? "")

        // A physio_app launch explicitly requests automatic capture. Set the
        // toggle only after the launch has selected a patient, so the
        // consent-gated handler cannot reset it while the patient is nil.
        print("[SessionAutoCapture] launch_request=\(launch.context?.automaticCaptureRequested == true)")
        if launch.context?.automaticCaptureRequested == true {
            sessionAutoCaptureEnabled = true
        }

        if !vm.isStreaming {
            await vm.prepareStandbyDisplay(patientName: currentPatient?.name)
        }

        // If the link is still negotiating, handleDeviceLinkStateChanged()
        // owns the single retry after MWDAT reports .connected. Starting here
        // as well can create two DeviceSession instances and make the glasses
        // report "Device unavailable". A launch that arrives after the link
        // is already connected is covered by that same callback in the normal
        // foreground lifecycle.
        guard !vm.isStreaming else { return }
    }

    private func handleViewAppear() {
        if DemoConfig.isDATMockPatientBootstrapEnabled, currentPatient == nil {
            currentPatient = store.touch(name: "DAT Mock QA")
        }
        vm.setup()
        Task {
            await vm.prepareStandbyDisplay(patientName: currentPatient?.name)
            await handlePendingGlassLaunchIfNeeded()
        }
    }

    private func handleDeviceSessionAppear() {
        deviceSession.start()
    }

    private func handlePatientSelectionChanged() {
        withAnimation {
            sttText = ""
            hudCandidates = []
        }
        Task {
            await ensureVisitSessionForCurrentPatient()
            await refreshHudCandidates()
            if vm.isStreaming {
                guard !vm.recorder.isRecording else { return }
                await GlassHUDManager.shared.updateContextPatient(currentPatient?.name)
                // The setting can persist across launches. If the live stream
                // was already running before a patient was selected, retry
                // the consent/session-gated automatic capture now that the
                // required context exists.
                await startAutomaticSessionCaptureIfNeeded()
                return
            }

            await vm.prepareStandbyDisplay(patientName: currentPatient?.name)
        }
    }

    private func ensureVisitSessionForCurrentPatient(historySummary: String = "") async {
        guard let patient = currentPatient else { return }
        guard !bridgeVm.client.ownerOrgId.isEmpty,
              !bridgeVm.client.ownerProviderPersonId.isEmpty,
              !bridgeVm.client.subjectPersonId.isEmpty
        else { return }
        if let active = bridgeVm.visitSession,
           active.status == "active",
           active.subject_person_id == bridgeVm.client.subjectPersonId {
            return
        }
        await bridgeVm.startVisitSession(
            patientAlias: patient.name,
            historySummary: historySummary,
            providerRole: providerRole
        )
    }

    private func handleDeviceLinkStateChanged(_ newState: LinkState) {
        Task {
            guard !vm.isStreaming else { return }
            guard newState == .connected else { return }
            await vm.prepareStandbyDisplay(patientName: currentPatient?.name)

            // A Care Live launch can arrive while MWDAT is still negotiating
            // the BLE link. In that window the first automatic-capture attempt
            // is rejected with PermissionError.noDeviceWithConnection. Retry
            // only after the link is genuinely connected; manual launches
            // still remain standby-only until the clinician starts live.
            guard sessionAutoCaptureEnabled, currentPatient != nil else { return }
            await vm.startStreaming()
        }
    }

    private func handleStreamingStateChanged(_ streaming: Bool) {
        Task {
            if streaming {
                await GlassHUDManager.shared.startContext(patient: currentPatient?.name)
                await startAutomaticSessionCaptureIfNeeded()
            } else {
                await GlassHUDManager.shared.stopContext()
                // A device/session error can stop the stream without going
                // through the phone stop button. Close the automatic capture
                // window here as well so a partial but valid session is not
                // left recording forever.
                if sessionAutoCaptureActive || sessionAutoAudioCaptureActive {
                    await stopAutomaticSessionCapture()
                }
            }
        }
    }

    private func handleRecorderStateChanged(_ recording: Bool) {
        Task {
            await bridgeVm.setVisitRecording(recording)
            if recording {
                await GlassHUDManager.shared.startRecording(patient: currentPatient?.name)
            } else {
                await GlassHUDManager.shared.stopRecording()
            }
        }
    }

    private func handleHUDAutoTestAppear() {
        guard DemoConfig.isHUDAutoTestEnabled else { return }
        if DemoConfig.isSessionAutoCaptureAutoTestEnabled {
            runAutomaticSessionCaptureAutoTest()
        } else {
            runHUDAutoTest()
        }
    }

    private func handlePhotoSave() async {
        await saveCurrentPhoto()
    }

    private func handleCapturedPhotoChange(_ newPhoto: UIImage?) {
        guard newPhoto != nil else { return }
        analysisText = ""
        lastEventId = nil
        saveStatus = .idle
        showPhotoSheet = true
        guard autoSaveCaptures else { return }
        Task {
            await saveCurrentPhoto(triggeredAutomatically: true)
        }
    }

    private func handleRecordedVideoChange(_ newURL: URL?) {
        guard let newURL else { return }
        analysisText = ""
        lastEventId = nil
        saveStatus = .idle
        if shouldAutoProcessNextRecordedVideo {
            shouldAutoProcessNextRecordedVideo = false
            showVideoSheet = false
            automaticCaptureProcessingTask = Task {
                await processHandsFreeRecordedVideo(newURL)
            }
            return
        }
        showVideoSheet = true
    }

    private func handlePhotoSend(_ photo: UIImage) async {
        await runWithConsent(.photo(photo))
    }

    private func handlePhotoChartOpen() {
        showPhotoSheet = false
        showChartSheet = true
    }

    private func handleVideoSave() async {
        await saveCurrentVideo()
    }

    private func handleVideoSend(_ url: URL) async {
        await runWithConsent(.video(url))
    }

    private func handleVideoChartOpen() {
        showVideoSheet = false
        showChartSheet = true
    }

    @ViewBuilder
    private var rightActionButton: some View {
        if vm.isStreaming {
            HStack(spacing: 10) {
                Button {
                    showCaptureTagSheet = true
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                } label: {
                    DockActionButton(title: "태그", systemImage: "tag.fill", tint: DS.ColorToken.warning)
                }
                .buttonStyle(TactileScaleButtonStyle())

                Button {
                    Task { await toggleRecording(triggeredBy: .phoneUI) }
                } label: {
                    DockActionButton(
                        title: vm.recorder.isRecording ? "중지" : "REC",
                        systemImage: vm.recorder.isRecording ? "stop.fill" : "record.circle",
                        tint: DS.ColorToken.danger,
                        isActive: vm.recorder.isRecording
                    )
                }
                .buttonStyle(TactileScaleButtonStyle())
                .disabled(isSavingInProgress)

                Button {
                    Task { await stopStreamingFlow() }
                } label: {
                    DockActionButton(
                        title: "완료",
                        systemImage: "xmark",
                        tint: .white,
                        isActive: false
                    )
                }
                .buttonStyle(TactileScaleButtonStyle())
            }
        } else if lastEventId != nil {
            // 완료 후 버튼 그룹
            HStack(spacing: 10) {
                // 라벨링 버튼
                Button {
                    showLabelSheet = true
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                } label: {
                    DockActionButton(title: "라벨", systemImage: "tag.fill", tint: DS.ColorToken.warning)
                }
                .buttonStyle(TactileScaleButtonStyle())
                // 차트 보기 버튼
                Button {
                    showChartSheet = true
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                } label: {
                    DockActionButton(title: "차트", systemImage: "doc.text.fill", tint: DS.ColorToken.primary)
                }
                .buttonStyle(TactileScaleButtonStyle())

                if bridgeVm.visitSession?.status == "active" {
                    Button {
                        Task { await finishVisitSession() }
                    } label: {
                        DockActionButton(
                            title: "종료",
                            systemImage: "checkmark.seal.fill",
                            tint: DS.ColorToken.success,
                            isBusy: isEndingVisitSession
                        )
                    }
                    .buttonStyle(TactileScaleButtonStyle())
                    .disabled(isEndingVisitSession)
                }
            }
        } else if let url = vm.recordedVideoURL {
            HStack(spacing: 10) {
                Button {
                    Task { await saveCurrentVideo() }
                } label: {
                    DockActionButton(
                        title: "저장",
                        systemImage: vm.lastSavedVideo == nil ? "square.and.arrow.down.fill" : "checkmark.circle.fill",
                        tint: DS.ColorToken.success,
                        isBusy: isSavingInProgress
                    )
                }
                .buttonStyle(TactileScaleButtonStyle())
                .disabled(isSavingInProgress)

                Button {
                    Task { await runWithConsent(.video(url)) }
                } label: {
                    DockActionButton(
                        title: videoActionButtonTitle,
                        systemImage: uploadButtonSymbol,
                        tint: DS.ColorToken.primary,
                        isBusy: isAnalyzing || isSavingInProgress
                    )
                }
                .buttonStyle(TactileScaleButtonStyle())
                .disabled(isAnalyzing || isSavingInProgress)
                .accessibilityLabel("분석 및 업로드")
            }
        } else {
            Button {
                Task { await prepareGlassesDisplay() }
            } label: {
                DockActionButton(
                    title: standbyHudButtonTitle,
                    systemImage: "rectangle.on.rectangle",
                    tint: glassHUD.isDisplayConnected ? DS.ColorToken.primary : .white,
                    isDisabled: false
                )
            }
            .buttonStyle(TactileScaleButtonStyle())
        }
    }

    // MARK: - Actions

    private func runHUDAutoTest() {
        Task {
            let hud = GlassHUDManager.shared
            // 1) 스트리밍이 활성화될 때까지 대기 (enableGlassDemoMode → isStreaming=true)
            for _ in 0..<20 {
                if vm.isStreaming { break }
                try? await Task.sleep(nanoseconds: 200_000_000)
            }
            try? await Task.sleep(nanoseconds: 500_000_000)
            print("[HUDAutoTest] ① context: \(hud.demoHUDSummary ?? "nil")")

            // 2) HUD 상태와 실제 VideoRecorder를 함께 시작한다.
            //    Demo 모드에서도 live frame이 들어오므로 MP4 산출물까지 검증할 수 있다.
            await hud.startRecording(patient: "테스트 김철수")
            vm.startRecording()
            print("[HUDAutoTest] recorder-start streaming=\(vm.isStreaming) frame=\(vm.currentFrame != nil) recording=\(vm.recorder.isRecording) status=\(vm.recorder.statusMessage)")
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            print("[HUDAutoTest] ② recording: \(hud.demoHUDSummary ?? "nil") frames=\(vm.recorder.frameCount) recording=\(vm.recorder.isRecording) status=\(vm.recorder.statusMessage)")

            // 3) 녹화 중지 → context 복귀
            await vm.stopRecording()
            let recordedURL = vm.recordedVideoURL
            await hud.stopRecording()
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            if let recordedURL,
               let attributes = try? FileManager.default.attributesOfItem(atPath: recordedURL.path),
               let fileSize = attributes[.size] as? NSNumber {
                print("[HUDAutoTest] ③ context after stop: \(hud.demoHUDSummary ?? "nil") media=mp4 bytes=\(fileSize.intValue) path=\(recordedURL.path)")
            } else {
                print("[HUDAutoTest] ③ context after stop: \(hud.demoHUDSummary ?? "nil") media=missing")
            }

            await runMediaUploadAutoTestIfRequested(recordedURL)

            // 4) AI 인사이트
            await hud.showSuccess(title: "차트 생성됨", body: "환자: 테스트 김철수")
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            print("[HUDAutoTest] ④ insight: \(hud.demoHUDSummary ?? "nil")")

            // 5) 인사이트 자동 해제 후 context 복귀 (8초 타이머)
            try? await Task.sleep(nanoseconds: 7_000_000_000)
            print("[HUDAutoTest] ⑤ after insight: \(hud.demoHUDSummary ?? "nil")")
            print("[HUDAutoTest] ✅ 완료")
        }
    }

    private func runAutomaticSessionCaptureAutoTest() {
        Task {
            for _ in 0..<30 {
                if vm.isStreaming, currentPatient != nil, bridgeVm.visitSession?.status == "active" {
                    break
                }
                try? await Task.sleep(nanoseconds: 200_000_000)
            }

            guard vm.isStreaming, currentPatient != nil else {
                print("[SessionAutoCaptureAutoTest] skipped reason=stream_or_patient_missing")
                return
            }

            sessionAutoCaptureEnabled = true
            for _ in 0..<50 {
                if sessionAutoCaptureActive || !sessionAutoCaptureEnabled { break }
                try? await Task.sleep(nanoseconds: 200_000_000)
            }
            guard sessionAutoCaptureActive else {
                print("[SessionAutoCaptureAutoTest] skipped reason=consent_or_visit_gate")
                sessionAutoCaptureEnabled = false
                return
            }

            print("[SessionAutoCaptureAutoTest] start streaming=\(vm.isStreaming) recording=\(vm.recorder.isRecording) audio=\(sessionAutoAudioCaptureActive)")
            // Physical Ray-Ban Display links can reach `.started` before the
            // first video frame is delivered. Keep the deterministic QA
            // window long enough to observe a real frame after negotiation.
            try? await Task.sleep(nanoseconds: 8_000_000_000)
            print("[SessionAutoCaptureAutoTest] recording frames=\(vm.recorder.frameCount) audio=\(sessionAutoAudioCaptureActive)")

            await stopAutomaticSessionCapture(processAutomatically: false)
            let recordedURL = vm.recordedVideoURL
            if let recordedURL,
               let attributes = try? FileManager.default.attributesOfItem(atPath: recordedURL.path),
               let fileSize = attributes[.size] as? NSNumber {
                print("[SessionAutoCaptureAutoTest] stop media=mp4 bytes=\(fileSize.intValue) path=\(recordedURL.path)")
            } else {
                print("[SessionAutoCaptureAutoTest] stop media=missing")
            }

            await runMediaUploadAutoTestIfRequested(recordedURL)
            // Close the DAT session as part of the physical QA harness. This
            // prevents a test process termination from leaving the glasses'
            // broadcast slot occupied for the next run.
            if vm.isStreaming {
                await vm.stopStreaming()
            }
            sessionAutoCaptureEnabled = false
            print("[SessionAutoCaptureAutoTest] ✅ 완료")
        }
    }

    private func runMediaUploadAutoTestIfRequested(_ recordedURL: URL?) async {
        guard DemoConfig.isMediaUploadAutoTestEnabled else { return }
        guard let recordedURL else {
            print("[MediaUploadAutoTest] skipped reason=missing_mp4")
            return
        }
        guard let patient = currentPatient,
              !patient.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else {
            print("[MediaUploadAutoTest] skipped reason=patient_not_selected")
            return
        }
        guard !bridgeVm.client.apiKey.isEmpty else {
            print("[MediaUploadAutoTest] skipped reason=bridge_api_key_missing")
            return
        }

        do {
            guard try await bridgeVm.client.hasActiveConsent(patientName: patient.name) else {
                print("[MediaUploadAutoTest] skipped reason=active_consent_missing")
                return
            }

            let accepted = try await bridgeVm.client.uploadVideo(
                fileURL: recordedURL,
                patientName: patient.name,
                // The same deterministic auto-stop harness is used on the
                // physical device. Preserve the actual capture origin rather
                // than labeling a real Ray-Ban frame as synthetic demo data.
                source: videoCaptureOrigin
            )
            let final = try await bridgeVm.client.waitUntilDone(
                eventId: accepted.event_id,
                maxTries: 180,
                intervalSec: 1.0
            )
            guard final.status == "done" else {
                print("[MediaUploadAutoTest] status=\(final.status) outer_event_id=\(accepted.event_id) error=\(final.error ?? final.message ?? "unknown")")
                return
            }

            let eventId = final.eventId ?? accepted.event_id
            await bridgeVm.attachVisitEventIfActive(eventId)
            await bridgeVm.recordMediaCaptureEvent(
                eventId: eventId,
                sourceType: "video",
                candidateType: "video_evidence",
                fileName: recordedURL.lastPathComponent,
                captureOrigin: videoCaptureOrigin
            )
            print("[MediaUploadAutoTest] status=done outer_event_id=\(accepted.event_id) clinical_event_id=\(eventId)")
        } catch {
            print("[MediaUploadAutoTest] failed error=\(bridgeErrorMessage(error))")
        }
    }

    private var isSavingInProgress: Bool {
        if case .saving = saveStatus {
            return true
        }
        return false
    }

    private var uploadButtonSymbol: String {
        if isSavingInProgress {
            return "hourglass"
        }
        return isAnalyzing ? "ellipsis" : "arrow.up.circle.fill"
    }

    private var controlHintText: String {
        if sessionAutoCaptureActive {
            return sessionAutoAudioCaptureActive
                ? "세션 자동기록 중입니다. 영상과 글래스 음성을 세션 종료 시 자동 처리합니다."
                : "세션 자동기록 중입니다. 영상은 기록되고 글래스 마이크 연결 후 음성이 추가됩니다."
        }
        switch liveStage {
        case .uploading:
            return "캡처를 전송하고 있습니다. 잠시만 기다려주세요."
        case .analyzing:
            return "자동 기록을 만들고 있습니다."
        case .recording:
            return isGuidedModeActive
                ? "안경 세션 녹화 중입니다. 종료하면 바로 저장하거나 분석할 수 있습니다."
                : "녹화 중입니다. 종료하면 영상 리뷰에서 저장하거나 분석할 수 있습니다."
        case .live:
            return isGuidedModeActive
                ? "라이브 세션 중입니다. 가운데는 촬영, 오른쪽은 녹화와 종료입니다."
                : "가운데 버튼은 사진 촬영, 오른쪽은 녹화와 종료입니다."
        case .completed:
            return "차트가 생성되었습니다. 라벨을 붙이거나 차트를 확인하세요."
        case .ready:
            if isGuidedModeActive {
                return "안경 HUD가 켜져 있습니다. 환자를 확인한 뒤 시작하세요."
            }
            if currentPatient == nil {
                return "안경 HUD가 켜져 있습니다. 환자를 선택한 뒤 시작하세요."
            }
            return "안경 HUD가 켜져 있습니다. 가운데 시작 버튼으로 촬영을 시작하세요."
        case .standby:
            break
        }

        if DemoConfig.isGlassDemoEnabled {
            if DemoConfig.usesMaskedCaptureFrame {
                return "데모 모드: 실제 마스킹 촬영 결과를 라이브 프레임처럼 보여줍니다."
            }
            return "데모 모드: 스마트 글라스 연결과 라이브 프레임 수신 흐름을 보여줍니다."
        }
        if vm.recordedVideoURL != nil {
            return "녹화 영상이 준비되었습니다. 저장하거나 전송하세요."
        }
        if !vm.hasActiveDevice {
            return "스마트 글라스 없이 iPhone 카메라로 촬영할 수 있습니다."
        }
        if currentPatient == nil {
            return glassHUD.isDisplayConnected
                ? "안경 HUD가 켜져 있습니다. 환자를 선택한 뒤 시작하세요."
                : "오른쪽 HUD 버튼으로 화면을 켜고, 환자를 선택한 뒤 시작하세요."
        }
        if glassHUD.isDisplayConnected {
            return "안경 HUD가 켜져 있습니다. 가운데 시작 버튼으로 촬영을 시작하세요."
        }
        return "스마트 글라스 연결 상태를 확인하고, 오른쪽 HUD 버튼으로 화면을 켜세요."
    }

    private var centerButtonTitle: String {
        if vm.isStreaming {
            return "촬영"
        }
        return vm.hasActiveDevice ? "시작" : "폰촬영"
    }

    private var standbyHudButtonTitle: String {
        glassHUD.isDisplayConnected ? "준비됨" : "HUD"
    }

    private var controlBarBottomPadding: CGFloat {
        isGuidedModeActive ? 14 : 96
    }

    private var videoActionButtonTitle: String {
        isAnalyzing ? "분석중" : "전송"
    }

    private func openPhoneCamera() {
        guard UIImagePickerController.isSourceTypeAvailable(.camera) else {
            showToast("iPhone 카메라를 사용할 수 없습니다")
            return
        }
        saveStatus = .idle
        lastEventId = nil
        analysisText = ""
        showPhoneCamera = true
    }

    private func prepareGlassesDisplay() async {
        await vm.prepareStandbyDisplay(patientName: currentPatient?.name)
        if glassHUD.isDisplayConnected {
            showToast(vm.hasActiveDevice ? "안경 화면을 준비하고 있습니다" : "안경 HUD 미리보기")
        } else {
            showToast("안경 연결을 찾는 중입니다")
        }
    }

    private func finishVisitSession() async {
        guard bridgeVm.visitSession?.status == "active" else { return }
        isEndingVisitSession = true
        defer { isEndingVisitSession = false }

        if sessionAutoCaptureActive || sessionAutoAudioCaptureActive {
            await stopAutomaticSessionCapture()
        }

        if let processingTask = automaticCaptureProcessingTask {
            await processingTask.value
            automaticCaptureProcessingTask = nil
        }

        // Audio is finalized by stopAutomaticSessionCapture. This guard also
        // covers an unexpected stream termination path that is still waiting
        // for consent-gated transcription to settle.
        if isTranscribing {
            showToast("음성 기록 처리 중입니다. 잠시 후 다시 종료하세요")
            return
        }

        await bridgeVm.endVisitSession()

        if bridgeVm.visitSession?.status == "ended" {
            showToast("방문 세션 종료 · 진행 노트 초안 생성")
            await GlassHUDManager.shared.showSuccess(
                title: "세션 종료",
                body: "iPhone에서 노트 초안을 검토하세요."
            )
        } else if !bridgeVm.visitStatusMessage.isEmpty {
            showToast(bridgeVm.visitStatusMessage)
        }
    }

    private func runWithConsent(_ kind: PendingConsentAction.Kind) async {
        guard let patient = currentPatient else {
            showToast("환자를 먼저 선택하세요")
            showPatientPicker = true
            return
        }

        do {
            if try await bridgeVm.client.hasActiveConsent(patientName: patient.name) {
                await runConsentAction(kind)
            } else {
                pendingConsentAction = PendingConsentAction(patientName: patient.name, kind: kind)
            }
        } catch {
            pendingConsentAction = PendingConsentAction(patientName: patient.name, kind: kind)
        }
    }

    private func recordConsentAndContinue(_ action: PendingConsentAction) async {
        do {
            try await bridgeVm.client.recordConsent(patientName: action.patientName)
            showToast("동의 기록 완료")
            await runConsentAction(action.kind)
        } catch {
            showToast("동의 기록 실패: \(bridgeErrorMessage(error))")
            UINotificationFeedbackGenerator().notificationOccurred(.error)
        }
    }

    private func runConsentAction(_ kind: PendingConsentAction.Kind) async {
        switch kind {
        case .photo(let image):
            await analyzeAndSend(image)
        case .video(let url):
            await uploadVideo(url)
        case .audio(let url):
            await transcribeAudio(fileURL: url)
        case .sessionAutoCapture:
            sessionAutoCaptureEnabled = true
            await startAutomaticSessionCaptureIfNeeded()
        }
    }

    private func analyzeAndSend(_ image: UIImage) async {
        isAnalyzing = true
        processingPhase = .uploading
        analysisText = "Vision 분석 중..."
        await GlassHUDManager.shared.showUploading(patient: currentPatient?.name)

        let result = await ImageAnalyzer.analyze(image)
        var displayParts = [result.summary]
        if let pose = result.pose { displayParts.append(pose.summary) }
        analysisText = displayParts.joined(separator: "\n")
        processingPhase = .analyzing
        await GlassHUDManager.shared.showAnalyzing(patient: currentPatient?.name)

        let patientTag = currentPatient.map { "환자: \($0.name)" } ?? "환자: 미지정"
        var descParts = ["[\(photoSource.analysisTitle) 캡처 분석]", patientTag]

        // STT 음성 메모가 있으면 S> 섹션 힌트로 포함
        if !sttText.isEmpty {
            descParts.append("[치료사 음성 메모 — S> 섹션 참고]\n\(sttText)")
        }

        descParts.append(result.summary)
        if let pose = result.pose { descParts.append(pose.summary) }
        descParts.append("위 이미지와 음성 메모를 참고해 임상 차트를 작성해주세요.")
        let description = descParts.joined(separator: "\n")

        do {
            let resp = try await bridgeVm.client.uploadImage(
                image,
                description: description,
                patientName: currentPatient?.name,
                source: photoSource.uploadSource
            )
            lastEventId = resp.event_id
            await bridgeVm.attachVisitEventIfActive(resp.event_id)
            analysisText += "\n✅ 차트 생성 완료"
            bridgeVm.markDone()
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            showToast("✅ 차트 저장됨 — 오른쪽 📄 버튼으로 보기")
            let insightBody = currentPatient.map { "환자: \($0.name)" } ?? "SOAP 노트 생성됨"
            await GlassHUDManager.shared.showSuccess(title: "차트 생성됨", body: insightBody)
        } catch {
            let errMsg = bridgeErrorMessage(error)
            analysisText += "\n⚠️ 업로드 실패 → 텍스트 전송\n\(errMsg)"
            bridgeVm.sendText(description, patientName: currentPatient?.name)
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            showToast("⚠️ 업로드 실패")
            await GlassHUDManager.shared.showError(title: "업로드 실패", body: errMsg)
        }

        isAnalyzing = false
        processingPhase = .none
    }

    private func saveCurrentPhoto(triggeredAutomatically: Bool = false) async {
        saveStatus = .saving(triggeredAutomatically ? "사진 자동 저장 중..." : "사진 저장 중...")
        do {
            let capture = try await vm.saveCapturedPhoto(patientName: currentPatient?.name)
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            let pathText = capture.relativePath
            saveStatus = .saved("사진 저장 완료")
            showToast("✅ 사진 앱 저장 완료 · \(pathText)")
        } catch {
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            saveStatus = .failed("사진 저장 실패")
            showToast("⚠️ 사진 저장 실패")
            handleSaveError(error)
        }
    }

    @discardableResult
    private func saveCurrentVideo(triggeredAutomatically: Bool = false) async -> Bool {
        saveStatus = .saving(triggeredAutomatically ? "영상 자동 저장 중..." : "영상 저장 중...")
        do {
            let capture = try await vm.saveRecordedVideo(patientName: currentPatient?.name)
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            saveStatus = .saved("영상 저장 완료")
            showToast("✅ 영상 저장 완료 · \(capture.relativePath)")
            return true
        } catch {
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            saveStatus = .failed("영상 저장 실패")
            showToast("⚠️ 영상 저장 실패")
            handleSaveError(error)
            return false
        }
    }

    private func processHandsFreeRecordedVideo(_ url: URL) async {
        defer { automaticCaptureProcessingTask = nil }
        let saveSucceeded = await saveCurrentVideo(triggeredAutomatically: true)
        if !saveSucceeded {
            showToast("⚠️ 영상 저장은 실패했지만 업로드는 계속합니다")
        }

        guard currentPatient != nil else {
            showToast("환자를 먼저 선택해야 자동 업로드할 수 있습니다")
            showVideoSheet = true
            return
        }

        await runWithConsent(.video(url))
    }

    private func handleGlassCaptureToggleNotification(_ note: Notification) {
        let source = GlassRecordToggleSource(notification: note)
        Task {
            await handleGlassCaptureToggle(source)
        }
    }

    private func handleGlassCaptureToggle(_ source: GlassRecordToggleSource) async {
        guard currentPatient != nil else {
            await handleGlassPatientPickerRequested()
            return
        }
        await toggleRecording(triggeredBy: .glass(source))
    }

    private func handleGlassRecordingStateNotification(_ note: Notification) {
        guard let recording = note.userInfo?["recording"] as? Bool else { return }
        let source = GlassRecordToggleSource(notification: note)
        Task {
            await handleGlassRecordingState(recording, source: source)
        }
    }

    private func handleGlassRecordingState(_ recording: Bool, source: GlassRecordToggleSource) async {
        guard currentPatient != nil else {
            await handleGlassPatientPickerRequested()
            return
        }

        if recording {
            guard !vm.recorder.isRecording else { return }
            if !vm.isStreaming {
                await handleGlassStandbyStartRequested()
            }
            guard vm.isStreaming else { return }
            handsFreeRecordingSession = source.isHandsFreeGlassPath
            vm.startRecording()
            showToast("🔴 \(source.toastLabel) · 녹화 시작")
        } else {
            guard vm.recorder.isRecording else { return }
            await toggleRecording(triggeredBy: .glass(source))
        }
    }

    private func handleGlassPhotoCaptureNotification() {
        Task {
            guard currentPatient != nil else {
                await handleGlassPatientPickerRequested()
                return
            }
            if !vm.isStreaming {
                await handleGlassStandbyStartRequested()
            }
            guard vm.isStreaming else { return }
            isCapturing = true
            photoSource = .rayban
            vm.capturePhoto()
            try? await Task.sleep(nanoseconds: 120_000_000)
            isCapturing = false
        }
    }

    private func handleGlassAudioStartNotification() {
        Task {
            guard currentPatient != nil else {
                await handleGlassPatientPickerRequested()
                return
            }
            if audioRecorder.isRecording {
                return
            }
            if sessionAutoCaptureActive {
                showToast("세션 자동기록 중입니다")
                return
            }
            await startAudioRecording()
        }
    }

    private func handleGlassAudioStopNotification() {
        Task {
            guard audioRecorder.isRecording else { return }
            await stopAndTranscribe()
        }
    }

    private func handleGlassPrimaryActionNotification(_ note: Notification) {
        let source = GlassRecordToggleSource(notification: note)
        Task {
            await handleGlassPrimaryAction(source)
        }
    }

    private func handleGlassPrimaryAction(_ source: GlassRecordToggleSource) async {
        guard currentPatient != nil else {
            await handleGlassPatientPickerRequested()
            return
        }
        if vm.isStreaming {
            await toggleRecording(triggeredBy: .glass(source))
        } else {
            await handleGlassStandbyStartRequested()
        }
    }

    private func handleGlassStandbyStartNotification() {
        Task {
            await handleGlassStandbyStartRequested()
        }
    }

    private func handleOpenCaptureHistoryNotification(_ note: Notification) {
        Task {
            await handleOpenCaptureHistoryRequested(note)
        }
    }

    private func handleGlassPatientPickerNotification() {
        Task {
            await handleGlassPatientPickerRequested()
        }
    }

    private func handleGlassPatientSelectedNotification(_ note: Notification) {
        let name = (note.userInfo?["patient_name"] as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !name.isEmpty else { return }
        currentPatient = store.touch(name: name)
        showPatientPicker = false
        showToast("환자 선택: \(name)")
        Task {
            if vm.isStreaming {
                await GlassHUDManager.shared.updateContextPatient(name)
            } else {
                await vm.prepareStandbyDisplay(patientName: name)
            }
        }
    }

    private func handleGlassRecommendedAssessmentNotification() {
        Task {
            await handleGlassRecommendedAssessmentRequested()
        }
    }

    private func handleGlassAssessmentSelectedNotification(_ note: Notification) {
        let assessment = (note.userInfo?["assessment"] as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !assessment.isEmpty else { return }
        showToast("추천 평가: \(assessment)")
        Task {
            await GlassHUDManager.shared.selectAssessment(
                assessment,
                patient: currentPatient?.name
            )
        }
    }

    private func handleGlassPatientPickerRequested() async {
        showPatientPicker = true
        showToast("환자를 선택하세요")
        await GlassHUDManager.shared.showPatientSelection(
            current: currentPatient?.name,
            candidates: store.recent.map(\.name)
        )
    }

    private func handleGlassRecommendedAssessmentRequested() async {
        showToast("추천 평가 확인")
        await GlassHUDManager.shared.showRecommendations(patient: currentPatient?.name)
    }

    private func handleGlassStandbyStartRequested() async {
        guard currentPatient != nil else {
            await handleGlassPatientPickerRequested()
            return
        }
        if vm.isStreaming {
            showToast("🟢 이미 라이브 연결됨")
            return
        }
        if !vm.hasActiveDevice {
            showToast("⚠️ 안경 연결이 먼저 필요합니다")
            return
        }
        saveStatus = .idle
        showToast("🟢 안경 HUD · 라이브 시작")
        await vm.startStreaming()
    }

    private func handleSessionAutoCaptureSettingChanged(_ enabled: Bool) {
        Task {
            if enabled {
                guard currentPatient != nil else {
                    sessionAutoCaptureEnabled = false
                    showPatientPicker = true
                    showToast("환자를 먼저 선택하세요")
                    return
                }
                await ensureVisitSessionForCurrentPatient()
                await startAutomaticSessionCaptureIfNeeded()
            } else {
                await stopAutomaticSessionCapture()
            }
        }
    }

    private func startAutomaticSessionCaptureIfNeeded() async {
        guard sessionAutoCaptureEnabled,
              !sessionAutoCaptureActive,
              currentPatient != nil,
              vm.isStreaming
        else { return }

        if bridgeVm.visitSession?.status != "active" {
            await ensureVisitSessionForCurrentPatient()
            guard bridgeVm.visitSession?.status == "active" else {
                showToast("방문 세션을 시작할 수 없어 자동기록을 대기합니다")
                return
            }
        }

        do {
            guard try await bridgeVm.client.hasActiveConsent(patientName: currentPatient?.name ?? "") else {
                sessionAutoCaptureEnabled = false
                showToast("자동기록은 환자 동의 확인 후 사용할 수 있습니다")
                print("[SessionAutoCapture] blocked reason=active_consent_missing")
                if let patient = currentPatient {
                    pendingConsentAction = PendingConsentAction(
                        patientName: patient.name,
                        kind: .sessionAutoCapture
                    )
                }
                await GlassHUDManager.shared.showError(
                    title: "동의 확인 필요",
                    body: "촬영·녹음·분석 동의를 먼저 기록해 주세요."
                )
                return
            }
        } catch {
            showToast("동의 상태를 확인할 수 없습니다")
            await GlassHUDManager.shared.showError(title: "자동기록 대기", body: bridgeErrorMessage(error))
            return
        }

        sessionAutoCaptureActive = true
        handsFreeRecordingSession = true
        vm.startRecording()
        showToast("🔴 세션 자동기록 시작 · 영상 준비 중")

        // HFP route가 실제로 선택되는 경우에만 음성 기록을 유지한다.
        // 글래스 마이크가 없으면 영상 세션은 계속되고, 음성만 안전하게 건너뛴다.
        await startAudioRecording(preservingGlassesStream: true)
        sessionAutoAudioCaptureActive = audioRecorder.isRecording
        if sessionAutoAudioCaptureActive {
            showToast("🔴 세션 자동기록 · 영상+글래스 음성")
        } else {
            showToast("🔴 세션 자동기록 · 영상만 기록 중")
        }
    }

    private func stopAutomaticSessionCapture(processAutomatically: Bool = true) async {
        guard sessionAutoCaptureActive || sessionAutoAudioCaptureActive else { return }

        sessionAutoCaptureActive = false
        let shouldStopAudio = sessionAutoAudioCaptureActive
        sessionAutoAudioCaptureActive = false

        if vm.recorder.isRecording, handsFreeRecordingSession {
            handsFreeRecordingSession = false
            shouldAutoProcessNextRecordedVideo = processAutomatically
            await vm.stopRecording()
        }

        if shouldStopAudio {
            await stopAndTranscribe()
        }
    }

    private func handleOpenCaptureHistoryRequested(_ note: Notification? = nil) async {
        let shouldOpenPhone = (note?.userInfo?["open_phone"] as? String) == "true"
        if shouldOpenPhone {
            showCaptureHistory = true
            showToast("저장 기록 상세")
            return
        }

        let summaries = captureHistorySummaries()
        await GlassHUDManager.shared.showCaptureHistory(
            patient: currentPatient?.name,
            summaries: summaries
        )
        showToast(summaries.isEmpty ? "HUD 기록 없음" : "HUD 기록 보기")
    }

    private func captureHistorySummaries() -> [String] {
        captureStore.captures.prefix(3).map { capture in
            let media = capture.mediaType == .photo ? "사진" : "영상"
            let patient = capture.patientName?.trimmingCharacters(in: .whitespacesAndNewlines)
            let patientLabel = (patient?.isEmpty == false) ? patient! : "환자 미지정"
            return "\(media) · \(patientLabel) · \(Self.captureHistoryTimeFormatter.string(from: capture.createdAt))"
        }
    }

    private func toggleRecording(triggeredBy source: RecordingTriggerSource) async {
        if vm.recorder.isRecording {
            let shouldAutoProcess = handsFreeRecordingSession
            handsFreeRecordingSession = false
            if shouldAutoProcess {
                shouldAutoProcessNextRecordedVideo = true
            }
            await vm.stopRecording()
            if shouldAutoProcess {
                return
            }
            if autoSaveCaptures {
                await saveCurrentVideo(triggeredAutomatically: true)
            } else {
                saveStatus = .saved("영상 저장 준비 완료")
                showToast(source.completionToastMessage)
            }
        } else {
            saveStatus = .idle
            handsFreeRecordingSession = source.shouldAutoProcess
            vm.startRecording()
            showToast(source.startToastMessage)
        }
    }

    private func stopStreamingFlow() async {
        let wasRecording = vm.recorder.isRecording
        let shouldAutoProcess = wasRecording && handsFreeRecordingSession
        let shouldFinishAutomaticAudio = sessionAutoAudioCaptureActive
        sessionAutoCaptureActive = false
        sessionAutoAudioCaptureActive = false
        if shouldAutoProcess {
            shouldAutoProcessNextRecordedVideo = true
        }
        handsFreeRecordingSession = false
        await vm.stopStreaming()
        if shouldFinishAutomaticAudio {
            await stopAndTranscribe()
        }
        if isGuidedModeActive {
            glassExperience.endGuidedMode()
        }
        if shouldAutoProcess {
            return
        }
        if wasRecording, autoSaveCaptures, vm.recordedVideoURL != nil {
            await saveCurrentVideo(triggeredAutomatically: true)
        } else if wasRecording, vm.recordedVideoURL != nil {
            saveStatus = .saved("영상 저장 준비 완료")
            showToast("📼 영상 캡처 완료 — 업로드 또는 저장 가능")
        }
    }

    private func uploadVideo(_ url: URL) async {
        isAnalyzing = true
        processingPhase = .uploading
        do {
            await GlassHUDManager.shared.showUploading(patient: currentPatient?.name)
            // 1) 업로드 → outer event_id 수신
            let accepted = try await bridgeVm.client.uploadVideo(
                fileURL: url,
                patientName: currentPatient?.name,
                source: videoCaptureOrigin
            )
            lastEventId = accepted.event_id          // 우선 outer id로 차트 버튼 활성화
            analysisText = "영상 분석 업로드 완료\n⚙️ 처리 중... (\(accepted.size_kb ?? 0)KB)"
            showToast("⚙️ 영상 분석 중...")
            processingPhase = .analyzing
            await GlassHUDManager.shared.showAnalyzing(patient: currentPatient?.name)

            // 2) 백그라운드 폴링 — 완료 시 toast 업데이트 (최대 60초)
            Task {
                let final = try? await bridgeVm.client.waitUntilDone(
                    eventId: accepted.event_id,
                    maxTries: 60,
                    intervalSec: 1.0
                )
                if final?.status == "done" {
                    let patientName = currentPatient?.name
                    let insightBody = patientName.map { "환자: \($0)" } ?? "SOAP 노트 생성됨"
                    await GlassHUDManager.shared.showSuccess(title: "차트 생성됨", body: insightBody)
                }
                await MainActor.run {
                    if final?.status == "done" {
                        // inner event_id가 있으면 교체 (차트 파일이 거기에 있음)
                        if let innerId = final?.result?.event?.id {
                            lastEventId = innerId
                            Task {
                                await bridgeVm.attachVisitEventIfActive(innerId)
                                await bridgeVm.recordMediaCaptureEvent(
                                    eventId: innerId,
                                    sourceType: "video",
                                    candidateType: "video_evidence",
                                    fileName: url.lastPathComponent,
                                    captureOrigin: videoCaptureOrigin
                                )
                            }
                        }
                        analysisText = "✅ 영상 분석 완료\n📄 차트 생성됨"
                        UINotificationFeedbackGenerator().notificationOccurred(.success)
                        showToast("📄 차트 생성 완료 — 버튼을 눌러 확인하세요")
                    } else if final?.status == "error" {
                        let message = UserFacingError.message(code: final?.error_code, fallback: final?.error)
                        analysisText = "⚠️ 영상 분석 실패\n\(message)"
                        showToast("⚠️ 처리 실패")
                        Task {
                            await GlassHUDManager.shared.showError(title: "분석 실패", body: message)
                        }
                    }
                    processingPhase = .none
                    // timeout이면 lastEventId(outer) 유지 — 서버 측 차트 복사로 조회 가능
                }
            }
        } catch {
            let message = bridgeErrorMessage(error)
            analysisText = "⚠️ 영상 업로드 실패\n\(message)"
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            showToast("⚠️ 업로드 실패")
            await GlassHUDManager.shared.showError(title: "업로드 실패", body: message)
            processingPhase = .none
        }
        isAnalyzing = false
    }

    // MARK: - STT

    private func startAudioRecording(preservingGlassesStream: Bool = false) async {
        guard preservingGlassesStream || !sessionAutoCaptureActive else {
            showToast("세션 자동기록 중입니다")
            return
        }
        let shouldResumeGlassesStream = vm.isStreaming
        if shouldResumeGlassesStream && !preservingGlassesStream {
            if vm.recorder.isRecording {
                showToast("영상 녹화를 먼저 중지하세요")
                return
            }
            await vm.stopStreaming()
        }
        let audioStarted = await audioRecorder.startRecording()
        await GlassHUDManager.shared.setAudioMemoRecording(audioStarted)
        if !audioStarted, preservingGlassesStream {
            showToast("글래스 마이크를 찾지 못해 영상만 기록합니다")
        }
        if shouldResumeGlassesStream && !preservingGlassesStream {
            await vm.startStreaming()
        }
    }

    private func stopAndTranscribe() async {
        audioRecorder.stopRecording()
        await GlassHUDManager.shared.setAudioMemoRecording(false)
        guard let fileURL = audioRecorder.recordedFileURL else { return }
        await runWithConsent(.audio(fileURL))
    }

    private func transcribeAudio(fileURL: URL) async {
        isTranscribing = true
        processingPhase = .uploading

        do {
            await GlassHUDManager.shared.showUploading(patient: currentPatient?.name)
            let accepted = try await bridgeVm.client.uploadAudio(
                fileURL: fileURL,
                patientName: currentPatient?.name,
                source: "rayban_hfp_microphone"
            )
            processingPhase = .analyzing
            await GlassHUDManager.shared.showAnalyzing(patient: currentPatient?.name)
            let final = try await bridgeVm.client.waitUntilDone(eventId: accepted.event_id)
            if let eventId = final.eventId {
                await bridgeVm.attachVisitEventIfActive(eventId)
                await bridgeVm.recordMediaCaptureEvent(
                    eventId: eventId,
                    sourceType: "audio",
                    candidateType: "transcript",
                    fileName: fileURL.lastPathComponent,
                    captureOrigin: "rayban_hfp_microphone"
                )
            }
            let transcript = final.result?.event?.raw_text ?? ""

            if transcript.isEmpty {
                showToast("🎙 변환 결과 없음")
                await GlassHUDManager.shared.showError(title: "음성 결과 없음", body: "다시 녹음해 주세요.")
            } else {
                withAnimation {
                    sttText = sttText.isEmpty ? transcript : sttText + "\n" + transcript
                }
                UINotificationFeedbackGenerator().notificationOccurred(.success)
                showToast("🎙 변환 완료")
                await GlassHUDManager.shared.showSuccess(title: "음성 기록됨", body: "SOAP 초안에 반영할 준비가 됐습니다.")
                await stageHudCandidateIfPossible(transcript: transcript)
            }
        } catch {
            let message = bridgeErrorMessage(error)
            showToast("⚠️ 변환 실패: \(message)")
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            await GlassHUDManager.shared.showError(title: "음성 변환 실패", body: message)
        }

        isTranscribing = false
        processingPhase = .none
    }

    private func stageHudCandidateIfPossible(transcript: String) async {
        guard !bridgeVm.client.physioSessionId.isEmpty,
              !bridgeVm.client.subjectPersonId.isEmpty,
              !bridgeVm.client.ownerOrgId.isEmpty,
              !bridgeVm.client.ownerProviderPersonId.isEmpty
        else { return }

        do {
            let response = try await bridgeVm.client.extractHudCandidate(
                fromTranscript: transcript,
                source: "ios_audio_transcript",
                createCandidate: true
            )
            guard response.status == "done", let candidate = response.candidate else { return }

            let parts = [candidate.test, candidate.side, candidate.value]
                .compactMap { value -> String? in
                    guard let value, !value.isEmpty else { return nil }
                    return value
                }
            let summary = parts.isEmpty ? "승인 대기" : parts.joined(separator: " / ")
            showToast("기록 후보 생성: \(summary)")
            await GlassHUDManager.shared.showSuccess(title: "기록 후보", body: summary)
            await refreshHudCandidates()
        } catch {
            // Candidate extraction is advisory. Keep the completed STT note available.
            print("HUD candidate extraction skipped: \(bridgeErrorMessage(error))")
        }
    }

    private func refreshHudCandidates() async {
        guard !bridgeVm.client.physioSessionId.isEmpty else {
            withAnimation { hudCandidates = [] }
            return
        }

        isLoadingHudCandidates = true
        defer { isLoadingHudCandidates = false }

        do {
            let response = try await bridgeVm.client.listHudCandidates(status: "candidate", limit: 5)
            withAnimation {
                hudCandidates = response.items.filter { $0.id?.isEmpty == false }
            }
        } catch {
            print("HUD candidate refresh failed: \(bridgeErrorMessage(error))")
        }
    }

    private func decideHudCandidate(_ candidate: BridgeClient.HudCandidate, approve: Bool) async {
        guard let candidateId = candidate.id, !candidateId.isEmpty else { return }
        hudCandidateDecisionId = candidateId
        defer { hudCandidateDecisionId = nil }

        do {
            if approve {
                _ = try await bridgeVm.client.approveHudCandidate(candidateId)
                showToast("기록 후보 승인됨")
                UINotificationFeedbackGenerator().notificationOccurred(.success)
                await GlassHUDManager.shared.showSuccess(title: "후보 승인", body: hudCandidateSummary(candidate))
            } else {
                _ = try await bridgeVm.client.discardHudCandidate(candidateId)
                showToast("기록 후보 폐기됨")
                UINotificationFeedbackGenerator().notificationOccurred(.warning)
            }
            await refreshHudCandidates()
        } catch {
            let message = bridgeErrorMessage(error)
            showToast("후보 처리 실패: \(message)")
            UINotificationFeedbackGenerator().notificationOccurred(.error)
        }
    }

    private func hudCandidateSummary(_ candidate: BridgeClient.HudCandidate) -> String {
        let parts = [candidate.test, candidate.side, candidate.value]
            .compactMap { value -> String? in
                guard let value, !value.isEmpty else { return nil }
                return value
            }
        return parts.isEmpty ? "후보" : parts.joined(separator: " / ")
    }

    private func recordCaptureTag(_ option: CaptureTagOption) async {
        guard let session = bridgeVm.visitSession else {
            showToast("방문 세션을 먼저 시작하세요")
            return
        }
        do {
            _ = try await bridgeVm.client.createCaptureEvent(
                visitSessionId: session.id,
                encounterId: session.encounter_id,
                eventType: option.eventType,
                sourceType: "therapist_tag",
                candidateType: option.eventType,
                startMs: sessionElapsedMilliseconds(session.started_at),
                confidence: 1,
                status: "draft",
                payload: [
                    "label": option.title,
                    "phase": session.phase,
                    "surface": "ios_stream_view"
                ]
            )
            showCaptureTagSheet = false
            showToast("시간축 이벤트 기록: \(option.title)")
        } catch {
            showToast("이벤트 기록 실패: \(bridgeErrorMessage(error))")
        }
    }

    private func sessionElapsedMilliseconds(_ startedAt: String?) -> Int? {
        guard let startedAt,
              let startedDate = ISO8601DateFormatter().date(from: startedAt)
        else { return nil }
        return max(0, Int(Date().timeIntervalSince(startedDate) * 1000))
    }

    private func showToast(_ message: String) {
        toastTask?.cancel()
        withAnimation(.spring(response: 0.3)) { toastMessage = message }
        toastTask = Task {
            try? await Task.sleep(nanoseconds: 2_500_000_000)
            guard !Task.isCancelled else { return }
            withAnimation { toastMessage = nil }
        }
    }

    private func bridgeErrorMessage(_ error: Error) -> String {
        UserFacingError.message(for: error)
    }

    private func handleSaveError(_ error: Error) {
        if let mediaError = error as? MediaSaveError, mediaError == .libraryAccessDenied {
            photoPermissionMessage = mediaError.localizedDescription
            showPhotoPermissionAlert = true
        }
    }

    private func openAppSettings() {
        guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
        UIApplication.shared.open(url)
    }

    private var captureOptionsMenu: some View {
        Menu {
            Toggle("촬영 즉시 저장", isOn: $autoSaveCaptures)
            Toggle("세션 자동기록", isOn: $sessionAutoCaptureEnabled)
            Text("동의된 환자의 글래스 영상·음성을 라이브 시작부터 세션 종료까지 기록합니다.")
            Picker("세션 직군", selection: $providerRole) {
                Text("물리치료사").tag("physical_therapist")
                Text("작업치료사").tag("occupational_therapist")
                Text("필라테스 강사").tag("pilates_instructor")
                Text("퍼스널 트레이너").tag("personal_trainer")
                Text("보호자").tag("caregiver")
                Text("기타").tag("other")
            }
            Button("저장 기록 보기") {
                showCaptureHistory = true
            }
            if let latest = captureStore.captures.first {
                Section("최근 저장") {
                    Text(latest.fileName)
                    if let patient = latest.patientName {
                        Text(patient)
                    }
                }
            }
        } label: {
            Image(systemName: autoSaveCaptures ? "externaldrive.badge.checkmark" : "externaldrive.badge.plus")
                .foregroundStyle(.white)
        }
    }
}

// MARK: - 카메라 빈 상태

private struct EmptyCameraState: View {
    let isStreaming: Bool
    let hasActiveDevice: Bool

    var body: some View {
        VStack(spacing: 18) {
            SmartGlassMark(isStreaming: isStreaming, hasActiveDevice: hasActiveDevice)

            VStack(spacing: 8) {
                Text(title)
                    .font(.system(size: 24, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)
                    .multilineTextAlignment(.center)

                Text(message)
                    .font(.system(size: 13, weight: .medium, design: .rounded))
                    .foregroundStyle(.white.opacity(0.66))
                    .multilineTextAlignment(.center)
                    .lineLimit(3)
                    .frame(maxWidth: 280)
            }

            HStack(spacing: 8) {
                CapabilityChip(title: hasActiveDevice ? "Glass" : "iPhone", iconName: hasActiveDevice ? "eyeglasses" : "iphone")
                CapabilityChip(title: "Voice", iconName: "waveform")
                CapabilityChip(title: "Chart", iconName: "doc.text")
            }
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 30)
        .background {
            RoundedRectangle(cornerRadius: 26, style: .continuous)
                .fill(.ultraThinMaterial.opacity(0.62))
                .overlay {
                    RoundedRectangle(cornerRadius: 26, style: .continuous)
                        .stroke(
                            LinearGradient(
                                colors: [
                                    Color.white.opacity(0.28),
                                    DS.ColorToken.electric.opacity(0.22),
                                    Color.white.opacity(0.06)
                                ],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            ),
                            lineWidth: 1
                        )
                }
                .shadow(color: DS.ColorToken.primary.opacity(0.22), radius: 34, y: 20)
        }
        .padding(.horizontal, 26)
    }

    private var title: String {
        if isStreaming { return "프레임 수신 대기 중" }
        return hasActiveDevice ? "Kinelo AR Ready" : "iPhone Camera Ready"
    }

    private var message: String {
        if isStreaming { return "잠시 후 카메라 프레임이 표시됩니다." }
        if hasActiveDevice { return "환자를 선택하고 시작 버튼을 누르면 현장 입력이 시작됩니다." }
        return "스마트 글라스 없이도 폰촬영으로 기록을 만들 수 있습니다."
    }
}

private struct SmartGlassMotionBackdrop: View {
    var body: some View {
        TimelineView(.animation) { timeline in
            let time = timeline.date.timeIntervalSinceReferenceDate
            Canvas { context, size in
                let rect = CGRect(origin: .zero, size: size)
                context.fill(
                    Path(rect),
                    with: .linearGradient(
                        Gradient(colors: [
                            DS.ColorToken.midnight,
                            Color(red: 0.035, green: 0.055, blue: 0.12),
                            Color(red: 0.02, green: 0.035, blue: 0.08)
                        ]),
                        startPoint: .zero,
                        endPoint: CGPoint(x: size.width, y: size.height)
                    )
                )

                drawGlow(
                    context: &context,
                    size: size,
                    time: time,
                    offset: 0,
                    color: DS.ColorToken.electric.opacity(0.28)
                )
                drawGlow(
                    context: &context,
                    size: size,
                    time: time,
                    offset: 2.1,
                    color: DS.ColorToken.violet.opacity(0.24)
                )
                drawGlow(
                    context: &context,
                    size: size,
                    time: time,
                    offset: 4.2,
                    color: DS.ColorToken.success.opacity(0.16)
                )
            }
            .overlay {
                LinearGradient(
                    colors: [
                        Color.black.opacity(0.10),
                        Color.black.opacity(0.44)
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
        }
    }

    private func drawGlow(
        context: inout GraphicsContext,
        size: CGSize,
        time: TimeInterval,
        offset: Double,
        color: Color
    ) {
        let x = size.width * (0.5 + 0.34 * CGFloat(sin(time * 0.18 + offset)))
        let y = size.height * (0.5 + 0.22 * CGFloat(cos(time * 0.15 + offset)))
        let radius = max(size.width, size.height) * 0.42
        let rect = CGRect(x: x - radius, y: y - radius, width: radius * 2, height: radius * 2)

        context.addFilter(.blur(radius: 38))
        context.fill(
            Path(ellipseIn: rect),
            with: .radialGradient(
                Gradient(colors: [color, .clear]),
                center: CGPoint(x: x, y: y),
                startRadius: 0,
                endRadius: radius
            )
        )
    }
}

private struct SmartGlassMark: View {
    let isStreaming: Bool
    let hasActiveDevice: Bool

    var body: some View {
        TimelineView(.animation) { timeline in
            let time = timeline.date.timeIntervalSinceReferenceDate
            let pulse = 1 + 0.045 * CGFloat(sin(time * 2.2))

            ZStack {
                Circle()
                    .fill(DS.ColorToken.electric.opacity(0.12))
                    .frame(width: 138, height: 138)
                    .blur(radius: 10)
                    .scaleEffect(pulse)

                Circle()
                    .stroke(DS.ColorToken.electric.opacity(0.20), lineWidth: 1)
                    .frame(width: 118, height: 118)
                    .rotationEffect(.degrees(time * 12))

                Image(systemName: iconName)
                    .font(.system(size: 48, weight: .semibold))
                    .foregroundStyle(
                        LinearGradient(
                            colors: [.white, DS.ColorToken.electric, DS.ColorToken.violet],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .symbolEffect(.pulse, options: .repeating, value: isStreaming)
            }
            .frame(width: 142, height: 142)
        }
    }

    private var iconName: String {
        if isStreaming { return "camera.metering.unknown" }
        return hasActiveDevice ? "eyeglasses" : "camera.fill"
    }
}

private struct CapabilityChip: View {
    let title: String
    let iconName: String

    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: iconName)
                .font(.system(size: 10, weight: .bold))
            Text(title)
                .font(.system(size: 11, weight: .bold, design: .rounded))
        }
        .foregroundStyle(.white.opacity(0.84))
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(Color.white.opacity(0.08), in: Capsule())
        .overlay {
            Capsule()
                .stroke(Color.white.opacity(0.10), lineWidth: 1)
        }
    }
}

// MARK: - 하단 독 버튼

private struct DockActionButton: View {
    let title: String
    let systemImage: String
    let tint: Color
    var isActive = false
    var isBusy = false
    var isDisabled = false

    var body: some View {
        VStack(spacing: 6) {
            ZStack {
                Circle()
                    .fill(backgroundColor)
                    .frame(width: 44, height: 44)
                Circle()
                    .stroke(borderColor, lineWidth: 1)
                    .frame(width: 44, height: 44)

                if isBusy {
                    ProgressView()
                        .progressViewStyle(.circular)
                        .tint(.white)
                        .scaleEffect(0.78)
                } else {
                    Image(systemName: systemImage)
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(iconColor)
                }
            }

            Text(title)
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(.white.opacity(isDisabled ? 0.34 : 0.82))
                .lineLimit(1)
                .frame(width: 52)
        }
        .frame(width: 56, height: 62)
        .opacity(isDisabled ? 0.55 : 1)
    }

    private var backgroundColor: Color {
        if isDisabled { return Color.white.opacity(0.06) }
        if isActive { return tint.opacity(0.25) }
        return DS.ColorToken.surfaceSoft
    }

    private var borderColor: Color {
        if isDisabled { return Color.white.opacity(0.08) }
        return isActive ? tint.opacity(0.85) : Color.white.opacity(0.18)
    }

    private var iconColor: Color {
        isDisabled ? Color.white.opacity(0.34) : tint
    }
}

// MARK: - 마이크 버튼 컴포넌트

private struct MicButton: View {
    let isRecording: Bool
    let isTranscribing: Bool

    var body: some View {
        ZStack {
            Circle()
                .fill(DS.ColorToken.surfaceSoft)
                .frame(width: 52, height: 52)

            Circle()
                .stroke(isRecording ? DS.ColorToken.danger : Color.white.opacity(0.45), lineWidth: 2)
                .frame(width: 52, height: 52)

            if isTranscribing {
                ProgressView()
                    .progressViewStyle(.circular)
                    .tint(.white)
                    .scaleEffect(0.8)
            } else {
                Image(systemName: isRecording ? "stop.fill" : "mic.fill")
                    .font(.system(size: isRecording ? 18 : 20))
                    .foregroundStyle(isRecording ? DS.ColorToken.danger : .white)
                    .scaleEffect(isRecording ? 1.1 : 1.0)
                    .animation(.easeInOut(duration: 0.5).repeatForever(autoreverses: true),
                               value: isRecording)
            }
        }
    }
}

// MARK: - 촬영 버튼 컴포넌트

private struct CaptureButton: View {
    let isStreaming: Bool
    let isCapturing: Bool
    var usesPhoneCameraFallback = false

    var body: some View {
        ZStack {
            if isStreaming {
                // 촬영 버튼 (흰 원)
                Circle()
                    .stroke(.white, lineWidth: 3)
                    .frame(width: 62, height: 62)
                Circle()
                    .fill(.white)
                    .frame(width: 50, height: 50)
                    .scaleEffect(isCapturing ? 0.85 : 1.0)
                    .animation(.easeOut(duration: 0.1), value: isCapturing)
            } else {
                // 스트리밍 시작
                ZStack {
                    Circle()
                        .fill(
                            LinearGradient(
                                colors: [DS.ColorToken.primaryAlt, DS.ColorToken.primary],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            )
                        )
                        .frame(width: 62, height: 62)
                        .shadow(color: DS.ColorToken.primary.opacity(0.34), radius: 10, y: 4)
                    Image(systemName: usesPhoneCameraFallback ? "camera.fill" : "play.fill")
                        .font(.system(size: 23))
                        .foregroundStyle(.white)
                        .offset(x: usesPhoneCameraFallback ? 0 : 3)
                }
                .overlay {
                    Circle()
                        .stroke(
                            LinearGradient(
                                colors: [
                                    Color.white.opacity(0.56),
                                    DS.ColorToken.electric.opacity(0.38),
                                    Color.white.opacity(0.08)
                                ],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            ),
                            lineWidth: 1
                        )
                        .frame(width: 70, height: 70)
                }
            }
        }
    }
}

// MARK: - 녹화 버튼 컴포넌트

private struct RecordButton: View {
    let isRecording: Bool

    var body: some View {
        ZStack {
            Circle()
                .stroke(.white.opacity(0.5), lineWidth: 2)
                .frame(width: 48, height: 48)
            RoundedRectangle(cornerRadius: isRecording ? 4 : 22, style: .continuous)
                .fill(DS.ColorToken.danger)
                .frame(width: isRecording ? 20 : 36, height: isRecording ? 20 : 36)
                .animation(.spring(response: 0.3, dampingFraction: 0.7), value: isRecording)
        }
    }
}

// MARK: - 촬영 리뷰 시트

private struct PhotoReviewSheet: View {
    let photo: UIImage
    @Binding var isAnalyzing: Bool
    @Binding var analysisText: String
    let saveStatus: StreamView.SaveStatus
    let onSave: () async -> Void
    let onSend: () async -> Void
    let onViewChart: () -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    // 사진
                    Image(uiImage: photo)
                        .resizable()
                        .scaledToFit()
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .shadow(color: .black.opacity(0.12), radius: 12, y: 4)
                        .padding(.horizontal, 4)

                    // 분석 결과
                    if !analysisText.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            Label("분석 결과", systemImage: "brain")
                                .font(.headline)
                                .foregroundStyle(.primary)

                            Text(analysisText)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .textSelection(.enabled)
                        }
                        .padding(16)
                        .background(Color(.secondarySystemBackground),
                                    in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .transition(.opacity.combined(with: .move(edge: .bottom)))
                    }

                    // 차트 보기 버튼 (성공 후)
                    if analysisText.contains("✅") {
                        Button {
                            onViewChart()
                        } label: {
                            Label("생성된 차트 보기", systemImage: "doc.text.fill")
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 4)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(DS.ColorToken.primary)
                        .transition(.opacity.combined(with: .scale(0.95)))
                    }

                    // 액션 버튼
                    if let message = saveStatus.message {
                        HStack(spacing: 8) {
                            Circle()
                                .fill(saveStatus.tint)
                                .frame(width: 8, height: 8)
                            Text(message)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Spacer()
                        }
                    }

                    HStack(spacing: 12) {
                        Button {
                            Task { await onSave() }
                        } label: {
                            Label("앨범 저장", systemImage: "square.and.arrow.down")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .tint(DS.ColorToken.success)
                        .disabled({
                            if case .saving = saveStatus { return true }
                            return false
                        }())

                        Button {
                            Task { await onSend() }
                        } label: {
                            Group {
                                if isAnalyzing {
                                    ProgressView()
                                        .progressViewStyle(.circular)
                                        .tint(.white)
                                } else {
                                    Label("분석 & 전송", systemImage: "brain.head.profile")
                                }
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 2)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.purple)
                        .disabled(isAnalyzing || analysisText.contains("✅"))
                    }
                }
                .padding(20)
                .animation(.spring(response: 0.35, dampingFraction: 0.8), value: analysisText)
            }
            .navigationTitle("촬영 리뷰")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("닫기") { dismiss() }
                }
            }
        }
    }
}

private struct VideoReviewSheet: View {
    let videoURL: URL
    @Binding var isAnalyzing: Bool
    @Binding var analysisText: String
    let saveStatus: StreamView.SaveStatus
    let onSave: () async -> Void
    let onSend: () async -> Void
    let onViewChart: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var player = AVPlayer()

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    VideoPlayer(player: player)
                        .frame(minHeight: 260)
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .shadow(color: .black.opacity(0.12), radius: 12, y: 4)

                    if !analysisText.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            Label("분석 상태", systemImage: "film.stack")
                                .font(.headline)
                            Text(analysisText)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .textSelection(.enabled)
                        }
                        .padding(16)
                        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }

                    if analysisText.contains("✅") {
                        Button {
                            onViewChart()
                        } label: {
                            Label("생성된 차트 보기", systemImage: "doc.text.fill")
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 4)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(DS.ColorToken.primary)
                    }

                    if let message = saveStatus.message {
                        HStack(spacing: 8) {
                            Circle()
                                .fill(saveStatus.tint)
                                .frame(width: 8, height: 8)
                            Text(message)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Spacer()
                        }
                    }

                    HStack(spacing: 12) {
                        Button {
                            Task { await onSave() }
                        } label: {
                            Label("영상 저장", systemImage: "square.and.arrow.down")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .tint(DS.ColorToken.success)
                        .disabled({
                            if case .saving = saveStatus { return true }
                            return false
                        }())

                        Button {
                            Task { await onSend() }
                        } label: {
                            Group {
                                if isAnalyzing {
                                    ProgressView()
                                        .progressViewStyle(.circular)
                                        .tint(.white)
                                } else {
                                    Label("분석 & 업로드", systemImage: "waveform.and.magnifyingglass")
                                }
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 2)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.purple)
                        .disabled(isAnalyzing || analysisText.contains("✅"))
                    }
                }
                .padding(20)
            }
            .navigationTitle("영상 리뷰")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("닫기") { dismiss() }
                }
            }
            .onAppear {
                player = AVPlayer(url: videoURL)
                player.play()
            }
            .onDisappear {
                player.pause()
            }
        }
    }
}

private struct PhoneCameraPicker: UIViewControllerRepresentable {
    let onCapture: (UIImage) -> Void
    @Environment(\.dismiss) private var dismiss

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = .camera
        picker.cameraCaptureMode = .photo
        picker.allowsEditing = false
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ uiViewController: UIImagePickerController, context: Context) {}

    func makeCoordinator() -> Coordinator {
        Coordinator(onCapture: onCapture, dismiss: dismiss)
    }

    final class Coordinator: NSObject, UINavigationControllerDelegate, UIImagePickerControllerDelegate {
        let onCapture: (UIImage) -> Void
        let dismiss: DismissAction

        init(onCapture: @escaping (UIImage) -> Void, dismiss: DismissAction) {
            self.onCapture = onCapture
            self.dismiss = dismiss
        }

        func imagePickerController(
            _ picker: UIImagePickerController,
            didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]
        ) {
            if let image = info[.originalImage] as? UIImage {
                onCapture(image)
            }
            dismiss()
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            dismiss()
        }
    }
}

private struct CaptureTagOption: Identifiable {
    let id: String
    let eventType: String
    let title: String
    let systemImage: String
    let tint: Color
}

private let captureTagOptions: [CaptureTagOption] = [
    CaptureTagOption(id: "safety_check", eventType: "safety_check", title: "안전·의학적 안정성", systemImage: "exclamationmark.shield.fill", tint: DS.ColorToken.primary),
    CaptureTagOption(id: "assessment_started", eventType: "assessment_started", title: "평가 시작", systemImage: "stethoscope", tint: DS.ColorToken.primary),
    CaptureTagOption(id: "assessment_finding", eventType: "assessment_finding", title: "평가 소견", systemImage: "waveform.path.ecg", tint: DS.ColorToken.primary),
    CaptureTagOption(id: "positioning_alignment", eventType: "positioning_alignment", title: "자세·정렬", systemImage: "figure.stand", tint: DS.ColorToken.primary),
    CaptureTagOption(id: "rom_measurement", eventType: "rom_measurement", title: "ROM·측정", systemImage: "ruler.fill", tint: DS.ColorToken.primary),
    CaptureTagOption(id: "functional_task", eventType: "functional_task", title: "기능 과제", systemImage: "figure.walk", tint: DS.ColorToken.warning),
    CaptureTagOption(id: "intervention_started", eventType: "intervention_started", title: "중재 시작", systemImage: "hands.sparkles.fill", tint: DS.ColorToken.warning),
    CaptureTagOption(id: "movement_correction", eventType: "movement_correction", title: "동작 교정·촉진", systemImage: "figure.mixed.cardio", tint: DS.ColorToken.warning),
    CaptureTagOption(id: "orthosis_assistive_device", eventType: "orthosis_assistive_device", title: "보조기·도구", systemImage: "bandage.fill", tint: DS.ColorToken.warning),
    CaptureTagOption(id: "exercise_instruction", eventType: "exercise_instruction", title: "운동·과제 설명", systemImage: "figure.strengthtraining.traditional", tint: DS.ColorToken.success),
    CaptureTagOption(id: "response_tolerance", eventType: "response_tolerance", title: "반응·내약성", systemImage: "heart.text.square.fill", tint: DS.ColorToken.success),
    CaptureTagOption(id: "caregiver_education", eventType: "caregiver_education", title: "보호자 교육", systemImage: "person.2.fill", tint: DS.ColorToken.success),
    CaptureTagOption(id: "reassessment_outcome", eventType: "reassessment_outcome", title: "재평가·결과", systemImage: "checkmark.seal.fill", tint: DS.ColorToken.success),
    CaptureTagOption(id: "home_program", eventType: "home_program", title: "홈프로그램 지시", systemImage: "house.and.flag.fill", tint: DS.ColorToken.success)
]

private struct CaptureTagSheet: View {
    let onSelect: (CaptureTagOption) async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var isSubmitting = false

    var body: some View {
        NavigationStack {
            List {
                Section("현재 시점에 기록할 이벤트") {
                    ForEach(captureTagOptions) { option in
                        Button {
                            isSubmitting = true
                            Task { @MainActor in
                                await onSelect(option)
                                isSubmitting = false
                            }
                        } label: {
                            Label(option.title, systemImage: option.systemImage)
                                .foregroundStyle(option.tint)
                        }
                        .disabled(isSubmitting)
                    }
                }
            }
            .navigationTitle("시간축 태그")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("닫기") { dismiss() }
                }
            }
            .overlay {
                if isSubmitting {
                    ProgressView("기록 중…")
                        .padding(20)
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
                }
            }
        }
    }
}
