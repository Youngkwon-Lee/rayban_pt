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
    private let session: URLSession

    init(baseURL: URL, apiKey: String = "", session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
        let stored = UserDefaults.standard.string(forKey: "bridge_api_key")?.trimmingCharacters(in: .whitespacesAndNewlines)
        self.apiKey = !apiKey.isEmpty ? apiKey : (stored ?? "")
    }

    /// 런타임에 서버 URL 변경 (UserDefaults 설정 후 적용)
    func updateBaseURL(_ url: URL) {
        self.baseURL = url
    }

    func updateAPIKey(_ key: String) {
        self.apiKey = key
    }

    /// API 키 헤더를 URLRequest에 추가
    private func addAuth(_ req: inout URLRequest) {
        if !apiKey.isEmpty {
            req.setValue(apiKey, forHTTPHeaderField: "x-api-key")
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

        let body = IngestRequest(source: source, event_type: "text", text: text, audio_path: nil, image_base64: nil, patient_name: patientName)
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
            patient_name: patientName
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
        let assist_level: String
        let performance: String
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

    func saveLabel(eventId: String, sessionType: String, coreTask: String,
                   assistLevel: String, performance: String,
                   flags: [String], notes: String) async throws -> RehabLabel? {
        guard let url = URL(string: "/labels/\(eventId)", relativeTo: baseURL) else { throw BridgeError.invalidURL }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        addAuth(&req)
        let body: [String: Any] = [
            "session_type": sessionType,
            "core_task": coreTask,
            "assist_level": assistLevel,
            "performance": performance,
            "flags": flags,
            "notes": notes
        ]
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
