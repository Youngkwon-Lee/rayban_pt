import Foundation
import Observation
import Photos
import UIKit

enum CaptureMediaType: String, Codable {
    case photo
    case video
}

struct SavedCapture: Identifiable, Codable, Hashable {
    let id: UUID
    let mediaType: CaptureMediaType
    let createdAt: Date
    let patientName: String?
    let eventId: String?
    let fileName: String
    let relativePath: String
    let assetLocalIdentifier: String?

    init(
        id: UUID = UUID(),
        mediaType: CaptureMediaType,
        createdAt: Date = .now,
        patientName: String?,
        eventId: String?,
        fileName: String,
        relativePath: String,
        assetLocalIdentifier: String?
    ) {
        self.id = id
        self.mediaType = mediaType
        self.createdAt = createdAt
        self.patientName = patientName
        self.eventId = eventId
        self.fileName = fileName
        self.relativePath = relativePath
        self.assetLocalIdentifier = assetLocalIdentifier
    }
}

enum MediaSaveError: LocalizedError {
    case missingPhoto
    case missingVideo
    case imageEncodingFailed
    case saveFailed
    case libraryAccessDenied

    var errorDescription: String? {
        switch self {
        case .missingPhoto:
            return "저장할 사진이 없습니다."
        case .missingVideo:
            return "저장할 영상이 없습니다."
        case .imageEncodingFailed:
            return "사진 파일 생성에 실패했습니다."
        case .saveFailed:
            return "사진 보관함 저장에 실패했습니다."
        case .libraryAccessDenied:
            return "사진 보관함 접근 권한이 필요합니다."
        }
    }
}

@Observable
@MainActor
final class CaptureStore {
    static let shared = CaptureStore()

    private(set) var captures: [SavedCapture] = []

    private let key = "rayban_pt.saved_captures"

    private init() {
        load()
    }

    func record(_ capture: SavedCapture) {
        captures.removeAll { $0.fileName == capture.fileName && $0.mediaType == capture.mediaType }
        captures.insert(capture, at: 0)
        save()
    }

    func fileURL(for capture: SavedCapture) -> URL {
        CapturePersistence.capturesDirectory().appendingPathComponent(capture.fileName)
    }

    func delete(_ capture: SavedCapture) {
        captures.removeAll { $0.id == capture.id }
        let fileURL = fileURL(for: capture)
        if FileManager.default.fileExists(atPath: fileURL.path) {
            try? FileManager.default.removeItem(at: fileURL)
        }
        save()
    }

    private func load() {
        guard let data = UserDefaults.standard.data(forKey: key),
              let decoded = try? JSONDecoder().decode([SavedCapture].self, from: data) else {
            return
        }
        captures = decoded
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(captures) else {
            return
        }
        UserDefaults.standard.set(data, forKey: key)
    }
}

enum CapturePersistence {
    static func persistPhoto(
        _ image: UIImage,
        patientName: String?,
        eventId: String? = nil
    ) async throws -> SavedCapture {
        let fileName = makeFileName(prefix: "photo", patientName: patientName, ext: "jpg")
        let fileURL = capturesDirectory().appendingPathComponent(fileName)
        guard let data = image.jpegData(compressionQuality: 0.92) else {
            throw MediaSaveError.imageEncodingFailed
        }
        try data.write(to: fileURL, options: .atomic)
        let assetId = try await PhotoLibrarySaver.saveImage(image)
        return SavedCapture(
            mediaType: .photo,
            patientName: patientName,
            eventId: eventId,
            fileName: fileName,
            relativePath: "Captures/\(fileName)",
            assetLocalIdentifier: assetId
        )
    }

    static func persistVideo(
        _ sourceURL: URL,
        patientName: String?,
        eventId: String? = nil
    ) async throws -> SavedCapture {
        let fileName = makeFileName(prefix: "video", patientName: patientName, ext: sourceURL.pathExtension.isEmpty ? "mp4" : sourceURL.pathExtension)
        let destinationURL = capturesDirectory().appendingPathComponent(fileName)
        if FileManager.default.fileExists(atPath: destinationURL.path) {
            try FileManager.default.removeItem(at: destinationURL)
        }
        try FileManager.default.copyItem(at: sourceURL, to: destinationURL)
        let assetId = try await PhotoLibrarySaver.saveVideo(from: destinationURL)
        return SavedCapture(
            mediaType: .video,
            patientName: patientName,
            eventId: eventId,
            fileName: fileName,
            relativePath: "Captures/\(fileName)",
            assetLocalIdentifier: assetId
        )
    }

    static func capturesDirectory() -> URL {
        let directory = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Captures", isDirectory: true)
        if !FileManager.default.fileExists(atPath: directory.path) {
            try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        }
        return directory
    }

    private static func makeFileName(prefix: String, patientName: String?, ext: String) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let timestamp = formatter.string(from: .now)
            .replacingOccurrences(of: ":", with: "-")
        let patient = sanitize(patientName) ?? "unknown"
        return "\(prefix)_\(patient)_\(timestamp).\(ext.lowercased())"
    }

    private static func sanitize(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let allowed = CharacterSet.alphanumerics.union(.init(charactersIn: "-_"))
        let scalars = trimmed.unicodeScalars.map { allowed.contains($0) ? Character($0) : "_" }
        let normalized = String(scalars)
            .replacingOccurrences(of: "__+", with: "_", options: .regularExpression)
            .trimmingCharacters(in: CharacterSet(charactersIn: "_"))
        return normalized.isEmpty ? nil : normalized
    }
}

private enum PhotoLibrarySaver {
    static func saveImage(_ image: UIImage) async throws -> String? {
        try await ensureLibraryAccess()
        return try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<String?, Error>) in
            var localIdentifier: String?
            PHPhotoLibrary.shared().performChanges({
                let request = PHAssetChangeRequest.creationRequestForAsset(from: image)
                localIdentifier = request.placeholderForCreatedAsset?.localIdentifier
            }) { success, error in
                if let error {
                    continuation.resume(throwing: error)
                } else if success {
                    continuation.resume(returning: localIdentifier)
                } else {
                    continuation.resume(throwing: MediaSaveError.saveFailed)
                }
            }
        }
    }

    static func saveVideo(from url: URL) async throws -> String? {
        try await ensureLibraryAccess()
        return try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<String?, Error>) in
            var localIdentifier: String?
            PHPhotoLibrary.shared().performChanges({
                if let request = PHAssetChangeRequest.creationRequestForAssetFromVideo(atFileURL: url) {
                    localIdentifier = request.placeholderForCreatedAsset?.localIdentifier
                }
            }) { success, error in
                if let error {
                    continuation.resume(throwing: error)
                } else if success {
                    continuation.resume(returning: localIdentifier)
                } else {
                    continuation.resume(throwing: MediaSaveError.saveFailed)
                }
            }
        }
    }

    private static func ensureLibraryAccess() async throws {
        let currentStatus = PHPhotoLibrary.authorizationStatus(for: .addOnly)
        switch currentStatus {
        case .authorized, .limited:
            return
        case .notDetermined:
            let requested = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
            guard requested == .authorized || requested == .limited else {
                throw MediaSaveError.libraryAccessDenied
            }
        default:
            throw MediaSaveError.libraryAccessDenied
        }
    }
}
