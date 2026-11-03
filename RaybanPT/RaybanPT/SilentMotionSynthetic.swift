import Foundation

/// Deterministic fake motion for local UI and classifier regression checks.
/// Not measurements of real human speech or Meta/Apple sensor output.
enum SilentMotionSynthetic {
    static let featureCount = 9

    static func trace(labelIndex: Int, take: Double, frameCount: Int = 48) -> [[Double]] {
        guard labelIndex >= 0, frameCount >= 2, take.isFinite else { return [] }
        let classIndex = Double(labelIndex)
        let shift = take * 0.012
        return (0..<frameCount).map { frame in
            let t = Double(frame) / Double(frameCount - 1)
            let center = 0.28 + classIndex * 0.095 + shift
            let pulse = exp(-pow((t - center) / 0.13, 2))
            let omega = 2 * Double.pi * (1.2 + classIndex * 0.50) * t
            return [
                sin(omega + shift) * pulse,
                cos(omega + shift) * pulse,
                (0.3 + 0.14 * classIndex) * pulse,
                sin(2 * omega) * pulse,
                cos(2 * omega) * pulse,
                sin(omega / 2) * pulse,
                (0.12 + 0.05 * classIndex) * pulse,
                sin(omega) * 0.5 * pulse,
                cos(omega) * 0.3 * pulse
            ]
        }
    }

    static func unseenTake(afterTrainingCount count: Int) -> Double {
        max(0.5, Double(count) - 0.5)
    }

    static func idle(frameCount: Int = 48) -> [[Double]] {
        Array(repeating: Array(repeating: 0.0, count: featureCount), count: max(0, frameCount))
    }
}
