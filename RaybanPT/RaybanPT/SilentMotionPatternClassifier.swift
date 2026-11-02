import Foundation

/// Short, manually segmented movement-template classifier.
/// No speech semantics: the caller supplies labels. Data remains in memory.
struct SilentMotionPrediction: Equatable {
    let label: String
    let distance: Double
    let margin: Double
}

struct SilentMotionPatternClassifier {
    static let featureCount = 9
    static let minimumFrames = 12
    static let minimumTakes = 3
    static let maximumTakes = 8

    let labels: [String]
    private(set) var templates: [String: [[[Double]]]] = [:]

    init(labels: [String]) {
        self.labels = Array(NSOrderedSet(array: labels)) as? [String] ?? labels
    }

    func count(for label: String) -> Int {
        templates[label]?.count ?? 0
    }

    func eligibleLabels() -> [String] {
        labels.filter { count(for: $0) >= Self.minimumTakes }
    }

    mutating func clear() {
        templates.removeAll()
    }

    @discardableResult
    mutating func train(label: String, frames: [[Double]]) -> Bool {
        guard labels.contains(label), Self.isValid(frames), Self.hasMovement(frames) else {
            return false
        }
        var saved = templates[label, default: []]
        saved.append(frames)
        if saved.count > Self.maximumTakes {
            saved.removeFirst(saved.count - Self.maximumTakes)
        }
        templates[label] = saved
        return true
    }

    func predict(_ frames: [[Double]]) -> SilentMotionPrediction? {
        guard Self.isValid(frames), Self.hasMovement(frames) else { return nil }
        let eligible = eligibleLabels()
        guard eligible.count >= 2 else { return nil }
        let scale = scales()
        let query = normalize(frames, scales: scale)
        var candidates: [(label: String, distance: Double, threshold: Double)] = []

        for label in eligible {
            let normalized = (templates[label] ?? []).map { normalize($0, scales: scale) }
            let nearest = normalized.map { Self.dtw(query, $0) }.min() ?? .infinity
            // A deliberately conservative, training-only rejection threshold.
            let nearestSameClass = normalized.indices.map { i in
                normalized.indices.filter { $0 != i }
                    .map { Self.dtw(normalized[i], normalized[$0]) }.min() ?? .infinity
            }
            if let upper = nearestSameClass.max(), upper.isFinite {
                candidates.append((label, nearest, max(upper * 1.25, 0.001)))
            }
        }

        let sorted = candidates.sorted { $0.distance < $1.distance }
        guard sorted.count >= 2 else { return nil }
        let best = sorted[0], runnerUp = sorted[1]
        let margin = (runnerUp.distance - best.distance) / max(runnerUp.distance, 0.001)
        guard best.distance.isFinite,
              best.distance <= best.threshold,
              margin.isFinite,
              margin >= 0.25 else { return nil }
        return SilentMotionPrediction(label: best.label, distance: best.distance, margin: margin)
    }

    func leaveOneOutSummary() -> String {
        let eligible = eligibleLabels()
        guard eligible.count >= 2 else { return "서로 다른 명령을 각각 3회 이상 학습하세요." }
        let scale = scales()
        let entries: [(label: String, frames: [[Double]])] = eligible.flatMap { label in
            (templates[label] ?? []).map { (label: label, frames: normalize($0, scales: scale)) }
        }
        var correct = 0
        for i in entries.indices {
            var bestDistance = Double.infinity
            var nearestLabel = ""
            for j in entries.indices where j != i {
                let distance = Self.dtw(entries[i].frames, entries[j].frames)
                if distance < bestDistance {
                    bestDistance = distance
                    nearestLabel = entries[j].label
                }
            }
            if nearestLabel == entries[i].label { correct += 1 }
        }
        let percent = Int(100 * Double(correct) / Double(entries.count))
        return "훈련 데이터 leave-one-out: \(correct)/\(entries.count) (\(percent)%). 실제 재착용·새 세션 정확도는 미검증."
    }

    private static func isValid(_ frames: [[Double]]) -> Bool {
        frames.count >= minimumFrames &&
        frames.count <= 256 &&
        frames.allSatisfy { $0.count == featureCount && $0.allSatisfy(\.isFinite) }
    }

    // Explicitly reject flat / idle segments, including stationary gravity.
    private static func hasMovement(_ frames: [[Double]]) -> Bool {
        guard let first = frames.first else { return false }
        return frames.dropFirst().contains { frame in
            (0..<6).contains { abs(frame[$0] - first[$0]) > 0.0001 }
        }
    }

    private func scales() -> [Double] {
        let all = templates.values.flatMap { $0 }.flatMap { $0 }
        guard !all.isEmpty else { return Array(repeating: 1.0, count: Self.featureCount) }
        return (0..<Self.featureCount).map { k in
            let mean = all.reduce(0.0) { $0 + $1[k] } / Double(all.count)
            let variance = all.reduce(0.0) { sum, row in
                let delta = row[k] - mean
                return sum + delta * delta
            } / Double(all.count)
            return max(sqrt(variance), 0.01)
        }
    }

    private func normalize(_ frames: [[Double]], scales: [Double]) -> [[Double]] {
        let center = (0..<Self.featureCount).map { k in
            frames.reduce(0.0) { $0 + $1[k] } / Double(frames.count)
        }
        return frames.map { row in
            (0..<Self.featureCount).map { (row[$0] - center[$0]) / scales[$0] }
        }
    }

    private static func dtw(_ lhs: [[Double]], _ rhs: [[Double]]) -> Double {
        let n = lhs.count, m = rhs.count
        guard n > 0, m > 0 else { return .infinity }
        let band = max(max(8, abs(n - m) + 8), max(n, m) / 3)
        var previous = [Double](repeating: .infinity, count: m + 1)
        previous[0] = 0
        for i in 1...n {
            var current = [Double](repeating: .infinity, count: m + 1)
            let lo = max(1, i - band), hi = min(m, i + band)
            if lo <= hi {
                for j in lo...hi {
                    var squared = 0.0
                    for k in 0..<featureCount {
                        let delta = lhs[i - 1][k] - rhs[j - 1][k]
                        squared += delta * delta
                    }
                    current[j] = sqrt(squared / Double(featureCount)) +
                                 min(previous[j], min(previous[j - 1], current[j - 1]))
                }
            }
            previous = current
        }
        return previous[m] / Double(max(n, m))
    }
}
