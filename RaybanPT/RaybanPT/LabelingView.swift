import SwiftUI

// MARK: - 옵션 정의

private let sessionTypes = ["기립훈련", "보행훈련", "상지운동", "하지운동", "균형훈련", "호흡훈련", "ADL훈련", "기타"]
private let assistLevels: [(code: String, label: String)] = [
    ("independent", "독립"),
    ("min",         "최소 보조"),
    ("mod",         "중등도 보조"),
    ("max",         "최대 보조"),
    ("dependent",   "완전 의존"),
]
private let performanceLevels = ["좋음", "보통", "저하", "악화"]
private let flagOptions = ["통증 호소", "피로", "자세흔들림", "낙상 위험", "순응도 저하", "집중도 저하", "부종"]

// MARK: - ViewModel

@MainActor
@Observable
final class LabelingViewModel {
    // 입력 필드
    var sessionType: String = sessionTypes[0]
    var coreTask: String = ""
    var assistLevel: String = "mod"
    var performance: String = "보통"
    var selectedFlags: Set<String> = []
    var notes: String = ""

    // 상태
    var isLoading = false
    var isSaving = false
    var savedLabel: BridgeClient.RehabLabel? = nil
    var errorMessage: String? = nil
    var saveSuccess = false

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
            if let label = try await client.fetchLabel(eventId: eventId) {
                apply(label)
                savedLabel = label
            }
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    func save() async {
        isSaving = true
        errorMessage = nil
        saveSuccess = false
        do {
            let label = try await client.saveLabel(
                eventId: eventId,
                sessionType: sessionType,
                coreTask: coreTask,
                assistLevel: assistLevel,
                performance: performance,
                flags: Array(selectedFlags).sorted(),
                notes: notes
            )
            savedLabel = label
            saveSuccess = true
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        } catch {
            errorMessage = error.localizedDescription
            UINotificationFeedbackGenerator().notificationOccurred(.error)
        }
        isSaving = false
    }

    private func apply(_ label: BridgeClient.RehabLabel) {
        sessionType  = label.session_type
        coreTask     = label.core_task
        assistLevel  = label.assist_level
        performance  = label.performance
        selectedFlags = Set(label.flags)
        notes        = label.notes
    }

    var canSave: Bool { !sessionType.isEmpty && !coreTask.isEmpty }
}

// MARK: - LabelingView (Sheet)

struct LabelingView: View {
    let eventId: String
    let client: BridgeClient
    @Environment(\.dismiss) private var dismiss
    @State private var vm: LabelingViewModel

    init(eventId: String, client: BridgeClient) {
        self.eventId = eventId
        self.client = client
        self._vm = State(wrappedValue: LabelingViewModel(eventId: eventId, client: client))
    }

    var body: some View {
        NavigationStack {
            Form {
                // ── 세션 유형
                Section {
                    Picker("세션 유형", selection: $vm.sessionType) {
                        ForEach(sessionTypes, id: \.self) { Text($0).tag($0) }
                    }
                } header: { Text("세션 유형") }

                // ── 핵심 과제
                Section {
                    TextField("예: 경부 회전+중립 유지, 계단 오르기", text: $vm.coreTask, axis: .vertical)
                        .lineLimit(2...4)
                } header: { Text("핵심 과제") }

                // ── 보조 수준
                Section {
                    ForEach(assistLevels, id: \.code) { item in
                        HStack {
                            Text(item.label)
                            Spacer()
                            if vm.assistLevel == item.code {
                                Image(systemName: "checkmark").foregroundStyle(.blue)
                            }
                        }
                        .contentShape(Rectangle())
                        .onTapGesture {
                            UIImpactFeedbackGenerator(style: .light).impactOccurred()
                            vm.assistLevel = item.code
                        }
                    }
                } header: { Text("보조 수준 (Assist Level)") }

                // ── 수행 평가
                Section {
                    Picker("수행도", selection: $vm.performance) {
                        ForEach(performanceLevels, id: \.self) { Text($0).tag($0) }
                    }
                    .pickerStyle(.segmented)
                } header: { Text("수행 평가") }

                // ── 플래그
                Section {
                    ForEach(flagOptions, id: \.self) { flag in
                        HStack {
                            Image(systemName: vm.selectedFlags.contains(flag)
                                  ? "checkmark.circle.fill" : "circle")
                                .foregroundStyle(vm.selectedFlags.contains(flag) ? .orange : .secondary)
                            Text(flag)
                            Spacer()
                        }
                        .contentShape(Rectangle())
                        .onTapGesture {
                            UIImpactFeedbackGenerator(style: .light).impactOccurred()
                            if vm.selectedFlags.contains(flag) {
                                vm.selectedFlags.remove(flag)
                            } else {
                                vm.selectedFlags.insert(flag)
                            }
                        }
                    }
                } header: { Text("특이사항 플래그") }

                // ── 메모
                Section {
                    TextField("추가 메모 (선택)", text: $vm.notes, axis: .vertical)
                        .lineLimit(3...6)
                } header: { Text("메모") }

                // ── 저장 버튼
                Section {
                    Button {
                        Task { await vm.save() }
                    } label: {
                        HStack {
                            Spacer()
                            if vm.isSaving {
                                ProgressView().tint(.white)
                            } else {
                                Label(vm.savedLabel == nil ? "라벨 저장" : "라벨 업데이트",
                                      systemImage: "tag.fill")
                            }
                            Spacer()
                        }
                    }
                    .disabled(!vm.canSave || vm.isSaving)
                    .listRowBackground(vm.canSave ? Color.blue : Color.gray.opacity(0.3))
                    .foregroundStyle(.white)
                    .fontWeight(.semibold)
                }

                // ── 에러
                if let err = vm.errorMessage {
                    Section {
                        Label(err, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.red)
                            .font(.caption)
                    }
                }

                // ── 기존 라벨 요약
                if let saved = vm.savedLabel, let updatedAt = saved.updated_at {
                    Section {
                        VStack(alignment: .leading, spacing: 4) {
                            Label("마지막 저장: \(formatDate(updatedAt))", systemImage: "clock")
                                .font(.caption2).foregroundStyle(.secondary)
                            if !saved.flags.isEmpty {
                                Text("플래그: " + saved.flags.joined(separator: ", "))
                                    .font(.caption2).foregroundStyle(.orange)
                            }
                        }
                    }
                }
            }
            .navigationTitle("재활 라벨링")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("닫기") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    if vm.saveSuccess {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                            .transition(.scale.combined(with: .opacity))
                    }
                }
            }
            .task { await vm.load() }
            .overlay {
                if vm.isLoading {
                    ProgressView("라벨 불러오는 중...")
                        .padding(20)
                        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
                }
            }
        }
    }

    private func formatDate(_ str: String) -> String {
        let parts = str.components(separatedBy: " ")
        guard parts.count == 2 else { return str }
        let d = parts[0].components(separatedBy: "-")
        let t = parts[1].components(separatedBy: ":").prefix(2).joined(separator: ":")
        guard d.count == 3 else { return str }
        return "\(d[1])/\(d[2]) \(t)"
    }
}
