import AVFoundation
import UIKit
import CoreVideo

@Observable
@MainActor
final class VideoRecorder {

    var isRecording = false
    var frameCount = 0
    var statusMessage = ""

    private var assetWriter: AVAssetWriter?
    private var videoInput: AVAssetWriterInput?
    private var adaptor: AVAssetWriterInputPixelBufferAdaptor?
    private var outputURL: URL?
    private var recordingStartUptime: TimeInterval = 0
    private var lastPresentationTime = CMTime.invalid
    private var targetWidth = 0
    private var targetHeight = 0
    private let timeScale: CMTimeScale = 600

    func start() {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(UUID().uuidString).mp4")

        assetWriter = nil
        videoInput = nil
        adaptor = nil
        outputURL = url
        frameCount = 0
        targetWidth = 0
        targetHeight = 0
        recordingStartUptime = ProcessInfo.processInfo.systemUptime
        lastPresentationTime = .invalid
        isRecording = true
        statusMessage = "글래스 영상 녹화 중"
    }

    /// 스트림 프레임이 올 때마다 호출
    func addFrame(_ image: UIImage, receivedAt uptime: TimeInterval) {
        guard isRecording, let cgImage = image.cgImage else { return }
        if assetWriter == nil {
            let width = max(2, cgImage.width)
            let height = max(2, cgImage.height)
            print("[VideoRecorder] first-frame source=\(width)x\(height)")
            guard prepareWriter(width: width, height: height) else {
                isRecording = false
                return
            }
        }
        guard let input = videoInput,
              input.isReadyForMoreMediaData,
              let adaptor,
              let buffer = pixelBuffer(from: cgImage) else { return }

        var presentationTime = CMTime(
            seconds: max(0, uptime - recordingStartUptime),
            preferredTimescale: timeScale
        )
        if lastPresentationTime.isValid, CMTimeCompare(presentationTime, lastPresentationTime) <= 0 {
            presentationTime = CMTimeAdd(lastPresentationTime, CMTime(value: 1, timescale: timeScale))
        }
        guard adaptor.append(buffer, withPresentationTime: presentationTime) else {
            statusMessage = "영상 프레임 기록 실패"
            return
        }
        lastPresentationTime = presentationTime
        frameCount += 1
    }

    /// 녹화 중지 → MP4 파일 URL 반환
    func stop() async -> URL? {
        isRecording = false
        guard let writer = assetWriter, let input = videoInput, frameCount > 0 else {
            statusMessage = "저장할 영상 프레임 없음"
            clearWriter()
            return nil
        }
        statusMessage = "파일 저장 중..."
        input.markAsFinished()
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            writer.finishWriting { cont.resume() }
        }
        guard writer.status == .completed else {
            statusMessage = "영상 저장 실패"
            clearWriter()
            return nil
        }
        let savedURL = outputURL
        if let savedURL,
           let attributes = try? FileManager.default.attributesOfItem(atPath: savedURL.path),
           let fileSize = attributes[.size] as? NSNumber {
            print("[VideoRecorder] recording-stopped frames=\(frameCount) bytes=\(fileSize.intValue) path=\(savedURL.path)")
        } else {
            print("[VideoRecorder] recording-stopped frames=\(frameCount) bytes=0 path=missing")
        }
        let seconds = lastPresentationTime.isValid ? max(0, CMTimeGetSeconds(lastPresentationTime)) : 0
        statusMessage = "\(frameCount)프레임 (\(Int(seconds.rounded()))초) 저장됨"
        clearWriter(keepingOutputURL: true)
        return savedURL
    }

    private func prepareWriter(width: Int, height: Int) -> Bool {
        guard let outputURL else { return false }
        // Keep portrait glass frames within a conservative H.264 envelope.
        // Some iPhone hardware encoders reject very tall, non-macroblock-sized
        // inputs even though the source dimensions are otherwise valid.
        let scale = min(1.0, 1280.0 / Double(max(width, height)))
        let scaledWidth = max(16, Int(Double(width) * scale))
        let scaledHeight = max(16, Int(Double(height) * scale))
        targetWidth = max(16, scaledWidth - (scaledWidth % 16))
        targetHeight = max(16, scaledHeight - (scaledHeight % 16))

        do {
            try? FileManager.default.removeItem(at: outputURL)
            let writer = try AVAssetWriter(outputURL: outputURL, fileType: .mp4)
            let videoSettings: [String: Any] = [
                AVVideoCodecKey: AVVideoCodecType.h264,
                AVVideoWidthKey: targetWidth,
                AVVideoHeightKey: targetHeight,
                AVVideoCompressionPropertiesKey: [
                    AVVideoAverageBitRateKey: 1_200_000,
                    AVVideoMaxKeyFrameIntervalKey: 24,
                ],
            ]
            let input = AVAssetWriterInput(mediaType: .video, outputSettings: videoSettings)
            input.expectsMediaDataInRealTime = true
            guard writer.canAdd(input) else {
                statusMessage = "영상 인코더 준비 실패"
                return false
            }
            writer.add(input)

            let attributes: [String: Any] = [
                kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32BGRA),
                kCVPixelBufferWidthKey as String: targetWidth,
                kCVPixelBufferHeightKey as String: targetHeight,
            ]
            let newAdaptor = AVAssetWriterInputPixelBufferAdaptor(
                assetWriterInput: input,
                sourcePixelBufferAttributes: attributes
            )
            guard writer.startWriting() else {
                let reason = writer.error?.localizedDescription ?? "unknown"
                print("[VideoRecorder] startWriting failed: \(reason) size=\(targetWidth)x\(targetHeight) url=\(outputURL.path)")
                statusMessage = "영상 파일 시작 실패: \(reason)"
                return false
            }
            writer.startSession(atSourceTime: .zero)
            assetWriter = writer
            videoInput = input
            adaptor = newAdaptor
            return true
        } catch {
            statusMessage = "영상 파일 준비 실패"
            return false
        }
    }

    private func clearWriter(keepingOutputURL: Bool = false) {
        assetWriter = nil
        videoInput = nil
        adaptor = nil
        if !keepingOutputURL { outputURL = nil }
    }

    private func pixelBuffer(from image: CGImage) -> CVPixelBuffer? {
        var pb: CVPixelBuffer?
        let attrs: [String: Any] = [
            kCVPixelBufferCGImageCompatibilityKey as String: true,
            kCVPixelBufferCGBitmapContextCompatibilityKey as String: true,
        ]
        guard CVPixelBufferCreate(
            kCFAllocatorDefault,
            targetWidth, targetHeight,
            kCVPixelFormatType_32BGRA,
            attrs as CFDictionary,
            &pb
        ) == kCVReturnSuccess, let buffer = pb else { return nil }

        CVPixelBufferLockBaseAddress(buffer, [])
        defer { CVPixelBufferUnlockBaseAddress(buffer, []) }

        guard let ctx = CGContext(
            data: CVPixelBufferGetBaseAddress(buffer),
            width: targetWidth,
            height: targetHeight,
            bitsPerComponent: 8,
            bytesPerRow: CVPixelBufferGetBytesPerRow(buffer),
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue
                      | CGBitmapInfo.byteOrder32Little.rawValue
        ) else { return nil }

        ctx.draw(image, in: CGRect(x: 0, y: 0, width: targetWidth, height: targetHeight))
        return buffer
    }
}
