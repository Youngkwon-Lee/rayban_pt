import Foundation
import Observation
import UIKit
import MWDATCore

@Observable
@MainActor
final class DeviceSessionManager {

    static let shared = DeviceSessionManager()

    var linkState: LinkState = .disconnected
    var registrationState: RegistrationState = .unavailable
    var activeDeviceId: DeviceIdentifier? = nil
    var statusMessage: String = "초기화 중..."

    private var registrationTask: Task<Void, Never>?
    private var devicesTask: Task<Void, Never>?
    private var linkListenerToken: (any AnyListenerToken)?
    private var isRegistrationRequestInFlight = false
    private var isStarted = false

    private var wearables: any WearablesInterface { Wearables.shared }

    private init() {}

    func start() {
        guard !isStarted else { return }
        isStarted = true

        guard !DemoConfig.isGlassDemoEnabled else {
            registrationState = .registered
            linkState = .connected
            activeDeviceId = nil
            statusMessage = "스마트 글라스 데모 연결됨"
            return
        }

        guard hasValidDATConfiguration else {
            registrationState = .unavailable
            linkState = .disconnected
            statusMessage = "Meta DAT 앱 설정 누락 · MetaAppID/ClientToken 확인 필요"
            print("[MWDAT] invalid app configuration: MetaAppID/ClientToken missing or placeholder")
            return
        }

        registrationTask = Task {
            for await state in wearables.registrationStateStream() {
                print("[MWDAT] registrationState → \(state)")
                self.updateRegistrationState(state)
                if state == .registered {
                    self.observeDevices()
                } else if state == .available {
                    self.beginRegistrationIfReady()
                }
            }
        }

        Task {
            registrationState = wearables.registrationState
            print("[MWDAT] 초기 registrationState: \(registrationState)")
            if registrationState == .registered {
                observeDevices()
            } else {
                updateRegistrationState(registrationState)
                beginRegistrationIfReady()
            }
        }
    }

    private func beginRegistrationIfReady() {
        guard registrationState == .available, !isRegistrationRequestInFlight else { return }
        isRegistrationRequestInFlight = true

        Task {
            defer { isRegistrationRequestInFlight = false }
            do {
                try await wearables.startRegistration()
                print("[MWDAT] startRegistration() 완료")
            } catch let e as RegistrationError {
                print("[MWDAT] RegistrationError: \(e) / \(e.localizedDescription)")
                statusMessage = "등록 오류: \(e.localizedDescription)"
            } catch {
                print("[MWDAT] 등록 오류: \(error.localizedDescription)")
                statusMessage = "등록 오류: \(error.localizedDescription)"
            }
        }
    }

    private func observeDevices() {
        let current = wearables.devices
        print("[MWDAT] 현재 devices: \(current)")
        if let id = current.first {
            activeDeviceId = id
            monitorLinkState(for: id)
        } else {
            activeDeviceId = nil
            cancelLinkListener()
            statusMessage = "Ray-Ban 연결 대기 · Meta AI에서 안경을 가까이 두세요"
        }

        devicesTask?.cancel()
        devicesTask = Task {
            for await devices in wearables.devicesStream() {
                print("[MWDAT] devicesStream: \(devices)")
                let id = devices.first
                self.activeDeviceId = id
                if let id {
                    self.monitorLinkState(for: id)
                } else {
                    self.cancelLinkListener()
                    self.activeDeviceId = nil
                    self.linkState = .disconnected
                    self.statusMessage = "Ray-Ban 연결 대기 · Meta AI에서 안경을 가까이 두세요"
                }
            }
        }
    }

    private func monitorLinkState(for deviceId: DeviceIdentifier) {
        guard let device = wearables.deviceForIdentifier(deviceId) else {
            statusMessage = "기기 정보 없음: \(deviceId.prefix(8))"
            return
        }

        activeDeviceId = deviceId
        cancelLinkListener()

        let deviceName = device.nameOrId()

        // 현재 linkState 즉시 반영
        updateLinkState(device.linkState, deviceName: deviceName)

        // 변경 리스닝
        linkListenerToken = device.addLinkStateListener { [weak self] state in
            guard let manager = self else { return }
            Task { @MainActor in
                guard manager.activeDeviceId == deviceId else { return }
                manager.updateLinkState(state, deviceName: deviceName)
            }
        }
    }

    private func cancelLinkListener() {
        let existingToken = linkListenerToken
        linkListenerToken = nil

        if let existingToken {
            Task {
                await existingToken.cancel()
            }
        }
    }

    private func updateLinkState(_ state: LinkState, deviceName: String) {
        linkState = state
        switch state {
        case .connected:
            statusMessage = "✅ \(deviceName) 연결됨"
        case .connecting:
            statusMessage = "🔄 \(deviceName) 연결 중..."
        case .disconnected:
            statusMessage = "❌ \(deviceName) 연결 끊김"
        }
        print("[MWDAT] linkState: \(state) / \(deviceName)")
    }

    private func updateRegistrationState(_ state: RegistrationState) {
        registrationState = state
        if state == .registered {
            isRegistrationRequestInFlight = false
        }
        switch state {
        case .unavailable:
            statusMessage = "Meta AI에서 안경 페어링·Developer Mode·DAT 권한 필요"
        case .available:
            statusMessage = "Meta AI 연결 승인 대기"
        case .registering:
            statusMessage = "Meta AI 등록 중..."
        case .registered:
            statusMessage = "스마트 글라스 등록됨"
        @unknown default:
            statusMessage = "안경 등록 상태 확인 필요"
        }
    }

    func stop() {
        isStarted = false
        devicesTask?.cancel()
        registrationTask?.cancel()
        devicesTask = nil
        registrationTask = nil
        activeDeviceId = nil
        isRegistrationRequestInFlight = false
        cancelLinkListener()
    }

    func retryConnection() {
        if DemoConfig.isGlassDemoEnabled {
            registrationState = .registered
            linkState = .connected
            activeDeviceId = nil
            statusMessage = "스마트 글라스 데모 연결됨"
            return
        }

        stop()
        linkState = .disconnected
        registrationState = .unavailable
        activeDeviceId = nil
        statusMessage = "재연결 시도 중..."
        start()

        // DAT가 아직 unavailable이면 SDK가 Meta AI를 자동으로 열지 않는다.
        // 사용자가 페어링/Developer Mode/DAT 권한을 완료할 수 있도록 바로 앱을 연다.
        Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 500_000_000)
            guard let self, self.registrationState == .unavailable else { return }
            self.openMetaAIForConnection()
        }
    }

    func openMetaAIForConnection() {
        guard let url = URL(string: "fb-viewapp://") else { return }
        guard UIApplication.shared.canOpenURL(url) else {
            statusMessage = "Meta AI 앱 설치 후 안경을 페어링하세요"
            return
        }
        UIApplication.shared.open(url)
        statusMessage = "Meta AI에서 안경 페어링·Developer Mode·DAT 권한을 승인하세요"
    }

    private var hasValidDATConfiguration: Bool {
        guard let values = Bundle.main.object(forInfoDictionaryKey: "MWDAT") as? [String: Any] else {
            return false
        }
        let appID = (values["MetaAppID"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let clientToken = (values["ClientToken"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return !appID.isEmpty && appID != "0" && !clientToken.isEmpty && clientToken != "0"
    }

}
