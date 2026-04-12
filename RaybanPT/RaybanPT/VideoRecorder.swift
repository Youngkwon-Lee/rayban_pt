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

    // 레이번 저해상도 스트림 기준
    private let targetWidth  = 640
    private let targetHeight = 480
    private let frameRate: CMTimeScale = 24

    func start() {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(UUID().uuidString).mp4")

        guard let writer = try? AVAssetWriter(outputURL: url, fileType: .mp4) else {
            statusMessage = "녹화 시작 실패"
            return
        }

        let videoSettings: [String: Any] = [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey:  targetWidth,
            AVVideoHeightKey: targetHeight,
            AVVideoCompressionPropertiesKey: [
                AVVideoAverageBitRateKey: 800_000,   // 0.8 Mbps
                AVVideoMaxKeyFrameIntervalKey: 24,
            ],
        ]
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: videoSettings)
        input.expectsMediaDataInRealTime = true

        let srcAttrs: [String: Any] = [
            kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32BGRA),
            kCVPixelBufferWidthKey  as String: targetWidth,
            kCVPixelBufferHeightKey as String: targetHeight,
        ]
        let adp = AVAssetWriterInputPixelBufferAdaptor(
            assetWriterInput: input,
            sourcePixelBufferAttributes: srcAttrs
        )

        writer.add(input)
        writer.startWriting()
        writer.startSession(atSourceTime: .zero)

        assetWriter = writer
        videoInput  = input
        adaptor     = adp
        outputURL   = url
        frameCount  = 0
        isRecording = true
        statusMessage = "🔴 녹화 중..."
    }

    /// 스트림 프레임이 올 때마다 호출
    func addFrame(_ image: UIImage) {
        guard isRecording,
              let input = videoInput,
              input.isReadyForMoreMediaData,
              let adp = adaptor else { return }

        let pts = CMTime(value: CMTimeValue(frameCount), timescale: frameRate)
        if let buf = pixelBuffer(from: image) {
            adp.append(buf, withPresentationTime: pts)
            frameCount += 1
        }
    }

    /// 녹화 중지 → MP4 파일 URL 반환
    func stop() async -> URL? {
        guard let writer = assetWriter, let input = videoInput else { return nil }
        isRecording = false
        statusMessage = "파일 저장 중..."
        input.markAsFinished()
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            writer.finishWriting { cont.resume() }
        }
        let secs = frameCount / Int(frameRate)
        statusMessage = "✅ \(frameCount)프레임 (\(secs)초) 저장됨"
        return outputURL
    }

    // MARK: - UIImage → CVPixelBuffer
    private func pixelBuffer(from image: UIImage) -> CVPixelBuffer? {
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
        ), let cg = image.cgImage else { return nil }

        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: targetWidth, height: targetHeight))
        return buffer
    }
}
