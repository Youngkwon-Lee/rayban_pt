import SwiftUI

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
            sections = parse(res.chart)
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
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
    @State private var vm: ChartDetailViewModel
    @State private var showShareSheet = false
    @State private var showLabelSheet = false
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
                    // 라벨링 버튼
                    Button {
                        showLabelSheet = true
                    } label: {
                        Image(systemName: hasLabel ? "tag.fill" : "tag")
                            .foregroundStyle(hasLabel ? .orange : .primary)
                    }
                    // 공유 버튼
                    Button {
                        showShareSheet = true
                    } label: {
                        Image(systemName: "square.and.arrow.up")
                    }
                    .disabled(vm.rawText.isEmpty)
                }
            }
        }
        .sheet(isPresented: $showShareSheet) {
            ShareSheet(text: vm.rawText)
        }
        .sheet(isPresented: $showLabelSheet) {
            LabelingView(eventId: eventId, client: client)
                .onDisappear {
                    // 라벨 저장 후 뱃지 업데이트
                    Task {
                        if let label = try? await client.fetchLabel(eventId: eventId) {
                            hasLabel = label != nil
                        }
                    }
                }
        }
        .task {
            await vm.load()
            // 라벨 존재 여부 체크
            if let label = try? await client.fetchLabel(eventId: eventId) {
                hasLabel = label != nil
            }
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

                ForEach(vm.sections) { section in
                    SectionCard(section: section)
                }

                Spacer(minLength: 32)
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
        }
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
                    in: RoundedRectangle(cornerRadius: 12, style: .continuous))
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

// MARK: - 공유 시트

private struct ShareSheet: UIViewControllerRepresentable {
    let text: String
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [text], applicationActivities: nil)
    }
    func updateUIViewController(_ uvc: UIActivityViewController, context: Context) {}
}
