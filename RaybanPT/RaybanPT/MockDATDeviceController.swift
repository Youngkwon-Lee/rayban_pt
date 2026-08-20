#if DEBUG && targetEnvironment(simulator)

import AVFoundation
import CoreGraphics
import CoreMedia
import CoreVideo
import Foundation
import MWDATMockDevice
import UIKit

/// Configures Meta's official MockDeviceKit so Simulator runs exercise the
/// same DAT DeviceSession/Camera.Stream path as a paired device. This is a
/// transport/camera-path test only; Bluetooth HFP still requires real glasses.
final class MockDATDeviceController {

    static let shared = MockDATDeviceController()

    private var glasses: (any MockGlasses)?
    private var feedURL: URL?

    private init() {}

    func enableIfRequested() {
        guard DemoConfig.isDATMockEnabled else { return }

        let kit = MockDeviceKit.shared
        if !kit.isEnabled {
            kit.enable(config: MockDeviceKitConfig(initiallyRegistered: true, initialPermissionsGranted: true))
        }

        do {
            let mockGlasses: any MockGlasses
            if let existing = kit.pairedDevices
                .compactMap({ $0 as? any MockGlasses })
                .first {
                mockGlasses = existing
            } else {
                mockGlasses = try kit.pairGlasses(model: .rayBanMeta)
            }
            mockGlasses.powerOn()
            mockGlasses.unfold()
            mockGlasses.don()
            let feed = try makeFeed()
            mockGlasses.services.camera.setCameraFeed(fileURL: feed)
            glasses = mockGlasses
            feedURL = feed
            print("[MWDATMock] enabled device=\(mockGlasses.deviceIdentifier) feed=\(feed.path)")
        } catch {
            print("[MWDATMock] setup failed: \(error)")
        }
    }

    func disableIfRequested() {
        guard DemoConfig.isDATMockEnabled else { return }
        if let glasses {
            MockDeviceKit.shared.unpairDevice(glasses)
        }
        MockDeviceKit.shared.disable()
        if let feedURL {
            try? FileManager.default.removeItem(at: feedURL)
        }
        glasses = nil
        feedURL = nil
    }

    private func makeFeed() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("rayban-dat-mock-\(UUID().uuidString).mp4")
        let writer = try AVAssetWriter(outputURL: url, fileType: .mp4)
        let settings: [String: Any] = [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: 640,
            AVVideoHeightKey: 360,
            AVVideoCompressionPropertiesKey: [
                AVVideoAverageBitRateKey: 700_000,
                AVVideoMaxKeyFrameIntervalKey: 12,
            ],
        ]
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
        input.expectsMediaDataInRealTime = false
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(
            assetWriterInput: input,
            sourcePixelBufferAttributes: [
                kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32BGRA),
                kCVPixelBufferWidthKey as String: 640,
                kCVPixelBufferHeightKey as String: 360,
            ]
        )
        guard writer.canAdd(input) else { throw MockDATError.writerSetup }
        writer.add(input)
        guard writer.startWriting() else { throw writer.error ?? MockDATError.writerSetup }
        writer.startSession(atSourceTime: .zero)

        // Keep the source alive through app launch, the ready panel, and the
        // UI-test tap that starts the stream. MockDevice consumes this file
        // as a finite camera source rather than as an infinite loop.
        for index in 0..<360 {
            while !input.isReadyForMoreMediaData {
                RunLoop.current.run(until: Date().addingTimeInterval(0.005))
            }
            guard let buffer = makeBuffer(frame: index),
                  adaptor.append(buffer, withPresentationTime: CMTime(value: CMTimeValue(index), timescale: 12))
            else { throw MockDATError.frameWrite }
        }

        input.markAsFinished()
        let semaphore = DispatchSemaphore(value: 0)
        writer.finishWriting { semaphore.signal() }
        semaphore.wait()
        guard writer.status == .completed else { throw writer.error ?? MockDATError.writerSetup }
        return url
    }

    private func makeBuffer(frame: Int) -> CVPixelBuffer? {
        var pixelBuffer: CVPixelBuffer?
        let attributes: [String: Any] = [
            kCVPixelBufferCGImageCompatibilityKey as String: true,
            kCVPixelBufferCGBitmapContextCompatibilityKey as String: true,
        ]
        guard CVPixelBufferCreate(
            kCFAllocatorDefault,
            640,
            360,
            kCVPixelFormatType_32BGRA,
            attributes as CFDictionary,
            &pixelBuffer
        ) == kCVReturnSuccess,
        let pixelBuffer else { return nil }

        CVPixelBufferLockBaseAddress(pixelBuffer, [])
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, []) }
        guard let baseAddress = CVPixelBufferGetBaseAddress(pixelBuffer),
              let context = CGContext(
                  data: baseAddress,
                  width: 640,
                  height: 360,
                  bitsPerComponent: 8,
                  bytesPerRow: CVPixelBufferGetBytesPerRow(pixelBuffer),
                  space: CGColorSpaceCreateDeviceRGB(),
                  bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue
                      | CGBitmapInfo.byteOrder32Little.rawValue
              ) else { return nil }

        let hue = CGFloat(frame % 24) / 24.0
        context.setFillColor(UIColor(hue: hue, saturation: 0.55, brightness: 0.68, alpha: 1).cgColor)
        context.fill(CGRect(x: 0, y: 0, width: 640, height: 360))
        context.setFillColor(CGColor(gray: 1, alpha: 0.88))
        context.fill(CGRect(x: 48 + CGFloat(frame * 7 % 420), y: 142, width: 172, height: 76))
        return pixelBuffer
    }
}

private enum MockDATError: Error {
    case writerSetup
    case frameWrite
}

#endif
