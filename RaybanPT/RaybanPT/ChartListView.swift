import SwiftUI

// MARK: - ViewModel

@MainActor
@Observable
final class ChartListViewModel {
    var allEvents: [BridgeClient.RecentEvent] = []
    var isLoading = false
    var errorMessage: String? = nil
    var selectedPatient: String? = nil   // nil = 전체

    let client: BridgeClient

    init(client: BridgeClient) {
        self.client = client
    }

    /// 현재 필터 적용된 목록
    var filteredEvents: [BridgeClient.RecentEvent] {
        guard let name = selectedPatient else { return allEvents }
        return allEvents.filter { $0.patient_name == name }
    }

    /// 서버에 있는 환자 이름 목록 (중복 제거, 정렬)
    var patientNames: [String] {
        let names = allEvents.compactMap { $0.patient_name }.filter { !$0.isEmpty }
        return Array(Set(names)).sorted()
    }

    func load(force: Bool = false) async {
        // 이미 데이터 있고 강제 새로고침 아니면 스킵
        guard force || allEvents.isEmpty else { return }
        isLoading = true
        errorMessage = nil
        do {
            allEvents = try await client.recentEvents(limit: 50)
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }
}

// MARK: - ChartListView

struct ChartListView: View {
    @State private var vm: ChartListViewModel
    @State private var store = PatientStore()
    let client: BridgeClient

    init(client: BridgeClient) {
        self.client = client
        self._vm = State(wrappedValue: ChartListViewModel(client: client))
    }

    var body: some View {
        NavigationStack {
            Group {
                if vm.isLoading && vm.allEvents.isEmpty {
                    loadingView
                } else if let err = vm.errorMessage, vm.allEvents.isEmpty {
                    errorView(err)
                } else if vm.filteredEvents.isEmpty {
                    emptyView
                } else {
                    listView
                }
            }
            .navigationTitle("차트")
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await vm.load(force: true) }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .disabled(vm.isLoading)
                }
            }
            .safeAreaInset(edge: .top, spacing: 0) {
                if !vm.patientNames.isEmpty {
                    patientFilterBar
                }
            }
            .task { await vm.load() }
            .refreshable { await vm.load(force: true) }
        }
    }

    // MARK: - 환자 필터 바

    private var patientFilterBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                // 전체 칩
                FilterChip(
                    label: "전체",
                    icon: "person.3",
                    isSelected: vm.selectedPatient == nil
                ) {
                    withAnimation(.spring(response: 0.3)) { vm.selectedPatient = nil }
                }

                // 환자별 칩
                ForEach(vm.patientNames, id: \.self) { name in
                    FilterChip(
                        label: name,
                        icon: "person.fill",
                        isSelected: vm.selectedPatient == name
                    ) {
                        withAnimation(.spring(response: 0.3)) {
                            vm.selectedPatient = vm.selectedPatient == name ? nil : name
                        }
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
        }
        .background(.ultraThinMaterial)
        .overlay(alignment: .bottom) {
            Divider()
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
            Button("다시 시도") { Task { await vm.load(force: true) } }
                .buttonStyle(.borderedProminent)
        }
        .padding(32).frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: 빈 목록
    private var emptyView: some View {
        VStack(spacing: 16) {
            Image(systemName: vm.selectedPatient != nil ? "person.slash" : "doc.text.magnifyingglass")
                .font(.system(size: 52)).foregroundStyle(.tertiary)
            if let name = vm.selectedPatient {
                Text("\(name) 환자 차트 없음").font(.headline)
                Text("이 환자로 텍스트·음성·영상을 전송하면\n차트가 자동 생성됩니다.")
                    .font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
                Button("전체 보기") {
                    withAnimation { vm.selectedPatient = nil }
                }
                .buttonStyle(.bordered)
            } else {
                Text("차트가 없어요").font(.headline)
                Text("텍스트, 음성, 영상을 전송하면\n자동으로 차트가 생성됩니다.")
                    .font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: 목록
    private var listView: some View {
        List {
            // 현재 필터 헤더
            if let name = vm.selectedPatient {
                Section {
                    HStack {
                        Label(name, systemImage: "person.fill")
                            .font(.subheadline).fontWeight(.medium)
                        Spacer()
                        Text("\(vm.filteredEvents.count)건")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }

            ForEach(vm.filteredEvents) { event in
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

// MARK: - 필터 칩

private struct FilterChip: View {
    let label: String
    let icon: String
    let isSelected: Bool
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 5) {
                Image(systemName: icon)
                    .font(.system(size: 11, weight: .medium))
                Text(label)
                    .font(.system(size: 13, weight: isSelected ? .semibold : .regular))
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(
                isSelected ? Color.accentColor : Color(.secondarySystemBackground),
                in: Capsule()
            )
            .foregroundStyle(isSelected ? .white : .primary)
        }
        .buttonStyle(.plain)
    }
}

// MARK: - 행 뷰

private struct ChartRowView: View {
    let event: BridgeClient.RecentEvent

    var typeIcon: String {
        switch event.event_type {
        case "audio": return "mic.fill"
        case "video": return "video.fill"
        default:      return "text.alignleft"
        }
    }

    var typeColor: Color {
        switch event.event_type {
        case "audio": return .purple
        case "video": return .blue
        default:      return .orange
        }
    }

    var formattedDate: String {
        let parts = event.created_at.components(separatedBy: " ")
        guard parts.count == 2 else { return event.created_at }
        let dateParts = parts[0].components(separatedBy: "-")
        guard dateParts.count == 3 else { return event.created_at }
        let time = parts[1].components(separatedBy: ":").prefix(2).joined(separator: ":")
        return "\(dateParts[1])/\(dateParts[2]) \(time)"
    }

    var typeLabel: String {
        switch event.event_type {
        case "audio": return "음성 기록"
        case "video": return "영상 분석"
        default:      return "텍스트 메모"
        }
    }

    var body: some View {
        HStack(spacing: 12) {
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
                    VStack(alignment: .leading, spacing: 1) {
                        Text(typeLabel)
                            .font(.subheadline).fontWeight(.semibold)
                        if let name = event.patient_name, !name.isEmpty {
                            Label(name, systemImage: "person.fill")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    Text(formattedDate)
                        .font(.caption2).foregroundStyle(.secondary)
                }

                HStack(spacing: 6) {
                    Text(event.status == "processed" ? "완료" : event.status)
                        .font(.caption2).fontWeight(.medium)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(event.status == "processed" ? Color.green.opacity(0.12) : Color.orange.opacity(0.12),
                                    in: Capsule())
                        .foregroundStyle(event.status == "processed" ? .green : .orange)

                    if event.has_label {
                        Label("라벨됨", systemImage: "tag.fill")
                            .font(.caption2).foregroundStyle(.orange)
                            .labelStyle(.iconOnly)
                    }

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
