import SwiftUI
import MWDATCore

struct StreamView: View {
    @State private var vm = StreamViewModel()
    @StateObject private var bridgeVm: AdapterViewModel
    @State private var store = PatientStore()

    @State private var currentPatient: Patient? = nil
    @State private var showPatientPicker = false
    @State private var isAnalyzing = false
    @State private var showPhotoSheet = false
    @State private var showChartSheet = false
    @State private var lastEventId: String? = nil
    @State private var analysisText: String = ""
    @State private var isCapturing = false
    @State private var toastMessage: String? = nil

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
            Color.black.ignoresSafeArea()

            // 카메라 피드
            cameraFeed
                .ignoresSafeArea(edges: .top)

            // 상단 오버레이
            VStack(spacing: 8) {
                statusPill
                    .padding(.top, 8)
                    .padding(.horizontal, 16)

                // STT 결과 pill
                if !sttText.isEmpty || isTranscribing {
                    sttPill
                        .padding(.horizontal, 16)
                        .transition(.opacity.combined(with: .move(edge: .top)))
                }

                Spacer()
            }

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
        .navigationTitle(currentPatient.map { $0.name } ?? "Ray-Ban 카메라")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(.ultraThinMaterial, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button {
                    showPatientPicker = true
                } label: {
                    HStack(spacing: 5) {
                        Image(systemName: currentPatient == nil ? "person.crop.circle.badge.plus" : "person.crop.circle.fill")
                            .foregroundStyle(currentPatient == nil ? .orange : .green)
                        if let p = currentPatient {
                            Text(p.name)
                                .font(.caption)
                                .foregroundStyle(.white)
                        }
                    }
                }
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
        .onDisappear { Task { await vm.tearDown() } }
        // 촬영 리뷰 시트
        .sheet(isPresented: $showPhotoSheet) {
            if let photo = vm.capturedPhoto {
                PhotoReviewSheet(
                    photo: photo,
                    isAnalyzing: $isAnalyzing,
                    analysisText: $analysisText,
                    onSave: { vm.savePhoto() },
                    onSend: { await analyzeAndSend(photo) },
                    onViewChart: { showPhotoSheet = false; showChartSheet = true }
                )
            }
        }
        // 차트 시트
        .sheet(isPresented: $showChartSheet) {
            if let eventId = lastEventId {
                ChartDetailView(eventId: eventId)
            }
        }
        .onChange(of: vm.capturedPhoto) { _, newPhoto in
            if newPhoto != nil {
                analysisText = ""
                lastEventId = nil
                showPhotoSheet = true
            }
        }
    }

    // MARK: - 카메라 피드

