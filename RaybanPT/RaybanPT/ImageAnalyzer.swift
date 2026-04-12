import Foundation
import Vision
import UIKit

struct ImageAnalysisResult {
    let text: String           // OCR
    let labels: [String]       // 장면 분류
    let summary: String        // 최종 요약 텍스트
}

enum ImageAnalyzer {

    static func analyze(_ image: UIImage) async -> ImageAnalysisResult {
        guard let cgImage = image.cgImage else {
            return ImageAnalysisResult(text: "", labels: [], summary: "이미지 변환 실패")
        }

        async let ocrResult = recognizeText(cgImage)
        async let labelResult = classifyImage(cgImage)

        let (ocr, labels) = await (ocrResult, labelResult)

        var parts: [String] = []
        if !ocr.isEmpty {
            parts.append("📝 인식된 텍스트: \(ocr)")
        }
        if !labels.isEmpty {
            parts.append("🏷 장면: \(labels.prefix(3).joined(separator: ", "))")
        }
        let summary = parts.isEmpty ? "분석 결과 없음" : parts.joined(separator: "\n")

        return ImageAnalysisResult(text: ocr, labels: labels, summary: summary)
    }

    private static func recognizeText(_ cgImage: CGImage) async -> String {
        await withCheckedContinuation { cont in
            let request = VNRecognizeTextRequest { req, _ in
                let results = req.results as? [VNRecognizedTextObservation] ?? []
                let text = results
                    .compactMap { $0.topCandidates(1).first?.string }
                    .joined(separator: " ")
                cont.resume(returning: text)
            }
            request.recognitionLanguages = ["ko-KR", "en-US"]
            request.recognitionLevel = .accurate

            let handler = VNImageRequestHandler(cgImage: cgImage)
            try? handler.perform([request])
        }
    }

    private static func classifyImage(_ cgImage: CGImage) async -> [String] {
        await withCheckedContinuation { cont in
            let request = VNClassifyImageRequest { req, _ in
                let results = req.results as? [VNClassificationObservation] ?? []
                let labels = results
                    .filter { $0.confidence > 0.3 }
                    .prefix(5)
                    .map { $0.identifier }
                cont.resume(returning: Array(labels))
            }
            let handler = VNImageRequestHandler(cgImage: cgImage)
            try? handler.perform([request])
        }
    }
}
