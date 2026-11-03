internal import Combine
import Foundation
import MWDATCore
import MWDATMotion
import SwiftUI

// Standalone physical-glasses QA. NO MockDeviceKit, camera, audio, PHI,
// recording, backend/clinical writes, or automatic gesture execution.
@main
struct RealMotionProbeApp: App {
    init() {
        do {
            try Wearables.configure()
        } catch WearablesError.alreadyConfigured {
            // SwiftUI can recreate views, but the DAT singleton is initialized once.
        } catch {
            print("REAL_MOTION_QA SDK configure failed: \(error)")
        }
    }

    var body: some Scene {
        WindowGroup {
            RealMotionProbeView()
                .onOpenURL { url in
                    guard let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
                          items.contains(where: { $0.name == "metaWearablesAction" }) else {
                        return
                    }
                    Task {
                        do { _ = try await Wearables.shared.handleUrl(url) }
                        catch { print("REAL_MOTION_QA callback failed: \(error)") }
                    }
                }
        }
    }
}

@MainActor
final class RealMotionProbeModel: ObservableObject {
    @Published private(set) var status = "실제 Meta 안경 확인 준비. 자동 수집 없음."
    @Published private(set) var registration = ""
    @Published private(set) var deviceDescriptions: [String] = []
    @Published private(set) var specificSelectorEligible: [Bool] = []
    @Published private(set) var autoSelectorEligible = false
    @Published var selectedDeviceIndex = 0
    @Published var trial = Trial.still
    @Published private(set) var running = false
    @Published private(set) var result = "NOT_RUN"
    @Published private(set) var totalFrames = 0
    @Published private(set) var glassesFrames = 0
    @Published private(set) var accelerationFrames = 0
    @Published private(set) var gyroFrames = 0
    @Published private(set) var monotonicTimestamp = true
    @Published private(set) var sessionStateEvents: [String] = []
    @Published private(set) var sessionErrorEvents: [String] = []
    @Published private(set) var linkAtTrialStart = ""
    @Published private(set) var compatibilityAtTrialStart = ""

    enum Trial: String, CaseIterable, Identifiable {
        case still = "정지"
        case mouth = "입만 움직임"
        case head = "머리 움직임"
        var id: String { rawValue }
    }

    private var availableDevices: [DeviceIdentifier] = []
    private var firstNs: Int64?
    private var lastNs: Int64?
    private var previousNs: Int64?

    func refreshDevices() {
        let wearables = Wearables.shared
        registration = String(describing: wearables.registrationState)
        availableDevices = wearables.devices
        specificSelectorEligible = availableDevices.map {
            SpecificDeviceSelector(device: $0).activeDevice != nil
        }
        autoSelectorEligible = AutoDeviceSelector(wearables: wearables).activeDevice != nil
        deviceDescriptions = wearables.devices.enumerated().map { index, id in
            let device = wearables.deviceForIdentifier(id)
            let link = device.map { String(describing: $0.linkState) } ?? "unknown"
            let compatibility = device.map { String(describing: $0.compatibility()) } ?? "unknown"
            let don = device.map { String(describing: $0.donState) } ?? "unknown"
            let eligible = specificSelectorEligible[index]
            return "기기 \(index + 1): 연결=\(link), 호환성=\(compatibility), 착용=\(don), 세션후보=\(eligible)"
        }
        if selectedDeviceIndex >= deviceDescriptions.count { selectedDeviceIndex = 0 }
        status = availableDevices.isEmpty
            ? "연결된 DAT 기기가 없습니다. Meta AI 앱, 안경 연결, Developer Mode를 확인하세요."
            : specificSelectorEligible.contains(true)
                ? "세션 후보 기기를 선택하세요. 실기기 수집은 수동으로만 시작합니다."
                : "안경은 보이지만 세션 후보가 없습니다. SDK·펌웨어 호환성을 점검하세요."
        saveDiscoverySummary()
    }

    /// Read-only metadata snapshot: no hardware IDs, personal data, or raw IMU frames.
    private func saveDiscoverySummary() {
        let wearables = Wearables.shared
        let devices = wearables.devices.compactMap { wearables.deviceForIdentifier($0) }
        let summary: [String: Any] = [
            "sdk": "1.0.1",
            "mode": "physical_discovery_only",
            "registrationState": registration,
            "deviceCount": availableDevices.count,
            "connectedCount": devices.filter { $0.linkState == .connected }.count,
            "donnedCount": devices.filter { $0.donState == .donned }.count,
            "deviceStates": deviceDescriptions,
            "specificSelectorEligible": specificSelectorEligible,
            "autoSelectorEligible": autoSelectorEligible,
            "recordedAt": ISO8601DateFormatter().string(from: Date()),
            "rawSamplesSaved": false,
            "deviceIdentifiersSaved": false,
            "clinicalWrites": false
        ]
        do {
            let output = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("real-motion-discovery.json")
            try JSONSerialization.data(withJSONObject: summary, options: [.sortedKeys, .prettyPrinted])
                .write(to: output, options: .atomic)
        } catch {
            status += " · 기기 상태 요약 저장 실패"
        }
    }

