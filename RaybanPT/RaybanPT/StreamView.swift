import SwiftUI
import MWDATCore
import UIKit
import AVKit

struct StreamView: View {
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
                return "rayban-camera"
            case .phone:
                return "iphone-camera"
            }
        }
    }

    private struct PendingConsentAction: Identifiable {
        enum Kind {
            case photo(UIImage)
            case video(URL)
            case audio(URL)
        }

        let id = UUID()
        let patientName: String
        let kind: Kind
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
    @State private var vm = StreamViewModel()
    @State private var deviceSession = DeviceSessionManager.shared
    @StateObject private var bridgeVm: AdapterViewModel
    @State private var store = PatientStore()
    @State private var captureStore = CaptureStore.shared

    @State private var currentPatient: Patient? = nil
    @State private var showPatientPicker = false
    @State private var isAnalyzing = false
    @State private var showPhotoSheet = false
    @State private var showChartSheet = false
    @State private var showLabelSheet = false
    @State private var showVideoSheet = false
    @State private var showPhoneCamera = false
    @State private var lastEventId: String? = nil
    @State private var analysisText: String = ""
    @State private var isCapturing = false
    @State private var toastMessage: String? = nil
    @State private var saveStatus: SaveStatus = .idle
    @State private var showCaptureHistory = false
    @State private var showPhotoPermissionAlert = false
    @State private var photoPermissionMessage = "사진 보관함 접근 권한이 필요합니다."
    @State private var photoSource: PhotoSource = .rayban
    @State private var pendingConsentAction: PendingConsentAction?

    // STT
    @State private var audioRecorder = AudioRecorder()
    @State private var sttText: String = ""       // Whisper 변환 결과 (누적)
    @State private var isTranscribing = false
    @State private var toastTask: Task<Void, Never>? = nil

    init(client: BridgeClient) {
        _bridgeVm = StateObject(wrappedValue: AdapterViewModel(client: client))
    }

    // MARK: - Body

    var body: some View {
        ZStack(alignment: .bottom) {
            DS.ColorToken.cameraBackground.ignoresSafeArea()

            // 카메라 피드
            cameraFeed
                .ignoresSafeArea(edges: .top)

            // 상단 오버레이
            VStack(spacing: 8) {
                // 스트리밍 중일 때만 상태 pill 표시 (비스트리밍 시 DeviceStatusBanner가 대신함)
                if vm.isStreaming || vm.recorder.isRecording {
                    statusPill
                        .padding(.top, 8)
                        .padding(.horizontal, 16)
                        .transition(.opacity.combined(with: .move(edge: .top)))
                }

                // STT 결과 pill
                if !sttText.isEmpty || isTranscribing {
                    sttPill
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
            .animation(.spring(response: 0.3), value: vm.isStreaming)

            // 토스트 (중앙)
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

            // 하단 컨트롤바
            controlBar
        }
        .navigationTitle(currentPatient.map { $0.name } ?? "스마트 글라스 카메라")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(.ultraThinMaterial, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button {
                    showPatientPicker = true
                } label: {
                    patientToolbarLabel
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                captureOptionsMenu
            }
        }
        .sheet(isPresented: $showPatientPicker) {
            PatientPickerView(selectedPatient: $currentPatient, store: store)
        }
        .onChange(of: currentPatient?.id) { _, _ in
            // 환자 변경 시 STT 텍스트 초기화
            withAnimation { sttText = "" }
        }
        .onAppear { vm.setup() }
        .onAppear { deviceSession.start() }
        .onDisappear { Task { await vm.tearDown() } }
        // 촬영 리뷰 시트
        .sheet(isPresented: $showPhotoSheet) {
            if let photo = vm.capturedPhoto {
                PhotoReviewSheet(
                    photo: photo,
                    isAnalyzing: $isAnalyzing,
                    analysisText: $analysisText,
                    saveStatus: saveStatus,
                    onSave: { await saveCurrentPhoto() },
                    onSend: { await runWithConsent(.photo(photo)) },
                    onViewChart: { showPhotoSheet = false; showChartSheet = true }
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
                    onSave: { await saveCurrentVideo() },
                    onSend: { await runWithConsent(.video(url)) },
                    onViewChart: { showVideoSheet = false; showChartSheet = true }
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
        // 차트 시트
        .sheet(isPresented: $showChartSheet) {
            if let eventId = lastEventId {
                NavigationStack {
                    ChartDetailView(eventId: eventId, client: bridgeVm.client)
                }
            }
        }
        // 라벨링 시트
        .sheet(isPresented: $showLabelSheet) {
            if let eventId = lastEventId {
                LabelingView(eventId: eventId, client: bridgeVm.client)
            }
        }
        .onChange(of: vm.capturedPhoto) { _, newPhoto in
            if newPhoto != nil {
                analysisText = ""
                lastEventId = nil
                saveStatus = .idle
                showPhotoSheet = true
                if autoSaveCaptures {
                    Task { await saveCurrentPhoto(triggeredAutomatically: true) }
                }
            }
        }
        .onChange(of: vm.recordedVideoURL) { _, newURL in
            if newURL != nil {
                analysisText = ""
                lastEventId = nil
                showVideoSheet = true
            }
        }
        .onChange(of: vm.isStreaming) { _, streaming in
            Task {
                if streaming {
                    await GlassHUDManager.shared.startContext(patient: currentPatient?.name)
                } else {
                    await GlassHUDManager.shared.stopContext()
                }
            }
        }
        .onChange(of: currentPatient?.id) { _, _ in
            Task {
                if vm.isStreaming, !vm.recorder.isRecording {
                    await GlassHUDManager.shared.updateContextPatient(currentPatient?.name)
                }
            }
        }
        .onChange(of: vm.recorder.isRecording) { _, recording in
            Task {
                if recording {
                    await GlassHUDManager.shared.startRecording(patient: currentPatient?.name)
                } else {
                    await GlassHUDManager.shared.stopRecording()
                }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .glassCaptouchRecordToggle)) { _ in
            Task { await toggleRecording() }
        }
        .sheet(isPresented: $showCaptureHistory) {
            NavigationStack {
                CaptureHistoryView()
            }
        }
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
                message: Text("\(action.patientName) 환자/보호자의 촬영, 녹음, 분석, 차트 생성 동의를 확인한 뒤 진행하세요."),
                primaryButton: .default(Text("동의 기록 후 진행")) {
                    Task { await recordConsentAndContinue(action) }
                },
                secondaryButton: .cancel(Text("취소"))
            )
        }
    }

    // MARK: - 카메라 피드

    private var cameraFeed: some View {
        GeometryReader { geo in
            ZStack {
                DS.ColorToken.cameraBackground

                if let frame = vm.currentFrame {
                    Image(uiImage: frame)
                        .resizable()
                        .scaledToFill()
                        .frame(width: geo.size.width, height: geo.size.height)
                        .clipped()
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

    // MARK: - 상태 Pill

    private var statusPill: some View {
        HStack(spacing: DS.Spacing.xs) {
            Circle()
                .fill(vm.isStreaming ? DS.ColorToken.success : DS.ColorToken.warning)
                .frame(width: 8, height: 8)
                .shadow(color: vm.isStreaming ? DS.ColorToken.success : DS.ColorToken.warning, radius: 4)

            Text(vm.isStreaming
                 ? (vm.recorder.isRecording ? "녹화 중 · \(vm.recorder.frameCount)f" : (DemoConfig.isGlassDemoEnabled ? "데모 스트리밍 중" : "스트리밍 중"))
                 : vm.statusMessage)
                .font(.system(size: DS.FontSize.caption, weight: .semibold))
                .fontWeight(.medium)
                .foregroundStyle(.white)

            if vm.recorder.isRecording {
                Spacer()
                Image(systemName: "record.circle.fill")
                    .foregroundStyle(DS.ColorToken.danger)
                    .font(.caption)
            }
        }
        .padding(.horizontal, DS.Spacing.sm)
        .padding(.vertical, DS.Spacing.xs)
        .frame(minHeight: 32)
        .background(DS.ColorToken.surface, in: Capsule())
        .frame(maxWidth: .infinity, alignment: .leading)
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
            Spacer()
            if deviceSession.linkState != .connected {
                Button("재연결") {
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

                    Label(vm.isStreaming ? "스트리밍" : vm.statusMessage, systemImage: vm.isStreaming ? "dot.radiowaves.left.and.right" : "antenna.radiowaves.left.and.right")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(vm.isStreaming ? DS.ColorToken.success : .white.opacity(0.72))
                        .lineLimit(1)
                }

                HStack(alignment: .center, spacing: 12) {
                    Button {
                        Task {
                            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                            if audioRecorder.isRecording {
                                await stopAndTranscribe()
                            } else {
                                await audioRecorder.startRecording()
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
                    .buttonStyle(.plain)
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
                    .buttonStyle(.plain)

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
            .padding(.bottom, 8)
        }
    }

    @ViewBuilder
    private var rightActionButton: some View {
        if vm.isStreaming {
            HStack(spacing: 10) {
                Button {
                    Task { await toggleRecording() }
                } label: {
                    DockActionButton(
                        title: vm.recorder.isRecording ? "녹화중" : "녹화",
                        systemImage: vm.recorder.isRecording ? "stop.fill" : "record.circle",
                        tint: DS.ColorToken.danger,
                        isActive: vm.recorder.isRecording
                    )
                }
                .buttonStyle(.plain)
                .disabled(isSavingInProgress)

                Button {
                    Task { await stopStreamingFlow() }
                } label: {
                    DockActionButton(
                        title: "종료",
                        systemImage: "xmark",
                        tint: .white,
                        isActive: false
                    )
                }
                .buttonStyle(.plain)
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
                .buttonStyle(.plain)
                // 차트 보기 버튼
                Button {
                    showChartSheet = true
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                } label: {
                    DockActionButton(title: "차트", systemImage: "doc.text.fill", tint: DS.ColorToken.primary)
                }
                .buttonStyle(.plain)
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
                .buttonStyle(.plain)
                .disabled(isSavingInProgress)

                Button {
                    Task { await runWithConsent(.video(url)) }
                } label: {
                    DockActionButton(
                        title: "분석",
                        systemImage: uploadButtonSymbol,
                        tint: DS.ColorToken.primary,
                        isBusy: isAnalyzing || isSavingInProgress
                    )
                }
                .buttonStyle(.plain)
                .disabled(isAnalyzing || isSavingInProgress)
                .accessibilityLabel("분석 및 업로드")
            }
        } else {
            DockActionButton(
                title: "대기",
                systemImage: "ellipsis",
                tint: .white.opacity(0.6),
                isDisabled: true
            )
        }
    }

    // MARK: - Actions

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
        if vm.recorder.isRecording {
            return "녹화 중입니다. 종료하면 영상 리뷰에서 저장하거나 분석할 수 있습니다."
        }
        if DemoConfig.isGlassDemoEnabled {
            if DemoConfig.usesMaskedCaptureFrame {
                return "데모 모드: 실제 마스킹 촬영 결과를 라이브 프레임처럼 보여줍니다."
            }
            return "데모 모드: 스마트 글라스 연결과 라이브 프레임 수신 흐름을 보여줍니다."
        }
        if vm.isStreaming {
            return "가운데 버튼은 사진 촬영, 오른쪽은 녹화와 종료입니다."
        }
        if lastEventId != nil {
            return "차트가 생성되었습니다. 라벨을 붙이거나 차트를 확인하세요."
        }
        if vm.recordedVideoURL != nil {
            return "녹화 영상이 준비되었습니다. 저장하거나 분석 업로드하세요."
        }
        if !vm.hasActiveDevice {
            return "스마트 글라스 없이 iPhone 카메라로 촬영할 수 있습니다."
        }
        if currentPatient == nil {
            return "환자를 선택하면 스트리밍을 시작할 수 있습니다."
        }
        return "스마트 글라스 연결 상태를 확인하고 시작 버튼을 누르세요."
    }

    private var centerButtonTitle: String {
        if vm.isStreaming {
            return "촬영"
        }
        return vm.hasActiveDevice ? "시작" : "폰촬영"
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
        }
    }

    private func analyzeAndSend(_ image: UIImage) async {
        isAnalyzing = true
        analysisText = "Vision 분석 중..."

        let result = await ImageAnalyzer.analyze(image)
        var displayParts = [result.summary]
        if let pose = result.pose { displayParts.append(pose.summary) }
        analysisText = displayParts.joined(separator: "\n")

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
            analysisText += "\n✅ 차트 생성 완료"
            bridgeVm.markDone()
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            showToast("✅ 차트 저장됨 — 오른쪽 📄 버튼으로 보기")
            let insightBody = currentPatient.map { "환자: \($0.name)" } ?? "SOAP 노트 생성됨"
            await GlassHUDManager.shared.showInsight(title: "차트 생성됨", body: insightBody)
        } catch {
            let errMsg = bridgeErrorMessage(error)
            analysisText += "\n⚠️ 업로드 실패 → 텍스트 전송\n\(errMsg)"
            bridgeVm.sendText(description, patientName: currentPatient?.name)
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            showToast("⚠️ 업로드 실패")
        }

        isAnalyzing = false
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

    private func saveCurrentVideo(triggeredAutomatically: Bool = false) async {
        saveStatus = .saving(triggeredAutomatically ? "영상 자동 저장 중..." : "영상 저장 중...")
        do {
            let capture = try await vm.saveRecordedVideo(patientName: currentPatient?.name)
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            saveStatus = .saved("영상 저장 완료")
            showToast("✅ 영상 저장 완료 · \(capture.relativePath)")
        } catch {
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            saveStatus = .failed("영상 저장 실패")
            showToast("⚠️ 영상 저장 실패")
            handleSaveError(error)
        }
    }

    private func toggleRecording() async {
        if vm.recorder.isRecording {
            await vm.stopRecording()
            if autoSaveCaptures {
                await saveCurrentVideo(triggeredAutomatically: true)
            } else {
                saveStatus = .saved("영상 저장 준비 완료")
                showToast("📼 영상 캡처 완료 — 업로드 또는 저장 가능")
            }
        } else {
            saveStatus = .idle
            vm.startRecording()
            showToast("🔴 영상 녹화 시작")
        }
    }

    private func stopStreamingFlow() async {
        let wasRecording = vm.recorder.isRecording
        await vm.stopStreaming()
        if wasRecording, autoSaveCaptures, vm.recordedVideoURL != nil {
            await saveCurrentVideo(triggeredAutomatically: true)
        } else if wasRecording, vm.recordedVideoURL != nil {
            saveStatus = .saved("영상 저장 준비 완료")
            showToast("📼 영상 캡처 완료 — 업로드 또는 저장 가능")
        }
    }

    private func uploadVideo(_ url: URL) async {
        isAnalyzing = true
        do {
            // 1) 업로드 → outer event_id 수신
            let accepted = try await bridgeVm.client.uploadVideo(
                fileURL: url,
                patientName: currentPatient?.name
            )
            lastEventId = accepted.event_id          // 우선 outer id로 차트 버튼 활성화
            analysisText = "영상 분석 업로드 완료\n⚙️ 처리 중... (\(accepted.size_kb ?? 0)KB)"
            showToast("⚙️ 영상 분석 중...")

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
                    await GlassHUDManager.shared.showInsight(title: "차트 생성됨", body: insightBody)
                }
                await MainActor.run {
                    if final?.status == "done" {
                        // inner event_id가 있으면 교체 (차트 파일이 거기에 있음)
                        if let innerId = final?.result?.event?.id {
                            lastEventId = innerId
                        }
                        analysisText = "✅ 영상 분석 완료\n📄 차트 생성됨"
                        UINotificationFeedbackGenerator().notificationOccurred(.success)
                        showToast("📄 차트 생성 완료 — 버튼을 눌러 확인하세요")
                    } else if final?.status == "error" {
                        let message = UserFacingError.message(code: final?.error_code, fallback: final?.error)
                        analysisText = "⚠️ 영상 분석 실패\n\(message)"
                        showToast("⚠️ 처리 실패")
                    }
                    // timeout이면 lastEventId(outer) 유지 — 서버 측 차트 복사로 조회 가능
                }
            }
        } catch {
            analysisText = "⚠️ 영상 업로드 실패\n\(bridgeErrorMessage(error))"
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            showToast("⚠️ 업로드 실패")
        }
        isAnalyzing = false
    }

    // MARK: - STT

    private func stopAndTranscribe() async {
        audioRecorder.stopRecording()
        guard let fileURL = audioRecorder.recordedFileURL else { return }
        await runWithConsent(.audio(fileURL))
    }

    private func transcribeAudio(fileURL: URL) async {
        isTranscribing = true

        do {
            let accepted = try await bridgeVm.client.uploadAudio(fileURL: fileURL, patientName: currentPatient?.name)
            let final = try await bridgeVm.client.waitUntilDone(eventId: accepted.event_id)
            let transcript = final.result?.event?.raw_text ?? ""

            if transcript.isEmpty {
                showToast("🎙 변환 결과 없음")
            } else {
                withAnimation {
                    sttText = sttText.isEmpty ? transcript : sttText + "\n" + transcript
                }
                UINotificationFeedbackGenerator().notificationOccurred(.success)
                showToast("🎙 변환 완료")
            }
        } catch {
            showToast("⚠️ 변환 실패: \(bridgeErrorMessage(error))")
            UINotificationFeedbackGenerator().notificationOccurred(.error)
        }

        isTranscribing = false
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
        VStack(spacing: 14) {
            Image(systemName: iconName)
                .font(.system(size: 44, weight: .light))
                .foregroundStyle(.white.opacity(0.48))

            VStack(spacing: 6) {
                Text(title)
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.84))
                Text(message)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.white.opacity(0.48))
                    .multilineTextAlignment(.center)
                    .lineLimit(3)
            }
        }
        .padding(.horizontal, 32)
    }

    private var iconName: String {
        if isStreaming { return "camera.metering.unknown" }
        return hasActiveDevice ? "play.circle" : "camera.fill"
    }

    private var title: String {
        if isStreaming { return "프레임 수신 대기 중" }
        return hasActiveDevice ? "촬영 준비됨" : "iPhone 카메라 사용 가능"
    }

    private var message: String {
        if isStreaming { return "잠시 후 카메라 프레임이 표시됩니다." }
        if hasActiveDevice { return "환자를 선택하고 시작 버튼을 누르세요." }
        return "가운데 폰촬영 버튼으로 사진을 찍어 분석할 수 있습니다."
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
