import SwiftUI

private func dismissLabelKeyboard() {
    UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
}

private struct LabelKeyboardDoneToolbar: ViewModifier {
    func body(content: Content) -> some View {
        content.toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("완료") {
                    dismissLabelKeyboard()
                }
            }
        }
    }
}

private extension View {
    func labelKeyboardDoneToolbar() -> some View {
        modifier(LabelKeyboardDoneToolbar())
    }
}

// MARK: - 옵션 정의

private struct LabelOption: Identifiable {
    let code: String
    let label: String
    var id: String { code }
}

private let sessionTypes: [LabelOption] = [
    .init(code: "assessment", label: "평가"),
    .init(code: "therapeutic_exercise", label: "치료적 운동"),
    .init(code: "neuromotor_training", label: "신경운동 훈련"),
    .init(code: "gait_training", label: "보행 훈련"),
    .init(code: "balance_training", label: "균형 훈련"),
    .init(code: "caregiver_training", label: "보호자 교육"),
    .init(code: "home_exercise_review", label: "홈운동 점검"),
    .init(code: "other", label: "기타")
]
private let coreTasks: [LabelOption] = [
    .init(code: "prone_head_control", label: "엎드린 머리 조절"),
    .init(code: "sitting_balance", label: "앉은 균형"),
    .init(code: "standing_balance", label: "선 균형"),
    .init(code: "gait_practice", label: "보행 연습"),
    .init(code: "sit_to_stand", label: "앉았다 일어서기"),
    .init(code: "reaching", label: "뻗기/도달"),
    .init(code: "range_of_motion", label: "관절가동범위"),
    .init(code: "caregiver_handling", label: "보호자 핸들링"),
    .init(code: "positioning", label: "자세 잡기"),
    .init(code: "other", label: "기타")
]
private let bodyPositions: [LabelOption] = [
    .init(code: "supine", label: "바로누움"),
    .init(code: "prone", label: "엎드림"),
    .init(code: "side_lying", label: "옆으로 누움"),
    .init(code: "sitting", label: "앉기"),
    .init(code: "quadruped", label: "네발기기"),
    .init(code: "kneeling", label: "무릎서기"),
    .init(code: "standing", label: "서기"),
    .init(code: "walking", label: "걷기"),
    .init(code: "unknown", label: "모름")
]
private let assistLevels: [(code: String, label: String)] = [
    ("independent", "독립"),
    ("supervision", "감독"),
    ("standby_assist", "대기 보조"),
    ("contact_guard", "접촉 보조"),
    ("minimal_assist", "최소 보조"),
    ("moderate_assist", "중등도 보조"),
    ("maximal_assist", "최대 보조"),
    ("dependent",   "완전 의존"),
    ("not_tested", "미검사")
]
private let performanceLevels: [LabelOption] = [
    .init(code: "improved", label: "개선"),
    .init(code: "stable", label: "안정"),
    .init(code: "declined", label: "저하"),
    .init(code: "variable", label: "가변"),
    .init(code: "unable", label: "불가"),
    .init(code: "not_observed", label: "관찰 안 됨")
]
private let reviewStatuses: [LabelOption] = [
    .init(code: "unreviewed", label: "미검수"),
    .init(code: "reviewed", label: "검수"),
    .init(code: "corrected", label: "수정"),
    .init(code: "approved", label: "승인"),
    .init(code: "rejected", label: "반려")
]
private let toleranceLevels: [LabelOption] = [
    .init(code: "", label: "미기록"),
    .init(code: "good", label: "좋음"),
    .init(code: "fair", label: "보통"),
    .init(code: "poor", label: "낮음"),
    .init(code: "not_observed", label: "관찰 안 됨")
]
private let fatigueLevels: [LabelOption] = [
    .init(code: "", label: "미기록"),
    .init(code: "none", label: "없음"),
    .init(code: "mild", label: "경도"),
    .init(code: "moderate", label: "중등도"),
    .init(code: "severe", label: "심함"),
    .init(code: "uncertain", label: "불확실")
]
private let flagOptions: [LabelOption] = [
    .init(code: "fatigue", label: "피로"),
    .init(code: "postural_sway", label: "자세 흔들림"),
    .init(code: "pain", label: "통증"),
    .init(code: "caregiver_assist", label: "보호자 보조"),
    .init(code: "safety_risk", label: "안전 위험"),
    .init(code: "low_attention", label: "주의 저하"),
    .init(code: "equipment_used", label: "장비 사용"),
    .init(code: "needs_review", label: "추가 검토")
]
private let compensationOptions: [LabelOption] = [
    .init(code: "right_weight_shift", label: "우측 체중이동"),
    .init(code: "left_weight_shift", label: "좌측 체중이동"),
    .init(code: "trunk_lateral_flexion", label: "몸통 측굴"),
    .init(code: "excessive_extension", label: "과도한 신전"),
    .init(code: "excessive_flexion", label: "과도한 굴곡"),
    .init(code: "shoulder_elevation", label: "어깨 거상"),
    .init(code: "pelvic_rotation", label: "골반 회전")
]