    func registerWithMetaAI() async {
        guard !running else { return }
        do {
            status = "Meta AI 앱의 등록 화면을 엽니다. 승인 작업은 사용자가 직접 수행합니다."
            try await Wearables.shared.startRegistration()
            refreshDevices()
        } catch {
            status = "등록 시작 실패: \(error)"
        }
    }

    private func resetCounters() {
        totalFrames = 0
        glassesFrames = 0
        accelerationFrames = 0
        gyroFrames = 0
        monotonicTimestamp = true
        sessionStateEvents = []
        sessionErrorEvents = []
        linkAtTrialStart = ""
        compatibilityAtTrialStart = ""
        firstNs = nil
        lastNs = nil
        previousNs = nil
        result = "RUNNING"
    }

    func runPhysicalMotionProbe() async {
        guard !running else { return }
        refreshDevices()
        guard availableDevices.indices.contains(selectedDeviceIndex) else {
            result = "BLOCKED: NO_DEVICE"
            return
        }
        guard Wearables.shared.registrationState == .registered else {
            result = "BLOCKED: NOT_REGISTERED"
            status = "Meta AI 앱에 이 QA 앱을 등록해야 합니다."
            return
        }
        let id = availableDevices[selectedDeviceIndex]
        guard specificSelectorEligible.indices.contains(selectedDeviceIndex),
              specificSelectorEligible[selectedDeviceIndex] else {
            result = "BLOCKED: SELECTOR_NO_ELIGIBLE_DEVICE"
            status = "연결된 안경이 SDK DeviceSelector에서 세션 대상으로 선택되지 않습니다."
            return
        }
        guard let device = Wearables.shared.deviceForIdentifier(id) else {
            result = "BLOCKED: DEVICE_NOT_FOUND"
            return
        }
        guard device.linkState == .connected else {
            result = "BLOCKED: NOT_CONNECTED"
            status = "안경 링크 상태: \(device.linkState)"
            return
        }
        running = true
        resetCounters()
        linkAtTrialStart = String(describing: device.linkState)
        compatibilityAtTrialStart = String(describing: device.compatibility())
        var session: DeviceSession?
        var motion: MWDATMotion.Motion?
        var stateMonitor: Task<Void, Never>?
        var errorMonitor: Task<Void, Never>?
        var failurePhase = "준비"
        let startTime = Date()
        defer { running = false }

        do {
            failurePhase = "세션 생성"
            status = failurePhase
            let selector = SpecificDeviceSelector(device: id)
            let created = try Wearables.shared.createSession(deviceSelector: selector)
            session = created
            // Observe BEFORE start, so one-shot transition/error events are not missed.
            stateMonitor = Task { @MainActor in
                for await state in created.stateStream() {
                    if Task.isCancelled { break }
                    if sessionStateEvents.count < 20 {
                        sessionStateEvents.append(String(describing: state))
                    }
                }
            }
            errorMonitor = Task { @MainActor in
                for await error in created.errorStream() {
                    if Task.isCancelled { break }
                    if sessionErrorEvents.count < 10 {
                        sessionErrorEvents.append(String(describing: error))
                    }
                }
            }
            try created.start()

            failurePhase = "세션 시작"
            status = failurePhase
            for _ in 0..<80 {
                let currentState = created.state
                if currentState == .started || currentState == .stopped { break }
                try await Task.sleep(for: .milliseconds(100))
            }
            guard created.state == .started else {
                throw RealMotionProbeError.sessionNotStarted(
                    String(describing: created.state)
                )
            }

            failurePhase = "Motion 부착"
            status = failurePhase
            guard let sensor = try created.addMotion(
                configuration: MotionConfiguration(samplingRate: .hz10)
            ) else {
                throw RealMotionProbeError.capabilityUnavailable
            }
            motion = sensor
            // The trial is manually started; only counts and timestamps are retained.
            failurePhase = "실제 Motion 수신"
            status = "\(trial.rawValue) 검사: 최대 30프레임"
            let reader = Task { @MainActor in
                for await sample in sensor.samples {
                    totalFrames += 1
                    if sample.source == .glasses { glassesFrames += 1 }
                    if sample.accelerometer != nil { accelerationFrames += 1 }
                    if sample.gyroscope != nil { gyroFrames += 1 }
                    if let previousNs, sample.timestampNs <= previousNs {
                        monotonicTimestamp = false
                    }
                    if firstNs == nil { firstNs = sample.timestampNs }
                    lastNs = sample.timestampNs
                    previousNs = sample.timestampNs
                    if totalFrames >= 30 { break }
                }
            }
            sensor.start()
            let watchdog = Task { @MainActor in
                try? await Task.sleep(for: .seconds(9))
                reader.cancel()
            }
            await reader.value
            watchdog.cancel()
            if totalFrames >= 10 &&
               glassesFrames == totalFrames &&
               accelerationFrames >= 10 &&
               gyroFrames >= 10 && monotonicTimestamp {
                result = "PASS: REAL_GLASSES_IMU"
            } else {
                result = "FAIL: REAL_GLASSES_IMU_NOT_CONFIRMED"
            }
            status = "실기기 측정 종료"
        } catch {
            result = "FAIL: SESSION_OR_MOTION_ERROR"
            status = "\(failurePhase) 오류: \(error)"
        }

        motion?.stop()
        if let session {
            try? session.removeMotion()
            session.stop()
        }
        // Allow terminal session state/error events to reach the app-visible stream.
        try? await Task.sleep(for: .milliseconds(150))
        stateMonitor?.cancel()
        errorMonitor?.cancel()
        saveSummary(durationSeconds: Date().timeIntervalSince(startTime))
    }

