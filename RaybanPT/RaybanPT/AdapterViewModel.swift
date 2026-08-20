import Foundation
internal import Combine

@MainActor
final class AdapterViewModel: ObservableObject {
    @Published var state: AdapterState = .idle
    @Published var lastMessage: String = ""
    @Published var lastEventId: String? = nil   // 완료된 가장 최근 event_id
    @Published var visitSession: BridgeClient.VisitSession? = nil
    @Published var visitStatusMessage: String = ""
    private var activeProviderRole = "unspecified"

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
                await attachVisitEventIfActive(r.event_id)
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
                        await attachVisitEventIfActive(eventId)
                        await recordMediaCaptureEvent(
                            eventId: eventId,
                            sourceType: "audio",
                            candidateType: "transcript",
                            fileName: fileURL.lastPathComponent,
                            captureOrigin: "rayban_hfp_microphone"
                        )
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
                if let eventId = final.eventId {
                    await attachVisitEventIfActive(eventId)
                    await recordMediaCaptureEvent(
                        eventId: eventId,
                        sourceType: "video",
                        candidateType: "video_evidence",
                        fileName: fileURL.lastPathComponent
                    )
                }
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

    func recordMediaCaptureEvent(
        eventId: String,
        sourceType: String,
        candidateType: String,
        fileName: String,
        captureOrigin: String = "rayban_dat_camera"
    ) async {
        guard let session = visitSession else { return }
        _ = try? await client.createCaptureEvent(
            visitSessionId: session.id,
            encounterId: session.encounter_id,
            eventType: "media",
            sourceType: sourceType,
            candidateType: candidateType,
            sourceEventId: eventId,
            confidence: nil,
            status: "draft",
            payload: [
                "media_event_type": candidateType,
                "file_name": fileName,
                "capture_origin": captureOrigin,
                "provider_role": activeProviderRole
            ]
        )
    }

    func startVisitSession(patientAlias: String, historySummary: String = "", providerRole: String? = nil) async {
        let normalizedProviderRole = providerRole?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        activeProviderRole = normalizedProviderRole.isEmpty ? "unspecified" : normalizedProviderRole
        do {
            let response = try await client.startVisitSession(
                providerRole: providerRole,
                patientAlias: patientAlias,
                historySummary: historySummary,
                updateGlass: true
            )
            visitSession = response.session
            visitStatusMessage = "방문 세션 시작: \(response.session.phase)"
        } catch {
            visitStatusMessage = UserFacingError.message(for: error)
        }
    }

    func updateVisitPhase(_ phase: String, cue: String? = nil) async {
        guard let sessionId = visitSession?.id else { return }
        do {
            let response = try await client.updateVisitPhase(sessionId: sessionId, phase: phase, cue: cue, updateGlass: true)
            visitSession = response.session
            visitStatusMessage = "단계 업데이트: \(response.session.phase)"
        } catch {
            visitStatusMessage = UserFacingError.message(for: error)
        }
    }

    func setVisitRecording(_ isRecording: Bool) async {
        guard let sessionId = visitSession?.id else { return }
        do {
            let response = try await client.setVisitRecording(sessionId: sessionId, isRecording: isRecording, updateGlass: true)
            visitSession = response.session
            visitStatusMessage = isRecording ? "녹화 중" : "녹화 대기"
        } catch {
            visitStatusMessage = UserFacingError.message(for: error)
        }
    }

    func attachVisitEventIfActive(_ eventId: String) async {
        guard let sessionId = visitSession?.id else { return }
        do {
            let response = try await client.attachVisitEvent(sessionId: sessionId, eventId: eventId, updateGlass: true)
            visitSession = response.session
            visitStatusMessage = "세션 이벤트 \(response.session.event_ids.count)개"
        } catch {
            visitStatusMessage = UserFacingError.message(for: error)
        }
    }

    func endVisitSession() async {
        guard let sessionId = visitSession?.id else { return }
        do {
            let response = try await client.endVisitSession(sessionId: sessionId, updateGlass: true)
            visitSession = response.session
            if let summary = response.moai_write_plan?.summary {
                visitStatusMessage = "노트 초안 생성: \(summary.operation_count) ops"
            } else {
                visitStatusMessage = "방문 세션 종료"
            }
        } catch {
            visitStatusMessage = UserFacingError.message(for: error)
        }
    }
}
