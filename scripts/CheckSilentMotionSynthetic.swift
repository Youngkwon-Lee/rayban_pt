import Foundation

@main
struct CheckSilentMotionSynthetic {
    static func main() {
        for label in 0..<5 {
            for count in [3, 5, 8] {
                let training = (0..<count).map { SilentMotionSynthetic.trace(labelIndex: label, take: Double($0)) }
                let unseen = SilentMotionSynthetic.trace(labelIndex: label, take: SilentMotionSynthetic.unseenTake(afterTrainingCount: count))
                precondition(unseen.count == 48 && unseen.allSatisfy { $0.count == 9 && $0.allSatisfy(\.isFinite) })
                precondition(training.allSatisfy { $0 != unseen }, "Held-out synthetic take repeated training example")
                precondition(!unseen.contains { $0.allSatisfy { $0 == 0 } })
                precondition(training.count == count)
            }
            let first = SilentMotionSynthetic.trace(labelIndex: label, take: 2.5)
            let second = SilentMotionSynthetic.trace(labelIndex: label, take: 2.5)
            precondition(first == second, "Synthetic data must remain deterministic")
            precondition(first != SilentMotionSynthetic.idle(), "Idle must differ from command trace")
        }
        precondition(SilentMotionSynthetic.trace(labelIndex: -1, take: 1).isEmpty)
        precondition(SilentMotionSynthetic.trace(labelIndex: 1, take: .nan).isEmpty)
        precondition(SilentMotionSynthetic.trace(labelIndex: 0, take: 0, frameCount: 1).isEmpty)
        print("PASS: synthetic trace determinism, unique held-out takes, dimensions, idle and invalid inputs (5 labels × 3 sizes)")
    }
}
