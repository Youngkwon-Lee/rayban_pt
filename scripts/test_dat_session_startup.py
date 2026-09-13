#!/usr/bin/env python3
"""Execute the production startup method against deterministic SDK lifecycle doubles.

Uses the host Swift compiler, without booting a simulator or accessing a device.
This tests adapter control flow, not Meta's binary SDK or Bluetooth transport.
"""

from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "RaybanPT/RaybanPT/StreamViewModel.swift").read_text()
start = source.index("    private func ensureStartedSession(")
end = source.index("    // MARK: - Demo mode", start)
method = source[start:end]

fixture = r'''
import Foundation

enum SessionState { case idle, starting, started, paused, stopping, stopped }
enum DeviceSessionError: Error { case noEligibleDevice }
struct AutoDeviceSelector {}

@MainActor final class DeviceSession {
    var state: SessionState
    var stateAfterStart: SessionState = .started
    var deliveredStates: [SessionState] = []
    var finishImmediately = true
    var onSubscribe: (() -> Void)?
    var starts = 0
    init(_ state: SessionState) { self.state = state }
    func start() throws { starts += 1; state = stateAfterStart }
    func stateStream() -> AsyncStream<SessionState> {
        onSubscribe?()
        let (stream, continuation) = AsyncStream.makeStream(of: SessionState.self)
        for value in deliveredStates { state = value; continuation.yield(value) }
        if finishImmediately { continuation.finish() }
        return stream
    }
    func errorStream() -> AsyncStream<DeviceSessionError> {
        AsyncStream { $0.finish() }
    }
}

@MainActor final class Wearables {
    var created = 0
    var next = DeviceSession(.idle)
    func createSession(deviceSelector: AutoDeviceSelector) throws -> DeviceSession {
        created += 1
        return next
    }
}

@MainActor final class DeviceSessionManager {
    static let shared = DeviceSessionManager()
    var linkState = "fixture"
}

@MainActor final class StartupAdapter {
    var deviceSession: DeviceSession?
    let wearables = Wearables()
    var statusMessage = ""
    func handleSessionError(_ error: DeviceSessionError) async {}
    func run() async throws -> DeviceSession {
        try await ensureStartedSession(selector: AutoDeviceSelector())
    }
    // INSERT_PRODUCTION_METHOD
}

@main struct StartupRegression {
    @MainActor static func expectFailure(_ adapter: StartupAdapter) async {
        do {
            _ = try await adapter.run()
            fatalError("An unready session was returned")
        } catch DeviceSessionError.noEligibleDevice {} catch {
            fatalError("Unexpected error: \(error)")
        }
    }

    @MainActor static func main() async throws {
        let normal = StartupAdapter()
        let ready = try await normal.run()
        precondition(ready.state == .started && ready.starts == 1)
        let reused = try await normal.run()
        precondition(reused === ready && ready.starts == 1)

        let terminal = StartupAdapter()
        let old = DeviceSession(.stopped)
        terminal.deviceSession = old
        let replacement = try await terminal.run()
        precondition(replacement !== old && old.starts == 0)
        precondition(terminal.wearables.created == 1)

        // DAT 0.9: subscribing after an immediate stop yields no states.
        let stoppedDuringStart = StartupAdapter()
        stoppedDuringStart.wearables.next.stateAfterStart = .stopped
        await expectFailure(stoppedDuringStart)
        precondition(stoppedDuringStart.deviceSession == nil)

        let exhausted = StartupAdapter()
        exhausted.wearables.next.stateAfterStart = .starting
        await expectFailure(exhausted)

        for initial in [SessionState.paused, .starting] {
            let pending = StartupAdapter()
            let session = DeviceSession(initial)
            session.deliveredStates = [.started]
            pending.deviceSession = session
            let resumed = try await pending.run()
            precondition(resumed === session && session.starts == 0)
        }

        // A stopped callback from an old attempt must not clear its replacement.
        let superseded = StartupAdapter()
        let previous = DeviceSession(.starting)
        let current = DeviceSession(.started)
        previous.deliveredStates = [.stopped]
        previous.onSubscribe = { superseded.deviceSession = current }
        superseded.deviceSession = previous
        await expectFailure(superseded)
        precondition(superseded.deviceSession === current)

        let cancelled = StartupAdapter()
        let waiting = DeviceSession(.starting)
        waiting.finishImmediately = false
        cancelled.deviceSession = waiting
        let task = Task { try await cancelled.run() }
        await Task.yield()
        task.cancel()
        do {
            _ = try await task.value
            fatalError("Cancelled startup returned success")
        } catch is CancellationError {}

        print("PASS: 9 DAT startup lifecycle scenarios (adapter doubles, not device proof)")
    }
}
'''

with tempfile.TemporaryDirectory(prefix="rayban-dat-startup-") as folder:
    swift = Path(folder) / "StartupRegression.swift"
    binary = Path(folder) / "startup-regression"
    swift.write_text(fixture.replace("    // INSERT_PRODUCTION_METHOD", method))
    subprocess.run(
        ["xcrun", "swiftc", "-parse-as-library", str(swift), "-o", str(binary)],
        check=True, timeout=60,
    )
    subprocess.run([str(binary)], check=True, timeout=15)
