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
    var lastSavedPhoto: SavedCapture? = nil
    var lastSavedVideo: SavedCapture? = nil

    private var streamSession: StreamSession?
    private var stateToken: (any AnyListenerToken)?
    private var frameToken: (any AnyListenerToken)?
    private var errorToken: (any AnyListenerToken)?
    private var photoToken: (any AnyListenerToken)?
    private var deviceTask: Task<Void, Never>?

    private var wearables: any WearablesInterface { Wearables.shared }

    func setup() {
        guard streamSession == nil else { return }

        let selector = AutoDeviceSelector(wearables: wearables)
        let config = StreamSessionConfig(
            videoCodec: .raw,
            resolution: .low,
            frameRate: 24
        )
        let session = StreamSession(streamSessionConfig: config, deviceSelector: selector)
        self.streamSession = session

        deviceTask = Task { [weak self] in
            for await device in selector.activeDeviceStream() {
                guard let self else { return }
                self.hasActiveDevice = device != nil
                self.statusMessage = device != nil ? "기기 준비됨" : "대기 중"
            }
        }

        stateToken = session.statePublisher.listen { [weak self] state in
            Task { [weak self, state] in
                guard let self else { return }
                await self.handleStateUpdate(state)
            }
        }

        frameToken = session.videoFramePublisher.listen { [weak self] frame in
            Task { [weak self, frame] in
                guard let self else { return }
                let img = frame.makeUIImage()
                await self.handleIncomingFrame(img)
            }
        }

        errorToken = session.errorPublisher.listen { [weak self] error in
            Task { [weak self, error] in
                guard let self else { return }
                await self.handleStreamError(error)
            }
        }

        photoToken = session.photoDataPublisher.listen { [weak self] photoData in
            Task { [weak self, photoData] in
                guard let self else { return }
                await self.handleCapturedPhotoData(photoData.data)
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
        isStreaming = false
    }

    func tearDown() async {
        if recorder.isRecording {
            recordedVideoURL = await recorder.stop()
        }

        await streamSession?.stop()
        releaseResources()
        currentFrame = nil
        capturedPhoto = nil
        isStreaming = false
        hasActiveDevice = false
        errorMessage = nil
        statusMessage = "대기 중"
    }

    func startRecording() {
        recordedVideoURL = nil
        lastSavedVideo = nil
        recorder.start()
    }

    func stopRecording() async {
        recordedVideoURL = await recorder.stop()
    }

    func capturePhoto() {
        lastSavedPhoto = nil
        streamSession?.capturePhoto(format: .jpeg)
    }

    func saveCapturedPhoto(patientName: String?, eventId: String? = nil) async throws -> SavedCapture {
        if let lastSavedPhoto {
            return lastSavedPhoto
        }

        guard let photo = capturedPhoto else {
            throw MediaSaveError.missingPhoto
        }

        let capture = try await CapturePersistence.persistPhoto(photo, patientName: patientName, eventId: eventId)
        CaptureStore.shared.record(capture)
        lastSavedPhoto = capture
        statusMessage = "사진 저장됨"
        return capture
    }

    func saveRecordedVideo(patientName: String?, eventId: String? = nil) async throws -> SavedCapture {
        if let lastSavedVideo {
            return lastSavedVideo
        }

        guard let recordedVideoURL else {
            throw MediaSaveError.missingVideo
        }

        let capture = try await CapturePersistence.persistVideo(recordedVideoURL, patientName: patientName, eventId: eventId)
        CaptureStore.shared.record(capture)
        lastSavedVideo = capture
        statusMessage = "영상 저장됨"
        return capture
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

    private func handleStateUpdate(_ state: StreamSessionState) {
        updateState(state)
    }

    private func handleIncomingFrame(_ image: UIImage?) {
        currentFrame = image
        if let image {
            recorder.addFrame(image)
        }
    }

    private func handleStreamError(_ error: Error) {
        errorMessage = error.localizedDescription
        statusMessage = "오류: \(error.localizedDescription)"
    }

    private func handleCapturedPhotoData(_ data: Data) {
        capturedPhoto = UIImage(data: data)
    }

    private func releaseResources() {
        deviceTask?.cancel()
        deviceTask = nil
        streamSession = nil
        cancel(token: stateToken)
        cancel(token: frameToken)
        cancel(token: errorToken)
        cancel(token: photoToken)
        stateToken = nil
        frameToken = nil
        errorToken = nil
        photoToken = nil
    }

    private func cancel(token: (any AnyListenerToken)?) {
        guard let token else { return }

        Task {
            await token.cancel()
        }
    }
}
