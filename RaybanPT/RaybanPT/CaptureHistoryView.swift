import SwiftUI

struct CaptureHistoryView: View {
    @State private var captureStore = CaptureStore.shared

    var body: some View {
        List {
            if captureStore.captures.isEmpty {
                ContentUnavailableView(
                    "저장된 캡처 없음",
                    systemImage: "tray",
                    description: Text("사진이나 영상을 저장하면 여기에서 기록을 확인할 수 있습니다.")
                )
            } else {
                ForEach(captureStore.captures) { capture in
                    CaptureHistoryRow(capture: capture, fileURL: captureStore.fileURL(for: capture)) {
                        captureStore.delete(capture)
                    }
                }
            }
        }
        .navigationTitle("캡처 히스토리")
    }
}

private struct CaptureHistoryRow: View {
    let capture: SavedCapture
    let fileURL: URL
    let onDelete: () -> Void

    private static let timestampFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return formatter
    }()

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label(capture.mediaType == .photo ? "사진" : "영상",
                      systemImage: capture.mediaType == .photo ? "photo" : "video")
                    .font(.subheadline.weight(.semibold))
                Spacer()
                Text(Self.timestampFormatter.string(from: capture.createdAt))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Text(capture.fileName)
                .font(.footnote.monospaced())
                .textSelection(.enabled)

            if let patientName = capture.patientName {
                Text("환자: \(patientName)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if let eventId = capture.eventId {
                Text("이벤트: \(eventId)")
                    .font(.caption2.monospaced())
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }

            Text(capture.relativePath)
                .font(.caption2.monospaced())
                .foregroundStyle(.secondary)
                .textSelection(.enabled)

            HStack {
                ShareLink(item: fileURL) {
                    Label("공유", systemImage: "square.and.arrow.up")
                }
                .buttonStyle(.bordered)

                Button(role: .destructive, action: onDelete) {
                    Label("삭제", systemImage: "trash")
                }
                .buttonStyle(.bordered)

                Spacer()
            }
        }
        .padding(.vertical, 4)
    }
}