    private var cameraFeed: some View {
        GeometryReader { geo in
            ZStack {
                Color.black

                if let frame = vm.currentFrame {
                    Image(uiImage: frame)
                        .resizable()
                        .scaledToFill()
                        .frame(width: geo.size.width, height: geo.size.height)
                        .clipped()
                } else {
                    VStack(spacing: 12) {
                        Image(systemName: "video.slash.fill")
                            .font(.system(size: 40, weight: .light))
                            .foregroundStyle(.white.opacity(0.4))
                        Text(vm.isStreaming ? "프레임 수신 중..." : "스트리밍을 시작하세요")
                            .font(.subheadline)
                            .foregroundStyle(.white.opacity(0.4))
                    }
                }

                // 녹화 중 테두리
                if vm.recorder.isRecording {
                    RoundedRectangle(cornerRadius: 0)
                        .stroke(Color.red, lineWidth: 3)
                        .ignoresSafeArea()
                        .animation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true),
                                   value: vm.recorder.isRecording)
                }
            }
        }
    }

    // MARK: - 상태 필

    private var statusPill: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(vm.isStreaming ? Color.green : Color.orange)
                .frame(width: 7, height: 7)
                .shadow(color: vm.isStreaming ? .green : .orange, radius: 4)

            Text(vm.isStreaming
                 ? (vm.recorder.isRecording ? "녹화 중 · \(vm.recorder.frameCount)f" : "스트리밍 중")
                 : vm.statusMessage)
                .font(.caption)
                .fontWeight(.medium)
                .foregroundStyle(.white)

            if vm.recorder.isRecording {
                Spacer()
                Image(systemName: "record.circle.fill")
                    .foregroundStyle(.red)
                    .font(.caption)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(.ultraThinMaterial, in: Capsule())
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - STT Pill

    private var sttPill: some View {
        HStack(spacing: 8) {
            if isTranscribing {
                ProgressView()
                    .progressViewStyle(.circular)
                    .scaleEffect(0.7)
                    .tint(.white)
                Text("변환 중...")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.8))
            } else {
                Image(systemName: "text.bubble.fill")
                    .font(.caption)
                    .foregroundStyle(.yellow)
                Text(sttText)
                    .font(.caption)
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
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Color.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
    }

    // MARK: - 하단 컨트롤바

    private var controlBar: some View {
        VStack(spacing: 0) {
            // 에러 메시지
            if let err = vm.errorMessage {
                Text(err)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .padding(.horizontal, 20)
                    .padding(.top, 8)
            }

            HStack(spacing: 0) {
                // 왼쪽: 마이크(STT) 버튼
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
                    MicButton(
                        isRecording: audioRecorder.isRecording,
                        isTranscribing: isTranscribing
                    )
                }
                .frame(maxWidth: .infinity)

                // 중앙: 촬영 / 스트리밍 토글
                Button {
                    Task {
                        UIImpactFeedbackGenerator(style: .heavy).impactOccurred()
                        if vm.isStreaming {
                            // 촬영
                            isCapturing = true
                            vm.capturePhoto()
                            try? await Task.sleep(nanoseconds: 120_000_000)
                            isCapturing = false
                        } else {
                            // 환자 미선택 시 picker 먼저
                            if currentPatient == nil {
                                showPatientPicker = true
                            } else {
                                await vm.startStreaming()
                            }
                        }
                    }
                } label: {
                    CaptureButton(isStreaming: vm.isStreaming, isCapturing: isCapturing)
                }
                .frame(maxWidth: .infinity)
                .disabled(!vm.hasActiveDevice && !vm.isStreaming)

                // 오른쪽: 스트리밍 중지 or 영상 업로드
                rightActionButton
                    .frame(maxWidth: .infinity)
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 20)
        }
        .background(.ultraThinMaterial)
    }

    @ViewBuilder
    private var rightActionButton: some View {
        if vm.isStreaming {
            Button {
                Task { await vm.stopStreaming() }
            } label: {
                Image(systemName: "stop.fill")
                    .font(.system(size: 22))
                    .foregroundStyle(.white)
                    .frame(width: 48, height: 48)
                    .background(.ultraThinMaterial, in: Circle())
            }
        } else if lastEventId != nil {
            // 차트 보기 버튼 (전송 완료 후)
            Button {
                showChartSheet = true
                UIImpactFeedbackGenerator(style: .light).impactOccurred()
            } label: {
                ZStack {
                    Circle()
                        .fill(Color.indigo.opacity(0.85))
                        .frame(width: 48, height: 48)
                    Image(systemName: "doc.text.fill")
                        .font(.system(size: 20))
                        .foregroundStyle(.white)
                }
            }
        } else if let url = vm.recordedVideoURL {
            Button {
                Task { await uploadVideo(url) }
            } label: {
                Image(systemName: isAnalyzing ? "ellipsis" : "arrow.up.circle.fill")
                    .font(.system(size: 26))
                    .foregroundStyle(isAnalyzing ? Color.secondary : Color.white)
                    .frame(width: 48, height: 48)
                    .background(Color.indigo.opacity(0.8), in: Circle())
            }
            .disabled(isAnalyzing)
        } else {
            Color.clear.frame(width: 48, height: 48)
        }
    }

    // MARK: - Actions

    private func analyzeAndSend(_ image: UIImage) async {
        isAnalyzing = true
        analysisText = "Vision 분석 중..."

        let result = await ImageAnalyzer.analyze(image)
        var displayParts = [result.summary]
        if let pose = result.pose { displayParts.append(pose.summary) }
        analysisText = displayParts.joined(separator: "\n")

        let patientTag = currentPatient.map { "환자: \($0.name)" } ?? "환자: 미지정"
        var descParts = ["[Ray-Ban 카메라 캡처 분석]", patientTag]

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
                patientName: currentPatient?.name
            )
            lastEventId = resp.event_id
            analysisText += "\n✅ 차트 생성 완료"
            bridgeVm.markDone()
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            showToast("✅ 차트 저장됨 — 오른쪽 📄 버튼으로 보기")
        } catch {
            let errMsg = bridgeErrorMessage(error)
            analysisText += "\n⚠️ 업로드 실패 → 텍스트 전송\n\(errMsg)"
            bridgeVm.sendText(description)
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            showToast("⚠️ 업로드 실패")
        }

        isAnalyzing = false
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
            analysisText = "⚙️ 처리 중... (\(accepted.size_kb ?? 0)KB)"
            showToast("⚙️ 영상 분석 중...")

            // 2) 백그라운드 폴링 — 완료 시 toast 업데이트 (최대 60초)
            Task {
                let final = try? await bridgeVm.client.waitUntilDone(
                    eventId: accepted.event_id,
                    maxTries: 60,
                    intervalSec: 1.0
                )
                await MainActor.run {
                    if final?.status == "done" {
                        // inner event_id가 있으면 교체 (차트 파일이 거기에 있음)
                        if let innerId = final?.result?.event?.id {
                            lastEventId = innerId
                        }
                        analysisText = "📄 차트 생성됨"
                        UINotificationFeedbackGenerator().notificationOccurred(.success)
                        showToast("📄 차트 생성 완료 — 버튼을 눌러 확인하세요")
                    } else if final?.status == "error" {
                        analysisText = "⚠️ \(final?.error ?? "처리 오류")"
                        showToast("⚠️ 처리 실패")
                    }
                    // timeout이면 lastEventId(outer) 유지 — 서버 측 차트 복사로 조회 가능
                }
            }
        } catch {
            analysisText = "⚠️ \(bridgeErrorMessage(error))"
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            showToast("⚠️ 업로드 실패")
        }
        isAnalyzing = false
    }

    // MARK: - STT

    private func stopAndTranscribe() async {
        audioRecorder.stopRecording()
        guard let fileURL = audioRecorder.recordedFileURL else { return }

        isTranscribing = true

        do {
            let accepted = try await bridgeVm.client.uploadAudio(fileURL: fileURL)
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
        (error as? BridgeError).map { e in
            switch e {
            case .badStatus(let c, let b): return "HTTP \(c): \(b)"
            case .network(let m): return m
            case .decode(let m): return m
            case .fileNotFound: return "파일 없음"
            case .invalidURL: return "URL 오류"
            }
        } ?? error.localizedDescription
    }
}

