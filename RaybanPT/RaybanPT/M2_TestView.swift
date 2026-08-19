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
    @State private var glassExperience = GlassExperienceCoordinator.shared
    @State private var selectedTab: Tab = .camera
    @State private var showServerSetup = false
    @State private var showLaunchIntro = true
    @State private var showReadyPanel = false
    @State private var didRunLaunchIntro = false
    @AppStorage("smart_glass_live.has_seen_ready_panel") private var hasSeenReadyPanel = false

    // 업로드 완료 액션
    @State private var showPostUploadDialog = false
    @State private var showPostLabelSheet = false
    @State private var showPostChartSheet = false

    // 미라벨 뱃지
    @State private var unlabeledBadge = 0

    enum Tab: CaseIterable {
        case camera, audio, text, charts

        var title: String {
            switch self {
            case .camera: return "카메라"
            case .audio: return "음성"
            case .text: return "텍스트"
            case .charts: return "차트"
            }
        }

        var iconName: String {
            switch self {
            case .camera: return "camera.viewfinder"
            case .audio: return "waveform"
            case .text: return "text.bubble.fill"
            case .charts: return "doc.text.fill"
            }
        }

        var shortHint: String {
            switch self {
            case .camera: return "촬영"
            case .audio: return "STT"
            case .text: return "메모"
            case .charts: return "기록"
            }
        }

        var accent: Color {
            switch self {
            case .camera: return DS.ColorToken.electric
            case .audio: return DS.ColorToken.success
            case .text: return DS.ColorToken.violet
            case .charts: return DS.ColorToken.primaryAlt
            }
        }
    }

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

    private var isGuidedModeActive: Bool {
        glassExperience.isGuidedModeActive && selectedTab == .camera
    }

    var body: some View {
        ZStack {
            selectedTabContent
        }
        .tint(DS.ColorToken.primary)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if showsBottomTabDock {
                SmartGlassTabDock(selectedTab: $selectedTab)
                    .padding(.horizontal, 14)
                    .padding(.top, 8)
                    .padding(.bottom, 8)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .overlay(alignment: .top) {
            if selectedTab == .camera {
                DeviceStatusBanner(deviceManager: deviceManager)
                    .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .overlay(alignment: .topTrailing) {
            if isGuidedModeActive {
                guidedModeExitButton
                    .padding(.top, 56)
                    .padding(.trailing, 16)
                    .transition(.move(edge: .trailing).combined(with: .opacity))
            }
        }
        .overlay(alignment: .bottomTrailing) {
            if !isGuidedModeActive && !showLaunchIntro && !showReadyPanel && selectedTab != .camera {
                Button {
                    showServerSetup = true
                } label: {
                    ServerSettingsButton(needsSetup: needsServerSetup)
                }
                .accessibilityIdentifier("serverSettingsButton")
                .padding(.bottom, showsBottomTabDock ? 104 : 22)
                .padding(.trailing, 16)
            }
        }
        .overlay {
            if showLaunchIntro {
                SmartGlassLaunchIntro()
                    .transition(.opacity.combined(with: .scale(scale: 1.04)))
                    .zIndex(10)
            }
        }
        .overlay {
            if showReadyPanel {
                SmartGlassReadyPanel(
                    isServerReady: !needsServerSetup,
                    isGlassConnected: deviceManager.linkState == .connected,
                    onStart: {
                        completeReadyPanel(tab: .camera)
                    },
                    onOpenServer: {
                        hasSeenReadyPanel = true
                        withAnimation(.spring(response: 0.42, dampingFraction: 0.88)) {
                            showReadyPanel = false
                        }
                        showServerSetup = true
                    },
                    onSelectTab: { tab in
                        completeReadyPanel(tab: tab)
                    }
                )
                .transition(.opacity.combined(with: .scale(scale: 1.02)))
                .zIndex(9)
            }
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
            ServerSetupSheet(client: vm.client) { newURL, newAPIKey, newOrgId, newProviderPersonId, newSubjectPersonId in
                UserDefaults.standard.set(newURL, forKey: "bridge_base_url")
                UserDefaults.standard.set(newAPIKey, forKey: "bridge_api_key")
                UserDefaults.standard.set(newOrgId, forKey: "glasspt_owner_org_id")
                UserDefaults.standard.set(newProviderPersonId, forKey: "glasspt_owner_provider_person_id")
                UserDefaults.standard.set(newSubjectPersonId, forKey: "glasspt_subject_person_id")
                vm.client.updateBaseURL(URL(string: newURL)!)
                vm.client.updateAPIKey(newAPIKey)
                vm.client.updateOwnerScope(orgId: newOrgId, providerPersonId: newProviderPersonId)
                vm.client.updatePhysioContext(
                    clientId: UserDefaults.standard.string(forKey: "glasspt_physio_client_id") ?? "",
                    sessionId: UserDefaults.standard.string(forKey: "glasspt_physio_session_id") ?? "",
                    subjectPersonId: newSubjectPersonId
                )
                NotificationCenter.default.post(name: Notification.Name("bridgeSettingsDidChange"), object: nil)
                Task { await refreshBadge() }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("bridgeSettingsDidChange"))) { _ in
            let orgId = UserDefaults.standard.string(forKey: "glasspt_owner_org_id") ?? ""
            let providerPersonId = UserDefaults.standard.string(forKey: "glasspt_owner_provider_person_id") ?? ""
            let subjectPersonId = UserDefaults.standard.string(forKey: "glasspt_subject_person_id") ?? ""
            vm.client.updateOwnerScope(orgId: orgId, providerPersonId: providerPersonId)
            vm.client.updatePhysioContext(
                clientId: UserDefaults.standard.string(forKey: "glasspt_physio_client_id") ?? "",
                sessionId: UserDefaults.standard.string(forKey: "glasspt_physio_session_id") ?? "",
                subjectPersonId: subjectPersonId
            )
        }
        .onReceive(NotificationCenter.default.publisher(for: .glassExperienceLaunchRequested)) { _ in
            selectedTab = .camera
            showReadyPanel = false
            hasSeenReadyPanel = true
        }
        .onReceive(NotificationCenter.default.publisher(for: .openServerSetupRequested)) { _ in
            showServerSetup = true
        }
        .task { await refreshBadge() }
        .onAppear {
            runLaunchIntroIfNeeded()
        }
    }

    private var showsBottomTabDock: Bool {
        !isGuidedModeActive && !showLaunchIntro && !showReadyPanel
    }

    @ViewBuilder
    private var selectedTabContent: some View {
        switch selectedTab {
        case .camera:
            NavigationStack {
                StreamView(client: vm.client)
            }
        case .audio:
            NavigationStack {
                AudioTab(vm: vm)
            }
        case .text:
            NavigationStack {
                TextTab(vm: vm)
            }
        case .charts:
            ChartListView(client: vm.client)
        }
    }

    private func runLaunchIntroIfNeeded() {
        guard !didRunLaunchIntro else { return }
        didRunLaunchIntro = true

        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 1_650_000_000)
            withAnimation(.spring(response: 0.62, dampingFraction: 0.88)) {
                showLaunchIntro = false
            }

            if !hasSeenReadyPanel {
                try? await Task.sleep(nanoseconds: 220_000_000)
                withAnimation(.spring(response: 0.48, dampingFraction: 0.88)) {
                    showReadyPanel = true
                }
                return
            }

            // Keep the capture/HUD surface available even before the bridge is
            // configured. The server sheet remains reachable from the settings
            // affordance; auto-presenting it here blocks DAT registration and
            // makes physical glasses validation impossible.
        }
    }

    private func completeReadyPanel(tab: Tab) {
        hasSeenReadyPanel = true
        selectedTab = tab
        withAnimation(.spring(response: 0.42, dampingFraction: 0.88)) {
            showReadyPanel = false
        }
    }

    private var guidedModeExitButton: some View {
        Button {
            glassExperience.endGuidedMode()
        } label: {
            HStack(spacing: 7) {
                Image(systemName: "rectangle.portrait.and.arrow.right")
                    .font(.system(size: 13, weight: .semibold))
                Text("일반 화면")
                    .font(.system(size: 12, weight: .semibold))
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(DS.ColorToken.surface, in: Capsule())
            .overlay {
                Capsule()
                    .stroke(Color.white.opacity(0.12), lineWidth: 1)
            }
            .shadow(color: .black.opacity(0.18), radius: 8, y: 3)
        }
        .buttonStyle(TactileScaleButtonStyle())
        .accessibilityLabel("일반 화면으로 전환")
    }

    // MARK: - 미라벨 뱃지 갱신
    private func refreshBadge() async {
        guard let events = try? await vm.client.recentEvents(limit: 50) else { return }
        unlabeledBadge = events.filter { !$0.has_label }.count
    }
}

// MARK: - 첫 진입 준비 화면

private struct SmartGlassReadyPanel: View {
    let isServerReady: Bool
    let isGlassConnected: Bool
    let onStart: () -> Void
    let onOpenServer: () -> Void
    let onSelectTab: (M2_TestView.Tab) -> Void

    var body: some View {
        ZStack {
            LaunchBackdrop(time: Date().timeIntervalSinceReferenceDate)
                .ignoresSafeArea()

            VStack(spacing: 18) {
                Spacer(minLength: 18)

                VStack(spacing: 14) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(.white.opacity(0.08))
                            .frame(width: 92, height: 92)
                            .rotationEffect(.degrees(45))
                            .overlay {
                                RoundedRectangle(cornerRadius: 8, style: .continuous)
                                    .stroke(DS.ColorToken.electric.opacity(0.32), lineWidth: 1)
                                    .rotationEffect(.degrees(45))
                            }

                        Image(systemName: "eyeglasses")
                            .font(.system(size: 40, weight: .bold))
                            .foregroundStyle(.white)
                    }

                    VStack(spacing: 7) {
                        Text("Kinelo AR")
                            .font(.system(size: 30, weight: .bold, design: .rounded))
                            .foregroundStyle(.white)
                            .multilineTextAlignment(.center)

                        Text("현장 입력 준비")
                            .font(.system(size: 15, weight: .semibold, design: .rounded))
                            .foregroundStyle(.white.opacity(0.68))
                    }
                }

                VStack(spacing: 10) {
                    ReadyStatusRow(
                        title: "서버",
                        detail: isServerReady ? "저장 준비됨" : "설정 필요",
                        iconName: "server.rack",
                        isReady: isServerReady,
                        actionTitle: isServerReady ? nil : "설정",
                        action: onOpenServer
                    )

                    ReadyStatusRow(
                        title: "글래스",
                        detail: isGlassConnected ? "연결됨" : "앱에서 연결 후 시작",
                        iconName: "eyeglasses",
                        isReady: isGlassConnected,
                        actionTitle: nil,
                        action: {}
                    )
                }
                .padding(14)
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .stroke(Color.white.opacity(0.12), lineWidth: 1)
                }

                Button(action: onStart) {
                    HStack(spacing: 10) {
                        Image(systemName: "camera.viewfinder")
                            .font(.system(size: 15, weight: .bold))
                        Text("현장 입력 시작")
                            .font(.system(size: 16, weight: .bold, design: .rounded))
                    }
                    .foregroundStyle(.black)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 15)
                    .background(
                        LinearGradient(
                            colors: [DS.ColorToken.electric, DS.ColorToken.success],
                            startPoint: .leading,
                            endPoint: .trailing
                        ),
                        in: RoundedRectangle(cornerRadius: 8, style: .continuous)
                    )
                }
                .buttonStyle(TactileScaleButtonStyle())
                .accessibilityIdentifier("startFieldInputButton")

                HStack(spacing: 8) {
                    ReadyQuickAction(tab: .audio) { onSelectTab(.audio) }
                    ReadyQuickAction(tab: .text) { onSelectTab(.text) }
                    ReadyQuickAction(tab: .charts) { onSelectTab(.charts) }
                }

                Spacer(minLength: 18)
            }
            .padding(.horizontal, 22)
        }
        .accessibilityElement(children: .contain)
    }
}

