import SwiftUI
import UniformTypeIdentifiers
import MWDATCore

struct M2_TestView: View {
    @StateObject private var vm: AdapterViewModel
    @Environment(DeviceSessionManager.self) private var deviceManager
    @State private var selectedTab: Tab = .camera
    @State private var showServerSetup = false

    enum Tab { case audio, text, camera, charts }

    static var defaultBridgeURL: URL {
        let stored = UserDefaults.standard.string(forKey: "bridge_base_url") ?? ""
        return URL(string: stored) ?? URL(string: "http://localhost:8791")!
    }

    static var defaultAPIKey: String {
        UserDefaults.standard.string(forKey: "bridge_api_key") ?? ""
    }

    init(baseURL: URL = M2_TestView.defaultBridgeURL, apiKey: String = M2_TestView.defaultAPIKey) {
        _vm = StateObject(wrappedValue: AdapterViewModel(client: BridgeClient(baseURL: baseURL, apiKey: apiKey)))
    }

    /// 서버 URL 미설정 시 첫 실행에 setup sheet 자동 표시
    var needsServerSetup: Bool {
        let stored = UserDefaults.standard.string(forKey: "bridge_base_url") ?? ""
        return stored.isEmpty
    }

    var body: some View {
        TabView(selection: $selectedTab) {
            // 카메라 탭
            NavigationStack {
                StreamView(client: vm.client)
            }
            .tabItem { Label("카메라", systemImage: "video.fill") }
            .tag(Tab.camera)

            // 음성 탭
            NavigationStack {
                AudioTab(vm: vm)
            }
            .tabItem { Label("음성", systemImage: "mic.fill") }
            .tag(Tab.audio)

            // 텍스트 탭
            NavigationStack {
                TextTab(vm: vm)
            }
            .tabItem { Label("텍스트", systemImage: "text.bubble.fill") }
            .tag(Tab.text)

            // 차트 탭
            ChartListView(client: vm.client)
            .tabItem { Label("차트", systemImage: "doc.text.fill") }
            .tag(Tab.charts)
        }
        .overlay(alignment: .top) {
            if selectedTab == .camera {
                DeviceStatusBanner(deviceManager: deviceManager)
                    .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .overlay(alignment: .bottomTrailing) {
            // 서버 설정 버튼 (항상 접근 가능)
            Button {
                showServerSetup = true
            } label: {
                Image(systemName: "server.rack")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(needsServerSetup ? .orange : .white.opacity(0.5))
                    .padding(10)
                    .background(needsServerSetup ? Color.orange.opacity(0.2) : DS.ColorToken.surface,
                                in: Circle())
            }
            .padding(.bottom, 68)
            .padding(.trailing, 16)
        }
        .animation(.spring(response: 0.3), value: selectedTab)
        .sheet(isPresented: $showServerSetup) {
            ServerSetupSheet { newURL, newAPIKey in
                UserDefaults.standard.set(newURL, forKey: "bridge_base_url")
                UserDefaults.standard.set(newAPIKey, forKey: "bridge_api_key")
                vm.client.updateBaseURL(URL(string: newURL)!)
                vm.client.updateAPIKey(newAPIKey)
            }
        }
        .onAppear {
            if needsServerSetup { showServerSetup = true }
        }
    }
}

// MARK: - 서버 설정 Sheet

private struct ServerSetupSheet: View {
    let onSave: (String, String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var urlText: String = UserDefaults.standard.string(forKey: "bridge_base_url") ?? ""
    @State private var apiKeyText: String = UserDefaults.standard.string(forKey: "bridge_api_key") ?? ""

    var isValid: Bool {
        URL(string: urlText)?.scheme?.hasPrefix("http") == true
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("http://서버주소:8791", text: $urlText)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                } header: {
                    Text("서버 URL")
                } footer: {
                    Text("Tailscale 연결 시 예시:\nhttp://desktop-xxxxx.tailde3b80.ts.net:8791")
                        .font(.caption)
                }

                Section {
                    TextField("API 키 (선택)", text: $apiKeyText)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                } header: {
                    Text("API 키")
                } footer: {
                    Text("서버에 BRIDGE_API_KEY 설정된 경우 입력하세요.")
                        .font(.caption)
                }

                Section {
                    Button("저장") {
                        let trimmedURL = urlText.trimmingCharacters(in: .whitespacesAndNewlines)
                        let trimmedKey = apiKeyText.trimmingCharacters(in: .whitespacesAndNewlines)
                        onSave(trimmedURL, trimmedKey)
                        dismiss()
                    }
                    .disabled(!isValid)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .foregroundStyle(isValid ? .blue : .gray)
                }
            }
            .navigationTitle("서버 설정")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("닫기") { dismiss() }
                }
            }
        }
    }
}

// MARK: - 기기 상태 배너

private struct DeviceStatusBanner: View {
    let deviceManager: DeviceSessionManager

    var isConnected: Bool { deviceManager.linkState == .connected }