// MARK: - 마이크 버튼 컴포넌트

private struct MicButton: View {
    let isRecording: Bool
    let isTranscribing: Bool

    var body: some View {
        ZStack {
            Circle()
                .stroke(isRecording ? Color.red : Color.white.opacity(0.5), lineWidth: 2)
                .frame(width: 48, height: 48)

            if isTranscribing {
                ProgressView()
                    .progressViewStyle(.circular)
                    .tint(.white)
                    .scaleEffect(0.8)
            } else {
                Image(systemName: isRecording ? "stop.fill" : "mic.fill")
                    .font(.system(size: isRecording ? 18 : 20))
                    .foregroundStyle(isRecording ? .red : .white)
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

    var body: some View {
        ZStack {
            if isStreaming {
                // 촬영 버튼 (흰 원)
                Circle()
                    .stroke(.white, lineWidth: 3)
                    .frame(width: 70, height: 70)
                Circle()
                    .fill(.white)
                    .frame(width: 58, height: 58)
                    .scaleEffect(isCapturing ? 0.85 : 1.0)
                    .animation(.easeOut(duration: 0.1), value: isCapturing)
            } else {
                // 스트리밍 시작
                ZStack {
                    Circle()
                        .fill(Color.blue)
                        .frame(width: 70, height: 70)
                    Image(systemName: "play.fill")
                        .font(.system(size: 26))
                        .foregroundStyle(.white)
                        .offset(x: 3)
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
                .fill(Color.red)
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
    let onSave: () -> Void
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
                        .tint(.indigo)
                        .transition(.opacity.combined(with: .scale(0.95)))
                    }

                    // 액션 버튼
                    HStack(spacing: 12) {
                        Button {
                            onSave()
                            UINotificationFeedbackGenerator().notificationOccurred(.success)
                        } label: {
                            Label("앨범 저장", systemImage: "square.and.arrow.down")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .tint(.green)

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

