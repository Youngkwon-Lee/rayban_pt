internal import Combine
import CoreMotion
import Foundation
import SwiftUI

// Opt-in research UI. No microphone, camera, patient data or backend writes.
struct SilentCommandGuess {
    let label: String
    let distance: Double
    let margin: Double
}

@MainActor
final class SilentCommandExperimentModel: ObservableObject {
    static let labels = ["다음", "이전", "기록", "확인", "취소"]

    @Published private(set) var status = "AirPods를 iPhone에 연결하고 센서를 시작하세요."
    @Published private(set) var sensing = false
    @Published private(set) var demoMode = false
    @Published private(set) var recording = false
    @Published private(set) var frameCount = 0
    @Published private(set) var counts: [String: Int] = [:]
    @Published private(set) var evaluation = "명령 두 가지를 각각 3회 이상 학습하면 교차검증을 표시합니다."
    @Published private(set) var guess: SilentCommandGuess?

    private enum Mode { case train(String), decode }
    private let sensor = CMHeadphoneMotionManager()
    private var mode: Mode?
    // label -> takes -> frames -> 9 motion features. Memory only.
    private var templates: [String: [[[Double]]]] = [:]
    private var syntheticTrainingCounts: [String: Int] = [:]
    private var frames: [[Double]] = []
    private var referenceAngles: [Double]?

    func startSensor() {
        guard !sensing else { return }
        guard sensor.isDeviceMotionAvailable else {
            status = "헤드 트래킹 지원 AirPods를 착용하고 iPhone 오디오 출력으로 연결하세요. 시뮬레이터는 지원하지 않습니다."
            return
        }
        sensor.startDeviceMotionUpdates(to: .main) { [weak self] motion, error in
            Task { @MainActor [weak self] in
                guard let self else { return }
                if let error {
                    self.stopSensor()
                    self.status = "센서 오류: \(error.localizedDescription)"
                } else if let motion {
                    self.receive(motion)
                }
            }
        }
        sensing = sensor.isDeviceMotionActive
        status = sensing ? "센서 시작됨. 입만 움직여 단어를 학습하세요." : "센서 연결 또는 동작 권한을 확인하세요."
    }

    func startSyntheticDemo() {
        guard !sensing else { return }
        demoMode = true
        sensing = true
        status = "합성 센서 데이터로 화면과 분류 알고리즘을 시험합니다. 실제 무음 발화 검증은 아닙니다."
    }

    func stopSensor() {
        sensor.stopDeviceMotionUpdates()
        sensing = false
        demoMode = false
        recording = false
        mode = nil
        frames = []
        referenceAngles = nil
    }

    private func begin(_ kind: Mode) {
        mode = kind
        frames = []
        referenceAngles = nil
        frameCount = 0
        recording = true
        guess = nil
    }

    func startTraining(_ label: String) {
        guard sensing, !recording, Self.labels.contains(label) else { return }
        begin(.train(label))
        if demoMode {
            let nextTake = syntheticTrainingCounts[label, default: 0]
            syntheticTrainingCounts[label] = nextTake + 1
            frames = syntheticFrames(for: label, take: Double(nextTake))
            frameCount = frames.count
            endRecording()
            status = "[합성 데이터] " + status
            return
        }
        status = "'\(label)'를 소리 없이 입 모양으로 표현한 뒤 구간 종료를 누르세요."
    }

    func startRecognition(demoLabel: String) {
        guard sensing, !recording else { return }
        guard Self.labels.filter({ (templates[$0]?.count ?? 0) >= 3 }).count >= 2 else {
            status = "명령 두 가지를 각각 최소 3회 학습하세요."
            return
        }
        if demoMode && (templates[demoLabel]?.count ?? 0) < 3 {
            status = "선택한 명령을 먼저 3회 이상 학습하세요."
            return
        }
        begin(.decode)
        if demoMode {
            // A fractional take never equals an integer-indexed synthetic training take.
            let unseenTake = SilentMotionSynthetic.unseenTake(afterTrainingCount: syntheticTrainingCounts[demoLabel, default: 0])
            frames = syntheticFrames(for: demoLabel, take: unseenTake)
            frameCount = frames.count
            endRecording()
            status = "[합성 데이터 · 정답 \(demoLabel)] " + status
            return
        }
        status = "학습한 명령 하나를 소리 없이 표현하고 구간 종료를 누르세요."
    }

