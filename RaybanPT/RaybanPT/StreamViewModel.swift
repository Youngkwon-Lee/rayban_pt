import Foundation
import Observation
import SwiftUI
import MWDATCore
import MWDATCamera

@Observable
@MainActor
final class StreamViewModel {

    var currentFrame: UIImage? = nil
    var isStreaming = false
    var statusMessage = "대기 중"
    var capturedPhoto: UIImage? = nil
    var hasActiveDevice = false
    var errorMessage: String? = nil
    var recorder = VideoRecorder()
    var recordedVideoURL: URL? = nil

    private var streamSession: StreamSession?
    private var stateToken: (any AnyListenerToken)?
    private var frameToken: (any AnyListenerToken)?
    private var errorToken: (any AnyListenerToken)?
    private var photoToken: (any AnyListenerToken)?
    private var deviceTask: Task<Void, Never>?

    private var wearables: any WearablesInterface { Wearables.shared }

    func setup() {
        let selector = AutoDeviceSelector(wearables: wearables)
        let config = StreamSessionConfig(
            videoCodec: .raw,
            resolution: .low,
            frameRate: 24
        )
        let session = StreamSession(streamSessionConfig: config, deviceSelector: selector)
        self.streamSession = session

        deviceTask = Task {
            for await device in selector.activeDeviceStream() {
                self.hasActiveDevice = device != nil
                self.statusMessage = device != nil ? "기기 준비됨" : "기기 없음"
            }
        }

        stateToken = session.statePublisher.listen { [weak self] state in
            Task { @MainActor in
                self?.updateState(state)
            }
        }

        frameToken = session.videoFramePublisher.listen { [weak self] frame in
            Task { @MainActor in
                let img = frame.makeUIImage()
                self?.currentFrame = img
                // 녹화 중이면 프레임 추가
                if let img { self?.recorder.addFrame(img) }
            }
        }

        errorToken = session.errorPublisher.listen { [weak self] error in
            Task { @MainActor in
                self?.errorMessage = error.localizedDescription
                self?.statusMessage = "오류: \(error.localizedDescription)"
            }
        }

        photoToken = session.photoDataPublisher.listen { [weak self] photoData in
            Task { @MainActor in
                self?.capturedPhoto = UIImage(data: photoData.data)
            }
        }

        updateState(session.state)
    }

    func startStreaming() async {
        guard let session = streamSession else { return }
        errorMessage = nil

        do {
            let status = try await wearables.checkPermissionStatus(.camera)
            if status != .granted {
                let requested = try await wearables.requestPermission(.camera)
                guard requested == .granted else {
                    statusMessage = "카메라 권한 거부됨"
                    return
                }
            }
        } catch {
            statusMessage = "권한 오류: \(error.localizedDescription)"
            return
        }

        await session.start()
    }

    func stopStreaming() async {
        if recorder.isRecording {
            recordedVideoURL = await recorder.stop()
        }
        await streamSession?.stop()
        currentFrame = nil
    }

    func startRecording() {
        recordedVideoURL = nil
        recorder.start()
    }

    func stopRecording() async {
        recordedVideoURL = await recorder.stop()
    }

    func capturePhoto() {
        streamSession?.capturePhoto(format: .jpeg)
    }

    func savePhoto() {
        guard let photo = capturedPhoto else { return }
        UIImageWriteToSavedPhotosAlbum(photo, nil, nil, nil)
        statusMessage = "사진 저장됨"
    }

    private func updateState(_ state: StreamSessionState) {
        switch state {
        case .stopped:
            isStreaming = false
            statusMessage = "스트리밍 중지됨"
        case .waitingForDevice:
            isStreaming = false
            statusMessage = "기기 대기 중..."
        case .starting:
            statusMessage = "시작 중..."
        case .stopping:
            statusMessage = "중지 중..."
        case .paused:
            statusMessage = "일시정지"
        case .streaming:
            isStreaming = true
            statusMessage = "✅ 스트리밍 중"
        @unknown default:
            break
        }
    }
}
