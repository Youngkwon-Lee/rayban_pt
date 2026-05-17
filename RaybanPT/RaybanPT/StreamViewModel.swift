import Foundation
import Observation
import SwiftUI
import UIKit
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

    private var deviceSession: DeviceSession?
    private var stream: MWDATCamera.Stream?
    private var deviceSelector: AutoDeviceSelector?
    private var stateToken: (any AnyListenerToken)?
    private var frameToken: (any AnyListenerToken)?
    private var errorToken: (any AnyListenerToken)?
    private var photoToken: (any AnyListenerToken)?
    private var deviceTask: Task<Void, Never>?
    private var demoFrameTask: Task<Void, Never>?

    private var wearables: any WearablesInterface { Wearables.shared }

    func setup() {
        if DemoConfig.isGlassDemoEnabled {
            enableGlassDemoMode()
            return
        }

        guard stream == nil else { return }

        let selector = AutoDeviceSelector(wearables: wearables)
        deviceSelector = selector

        deviceTask = Task { [weak self] in
            for await device in selector.activeDeviceStream() {
                guard let self else { return }
                self.hasActiveDevice = device != nil
                self.statusMessage = device != nil ? "기기 준비됨" : "대기 중"
            }
        }
    }

    func startStreaming() async {
        if DemoConfig.isGlassDemoEnabled {
            enableGlassDemoMode()
            return
        }

        guard stream == nil, let selector = deviceSelector else { return }
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

        let session: DeviceSession
        do {
            session = try wearables.createSession(deviceSelector: selector)
            deviceSession = session
        } catch {
            statusMessage = "세션 생성 실패: \(error.localizedDescription)"
            return
        }

        do {
            try session.start()
        } catch {
            statusMessage = "세션 시작 실패: \(error.localizedDescription)"
            deviceSession = nil
            return
        }

        if session.state != .started {
            statusMessage = "기기 연결 중..."
            for await state in session.stateStream() {
                if state == .started { break }
                if state == .stopped {
                    statusMessage = "기기 연결 실패"
                    deviceSession = nil
                    return
                }
            }
        }

        let config = StreamConfiguration(videoCodec: .raw, resolution: .low, frameRate: 24)
        guard let newStream = try? session.addStream(config: config) else {
            statusMessage = "스트림 추가 실패"
            return
        }
        stream = newStream

        stateToken = newStream.statePublisher.listen { [weak self] state in
            Task { [weak self, state] in
                guard let self else { return }
                await self.handleStateUpdate(state)
            }
        }
        frameToken = newStream.videoFramePublisher.listen { [weak self] frame in
            Task { [weak self, frame] in
                guard let self else { return }
                await self.handleIncomingFrame(frame.makeUIImage())
            }
        }
        errorToken = newStream.errorPublisher.listen { [weak self] error in
            Task { [weak self, error] in
                guard let self else { return }
                await self.handleStreamError(error)
            }
        }
        photoToken = newStream.photoDataPublisher.listen { [weak self] photoData in
            Task { [weak self, photoData] in
                guard let self else { return }
                await self.handleCapturedPhotoData(photoData.data)
            }
        }

        await GlassHUDManager.shared.attachDisplay(to: session)
        await newStream.start()
    }

    func stopStreaming() async {
        if DemoConfig.isGlassDemoEnabled {
            if recorder.isRecording {
                recordedVideoURL = await recorder.stop()
            }
            demoFrameTask?.cancel()
            demoFrameTask = nil
            await GlassHUDManager.shared.detachDisplay()
            currentFrame = nil
            isStreaming = false
            statusMessage = "스마트 글라스 데모 중지됨"
            return
        }

        if recorder.isRecording {
            recordedVideoURL = await recorder.stop()
        }

        await GlassHUDManager.shared.detachDisplay()
        clearStreamListeners()

        if let s = stream {
            stream = nil
            await s.stop()
        }
        deviceSession?.stop()
        deviceSession = nil
        currentFrame = nil
        isStreaming = false
    }

    func tearDown() async {
        if recorder.isRecording {
            recordedVideoURL = await recorder.stop()
        }

        demoFrameTask?.cancel()
        demoFrameTask = nil
        deviceTask?.cancel()
        deviceTask = nil

        await GlassHUDManager.shared.detachDisplay()
        clearStreamListeners()

        if let s = stream {
            stream = nil
            await s.stop()
        }
        deviceSession?.stop()
        deviceSession = nil

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
        if DemoConfig.isGlassDemoEnabled {
            capturedPhoto = currentFrame
            statusMessage = "스마트 글라스 사진 캡처됨"
            return
        }
        _ = stream?.capturePhoto(format: .jpeg)
    }

    func usePhoneCameraPhoto(_ image: UIImage) {
        lastSavedPhoto = nil
        capturedPhoto = image
        statusMessage = "iPhone 사진 준비됨"
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

    private func updateState(_ state: StreamState) {
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

    private func handleStateUpdate(_ state: StreamState) {
        updateState(state)
    }

    private func handleIncomingFrame(_ image: UIImage?) {
        currentFrame = image
        if let image {
            recorder.addFrame(image)
        }
    }

    private func handleStreamError(_ error: StreamError) {
        errorMessage = error.localizedDescription
        statusMessage = "오류: \(error.localizedDescription)"
    }

    private func handleCapturedPhotoData(_ data: Data) {
        capturedPhoto = UIImage(data: data)
    }

    private func clearStreamListeners() {
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
        Task { await token.cancel() }
    }

    // MARK: - Demo mode

    private func enableGlassDemoMode() {
        hasActiveDevice = true
        isStreaming = true
        errorMessage = nil
        statusMessage = "스마트 글라스 라이브 수신 중"

        guard demoFrameTask == nil else { return }
        currentFrame = makeCurrentDemoFrame(index: 0)
        demoFrameTask = Task { @MainActor [weak self] in
            var index = 1
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 650_000_000)
                guard let self else { return }
                self.currentFrame = self.makeCurrentDemoFrame(index: index)
                self.statusMessage = "스마트 글라스 라이브 수신 중 · \(index)f"
                index += 1
            }
        }
        Task {
            await GlassHUDManager.shared.attachSimulatedDisplay()
        }
    }

    private func makeCurrentDemoFrame(index: Int) -> UIImage {
        if DemoConfig.usesMaskedCaptureFrame, let maskedImage = loadDemoMaskedCapture() {
            return makeMaskedCaptureDemoFrame(maskedImage, index: index)
        }
        return makeDemoFrame(index: index)
    }

    private func loadDemoMaskedCapture() -> UIImage? {
        if let image = UIImage(named: "DemoMaskedCapture") {
            return image
        }
        if let image = UIImage(named: "DemoAssets/DemoMaskedCapture") {
            return image
        }
        if let url = Bundle.main.url(forResource: "DemoMaskedCapture", withExtension: "jpg", subdirectory: "DemoAssets") {
            return UIImage(contentsOfFile: url.path)
        }
        if let url = Bundle.main.url(forResource: "DemoMaskedCapture", withExtension: "jpg") {
            return UIImage(contentsOfFile: url.path)
        }
        return nil
    }

    private func makeMaskedCaptureDemoFrame(_ image: UIImage, index: Int) -> UIImage {
        let size = CGSize(width: 720, height: 1565)
        let renderer = UIGraphicsImageRenderer(size: size)
        return renderer.image { context in
            let cg = context.cgContext
            drawDemoBackground(in: cg, size: size)

            let photoRect = CGRect(x: 56, y: 315, width: 608, height: 920)
            let photoPath = UIBezierPath(roundedRect: photoRect, cornerRadius: 32)
            UIColor(red: 0.02, green: 0.03, blue: 0.07, alpha: 1).setFill()
            photoPath.fill()
            photoPath.addClip()

            drawImage(image, filling: photoRect)

            cg.resetClip()
            UIColor(red: 0.05, green: 0.72, blue: 0.56, alpha: 0.92).setStroke()
            cg.setLineWidth(4)
            photoPath.stroke()

            drawMaskedCaptureTelemetry(size: size, index: index, photoRect: photoRect)
        }
    }

    private func drawDemoBackground(in cg: CGContext, size: CGSize) {
        let colors = [
            UIColor(red: 0.02, green: 0.03, blue: 0.07, alpha: 1).cgColor,
            UIColor(red: 0.04, green: 0.12, blue: 0.17, alpha: 1).cgColor,
            UIColor(red: 0.04, green: 0.05, blue: 0.12, alpha: 1).cgColor
        ]
        let gradient = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: colors as CFArray, locations: [0, 0.55, 1])!
        cg.drawLinearGradient(gradient, start: CGPoint(x: 0, y: 0), end: CGPoint(x: size.width, y: size.height), options: [])

        UIColor.white.withAlphaComponent(0.055).setStroke()
        for x in stride(from: 0, through: size.width, by: 80) {
            cg.move(to: CGPoint(x: x, y: 0))
            cg.addLine(to: CGPoint(x: x, y: size.height))
        }
        for y in stride(from: 0, through: size.height, by: 80) {
            cg.move(to: CGPoint(x: 0, y: y))
            cg.addLine(to: CGPoint(x: size.width, y: y))
        }
        cg.setLineWidth(1)
        cg.strokePath()
    }

    private func drawImage(_ image: UIImage, filling rect: CGRect) {
        guard image.size.width > 0, image.size.height > 0 else { return }
        let imageRatio = image.size.width / image.size.height
        let targetRatio = rect.width / rect.height
        var drawRect = rect
        if imageRatio > targetRatio {
            let width = rect.height * imageRatio
            drawRect = CGRect(x: rect.midX - width / 2, y: rect.minY, width: width, height: rect.height)
        } else {
            let height = rect.width / imageRatio
            drawRect = CGRect(x: rect.minX, y: rect.midY - height / 2, width: rect.width, height: height)
        }
        image.draw(in: drawRect)
    }

    private func drawMaskedCaptureTelemetry(size: CGSize, index: Int, photoRect: CGRect) {
        let liveRect = CGRect(x: 34, y: 38, width: 210, height: 44)
        UIColor(red: 0.03, green: 0.06, blue: 0.12, alpha: 0.82).setFill()
        UIBezierPath(roundedRect: liveRect, cornerRadius: 22).fill()
        UIColor(red: 0.05, green: 0.72, blue: 0.56, alpha: 1).setFill()
        UIBezierPath(ovalIn: CGRect(x: 52, y: 54, width: 12, height: 12)).fill()
        drawDemoText("LIVE · MASKED", at: CGPoint(x: 76, y: 50), size: 17, weight: .bold)

        let frameText = "24 fps · masked frame \(String(format: "%04d", index))"
        drawDemoText(frameText, at: CGPoint(x: 34, y: 98), size: 16, color: UIColor.white.withAlphaComponent(0.66))

        let safeRect = CGRect(x: photoRect.minX + 22, y: photoRect.minY + 22, width: 166, height: 44)
        UIColor(red: 0.02, green: 0.03, blue: 0.07, alpha: 0.78).setFill()
        UIBezierPath(roundedRect: safeRect, cornerRadius: 22).fill()
        UIColor(red: 0.05, green: 0.72, blue: 0.56, alpha: 1).setFill()
        UIBezierPath(ovalIn: CGRect(x: safeRect.minX + 18, y: safeRect.minY + 16, width: 12, height: 12)).fill()
        drawDemoText("마스킹 적용", at: CGPoint(x: safeRect.minX + 42, y: safeRect.minY + 10), size: 17, weight: .bold)

        let demoRect = CGRect(x: size.width - 132, y: 38, width: 98, height: 44)
        UIColor(red: 0.35, green: 0.34, blue: 0.84, alpha: 0.95).setFill()
        UIBezierPath(roundedRect: demoRect, cornerRadius: 22).fill()
        drawDemoText("DEMO", at: CGPoint(x: demoRect.minX + 24, y: demoRect.minY + 11), size: 16, weight: .bold)

        UIColor(red: 0.05, green: 0.72, blue: 0.56, alpha: 0.75).setStroke()
        UIBezierPath(roundedRect: photoRect.insetBy(dx: 12, dy: 12), cornerRadius: 24).stroke()
    }

    private func makeDemoFrame(index: Int) -> UIImage {
        let size = CGSize(width: 720, height: 1565)
        let renderer = UIGraphicsImageRenderer(size: size)
        return renderer.image { context in
            let cg = context.cgContext
            drawDemoBackground(in: cg, size: size)
            drawDemoRoom(in: cg, size: size, index: index)
            drawDemoTelemetry(in: cg, size: size, index: index)
        }
    }

    private func drawDemoRoom(in cg: CGContext, size: CGSize, index: Int) {
        let pulse = CGFloat((index % 8)) / 8

        UIColor(red: 0.12, green: 0.18, blue: 0.29, alpha: 0.84).setFill()
        UIBezierPath(roundedRect: CGRect(x: 86, y: 650, width: 548, height: 130), cornerRadius: 28).fill()

        UIColor(red: 0.25, green: 0.45, blue: 0.95, alpha: 0.24 + pulse * 0.16).setFill()
        UIBezierPath(ovalIn: CGRect(x: 172, y: 210, width: 376, height: 376)).fill()

        UIColor(red: 0.78, green: 0.84, blue: 0.93, alpha: 0.72).setStroke()
        cg.setLineWidth(10)
        cg.setLineCap(.round)
        cg.move(to: CGPoint(x: 360, y: 305))
        cg.addLine(to: CGPoint(x: 360, y: 512))
        cg.move(to: CGPoint(x: 360, y: 386))
        cg.addLine(to: CGPoint(x: 266, y: 452))
        cg.move(to: CGPoint(x: 360, y: 386))
        cg.addLine(to: CGPoint(x: 454, y: 448))
        cg.move(to: CGPoint(x: 360, y: 512))
        cg.addLine(to: CGPoint(x: 294, y: 654))
        cg.move(to: CGPoint(x: 360, y: 512))
        cg.addLine(to: CGPoint(x: 436, y: 650))
        cg.strokePath()

        UIColor(red: 0.85, green: 0.91, blue: 1.0, alpha: 0.86).setFill()
        UIBezierPath(ovalIn: CGRect(x: 318, y: 238, width: 84, height: 84)).fill()

        UIColor(red: 0.05, green: 0.72, blue: 0.56, alpha: 0.9).setStroke()
        cg.setLineWidth(4)
        cg.stroke(CGRect(x: 224, y: 204, width: 272, height: 466))

        UIColor(red: 0.95, green: 0.60, blue: 0.18, alpha: 0.9).setStroke()
        cg.setLineWidth(3)
        cg.stroke(CGRect(x: 264, y: 236, width: 192, height: 128))

        let points = [
            CGPoint(x: 360, y: 280),
            CGPoint(x: 360, y: 386),
            CGPoint(x: 266, y: 452),
            CGPoint(x: 454, y: 448),
            CGPoint(x: 360, y: 512),
            CGPoint(x: 294, y: 654),
            CGPoint(x: 436, y: 650)
        ]
        UIColor(red: 0.18, green: 0.64, blue: 0.95, alpha: 1).setFill()
        for point in points {
            UIBezierPath(ovalIn: CGRect(x: point.x - 7, y: point.y - 7, width: 14, height: 14)).fill()
        }
    }

    private func drawDemoTelemetry(in cg: CGContext, size: CGSize, index: Int) {
        let liveRect = CGRect(x: 34, y: 38, width: 202, height: 44)
        UIColor(red: 0.03, green: 0.06, blue: 0.12, alpha: 0.82).setFill()
        UIBezierPath(roundedRect: liveRect, cornerRadius: 22).fill()
        UIColor(red: 0.05, green: 0.72, blue: 0.56, alpha: 1).setFill()
        UIBezierPath(ovalIn: CGRect(x: 52, y: 54, width: 12, height: 12)).fill()
        drawDemoText("LIVE · SMART GLASS", at: CGPoint(x: 76, y: 50), size: 17, weight: .bold)

        let frameText = "24 fps · frame \(String(format: "%04d", index))"
        drawDemoText(frameText, at: CGPoint(x: 34, y: 98), size: 16, color: UIColor.white.withAlphaComponent(0.66))

        let demoRect = CGRect(x: size.width - 132, y: 38, width: 98, height: 44)
        UIColor(red: 0.35, green: 0.34, blue: 0.84, alpha: 0.95).setFill()
        UIBezierPath(roundedRect: demoRect, cornerRadius: 22).fill()
        drawDemoText("DEMO", at: CGPoint(x: demoRect.minX + 24, y: demoRect.minY + 11), size: 16, weight: .bold)

        UIColor(red: 0.03, green: 0.06, blue: 0.12, alpha: 0.74).setFill()
        UIBezierPath(roundedRect: CGRect(x: 34, y: size.height - 118, width: size.width - 68, height: 72), cornerRadius: 22).fill()
        drawDemoText("스마트 글라스 카메라 연결됨", at: CGPoint(x: 62, y: size.height - 98), size: 22, weight: .bold)
        drawDemoText("라이브 프레임 수신 · 동의 확인 후 분석 전송 가능", at: CGPoint(x: 62, y: size.height - 66), size: 16, color: UIColor.white.withAlphaComponent(0.68))
    }

    private func drawDemoText(_ text: String, at point: CGPoint, size: CGFloat, weight: UIFont.Weight = .regular, color: UIColor = .white) {
        let attrs: [NSAttributedString.Key: Any] = [
            .font: UIFont.systemFont(ofSize: size, weight: weight),
            .foregroundColor: color
        ]
        text.draw(at: point, withAttributes: attrs)
    }
}
