import SwiftUI

// MARK: - 차트 섹션 모델

private struct ChartSection: Identifiable {
    let id = UUID()
    let key: String       // "S>", "P/E>" 등 원본 키
    let title: String     // 표시용 한글 제목
    let icon: String      // SF Symbol
    let content: String
    var color: Color { Self.colorMap[key] ?? .secondary }

    static let colorMap: [String: Color] = [
        "F/U>":    .blue,
        "Dx.>":    .purple,
        "S>":      .orange,
        "P/E>":    .green,
        "rehab device>": .teal,
        "PTx.>":   .indigo,
        "Comment>": .gray,
    ]

    static let iconMap: [String: String] = [
        "F/U>":    "calendar.badge.clock",
        "Dx.>":    "stethoscope",
        "S>":      "mic.fill",
        "P/E>":    "figure.arms.open",
        "rehab device>": "medical.thermometer",
        "PTx.>":   "figure.walk",
        "Comment>": "text.bubble.fill",
    ]

    static let titleMap: [String: String] = [
        "F/U>":    "F/U (경과)",
        "Dx.>":    "진단",
        "S>":      "주관적 소견",
        "P/E>":    "신체 검사",
        "rehab device>": "재활 기기",
        "PTx.>":   "치료 계획",
        "Comment>": "코멘트",
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
private final class ChartViewModel {
    var sections: [ChartSection] = []
    var isLoading = true
    var errorMessage: String? = nil
    var rawText: String = ""

    let eventId: String
    private let baseURL: URL = {
        let stored = UserDefaults.standard.string(forKey: "bridge_base_url") ?? ""
        return URL(string: stored) ?? URL(string: "http://localhost:8791")!
    }()

    init(eventId: String) {
        self.eventId = eventId
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        do {
            let url = baseURL.appendingPathComponent("charts/\(eventId)")
            let (data, _) = try await URLSession.shared.data(from: url)
            let json = try JSONDecoder().decode(ChartResponse.self, from: data)
            rawText = json.chart
            sections = parse(json.chart)
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    private func parse(_ text: String) -> [ChartSection] {
        let keys = ["F/U>", "Dx.>", "S>", "P/E>", "rehab device>", "PTx.>", "Comment>"]
        var result: [ChartSection] = []

        var current: (key: String, lines: [String])? = nil

        for line in text.components(separatedBy: "\n") {
            if let key = keys.first(where: { line.trimmingCharacters(in: .whitespaces) == $0 }) {
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

    struct ChartResponse: Decodable {
        let chart: String
    }
}

// MARK: - ChartDetailView

struct ChartDetailView: View {
    let eventId: String
    @State private var vm: ChartViewModel
    @State private var expandedSections: Set<String> = []
    @State private var showShareSheet = false

    init(eventId: String) {
        self.eventId = eventId
        self._vm = State(wrappedValue: ChartViewModel(eventId: eventId))
    }

    var body: some View {
        NavigationStack {
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
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showShareSheet = true
                    } label: {
                        Image(systemName: "square.and.arrow.up")
                    }
                    .disabled(vm.rawText.isEmpty)
                }
            }
            .sheet(isPresented: $showShareSheet) {
                ShareSheet(text: vm.rawText)
            }
        }
        .task { await vm.load() }
    }

    // MARK: - 로딩

    private var loadingView: some View {
        VStack(spacing: 16) {
            ProgressView()
                .scaleEffect(1.2)
            Text("차트 로딩 중...")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - 에러

    private func errorView(_ msg: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 40))
                .foregroundStyle(.orange)
            Text("차트를 불러올 수 없어요")
                .font(.headline)
            Text(msg)
                .font(.caption)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("다시 시도") {
                Task { await vm.load() }
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - 차트 본문

    private var chartContent: some View {
        ScrollView {
            VStack(spacing: 12) {
                // 이벤트 ID 배지
                HStack {
                    Label(String(eventId.prefix(8)) + "...", systemImage: "number")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                    Spacer()
                    Label("자동생성 초안", systemImage: "wand.and.stars")
                        .font(.caption2)
                        .foregroundStyle(.orange)
                }
                .padding(.horizontal, 4)

                // 섹션 카드들
                ForEach(vm.sections) { section in
                    SectionCard(section: section)
                }

                // 하단 여백
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
            // 헤더
            Button {
                withAnimation(.spring(response: 0.3, dampingFraction: 0.75)) {
                    isExpanded.toggle()
                }
                UIImpactFeedbackGenerator(style: .light).impactOccurred()
            } label: {
                HStack(spacing: 10) {
                    ZStack {
                        Circle()
                            .fill(section.color.opacity(0.15))
                            .frame(width: 34, height: 34)
                        Image(systemName: section.icon)
                            .font(.system(size: 15, weight: .medium))
                            .foregroundStyle(section.color)
                    }

                    Text(section.title)
                        .font(.subheadline)
                        .fontWeight(.semibold)
                        .foregroundStyle(.primary)

                    Spacer()

                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
            }
            .buttonStyle(.plain)

            // 내용
            if isExpanded {
                Divider()
                    .padding(.horizontal, 14)

                Text(cleanContent(section.content))
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 12)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .background(Color(.secondarySystemBackground),
                    in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .shadow(color: .black.opacity(0.04), radius: 6, y: 2)
    }

    // # 주석 제거하고 보기 좋게
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
