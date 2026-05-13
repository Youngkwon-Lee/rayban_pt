import SwiftUI
import UIKit

// MARK: - ViewModel

struct MergeCandidate {
    let image: BridgeClient.RecentEvent
    let audio: BridgeClient.RecentEvent
    let patientName: String

    var detailText: String {
        "\(patientName) · \(mediaLabel(image)) \(shortID(image)) + \(mediaLabel(audio)) \(shortID(audio))"
    }

    private func shortID(_ event: BridgeClient.RecentEvent) -> String {
        String(event.id.prefix(8))
    }

    private func mediaLabel(_ event: BridgeClient.RecentEvent) -> String {
        switch event.event_type {
        case "image": return "카메라"
        case "video": return "영상"
        case "audio": return "음성"
        case "text": return "텍스트"
        default: return event.event_type
        }
    }
}

@MainActor
@Observable
final class ChartListViewModel {
    var allEvents: [BridgeClient.RecentEvent] = []
    var reviewItems: [ChartReviewItem] = []
    var isLoading = false
    var isManaging = false
    var isMerging = false
    var errorMessage: String? = nil
    var statusMessage: String? = nil
    var statusIsError = false
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

    var filteredReviewItems: [ChartReviewItem] {
        guard let name = selectedPatient else { return reviewItems }
        return reviewItems.filter {
            $0.patient_name?.trimmingCharacters(in: .whitespacesAndNewlines) == name
        }
    }

    /// 서버에 있는 환자 이름 목록 (중복 제거, 정렬)
    var patientNames: [String] {
        let names = allEvents.compactMap { $0.patient_name }.filter { !$0.isEmpty }
        return Array(Set(names)).sorted()
    }

    var mergeCandidate: MergeCandidate? {
        if let selectedPatient {
            return mergeCandidate(for: selectedPatient)
        }

        var checkedPatients = Set<String>()
        for event in allEvents {
            guard let patient = normalizedPatientName(event), checkedPatients.insert(patient).inserted else {
                continue
            }
            if let candidate = mergeCandidate(for: patient) {
                return candidate
            }
        }
        return nil
    }

    var mergeHintText: String {
        if let candidate = mergeCandidate {
            return candidate.detailText
        }
        if let selectedPatient {
            return "\(selectedPatient) 환자의 음성+카메라 기록이 필요합니다"
        }
        return "같은 환자의 음성+카메라 기록이 필요합니다"
    }

    func load(force: Bool = false) async {
        // 이미 데이터 있고 강제 새로고침 아니면 스킵
        guard force || allEvents.isEmpty else { return }
        isLoading = true
        errorMessage = nil
        do {
            allEvents = try await client.recentEvents(limit: 50)
            reviewItems = (try? await client.chartReviewItems(limit: 50)) ?? []
        } catch {
            errorMessage = UserFacingError.message(for: error)
        }
        isLoading = false
    }

    func delete(event: BridgeClient.RecentEvent) async {
        isManaging = true
        statusMessage = nil
        statusIsError = false
        defer { isManaging = false }

        do {
            let result = try await client.deleteEvent(eventId: event.id)
            allEvents.removeAll { $0.id == event.id }
            reviewItems.removeAll { $0.event_id == event.id }
            statusMessage = result.deleted_files.isEmpty ? "기록 삭제 완료" : "기록과 첨부 파일 삭제 완료"
        } catch {
            statusIsError = true
            statusMessage = UserFacingError.message(for: error)
        }
    }

    func purgeOldEvents(days: Int = 30) async {
        isManaging = true
        statusMessage = nil
        statusIsError = false
        defer { isManaging = false }

        do {
            let result = try await client.purgeOldEvents(days: days)
            allEvents = try await client.recentEvents(limit: 50)
            reviewItems = (try? await client.chartReviewItems(limit: 50)) ?? []
            statusMessage = result.purged_events == 0 ? "정리할 오래된 기록이 없습니다" : "\(result.purged_events)건 정리 완료"
        } catch {
            statusIsError = true
            statusMessage = UserFacingError.message(for: error)
        }
    }

    func revokeConsent(patientName: String) async {
        isManaging = true
        statusMessage = nil
        statusIsError = false
        defer { isManaging = false }

        do {
            let result = try await client.revokeConsent(patientName: patientName)
            statusMessage = result.revoked == 0 ? "철회할 활성 동의가 없습니다" : "\(patientName) 동의 철회 완료"
        } catch {
            statusIsError = true
            statusMessage = UserFacingError.message(for: error)
        }
    }

