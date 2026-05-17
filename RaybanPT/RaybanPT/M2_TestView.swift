import SwiftUI
import Foundation
import UniformTypeIdentifiers
import MWDATCore

private func dismissKeyboard() {
    UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
}

private struct KeyboardDoneToolbar: ViewModifier {
    func body(content: Content) -> some View {
        content.toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("완료") {
                    dismissKeyboard()
                }
            }
        }
    }
}

private extension View {
    func keyboardDoneToolbar() -> some View {
        modifier(KeyboardDoneToolbar())
    }
}

struct M2_TestView: View {
    @StateObject private var vm: AdapterViewModel
    @Environment(DeviceSessionManager.self) private var deviceManager
    @State private var selectedTab: Tab = .camera
    @State private var showServerSetup = false

    // 업로드 완료 액션
    @State private var showPostUploadDialog = false
    @State private var showPostLabelSheet = false
    @State private var showPostChartSheet = false

    // 미라벨 뱃지
    @State private var unlabeledBadge = 0

    enum Tab { case audio, text, camera, charts }

    static var defaultBridgeURL: URL {
        let stored = UserDefaults.standard.string(forKey: "bridge_base_url") ?? ""
        return URL(string: stored) ?? URL(string: "http://localhost:8791")!
    }

    static var defaultAPIKey: String {
        UserDefaults.standard.string(forKey: "bridge_api_key") ?? ""
    }

    init(baseURL: URL = M2_TestView.defaultBridgeURL, apiKey: String = M2_TestView.defaultAPIKey) {
        _vm = StateObject(wrappedValue: AdapterViewModel(client: BridgeClient(baseURL: baseURL, apiKey: apiKey)))
    }

    /// 서버 URL 미설정 시 첫 실행에 setup sheet 자동 표시
    var needsServerSetup: Bool {
        let stored = UserDefaults.standard.string(forKey: "bridge_base_url") ?? ""
        return stored.isEmpty
    }

