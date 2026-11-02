import Foundation

@main
enum CheckSilentMotionPatternClassifier {
    static func main() {
        var model = SilentMotionPatternClassifier(labels: ["다음", "이전"])
        precondition(model.predict(SilentMotionSynthetic.trace(labelIndex: 0, take: 2.5)) == nil,
                     "Must not classify without trained alternatives")
        for (index, label) in ["다음", "이전"].enumerated() {
            for take in 0..<3 {
                precondition(model.train(label: label,
                                         frames: SilentMotionSynthetic.trace(labelIndex: index, take: Double(take))))
            }
        }
        precondition(model.eligibleLabels().count == 2)
        for (index, label) in ["다음", "이전"].enumerated() {
            let unseen = SilentMotionSynthetic.trace(labelIndex: index, take: 2.5)
            let result = model.predict(unseen)
            print("HELD_OUT \(label): \(result?.label ?? "REJECTED") distance=\(result?.distance ?? .infinity)")
            precondition(result?.label == label, "Held-out synthetic label should match")
        }
        precondition(model.predict(SilentMotionSynthetic.idle()) == nil, "Idle must be rejected")
        precondition(model.predict(Array(repeating: Array(repeating: 9.81, count: 9), count: 30)) == nil,
                     "Stationary gravity must be rejected")
        precondition(model.predict([[1, 2]]) == nil)
        let nonfinite = Array(repeating: Array(repeating: Double.nan, count: 9), count: 48)
        precondition(!model.train(label: "다음", frames: nonfinite), "Invalid training samples must fail")
        precondition(!model.train(label: "알수없음", frames: SilentMotionSynthetic.trace(labelIndex: 0, take: 9)))
        precondition(!model.train(label: "다음", frames: SilentMotionSynthetic.idle()))
        precondition(model.count(for: "다음") == 3)
        precondition(model.leaveOneOutSummary().contains("6/6"), "Unexpected leave-one-out summary")
        model.clear()
        precondition(model.eligibleLabels().isEmpty)
        print("PASS: exact classifier + unseen labels, idle/gravity reject, invalid input, reset")
    }
}
