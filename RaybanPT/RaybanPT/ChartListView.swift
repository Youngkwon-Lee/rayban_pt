import SwiftUI

// MARK: - ViewModel

@MainActor
@Observable
final class ChartListViewModel {
    var events: [BridgeClient.RecentEvent] = []
    var isLoading = false
    var errorMessage: String? = nil

    let client: BridgeClient

    init(client: BridgeClient) {
        self.client = client
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        do {
            events = try await client.recentEvents(limit: 30)
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }
}

// MARK: - ChartListView

struct ChartListView: View {
    @State private var vm: ChartListViewModel
    let client: BridgeClient

    init(client: BridgeClient) {
        self.client = client
        self._vm = State(wrappedValue: ChartListViewModel(client: client))
    }

    var body: some View {
        NavigationStack {
            Group {
                if vm.isLoading && vm.events.isEmpty {
                    loadingView
                } else if let err = vm.errorMessage, vm.events.isEmpty {
                    errorView(err)
                } else if vm.events.isEmpty {
                    emptyView
                } else {
                    listView
                }
            }
            .navigationTitle("차트 목록")
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await vm.load() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                            .rotationEffect(.degrees(vm.isLoading ? 360 : 0))
                            .animation(vm.isLoading ? .linear(duration: 1).repeatForever(autoreverses: false) : .default,
                                       value: vm.isLoading)
                    }
                }
            }
            .task { await vm.load() }
            .refreshable { await vm.load() }
        }
    }

    // MARK: 로딩
    private var loadingView: some View {
        VStack(spacing: 16) {
            ProgressView().scaleEffect(1.2)
            Text("차트 불러오는 중...")
                .font(.subheadline).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: 에러
    private func errorView(_ msg: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 44)).foregroundStyle(.orange)
            Text("차트를 불러올 수 없어요").font(.headline)
            Text(msg).font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
            Button("다시 시도") { Task { await vm.load() } }
                .buttonStyle(.borderedProminent)
        }
        .padding(32).frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: 빈 목록
    private var emptyView: some View {
        VStack(spacing: 16) {
            Image(systemName: "doc.text.magnifyingglass")
                .font(.system(size: 52)).foregroundStyle(.tertiary)
            Text("차트가 없어요").font(.headline)
            Text("텍스트, 음성, 영상을 전송하면\n자동으로 차트가 생성됩니다.")
                .font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: 목록
    private var listView: some View {
        List {
            ForEach(vm.events) { event in
                NavigationLink {
                    ChartDetailView(eventId: event.id, client: client)
                } label: {
                    ChartRowView(event: event)
                }
                .listRowInsets(EdgeInsets(top: 8, leading: 16, bottom: 8, trailing: 16))
            }
        }
        .listStyle(.plain)
    }
}

// MARK: - 행 뷰

private struct ChartRowView: View {
    let event: BridgeClient.RecentEvent

    var typeIcon: String {
        switch event.event_type {
        case "audio": return "mic.fill"
        case "video": return "video.fill"
        default: return "text.alignleft"
        }
    }

    var typeColor: Color {
        switch event.event_type {
        case "audio": return .purple
        case "video": return .blue
        default: return .orange
        }
    }

    var formattedDate: String {
        // "2026-04-19 07:07:05" → "4/19 07:07"
        let parts = event.created_at.components(separatedBy: " ")
        guard parts.count == 2 else { return event.created_at }
        let dateParts = parts[0].components(separatedBy: "-")
        guard dateParts.count == 3 else { return event.created_at }
        let timeParts = parts[1].components(separatedBy: ":")
        let time = timeParts.prefix(2).joined(separator: ":")
        return "\(dateParts[1])/\(dateParts[2]) \(time)"
    }

    var body: some View {
        HStack(spacing: 12) {
            // 타입 아이콘
            ZStack {
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(typeColor.opacity(0.12))
                    .frame(width: 44, height: 44)
                Image(systemName: typeIcon)
                    .font(.system(size: 18, weight: .medium))
                    .foregroundStyle(typeColor)
            }

            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(event.event_type == "audio" ? "음성 기록" :
                         event.event_type == "video" ? "영상 분석" : "텍스트 메모")
                        .font(.subheadline).fontWeight(.semibold)
                    Spacer()
                    Text(formattedDate)
                        .font(.caption2).foregroundStyle(.secondary)
                }

                HStack(spacing: 6) {
                    // 상태 배지
                    Text(event.status == "processed" ? "완료" : event.status)
                        .font(.caption2).fontWeight(.medium)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(event.status == "processed" ? Color.green.opacity(0.12) : Color.orange.opacity(0.12),
                                    in: Capsule())
                        .foregroundStyle(event.status == "processed" ? .green : .orange)

                    if let intent = event.intent {
                        Text(intent)
                            .font(.caption2).foregroundStyle(.secondary)
                    }

                    Spacer()

                    Text(String(event.id.prefix(8)) + "…")
                        .font(.caption2.monospaced()).foregroundStyle(.tertiary)
                }
            }
        }
        .padding(.vertical, 4)
    }
}