    var body: some View {
        TabView(selection: $selectedTab) {
            // 카메라 탭
            NavigationStack {
                StreamView(client: vm.client)
            }
            .tabItem { Label("카메라", systemImage: "video.fill") }
            .tag(Tab.camera)

            // 음성 탭
            NavigationStack {
                AudioTab(vm: vm)
            }
            .tabItem { Label("음성", systemImage: "mic.fill") }
            .tag(Tab.audio)

            // 텍스트 탭
            NavigationStack {
                TextTab(vm: vm)
            }
            .tabItem { Label("텍스트", systemImage: "text.bubble.fill") }
            .tag(Tab.text)

            // 차트 탭
            ChartListView(client: vm.client)
            .tabItem { Label("차트", systemImage: "doc.text.fill") }
            .tag(Tab.charts)
            .badge(unlabeledBadge > 0 ? unlabeledBadge : 0)
        }
        .tint(DS.ColorToken.primary)
        .overlay(alignment: .top) {
            if selectedTab == .camera {
                DeviceStatusBanner(deviceManager: deviceManager)
                    .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .overlay(alignment: .bottomTrailing) {
            // 서버 설정 버튼 (항상 접근 가능)
            Button {
                showServerSetup = true
            } label: {
                ServerSettingsButton(needsSetup: needsServerSetup)
            }
            .accessibilityIdentifier("serverSettingsButton")
            .padding(.bottom, 68)
            .padding(.trailing, 16)
        }
        // MARK: 업로드 완료 다이얼로그
        .confirmationDialog("차트가 생성됐어요", isPresented: $showPostUploadDialog, titleVisibility: .visible) {
            Button("지금 라벨링하기") {
                showPostLabelSheet = true
            }
            Button("차트 보기") {
                showPostChartSheet = true
            }
            Button("나중에", role: .cancel) { }
        }
        .sheet(isPresented: $showPostLabelSheet) {
            if let id = vm.lastEventId {
                LabelingView(eventId: id, client: vm.client)
            }
        }
        .sheet(isPresented: $showPostChartSheet) {
            if let id = vm.lastEventId {
                NavigationStack {
                    ChartDetailView(eventId: id, client: vm.client)
                }
            }
        }
        // 업로드 완료 감지
        .onChange(of: vm.state) { _, newState in
            if case .done = newState, vm.lastEventId != nil {
                showPostUploadDialog = true
            }
        }
        // 차트 탭에서 다른 탭으로 나올 때만 뱃지 갱신 (진입 시 X)
        .onChange(of: selectedTab) { oldTab, _ in
            if oldTab == .charts { Task { await refreshBadge() } }
        }
        .sheet(isPresented: $showServerSetup) {
            ServerSetupSheet(client: vm.client) { newURL, newAPIKey, newOrgId, newProviderPersonId in
                UserDefaults.standard.set(newURL, forKey: "bridge_base_url")
                UserDefaults.standard.set(newAPIKey, forKey: "bridge_api_key")
                UserDefaults.standard.set(newOrgId, forKey: "glasspt_owner_org_id")
                UserDefaults.standard.set(newProviderPersonId, forKey: "glasspt_owner_provider_person_id")
                vm.client.updateBaseURL(URL(string: newURL)!)
                vm.client.updateAPIKey(newAPIKey)
                vm.client.updateOwnerScope(orgId: newOrgId, providerPersonId: newProviderPersonId)
                NotificationCenter.default.post(name: Notification.Name("bridgeSettingsDidChange"), object: nil)
                Task { await refreshBadge() }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("bridgeSettingsDidChange"))) { _ in
            let orgId = UserDefaults.standard.string(forKey: "glasspt_owner_org_id") ?? ""
            let providerPersonId = UserDefaults.standard.string(forKey: "glasspt_owner_provider_person_id") ?? ""
            vm.client.updateOwnerScope(orgId: orgId, providerPersonId: providerPersonId)
        }
        .task { await refreshBadge() }
        .onAppear {
            if needsServerSetup { showServerSetup = true }
        }
    }

    // MARK: - 미라벨 뱃지 갱신
    private func refreshBadge() async {
        guard let events = try? await vm.client.recentEvents(limit: 50) else { return }
        unlabeledBadge = events.filter { !$0.has_label }.count
    }
}

// MARK: - 기기 E2E 점검

private struct ServerSettingsButton: View {
    let needsSetup: Bool

    var body: some View {
        HStack(spacing: 7) {
            ZStack(alignment: .topTrailing) {
                Image(systemName: "server.rack")
                    .font(.system(size: 13, weight: .semibold))
                Circle()
                    .fill(needsSetup ? DS.ColorToken.warning : DS.ColorToken.success)
                    .frame(width: 7, height: 7)
                    .offset(x: 4, y: -4)
            }
            Text("서버")
                .font(.system(size: 12, weight: .semibold))
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(DS.ColorToken.surface, in: Capsule())
        .overlay {
            Capsule()
                .stroke(needsSetup ? DS.ColorToken.warning.opacity(0.45) : Color.white.opacity(0.12), lineWidth: 1)
        }
        .shadow(color: .black.opacity(0.18), radius: 8, y: 3)
        .accessibilityLabel(needsSetup ? "서버 설정 필요" : "서버 설정")
    }
}

private struct CheckupTab: View {
    let client: BridgeClient
    @Environment(DeviceSessionManager.self) private var deviceManager
    @AppStorage("raybanpt_e2e_checklist_ids") private var storedCompletedIDs = ""
    @State private var completedIDs: Set<String> = []
    @State private var checkMessages: [String: String] = [:]
    @State private var health: BridgeHealthResponse?
    @State private var healthMessage = "아직 확인 전"
    @State private var isCheckingHealth = false
    @State private var isRunningFullCheck = false
    @State private var lastCheckedAt = ""

    private let items: [CheckupItem] = [
        .init(id: "bridge", icon: "network", title: "브리지 연결", detail: "서버 응답과 DB 상태 확인"),
        .init(id: "security", icon: "lock.shield", title: "보안 설정", detail: "API 키, 동의, 다운로드 차단 확인"),
        .init(id: "patient", icon: "person.crop.circle.badge.checkmark", title: "환자 연결", detail: "음성/카메라 기록의 환자 이름 일치"),
        .init(id: "audio", icon: "mic.fill", title: "음성 처리", detail: "최신 음성 기록 processed 확인"),
        .init(id: "camera", icon: "camera.viewfinder", title: "카메라 처리", detail: "최신 사진 또는 영상 processed 확인"),
        .init(id: "masking", icon: "face.dashed", title: "마스킹 성공", detail: "얼굴 마스킹 성공 기록 확인"),
        .init(id: "merge", icon: "link.circle.fill", title: "통합 차트", detail: "최신 combined 이벤트 확인"),
        .init(id: "chart", icon: "doc.text.magnifyingglass", title: "차트 품질", detail: "기술문구/STT 노이즈/자동 기본값 확인"),
        .init(id: "label", icon: "tag.fill", title: "라벨링", detail: "통합 차트 라벨 저장 확인"),
        .init(id: "audit", icon: "list.bullet.clipboard", title: "감사 로그", detail: "최근 60분 오류 없음 확인"),
    ]

    private var completedCount: Int {
        items.filter { completedIDs.contains($0.id) }.count
    }

    private var bridgeStatusText: String {
        if isCheckingHealth || isRunningFullCheck { return "확인 중" }
        guard let health else { return "대기" }
        return health.ok && health.db.ok ? "정상" : "확인 필요"
    }

    var body: some View {
        Form {
            Section {
                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        Label("브리지", systemImage: "server.rack")
                            .font(.headline)
                        Spacer()
                        StatusPill(text: bridgeStatusText, ok: health?.ok == true && health?.db.ok == true)
                    }

                    Text(client.baseURL.absoluteString)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                        .textSelection(.enabled)

                    Button {
                        Task { await runHealthCheck() }
                    } label: {
                        Label(isCheckingHealth ? "확인 중" : "연결 확인", systemImage: "arrow.clockwise")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(isCheckingHealth || isRunningFullCheck)

                    Text(healthMessage)
                        .font(.caption)
                        .foregroundStyle(health?.ok == true ? Color.secondary : DS.ColorToken.warning)

                    if let health {
                        VStack(spacing: 8) {
                            HealthLine(title: "DB", detail: health.db.ok ? "정상" : (health.db.error ?? "오류"), ok: health.db.ok)
                            HealthLine(title: "API 키", detail: client.apiKey.isEmpty ? "앱 키 없음" : "앱 키 입력됨", ok: health.security.api_key_configured && !client.apiKey.isEmpty)
                            HealthLine(title: "환자 동의", detail: health.security.patient_consent_required ? "필수" : "꺼짐", ok: health.security.patient_consent_required)
                            HealthLine(title: "원본 다운로드", detail: health.security.file_downloads_enabled ? "켜짐" : "차단", ok: !health.security.file_downloads_enabled)
                            HealthLine(title: "비마스킹 저장", detail: health.security.allow_unmasked_image ? "허용" : "차단", ok: !health.security.allow_unmasked_image)
                        }
                        .padding(.top, 4)
                    }
                }
                .padding(.vertical, 4)
            } header: {
                Text("서버")
            }

            Section {
                HStack {
                    Label("진행률", systemImage: "chart.bar.fill")
                    Spacer()
                    Text("\(completedCount)/\(items.count)")
                        .fontWeight(.semibold)
                }
                ProgressView(value: Double(completedCount), total: Double(items.count))

                Button {
                    Task { await runFullCheck() }
                } label: {
                    HStack {
                        Spacer()
                        if isRunningFullCheck {
                            ProgressView()
                        } else {
                            Image(systemName: "checklist.checked")
                        }
                        Text(isRunningFullCheck ? "전체 점검 중..." : "전체 점검 실행")
                        Spacer()
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(isCheckingHealth || isRunningFullCheck)

                if !lastCheckedAt.isEmpty {
                    Label("마지막 점검 \(lastCheckedAt)", systemImage: "clock")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section {
                ForEach(items) { item in
                    CheckupRow(
                        item: item,
                        detail: checkMessages[item.id] ?? item.detail,
                        isOn: Binding(
                            get: { completedIDs.contains(item.id) },
                            set: { setCompleted(item.id, to: $0) }
                        )
                    )
                }
            } header: {
                Text("기기 E2E")
            }

            Section {
                HStack {
                    Label(deviceManager.statusMessage, systemImage: deviceManager.linkState == .connected ? "antenna.radiowaves.left.and.right" : "antenna.radiowaves.left.and.right.slash")
                        .foregroundStyle(deviceManager.linkState == .connected ? DS.ColorToken.success : DS.ColorToken.warning)
                    Spacer()
                    Button("재연결") {
                        deviceManager.retryConnection()
                    }
                    .buttonStyle(.bordered)
                }
            } header: {
                Text("기기 상태")
            }

            Section {
                Button("체크 초기화", role: .destructive) {
                    completedIDs.removeAll()
                    persistCompletedIDs()
                }
            }
        }
        .navigationTitle("기기 점검")
        .navigationBarTitleDisplayMode(.large)
        .onAppear {
            loadCompletedIDs()
        }
    }

    private func runHealthCheck() async {
        isCheckingHealth = true
        defer { isCheckingHealth = false }

        do {
            let response = try await client.health()
            health = response
            let serverOK = response.ok && response.db.ok
            var protectedAPIOK = !response.security.api_key_configured
            if response.security.api_key_configured && !client.apiKey.isEmpty {
                do {
                    _ = try await client.recentEvents(limit: 1)
                    protectedAPIOK = true
                } catch {
                    protectedAPIOK = false
                    healthMessage = UserFacingError.message(for: error)
                }
            }

            let securityOK = response.security.api_key_configured
                && !client.apiKey.isEmpty
                && protectedAPIOK
                && response.security.require_api_key
                && response.security.patient_consent_required
                && !response.security.file_downloads_enabled
                && !response.security.allow_unmasked_image

            setCompleted("bridge", to: serverOK)
            setCompleted("security", to: securityOK)

            if serverOK && securityOK {
                healthMessage = "서버와 보안 설정이 준비됐습니다."
            } else if response.security.api_key_configured && !protectedAPIOK {
                healthMessage = client.apiKey.isEmpty
                    ? "서버 API 키를 앱에 입력하고 저장하세요."
                    : "API 키가 맞지 않습니다. 서버 설정에서 다시 입력하고 저장하세요."
            } else if serverOK {
                healthMessage = "서버는 연결됐고, 보안 설정을 확인하세요."
            } else {
                healthMessage = "서버 응답 또는 DB 상태를 확인하세요."
            }
        } catch {
            health = nil
            setCompleted("bridge", to: false)
            healthMessage = UserFacingError.message(for: error)
        }
    }

    private func runFullCheck() async {
        isRunningFullCheck = true
        isCheckingHealth = true
        healthMessage = "전체 점검을 실행 중입니다."
        checkMessages = Dictionary(uniqueKeysWithValues: items.map { ($0.id, "점검 대기") })
        completedIDs.removeAll()
        persistCompletedIDs()

        defer {
            isCheckingHealth = false
            isRunningFullCheck = false
            lastCheckedAt = Date.now.formatted(date: .omitted, time: .shortened)
        }

        do {
            let response = try await client.health()
            health = response

            let serverOK = response.ok && response.db.ok
            setCheck("bridge", ok: serverOK, detail: serverOK ? "서버/DB 정상" : "서버 또는 DB 상태 확인 필요")

            var protectedAPIOK = !response.security.api_key_configured
            if response.security.api_key_configured && !client.apiKey.isEmpty {
                do {
                    _ = try await client.recentEvents(limit: 1)
                    protectedAPIOK = true
                } catch {
                    protectedAPIOK = false
                }
            }

            let securityOK = response.security.api_key_configured
                && !client.apiKey.isEmpty
                && protectedAPIOK
                && response.security.require_api_key
                && response.security.patient_consent_required
                && !response.security.file_downloads_enabled
                && !response.security.allow_unmasked_image
            setCheck("security", ok: securityOK, detail: securityOK ? "API 키/동의/다운로드 차단 정상" : "API 키 또는 보안 설정 확인 필요")

            let events = try await client.recentEvents(limit: 50)
            let latestAudio = events.first { $0.event_type == "audio" && $0.status == "processed" }
            let latestCamera = events.first { ($0.event_type == "image" || $0.event_type == "video") && $0.status == "processed" }
            let latestCombined = events.first { $0.event_type == "combined" && $0.status == "processed" }

            let patientNames = [latestAudio?.patient_name, latestCamera?.patient_name]
                .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
            let patientOK = !patientNames.isEmpty && Set(patientNames).count == 1
            setCheck("patient", ok: patientOK, detail: patientOK ? "\(patientNames[0]) 환자로 음성/카메라 연결됨" : "같은 환자의 음성+카메라 기록 필요")

            setCheck("audio", ok: latestAudio != nil, detail: latestAudio.map { "음성 \(shortID($0.id)) 처리 완료" } ?? "processed 음성 기록 없음")
            setCheck("camera", ok: latestCamera != nil, detail: latestCamera.map { "\(mediaLabel($0.event_type)) \(shortID($0.id)) 처리 완료" } ?? "processed 카메라/영상 기록 없음")

            if let camera = latestCamera {
                let eventDetail = try? await client.getEvent(camera.id)
                let rawText = eventDetail?.result?.event?.raw_text ?? ""
                let logs = (try? await client.auditLogs(limit: 30, eventId: camera.id)) ?? []
                let maskingText = ([rawText] + logs.map(\.message)).joined(separator: "\n")
                let maskingOK = maskingText.contains("[마스킹 완료]")
                    || maskingText.contains("masking completed")
                    || (camera.event_type == "video" && maskingText.contains("명 감지") && !maskingText.contains("0명 감지"))
                setCheck("masking", ok: maskingOK, detail: maskingOK ? "마스킹 성공 기록 확인" : "마스킹 성공 로그를 찾지 못함")
            } else {
                setCheck("masking", ok: false, detail: "카메라 기록이 필요합니다")
            }

            if let combined = latestCombined {
                setCheck("merge", ok: true, detail: "통합 차트 \(shortID(combined.id)) 생성됨")

                if let chart = try? await client.fetchChart(eventId: combined.id) {
                    let clean = !containsOperationalMaskingText(chart.chart)
                    let qualityOK = chart.quality.map { $0.level == "good" } ?? true
                    let chartOK = clean && qualityOK
                    let detail = clean
                        ? chartQualityDetail(chart.quality)
                        : "차트 본문에 마스킹 기술문구 남음"
                    setCheck("chart", ok: chartOK, detail: detail)
                } else {
                    setCheck("chart", ok: false, detail: "통합 차트 본문 조회 실패")
                }

                let label = try? await client.fetchLabel(eventId: combined.id)
                let labelOK = combined.has_label || label != nil
                setCheck("label", ok: labelOK, detail: labelOK ? "통합 차트 라벨 저장됨" : "통합 차트 라벨링 필요")
            } else {
                setCheck("merge", ok: false, detail: "통합 차트가 아직 없습니다")
                setCheck("chart", ok: false, detail: "통합 차트 생성 후 확인 가능")
                setCheck("label", ok: false, detail: "통합 차트 라벨링 필요")
            }

            let recentErrors = response.recent_error_logs_60m ?? 0
            setCheck("audit", ok: recentErrors == 0, detail: recentErrors == 0 ? "최근 60분 오류 로그 없음" : "최근 60분 오류 \(recentErrors)건")

            healthMessage = completedCount == items.count
                ? "전체 점검을 통과했습니다."
                : "\(completedCount)/\(items.count)개 통과. 미완료 항목을 확인하세요."
        } catch {
            health = nil
            setCheck("bridge", ok: false, detail: UserFacingError.message(for: error))
            healthMessage = UserFacingError.message(for: error)
        }
    }

    private func loadCompletedIDs() {
        completedIDs = Set(
            storedCompletedIDs
                .split(separator: ",")
                .map { String($0) }
        )
    }

    private func setCompleted(_ id: String, to isCompleted: Bool) {
        if isCompleted {
            completedIDs.insert(id)
        } else {
            completedIDs.remove(id)
        }
        persistCompletedIDs()
    }

    private func setCheck(_ id: String, ok: Bool, detail: String) {
        checkMessages[id] = detail
        setCompleted(id, to: ok)
    }

    private func shortID(_ id: String) -> String {
        String(id.prefix(8))
    }

    private func mediaLabel(_ eventType: String) -> String {
        eventType == "video" ? "영상" : "카메라"
    }

    private func containsOperationalMaskingText(_ text: String) -> Bool {
        let tokens = ["[마스킹", "detector=", "segmenter=", "masked.jpg", "파일="]
        return tokens.contains { text.contains($0) }
    }

    private func chartQualityDetail(_ quality: ChartQuality?) -> String {
        guard let quality else {
            return "차트 본문 기술문구 없음"
        }

        let status: String
        switch quality.level {
        case "good":
            status = "품질 좋음"
        case "needs_edit":
            status = "수정 필요"
        default:
            status = "검수 권장"
        }

        if let firstIssue = quality.issues.first {
            return "\(status) \(quality.score)점 · \(firstIssue.message)"
        }
        return "\(status) \(quality.score)점"
    }

    private func persistCompletedIDs() {
        storedCompletedIDs = items
            .map(\.id)
            .filter { completedIDs.contains($0) }
            .joined(separator: ",")
    }
}

private struct CheckupItem: Identifiable {
    let id: String
    let icon: String
    let title: String
    let detail: String
}

private struct CheckupRow: View {
    let item: CheckupItem
    let detail: String
    @Binding var isOn: Bool

    var body: some View {
        Toggle(isOn: $isOn) {
            Label {
                VStack(alignment: .leading, spacing: 3) {
                    Text(item.title)
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } icon: {
                Image(systemName: item.icon)
                    .foregroundStyle(isOn ? DS.ColorToken.success : .secondary)
            }
        }
    }
}

private struct StatusPill: View {
    let text: String
    let ok: Bool

    var body: some View {
        Text(text)
            .font(.caption2.weight(.bold))
            .foregroundStyle(ok ? DS.ColorToken.success : DS.ColorToken.warning)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background((ok ? DS.ColorToken.success : DS.ColorToken.warning).opacity(0.12), in: Capsule())
    }
}

private struct HealthLine: View {
    let title: String
    let detail: String
    let ok: Bool

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .foregroundStyle(ok ? DS.ColorToken.success : DS.ColorToken.warning)
                .frame(width: 18)
            Text(title)
            Spacer()
            Text(detail)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
        .font(.caption)
    }
}

// MARK: - 서버 설정 Sheet

private struct ServerSetupSheet: View {
    let client: BridgeClient
    let onSave: (String, String, String, String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var urlText: String = UserDefaults.standard.string(forKey: "bridge_base_url") ?? ""
    @State private var apiKeyText: String = UserDefaults.standard.string(forKey: "bridge_api_key") ?? ""
    @State private var ownerOrgIdText: String = UserDefaults.standard.string(forKey: "glasspt_owner_org_id") ?? ""
    @State private var ownerProviderPersonIdText: String = UserDefaults.standard.string(forKey: "glasspt_owner_provider_person_id") ?? ""
    @State private var isCheckingConnection = false
    @State private var connectionMessage = ""
    @State private var connectionOK = false

    var isValid: Bool {
        URL(string: urlText)?.scheme?.hasPrefix("http") == true
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    NavigationLink {
                        CheckupTab(client: client)
                    } label: {
                        Label("기기 점검 열기", systemImage: "checklist.checked")
                    }
                } footer: {
                    Text("현장 테스트 전 브리지, 보안, 마스킹, 차트 품질을 여기에서 확인합니다.")
                        .font(.caption)
                }

                Section {
                    TextField("http://서버주소:8791", text: $urlText)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                } header: {
                    Text("서버 URL")
                } footer: {
                    Text("Tailscale 연결 시 예시:\nhttp://desktop-xxxxx.tailde3b80.ts.net:8791")
                        .font(.caption)
                }

                Section {
                    Button {
                        dismissKeyboard()
                        Task { await checkConnection() }
                    } label: {
                        HStack {
                            if isCheckingConnection {
                                ProgressView()
                            } else {
                                Image(systemName: connectionOK ? "checkmark.circle.fill" : "network")
                                    .foregroundStyle(connectionOK ? DS.ColorToken.success : DS.ColorToken.primary)
                            }
                            Text(isCheckingConnection ? "확인 중..." : "연결 확인")
                        }
                        .frame(maxWidth: .infinity, alignment: .center)
                    }
                    .disabled(!isValid || isCheckingConnection)
                    .accessibilityIdentifier("checkBridgeConnectionButton")

                    if !connectionMessage.isEmpty {
                        Text(connectionMessage)
                            .font(.caption)
                            .foregroundStyle(connectionOK ? DS.ColorToken.success : DS.ColorToken.danger)
                    }
                } footer: {
                    Text("저장 전 iPhone에서 bridge /health 응답을 받을 수 있는지 확인합니다.")
                        .font(.caption)
                }

                Section {
                    TextField("server/.bridge_api_key 값", text: $apiKeyText)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                } header: {
                    Text("API 키")
                } footer: {
                    Text("LAN 연결은 API 키가 필요합니다. run_lan_bridge.sh 실행 시 출력되는 키를 입력하세요.")
                        .font(.caption)
                }

                Section {
                    TextField("physio_app organization id", text: $ownerOrgIdText)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                    TextField("physio_app expert person id", text: $ownerProviderPersonIdText)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                } header: {
                    Text("Physio App 연동")
                } footer: {
                    Text("physio_app의 GlassPT 화면에 표시되는 조직 ID와 전문가 ID를 입력하면, 새 촬영/음성/텍스트 기록이 해당 로그인 전문가의 수신함에만 표시됩니다.")
                        .font(.caption)
                }

                Section {
                    Button("저장") {
                        dismissKeyboard()
                        saveSettings()
                        dismiss()
                    }
                    .disabled(!isValid)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .foregroundStyle(isValid ? DS.ColorToken.primary : .gray)
                }
            }
            .scrollDismissesKeyboard(.interactively)
            .keyboardDoneToolbar()
            .navigationTitle("서버 설정")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("닫기") { dismiss() }
                }
            }
        }
    }

    private func checkConnection() async {
        let trimmedURL = urlText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let baseURL = URL(string: trimmedURL), baseURL.scheme?.hasPrefix("http") == true else {
            connectionOK = false
            connectionMessage = "유효한 http URL을 입력하세요."
            return
        }

        isCheckingConnection = true
        connectionMessage = ""
        defer { isCheckingConnection = false }

        let healthURL = baseURL.appending(path: "health")
        var request = URLRequest(url: healthURL)
        request.timeoutInterval = 5

        let apiKey = apiKeyText.trimmingCharacters(in: .whitespacesAndNewlines)
        if !apiKey.isEmpty {
            request.setValue(apiKey, forHTTPHeaderField: "x-api-key")
        }

        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                connectionOK = false
                connectionMessage = "연결 실패: 서버 응답을 확인하세요."
                return
            }

            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let ok = json["ok"] as? Bool,
               !ok {
                connectionOK = false
                connectionMessage = "연결됨, DB 상태를 확인하세요."
                return
            }

            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let security = json["security"] as? [String: Any] {
                let configured = security["api_key_configured"] as? Bool ?? false
                let required = security["require_api_key"] as? Bool ?? false
                if required && !configured {
                    connectionOK = false
                    connectionMessage = "서버 API 키가 아직 설정되지 않았습니다."
                    return
                }
                if configured && apiKey.isEmpty {
                    connectionOK = false
                    connectionMessage = "서버 API 키를 입력해야 업로드할 수 있습니다."
                    return
                }
                if configured {
                    let probeURL = baseURL.appending(path: "recent-events").appending(queryItems: [
                        URLQueryItem(name: "limit", value: "1")
                    ])
                    var probeRequest = URLRequest(url: probeURL)
                    probeRequest.timeoutInterval = 5
                    probeRequest.setValue(apiKey, forHTTPHeaderField: "x-api-key")

                    let (probeData, probeResponse) = try await URLSession.shared.data(for: probeRequest)
                    guard let probeHTTP = probeResponse as? HTTPURLResponse else {
                        connectionOK = false
                        connectionMessage = "보호 API 응답을 확인할 수 없습니다."
                        return
                    }
                    guard (200..<300).contains(probeHTTP.statusCode) else {
                        let body = String(data: probeData, encoding: .utf8) ?? ""
                        connectionOK = false
                        connectionMessage = UserFacingError.message(for: BridgeError.badStatus(probeHTTP.statusCode, body: body))
                        return
                    }
                }
            }

            connectionOK = true
            saveSettings(trimmedURL: trimmedURL, apiKey: apiKey)
            connectionMessage = "연결 성공: API 키 확인됨. 저장 완료."
        } catch {
            connectionOK = false
            connectionMessage = UserFacingError.message(for: error)
        }
    }

