import Foundation
import MWDATCore
import MWDATMockDevice
import MWDATMotion

// Experimental Meta DAT 1.0.1 API probe. Not linked to rayban_pt production app.
// A real sample may omit any sensor component; all optional fields are preserved.
struct GlassesMotionFrame: Sendable {
    let timestampNs: Int64
    let accelerationMetersPerSecondSquared: SIMD3<Float>?
    let gyroscopeRadiansPerSecond: SIMD3<Float>?
    let orientationQuaternionXYZW: SIMD4<Float>?

    init(_ sample: MWDATMotion.MotionSample) {
        timestampNs = sample.timestampNs
        if let v = sample.accelerometer {
            accelerationMetersPerSecondSquared = SIMD3(v.x, v.y, v.z)
        } else {
            accelerationMetersPerSecondSquared = nil
        }
        if let v = sample.gyroscope {
            gyroscopeRadiansPerSecond = SIMD3(v.x, v.y, v.z)
        } else {
            gyroscopeRadiansPerSecond = nil
        }
        if let q = sample.orientation {
            orientationQuaternionXYZW = SIMD4(q.x, q.y, q.z, q.w)
        } else {
            orientationQuaternionXYZW = nil
        }
    }
}

enum MetaMotionMockSpikeError: Error {
    case sessionNotStarted
    case motionNotAvailable
}

enum MetaMotionMockSpike {
    // Requires the host test app to call Wearables.configure() and to manage its
    // own isolated test lifecycle. Mock samples contain no personal information.
    static func prepareMockGlasses() throws -> any MockGlasses {
        let kit = MockDeviceKit.shared
        kit.enable(config: MockDeviceKitConfig(
            initiallyRegistered: true,
            initialPermissionsGranted: true
        ))
        let glasses = try kit.pairGlasses(model: .rayBanMeta)
        glasses.powerOn()
        glasses.unfold()
        glasses.don()

        let feed: [MWDATMockDevice.MotionSample] = (0..<30).map { i in
            let t = Float(i) / 10.0
            return MWDATMockDevice.MotionSample(
                timestampNs: Int64(i) * 100_000_000,
                accelerometer: MWDATMockDevice.Vector3(x: 0.1 * sin(t), y: 0, z: 9.81),
                gyroscope: MWDATMockDevice.Vector3(x: 0, y: 0.2 * cos(t), z: 0),
                orientation: MWDATMockDevice.Quaternion(x: 0, y: 0, z: 0, w: 1),
                source: .glasses
            )
        }
        glasses.services.motion.setMotionFeed(feed)
        return glasses
    }

    // Host must start its own specific-device DeviceSession and wait until
    // state == .started before calling this; no camera or audio is opened.
    static func startMotion(on session: DeviceSession) throws -> MWDATMotion.Motion {
        guard session.state == .started else {
            throw MetaMotionMockSpikeError.sessionNotStarted
        }
        guard let motion = try session.addMotion(
            configuration: MotionConfiguration(samplingRate: .hz10)
        ) else {
            throw MetaMotionMockSpikeError.motionNotAvailable
        }
        motion.start()
        return motion
    }

    static func readSome(_ motion: MWDATMotion.Motion, count: Int = 30) async -> [GlassesMotionFrame] {
        var result: [GlassesMotionFrame] = []
        guard count > 0 else { return result }
        for await sample in motion.samples {
            result.append(GlassesMotionFrame(sample))
            if result.count >= count { break }
        }
        return result
    }

    // Stop consumers/observers in the host before this cleanup.
    static func stop(
        motion: MWDATMotion.Motion,
        session: DeviceSession,
        glasses: any MockGlasses
    ) throws {
        motion.stop()
        try session.removeMotion()
        glasses.doff()
        glasses.fold()
        glasses.powerOff()
        MockDeviceKit.shared.unpairDevice(glasses)
        MockDeviceKit.shared.disable()
    }
}