    // Synthetic generator lives in a Foundation-only module so it can be tested without iOS hardware.
    private func syntheticFrames(for label: String, take: Double) -> [[Double]] {
        SilentMotionSynthetic.trace(labelIndex: Self.labels.firstIndex(of: label) ?? 0, take: take)
    }

    func checkSyntheticIdleRejection() {
        guard demoMode, sensing, !recording else { return }
        guard eligibleLabels().count >= 2 else {
            status = "먼저 서로 다른 명령 2개 이상을 각각 3회 학습하세요."
            return
        }
        // No mouth or head movement: an unknown baseline must not trigger an action.
        let idle = SilentMotionSynthetic.idle()
        guess = recognize(idle)
        status = guess == nil
            ? "[합성 무동작] 거부됨. 합성 테스트만 통과했으며 실제 오탐률은 미검증입니다."
            : "[합성 무동작] 오탐 후보 발생. 모델 개선 전 실제 명령에 연결하지 마세요."
    }

    func endRecording() {
        guard recording, let mode else { return }
        recording = false
        self.mode = nil
        let sample = frames
        frames = []
        referenceAngles = nil
        guard sample.count >= 12 else {
            status = "샘플 \(sample.count)개만 수집됐습니다. 약 1~3초간 다시 시도하세요."
            return
        }
        switch mode {
        case .train(let label):
            var saved = templates[label, default: []]
            saved.append(sample)
            if saved.count > 8 { saved.removeFirst() }
            templates[label] = saved
            counts[label] = saved.count
            evaluation = leaveOneOut()
            status = "'\(label)' 학습 \(saved.count)회 완료."
        case .decode:
            guess = recognize(sample)
            status = guess.map { "후보 '\($0.label)' — 사용자 확인 전에는 실행하지 않습니다." }
                ?? "분류 기준에 미달해 명령을 거부했습니다. 재학습하거나 다시 시도하세요."
        }
    }

    func discardGuess() {
        guess = nil
        status = "후보를 거부했습니다. 명령은 실행되지 않았습니다."
    }

    func reset() {
        guard !recording else { return }
        templates = [:]
        syntheticTrainingCounts = [:]
        counts = [:]
        guess = nil
        evaluation = "명령 두 가지를 각각 3회 이상 학습하면 교차검증을 표시합니다."
        status = "학습 템플릿을 메모리에서 삭제했습니다."
    }

    private func receive(_ motion: CMDeviceMotion) {
        guard recording else { return }
        let angles = [motion.attitude.roll, motion.attitude.pitch, motion.attitude.yaw]
        if referenceAngles == nil { referenceAngles = angles }
        let start = referenceAngles ?? angles
        frames.append([
            motion.userAcceleration.x, motion.userAcceleration.y, motion.userAcceleration.z,
            motion.rotationRate.x, motion.rotationRate.y, motion.rotationRate.z,
            Self.wrap(angles[0] - start[0]),
            Self.wrap(angles[1] - start[1]),
            Self.wrap(angles[2] - start[2])
        ])
        frameCount = frames.count
        if frameCount >= 125 { endRecording() }
    }

    private static func wrap(_ angle: Double) -> Double {
        atan2(sin(angle), cos(angle))
    }