private struct ReadyStatusRow: View {
    let title: String
    let detail: String
    let iconName: String
    let isReady: Bool
    let actionTitle: String?
    let action: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: iconName)
                .font(.system(size: 14, weight: .bold))
                .foregroundStyle(isReady ? DS.ColorToken.success : DS.ColorToken.warning)
                .frame(width: 26, height: 26)
                .background((isReady ? DS.ColorToken.success : DS.ColorToken.warning).opacity(0.14), in: Circle())

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(.white)
                Text(detail)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.58))
            }

            Spacer()

            if let actionTitle {
                Button(actionTitle, action: action)
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(DS.ColorToken.electric)
                    .buttonStyle(TactileScaleButtonStyle())
            } else {
                Image(systemName: isReady ? "checkmark.circle.fill" : "circle.dotted")
                    .font(.system(size: 17, weight: .bold))
                    .foregroundStyle(isReady ? DS.ColorToken.success : .white.opacity(0.36))
            }
        }
        .padding(10)
        .background(Color.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

private struct ReadyQuickAction: View {
    let tab: M2_TestView.Tab
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 7) {
                Image(systemName: tab.iconName)
                    .font(.system(size: 17, weight: .bold))
                    .foregroundStyle(tab.accent)
                    .frame(width: 34, height: 34)
                    .background(tab.accent.opacity(0.14), in: Circle())

                Text(tab.title)
                    .font(.system(size: 12, weight: .bold, design: .rounded))
                    .foregroundStyle(.white.opacity(0.86))
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 12)
            .background(.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(Color.white.opacity(0.10), lineWidth: 1)
            }
        }
        .buttonStyle(TactileScaleButtonStyle())
    }
}