    func mergeLatestPair() async -> String? {
        guard let candidate = mergeCandidate else {
            statusIsError = true
            statusMessage = mergeHintText
            return nil
        }

        isMerging = true
        isManaging = true
        statusMessage = "통합 차트 생성 중..."
        statusIsError = false
        defer {
            isMerging = false
            isManaging = false
        }

        do {
            let result = try await client.mergeEvents(
                imageEventId: candidate.image.id,
                audioEventId: candidate.audio.id,
                patientName: candidate.patientName
            )
            allEvents = try await client.recentEvents(limit: 50)
            reviewItems = (try? await client.chartReviewItems(limit: 50)) ?? []
            statusMessage = "\(result.patient_name ?? candidate.patientName) 통합 차트 생성 완료"
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            return result.event_id
        } catch {
            statusIsError = true
            statusMessage = UserFacingError.message(for: error)
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            return nil
        }
    }

    private func mergeCandidate(for patientName: String) -> MergeCandidate? {
        let events = allEvents.filter {
            normalizedPatientName($0) == patientName && $0.status == "processed"
        }
        guard let image = events.first(where: { isImageEvent($0) }),
              let audio = events.first(where: { isAudioEvent($0) }) else {
            return nil
        }
        return MergeCandidate(image: image, audio: audio, patientName: patientName)
    }

    private func normalizedPatientName(_ event: BridgeClient.RecentEvent) -> String? {
        guard let name = event.patient_name?.trimmingCharacters(in: .whitespacesAndNewlines),
              !name.isEmpty else {
            return nil
        }
        return name
    }

    private func isImageEvent(_ event: BridgeClient.RecentEvent) -> Bool {
        event.event_type == "image" || event.event_type == "video"
    }

    private func isAudioEvent(_ event: BridgeClient.RecentEvent) -> Bool {
        event.event_type == "audio" || event.event_type == "text"
    }
}

// MARK: - ChartListView