    private func scales() -> [Double] {
        let allFrames = templates.values.flatMap { $0 }.flatMap { $0 }
        guard !allFrames.isEmpty else { return Array(repeating: 1.0, count: 9) }
        return (0..<9).map { index in
            let mean = allFrames.reduce(0.0) { $0 + $1[index] } / Double(allFrames.count)
            let variance = allFrames.reduce(0.0) { acc, row in
                let diff = row[index] - mean
                return acc + diff * diff
            } / Double(allFrames.count)
            return max(sqrt(variance), 0.01)
        }
    }

    // Remove constant offsets within each take, then scale by training variation.
    private func normalize(_ sample: [[Double]], using scale: [Double]) -> [[Double]] {
        let means = (0..<9).map { index in
            sample.reduce(0.0) { $0 + $1[index] } / Double(sample.count)
        }
        return sample.map { row in
            (0..<9).map { (row[$0] - means[$0]) / scale[$0] }
        }
    }

    // Banded dynamic time warping; compares only short, manually segmented takes.
    private func dtw(_ a: [[Double]], _ b: [[Double]]) -> Double {
        guard !a.isEmpty, !b.isEmpty else { return .infinity }
        let n = a.count, m = b.count
        let band = max(max(8, abs(n - m) + 8), max(n, m) / 3)
        var previous = [Double](repeating: .infinity, count: m + 1)
        previous[0] = 0
        for i in 1...n {
            var current = [Double](repeating: .infinity, count: m + 1)
            let lo = max(1, i - band), hi = min(m, i + band)
            if lo <= hi {
                for j in lo...hi {
                    var squared = 0.0
                    for k in 0..<9 {
                        let d = a[i - 1][k] - b[j - 1][k]
                        squared += d * d
                    }
                    current[j] = sqrt(squared / 9) + min(previous[j], min(previous[j - 1], current[j - 1]))
                }
            }
            previous = current
        }
        return previous[m] / Double(max(n, m))
    }

    private func eligibleLabels() -> [String] {
        Self.labels.filter { (templates[$0]?.count ?? 0) >= 3 }
    }

    private func leaveOneOut() -> String {
        guard eligibleLabels().count >= 2 else { return "서로 다른 명령을 각각 3회 이상 학습하세요." }
        let scale = scales()
        let entries: [(label: String, rows: [[Double]])] = eligibleLabels().flatMap { label in
            (templates[label] ?? []).map { (label: label, rows: normalize($0, using: scale)) }
        }
        var correct = 0
        for i in entries.indices {
            var best = Double.infinity, winner = ""
            for j in entries.indices where j != i {
                let d = dtw(entries[i].rows, entries[j].rows)
                if d < best { best = d; winner = entries[j].label }
            }
            if winner == entries[i].label { correct += 1 }
        }
        let percent = Int(100 * Double(correct) / Double(entries.count))
        return "훈련 데이터 leave-one-out: \(correct)/\(entries.count) (\(percent)%). 실제 재착용·새 세션 정확도는 미검증."
    }

    private func recognize(_ sample: [[Double]]) -> SilentCommandGuess? {
        let labels = eligibleLabels()
        guard labels.count >= 2 else { return nil }
        let scale = scales()
        let query = normalize(sample, using: scale)
        var results: [(label: String, distance: Double, threshold: Double)] = []
        for label in labels {
            let training = (templates[label] ?? []).map { normalize($0, using: scale) }
            let nearest = training.map { dtw(query, $0) }.min() ?? .infinity
            let internalDistances = training.indices.map { i in
                training.indices.filter { $0 != i }
                    .map { dtw(training[i], training[$0]) }.min() ?? .infinity
            }
            if let worst = internalDistances.max(), worst.isFinite {
                results.append((label, nearest, max(worst * 1.25, 0.001)))
            }
        }
        let ranked = results.sorted { $0.distance < $1.distance }
        guard ranked.count >= 2 else { return nil }
        let best = ranked[0], next = ranked[1]
        let margin = (next.distance - best.distance) / max(next.distance, 0.001)
        guard best.distance.isFinite, best.distance <= best.threshold, margin >= 0.25 else {
            return nil
        }
        return SilentCommandGuess(label: best.label, distance: best.distance, margin: margin)
    }
}