    private func saveSettings(trimmedURL: String? = nil, apiKey: String? = nil) {
        let url = trimmedURL ?? urlText.trimmingCharacters(in: .whitespacesAndNewlines)
        let key = apiKey ?? apiKeyText.trimmingCharacters(in: .whitespacesAndNewlines)
        let orgId = ownerOrgIdText.trimmingCharacters(in: .whitespacesAndNewlines)
        let providerPersonId = ownerProviderPersonIdText.trimmingCharacters(in: .whitespacesAndNewlines)
        onSave(url, key, orgId, providerPersonId)
    }
}

// MARK: - 기기 상태 배너

private struct DeviceStatusBanner: View {
    let deviceManager: DeviceSessionManager

    var isConnected: Bool { deviceManager.linkState == .connected }

    var body: some View {
        // 연결 끊겼을 때만 표시
        if !isConnected {
            HStack(alignment: .top, spacing: 9) {
                Image(systemName: "antenna.radiowaves.left.and.right.slash")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(DS.ColorToken.warning)
                    .frame(width: 18, height: 18)
                Text(deviceManager.statusMessage)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.white)
                    .lineLimit(2)
                    .minimumScaleFactor(0.86)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button("재연결") {
                    deviceManager.retryConnection()
                }
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(DS.ColorToken.warning)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(DS.ColorToken.surface, in: RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous))
            .padding(.horizontal, 16)
            .padding(.top, 56) // 내비바 아래
            .transition(.move(edge: .top).combined(with: .opacity))
        }
    }
}

