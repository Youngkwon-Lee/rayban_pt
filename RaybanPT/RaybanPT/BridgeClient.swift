import Foundation
import UIKit

enum BridgeError: Error {
    case invalidURL
    case fileNotFound
    case network(String)
    case badStatus(Int, body: String)
    case decode(String)
}

struct IngestRequest: Codable {
    let source: String
    let event_type: String
    let text: String?
    let audio_path: String?
    let image_base64: String?
    let patient_name: String?
    let owner_org_id: String?
    let owner_provider_person_id: String?
    let subject_person_id: String?
    let physio_client_id: String?
    let physio_session_id: String?
}

struct IngestResponse: Codable {
    let event_id: String
    let intent: String?
    let ack: String?
}

struct UploadAccepted: Codable {
    let event_id: String
    let status: String
    let message: String
    let image_saved: String?
    let video_saved: String?
    let size_kb: Int?
}

struct EventResult: Codable {
    let event: EventDetail?
    let event_id: String?
    let intent: String?
}

struct EventDetail: Codable {
    let id: String
    let source: String?
    let event_type: String?
    let raw_text: String?
    let intent: String?
    let status: String?
    let created_at: String?
    let subject_person_id: String?
    let physio_client_id: String?
    let physio_session_id: String?
}


struct EventStatusResponse: Codable {
    let status: String
    let message: String?
    let error: String?
    let error_code: String?
    let result: EventResult?

    var eventId: String? { result?.event?.id ?? result?.event_id }
    var intent: String? { result?.event?.intent ?? result?.intent }
}

struct ConsentPayload: Codable {
    let patient_name: String
    let scope: String
    let consent_text: String?
    let granted_by: String?
}

struct ConsentRecord: Codable {
    let id: String
    let patient_name: String
    let scope: String
    let consent_text: String?
    let granted_by: String?
    let created_at: String?
}

struct ConsentStatusResponse: Codable {
    let patient_name: String?
    let scope: String?
    let active: Bool?
    let consent: ConsentRecord?
}

struct ConsentCreateResponse: Codable {
    let ok: Bool
    let consent: ConsentRecord
}

struct EventDeleteResponse: Codable {
    let ok: Bool
    let event_id: String
    let deleted_files: [String]
}

struct RetentionPurgeResponse: Codable {
    let ok: Bool
    let days: Int
    let purged_events: Int
    let deleted_files: [String]
}

struct ConsentRevokeResponse: Codable {
    let ok: Bool
    let patient_name: String
    let scope: String
    let revoked: Int
}

struct MergeEventsRequest: Codable {
    let image_event_id: String
    let audio_event_id: String
    let patient_name: String?
}

struct SOAPSummary: Codable {
    let s: String
    let o: String
    let a: String
    let p: String
}

struct MergeEventsResponse: Codable {
    let event_id: String
    let status: String
    let message: String
    let patient_name: String?
    let soap: SOAPSummary?
}

struct AuditLog: Codable, Identifiable {
    let id: String
    let event_id: String?
    let level: String
    let message: String
    let created_at: String
}

struct AuditLogsResponse: Codable {
    let items: [AuditLog]
}

struct ChartReviewItem: Codable, Identifiable {
    var id: String { event_id }
    let event_id: String
    let source: String
    let event_type: String
    let intent: String?
    let status: String
    let created_at: String
    let patient_name: String?
    let has_label: Bool
    let quality: ChartQuality
    let review: ChartReviewRecord?
    let primary_issue: String
}

struct ChartReviewResponse: Codable {
    let items: [ChartReviewItem]
}

struct ChartReviewRecord: Codable {
    let event_id: String
    let reviewer: String
    let notes: String
    let quality_score: Int
    let quality_level: String
    let reviewed_at: String
}

struct ChartQualityIssue: Codable, Identifiable {
    var id: String { code + section + message }
    let code: String
    let section: String
    let message: String
    let severity: String
}

struct ChartQuality: Codable {
    let score: Int
    let level: String
    let issues: [ChartQualityIssue]
}

struct BridgeHealthResponse: Codable {
    let ok: Bool
    let service: String
    let version: String
    let time: String
    let db: BridgeHealthDB
    let security: BridgeHealthSecurity
    let recent_error_logs_60m: Int?
}

struct BridgeHealthDB: Codable {
    let ok: Bool
    let error: String?
}

struct BridgeHealthSecurity: Codable {
    let api_key_configured: Bool
    let require_api_key: Bool
    let allow_insecure_lan: Bool
    let docs_public_without_auth: Bool
    let file_downloads_enabled: Bool
    let allow_unmasked_image: Bool
    let patient_consent_required: Bool
    let video_store: Bool
}

final class BridgeClient {
    private(set) var baseURL: URL
    private(set) var apiKey: String
    private(set) var ownerOrgId: String
    private(set) var ownerProviderPersonId: String
    private(set) var subjectPersonId: String
    private(set) var physioClientId: String
    private(set) var physioSessionId: String
    private let session: URLSession