struct SilentCommandExperimentView: View {
    let onConfirm: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @StateObject private var model = SilentCommandExperimentModel()
    @State private var selected = SilentCommandExperimentModel.labels[0]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("연구용 · AirPods 없이 합성 데모 가능").font(.headline)
                    Text("실제 무음 발화 인식이 아닌 다섯 가지 움직임 패턴 분류 실험입니다. 합성 데모는 무음 발화의 인식률을 검증하지 않습니다.")
                        .font(.subheadline).foregroundStyle(.secondary)

                    Button(model.sensing ? "입력 활성화됨" : "AirPods 센서 시작") {
                        model.startSensor()
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(model.sensing)
                    .accessibilityIdentifier("silentSensorStartButton")

                    Button("AirPods 없는 합성 데이터 데모") {
                        model.startSyntheticDemo()
                    }
                    .buttonStyle(.bordered)
                    .disabled(model.sensing)
                    .accessibilityIdentifier("silentSyntheticDemoButton")

                    if model.demoMode {
                        Text("합성 데이터 전용: 실제 센서·무음 발화 성능과 무관합니다.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }

                    Text(model.status).font(.subheadline)
                        .accessibilityIdentifier("silentSensorStatus")

                    Divider()
                    Text("1. 명령 학습").font(.headline)
                    Picker("학습할 명령", selection: $selected) {
                        ForEach(SilentCommandExperimentModel.labels, id: \.self) { label in
                            Text("\(label) (\(model.counts[label, default: 0])회)").tag(label)
                        }
                    }
                    .pickerStyle(.menu)
                    Button("학습 구간 시작") { model.startTraining(selected) }
                        .disabled(!model.sensing || model.recording)
                        .accessibilityIdentifier("silentTrainStartButton")

                    Divider()
                    Text("2. 명령 인식").font(.headline)
                    Button("인식 구간 시작") { model.startRecognition(demoLabel: selected) }
                        .disabled(!model.sensing || model.recording)

                    if model.recording {
                        Text("센서 샘플 \(model.frameCount)개 (최대 125개)")
                            .font(.caption.monospacedDigit())
                        Button("구간 종료") { model.endRecording() }
                            .buttonStyle(.borderedProminent)
                            .accessibilityIdentifier("silentCaptureStopButton")
                    }
                    Text(model.evaluation).font(.footnote).foregroundStyle(.secondary)
                    if model.demoMode {
                        Button("합성 무동작 오탐 검사") {
                            model.checkSyntheticIdleRejection()
                        }
                        .disabled(model.recording)
                        .accessibilityIdentifier("silentIdleRejectionCheckButton")
                    }

                    if let guess = model.guess {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("후보: \(guess.label)").font(.headline)
                            Text("DTW 거리 \(guess.distance, specifier: "%.3f") · 분리도 \(Int(guess.margin * 100))%")
                                .font(.caption)
                            Text("확인해도 화면에 표시만 하며 임상 기록 저장이나 실제 명령 실행은 하지 않습니다.")
                                .font(.caption)
                            Button("후보 확인 (화면 표시만)") {
                                onConfirm(guess.label)
                                dismiss()
                            }
                            .buttonStyle(.borderedProminent)
                            Button("후보 거부") { model.discardGuess() }
                        }
                        .padding()
                        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
                    }

                    Button("학습 데이터 초기화", role: .destructive) { model.reset() }
                        .disabled(model.recording)
                    Text("동작 데이터와 합성 샘플은 메모리에만 남으며 닫으면 폐기됩니다. 마이크·카메라·서버·환자기록으로 전송되지 않습니다.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding()
            }
            .navigationTitle("무음 명령 실험")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("닫기") { dismiss() }
                }
            }
        }
        .onDisappear { model.stopSensor() }
    }
}
