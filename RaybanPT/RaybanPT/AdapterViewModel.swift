import Foundation
internal import Combine

@MainActor
final class AdapterViewModel: ObservableObject {
    @Published var state: AdapterState = .idle
    @Published var lastMessage: String = ""
    @Published var lastEventId: String? = nil   // 완료된 가장 최근 event_id

    let client: BridgeClient

    init(client: BridgeClient) {
        self.client = client
    }

    func sendText(_ text: String, patientName: String? = nil) {
        Task {
            do {
                state = .connecting
                let r = try await client.sendText(text, patientName: patientName)
                lastEventId = r.event_id
                state = .done
                lastMessage = "ack=\(r.ack ?? "-") intent=\(r.intent ?? "-") event=\(r.event_id)"
            } catch {
                state = .failed(message: UserFacingError.message(for: error))
                lastMessage = UserFacingError.message(for: error)
            }
        }
    }

    func markDone() {
        state = .done
    }

    func uploadAudio(fileURL: URL, patientName: String? = nil) {
        Task {
            do {
                state = .uploading
                let accepted = try await client.uploadAudio(fileURL: fileURL, patientName: patientName)
                state = .processing(eventId: accepted.event_id)

                let final = try await client.waitUntilDone(
                    eventId: accepted.event_id,
                    maxTries: 180,
                    intervalSec: 1.0
                )
                if final.status == "done" {
                    if let eventId = final.eventId {
                        lastEventId = eventId
                    }
                    state = .done
                    let intent = final.intent ?? "-"
                    let eventId = final.eventId ?? "-"
                    lastMessage = "done intent=\(intent) event=\(eventId)"
                } else if final.status == "error" {
                    let message = UserFacingError.message(code: final.error_code, fallback: final.error)
                    state = .failed(message: message)
                    lastMessage = message
                } else {
                    state = .failed(message: final.message ?? "timeout")
                    lastMessage = final.message ?? "timeout"
                }
            } catch {
                state = .failed(message: UserFacingError.message(for: error))
                lastMessage = UserFacingError.message(for: error)
            }
        }
    }

    @discardableResult
    func uploadVideo(fileURL: URL) async throws -> UploadAccepted {
        do {
            state = .uploading
            lastMessage = ""

            let accepted = try await client.uploadVideo(fileURL: fileURL)
            state = .processing(eventId: accepted.event_id)
            lastMessage = accepted.message

            let final = try await client.waitUntilDone(eventId: accepted.event_id)
            if final.status == "done" {
                state = .done
                let intent = final.intent ?? "-"
                let eventId = final.eventId ?? "-"
                lastMessage = "done intent=\(intent) event=\(eventId)"
                return accepted
            }

            let message: String
            if final.status == "error" {
                message = UserFacingError.message(code: final.error_code, fallback: final.error)
            } else {
                message = final.message ?? "timeout"
            }

            state = .failed(message: message)
            lastMessage = message
            throw BridgeError.network(message)
        } catch {
            state = .failed(message: UserFacingError.message(for: error))
            lastMessage = UserFacingError.message(for: error)
            throw error
        }
    }
}