// MARK: - ViewModel

@MainActor
@Observable
final class LabelingViewModel {
    // 입력 필드
    var sessionType: String = sessionTypes[2].code
    var coreTask: String = coreTasks[0].code
    var customTask: String = ""
    var bodyPosition: String = "prone"
    var assistLevel: String = "moderate_assist"
    var performance: String = "stable"
    var reviewStatus: String = "reviewed"
    var reviewerPersonId: String = ""
    var usableForTraining = false
    var labelConfidence: String = ""
    var repetitionCount: String = ""
    var holdDurationSeconds: String = ""
    var tolerance: String = ""
    var fatigueLevel: String = ""
    var selectedFlags: Set<String> = []
    var selectedCompensations: Set<String> = []
    var caregiverPresent = false
    var notes: String = ""

    // 상태
    var isLoading = false
    var isSaving = false
    var isCheckingPilot = false
    var savedLabel: BridgeClient.RehabLabel? = nil
    var pilotReadiness: BridgeClient.PilotReadiness?
    var writePlanSummary: BridgeClient.MoaiWritePlanSummary?
    var errorMessage: String? = nil
    var pilotErrorMessage: String? = nil
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
                await refreshPilotDryRun()
            }
        } catch {
            errorMessage = UserFacingError.message(for: error)
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
                customTask: customTask,
                bodyPosition: bodyPosition,
                assistLevel: assistLevel,
                performance: performance,
                reviewStatus: reviewStatus,
                reviewerPersonId: reviewerPersonId,
                usableForTraining: usableForTraining,
                labelConfidence: Double(labelConfidence),
                repetitionCount: Int(repetitionCount),
                holdDurationSeconds: Double(holdDurationSeconds),
                tolerance: tolerance,
                fatigueLevel: fatigueLevel,
                compensations: Array(selectedCompensations).sorted(),
                caregiverPresent: caregiverPresent,
                flags: Array(selectedFlags).sorted(),
                notes: notes
            )
            savedLabel = label
            await refreshPilotDryRun()
            saveSuccess = true
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        } catch {
            errorMessage = UserFacingError.message(for: error)
            UINotificationFeedbackGenerator().notificationOccurred(.error)
        }
        isSaving = false
    }

    private func apply(_ label: BridgeClient.RehabLabel) {
        sessionType   = label.session_type
        coreTask      = label.core_task
        customTask    = label.custom_task ?? ""
        bodyPosition  = label.body_position ?? "unknown"
        assistLevel   = label.assist_level
        performance   = label.performance_level ?? label.performance
        reviewStatus  = label.review_status ?? "reviewed"
        reviewerPersonId = label.reviewer_person_id ?? ""
        usableForTraining = label.usable_for_training ?? false
        labelConfidence = label.label_confidence.map { String($0) } ?? ""
        repetitionCount = label.repetition_count.map { String($0) } ?? ""
        holdDurationSeconds = label.hold_duration_seconds.map { String($0) } ?? ""
        tolerance = label.tolerance ?? ""
        fatigueLevel = label.fatigue_level ?? ""
        selectedCompensations = Set(label.compensations ?? [])
        caregiverPresent = label.caregiver_present ?? false
        selectedFlags = Set(label.flags)
        notes         = label.notes
    }

    var canSave: Bool {
        if sessionType.isEmpty || coreTask.trimmingCharacters(in: .whitespaces).isEmpty {
            return false
        }
        if coreTask == "other" && customTask.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return false
        }
        return true
    }

    func applySupportedKneelingPreset() {
        sessionType = "neuromotor_training"
        coreTask = "other"
        customTask = "supported_kneeling"
        bodyPosition = "kneeling"
        assistLevel = "minimal_assist"
        performance = "stable"
        reviewStatus = "reviewed"
        selectedFlags.formUnion(["caregiver_assist", "needs_review"])
        if notes.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            notes = "Supported kneeling custom task candidate. Keep as core_task=other until taxonomy review."
        }
    }

    func refreshPilotDryRun() async {
        isCheckingPilot = true
        pilotErrorMessage = nil
        do {
            async let readiness = client.pilotReadiness(eventId: eventId, resolveIdentity: false)
            async let writePlan = client.moaiWritePlan(eventId: eventId, resolveIdentity: false)
            let result = try await (readiness, writePlan)
            pilotReadiness = result.0.readiness
            writePlanSummary = result.1.result.summary
        } catch {
            pilotErrorMessage = UserFacingError.message(for: error)
        }
        isCheckingPilot = false
    }
}

