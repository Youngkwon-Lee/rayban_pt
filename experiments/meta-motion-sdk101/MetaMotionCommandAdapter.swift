import Foundation
import MWDATMockDevice

/// Maps a MockDeviceKit or DAT Motion stream to the shared nine-feature
/// pattern-classifier format. These are movements, NOT inferred words.
enum MetaMotionCommandAdapter {
    static let framesPerTake = 30
    static let expectedLabel = "다음"
    static let labels = ["다음", "이전"]

    // Training labels are synthetic and encoded explicitly; there is no speech sensor.
    static func makeFeed(labelIndex: Int, take: Double) -> [MWDATMockDevice.MotionSample] {
        SilentMotionSynthetic.trace(
            labelIndex: labelIndex, take: take, frameCount: framesPerTake
        ).enumerated().map { i, row in
            MWDATMockDevice.MotionSample(
                timestampNs: Int64(i) * 100_000_000,
                accelerometer: MWDATMockDevice.Vector3(
                    x: Float(row[0]), y: Float(row[1]), z: Float(row[2] + 9.81)
                ),
                gyroscope: MWDATMockDevice.Vector3(
                    x: Float(row[3]), y: Float(row[4]), z: Float(row[5])
                ),
                orientation: MWDATMockDevice.Quaternion(x: 0, y: 0, z: 0, w: 1),
                source: .glasses
            )
        }
    }

    static func trainingFeatures(labelIndex: Int, take: Double) -> [[Double]]? {
        let generated = makeFeed(labelIndex: labelIndex, take: take)
        return features(generated.map { GlassesMotionFrame($0) })
    }

    /// Fails closed on missing IMU axes, orientation, invalid quaternions,
    /// nonfinite readings, duplicate timestamps or undersized segments.
    /// Raw acceleration may include gravity; per-take centering in the
    /// classifier removes constant offsets but not rotated gravity.
    static func features(_ input: [GlassesMotionFrame]) -> [[Double]]? {
        guard input.count >= SilentMotionPatternClassifier.minimumFrames else { return nil }
        var lastTimestamp: Int64?
        var reference: SIMD4<Double>?
        var values: [[Double]] = []
        values.reserveCapacity(input.count)
        for sample in input {
            guard let a = sample.accelerationMetersPerSecondSquared,
                  let gyro = sample.gyroscopeRadiansPerSecond,
                  let q = sample.orientationQuaternionXYZW else { return nil }
            guard sample.timestampNs >= 0,
                  lastTimestamp == nil || sample.timestampNs > lastTimestamp! else { return nil }
            lastTimestamp = sample.timestampNs

            let raw = SIMD4(Double(q.x), Double(q.y), Double(q.z), Double(q.w))
            let magnitude = sqrt(raw.x*raw.x + raw.y*raw.y + raw.z*raw.z + raw.w*raw.w)
            guard magnitude.isFinite, magnitude > 0.5, magnitude < 1.5 else { return nil }
            var normalized = raw / magnitude
            if reference == nil { reference = normalized }
            guard let start = reference else { return nil }
            // Quaternions q and -q represent the same physical orientation.
            let dot = start.x*normalized.x + start.y*normalized.y +
                      start.z*normalized.z + start.w*normalized.w
            if dot < 0 { normalized = -normalized }
            // Relative orientation = conjugate(first) * current, vector part.
            let rx = start.w*normalized.x - start.x*normalized.w -
                     start.y*normalized.z + start.z*normalized.y
            let ry = start.w*normalized.y + start.x*normalized.z -
                     start.y*normalized.w - start.z*normalized.x
            let rz = start.w*normalized.z - start.x*normalized.y +
                     start.y*normalized.x - start.z*normalized.w
            let feature: [Double] = [
                Double(a.x), Double(a.y), Double(a.z),
                Double(gyro.x), Double(gyro.y), Double(gyro.z),
                rx, ry, rz
            ]
            guard feature.allSatisfy(\.isFinite) else { return nil }
            values.append(feature)
        }
        return values
    }

    static func trainedClassifier() -> SilentMotionPatternClassifier? {
        var classifier = SilentMotionPatternClassifier(labels: labels)
        for (index, label) in labels.enumerated() {
            for take in 0..<3 {
                guard let feature = trainingFeatures(labelIndex: index, take: Double(take)),
                      classifier.train(label: label, frames: feature) else { return nil }
            }
        }
        return classifier
    }

    static func syntheticNoMotionIsRejected(_ classifier: SilentMotionPatternClassifier) -> Bool {
        let base = Array(repeating: 0.0, count: SilentMotionPatternClassifier.featureCount)
        return classifier.predict(Array(repeating: base, count: framesPerTake)) == nil
    }
}
