import SwiftUI
import MWDATCore

struct StreamView: View {
    @State private var vm = StreamViewModel()
    @StateObject private var bridgeVm: AdapterViewModel
    @State private var analysisResult: String? = nil
    @State private var isAnalyzing = false

    init(client: BridgeClient) {
        _bridgeVm = StateObject(wrappedValue: AdapterViewModel(client: client))
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {

                // 상태 배너
                HStack {
                    Circle()
                        .fill(vm.isStreaming ? Color.green : Color.orange)
                        .frame(width: 10, height: 10)
                    Text(vm.statusMessage)
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Spacer()
                }
                .padding(8)
                .background(Color(.secondarySystemBackground))
                .cornerRadius(8)

                // 영상 프레임
                ZStack {
                    Color.black.cornerRadius(12)
                    if let frame = vm.currentFrame {
                        Image(uiImage: frame)
                            .resizable()
                            .scaledToFit()
                            .cornerRadius(12)
                    } else {
                        VStack(spacing: 8) {
                            Image(systemName: "video.slash")
                                .font(.largeTitle)
                                .foregroundColor(.gray)
                            Text(vm.isStreaming ? "프레임 수신 중..." : "스트리밍 시작 버튼을 누르세요")
                                .font(.caption)
                                .foregroundColor(.gray)
                        }
                    }
                }
                .frame(maxWidth: .infinity)
                .frame(height: 260)

                // 스트리밍 컨트롤
                HStack(spacing: 12) {
                    Button {
                        Task {
                            if vm.isStreaming {
                                await vm.stopStreaming()
                            } else {
                                await vm.startStreaming()
                            }
                        }
                    } label: {
                        Label(
                            vm.isStreaming ? "중지" : "스트리밍 시작",
                            systemImage: vm.isStreaming ? "stop.circle.fill" : "play.circle.fill"
                        )
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(vm.isStreaming ? .red : .blue)
                    .disabled(!vm.hasActiveDevice && !vm.isStreaming)

                    // 촬영 버튼
                    Button {
                        vm.capturePhoto()
                    } label: {
                        Label("촬영", systemImage: "camera.circle.fill")
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.green)
                    .disabled(!vm.isStreaming)
                }

                // 녹화 컨트롤
                HStack(spacing: 12) {
                    Button {
                        Task {
                            if vm.recorder.isRecording {
                                await vm.stopRecording()
                            } else {
                                vm.startRecording()
                            }
                        }
                    } label: {
                        Label(
                            vm.recorder.isRecording
                                ? "녹화 중지 (\(vm.recorder.frameCount)f)"
                                : "녹화 시작",
                            systemImage: vm.recorder.isRecording
                                ? "record.circle.fill"
                                : "record.circle"
                        )
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(vm.recorder.isRecording ? .red : .orange)
                    .disabled(!vm.isStreaming)

                    // 녹화 완료 후 서버 전송
                    if let videoURL = vm.recordedVideoURL {
                        Button {
                            Task { await uploadVideo(videoURL) }
                        } label: {
                            Label(isAnalyzing ? "전송 중..." : "서버 전송",
                                  systemImage: "arrow.up.circle.fill")
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.indigo)
                        .disabled(isAnalyzing)
                    }
                }

                // 캡처된 사진 + 처리
                if let photo = vm.capturedPhoto {
                    VStack(spacing: 10) {
                        Text("📸 캡처 완료")
                            .font(.subheadline)
                            .fontWeight(.semibold)

                        Image(uiImage: photo)
                            .resizable()
                            .scaledToFit()
                            .frame(height: 180)
                            .cornerRadius(10)

                        HStack(spacing: 10) {
                            Button {
                                vm.savePhoto()
                            } label: {
                                Label("앨범 저장", systemImage: "square.and.arrow.down")
                                    .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(.green)

                            Button {
                                Task { await analyzeAndSend(photo) }
                            } label: {
                                Label(isAnalyzing ? "분석 중..." : "분석 & 전송",
                                      systemImage: "brain.head.profile")
                                    .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(.purple)
                            .disabled(isAnalyzing)
                        }

                    }
                    .padding()
                    .background(Color(.secondarySystemBackground))
                    .cornerRadius(12)
                }


                // 녹화기 상태 (항상 표시)
                if !vm.recorder.statusMessage.isEmpty {
                    HStack {
                        Image(systemName: vm.recorder.isRecording ? "record.circle.fill" : "checkmark.circle.fill")
                            .foregroundColor(vm.recorder.isRecording ? .red : .green)
                        Text(vm.recorder.statusMessage)
                            .font(.caption)
                            .foregroundColor(.secondary)
                        Spacer()
                    }
                    .padding(8)
                    .background(Color(.secondarySystemBackground))
                    .cornerRadius(8)
                }

                // 업로드/분석 결과 (항상 표시)
                if let result = analysisResult {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(result)
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        if case .done = bridgeVm.state {
                            Label("서버 처리 완료", systemImage: "checkmark.circle.fill")
                                .foregroundColor(.green)
                                .font(.caption)
                        }
                    }
                    .padding(8)
                    .background(Color(.tertiarySystemBackground))
                    .cornerRadius(8)
                }

                if let err = vm.errorMessage {
                    Text(err)
                        .font(.caption)
                        .foregroundColor(.red)
                }

                Spacer(minLength: 20)
            }
            .padding()
        }
        .navigationTitle("Ray-Ban 카메라")
        .onAppear { vm.setup() }
        .onDisappear { Task { await vm.tearDown() } }
    }

    private func uploadVideo(_ url: URL) async {
        isAnalyzing = true
        analysisResult = "영상 업로드 중..."
        do {
            let accepted = try await bridgeVm.uploadVideo(fileURL: url)
            let kb = accepted.size_kb ?? 0
            let detail = bridgeVm.lastMessage.isEmpty ? "" : "\n\(bridgeVm.lastMessage)"
            analysisResult = "✅ 영상 저장됨: \(accepted.video_saved ?? accepted.event_id) (\(kb)KB)\(detail)"
        } catch {
            let msg = (error as? BridgeError).map { e in
                switch e {
                case .badStatus(let c, let b): return "HTTP \(c): \(b)"
                case .network(let m): return m
                default: return e.localizedDescription
                }
            } ?? error.localizedDescription
            let detail = bridgeVm.lastMessage.isEmpty ? "" : "\n\(bridgeVm.lastMessage)"
            analysisResult = "⚠️ 영상 업로드 실패: \(msg)\(detail)"
        }
        isAnalyzing = false
    }

    private func analyzeAndSend(_ image: UIImage) async {
        isAnalyzing = true
        analysisResult = "분석 중..."

        // 1) 온디바이스 Vision 분석
        let result = await ImageAnalyzer.analyze(image)
        var displayParts = [result.summary]
        if let pose = result.pose {
            displayParts.append(pose.summary)
        }
        analysisResult = displayParts.joined(separator: "\n")

        // 2) 이미지 + 분석 설명을 서버에 직접 업로드
        var descParts = ["[Ray-Ban 카메라 캡처 분석]", result.summary]
        if let pose = result.pose {
            descParts.append(pose.summary)
        }
        descParts.append("위 이미지를 참고해 임상 메모를 작성해주세요.")
        let description = descParts.joined(separator: "\n")

        do {
            let resp = try await bridgeVm.client.uploadImage(image, description: description)
            analysisResult = result.summary + "\n✅ 서버 저장됨 (event: \(resp.event_id))"
            bridgeVm.markDone()
        } catch {
            // 업로드 실패 시 텍스트 전송으로 폴백
            let errMsg = (error as? BridgeError).map { e in
                switch e {
                case .badStatus(let code, let body): return "HTTP \(code): \(body)"
                case .network(let m): return "네트워크: \(m)"
                case .decode(let m): return "디코드: \(m)"
                case .fileNotFound: return "파일없음"
                case .invalidURL: return "URL오류"
                }
            } ?? error.localizedDescription
            analysisResult = result.summary + "\n⚠️ 업로드 실패 [\(errMsg)]\n→ 텍스트로 전송"
            bridgeVm.sendText(description)
        }

        isAnalyzing = false
    }
}