    var body: some View {
        // 연결 끊겼을 때만 표시
        if !isConnected {
            HStack(spacing: DS.Spacing.xs) {
                Image(systemName: "antenna.radiowaves.left.and.right.slash")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(DS.ColorToken.warning)
                Text(deviceManager.statusMessage)
                    .font(.system(size: DS.FontSize.caption, weight: .medium))
                    .foregroundStyle(.white)
                Button("재연결") {
                    deviceManager.retryConnection()
                }
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(DS.ColorToken.warning)
            }
            .padding(.horizontal, DS.Spacing.sm)
            .padding(.vertical, 6)
            .background(DS.ColorToken.surface, in: Capsule())
            .padding(.top, 56) // 내비바 아래
            .transition(.move(edge: .top).combined(with: .opacity))
        }
    }
}

// MARK: - 음성 탭

private struct AudioTab: View {
    @ObservedObject var vm: AdapterViewModel
    @State private var audioRecorder = AudioRecorder()
    @State private var selectedAudioURL: URL?
    @State private var showImporter = false

    var body: some View {
        ScrollView {
            VStack(spacing: 24) {
                // 녹음 버튼
                Button {
                    Task {
                        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
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
                    VStack(spacing: 14) {
                        ZStack {
                            Circle()
                                .fill(audioRecorder.isRecording ? Color.red.opacity(0.12) : Color.blue.opacity(0.10))
                                .frame(width: 100, height: 100)
                            Image(systemName: audioRecorder.isRecording ? "stop.circle.fill" : "mic.circle.fill")
                                .font(.system(size: 56))
                                .foregroundStyle(audioRecorder.isRecording ? .red : .blue)
                                .scaleEffect(audioRecorder.isRecording ? 1.08 : 1.0)
                                .animation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true),
                                           value: audioRecorder.isRecording)
                        }
                        Text(audioRecorder.isRecording ? "중지 & 업로드" : "Ray-Ban 녹음")
                            .font(.headline)
                            .foregroundStyle(audioRecorder.isRecording ? .red : .blue)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 28)
                    .background(
                        RoundedRectangle(cornerRadius: 20, style: .continuous)
                            .fill(audioRecorder.isRecording ? Color.red.opacity(0.06) : Color.blue.opacity(0.06))
                    )
                }
                .buttonStyle(.plain)

                Text(audioRecorder.statusMessage)
                    .font(.caption)
                    .foregroundStyle(.secondary)

                Divider()

                // 파일 선택
                HStack(spacing: 12) {
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
                    Label(u.lastPathComponent, systemImage: "waveform")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                ResultCard(vm: vm)
            }
            .padding(20)
        }
        .navigationTitle("음성 녹음")
        .navigationBarTitleDisplayMode(.large)
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
}

// MARK: - 텍스트 탭

private struct TextTab: View {
    @ObservedObject var vm: AdapterViewModel
    @State private var textInput = ""

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                VStack(alignment: .leading, spacing: 8) {
                    Label("임상 메모", systemImage: "square.and.pencil")
                        .font(.headline)
                    TextField("환자 상태, 치료 내용 등을 입력하세요...", text: $textInput, axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                        .lineLimit(5...12)
                }

                Button {
                    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                    vm.sendText(textInput)
                } label: {
                    Label("전송", systemImage: "paperplane.fill")
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 4)
                }
                .buttonStyle(.borderedProminent)
                .disabled(textInput.trimmingCharacters(in: .whitespaces).isEmpty)

                ResultCard(vm: vm)
            }
            .padding(20)
        }
        .navigationTitle("텍스트 전송")
        .navigationBarTitleDisplayMode(.large)
    }
}

// MARK: - 결과 카드 (공유)

private struct ResultCard: View {
    @ObservedObject var vm: AdapterViewModel

    var body: some View {
        if vm.state != .idle {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("처리 결과")
                        .font(.subheadline)
                        .fontWeight(.semibold)
                    Spacer()
                    statusBadge
                }

                if !vm.lastMessage.isEmpty {
                    Text(vm.lastMessage)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                }
            }
            .padding(16)
            .background(Color(.secondarySystemBackground),
                        in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .transition(.opacity.combined(with: .move(edge: .bottom)))
            .animation(.spring(response: 0.35, dampingFraction: 0.8), value: vm.state)
        }
    }

    @ViewBuilder
    private var statusBadge: some View {
        switch vm.state {
        case .idle, .ready:
            EmptyView()
        case .connecting, .uploading:
            Label("업로드 중", systemImage: "arrow.up.circle")
                .foregroundStyle(.orange)
                .font(.caption)
        case .processing:
            Label("처리 중", systemImage: "gearshape.fill")
                .foregroundStyle(.blue)
                .font(.caption)
        case .done:
            Label("완료", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
                .font(.caption)
        case .failed(let msg):
            Label(msg.isEmpty ? "오류" : "오류", systemImage: "xmark.circle.fill")
                .foregroundStyle(.red)
                .font(.caption)
        }
    }
}