// MARK: - 하단 탭 독

private struct CameraTabMenuButton: View {
    @Binding var selectedTab: M2_TestView.Tab

    var body: some View {
        Menu {
            ForEach(M2_TestView.Tab.allCases.filter { $0 != .camera }, id: \.self) { tab in
                Button {
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                    withAnimation(.spring(response: 0.34, dampingFraction: 0.82)) {
                        selectedTab = tab
                    }
                } label: {
                    Label(tab.title, systemImage: tab.iconName)
                }
            }
        } label: {
            HStack(spacing: 7) {
                Image(systemName: "square.grid.2x2.fill")
                    .font(.system(size: 12, weight: .bold))
                Text("전환")
                    .font(.system(size: 12, weight: .bold, design: .rounded))
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(DS.ColorToken.surface, in: Capsule())
            .overlay {
                Capsule()
                    .stroke(Color.white.opacity(0.12), lineWidth: 1)
            }
            .shadow(color: .black.opacity(0.22), radius: 12, y: 5)
        }
        .accessibilityLabel("음성 텍스트 차트로 전환")
    }
}

private struct SmartGlassTabDock: View {
    @Binding var selectedTab: M2_TestView.Tab

    var body: some View {
        HStack(spacing: 6) {
            ForEach(M2_TestView.Tab.allCases, id: \.self) { tab in
                SmartGlassTabDockButton(
                    tab: tab,
                    isSelected: selectedTab == tab
                ) {
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                    withAnimation(.spring(response: 0.34, dampingFraction: 0.82)) {
                        selectedTab = tab
                    }
                }
            }
        }
        .padding(6)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(Color.white.opacity(0.12), lineWidth: 1)
        }
        .shadow(color: .black.opacity(0.26), radius: 18, y: 8)
    }
}