    init(baseURL: URL, apiKey: String = "", session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
        let stored = UserDefaults.standard.string(forKey: "bridge_api_key")?.trimmingCharacters(in: .whitespacesAndNewlines)
        self.apiKey = !apiKey.isEmpty ? apiKey : (stored ?? "")
        self.ownerOrgId = UserDefaults.standard.string(forKey: "glasspt_owner_org_id")?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        self.ownerProviderPersonId = UserDefaults.standard.string(forKey: "glasspt_owner_provider_person_id")?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        self.subjectPersonId = UserDefaults.standard.string(forKey: "glasspt_subject_person_id")?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        self.physioClientId = UserDefaults.standard.string(forKey: "glasspt_physio_client_id")?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        self.physioSessionId = UserDefaults.standard.string(forKey: "glasspt_physio_session_id")?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    /// 런타임에 서버 URL 변경 (UserDefaults 설정 후 적용)
    func updateBaseURL(_ url: URL) {
        self.baseURL = url
    }

    func updateAPIKey(_ key: String) {
        self.apiKey = key
    }

    func updateOwnerScope(orgId: String, providerPersonId: String) {
        self.ownerOrgId = orgId.trimmingCharacters(in: .whitespacesAndNewlines)
        self.ownerProviderPersonId = providerPersonId.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    func updatePhysioContext(clientId: String, sessionId: String, subjectPersonId: String? = nil) {
        if let subjectPersonId {
            self.subjectPersonId = subjectPersonId.trimmingCharacters(in: .whitespacesAndNewlines)
            UserDefaults.standard.set(self.subjectPersonId, forKey: "glasspt_subject_person_id")
        } else {
            self.subjectPersonId = UserDefaults.standard.string(forKey: "glasspt_subject_person_id")?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        }
        self.physioClientId = clientId.trimmingCharacters(in: .whitespacesAndNewlines)
        self.physioSessionId = sessionId.trimmingCharacters(in: .whitespacesAndNewlines)
        UserDefaults.standard.set(self.physioClientId, forKey: "glasspt_physio_client_id")
        UserDefaults.standard.set(self.physioSessionId, forKey: "glasspt_physio_session_id")
    }

    /// API 키 헤더를 URLRequest에 추가
    private func addAuth(_ req: inout URLRequest) {
        if !apiKey.isEmpty {
            req.setValue(apiKey, forHTTPHeaderField: "x-api-key")
        }
        if !ownerOrgId.isEmpty {
            req.setValue(ownerOrgId, forHTTPHeaderField: "x-glasspt-org-id")
        }
        if !ownerProviderPersonId.isEmpty {
            req.setValue(ownerProviderPersonId, forHTTPHeaderField: "x-glasspt-provider-person-id")
        }
    }

    func hasActiveConsent(patientName: String, scope: String = "capture_analysis_storage") async throws -> Bool {
        let trimmed = patientName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              let encodedName = trimmed.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed),
              let url = URL(string: "/consents/\(encodedName)?scope=\(scope)", relativeTo: baseURL)
        else { throw BridgeError.invalidURL }

        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        addAuth(&req)

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw BridgeError.network("no response") }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? "(empty)"
            throw BridgeError.badStatus(http.statusCode, body: body)
        }

