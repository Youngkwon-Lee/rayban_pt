import Foundation
import MWDATCore
import MWDATMotion
import MWDATMockDevice
import SwiftUI

@main
struct MetaMotionMockQAApp: App {
    var body: some Scene {
        WindowGroup {
            MotionQAView()
        }
    }
}

struct MotionQAView: View {
    @State private var started = false
    @State private var running = false
    @State private var status = "준비 완료"
    @State private var sampleCount = 0
    @State private var accelerometerCount = 0
    @State private var gyroscopeCount = 0
    @State private var firstTimestamp: Int64?
    @State private var lastTimestamp: Int64?
    @State private var timestampMonotonic = true
    @State private var finalResult = "PENDING"
    @State private var predictedCommand = "없음"
    @State private var classifierResult = "NOT_RUN"
    @State private var idleRejected = false
    @State private var featureFrameCount = 0

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 14) {
                Text("Meta SDK 1.0.1 · MockDeviceKit")
                    .font(.headline)
                Text("가상 Ray-Ban Motion 수신 검사")
                    .font(.title3.bold())
                Text("실험 전용. 환자정보·마이크·카메라·실제 안경에 접근하지 않습니다.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Divider()
                Text("상태: \(status)")
                Text("결과: \(finalResult)").font(.title3.bold())
                Text("수신 프레임: \(sampleCount)")
                Text("가속도: \(accelerometerCount) · 자이로: \(gyroscopeCount)")
                Text("타임스탬프 단조 증가: \(timestampMonotonic ? "예" : "아니오")")
                Divider()
                Text("합성 명령 정답: 다음 · 비교 명령: 이전")
                Text("분류 후보: \(predictedCommand)")
                Text("분류 검증: \(classifierResult)")
                Text("무동작 거부: \(idleRejected ? "PASS" : "미통과/미실행")")
                Text("변환된 9축 프레임: \(featureFrameCount)")
                Text("후보는 화면과 로컬 QA JSON에만 남습니다. 실제 명령은 실행되지 않습니다.")
                    .font(.caption).foregroundStyle(.secondary)
                if let firstTimestamp, let lastTimestamp {
                    Text("타임스탬프: \(firstTimestamp) → \(lastTimestamp)")
                        .font(.footnote.monospacedDigit())
                }
                Button("가상 센서 검사 다시 실행") {
                    Task { await run() }
                }
                .buttonStyle(.borderedProminent)
                .disabled(running)
                .accessibilityIdentifier("runMetaMotionMock")
                Spacer()
                Text("결과는 이 QA 앱의 Documents/meta-motion-mock-qa-result.json에 합성 지표만 저장합니다.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(20)
            .navigationTitle("Motion QA")
            .task {
                if !started {
                    started = true
                    await run()
                }
            }
        }
    }

    @MainActor
    private func run() async {
        guard !running else { return }
        running = true
        finalResult = "RUNNING"
        sampleCount = 0
        accelerometerCount = 0
        gyroscopeCount = 0
        firstTimestamp = nil
        lastTimestamp = nil
        timestampMonotonic = true
        predictedCommand = "없음"
        classifierResult = "NOT_RUN"
        idleRejected = false
        featureFrameCount = 0

        var receivedMotionFrames: [GlassesMotionFrame] = []
        var glasses: (any MockGlasses)?
        var session: DeviceSession?
        var motion: MWDATMotion.Motion?

        do {
            status = "SDK 설정"
            do {
                try Wearables.configure()
            } catch WearablesError.alreadyConfigured {
                // Reopening the QA screen must not crash if the SDK singleton remains configured.
                status = "SDK 설정 유지 중"
            }
            status = "MockDeviceKit 초기화"
            // New untrained take is injected through the actual Meta SDK mock stream.
            let heldOutFeed = MetaMotionCommandAdapter.makeFeed(labelIndex: 0, take: 2.5)
            guard heldOutFeed.count == MetaMotionCommandAdapter.framesPerTake else {
                throw QAError.invalidSyntheticFeed
            }
            let mock = try MetaMotionMockSpike.prepareMockGlasses(motionFeed: heldOutFeed)
            glasses = mock
            // Meta's MockDeviceKit guidance: powered + unfolded devices become
            // visible asynchronously; never create a session before that event.
            status = "MockDevice 등록 및 선택 가능 상태 대기"
            let selector = SpecificDeviceSelector(device: mock.deviceIdentifier)
            for _ in 0..<80 {
                let visible = Wearables.shared.devices.contains(mock.deviceIdentifier)
                if visible && selector.activeDevice != nil { break }
                try await Task.sleep(for: .milliseconds(100))
            }
            let visible = Wearables.shared.devices.contains(mock.deviceIdentifier)
            guard visible, selector.activeDevice != nil else {
                throw QAError.mockNotSelectable(
                    "visible=\(visible), deviceCount=\(Wearables.shared.devices.count), " +
                    "reg=\(Wearables.shared.registrationState), selectorActive=\(selector.activeDevice != nil)"
                )
            }
            status = "세션 생성"
            let created = try Wearables.shared.createSession(deviceSelector: selector)
            session = created
            try created.start()

            status = "세션 시작 상태 확인"
            for _ in 0..<100 where created.state != .started {
                try await Task.sleep(for: .milliseconds(100))
            }
            guard created.state == .started else {
                throw QAError.sessionNeverStarted(String(describing: created.state))
            }

            status = "Mock Motion 시작"
            let readerMotion = try MetaMotionMockSpike.startMotion(on: created)
            motion = readerMotion

            status = "Motion.samples 수신 (최대 12초)"
            let readTask = Task {
                for await sample in readerMotion.samples {
                    receivedMotionFrames.append(GlassesMotionFrame(sample))
                    sampleCount += 1
                    if sample.accelerometer != nil { accelerometerCount += 1 }
                    if sample.gyroscope != nil { gyroscopeCount += 1 }
                    if let previous = lastTimestamp, sample.timestampNs <= previous {
                        timestampMonotonic = false
                    }
                    if firstTimestamp == nil { firstTimestamp = sample.timestampNs }
                    lastTimestamp = sample.timestampNs
                    if sampleCount >= 30 { break }
                }
            }
            let watchTask = Task {
                try? await Task.sleep(for: .seconds(12))
                readTask.cancel()
            }
            await readTask.value
            watchTask.cancel()

            // Both this test and the AirPods text-tab experiment now use
            // the EXACT same Foundation-only SilentMotionPatternClassifier.
            let classifier = MetaMotionCommandAdapter.trainedClassifier()
            let features = MetaMotionCommandAdapter.features(receivedMotionFrames)
            featureFrameCount = features?.count ?? 0
            let guess = features.flatMap { classifier?.predict($0) }
            predictedCommand = guess?.label ?? "거부됨"
            idleRejected = classifier.map {
                MetaMotionCommandAdapter.syntheticNoMotionIsRejected($0)
            } ?? false
            let validTransport = sampleCount == MetaMotionCommandAdapter.framesPerTake &&
                accelerometerCount == sampleCount &&
                gyroscopeCount == sampleCount &&
                timestampMonotonic
            let validInference = classifier != nil &&
                featureFrameCount == sampleCount &&
                guess?.label == MetaMotionCommandAdapter.expectedLabel &&
                idleRejected
            classifierResult = validInference ? "PASS" : "FAIL"
            finalResult = (validTransport && validInference)
                ? "PASS: Mock Motion → 명령 분류"
                : "FAIL: Motion 프레임/변환/분류 검증 미통과"
            status = "수신·분류 종료"
        } catch {
            let failedPhase = status
            let count = Wearables.shared.devices.count
            let reg = Wearables.shared.registrationState
            let actualDevice = glasses.flatMap {
                Wearables.shared.deviceForIdentifier($0.deviceIdentifier)
            }
            let link = actualDevice.map { String(describing: $0.linkState) } ?? "unavailable"
            let compatibility = actualDevice.map { String(describing: $0.compatibility()) } ?? "unavailable"
            let don = actualDevice.map { String(describing: $0.donState) } ?? "unavailable"
            status = "phase=\(failedPhase); error=\(error); devices=\(count); reg=\(reg); link=\(link); compatibility=\(compatibility); don=\(don)"
            finalResult = "FAIL: SDK/세션 오류"
        }

        motion?.stop()
        if let session {
            try? session.removeMotion()
            session.stop()
        }
        if let glasses {
            glasses.doff()
            glasses.fold()
            glasses.powerOff()
            await MockDeviceKit.shared.unpairDevice(glasses)
        }
        await MockDeviceKit.shared.disable()

        saveResult()
        print("META_MOTION_MOCK_QA: \(finalResult); count=\(sampleCount); accel=\(accelerometerCount); gyro=\(gyroscopeCount); status=\(status)")
        running = false
    }

    @MainActor
    private func saveResult() {
        let result: [String: Any] = [
            "sdk": "1.0.1",
            "mode": "mock-only",
            "result": finalResult,
            "status": status,
            "samples": sampleCount,
            "accelerometerSamples": accelerometerCount,
            "gyroscopeSamples": gyroscopeCount,
            "timestampMonotonic": timestampMonotonic,
            "syntheticCommandExpected": MetaMotionCommandAdapter.expectedLabel,
            "syntheticCommandPredicted": predictedCommand,
            "syntheticClassifierResult": classifierResult,
            "syntheticIdleRejected": idleRejected,
            "convertedFeatureFrames": featureFrameCount,
            "realSpeechAccuracy": "NOT_MEASURED",
            "firstTimestampNs": firstTimestamp as Any? ?? NSNull(),
            "lastTimestampNs": lastTimestamp as Any? ?? NSNull(),
            "time": ISO8601DateFormatter().string(from: Date())
        ]
        do {
            let file = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("meta-motion-mock-qa-result.json")
            let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys, .prettyPrinted])
            try data.write(to: file, options: .atomic)
        } catch {
            status += " · 결과 파일 저장 실패: \(error.localizedDescription)"
        }
    }
}

private enum QAError: Error {
    case sessionNeverStarted(String)
    case mockNotSelectable(String)
    case invalidSyntheticFeed
}