private struct SmartGlassTabDockButton: View {
    let tab: M2_TestView.Tab
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 4) {
                Image(systemName: tab.iconName)
                    .font(.system(size: 16, weight: .bold))
                    .symbolEffect(.bounce, value: isSelected)

                Text(tab.title)
                    .font(.system(size: 11, weight: .bold, design: .rounded))
                    .lineLimit(1)
                    .minimumScaleFactor(0.78)
            }
            .foregroundStyle(isSelected ? .black : .white.opacity(0.78))
            .frame(maxWidth: .infinity)
            .frame(height: 54)
            .background {
                if isSelected {
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(tab.accent)
                        .overlay(alignment: .topTrailing) {
                            Text(tab.shortHint)
                                .font(.system(size: 8, weight: .black, design: .rounded))
                                .foregroundStyle(.black.opacity(0.64))
                                .padding(.horizontal, 5)
                                .padding(.vertical, 2)
                                .background(.black.opacity(0.08), in: Capsule())
                                .padding(5)
                        }
                }
            }
        }
        .buttonStyle(TactileScaleButtonStyle())
        .accessibilityLabel("\(tab.title) 탭")
    }
}

// MARK: - 브랜드 인트로

private struct SmartGlassLaunchIntro: View {
    var body: some View {
        TimelineView(.animation) { timeline in
            let time = timeline.date.timeIntervalSinceReferenceDate
            let pulse = 1 + 0.04 * CGFloat(sin(time * 2.4))
            let drift = CGFloat(sin(time * 0.8)) * 8

            ZStack {
                LaunchBackdrop(time: time)

                VStack(spacing: 22) {
                    ZStack {
                        Circle()
                            .fill(DS.ColorToken.electric.opacity(0.16))
                            .frame(width: 190, height: 190)
                            .blur(radius: 18)
                            .scaleEffect(pulse)

                        Circle()
                            .stroke(DS.ColorToken.electric.opacity(0.24), lineWidth: 1)
                            .frame(width: 154, height: 154)
                            .rotationEffect(.degrees(time * 18))

                        Circle()
                            .stroke(DS.ColorToken.violet.opacity(0.20), lineWidth: 1)
                            .frame(width: 128, height: 128)
                            .rotationEffect(.degrees(-time * 14))

                        Image(systemName: "eyeglasses")
                            .font(.system(size: 62, weight: .semibold))
                            .foregroundStyle(
                                LinearGradient(
                                    colors: [.white, DS.ColorToken.electric, DS.ColorToken.violet],
                                    startPoint: .topLeading,
                                    endPoint: .bottomTrailing
                                )
                            )
                            .offset(y: drift * 0.12)
                    }
                    .frame(width: 210, height: 210)

                    VStack(spacing: 9) {
                        Text("Kinelo AR")
                            .font(.system(size: 32, weight: .bold, design: .rounded))
                            .foregroundStyle(.white)
                            .multilineTextAlignment(.center)

                        Text("현장 입력을 바로 기록으로")
                            .font(.system(size: 14, weight: .semibold, design: .rounded))
                            .foregroundStyle(.white.opacity(0.64))
                    }

                    HStack(spacing: 10) {
                        LaunchStatusChip(title: "Glass", iconName: "eyeglasses")
                        LaunchStatusChip(title: "Voice", iconName: "waveform")
                        LaunchStatusChip(title: "Chart", iconName: "doc.text")
                    }
                    .padding(.top, 2)
                }
                .offset(y: -16 + drift * 0.18)
                .padding(.horizontal, 28)
            }
            .ignoresSafeArea()
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Kinelo AR 시작 화면")
    }
}

private struct LaunchBackdrop: View {
    let time: TimeInterval