        return (try JSONDecoder().decode(ConsentStatusResponse.self, from: data)).active == true
    }

    @discardableResult
    func recordConsent(patientName: String,
                       scope: String = "capture_analysis_storage",
                       grantedBy: String = "therapist") async throws -> ConsentRecord {
        guard let url = URL(string: "/consents", relativeTo: baseURL) else { throw BridgeError.invalidURL }

        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        addAuth(&req)

        let body = ConsentPayload(
            patient_name: patientName.trimmingCharacters(in: .whitespacesAndNewlines),
            scope: scope,
            consent_text: nil,
            granted_by: grantedBy
        )
        req.httpBody = try JSONEncoder().encode(body)

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw BridgeError.network("no response") }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? "(empty)"
            throw BridgeError.badStatus(http.statusCode, body: body)
        }

        return try JSONDecoder().decode(ConsentCreateResponse.self, from: data).consent
    }

    @discardableResult
    func revokeConsent(patientName: String, scope: String = "capture_analysis_storage") async throws -> ConsentRevokeResponse {
        let trimmed = patientName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              let encodedName = trimmed.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed),
              let url = URL(string: "/consents/\(encodedName)?scope=\(scope)", relativeTo: baseURL)
        else { throw BridgeError.invalidURL }

        var req = URLRequest(url: url)
        req.httpMethod = "DELETE"
        addAuth(&req)

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw BridgeError.network("no response") }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? "(empty)"
            throw BridgeError.badStatus(http.statusCode, body: body)
        }

        return try JSONDecoder().decode(ConsentRevokeResponse.self, from: data)
    }

    func sendText(_ text: String, patientName: String? = nil, source: String = "iphone-rayban") async throws -> IngestResponse {
        guard let url = URL(string: "/ingest", relativeTo: baseURL) else { throw BridgeError.invalidURL }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let body = IngestRequest(
            source: source,
            event_type: "text",
            text: text,
            audio_path: nil,
            image_base64: nil,
            patient_name: patientName,
            owner_org_id: ownerOrgId.isEmpty ? nil : ownerOrgId,
            owner_provider_person_id: ownerProviderPersonId.isEmpty ? nil : ownerProviderPersonId,
            subject_person_id: subjectPersonId.isEmpty ? nil : subjectPersonId,
            physio_client_id: physioClientId.isEmpty ? nil : physioClientId,
            physio_session_id: physioSessionId.isEmpty ? nil : physioSessionId
        )
        req.httpBody = try JSONEncoder().encode(body)
        addAuth(&req)

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw BridgeError.network("no response") }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? "(empty)"
            throw BridgeError.badStatus(http.statusCode, body: body)
        }

        do {
            return try JSONDecoder().decode(IngestResponse.self, from: data)
        } catch {
            throw BridgeError.decode(error.localizedDescription)
        }
    }

    /// 오디오 파일 업로드 (비동기 accepted 반환)
    func uploadAudio(fileURL: URL, patientName: String? = nil, source: String = "iphone-rayban") async throws -> UploadAccepted {
        guard FileManager.default.fileExists(atPath: fileURL.path) else { throw BridgeError.fileNotFound }
        guard let url = URL(string: "/ingest-upload", relativeTo: baseURL) else { throw BridgeError.invalidURL }

        let boundary = "Boundary-\(UUID().uuidString)"
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        let fileData = try Data(contentsOf: fileURL)
        let filename = fileURL.lastPathComponent
        let mime = mimeType(for: fileURL)

        var body = Data()

        // source
        body.appendString("--\(boundary)\r\n")
        body.appendString("Content-Disposition: form-data; name=\"source\"\r\n\r\n")
        body.appendString("\(source)\r\n")

        // event_type
        body.appendString("--\(boundary)\r\n")
        body.appendString("Content-Disposition: form-data; name=\"event_type\"\r\n\r\n")
        body.appendString("audio\r\n")

        // patient_name (optional)
        if let name = patientName, !name.isEmpty {
            body.appendString("--\(boundary)\r\n")
            body.appendString("Content-Disposition: form-data; name=\"patient_name\"\r\n\r\n")
            body.appendString("\(name)\r\n")
        }

        if !ownerOrgId.isEmpty {
            body.appendString("--\(boundary)\r\n")
            body.appendString("Content-Disposition: form-data; name=\"owner_org_id\"\r\n\r\n")
            body.appendString("\(ownerOrgId)\r\n")
        }

        if !ownerProviderPersonId.isEmpty {
            body.appendString("--\(boundary)\r\n")
            body.appendString("Content-Disposition: form-data; name=\"owner_provider_person_id\"\r\n\r\n")
            body.appendString("\(ownerProviderPersonId)\r\n")
        }

        if !subjectPersonId.isEmpty {
            body.appendString("--\(boundary)\r\n")
            body.appendString("Content-Disposition: form-data; name=\"subject_person_id\"\r\n\r\n")
            body.appendString("\(subjectPersonId)\r\n")
        }

        if !physioClientId.isEmpty {
            body.appendString("--\(boundary)\r\n")
            body.appendString("Content-Disposition: form-data; name=\"physio_client_id\"\r\n\r\n")
            body.appendString("\(physioClientId)\r\n")
        }

        if !physioSessionId.isEmpty {
            body.appendString("--\(boundary)\r\n")
            body.appendString("Content-Disposition: form-data; name=\"physio_session_id\"\r\n\r\n")
            body.appendString("\(physioSessionId)\r\n")
        }

        // audio file
        body.appendString("--\(boundary)\r\n")
        body.appendString("Content-Disposition: form-data; name=\"audio\"; filename=\"\(filename)\"\r\n")
        body.appendString("Content-Type: \(mime)\r\n\r\n")
        body.append(fileData)
        body.appendString("\r\n")

        body.appendString("--\(boundary)--\r\n")
        req.httpBody = body
        addAuth(&req)

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw BridgeError.network("no response") }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? "(empty)"
            throw BridgeError.badStatus(http.statusCode, body: body)
        }

        do {
            return try JSONDecoder().decode(UploadAccepted.self, from: data)
        } catch {
            throw BridgeError.decode(error.localizedDescription)
        }
    }

    /// 이미지 + 분석 설명을 서버에 업로드 (JSON base64 — Tailscale multipart 502 우회)
    func uploadImage(_ image: UIImage, description: String, patientName: String? = nil, source: String = "rayban-camera") async throws -> IngestResponse {
        guard let url = URL(string: "/ingest", relativeTo: baseURL) else { throw BridgeError.invalidURL }
        guard let imageData = image.jpegData(compressionQuality: 0.6) else { throw BridgeError.network("이미지 변환 실패") }
        let base64Str = imageData.base64EncodedString()

        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let body = IngestRequest(
            source: source,
            event_type: "image",
            text: description,
            audio_path: nil,
            image_base64: base64Str,
            patient_name: patientName,
            owner_org_id: ownerOrgId.isEmpty ? nil : ownerOrgId,
            owner_provider_person_id: ownerProviderPersonId.isEmpty ? nil : ownerProviderPersonId,
            subject_person_id: subjectPersonId.isEmpty ? nil : subjectPersonId,
            physio_client_id: physioClientId.isEmpty ? nil : physioClientId,
            physio_session_id: physioSessionId.isEmpty ? nil : physioSessionId
        )
        req.httpBody = try JSONEncoder().encode(body)
        addAuth(&req)

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw BridgeError.network("no response") }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? "(empty)"
            throw BridgeError.badStatus(http.statusCode, body: body)
        }

        do {
            return try JSONDecoder().decode(IngestResponse.self, from: data)
        } catch {
            throw BridgeError.decode(error.localizedDescription)
        }
    }

    /// MP4 영상 파일을 서버에 업로드 (multipart)
    func uploadVideo(fileURL: URL, patientName: String? = nil, source: String = "rayban-camera") async throws -> UploadAccepted {
        guard let url = URL(string: "/ingest-video", relativeTo: baseURL) else { throw BridgeError.invalidURL }

        let boundary = "Boundary-\(UUID().uuidString)"
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 120  // 영상 파일 업로드 타임아웃 2분

        let fileData = try Data(contentsOf: fileURL)
        let filename = fileURL.lastPathComponent

        var body = Data()
        body.appendString("--\(boundary)\r\n")
        body.appendString("Content-Disposition: form-data; name=\"source\"\r\n\r\n")
        body.appendString("\(source)\r\n")

        // 환자 이름
        if let name = patientName {
            body.appendString("--\(boundary)\r\n")
            body.appendString("Content-Disposition: form-data; name=\"patient_name\"\r\n\r\n")
            body.appendString("\(name)\r\n")
        }

        if !ownerOrgId.isEmpty {
            body.appendString("--\(boundary)\r\n")
            body.appendString("Content-Disposition: form-data; name=\"owner_org_id\"\r\n\r\n")
            body.appendString("\(ownerOrgId)\r\n")
        }

        if !ownerProviderPersonId.isEmpty {
            body.appendString("--\(boundary)\r\n")
            body.appendString("Content-Disposition: form-data; name=\"owner_provider_person_id\"\r\n\r\n")
            body.appendString("\(ownerProviderPersonId)\r\n")
        }

        if !subjectPersonId.isEmpty {
            body.appendString("--\(boundary)\r\n")
            body.appendString("Content-Disposition: form-data; name=\"subject_person_id\"\r\n\r\n")
            body.appendString("\(subjectPersonId)\r\n")
        }

        if !physioClientId.isEmpty {
            body.appendString("--\(boundary)\r\n")
            body.appendString("Content-Disposition: form-data; name=\"physio_client_id\"\r\n\r\n")
            body.appendString("\(physioClientId)\r\n")
        }

        if !physioSessionId.isEmpty {
            body.appendString("--\(boundary)\r\n")
            body.appendString("Content-Disposition: form-data; name=\"physio_session_id\"\r\n\r\n")
            body.appendString("\(physioSessionId)\r\n")
        }

        body.appendString("--\(boundary)\r\n")
        body.appendString("Content-Disposition: form-data; name=\"video\"; filename=\"\(filename)\"\r\n")
        body.appendString("Content-Type: video/mp4\r\n\r\n")
        body.append(fileData)
        body.appendString("\r\n")
        body.appendString("--\(boundary)--\r\n")
        req.httpBody = body
        addAuth(&req)

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw BridgeError.network("no response") }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? "(empty)"
            throw BridgeError.badStatus(http.statusCode, body: body)
        }
        do {
            return try JSONDecoder().decode(UploadAccepted.self, from: data)
        } catch {
            throw BridgeError.decode(error.localizedDescription)
        }
    }

    func getEvent(_ eventId: String) async throws -> EventStatusResponse {
        guard let url = URL(string: "/events/\(eventId)", relativeTo: baseURL) else { throw BridgeError.invalidURL }
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        addAuth(&req)
        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw BridgeError.network("no response") }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? "(empty)"
            throw BridgeError.badStatus(http.statusCode, body: body)
        }

        do {
            return try JSONDecoder().decode(EventStatusResponse.self, from: data)
        } catch {
            throw BridgeError.decode(error.localizedDescription)
        }
    }

    /// accepted -> done/error까지 폴링
    func waitUntilDone(eventId: String, maxTries: Int = 20, intervalSec: Double = 1.0) async throws -> EventStatusResponse {
        for _ in 0..<maxTries {
            let s = try await getEvent(eventId)
            if s.status == "done" || s.status == "error" {
                return s
            }
            try await Task.sleep(nanoseconds: UInt64(intervalSec * 1_000_000_000))
        }
        return EventStatusResponse(status: "timeout", message: "poll timeout", error: nil, error_code: nil, result: nil)
    }

    // MARK: - 차트 목록 / 조회

    struct RecentEvent: Codable, Identifiable {
        let id: String
        let source: String
        let event_type: String
        let intent: String?
        let status: String
        let created_at: String
        let has_label: Bool
        let patient_name: String?
    }

    struct RecentEventsResponse: Codable {
        let items: [RecentEvent]
    }

    struct ChartResponse: Codable {
        let event_id: String
        let chart: String
        let quality: ChartQuality?
        let review: ChartReviewRecord?
    }

    private struct ChartUpdateRequest: Codable {
        let chart: String
    }

    private struct ChartReviewRequest: Codable {
        let reviewer: String
        let notes: String
    }

    struct ChartUpdateResponse: Codable {
        let ok: Bool
        let event_id: String
        let chart: String
        let quality: ChartQuality?
        let review: ChartReviewRecord?
    }

    struct ChartReviewMarkResponse: Codable {
        let ok: Bool
        let event_id: String
        let quality: ChartQuality?
        let review: ChartReviewRecord?
    }

    struct ChartReviewClearResponse: Codable {
        let ok: Bool
        let event_id: String
        let quality: ChartQuality?
        let review: ChartReviewRecord?
    }

    // MARK: - 라벨링

    struct RehabLabel: Codable {
        let event_id: String
        let session_type: String
        let core_task: String
        let custom_task: String?
        let body_position: String?
        let assist_level: String
        let performance: String
        let performance_level: String?
        let review_status: String?
        let reviewer_person_id: String?
        let usable_for_training: Bool?
        let label_confidence: Double?
        let repetition_count: Int?
        let hold_duration_seconds: Double?
        let tolerance: String?
        let fatigue_level: String?
        let compensations: [String]?
        let caregiver_present: Bool?
        let safety_flags: [String]?
        let flags: [String]
        let notes: String
        let updated_at: String?
    }

    struct LabelResponse: Codable {
        let event_id: String
        let label: RehabLabel?
    }

    struct SaveLabelResponse: Codable {
        let ok: Bool
        let label: RehabLabel?
    }

    struct PilotReadiness: Codable {
        let usable_for_schema_eval: Bool
        let eligible_for_gold_dataset: Bool
        let gate: String?
        let missing_requirements: [String]
        let gold_missing_requirements: [String]
    }

    struct PilotIdentity: Codable {
        let organization_id: String?
        let provider_person_id: String?
        let subject_person_id: String?
        let physio_client_id: String?
        let encounter_id: String?
        let identity_resolution_status: String?
        let identity_resolution_notes: String?
    }

    struct PilotReadinessResponse: Codable {
        let status: String
        let event_id: String
        let readiness: PilotReadiness
        let identity: PilotIdentity?
    }

    struct MoaiWritePlanSummary: Codable {
        let operation_count: Int
        let skipped_count: Int
    }

    struct MoaiWritePlanResult: Codable {
        let summary: MoaiWritePlanSummary
    }

    struct MoaiWritePlanResponse: Codable {
        let status: String
        let result: MoaiWritePlanResult
    }

    func fetchLabel(eventId: String) async throws -> RehabLabel? {
        guard let url = URL(string: "/labels/\(eventId)", relativeTo: baseURL) else { throw BridgeError.invalidURL }
        var req = URLRequest(url: url)
        addAuth(&req)
        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw BridgeError.network("no response") }
        if http.statusCode == 404 { return nil }
        guard (200..<300).contains(http.statusCode) else {
            throw BridgeError.badStatus(http.statusCode, body: String(data: data, encoding: .utf8) ?? "")
        }
        return (try JSONDecoder().decode(LabelResponse.self, from: data)).label
    }

    func pilotReadiness(eventId: String, resolveIdentity: Bool = false) async throws -> PilotReadinessResponse {
        guard let url = URL(string: "/events/\(eventId)/pilot-readiness?resolve_identity=\(resolveIdentity ? "true" : "false")", relativeTo: baseURL) else {
            throw BridgeError.invalidURL
        }
        var req = URLRequest(url: url)
        addAuth(&req)
        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw BridgeError.badStatus((resp as? HTTPURLResponse)?.statusCode ?? 0,
                                        body: String(data: data, encoding: .utf8) ?? "")
        }
        return try JSONDecoder().decode(PilotReadinessResponse.self, from: data)
    }

    func moaiWritePlan(eventId: String, resolveIdentity: Bool = false) async throws -> MoaiWritePlanResponse {
        guard let url = URL(string: "/events/\(eventId)/moai-write-plan?resolve_identity=\(resolveIdentity ? "true" : "false")", relativeTo: baseURL) else {
            throw BridgeError.invalidURL
        }
        var req = URLRequest(url: url)
        addAuth(&req)
        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw BridgeError.badStatus((resp as? HTTPURLResponse)?.statusCode ?? 0,
                                        body: String(data: data, encoding: .utf8) ?? "")
        }
        return try JSONDecoder().decode(MoaiWritePlanResponse.self, from: data)
    }

    func saveLabel(eventId: String, sessionType: String, coreTask: String,
                   customTask: String = "", bodyPosition: String = "",
                   assistLevel: String, performance: String,
                   reviewStatus: String = "reviewed",
                   reviewerPersonId: String = "",
                   usableForTraining: Bool = false,
                   labelConfidence: Double? = nil,
                   repetitionCount: Int? = nil,
                   holdDurationSeconds: Double? = nil,
                   tolerance: String = "",
                   fatigueLevel: String = "",
                   compensations: [String] = [],
                   caregiverPresent: Bool? = nil,
                   flags: [String], notes: String) async throws -> RehabLabel? {
        guard let url = URL(string: "/labels/\(eventId)", relativeTo: baseURL) else { throw BridgeError.invalidURL }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        addAuth(&req)
        var body: [String: Any] = [
            "session_type": sessionType,
            "core_task": coreTask,
            "custom_task": customTask,
            "body_position": bodyPosition,
            "assist_level": assistLevel,
            "performance_level": performance,
            "review_status": reviewStatus,
            "reviewer_person_id": reviewerPersonId,
            "usable_for_training": usableForTraining,
            "tolerance": tolerance,
            "fatigue_level": fatigueLevel,
            "compensations": compensations,
            "safety_flags": flags,
            "notes": notes
        ]
        if let labelConfidence { body["label_confidence"] = labelConfidence }
        if let repetitionCount { body["repetition_count"] = repetitionCount }
        if let holdDurationSeconds { body["hold_duration_seconds"] = holdDurationSeconds }
        if let caregiverPresent { body["caregiver_present"] = caregiverPresent }
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw BridgeError.badStatus((resp as? HTTPURLResponse)?.statusCode ?? 0,
                                        body: String(data: data, encoding: .utf8) ?? "")
        }
        return (try JSONDecoder().decode(SaveLabelResponse.self, from: data)).label
    }

    func recentEvents(limit: Int = 20) async throws -> [RecentEvent] {
        guard let url = URL(string: "/recent-events?limit=\(limit)", relativeTo: baseURL) else { throw BridgeError.invalidURL }
        var req = URLRequest(url: url)
        addAuth(&req)
        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw BridgeError.badStatus((resp as? HTTPURLResponse)?.statusCode ?? 0, body: body)
        }
        return (try JSONDecoder().decode(RecentEventsResponse.self, from: data)).items
    }

    func mergeEvents(imageEventId: String, audioEventId: String, patientName: String?) async throws -> MergeEventsResponse {
        guard let url = URL(string: "/events/merge", relativeTo: baseURL) else { throw BridgeError.invalidURL }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        addAuth(&req)

        let body = MergeEventsRequest(
            image_event_id: imageEventId,
            audio_event_id: audioEventId,
            patient_name: patientName?.trimmingCharacters(in: .whitespacesAndNewlines)
        )
        req.httpBody = try JSONEncoder().encode(body)

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw BridgeError.network("no response") }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw BridgeError.badStatus(http.statusCode, body: body)
        }

        do {
            return try JSONDecoder().decode(MergeEventsResponse.self, from: data)
        } catch {
            throw BridgeError.decode(error.localizedDescription)
        }
    }

    func fetchChart(eventId: String) async throws -> ChartResponse {
        guard let url = URL(string: "/charts/\(eventId)", relativeTo: baseURL) else { throw BridgeError.invalidURL }
        var req = URLRequest(url: url)
        addAuth(&req)
        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw BridgeError.badStatus((resp as? HTTPURLResponse)?.statusCode ?? 0, body: body)
        }
        do {
            return try JSONDecoder().decode(ChartResponse.self, from: data)
        } catch {
            throw BridgeError.decode(error.localizedDescription)
        }
    }

    func chartReviewItems(limit: Int = 50, includeGood: Bool = false) async throws -> [ChartReviewItem] {
        let includeValue = includeGood ? "true" : "false"
        guard let url = URL(string: "/chart-review?limit=\(limit)&include_good=\(includeValue)", relativeTo: baseURL) else {
            throw BridgeError.invalidURL
        }
        var req = URLRequest(url: url)
        addAuth(&req)

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw BridgeError.badStatus((resp as? HTTPURLResponse)?.statusCode ?? 0, body: body)
        }
        do {
            return try JSONDecoder().decode(ChartReviewResponse.self, from: data).items
        } catch {
            throw BridgeError.decode(error.localizedDescription)
        }
    }

    func updateChart(eventId: String, chart: String) async throws -> ChartUpdateResponse {
        guard let url = URL(string: "/charts/\(eventId)", relativeTo: baseURL) else { throw BridgeError.invalidURL }
        var req = URLRequest(url: url)
        req.httpMethod = "PUT"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        addAuth(&req)
        req.httpBody = try JSONEncoder().encode(ChartUpdateRequest(chart: chart))

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw BridgeError.badStatus((resp as? HTTPURLResponse)?.statusCode ?? 0, body: body)
        }
        do {
            return try JSONDecoder().decode(ChartUpdateResponse.self, from: data)
        } catch {
            throw BridgeError.decode(error.localizedDescription)
        }
    }

    func markChartReviewed(eventId: String, reviewer: String = "therapist", notes: String = "") async throws -> ChartReviewMarkResponse {
        guard let url = URL(string: "/charts/\(eventId)/review", relativeTo: baseURL) else { throw BridgeError.invalidURL }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        addAuth(&req)
        req.httpBody = try JSONEncoder().encode(ChartReviewRequest(reviewer: reviewer, notes: notes))

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw BridgeError.badStatus((resp as? HTTPURLResponse)?.statusCode ?? 0, body: body)
        }
        do {
            return try JSONDecoder().decode(ChartReviewMarkResponse.self, from: data)
        } catch {
            throw BridgeError.decode(error.localizedDescription)
        }
    }

    func clearChartReview(eventId: String) async throws -> ChartReviewClearResponse {
        guard let url = URL(string: "/charts/\(eventId)/review", relativeTo: baseURL) else { throw BridgeError.invalidURL }
        var req = URLRequest(url: url)
        req.httpMethod = "DELETE"
        addAuth(&req)

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw BridgeError.badStatus((resp as? HTTPURLResponse)?.statusCode ?? 0, body: body)
        }
        do {
            return try JSONDecoder().decode(ChartReviewClearResponse.self, from: data)
        } catch {
            throw BridgeError.decode(error.localizedDescription)
        }
    }

    func auditLogs(limit: Int = 50, level: String? = nil, eventId: String? = nil) async throws -> [AuditLog] {
        var path = "/audit-logs?limit=\(limit)"
        if let level, !level.isEmpty {
            path += "&level=\(level)"
        }
        if let eventId, !eventId.isEmpty {
            path += "&event_id=\(eventId)"
        }
        guard let url = URL(string: path, relativeTo: baseURL) else { throw BridgeError.invalidURL }
        var req = URLRequest(url: url)
        addAuth(&req)

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw BridgeError.network("no response") }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw BridgeError.badStatus(http.statusCode, body: body)
        }

        return try JSONDecoder().decode(AuditLogsResponse.self, from: data).items
    }

    @discardableResult
    func deleteEvent(eventId: String) async throws -> EventDeleteResponse {
        guard let url = URL(string: "/events/\(eventId)", relativeTo: baseURL) else { throw BridgeError.invalidURL }
        var req = URLRequest(url: url)
        req.httpMethod = "DELETE"
        addAuth(&req)

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw BridgeError.network("no response") }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw BridgeError.badStatus(http.statusCode, body: body)
        }

        return try JSONDecoder().decode(EventDeleteResponse.self, from: data)
    }

    @discardableResult
    func purgeOldEvents(days: Int = 30) async throws -> RetentionPurgeResponse {
        guard let url = URL(string: "/retention/events?days=\(days)", relativeTo: baseURL) else { throw BridgeError.invalidURL }
        var req = URLRequest(url: url)
        req.httpMethod = "DELETE"
        addAuth(&req)

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw BridgeError.network("no response") }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw BridgeError.badStatus(http.statusCode, body: body)
        }

        return try JSONDecoder().decode(RetentionPurgeResponse.self, from: data)
    }

    private func mimeType(for fileURL: URL) -> String {
        switch fileURL.pathExtension.lowercased() {
        case "mp3": return "audio/mpeg"
        case "wav": return "audio/wav"
        case "m4a": return "audio/mp4"
        case "aac": return "audio/aac"
        case "ogg": return "audio/ogg"
        case "flac": return "audio/flac"
        case "webm": return "audio/webm"
        default: return "application/octet-stream"
        }
    }

    // MARK: - Glass Relay (phone-free PT HUD)

    struct VisitSession: Codable, Identifiable {
        let id: String
        let organization_id: String
        let provider_person_id: String
        let subject_person_id: String
        let encounter_id: String
        let patient_alias: String
        let phase: String
        let status: String
        let recording_status: String
        let selected_at: String?
        let started_at: String?
        let ended_at: String?
        let session_timer_started_at: String?
        let history_summary: String?
        let readiness: String?
        let error_state: String?
        let cue: String?
        let event_ids: [String]
        let draft_progress_note: DraftProgressNote?
        let created_at: String?
        let updated_at: String?
    }

    struct DraftProgressNote: Codable {
        let note_format: String?
        let status: String?
        let requires_approval: Bool?
        let subjective: String?
        let objective: String?
        let assessment: String?
        let plan: String?
    }

    struct VisitGlassState: Codable {
        let patient: String?
        let mode: String?
        let message: String?
        let is_recording: Bool?
        let recording_start: String?
        let session_count: Int?
        let last_insight: GlassInsight?
    }

    private struct VisitSessionStartPayload: Encodable {
        let organization_id: String
        let provider_person_id: String
        let subject_person_id: String
        let encounter_id: String?
        let patient_alias: String
        let history_summary: String
        let update_glass: Bool
    }

    private struct VisitSessionPhasePayload: Encodable {
        let phase: String
        let cue: String?
        let update_glass: Bool
    }

    private struct VisitSessionRecordingPayload: Encodable {
        let is_recording: Bool
        let update_glass: Bool
    }

    private struct VisitSessionEventPayload: Encodable {
        let event_id: String
        let update_glass: Bool
    }

    struct VisitSessionResponse: Codable {
        let status: String
        let session: VisitSession
        let glass_state: VisitGlassState?
    }

    struct VisitSessionWritePlan: Codable {
        let summary: MoaiWritePlanSummary?
    }

    struct VisitSessionEndResponse: Codable {
        let status: String
        let session: VisitSession
        let glass_state: VisitGlassState?
        let moai_write_plan: VisitSessionWritePlan?
    }

    @discardableResult
    func startVisitSession(
        organizationId: String? = nil,
        providerPersonId: String? = nil,
        subjectPersonId: String? = nil,
        encounterId: String? = nil,
        patientAlias: String,
        historySummary: String = "",
        updateGlass: Bool = true
    ) async throws -> VisitSessionResponse {
        let orgId = (organizationId ?? ownerOrgId).trimmingCharacters(in: .whitespacesAndNewlines)
        let providerId = (providerPersonId ?? ownerProviderPersonId).trimmingCharacters(in: .whitespacesAndNewlines)
        let subjectId = (subjectPersonId ?? self.subjectPersonId).trimmingCharacters(in: .whitespacesAndNewlines)
        let encounter = (encounterId ?? physioSessionId).trimmingCharacters(in: .whitespacesAndNewlines)

        guard !orgId.isEmpty, !providerId.isEmpty, !subjectId.isEmpty else {
            throw BridgeError.network("Visit session requires organization, provider, and subject person IDs.")
        }

        let payload = VisitSessionStartPayload(
            organization_id: orgId,
            provider_person_id: providerId,
            subject_person_id: subjectId,
            encounter_id: encounter.isEmpty ? nil : encounter,
            patient_alias: patientAlias.trimmingCharacters(in: .whitespacesAndNewlines),
            history_summary: historySummary,
            update_glass: updateGlass
        )
        return try await postJSON(path: "/visit-sessions/start", body: payload)
    }

    func getVisitSession(sessionId: String) async throws -> VisitSessionResponse {
        guard let url = URL(string: "/visit-sessions/\(sessionId)", relativeTo: baseURL) else {
            throw BridgeError.invalidURL
        }
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        addAuth(&req)
        return try await decodeResponse(req)
    }

    @discardableResult
    func updateVisitPhase(sessionId: String, phase: String, cue: String? = nil, updateGlass: Bool = true) async throws -> VisitSessionResponse {
        let payload = VisitSessionPhasePayload(phase: phase, cue: cue, update_glass: updateGlass)
        return try await postJSON(path: "/visit-sessions/\(sessionId)/phase", body: payload)
    }

    @discardableResult
    func setVisitRecording(sessionId: String, isRecording: Bool, updateGlass: Bool = true) async throws -> VisitSessionResponse {
        let payload = VisitSessionRecordingPayload(is_recording: isRecording, update_glass: updateGlass)
        return try await postJSON(path: "/visit-sessions/\(sessionId)/recording", body: payload)
    }

    @discardableResult
    func attachVisitEvent(sessionId: String, eventId: String, updateGlass: Bool = true) async throws -> VisitSessionResponse {
        let payload = VisitSessionEventPayload(event_id: eventId, update_glass: updateGlass)
        return try await postJSON(path: "/visit-sessions/\(sessionId)/events", body: payload)
    }

    @discardableResult
    func endVisitSession(sessionId: String, updateGlass: Bool = true) async throws -> VisitSessionEndResponse {
        return try await postJSON(path: "/visit-sessions/\(sessionId)/end?update_glass=\(updateGlass ? "true" : "false")", body: EmptyPayload())
    }

    private struct EmptyPayload: Encodable {}

    private func postJSON<Body: Encodable, Response: Decodable>(path: String, body: Body) async throws -> Response {
        guard let url = URL(string: path, relativeTo: baseURL) else { throw BridgeError.invalidURL }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        addAuth(&req)
        return try await decodeResponse(req)
    }

    private func decodeResponse<Response: Decodable>(_ req: URLRequest) async throws -> Response {
        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw BridgeError.network("no response") }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? "(empty)"
            throw BridgeError.badStatus(http.statusCode, body: body)
        }
        do {
            return try JSONDecoder().decode(Response.self, from: data)
        } catch {
            throw BridgeError.decode(error.localizedDescription)
        }
    }

    struct GlassInsight: Codable {
        let id: String
        let title: String
        let body: String
    }

    struct GlassStatePayload: Encodable {
        let patient: String?
        let mode: String?
        let message: String?
        let is_recording: Bool?
        let recording_start: String?
        let session_count: Int?
        let last_insight: GlassInsight?

        enum CodingKeys: String, CodingKey {
            case patient
            case mode
            case message
            case is_recording
            case recording_start
            case session_count
            case last_insight
        }

        func encode(to encoder: Encoder) throws {
            var container = encoder.container(keyedBy: CodingKeys.self)
            if let patient {
                try container.encode(patient, forKey: .patient)
            } else {
                try container.encodeNil(forKey: .patient)
            }
            if let mode {
                try container.encode(mode, forKey: .mode)
            } else {
                try container.encodeNil(forKey: .mode)
            }
            if let message {
                try container.encode(message, forKey: .message)
            } else {
                try container.encodeNil(forKey: .message)
            }
            if let is_recording {
                try container.encode(is_recording, forKey: .is_recording)
            } else {
                try container.encodeNil(forKey: .is_recording)
            }
            if let recording_start {
                try container.encode(recording_start, forKey: .recording_start)
            } else {
                try container.encodeNil(forKey: .recording_start)
            }
            if let session_count {
                try container.encode(session_count, forKey: .session_count)
            } else {
                try container.encodeNil(forKey: .session_count)
            }
            if let last_insight {
                try container.encode(last_insight, forKey: .last_insight)
            } else {
                try container.encodeNil(forKey: .last_insight)
            }
        }
    }

    struct GlassCommandResponse: Codable {
        let command: String?
        let id: String?
        let created_at: String?
    }

    func pushGlassState(
        patient: String?,
        mode: String,
        message: String,
        isRecording: Bool,
        recordingStart: Date?,
        sessionCount: Int,
        lastInsight: GlassInsight?
    ) async {
        guard let url = URL(string: "/glass/state", relativeTo: baseURL) else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        addAuth(&req)

        var isoStart: String? = nil
        if let d = recordingStart {
            let fmt = ISO8601DateFormatter()
            fmt.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            isoStart = fmt.string(from: d)
        }

        let payload = GlassStatePayload(
            patient: patient,
            mode: mode,
            message: message,
            is_recording: isRecording,
            recording_start: isoStart,
            session_count: sessionCount,
            last_insight: lastInsight
        )
        req.httpBody = try? JSONEncoder().encode(payload)

        _ = try? await session.data(for: req)
    }

    func pollGlassCommand() async -> String? {
        guard let url = URL(string: "/glass/command", relativeTo: baseURL) else { return nil }
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        addAuth(&req)
        guard let (data, _) = try? await session.data(for: req) else { return nil }
        return (try? JSONDecoder().decode(GlassCommandResponse.self, from: data))?.command
    }

    func health() async throws -> BridgeHealthResponse {
        guard let url = URL(string: "/health", relativeTo: baseURL) else { throw BridgeError.invalidURL }
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        addAuth(&req)

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw BridgeError.network("no response") }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? "(empty)"
            throw BridgeError.badStatus(http.statusCode, body: body)
        }

        do {
            return try JSONDecoder().decode(BridgeHealthResponse.self, from: data)
        } catch {
            throw BridgeError.decode(error.localizedDescription)
        }
    }
}

private extension Data {
    mutating func appendString(_ string: String) {
        if let d = string.data(using: .utf8) {
            append(d)
        }
    }
}
