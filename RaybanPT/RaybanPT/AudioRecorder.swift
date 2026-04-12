import Foundation
import AVFoundation
import Observation

@Observable
@MainActor
final class AudioRecorder: NSObject {

    var isRecording = false
    var statusMessage = "대기 중"
    var recordedFileURL: URL? = nil

    private var recorder: AVAudioRecorder?
    private var currentFileURL: URL?

    override init() {
        super.init()
    }

    func requestPermission() async -> Bool {
        await AVAudioApplication.requestRecordPermission()
    }

    func startRecording() async {
        guard await requestPermission() else {
            statusMessage = "마이크 권한 없음"
            return
        }

        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playAndRecord, mode: .default, options: [.allowBluetooth, .allowBluetoothA2DP])
            try session.setActive(true)

            // Ray-Ban 마이크 우선 선택
            if let raybanInput = session.availableInputs?.first(where: { $0.portType == .bluetoothHFP || $0.portType == .bluetoothLE }) {
                try session.setPreferredInput(raybanInput)
                statusMessage = "🎙 \(raybanInput.portName) 녹음 중..."
                print("[Audio] Ray-Ban 마이크 선택: \(raybanInput.portName)")
            } else {
                statusMessage = "🎙 iPhone 마이크 녹음 중..."
                print("[Audio] 가용 입력: \(session.availableInputs?.map { $0.portName } ?? [])")
            }

        } catch {
            statusMessage = "세션 설정 오류: \(error.localizedDescription)"
            return
        }

        let fileName = "rayban_\(Int(Date().timeIntervalSince1970)).wav"
        let fileURL = FileManager.default.temporaryDirectory.appendingPathComponent(fileName)
        currentFileURL = fileURL

        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatLinearPCM),
            AVSampleRateKey: 16000,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
        ]

        do {
            recorder = try AVAudioRecorder(url: fileURL, settings: settings)
            recorder?.delegate = self
            recorder?.record()
            isRecording = true
        } catch {
            statusMessage = "녹음 시작 오류: \(error.localizedDescription)"
        }
    }

    func stopRecording() {
        recorder?.stop()
        recorder = nil
        isRecording = false
        recordedFileURL = currentFileURL
        statusMessage = "녹음 완료 → 업로드 준비"
    }
}

extension AudioRecorder: AVAudioRecorderDelegate {
    nonisolated func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder, successfully flag: Bool) {
        Task { @MainActor in
            if !flag {
                self.statusMessage = "녹음 실패"
            }
        }
    }
}