    var body: some View {
        Canvas { context, size in
            let rect = CGRect(origin: .zero, size: size)
            context.fill(
                Path(rect),
                with: .linearGradient(
                    Gradient(colors: [
                        DS.ColorToken.midnight,
                        Color(red: 0.03, green: 0.05, blue: 0.11),
                        Color(red: 0.015, green: 0.02, blue: 0.045)
                    ]),
                    startPoint: .zero,
                    endPoint: CGPoint(x: size.width, y: size.height)
                )
            )

            drawOrb(
                context: &context,
                size: size,
                x: 0.76 + 0.06 * CGFloat(sin(time * 0.22)),
                y: 0.24 + 0.05 * CGFloat(cos(time * 0.18)),
                radius: 0.44,
                color: DS.ColorToken.electric.opacity(0.24)
            )
            drawOrb(
                context: &context,
                size: size,
                x: 0.24 + 0.05 * CGFloat(cos(time * 0.20)),
                y: 0.78 + 0.04 * CGFloat(sin(time * 0.16)),
                radius: 0.48,
                color: DS.ColorToken.violet.opacity(0.22)
            )
        }
        .overlay {
            LinearGradient(
                colors: [
                    Color.black.opacity(0.04),
                    Color.black.opacity(0.22),
                    Color.black.opacity(0.42)
                ],
                startPoint: .top,
                endPoint: .bottom
            )
        }
    }

