import SwiftUI
import UniformTypeIdentifiers
import MWDATCore

struct M2_TestView: View {
    @StateObject private var vm: AdapterViewModel
    @Environment(DeviceSessionManager.self) private var deviceManager
    @State private var audioRecorder = AudioRecorder()
    @State private var textInput: String = ""
    @State private var selectedAudioURL: URL?
    @State private var showImporter = false
    @State private var selectedTab = 0

    init(baseURL: URL = URL(string: "http://YOUR_SERVER_HOST:8791")!) {
        _vm = StateObject(wrappedValue: AdapterViewModel(client: BridgeClient(baseURL: baseURL)))
    }

    var body: some View {
        NavigationView {
            VStack(spacing: 0) {

                // 연결 상태 배너
                connectionBanner

                // 탭
                Picker("", selection: $selectedTab) {
                    Text("음성").tag(0)
                    Text("텍스트").tag(1)
                    Text("카메라").tag(2)
                }
                .pickerStyle(.segmented)
                .padding(.horizontal)
                .padding(.vertical, 8)

                Divider()

                ScrollView {
                    VStack(spacing: 16) {
                        switch selectedTab {
                        case 0: audioTab
                        case 1: textTab
                        case 2: cameraTab
                        default: EmptyView()
                        }

                        // 결과
                        if vm.state != .idle {
                            resultCard
                        }
                    }
                    .padding()
                }
            }
            .navigationTitle("Rayban Bridge")
            .navigationBarTitleDisplayMode(.inline)
        }
        .fileImporter(
            isPresented: $showImporter,
            allowedContentTypes: [.audio, .wav, .item],
            allowsMultipleSelection: false
        ) { result in
            if case .success(let urls) = result, let url = urls.first {
                _ = url.startAccessingSecurityScopedResource()
                selectedAudioURL = url
            }
        }
    }

    // MARK: - 연결 배너
    var connectionBanner: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(deviceManager.linkState == .connected ? Color.green : Color.red)
                .frame(width: 8, height: 8)
            Text(deviceManager.statusMessage)
                .font(.caption)
                .foregroundColor(.secondary)
            Spacer()
        }
        .padding(.horizontal)
        .padding(.vertical, 6)
        .background(Color(.secondarySystemBackground))
    }

    // MARK: - 음성 탭
    var audioTab: some View {
        VStack(spacing: 16) {
            // 녹음 버튼
            Button {
                Task {
                    if audioRecorder.isRecording {
                        audioRecorder.stopRecording()
                        if let url = audioRecorder.recordedFileURL {
                            vm.uploadAudio(fileURL: url)
                        }
                    } else {
                        await audioRecorder.startRecording()
                    }
                }
            } label: {
                VStack(spacing: 10) {
                    Image(systemName: audioRecorder.isRecording ? "stop.circle.fill" : "mic.circle.fill")
                        .font(.system(size: 56))
                        .foregroundColor(audioRecorder.isRecording ? .red : .blue)
                    Text(audioRecorder.isRecording ? "중지 & 업로드" : "Ray-Ban 녹음")
                        .fontWeight(.semibold)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 24)
                .background(
                    RoundedRectangle(cornerRadius: 16)
                        .fill(audioRecorder.isRecording ? Color.red.opacity(0.08) : Color.blue.opacity(0.08))
                )
            }

            Text(audioRecorder.statusMessage)
                .font(.caption)
                .foregroundColor(.secondary)

            Divider()

            // 파일 선택
            HStack {
                Button {
                    showImporter = true
                } label: {
                    Label("파일 선택", systemImage: "folder")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)

                Button {
                    if let u = selectedAudioURL { vm.uploadAudio(fileURL: u) }
                } label: {
                    Label("업로드", systemImage: "arrow.up.circle")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .disabled(selectedAudioURL == nil)
            }

            if let u = selectedAudioURL {
                Text(u.lastPathComponent)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }

    // MARK: - 텍스트 탭
    var textTab: some View {
        VStack(spacing: 12) {
            TextField("환자 상태를 입력하세요...", text: $textInput, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(4...8)

            Button {
                vm.sendText(textInput)
            } label: {
                Label("전송", systemImage: "paperplane.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled(textInput.trimmingCharacters(in: .whitespaces).isEmpty)
        }
    }

    // MARK: - 카메라 탭
    var cameraTab: some View {
        NavigationLink(destination: StreamView()) {
            VStack(spacing: 12) {
                Image(systemName: "video.circle.fill")
                    .font(.system(size: 48))
                    .foregroundColor(.blue)
                Text("Ray-Ban 카메라 열기")
                    .fontWeight(.semibold)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 24)
            .background(RoundedRectangle(cornerRadius: 16).fill(Color.blue.opacity(0.08)))
        }
    }

    // MARK: - 결과 카드
    var resultCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("결과")
                    .font(.subheadline)
                    .fontWeight(.semibold)
                Spacer()
                statusBadge
            }

            if !vm.lastMessage.isEmpty {
                Text(vm.lastMessage)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding()
        .background(RoundedRectangle(cornerRadius: 12).fill(Color(.secondarySystemBackground)))
    }

    var statusBadge: some View {
        Group {
            switch vm.state {
            case .idle:
                EmptyView()
            case .connecting, .uploading:
                Label("업로드 중", systemImage: "arrow.up.circle")
                    .foregroundColor(.orange)
            case .processing:
                Label("처리 중", systemImage: "gearshape")
                    .foregroundColor(.blue)
            case .done:
                Label("완료", systemImage: "checkmark.circle.fill")
                    .foregroundColor(.green)
            case .failed:
                Label("오류", systemImage: "xmark.circle.fill")
                    .foregroundColor(.red)
            case .ready:
                EmptyView()
            }
        }
        .font(.caption)
    }
}