// MARK: - 음성 탭

private struct AudioTab: View {
    @ObservedObject var vm: AdapterViewModel
    @State private var audioRecorder = AudioRecorder()
    @State private var selectedAudioURL: URL?
    @State private var showImporter = false
    @State private var selectedPatient: Patient? = nil
    @State private var showPatientPicker = false
    @State private var store = PatientStore()
    @State private var showConsentAlert = false
    @State private var pendingAudioURL: URL?
    @State private var pendingConsentPatientName = ""

    var body: some View {
        ScrollView {
            VStack(spacing: 24) {
                // 환자 선택 버튼
                Button {
                    showPatientPicker = true
                } label: {
                    HStack {
                        Image(systemName: selectedPatient == nil ? "person.crop.circle.badge.plus" : "person.crop.circle.fill")
                            .foregroundStyle(selectedPatient == nil ? Color.secondary : DS.ColorToken.primary)
                        Text(selectedPatient?.name ?? "환자 선택 (필수)")
                            .foregroundStyle(selectedPatient == nil ? .secondary : .primary)
                        Spacer()
                        if selectedPatient != nil {
                            Button {
                                selectedPatient = nil
                            } label: {
                                Image(systemName: "xmark.circle.fill")
                                    .foregroundStyle(.secondary)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(12)
                    .background(DS.ColorToken.panel, in: RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous))
                }
                .buttonStyle(.plain)
                .sheet(isPresented: $showPatientPicker) {
                    PatientPickerView(selectedPatient: $selectedPatient, store: store)
                }

                // 녹음 버튼
                Button {
                    Task {
                        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                        if audioRecorder.isRecording {
                            audioRecorder.stopRecording()
                            if let url = audioRecorder.recordedFileURL {
                                queueAudioUpload(url)
                            }
                        } else {
                            guard selectedPatient != nil else {
                                showPatientPicker = true
                                return
                            }
                            await audioRecorder.startRecording()
                        }
                    }
                } label: {
                    VStack(spacing: 14) {
                        ZStack {
                            Circle()
                                .fill(audioRecorder.isRecording ? DS.ColorToken.danger.opacity(0.12) : DS.ColorToken.primary.opacity(0.10))
                                .frame(width: 100, height: 100)
                            Image(systemName: audioRecorder.isRecording ? "stop.circle.fill" : "mic.circle.fill")
                                .font(.system(size: 56))
                                .foregroundStyle(audioRecorder.isRecording ? DS.ColorToken.danger : DS.ColorToken.primary)
                                .scaleEffect(audioRecorder.isRecording ? 1.08 : 1.0)
                                .animation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true),
                                           value: audioRecorder.isRecording)
                        }
                        Text(audioRecorder.isRecording ? "중지 & 업로드" : "글라스 녹음")
                            .font(.headline)
                            .foregroundStyle(audioRecorder.isRecording ? DS.ColorToken.danger : DS.ColorToken.primary)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 28)
                    .background(
                        RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous)
                            .fill(audioRecorder.isRecording ? DS.ColorToken.danger.opacity(0.06) : DS.ColorToken.primary.opacity(0.06))
                    )
                }
                .buttonStyle(.plain)

                Text(audioRecorder.statusMessage)
                    .font(.caption)
                    .foregroundStyle(.secondary)

                Divider()

                // 파일 선택
                HStack(spacing: 12) {
                    Button {
                        showImporter = true
                    } label: {
                        Label("파일 선택", systemImage: "folder")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)

                    Button {
                        if let u = selectedAudioURL { queueAudioUpload(u) }
                    } label: {
                        Label("업로드", systemImage: "arrow.up.circle")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(selectedAudioURL == nil)
                }

                if let u = selectedAudioURL {
                    Label(u.lastPathComponent, systemImage: "waveform")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                ResultCard(vm: vm)
            }
            .padding(20)
        }
        .navigationTitle("음성 녹음")
        .navigationBarTitleDisplayMode(.large)
        .fileImporter(
            isPresented: $showImporter,
            allowedContentTypes: [.audio, .wav, .item],
            allowsMultipleSelection: false
        ) { result in
            if case .success(let urls) = result, let url = urls.first {
                _ = url.startAccessingSecurityScopedResource()
                selectedAudioURL = url
            }
        }
        .alert("환자 동의 확인", isPresented: $showConsentAlert) {
            Button("동의 기록 후 업로드") {
                confirmConsentAndUpload()
            }
            Button("취소", role: .cancel) { }
        } message: {
            Text("\(pendingConsentPatientName) 환자/보호자의 녹음, 분석, 차트 생성 동의를 확인한 뒤 진행하세요.")
        }
    }

    private func queueAudioUpload(_ url: URL) {
        guard let patient = selectedPatient else {
            showPatientPicker = true
            return
        }

        Task { @MainActor in
            do {
                if try await vm.client.hasActiveConsent(patientName: patient.name) {
                    vm.uploadAudio(fileURL: url, patientName: patient.name)
                } else {
                    pendingAudioURL = url
                    pendingConsentPatientName = patient.name
                    showConsentAlert = true
                }
            } catch {
                pendingAudioURL = url
                pendingConsentPatientName = patient.name
                showConsentAlert = true
            }
        }
    }

    private func confirmConsentAndUpload() {
        guard let url = pendingAudioURL else { return }
        let patientName = pendingConsentPatientName
        Task { @MainActor in
            do {
                try await vm.client.recordConsent(patientName: patientName)
                vm.uploadAudio(fileURL: url, patientName: patientName)
                pendingAudioURL = nil
            } catch {
                vm.state = .failed(message: UserFacingError.message(for: error))
                vm.lastMessage = UserFacingError.message(for: error)
            }
        }
    }
}

// MARK: - 텍스트 탭

private struct TextTab: View {
    @ObservedObject var vm: AdapterViewModel
    @State private var textInput = ""
    @State private var selectedPatient: Patient? = nil
    @State private var showPatientPicker = false
    @State private var store = PatientStore()
    @State private var showConsentAlert = false
    @State private var pendingText = ""
    @State private var pendingConsentPatientName = ""

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                // 환자 선택 버튼
                Button {
                    showPatientPicker = true
                } label: {
                    HStack {
                        Image(systemName: selectedPatient == nil ? "person.crop.circle.badge.plus" : "person.crop.circle.fill")
                            .foregroundStyle(selectedPatient == nil ? Color.secondary : DS.ColorToken.primary)
                        Text(selectedPatient?.name ?? "환자 선택 (필수)")
                            .foregroundStyle(selectedPatient == nil ? .secondary : .primary)
                        Spacer()
                        if selectedPatient != nil {
                            Button {
                                selectedPatient = nil
                            } label: {
                                Image(systemName: "xmark.circle.fill")
                                    .foregroundStyle(.secondary)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(12)
                    .background(DS.ColorToken.panel, in: RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous))
                }
                .buttonStyle(.plain)
                .sheet(isPresented: $showPatientPicker) {
                    PatientPickerView(selectedPatient: $selectedPatient, store: store)
                }

                // 텍스트 입력
                VStack(alignment: .leading, spacing: 8) {
                    Label("임상 메모", systemImage: "square.and.pencil")
                        .font(.headline)
                    TextField("환자 상태, 치료 내용 등을 입력하세요...", text: $textInput, axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                        .lineLimit(5...12)
                        .accessibilityIdentifier("clinicalMemoInput")
                }

                Button {
                    dismissKeyboard()
                    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                    queueTextSend()
                } label: {
                    Label("전송", systemImage: "paperplane.fill")
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 4)
                }
                .buttonStyle(.borderedProminent)
                .disabled(textInput.trimmingCharacters(in: .whitespaces).isEmpty)
                .accessibilityIdentifier("sendTextButton")

                ResultCard(vm: vm)
            }
            .padding(20)
        }
        .scrollDismissesKeyboard(.interactively)
        .keyboardDoneToolbar()
        .navigationTitle("텍스트 전송")
        .navigationBarTitleDisplayMode(.large)
        .alert("환자 동의 확인", isPresented: $showConsentAlert) {
            Button("동의 기록 후 전송") {
                confirmConsentAndSend()
            }
            Button("취소", role: .cancel) { }
        } message: {
            Text("\(pendingConsentPatientName) 환자/보호자의 텍스트 기록, 분석, 차트 생성 동의를 확인한 뒤 진행하세요.")
        }
    }