struct ChartListView: View {
    @State private var vm: ChartListViewModel
    @State private var store = PatientStore()
    @State private var pendingDeleteEvent: BridgeClient.RecentEvent?
    @State private var showPurgeConfirm = false
    @State private var showRevokeConsentConfirm = false
    @State private var showAuditLogs = false
    @State private var generatedChartEventId: String?
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
                    HStack(spacing: 12) {
                        Button {
                            Task { await vm.load(force: true) }
                        } label: {
                            Image(systemName: "arrow.clockwise")
                        }
                        .disabled(vm.isLoading || vm.isManaging)

                        Menu {
                            Button {
                                showAuditLogs = true
                            } label: {
                                Label("감사 로그", systemImage: "list.bullet.rectangle")
                            }

                            Button(role: .destructive) {
                                showPurgeConfirm = true
                            } label: {
                                Label("30일 지난 기록 정리", systemImage: "calendar.badge.minus")
                            }

                            if let patient = vm.selectedPatient {
                                Button(role: .destructive) {
                                    showRevokeConsentConfirm = true
                                } label: {
                                    Label("\(patient) 동의 철회", systemImage: "checkmark.shield")
                                }
                            }
                        } label: {
                            Image(systemName: "ellipsis.circle")
                        }
                        .disabled(vm.isManaging)
                    }
                }
            }
            .safeAreaInset(edge: .top, spacing: 0) {
                if !vm.patientNames.isEmpty {
                    patientFilterBar
                }
            }
            .onAppear {
                Task { await vm.load(force: true) }
            }
            .refreshable { await vm.load(force: true) }
            .onReceive(NotificationCenter.default.publisher(for: Notification.Name("chartReviewDidChange"))) { _ in
                Task { await vm.load(force: true) }
            }
            .onReceive(NotificationCenter.default.publisher(for: Notification.Name("bridgeSettingsDidChange"))) { _ in
                Task { await vm.load(force: true) }
            }
            .sheet(isPresented: $showAuditLogs) {
                NavigationStack {
                    AuditLogView(client: client)
                }
            }
            .sheet(isPresented: Binding(
                get: { generatedChartEventId != nil },
                set: { if !$0 { generatedChartEventId = nil } }
            )) {
                if let eventId = generatedChartEventId {
                    NavigationStack {
                        ChartDetailView(eventId: eventId, client: client)
                    }
                }
            }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                if let message = vm.statusMessage {
                    statusBanner(message)
                }
            }
            .alert(item: $pendingDeleteEvent) { event in
                Alert(
                    title: Text("기록 삭제"),
                    message: Text("이 차트와 연결된 서버 기록, 차트 파일, 마스킹 파일을 삭제합니다."),
                    primaryButton: .destructive(Text("삭제")) {
                        Task { await vm.delete(event: event) }
                    },
                    secondaryButton: .cancel(Text("취소"))
                )
            }
            .alert("오래된 기록 정리", isPresented: $showPurgeConfirm) {
                Button("30일 지난 기록 정리", role: .destructive) {
                    Task { await vm.purgeOldEvents(days: 30) }
                }
                Button("취소", role: .cancel) { }
            } message: {
                Text("생성 후 30일이 지난 이벤트와 차트 파일을 삭제합니다.")
            }
            .alert("동의 철회", isPresented: $showRevokeConsentConfirm) {
                Button("철회", role: .destructive) {
                    if let patient = vm.selectedPatient {
                        Task { await vm.revokeConsent(patientName: patient) }
                    }
                }
                Button("취소", role: .cancel) { }
            } message: {
                Text("선택한 환자의 활성 동의 기록을 철회합니다. 이후 새 분석 전 다시 동의 기록이 필요합니다.")
            }
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
            mergeActionSection
            reviewQueueSection

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
                    Button(role: .destructive) {
                        showRevokeConsentConfirm = true
                    } label: {
                        Label("동의 철회", systemImage: "checkmark.shield")
                    }
                    .disabled(vm.isManaging)
                }
            }

            ForEach(vm.filteredEvents) { event in
                NavigationLink {
                    ChartDetailView(eventId: event.id, client: client)
                } label: {
                    ChartRowView(event: event)
                }
                .accessibilityIdentifier("chartEventRow")
                .listRowInsets(EdgeInsets(top: 8, leading: 16, bottom: 8, trailing: 16))
                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                    Button(role: .destructive) {
                        pendingDeleteEvent = event
                    } label: {
                        Label("삭제", systemImage: "trash")
                    }
                }
            }
        }
        .listStyle(.plain)
        .accessibilityIdentifier("chartList")
    }

    private var mergeActionSection: some View {
        Section {
            Button {
                Task {
                    if let eventId = await vm.mergeLatestPair() {
                        generatedChartEventId = eventId
                    }
                }
            } label: {
                HStack(spacing: 12) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(Color.indigo.opacity(0.12))
                            .frame(width: 44, height: 44)
                        Image(systemName: "link.circle.fill")
                            .font(.system(size: 19, weight: .semibold))
                            .foregroundStyle(.indigo)
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        Text("최신 음성+카메라 통합")
                            .font(.subheadline)
                            .fontWeight(.semibold)
                            .foregroundStyle(.primary)
                        Text(vm.mergeHintText)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }

                    Spacer()

                    if vm.isMerging {
                        ProgressView()
                    } else {
                        Image(systemName: vm.mergeCandidate == nil ? "exclamationmark.circle" : "chevron.right")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(vm.mergeCandidate == nil ? Color.orange : Color.secondary)
                    }
                }
                .padding(.vertical, 4)
            }
            .buttonStyle(.plain)
            .disabled(vm.isManaging || vm.mergeCandidate == nil)
            .accessibilityIdentifier("mergeLatestChartButton")
        }
    }

    private var reviewQueueSection: some View {
        Section {
            if vm.filteredReviewItems.isEmpty {
                HStack(spacing: 10) {
                    Image(systemName: "checkmark.seal.fill")
                        .foregroundStyle(.green)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("수정 필요한 차트 없음")
                            .font(.subheadline.weight(.semibold))
                        Text("차트 품질 큐가 비어 있습니다")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(.vertical, 4)
                .accessibilityIdentifier("chartReviewQueueEmpty")
            } else {
                ForEach(vm.filteredReviewItems.prefix(3)) { item in
                    NavigationLink {
                        ChartDetailView(eventId: item.event_id, client: client)
                    } label: {
                        ChartReviewRowView(item: item)
                    }
                    .accessibilityIdentifier("chartReviewQueueRow")
                }

                if vm.filteredReviewItems.count > 3 {
                    Text("외 \(vm.filteredReviewItems.count - 3)건은 환자 필터를 조정하거나 새로고침 후 확인하세요.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        } header: {
            Text("검수 큐")
        }
    }

    private func statusBanner(_ message: String) -> some View {
        HStack(spacing: 10) {
            Image(systemName: vm.statusIsError ? "exclamationmark.triangle.fill" : "checkmark.circle.fill")
                .foregroundStyle(vm.statusIsError ? .orange : .green)
            Text(message)
                .font(.caption)
                .foregroundStyle(.primary)
                .lineLimit(2)
            Spacer()
            Button {
                vm.statusMessage = nil
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(.bar)
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
        case "image": return "camera.fill"
        case "video": return "video.fill"
        case "combined": return "link.circle.fill"
        default:      return "text.alignleft"
        }
    }

    var typeColor: Color {
        switch event.event_type {
        case "audio": return .purple
        case "image": return .green
        case "video": return .blue
        case "combined": return .indigo
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
        case "image": return "이미지 분석"
        case "video": return "영상 분석"
        case "combined": return "통합 차트"
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

private struct ChartReviewRowView: View {
    let item: ChartReviewItem

    private var tint: Color {
        item.quality.level == "needs_edit" ? .red : .orange
    }

    private var title: String {
        item.quality.level == "needs_edit" ? "수정 필요" : "검수 권장"
    }

    private var formattedDate: String {
        let parts = item.created_at.components(separatedBy: " ")
        guard parts.count == 2 else { return item.created_at }
        let dateParts = parts[0].components(separatedBy: "-")
        guard dateParts.count == 3 else { return item.created_at }
        let time = parts[1].components(separatedBy: ":").prefix(2).joined(separator: ":")
        return "\(dateParts[1])/\(dateParts[2]) \(time)"
    }

    private var issueText: String {
        if item.primary_issue.isEmpty {
            return "품질 검수 필요"
        }
        return item.primary_issue
    }

    var body: some View {
        HStack(spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(tint.opacity(0.12))
                    .frame(width: 44, height: 44)
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(tint)
            }

            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Text(title)
                        .font(.subheadline.weight(.semibold))
                    Text("\(item.quality.score)점")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(tint)
                    Spacer()
                    Text(formattedDate)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }

                Text(issueText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)

                HStack(spacing: 6) {
                    if let name = item.patient_name, !name.isEmpty {
                        Label(name, systemImage: "person.fill")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    Text(String(item.event_id.prefix(8)) + "…")
                        .font(.caption2.monospaced())
                        .foregroundStyle(.tertiary)
                    Spacer()
                }
            }
        }
        .padding(.vertical, 4)
    }
}

// MARK: - 감사 로그

@MainActor
@Observable
private final class AuditLogViewModel {
    enum Filter: String, CaseIterable, Identifiable {
        case all
        case error
        case info

        var id: String { rawValue }

        var title: String {
            switch self {
            case .all: return "전체"
            case .error: return "오류"
            case .info: return "정보"
            }
        }

        var level: String? {
            switch self {
            case .all: return nil
            case .error: return "error"
            case .info: return "info"
            }
        }
    }

    var items: [AuditLog] = []
    var isLoading = false
    var errorMessage: String? = nil
    var filter: Filter = .all

    let client: BridgeClient

    init(client: BridgeClient) {
        self.client = client
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        do {
            items = try await client.auditLogs(limit: 100, level: filter.level)
        } catch {
            errorMessage = UserFacingError.message(for: error)
        }
        isLoading = false
    }
}

private struct AuditLogView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var vm: AuditLogViewModel

    init(client: BridgeClient) {
        _vm = State(wrappedValue: AuditLogViewModel(client: client))
    }

    var body: some View {
        List {
            if let error = vm.errorMessage {
                Section {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                }
            }

            if vm.isLoading && vm.items.isEmpty {
                Section {
                    HStack {
                        ProgressView()
                        Text("로그 불러오는 중...")
                            .foregroundStyle(.secondary)
                    }
                }
            } else if vm.items.isEmpty && vm.errorMessage == nil {
                ContentUnavailableView("감사 로그 없음", systemImage: "list.bullet.rectangle")
            } else {
                Section {
                    ForEach(vm.items) { item in
                        AuditLogRow(item: item)
                    }
                }
            }
        }
        .navigationTitle("감사 로그")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button("닫기") { dismiss() }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { await vm.load() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .disabled(vm.isLoading)
            }
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            Picker("필터", selection: $vm.filter) {
                ForEach(AuditLogViewModel.Filter.allCases) { filter in
                    Text(filter.title).tag(filter)
                }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .background(.bar)
        }
        .task { await vm.load() }
        .onChange(of: vm.filter) { _, _ in
            Task { await vm.load() }
        }
        .refreshable { await vm.load() }
    }
}

private struct AuditLogRow: View {
    let item: AuditLog

    var tint: Color {
        switch item.level {
        case "error": return .red
        case "warning": return .orange
        default: return .blue
        }
    }

    var icon: String {
        switch item.level {
        case "error": return "exclamationmark.triangle.fill"
        case "warning": return "exclamationmark.circle.fill"
        default: return "info.circle.fill"
        }
    }

    var formattedDate: String {
        let parts = item.created_at.components(separatedBy: " ")
        guard parts.count == 2 else { return item.created_at }
        let dateParts = parts[0].components(separatedBy: "-")
        guard dateParts.count == 3 else { return item.created_at }
        let time = parts[1].components(separatedBy: ":").prefix(2).joined(separator: ":")
        return "\(dateParts[1])/\(dateParts[2]) \(time)"
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(tint)
                .frame(width: 24, height: 24)

            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 8) {
                    Text(item.level.uppercased())
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(tint)
                    Text(formattedDate)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Spacer()
                }

                Text(item.message)
                    .font(.subheadline)
                    .foregroundStyle(.primary)
                    .textSelection(.enabled)

                if let eventId = item.event_id, !eventId.isEmpty {
                    Text(String(eventId.prefix(8)) + "...")
                        .font(.caption2.monospaced())
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
            }
        }
        .padding(.vertical, 4)
    }
}