// MARK: - LabelingView (Sheet)

struct LabelingView: View {
    let eventId: String
    let client: BridgeClient
    @Environment(\.dismiss) private var dismiss
    @State private var vm: LabelingViewModel
    @State private var showCoreTaskHint = false   // 빈 채로 저장 시도 시 강조

    init(eventId: String, client: BridgeClient) {
        self.eventId = eventId
        self.client = client
        self._vm = State(wrappedValue: LabelingViewModel(eventId: eventId, client: client))
    }

    var body: some View {
        NavigationStack {
            ZStack(alignment: .top) {
                Form {
                    // ── 세션 유형
                    Section {
                        Picker("세션 유형", selection: $vm.sessionType) {
                            ForEach(sessionTypes) { option in
                                Text(option.label).tag(option.code)
                            }
                        }
                    } header: { Text("세션 유형") }

                    // ── 핵심 과제 (필수)
                    Section {
                        Picker("핵심 과제", selection: $vm.coreTask) {
                            ForEach(coreTasks) { option in
                                Text(option.label).tag(option.code)
                            }
                        }
                            .accessibilityIdentifier("labelCoreTaskInput")
                            .onChange(of: vm.coreTask) { _, _ in
                                if showCoreTaskHint { showCoreTaskHint = false }
                            }

                        if vm.coreTask == "other" {
                            TextField("예: supported_kneeling", text: $vm.customTask)
                                .textInputAutocapitalization(.never)
                                .autocorrectionDisabled()
                        }

                        Picker("몸 위치", selection: $vm.bodyPosition) {
                            ForEach(bodyPositions) { option in
                                Text(option.label).tag(option.code)
                            }
                        }

                        Button {
                            UIImpactFeedbackGenerator(style: .light).impactOccurred()
                            vm.applySupportedKneelingPreset()
                        } label: {
                            Label("Supported kneeling 프리셋", systemImage: "figure.strengthtraining.functional")
                        }
                    } header: {
                        HStack {
                            Text("핵심 과제")
                            Text("필수").font(.caption2).foregroundStyle(.orange)
                                .padding(.horizontal, 6).padding(.vertical, 2)
                                .background(Color.orange.opacity(0.15), in: Capsule())
                        }
                    } footer: {
                        if showCoreTaskHint && !vm.canSave {
                            Text("핵심 과제를 선택하고, 기타 과제는 custom_task까지 입력해야 저장할 수 있어요.")
                                .foregroundStyle(.red)
                                .font(.caption)
                        }
                    }

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
                            ForEach(performanceLevels) { option in
                                Text(option.label).tag(option.code)
                            }
                        }
                        .pickerStyle(.segmented)
                    } header: { Text("수행 평가") }

                    Section {
                        Picker("리뷰 상태", selection: $vm.reviewStatus) {
                            ForEach(reviewStatuses) { option in
                                Text(option.label).tag(option.code)
                            }
                        }
                        TextField("reviewer_person_id", text: $vm.reviewerPersonId)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        Toggle("학습/eval 데이터로 명시 승인", isOn: $vm.usableForTraining)
                    } header: {
                        Text("리뷰와 학습 게이트")
                    } footer: {
                        Text("이 토글을 켜야 gold dataset 후보가 됩니다. 임상 검수 없이 켜지 않는 것을 권장합니다.")
                    }

                    Section {
                        TextField("label_confidence 0-1", text: $vm.labelConfidence)
                            .keyboardType(.decimalPad)
                        TextField("반복 횟수", text: $vm.repetitionCount)
                            .keyboardType(.numberPad)
                        TextField("유지 시간 초", text: $vm.holdDurationSeconds)
                            .keyboardType(.decimalPad)
                        Picker("Tolerance", selection: $vm.tolerance) {
                            ForEach(toleranceLevels) { option in
                                Text(option.label).tag(option.code)
                            }
                        }
                        Picker("피로도", selection: $vm.fatigueLevel) {
                            ForEach(fatigueLevels) { option in
                                Text(option.label).tag(option.code)
                            }
                        }
                        Toggle("보호자 참여", isOn: $vm.caregiverPresent)
                    } header: {
                        Text("측정값")
                    }

                    // ── 플래그
                    Section {
                        ForEach(flagOptions) { flag in
                            HStack {
                                Image(systemName: vm.selectedFlags.contains(flag.code)
                                      ? "checkmark.circle.fill" : "circle")
                                    .foregroundStyle(vm.selectedFlags.contains(flag.code) ? .orange : .secondary)
                                Text(flag.label)
                                Spacer()
                            }
                            .contentShape(Rectangle())
                            .onTapGesture {
                                UIImpactFeedbackGenerator(style: .light).impactOccurred()
                                if vm.selectedFlags.contains(flag.code) {
                                    vm.selectedFlags.remove(flag.code)
                                } else {
                                    vm.selectedFlags.insert(flag.code)
                                }
                            }
                        }
                    } header: { Text("특이사항 플래그") }

                    Section {
                        ForEach(compensationOptions) { item in
                            HStack {
                                Image(systemName: vm.selectedCompensations.contains(item.code)
                                      ? "checkmark.circle.fill" : "circle")
                                    .foregroundStyle(vm.selectedCompensations.contains(item.code) ? .teal : .secondary)
                                Text(item.label)
                                Spacer()
                            }
                            .contentShape(Rectangle())
                            .onTapGesture {
                                UIImpactFeedbackGenerator(style: .light).impactOccurred()
                                if vm.selectedCompensations.contains(item.code) {
                                    vm.selectedCompensations.remove(item.code)
                                } else {
                                    vm.selectedCompensations.insert(item.code)
                                }
                            }
                        }
                    } header: { Text("보상 패턴") }


                    // ── 메모
                    Section {
                        TextField("추가 메모 (선택)", text: $vm.notes, axis: .vertical)
                            .lineLimit(3...6)
                    } header: { Text("메모") }

                    Section {
                        if vm.isCheckingPilot {
                            HStack {
                                ProgressView()
                                Text("Pilot readiness 확인 중...")
                                    .foregroundStyle(.secondary)
                            }
                        } else if let readiness = vm.pilotReadiness {
                            PilotGateSummary(
                                readiness: readiness,
                                writePlanSummary: vm.writePlanSummary
                            )
                        } else {
                            Text("라벨 저장 후 readiness와 moai dry-run 요약이 표시됩니다.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }

                        if let pilotError = vm.pilotErrorMessage {
                            Label(pilotError, systemImage: "exclamationmark.triangle.fill")
                                .foregroundStyle(.orange)
                                .font(.caption)
                        }

                        Button {
                            Task { await vm.refreshPilotDryRun() }
                        } label: {
                            Label("Readiness / Dry-run 새로고침", systemImage: "arrow.clockwise")
                        }
                        .disabled(vm.isCheckingPilot)
                    } header: {
                        Text("Pilot gate")
                    }

                    // ── 저장 버튼
                    Section {
                        Button {
                            dismissLabelKeyboard()
                            if !vm.canSave {
                                // 핵심 과제 비어있음 — 힌트 표시
                                withAnimation { showCoreTaskHint = true }
                                UINotificationFeedbackGenerator().notificationOccurred(.warning)
                                return
                            }
                            Task { await vm.save() }
                        } label: {
                            HStack {
                                Spacer()
                                if vm.isSaving {
                                    ProgressView().tint(.white)
                                    Text("저장 중...").foregroundStyle(.white)
                                } else {
                                    Label(vm.savedLabel == nil ? "라벨 저장" : "라벨 업데이트",
                                          systemImage: "tag.fill")
                                        .foregroundStyle(.white)
                                }
                                Spacer()
                            }
                        }
                        .disabled(vm.isSaving)
                        .listRowBackground(vm.isSaving ? Color.gray.opacity(0.5) : Color.blue)
                        .fontWeight(.semibold)
                        .accessibilityIdentifier("labelSaveButton")
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
                                if let customTask = saved.custom_task, !customTask.isEmpty {
                                    Text("Custom task: \(customTask)")
                                        .font(.caption2).foregroundStyle(.teal)
                                }
                                if let bodyPosition = saved.body_position, !bodyPosition.isEmpty {
                                    Text("Body position: \(bodyPosition)")
                                        .font(.caption2).foregroundStyle(.secondary)
                                }
                                if saved.usable_for_training == true {
                                    Text("Training/eval 후보로 명시 승인됨")
                                        .font(.caption2).foregroundStyle(.green)
                                }
                            }
                        }
                    }
                }
                .scrollDismissesKeyboard(.interactively)
                .labelKeyboardDoneToolbar()

                // ── 성공 배너 (상단 오버레이)
                if vm.saveSuccess {
                    SaveSuccessBanner {
                        withAnimation { vm.saveSuccess = false }
                        dismiss()
                    }
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .zIndex(1)
                }
            }
            .navigationTitle("재활 라벨링")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("닫기") { dismiss() }
                }
            }
            .animation(.spring(response: 0.4), value: vm.saveSuccess)
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

