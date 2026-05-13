import SwiftUI

private func dismissChartKeyboard() {
    UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
}

// MARK: - 차트 섹션 모델

struct ChartSection: Identifiable {
    let id = UUID()
    let key: String
    let title: String
    let icon: String
    let content: String
    var color: Color { Self.colorMap[key] ?? .secondary }

    static let colorMap: [String: Color] = [
        "F/U>":           .blue,
        "Dx.>":           .purple,
        "S>":             .orange,
        "O>":             .cyan,
        "P/E>":           .green,
        "A>":             .red,
        "rehab device>":  .teal,
        "PTx.>":          .indigo,
        "Comment>":       .gray,
    ]
    static let iconMap: [String: String] = [
        "F/U>":           "calendar.badge.clock",
        "Dx.>":           "stethoscope",
        "S>":             "mic.fill",
        "O>":             "chart.bar.xaxis",
        "P/E>":           "figure.arms.open",
        "A>":             "brain.head.profile",
        "rehab device>":  "medical.thermometer",
        "PTx.>":          "figure.walk",
        "Comment>":       "text.bubble.fill",
    ]
    static let titleMap: [String: String] = [
        "F/U>":           "F/U (경과)",
        "Dx.>":           "진단",
        "S>":             "주관적 소견",
        "O>":             "객관적 측정값",
        "P/E>":           "신체 검사",
        "A>":             "임상 해석",
        "rehab device>":  "재활 기기",
        "PTx.>":          "치료 계획",
        "Comment>":       "코멘트",
    ]

    init(key: String, content: String) {
        self.key = key
        self.title = Self.titleMap[key] ?? key
        self.icon = Self.iconMap[key] ?? "doc.text"
        self.content = content
    }
}

// MARK: - ViewModel

@MainActor
@Observable
final class ChartDetailViewModel {
    var sections: [ChartSection] = []
    var isLoading = true
    var errorMessage: String? = nil
    var rawText: String = ""
    var createdAt: String = ""
    var quality: ChartQuality?
    var review: ChartReviewRecord?

    let eventId: String
    let client: BridgeClient

    init(eventId: String, client: BridgeClient) {
        self.eventId = eventId
        self.client = client
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        do {
            let res = try await client.fetchChart(eventId: eventId)
            rawText = res.chart
            quality = res.quality
            review = res.review
            sections = parse(res.chart)
        } catch {
            errorMessage = UserFacingError.message(for: error)
        }
        isLoading = false
    }

    func saveEditedChart(_ text: String) async throws {
        let res = try await client.updateChart(eventId: eventId, chart: text)
        rawText = res.chart
        quality = res.quality
        review = res.review
        sections = parse(res.chart)
    }

    func markReviewed() async throws {
        let res = try await client.markChartReviewed(eventId: eventId)
        quality = res.quality
        review = res.review
    }

    func clearReview() async throws {
        let res = try await client.clearChartReview(eventId: eventId)
        quality = res.quality
        review = res.review
    }

    private func parse(_ text: String) -> [ChartSection] {
        let keys = ["F/U>", "Dx.>", "S>", "O>", "P/E>", "A>", "rehab device>", "PTx.>", "Comment>"]
        var result: [ChartSection] = []
        var current: (key: String, lines: [String])? = nil

        for line in text.components(separatedBy: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if let key = keys.first(where: { trimmed == $0 }) {
                if let c = current {
                    let body = c.lines.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
                    if !body.isEmpty { result.append(ChartSection(key: c.key, content: body)) }
                }
                current = (key: key, lines: [])
            } else if current != nil {
                current!.lines.append(line)
            }
        }
        if let c = current {
            let body = c.lines.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !body.isEmpty { result.append(ChartSection(key: c.key, content: body)) }
        }
        return result
    }
}

// MARK: - ChartDetailView

