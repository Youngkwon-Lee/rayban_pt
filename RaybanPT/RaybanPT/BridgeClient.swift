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
    let result: EventResult?

    var eventId: String? { result?.event?.id }
}

final class BridgeClient {
    private let baseURL: URL
    private let session: URLSession

    init(baseURL: URL, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
    }

    func sendText(_ text: String, source: String = "iphone-rayban") async throws -> IngestResponse {
        guard let url = URL(string: "/ingest", relativeTo: baseURL) else { throw BridgeError.invalidURL }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let body = IngestRequest(source: source, event_type: "text", text: text, audio_path: nil, image_base64: nil, patient_name: nil)
        req.httpBody = try JSONEncoder().encode(body)

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
    func uploadAudio(fileURL: URL, source: String = "iphone-rayban") async throws -> UploadAccepted {
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

        // audio file
        body.appendString("--\(boundary)\r\n")
        body.appendString("Content-Disposition: form-data; name=\"audio\"; filename=\"\(filename)\"\r\n")
        body.appendString("Content-Type: \(mime)\r\n\r\n")
        body.append(fileData)
        body.appendString("\r\n")

        body.appendString("--\(boundary)--\r\n")
        req.httpBody = body

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
        let (data, resp) = try await session.data(from: url)
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
        return EventStatusResponse(status: "timeout", message: "poll timeout", error: nil, result: nil)
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
}

private extension Data {
    mutating func appendString(_ string: String) {
        if let d = string.data(using: .utf8) {
            append(d)
        }
    }
}