// MARK: - 성공 배너

private struct PilotGateSummary: View {
    let readiness: BridgeClient.PilotReadiness
    let writePlanSummary: BridgeClient.MoaiWritePlanSummary?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                gatePill(
                    title: "Schema",
                    isReady: readiness.usable_for_schema_eval,
                    readyText: "ready",
                    blockedText: "blocked"
                )
                gatePill(
                    title: "Gold",
                    isReady: readiness.eligible_for_gold_dataset,
                    readyText: "ready",
                    blockedText: "blocked"
                )
            }

            if let writePlanSummary {
                Label(
                    "\(writePlanSummary.operation_count) operations · \(writePlanSummary.skipped_count) skipped",
                    systemImage: writePlanSummary.skipped_count == 0 ? "checkmark.seal.fill" : "exclamationmark.triangle.fill"
                )
                .font(.caption)
                .foregroundStyle(writePlanSummary.skipped_count == 0 ? .green : .orange)
            }

            if !readiness.missing_requirements.isEmpty {
                Text("Schema missing: \(readiness.missing_requirements.joined(separator: ", "))")
                    .font(.caption2)
                    .foregroundStyle(.orange)
            }

            if !readiness.gold_missing_requirements.isEmpty {
                Text("Gold missing: \(readiness.gold_missing_requirements.joined(separator: ", "))")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func gatePill(title: String, isReady: Bool, readyText: String, blockedText: String) -> some View {
        Text("\(title) \(isReady ? readyText : blockedText)")
            .font(.caption.weight(.semibold))
            .foregroundStyle(isReady ? .green : .orange)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background((isReady ? Color.green : Color.orange).opacity(0.12), in: Capsule())
    }
}

private struct SaveSuccessBanner: View {
    let onDismiss: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 22))
                    .foregroundStyle(.green)
                VStack(alignment: .leading, spacing: 2) {
                    Text("라벨 저장 완료!")
                        .font(.subheadline).fontWeight(.semibold)
                    Text("탭하면 닫힙니다")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button {
                    onDismiss()
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(.regularMaterial)
            Divider()
        }
        .onTapGesture { onDismiss() }
    }
}
