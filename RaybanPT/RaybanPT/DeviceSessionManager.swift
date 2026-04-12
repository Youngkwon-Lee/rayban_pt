import Foundation
import Observation
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

    private var wearables: any WearablesInterface { Wearables.shared }

    private init() {}

    func start() {
        registrationTask = Task {
            for await state in wearables.registrationStateStream() {
                self.registrationState = state
                self.statusMessage = "등록 상태: \(state.description)"
                if state == .registered {
                    self.observeDevices()
                }
            }
        }

        Task {
            do {
                registrationState = wearables.registrationState
                if registrationState == .registered {
                    observeDevices()
                } else {
                    statusMessage = "SDK 등록 중..."
                    try await wearables.startRegistration()
                }
            } catch let e as RegistrationError {
                statusMessage = "등록 실패: \(e.description)"
            } catch {
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
                    self.statusMessage = "연결된 기기 없음"
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

        // 현재 linkState 즉시 반영
        updateLinkState(device.linkState, deviceName: device.nameOrId())

        // 변경 리스닝
        linkListenerToken = device.addLinkStateListener { [weak self] state in
            Task { @MainActor in
                guard let self, self.activeDeviceId == deviceId else { return }
                self.updateLinkState(state, deviceName: device.nameOrId())
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

    func stop() {
        devicesTask?.cancel()
        registrationTask?.cancel()
        devicesTask = nil
        registrationTask = nil
        activeDeviceId = nil
        cancelLinkListener()
    }
}
