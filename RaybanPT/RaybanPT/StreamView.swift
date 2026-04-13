import SwiftUI
import MWDATCore

struct StreamView: View {
    @State private var vm = StreamViewModel()
    @StateObject private var bridgeVm: AdapterViewModel

    @State private var isAnalyzing = false
    @State private var showPhotoSheet = false
    @State private var showChartSheet = false
    @State private var lastEventId: String? = nil
    @State private var analysisText: String = ""
    @State private var isCapturing = false

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
            VStack {
                statusPill
                    .padding(.top, 8)
                    .padding(.horizontal, 16)
                Spacer()
            }

            // 하단 컨트롤바
            controlBar
        }
        .navigationTitle("Ray-Ban 카메라")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(.ultraThinMaterial, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
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
                // 왼쪽: 녹화 버튼
                Button {
                    Task {
                        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                        if vm.recorder.isRecording {
                            await vm.stopRecording()
                        } else {
                            vm.startRecording()
                        }
                    }
                } label: {
                    RecordButton(isRecording: vm.recorder.isRecording)
                }
                .disabled(!vm.isStreaming)
                .opacity(!vm.isStreaming ? 0.35 : 1.0)
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
                            await vm.startStreaming()
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

        var descParts = ["[Ray-Ban 카메라 캡처 분석]", result.summary]
        if let pose = result.pose { descParts.append(pose.summary) }
        descParts.append("위 이미지를 참고해 임상 메모를 작성해주세요.")
        let description = descParts.joined(separator: "\n")

        do {
            let resp = try await bridgeVm.client.uploadImage(image, description: description)
            lastEventId = resp.event_id
            analysisText += "\n✅ 차트 생성 완료"
            bridgeVm.markDone()
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        } catch {
            let errMsg = bridgeErrorMessage(error)
            analysisText += "\n⚠️ 업로드 실패 → 텍스트 전송\n\(errMsg)"
            bridgeVm.sendText(description)
            UINotificationFeedbackGenerator().notificationOccurred(.error)
        }

        isAnalyzing = false
    }

    private func uploadVideo(_ url: URL) async {
        isAnalyzing = true
        do {
            let accepted = try await bridgeVm.uploadVideo(fileURL: url)
            lastEventId = accepted.event_id
            analysisText = "✅ 영상 저장됨 (\(accepted.size_kb ?? 0)KB)"
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        } catch {
            analysisText = "⚠️ \(bridgeErrorMessage(error))"
            UINotificationFeedbackGenerator().notificationOccurred(.error)
        }
        isAnalyzing = false
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