    private func drawOrb(
        context: inout GraphicsContext,
        size: CGSize,
        x: CGFloat,
        y: CGFloat,
        radius: CGFloat,
        color: Color
    ) {
        let point = CGPoint(x: size.width * x, y: size.height * y)
        let r = max(size.width, size.height) * radius
        let rect = CGRect(x: point.x - r, y: point.y - r, width: r * 2, height: r * 2)

        context.addFilter(.blur(radius: 42))
        context.fill(
            Path(ellipseIn: rect),
            with: .radialGradient(
                Gradient(colors: [color, .clear]),
                center: point,
                startRadius: 0,
                endRadius: r
            )
        )
    }
}

private struct LaunchStatusChip: View {
    let title: String
    let iconName: String

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: iconName)
                .font(.system(size: 10, weight: .bold))
            Text(title)
                .font(.system(size: 11, weight: .bold, design: .rounded))
        }
        .foregroundStyle(.white.opacity(0.86))
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(.ultraThinMaterial, in: Capsule())
        .overlay {
            Capsule()
                .stroke(Color.white.opacity(0.12), lineWidth: 1)
        }
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
                    Button(deviceManager.registrationState == .registered ? "재연결" : "Meta AI 연결") {
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
    let onSave: (String, String, String, String, String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var urlText: String = UserDefaults.standard.string(forKey: "bridge_base_url") ?? ""
    @State private var apiKeyText: String = UserDefaults.standard.string(forKey: "bridge_api_key") ?? ""
    @State private var ownerOrgIdText: String = UserDefaults.standard.string(forKey: "glasspt_owner_org_id") ?? ""
    @State private var ownerProviderPersonIdText: String = UserDefaults.standard.string(forKey: "glasspt_owner_provider_person_id") ?? ""
    @State private var subjectPersonIdText: String = UserDefaults.standard.string(forKey: "glasspt_subject_person_id") ?? ""
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
                    TextField("patient subject_person_id", text: $subjectPersonIdText)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                } header: {
                    Text("Physio App 연동")
                } footer: {
                    Text("조직/전문가 ID는 현장 기록 정렬에 쓰이고, subject_person_id는 moai_web gold/readiness 매핑에 쓰입니다. physio_client_id가 있어도 subject_person_id가 있으면 더 안정적입니다.")
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
        let subjectPersonId = subjectPersonIdText.trimmingCharacters(in: .whitespacesAndNewlines)
        onSave(url, key, orgId, providerPersonId, subjectPersonId)
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
                Button(deviceManager.registrationState == .registered ? "재연결" : "Meta AI 연결") {
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
                            .buttonStyle(TactileScaleButtonStyle())
                        }
                    }
                    .padding(12)
                    .background(DS.ColorToken.panel, in: RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous))
                }
                .buttonStyle(TactileScaleButtonStyle())
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
                .buttonStyle(TactileScaleButtonStyle())

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
        .navigationTitle("음성")
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
                            .buttonStyle(TactileScaleButtonStyle())
                        }
                    }
                    .padding(12)
                    .background(DS.ColorToken.panel, in: RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous))
                }
                .buttonStyle(TactileScaleButtonStyle())
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
        .navigationTitle("텍스트")
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
