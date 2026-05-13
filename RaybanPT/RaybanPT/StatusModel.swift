import Foundation

enum AdapterState: Equatable {
    case idle
    case connecting
    case ready
    case uploading
    case processing(eventId: String)
    case done
    case failed(message: String)
}

enum UserFacingError {
    static func message(for error: Error) -> String {
        if let urlError = error as? URLError {
            return urlErrorMessage(urlError)
        }

        if let e = error as? BridgeError {
            switch e {
            case .invalidURL: return "설정된 URL이 잘못되었습니다."
            case .network(let message):
                return message.isEmpty ? "네트워크 연결을 확인해주세요." : serverCodeMessage(message) ?? message
            case .badStatus(let code, let body):
                return serverStatusMessage(statusCode: code, body: body)
            case .decode: return "응답 해석 중 오류가 발생했습니다."
            case .fileNotFound:
                return "파일을 찾을 수 없습니다."
            }
        }
        return "알 수 없는 오류가 발생했습니다."
    }

    static func message(code: String?, fallback: String?) -> String {
        if let code, let mapped = serverCodeMessage(code) {
            if let fallback, !fallback.isEmpty {
                return "\(mapped)\n\(fallback)"
            }
            return mapped
        }
        return fallback?.isEmpty == false ? fallback! : "처리 중 오류가 발생했습니다."
    }

    private static func serverStatusMessage(statusCode: Int, body: String) -> String {
        let parsed = parseServerError(body)
        if let code = parsed.code, let mapped = serverCodeMessage(code) {
            if let message = parsed.message, !message.isEmpty {
                return "\(mapped)\n\(message)"
            }
            return mapped
        }

        if let message = parsed.message, !message.isEmpty {
            return message
        }

        switch statusCode {
        case 401:
            return "API 키가 맞지 않습니다. 서버 설정의 API 키를 다시 확인해주세요."
        case 413:
            return "파일 용량이 너무 큽니다. 짧게 녹화하거나 해상도를 낮춰 다시 시도하세요."
        case 422:
            return "서버가 요청을 처리하지 못했습니다. 입력 파일과 환자 선택 상태를 확인해주세요."
        case 428:
            return "환자 동의 기록이 필요합니다. 환자 선택 후 동의 확인을 기록하고 다시 진행하세요."
        case 500...599:
            return "서버 내부 오류가 발생했습니다. 감사 로그에서 최근 오류를 확인해주세요."
        default:
            return "서버 오류(\(statusCode))가 발생했습니다."
        }
    }

    private static func parseServerError(_ body: String) -> (code: String?, message: String?) {
        guard let data = body.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            return (nil, body.trimmingCharacters(in: .whitespacesAndNewlines))
        }

        if let code = object["code"] as? String {
            return (code, object["message"] as? String)
        }

        if let detail = object["detail"] as? [String: Any] {
            return (detail["code"] as? String, detail["message"] as? String)
        }

        if let detail = object["detail"] as? String {
            return (nil, detail)
        }

        return (nil, nil)
    }

    private static func serverCodeMessage(_ code: String) -> String? {
        switch code {
        case "UNAUTHORIZED":
            return "API 키가 맞지 않습니다. 서버 설정의 API 키를 다시 확인해주세요."
        case "BRIDGE_API_KEY_REQUIRED":
            return "서버가 LAN 요청을 보호 중입니다. run_lan_bridge.sh 실행 시 출력된 API 키를 앱에 입력하세요."
        case "PATIENT_CONSENT_REQUIRED":
            return "환자 동의 기록이 필요합니다. 환자 선택 후 동의 확인을 기록하고 다시 진행하세요."
        case "FACE_NOT_DETECTED":
            return "얼굴을 감지하지 못해 이미지 처리를 중단했습니다. 환자 얼굴이 보이도록 다시 촬영하세요."
        case "MASKING_FAILED":
            return "얼굴 마스킹에 실패해 이미지 처리를 중단했습니다. 조명과 각도를 바꿔 다시 촬영하세요."
        case "FILE_DOWNLOAD_DISABLED":
            return "원본 업로드 파일 다운로드는 비활성화되어 있습니다."
        case "UPLOAD_TOO_LARGE":
            return "파일 용량이 너무 큽니다. 짧게 녹화하거나 해상도를 낮춰 다시 시도하세요."
        case "INVALID_AUDIO_FILE":
            return "오디오 파일만 업로드할 수 있습니다."
        case "INVALID_IMAGE_FILE":
            return "이미지 파일만 업로드할 수 있습니다."
        case "INVALID_VIDEO_FILE":
            return "영상 파일만 업로드할 수 있습니다."
        case "PROCESS_TIMEOUT":
            return "서버 처리 시간이 초과되었습니다. 파일을 줄이거나 잠시 후 다시 시도하세요."
        case "DB_ERROR":
            return "서버 데이터베이스 오류가 발생했습니다. 감사 로그와 서버 상태를 확인해주세요."
        default:
            return nil
        }
    }

    private static func urlErrorMessage(_ error: URLError) -> String {
        switch error.code {
        case .cannotConnectToHost, .cannotFindHost:
            return "브리지 서버에 연결할 수 없습니다. 서버가 켜져 있는지 확인하고, iPhone에서는 127.0.0.1/localhost 대신 Mac의 LAN 또는 Tailscale 주소를 입력하세요."
        case .timedOut:
            return "브리지 서버 응답 시간이 초과되었습니다. iPhone과 Mac이 같은 네트워크/Tailscale에 있는지 확인하세요."
        case .notConnectedToInternet, .networkConnectionLost:
            return "네트워크 연결이 끊겼습니다. Wi-Fi 또는 Tailscale 연결을 확인하세요."
        case .unsupportedURL, .badURL:
            return "서버 URL 형식이 잘못되었습니다. 예: http://192.168.50.9:8791"
        case .appTransportSecurityRequiresSecureConnection:
            return "iOS 보안 정책 때문에 HTTP 연결이 막혔습니다. 개발용 HTTP 예외 설정 또는 HTTPS/Tailscale 주소를 확인하세요."
        default:
            return "네트워크 오류가 발생했습니다: \(error.localizedDescription)"
        }
    }
}
