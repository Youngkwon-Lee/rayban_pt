import SwiftUI
import AVKit

struct CaptureHistoryView: View {
    @State private var captureStore = CaptureStore.shared
    @State private var previewCapture: SavedCapture?

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
                    CaptureHistoryRow(
                        capture: capture,
                        fileURL: captureStore.fileURL(for: capture),
                        onPreview: { previewCapture = capture }
                    ) {
                        captureStore.delete(capture)
                    }
                }
            }
        }
        .navigationTitle("캡처 히스토리")
        .sheet(item: $previewCapture) { capture in
            CapturePreviewSheet(capture: capture, fileURL: captureStore.fileURL(for: capture))
        }
    }
}

private struct CaptureHistoryRow: View {
    let capture: SavedCapture
    let fileURL: URL
    let onPreview: () -> Void
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
                Button(action: onPreview) {
                    Label("미리보기", systemImage: capture.mediaType == .photo ? "eye" : "play.rectangle")
                }
                .buttonStyle(.borderedProminent)

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

private struct CapturePreviewSheet: View {
    let capture: SavedCapture
    let fileURL: URL

    @Environment(\.dismiss) private var dismiss
    @State private var player = AVPlayer()

    var body: some View {
        NavigationStack {
            Group {
                if capture.mediaType == .photo, let image = UIImage(contentsOfFile: fileURL.path) {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                        .padding()
                } else if capture.mediaType == .video {
                    VideoPlayer(player: player)
                        .onAppear {
                            player = AVPlayer(url: fileURL)
                            player.play()
                        }
                        .onDisappear {
                            player.pause()
                        }
                } else {
                    ContentUnavailableView("미리보기 불가", systemImage: "exclamationmark.triangle")
                }
            }
            .navigationTitle(capture.mediaType == .photo ? "사진 미리보기" : "영상 재생")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("닫기") { dismiss() }
                }
            }
        }
    }
}
