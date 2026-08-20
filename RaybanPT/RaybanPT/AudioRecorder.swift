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
    private var selectedInputUID: String?
    private var routeObserver: NSObjectProtocol?

    override init() {
        super.init()
        routeObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: AVAudioSession.sharedInstance(),
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.validateActiveRoute()
            }
        }
    }

    func requestPermission() async -> Bool {
        await AVAudioApplication.requestRecordPermission()
    }

    @discardableResult
    func startRecording() async -> Bool {
        guard !isRecording else { return true }
        recordedFileURL = nil
        currentFileURL = nil
        selectedInputUID = nil
        guard await requestPermission() else {
            statusMessage = "마이크 권한 없음"
            return false
        }

        let session = AVAudioSession.sharedInstance()
        var selectedInput: AVAudioSessionPortDescription?
        do {
            try session.setCategory(.playAndRecord, mode: .spokenAudio, options: [.allowBluetoothHFP])
            try session.setPreferredSampleRate(8_000)
            try session.setActive(true, options: .notifyOthersOnDeactivation)
            logRoute("session-active", session: session)
            guard let glassesInput = await waitForRayBanInput(session) else {
                try? session.setActive(false, options: .notifyOthersOnDeactivation)
                statusMessage = "Ray-Ban Meta 마이크 연결 필요"
                logRoute("rayban-hfp-input-missing", session: session)
                return false
            }
            try session.setPreferredInput(glassesInput)
            selectedInput = glassesInput
            selectedInputUID = glassesInput.uid
            statusMessage = "글래스 마이크 연결 중"
            print("[AudioRecorder] selected HFP input name=\(glassesInput.portName) uid=\(glassesInput.uid)")
        } catch {
            statusMessage = "글래스 마이크 설정 실패: \(error.localizedDescription)"
            return false
        }

        let fileName = "glass_\(Int(Date().timeIntervalSince1970)).wav"
        let fileURL = FileManager.default.temporaryDirectory.appendingPathComponent(fileName)
        currentFileURL = fileURL

        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatLinearPCM),
            AVSampleRateKey: 8000,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
        ]

        do {
            recorder = try AVAudioRecorder(url: fileURL, settings: settings)
            recorder?.delegate = self
            guard recorder?.prepareToRecord() == true, recorder?.record() == true else {
                throw CocoaError(.fileWriteUnknown)
            }
            isRecording = true
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            guard isActiveGlassesRoute else {
                recorder?.stop()
                recorder = nil
                isRecording = false
                currentFileURL = nil
                selectedInputUID = nil
                try? FileManager.default.removeItem(at: fileURL)
                try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
                statusMessage = "글래스 마이크 연결 실패"
                logRoute("hfp-route-validation-failed", session: session)
                return false
            }
            statusMessage = "\(selectedInput?.portName ?? "글래스") 마이크 녹음 중"
            logRoute("recording-started", session: session)
            return true
        } catch {
            recorder = nil
            isRecording = false
            currentFileURL = nil
            selectedInputUID = nil
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            statusMessage = "글래스 녹음 시작 실패: \(error.localizedDescription)"
            return false
        }
    }

    func stopRecording(statusMessage: String = "녹음 완료 → 업로드 준비") {
        recorder?.stop()
        recorder = nil
        isRecording = false
        recordedFileURL = currentFileURL
        selectedInputUID = nil
        self.statusMessage = statusMessage
        if let recordedFileURL,
           let attributes = try? FileManager.default.attributesOfItem(atPath: recordedFileURL.path),
           let fileSize = attributes[.size] as? NSNumber {
            print("[AudioRecorder] recording-stopped bytes=\(fileSize.intValue) path=\(recordedFileURL.path)")
        } else {
            print("[AudioRecorder] recording-stopped bytes=0 path=missing")
        }
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private var isActiveGlassesRoute: Bool {
        guard let selectedInputUID else { return false }
        return AVAudioSession.sharedInstance().currentRoute.inputs.contains {
            $0.portType == .bluetoothHFP && $0.uid == selectedInputUID
        }
    }

    private func waitForRayBanInput(_ session: AVAudioSession) async -> AVAudioSessionPortDescription? {
        for _ in 0..<16 {
            if let input = session.availableInputs?.first(where: isRayBanHFPInput) {
                return input
            }
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
        return nil
    }

    private func isRayBanHFPInput(_ input: AVAudioSessionPortDescription) -> Bool {
        guard input.portType == .bluetoothHFP else { return false }
        let identity = "\(input.portName) \(input.uid)".lowercased()
        return identity.contains("ray-ban") || identity.contains("rayban") || identity.contains("meta")
    }

    private func validateActiveRoute() {
        guard isRecording, !isActiveGlassesRoute else { return }
        logRoute("hfp-route-dropped", session: AVAudioSession.sharedInstance())
        let interruptedURL = currentFileURL
        currentFileURL = nil
        stopRecording(statusMessage: "글래스 마이크 연결이 끊겨 녹음을 중지했습니다")
        if let interruptedURL {
            try? FileManager.default.removeItem(at: interruptedURL)
        }
    }

    private func logRoute(_ label: String, session: AVAudioSession) {
        let inputs = (session.availableInputs ?? []).map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ",")
        let current = session.currentRoute.inputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ",")
        print("[AudioRecorder] route=\(label) available=\(inputs.isEmpty ? "none" : inputs) current=\(current.isEmpty ? "none" : current)")
    }
}

extension AudioRecorder: AVAudioRecorderDelegate {
    nonisolated func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder, successfully flag: Bool) {
        Task { @MainActor in
            if !flag {
                self.recorder = nil
                self.isRecording = false
                self.currentFileURL = nil
                self.selectedInputUID = nil
                try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
                self.statusMessage = "녹음 실패"
            }
        }
    }
}