    private func queueTextSend() {
        guard let patient = selectedPatient else {
            showPatientPicker = true
            return
        }

        let text = textInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }

        Task { @MainActor in
            do {
                if try await vm.client.hasActiveConsent(patientName: patient.name) {
                    vm.sendText(text, patientName: patient.name)
                } else {
                    pendingText = text
                    pendingConsentPatientName = patient.name
                    showConsentAlert = true
                }
            } catch {
                pendingText = text
                pendingConsentPatientName = patient.name
                showConsentAlert = true
            }
        }
    }

    private func confirmConsentAndSend() {
        let text = pendingText
        let patientName = pendingConsentPatientName
        Task { @MainActor in
            do {
                try await vm.client.recordConsent(patientName: patientName)
                vm.sendText(text, patientName: patientName)
                pendingText = ""
            } catch {
                vm.state = .failed(message: UserFacingError.message(for: error))
                vm.lastMessage = UserFacingError.message(for: error)
            }
        }
    }
}

// MARK: - 결과 카드 (공유)

private struct ResultCard: View {
    @ObservedObject var vm: AdapterViewModel

    var body: some View {
        if vm.state != .idle {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("처리 결과")
                        .font(.subheadline)
                        .fontWeight(.semibold)
                    Spacer()
                    statusBadge
                }

                if !vm.lastMessage.isEmpty {
                    Text(vm.lastMessage)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                }
            }
            .padding(16)
            .background(DS.ColorToken.panel,
                        in: RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous))
            .transition(.opacity.combined(with: .move(edge: .bottom)))
            .animation(.spring(response: 0.35, dampingFraction: 0.8), value: vm.state)
        }
    }

    @ViewBuilder
    private var statusBadge: some View {
        switch vm.state {
        case .idle, .ready:
            EmptyView()
        case .connecting, .uploading:
            Label("업로드 중", systemImage: "arrow.up.circle")
                .foregroundStyle(DS.ColorToken.warning)
                .font(.caption)
        case .processing:
            Label("처리 중", systemImage: "gearshape.fill")
                .foregroundStyle(DS.ColorToken.primary)
                .font(.caption)
        case .done:
            Label("완료", systemImage: "checkmark.circle.fill")
                .foregroundStyle(DS.ColorToken.success)
                .font(.caption)
        case .failed(let msg):
            Label(msg.isEmpty ? "오류" : "오류", systemImage: "xmark.circle.fill")
                .foregroundStyle(DS.ColorToken.danger)
                .font(.caption)
        }
    }
}
