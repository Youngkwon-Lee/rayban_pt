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
        if let e = error as? BridgeError {
            switch e {
            case .invalidURL: return "설정된 URL이 잘못되었습니다."
            case .network: return "네트워크 연결을 확인해주세요."
            case .badStatus(let code): return "서버 오류(\(code))가 발생했습니다."
            case .decode: return "응답 해석 중 오류가 발생했습니다."
            }
        }
        return "알 수 없는 오류가 발생했습니다."
    }
}