struct ChartDetailView: View {
    let eventId: String
    let client: BridgeClient
    @Environment(\.dismiss) private var dismiss
    @State private var vm: ChartDetailViewModel
    @State private var showShareSheet = false
    @State private var showLabelSheet = false
    @State private var showDeleteConfirm = false
    @State private var showEditSheet = false
    @State private var deleteError: String?
    @State private var editError: String?
    @State private var reviewError: String?
    @State private var isDeleting = false
    @State private var isSavingEdit = false
    @State private var isMarkingReviewed = false
    @State private var editDraft = ""
    @State private var hasLabel = false

    init(eventId: String, client: BridgeClient) {
        self.eventId = eventId
        self.client = client
        self._vm = State(wrappedValue: ChartDetailViewModel(eventId: eventId, client: client))
    }

    var body: some View {
        Group {
            if vm.isLoading {
                loadingView
            } else if let err = vm.errorMessage {
                errorView(err)
            } else {
                chartContent
            }
        }
        .navigationTitle("재활 차트")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                HStack(spacing: 12) {
                    Button {
                        editDraft = vm.rawText
                        editError = nil
                        showEditSheet = true
                    } label: {
                        Image(systemName: "square.and.pencil")
                    }
                    .disabled(vm.rawText.isEmpty)
                    .accessibilityIdentifier("chartToolbarEditButton")

                    // 라벨링 버튼
                    Button {
                        showLabelSheet = true
                    } label: {
                        Image(systemName: hasLabel ? "tag.fill" : "tag")
                            .foregroundStyle(hasLabel ? .orange : .primary)
                    }
                    .accessibilityIdentifier("chartToolbarLabelButton")
                    // 공유 버튼
                    Button {
                        showShareSheet = true
                    } label: {
                        Image(systemName: "square.and.arrow.up")
                    }
                    .disabled(vm.rawText.isEmpty)
                    .accessibilityIdentifier("chartShareButton")

                    Menu {
                        Button(role: .destructive) {
                            showDeleteConfirm = true
                        } label: {
                            Label("기록 삭제", systemImage: "trash")
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                    .disabled(isDeleting)
                }
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if !vm.isLoading && vm.errorMessage == nil {
                labelActionBar
            }
        }
        .alert("검수 저장 실패", isPresented: Binding(
            get: { reviewError != nil },
            set: { if !$0 { reviewError = nil } }
        )) {
            Button("확인", role: .cancel) { reviewError = nil }
        } message: {
            Text(reviewError ?? "")
        }
        .sheet(isPresented: $showShareSheet) {
            ShareSheet(text: vm.rawText)
        }
        .sheet(isPresented: $showLabelSheet) {
            LabelingView(eventId: eventId, client: client)
                .onDisappear {
                    // 라벨 저장 후 뱃지 업데이트
                    Task {
                        let label = try? await client.fetchLabel(eventId: eventId)
                        hasLabel = label != nil
                    }
                }
        }
        .sheet(isPresented: $showEditSheet) {
            ChartEditorSheet(
                text: $editDraft,
                isSaving: isSavingEdit,
                errorMessage: editError
            ) {
                Task { await saveEditedChart() }
            }
        }
        .alert("기록 삭제", isPresented: $showDeleteConfirm) {
            Button("삭제", role: .destructive) {
                Task { await deleteCurrentEvent() }
            }
            Button("취소", role: .cancel) { }
        } message: {
            Text("이 차트와 연결된 서버 기록, 차트 파일, 마스킹 파일을 삭제합니다.")
        }
        .alert("삭제 실패", isPresented: Binding(
            get: { deleteError != nil },
            set: { if !$0 { deleteError = nil } }
        )) {
            Button("확인", role: .cancel) { deleteError = nil }
        } message: {
            Text(deleteError ?? "")
        }
        .task {
            await vm.load()
            // 라벨 존재 여부 체크
            let label = try? await client.fetchLabel(eventId: eventId)
            hasLabel = label != nil
        }
    }

    private var labelActionBar: some View {
        HStack(spacing: 12) {
            Button {
                editDraft = vm.rawText
                editError = nil
                showEditSheet = true
            } label: {
                Label("수정", systemImage: "square.and.pencil")
                    .font(.subheadline.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
            }
            .buttonStyle(.bordered)
            .disabled(vm.rawText.isEmpty)
            .accessibilityIdentifier("chartEditActionButton")
            .accessibilityLabel("차트 수정")

            Button {
                Task { await toggleChartReview() }
            } label: {
                if isMarkingReviewed {
                    ProgressView()
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                } else {
                    Label(vm.review == nil ? "검수" : "해제", systemImage: vm.review == nil ? "checkmark.seal" : "arrow.uturn.backward")
                        .font(.subheadline.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                }
            }
            .buttonStyle(.borderedProminent)
            .tint(vm.review == nil ? .green : .orange)
            .disabled(vm.rawText.isEmpty || isMarkingReviewed)
            .accessibilityIdentifier("chartReviewToggleButton")
            .accessibilityLabel(vm.review == nil ? "검수 완료" : "검수 완료 해제")

            Button {
                showLabelSheet = true
            } label: {
                Label("라벨", systemImage: hasLabel ? "tag.fill" : "tag")
                    .font(.subheadline.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
            }
            .buttonStyle(.borderedProminent)
            .tint(hasLabel ? .orange : .blue)
            .accessibilityIdentifier("chartLabelActionButton")
            .accessibilityLabel(hasLabel ? "라벨 수정" : "라벨링하기")
        }
        .padding(.horizontal, 16)
        .padding(.top, 10)
        .padding(.bottom, 8)
        .background(.bar)
        .accessibilityIdentifier("chartActionBar")
    }

    private func toggleChartReview() async {
        isMarkingReviewed = true
        reviewError = nil
        defer { isMarkingReviewed = false }

        do {
            if vm.review == nil {
                try await vm.markReviewed()
            } else {
                try await vm.clearReview()
            }
            NotificationCenter.default.post(name: Notification.Name("chartReviewDidChange"), object: eventId)
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        } catch {
            reviewError = UserFacingError.message(for: error)
            UINotificationFeedbackGenerator().notificationOccurred(.error)
        }
    }

    private func saveEditedChart() async {
        isSavingEdit = true
        editError = nil
        defer { isSavingEdit = false }

        do {
            try await vm.saveEditedChart(editDraft)
            showEditSheet = false
            NotificationCenter.default.post(name: Notification.Name("chartReviewDidChange"), object: eventId)
        } catch {
            editError = UserFacingError.message(for: error)
        }
    }

    private func deleteCurrentEvent() async {
        isDeleting = true
        defer { isDeleting = false }

        do {
            try await client.deleteEvent(eventId: eventId)
            NotificationCenter.default.post(name: Notification.Name("chartReviewDidChange"), object: eventId)
            dismiss()
        } catch {
            deleteError = UserFacingError.message(for: error)
        }
    }

    // MARK: 로딩
    private var loadingView: some View {
        VStack(spacing: 16) {
            ProgressView().scaleEffect(1.2)
            Text("차트 로딩 중...")
                .font(.subheadline).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: 에러
    private func errorView(_ msg: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 40)).foregroundStyle(.orange)
            Text("차트를 불러올 수 없어요").font(.headline)
            Text(msg).font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
            Button("다시 시도") { Task { await vm.load() } }
                .buttonStyle(.borderedProminent)
        }
        .padding(32).frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityIdentifier("chartDetailError")
    }

    // MARK: 차트 본문
    private var chartContent: some View {
        ScrollView {
            VStack(spacing: 12) {
                HStack {
                    Label(String(eventId.prefix(8)) + "…", systemImage: "number")
                        .font(.caption2).foregroundStyle(.secondary).textSelection(.enabled)
                    Spacer()
                    Label("자동생성 초안", systemImage: "wand.and.stars")
                        .font(.caption2).foregroundStyle(.orange)
                }
                .padding(.horizontal, 4)

                if let quality = vm.quality {
                    ChartQualityCard(quality: quality, review: vm.review)
                }

                ForEach(vm.sections) { section in
                    SectionCard(section: section)
                }

                Spacer(minLength: 32)
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
        }
        .accessibilityIdentifier("chartDetailContent")
    }
}

private struct ChartQualityCard: View {
    let quality: ChartQuality
    let review: ChartReviewRecord?

    private var tint: Color {
        switch quality.level {
        case "good": return .green
        case "needs_edit": return .red
        default: return .orange
        }
    }

    private var title: String {
        switch quality.level {
        case "good": return "검수 상태 좋음"
        case "needs_edit": return "수정 필요"
        default: return "검수 권장"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Image(systemName: quality.level == "good" ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                    .foregroundStyle(tint)
                Text(title)
                    .font(.subheadline.weight(.semibold))
                Spacer()
                Text("\(quality.score)")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(tint)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(tint.opacity(0.12), in: Capsule())
            }

            if quality.issues.isEmpty {
                Text("기술 문구와 기본값이 감지되지 않았습니다.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(quality.issues.prefix(3)) { issue in
                        Label(issue.message, systemImage: issue.severity == "needs_edit" ? "xmark.circle" : "info.circle")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            if let review {
                Label("치료사 검수 완료 · \(review.reviewed_at)", systemImage: "checkmark.seal.fill")
                    .font(.caption)
                    .foregroundStyle(.green)
                    .accessibilityIdentifier("chartReviewedBadge")
            }
        }
        .padding(14)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

// MARK: - 섹션 카드

private struct SectionCard: View {
    let section: ChartSection
    @State private var isExpanded = true

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(.spring(response: 0.3, dampingFraction: 0.75)) { isExpanded.toggle() }
                UIImpactFeedbackGenerator(style: .light).impactOccurred()
            } label: {
                HStack(spacing: 10) {
                    ZStack {
                        Circle().fill(section.color.opacity(0.15)).frame(width: 34, height: 34)
                        Image(systemName: section.icon)
                            .font(.system(size: 15, weight: .medium)).foregroundStyle(section.color)
                    }
                    Text(section.title)
                        .font(.subheadline).fontWeight(.semibold).foregroundStyle(.primary)
                    Spacer()
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.caption).foregroundStyle(.tertiary)
                }
                .padding(.horizontal, 14).padding(.vertical, 12)
            }
            .buttonStyle(.plain)

            if isExpanded {
                Divider().padding(.horizontal, 14)
                Text(cleanContent(section.content))
                    .font(.callout).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
                    .padding(.horizontal, 14).padding(.vertical, 12)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .background(Color(.secondarySystemBackground),
                    in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .shadow(color: .black.opacity(0.04), radius: 6, y: 2)
    }

    private func cleanContent(_ raw: String) -> String {
        raw.components(separatedBy: "\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
            .map { $0.hasPrefix("# ") ? String($0.dropFirst(2)) : $0 }
            .joined(separator: "\n")
    }
}

private struct ChartEditorSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Binding var text: String
    let isSaving: Bool
    let errorMessage: String?
    let onSave: () -> Void

    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                if let errorMessage {
                    Label(errorMessage, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundStyle(.red)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }

                TextEditor(text: $text)
                    .font(.system(.callout, design: .monospaced))
                    .padding(10)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    .scrollDismissesKeyboard(.interactively)
            }
            .padding(16)
            .navigationTitle("차트 수정")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("취소") { dismiss() }
                        .disabled(isSaving)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        dismissChartKeyboard()
                        onSave()
                    } label: {
                        if isSaving {
                            ProgressView()
                        } else {
                            Label("저장", systemImage: "checkmark")
                        }
                    }
                    .disabled(isSaving || text.trimmingCharacters(in: .whitespacesAndNewlines).count < 20)
                }
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("완료") {
                        dismissChartKeyboard()
                    }
                }
            }
        }
    }
}

// MARK: - 공유 시트

private struct ShareSheet: UIViewControllerRepresentable {
    let text: String
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [text], applicationActivities: nil)
    }
    func updateUIViewController(_ uvc: UIActivityViewController, context: Context) {}
}
