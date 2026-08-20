import XCTest

final class TextSendUITest: XCTestCase {

    var app: XCUIApplication!
    private var bridgeURL: String {
        ProcessInfo.processInfo.environment["BRIDGE_BASE_URL"] ?? "http://127.0.0.1:8791"
    }
    private var bridgeAPIKey: String {
        ProcessInfo.processInfo.environment["BRIDGE_API_KEY"] ?? ""
    }

    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
        // UserDefaults를 launch argument로 주입
        app.launchArguments = ["-bridge_base_url", bridgeURL,
                               "-bridge_api_key", bridgeAPIKey]
        app.launch()
    }

    func testTextSend() throws {
        // 텍스트 탭으로 이동
        let textTab = app.tabBars.buttons["텍스트"]
        XCTAssertTrue(textTab.waitForExistence(timeout: 5), "텍스트 탭이 없음")
        textTab.tap()

        // 텍스트 입력
        let textField = app.textFields["clinicalMemoInput"].exists
            ? app.textFields["clinicalMemoInput"]
            : app.textViews["clinicalMemoInput"]
        XCTAssertTrue(textField.waitForExistence(timeout: 5), "텍스트 필드 없음")
        textField.tap()
        textField.typeText("테스트: 환자 보행 속도 정상. 균형 양호. VAS 1/10.")

        // 전송 버튼 탭
        let sendButton = app.buttons["sendTextButton"]
        XCTAssertTrue(sendButton.waitForExistence(timeout: 3), "전송 버튼 없음")
        sendButton.tap()

        // 업로드 중 → 완료 대기
        let done = app.staticTexts["완료"]
        let postUploadDialog = app.staticTexts["차트가 생성됐어요"]
        XCTAssertTrue(
            done.waitForExistence(timeout: 15) || postUploadDialog.waitForExistence(timeout: 2),
            "완료 표시 없음"
        )

        // 스크린샷
        let screenshot = app.screenshot()
        let attachment = XCTAttachment(screenshot: screenshot)
        attachment.name = "텍스트전송완료"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testServerSetupSheet() throws {
        // 서버 설정 버튼이 흰색(정상)인지 확인
        let serverButton = app.buttons["serverSettingsButton"]
        // 버튼이 있으면 탭해서 sheet 열기
        if serverButton.exists {
            serverButton.tap()
            let sheetTitle = app.navigationBars["서버 설정"]
            XCTAssertTrue(sheetTitle.waitForExistence(timeout: 3))
            // URL 필드에 값이 있는지
            let urlField = app.textFields.firstMatch
            XCTAssertFalse(urlField.value as? String == "", "URL 미입력 상태")
            app.buttons["닫기"].tap()
        }
    }

    func testCheckupAccessFromServerSettings() throws {
        let cameraTab = app.tabBars.buttons["카메라"]
        XCTAssertTrue(cameraTab.waitForExistence(timeout: 3), "카메라 탭이 없음")
        cameraTab.tap()

        XCTAssertFalse(app.tabBars.buttons["점검"].exists, "점검은 하단 탭에서 제거되어야 함")

        let serverButton = app.buttons["serverSettingsButton"]
        XCTAssertTrue(serverButton.waitForExistence(timeout: 3), "서버 설정 버튼이 없음")
        serverButton.tap()

        let setupTitle = app.navigationBars["서버 설정"]
        XCTAssertTrue(setupTitle.waitForExistence(timeout: 3), "서버 설정 화면을 열 수 없음")

        let checkupLink = app.buttons["기기 점검 열기"]
        XCTAssertTrue(checkupLink.waitForExistence(timeout: 3), "서버 설정에서 기기 점검 열기 버튼이 없음")
        checkupLink.tap()

        XCTAssertTrue(app.navigationBars["기기 점검"].waitForExistence(timeout: 3), "서버 설정에서 기기 점검 화면으로 이동할 수 없음")
    }

    func testMockDATTransportCameraStream() throws {
        let mockApp = XCUIApplication()
        // Reset the one-time launch gate so this transport test is deterministic
        // even when the simulator has already completed the ready panel in a
        // previous UI test run.
        mockApp.launchArguments = [
            "-rayban_dat_mock",
            "-rayban_dat_mock_qa_patient",
            "-smart_glass_live.has_seen_ready_panel",
            "false",
        ]
        mockApp.launch()

        let startButton = mockApp.buttons["startFieldInputButton"]
        XCTAssertTrue(startButton.waitForExistence(timeout: 8), "준비 화면의 현장 입력 시작 버튼이 없음")
        startButton.tap()

        // The official MWDATMockDevice should drive the same DeviceSession /
        // Camera.Stream path used by physical glasses. The stream surface
        // exposes the connected source after the first mock frame arrives.
        XCTAssertTrue(
            mockApp.staticTexts["Glass"].waitForExistence(timeout: 8),
            "DAT mock 장치가 카메라 화면에서 활성 장치로 표시되지 않음"
        )
        let startStreamButton = mockApp.buttons["시작"]
        XCTAssertTrue(startStreamButton.waitForExistence(timeout: 8), "DAT mock 스트림 시작 버튼이 없음")
        startStreamButton.tap()

        // The center action opens the patient picker instead of starting the
        // DAT stream until a subject is selected. Handle both a clean
        // simulator (empty state) and a simulator with persisted QA patients.
        let patientPicker = mockApp.navigationBars["환자 선택"]
        if patientPicker.waitForExistence(timeout: 3) {
            let addFirstPatient = mockApp.buttons["첫 번째 환자 추가"]
            if addFirstPatient.waitForExistence(timeout: 2) {
                addFirstPatient.tap()
                let patientField = mockApp.textFields["환자 이름 입력"]
                XCTAssertTrue(patientField.waitForExistence(timeout: 3), "QA 환자 입력 필드가 없음")
                patientField.tap()
                patientField.typeText("DAT Mock QA")
                patientField.typeText("\n")
            } else {
                let firstPatient = mockApp.tables.cells.firstMatch
                XCTAssertTrue(firstPatient.waitForExistence(timeout: 3), "기존 QA 환자 행이 없음")
                firstPatient.tap()
            }

            let resumedStart = mockApp.buttons["시작"]
            XCTAssertTrue(resumedStart.waitForExistence(timeout: 5), "환자 선택 후 시작 버튼이 없음")
            resumedStart.tap()
        }

        // The session reaching the live state is a useful transport proof even
        // when the simulator's MockDevice media pipeline is unavailable.
        XCTAssertTrue(
            mockApp.staticTexts["프레임 수신 대기 중"].waitForExistence(timeout: 8),
            "DAT mock Camera.Stream이 streaming 상태로 올라오지 않음"
        )

        if !mockApp.images["raybanDATFrame"].waitForExistence(timeout: 8) {
            let os = ProcessInfo.processInfo.operatingSystemVersion
            if os.majorVersion == 26 && os.minorVersion == 5 {
                throw XCTSkip(
                    "Meta MWDATMockDevice upstream issue #197: iOS 26.5 Simulator에서 "
                    + "videoFramePublisher가 호출되지 않음"
                )
            }
            XCTFail("DAT mock Camera.Stream에서 앱 화면으로 프레임이 전달되지 않음")
        }
    }

    func testChartReviewQueueAccess() throws {
        try XCTSkipIf(bridgeAPIKey.isEmpty, "BRIDGE_API_KEY 환경변수가 있어야 보호된 차트 API를 테스트할 수 있음")

        let chartsTab = app.tabBars.buttons["차트"]
        XCTAssertTrue(chartsTab.waitForExistence(timeout: 5), "차트 탭이 없음")
        chartsTab.tap()

        XCTAssertTrue(app.navigationBars["차트"].waitForExistence(timeout: 5), "차트 화면을 열 수 없음")
        XCTAssertTrue(app.staticTexts["검수 큐"].waitForExistence(timeout: 10), "검수 큐 섹션이 없음")
        XCTAssertFalse(app.staticTexts["차트를 불러올 수 없어요"].exists, "차트 목록 로딩 실패")

        let reviewRow = app.buttons["chartReviewQueueRow"].firstMatch
        if reviewRow.waitForExistence(timeout: 5) {
            reviewRow.tap()
            XCTAssertTrue(app.navigationBars["재활 차트"].waitForExistence(timeout: 5), "검수 큐에서 차트 상세로 이동할 수 없음")
            XCTAssertTrue(app.buttons["chartReviewToggleButton"].waitForExistence(timeout: 5), "차트 검수 버튼이 없음")
            XCTAssertTrue(app.buttons["chartLabelActionButton"].exists, "차트 라벨 버튼이 없음")
        } else {
            XCTAssertTrue(
                app.staticTexts["수정 필요한 차트 없음"].exists || app.descendants(matching: .any)["chartReviewQueueEmpty"].exists,
                "검수 큐 행 또는 빈 상태가 표시되지 않음"
            )
        }
    }
}