    private func saveSummary(durationSeconds: TimeInterval) {
        let summary: [String: Any] = [
            "sdk": "1.0.1",
            "mode": "physical_glasses",
            "trial": trial.rawValue,
            "result": result,
            "status": status,
            "framesTotal": totalFrames,
            "framesFromGlasses": glassesFrames,
            "framesWithAccelerometer": accelerationFrames,
            "framesWithGyroscope": gyroFrames,
            "timestampMonotonic": monotonicTimestamp,
            "sessionStateEvents": sessionStateEvents,
            "sessionErrorEvents": sessionErrorEvents,
            "linkAtTrialStart": linkAtTrialStart,
            "compatibilityAtTrialStart": compatibilityAtTrialStart,
            "firstTimestampNs": firstNs as Any? ?? NSNull(),
            "lastTimestampNs": lastNs as Any? ?? NSNull(),
            "durationSeconds": durationSeconds,
            "recordedAt": ISO8601DateFormatter().string(from: Date()),
            "rawSamplesSaved": false,
            "speechAccuracy": "NOT_MEASURED",
            "deviceIdentifiersSaved": false
        ]
        do {
            let url = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("real-motion-probe-summary.json")
            try JSONSerialization.data(
                withJSONObject: summary, options: [.sortedKeys, .prettyPrinted]
            ).write(to: url, options: .atomic)
        } catch {
            status += " · 요약 파일 저장 실패: \(error.localizedDescription)"
        }
    }
}

private enum RealMotionProbeError: Error {
    case sessionNotStarted(String)
    case capabilityUnavailable
}

struct RealMotionProbeView: View {
    @StateObject private var model = RealMotionProbeModel()

    var body: some View {
        NavigationStack {
            Form {
                Section("실험 범위") {
                    Text("Meta Ray-Ban 실제 IMU. 마이크·카메라·환자정보 미사용.")
                    Text("Motion은 SDK 1.0.1의 실험적 기능입니다. Developer Center에서 승인 또는 활성화가 필요할 수 있습니다.")
                        .font(.caption)
                }

                Section("기기 연결") {
                    Text("Meta AI 등록: \(model.registration)")
                    Text("자동 세션 후보 존재: \(model.autoSelectorEligible ? "예" : "아니오")")
                    Button("기기 목록 새로고침") { model.refreshDevices() }
                    Button("Meta AI 앱에서 QA 앱 등록") {
                        Task { await model.registerWithMetaAI() }
                    }
                    ForEach(model.deviceDescriptions.indices, id: \.self) { index in
                        Button {
                            model.selectedDeviceIndex = index
                        } label: {
                            HStack {
                                Text(model.deviceDescriptions[index])
                                Spacer()
                                if model.selectedDeviceIndex == index {
                                    Image(systemName: "checkmark.circle.fill")
                                }
                            }
                        }
                    }
                }

                Section("수동 IMU 측정") {
                    Picker("시험 조건", selection: $model.trial) {
                        ForEach(RealMotionProbeModel.Trial.allCases) {
                            Text($0.rawValue).tag($0)
                        }
                    }
                    Button("실제 안경 IMU 측정 시작") {
                        Task { await model.runPhysicalMotionProbe() }
                    }
                    .disabled(model.running)
                    .accessibilityIdentifier("realMotionStart")
                    Text(model.status)
                }

                Section("결과") {
                    Text(model.result).font(.headline)
                    Text("전체 프레임 \(model.totalFrames) / 실제 안경 소스 \(model.glassesFrames)")
                    Text("가속도 \(model.accelerationFrames) / 자이로 \(model.gyroFrames)")
                    Text("타임스탬프 순서 \(model.monotonicTimestamp ? "정상" : "오류")")
                    Text("세션 전이: \(model.sessionStateEvents.joined(separator: " → "))")
                        .font(.caption)
                    Text("세션 오류: \(model.sessionErrorEvents.joined(separator: "; "))")
                        .font(.caption)
                    Text("기기에 저장되는 것은 요약 수치뿐입니다. 센서 원본·장치 ID·음성·사진·영상은 저장하지 않습니다.")
                        .font(.caption)
                }
            }
            .navigationTitle("Real Motion QA")
            .onAppear {
                model.refreshDevices()
                Task {
                    try? await Task.sleep(for: .seconds(1))
                    model.refreshDevices()
                }
            }
        }
    }
}
