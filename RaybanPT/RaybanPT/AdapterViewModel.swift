import Foundation
internal import Combine

@MainActor
final class AdapterViewModel: ObservableObject {
    @Published var state: AdapterState = .idle
    @Published var lastMessage: String = ""

    let client: BridgeClient

    init(client: BridgeClient) {
        self.client = client
    }

    func sendText(_ text: String) {
        Task {
            do {
                state = .connecting
                let r = try await client.sendText(text)
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

    func uploadAudio(fileURL: URL) {
        Task {
            do {
                state = .uploading
                let accepted = try await client.uploadAudio(fileURL: fileURL)
                state = .processing(eventId: accepted.event_id)

                let final = try await client.waitUntilDone(eventId: accepted.event_id)
                if final.status == "done" {
                    state = .done
                    if let res = final.result {
                        lastMessage = "done intent=\(res.intent ?? "-") ack=\(res.ack ?? "-") event=\(res.event_id)"
                    } else {
                        lastMessage = "done (no payload)"
                    }
                } else if final.status == "error" {
                    state = .failed(message: final.error ?? "processing error")
                    lastMessage = final.error ?? "processing error"
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
                if let result = final.result {
                    let intent = result.event?.intent ?? result.intent ?? "-"
                    let eventId = result.event?.id ?? result.event_id ?? "-"
                    lastMessage = "done intent=\(intent) event=\(eventId)"
                } else {
                    lastMessage = accepted.message
                }
                return accepted
            }

            let message: String
            if final.status == "error" {
                message = final.error ?? "processing error"
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
