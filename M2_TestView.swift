import SwiftUI
import UniformTypeIdentifiers

struct M2_TestView: View {
    @StateObject private var vm: AdapterViewModel
    @State private var textInput: String = "환아 김민수 MRN:12345678 보행 불안정, 통증 6점"
    @State private var selectedAudioURL: URL?
    @State private var showImporter = false

    init(baseURL: URL = URL(string: "http://YOUR_SERVER_HOST:8791")!) {
        _vm = StateObject(wrappedValue: AdapterViewModel(client: BridgeClient(baseURL: baseURL)))
    }

    var body: some View {
        NavigationView {
            VStack(spacing: 12) {
                TextField("텍스트 입력", text: $textInput, axis: .vertical)
                    .textFieldStyle(.roundedBorder)

                Button("텍스트 전송") {
                    vm.sendText(textInput)
                }
                .buttonStyle(.borderedProminent)

                Divider()

                HStack {
                    Button("오디오 선택") { showImporter = true }
                    Button("오디오 업로드") {
                        if let u = selectedAudioURL { vm.uploadAudio(fileURL: u) }
                    }
                    .disabled(selectedAudioURL == nil)
                }

                if let u = selectedAudioURL {
                    Text("선택 파일: \(u.lastPathComponent)")
                        .font(.footnote)
                        .foregroundColor(.secondary)
                }

                Divider()

                Text("상태: \(stateText(vm.state))")
                    .font(.headline)
                ScrollView {
                    Text(vm.lastMessage)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(8)
                }
                .background(Color(.secondarySystemBackground))
                .cornerRadius(8)

                Spacer()
            }
            .padding()
            .navigationTitle("Rayban Bridge M2 Test")
            .fileImporter(
                isPresented: $showImporter,
                allowedContentTypes: [.audio],
                allowsMultipleSelection: false
            ) { result in
                switch result {
                case .success(let urls):
                    selectedAudioURL = urls.first
                case .failure(let err):
                    vm.lastMessage = "파일 선택 오류: \(err.localizedDescription)"
                }
            }
        }
    }

    private func stateText(_ state: AdapterState) -> String {
        switch state {
        case .idle: return "idle"
        case .connecting: return "connecting"
        case .ready: return "ready"
        case .uploading: return "uploading"
        case .processing(let id): return "processing(\(id.prefix(8)))"
        case .done: return "done"
        case .failed(let m): return "failed: \(m)"
        }
    }
}
