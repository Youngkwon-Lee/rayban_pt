import XCTest

final class TextSendUITest: XCTestCase {

    var app: XCUIApplication!

    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
        // UserDefaults를 launch argument로 주입
        app.launchArguments = ["-bridge_base_url", "http://100.125.26.99:8791",
                               "-bridge_api_key", "4yOj6Il_gW4Jkg7YWPuDbAtjfyvfCjEn7HoiTBKfhmQ"]
        app.launch()
    }

    func testTextSend() throws {
        // 텍스트 탭으로 이동
        let textTab = app.tabBars.buttons["텍스트"]
        XCTAssertTrue(textTab.waitForExistence(timeout: 5), "텍스트 탭이 없음")
        textTab.tap()

        // 텍스트 입력
        let textField = app.textViews.firstMatch
        XCTAssertTrue(textField.waitForExistence(timeout: 3), "텍스트 필드 없음")
        textField.tap()
        textField.typeText("테스트: 환자 보행 속도 정상. 균형 양호. VAS 1/10.")

        // 전송 버튼 탭
        let sendButton = app.buttons["전송"]
        XCTAssertTrue(sendButton.waitForExistence(timeout: 3), "전송 버튼 없음")
        sendButton.tap()

        // 업로드 중 → 완료 대기
        let done = app.staticTexts["완료"]
        XCTAssertTrue(done.waitForExistence(timeout: 15), "완료 표시 없음")

        // 스크린샷
        let screenshot = app.screenshot()
        let attachment = XCTAttachment(screenshot: screenshot)
        attachment.name = "텍스트전송완료"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testServerSetupSheet() throws {
        // 서버 설정 버튼이 흰색(정상)인지 확인
        let serverButton = app.buttons.matching(identifier: "server.rack").firstMatch
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
}
