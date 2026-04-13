import Foundation
import Vision
import UIKit

struct ImageAnalysisResult {
    let text: String           // OCR
    let labels: [String]       // 장면 분류
    let pose: PoseAnalysisResult?  // 신체 자세
    let summary: String        // 최종 요약 텍스트
}

struct PoseAnalysisResult {
    struct JointAngle {
        let name: String       // e.g. "우측 팔꿈치"
        let degrees: Double
    }
    let angles: [JointAngle]
    let detectedSide: String   // "좌", "우", "양측"
    let summary: String
}

enum ImageAnalyzer {

    static func analyze(_ image: UIImage) async -> ImageAnalysisResult {
        guard let cgImage = image.cgImage else {
            return ImageAnalysisResult(text: "", labels: [], pose: nil, summary: "이미지 변환 실패")
        }

        async let ocrResult    = recognizeText(cgImage)
        async let labelResult  = classifyImage(cgImage)
        async let poseResult   = analyzePose(cgImage)

        let (ocr, labels, pose) = await (ocrResult, labelResult, poseResult)

        var parts: [String] = []
        if !ocr.isEmpty {
            parts.append("📝 인식된 텍스트: \(ocr)")
        }
        if !labels.isEmpty {
            parts.append("🏷 장면: \(labels.prefix(3).joined(separator: ", "))")
        }
        if let pose {
            parts.append("🦴 자세 분석:\n\(pose.summary)")
        }
        let summary = parts.isEmpty ? "분석 결과 없음" : parts.joined(separator: "\n")

        return ImageAnalysisResult(text: ocr, labels: labels, pose: pose, summary: summary)
    }

    // MARK: - OCR

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

    // MARK: - 장면 분류

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

    // MARK: - 신체 자세 분석

    private static func analyzePose(_ cgImage: CGImage) async -> PoseAnalysisResult? {
        await withCheckedContinuation { cont in
            let request = VNDetectHumanBodyPoseRequest { req, _ in
                guard let observations = req.results as? [VNHumanBodyPoseObservation],
                      let obs = observations.first else {
                    cont.resume(returning: nil)
                    return
                }
                cont.resume(returning: extractAngles(from: obs))
            }
            let handler = VNImageRequestHandler(cgImage: cgImage)
            try? handler.perform([request])
        }
    }

    private static func extractAngles(from obs: VNHumanBodyPoseObservation) -> PoseAnalysisResult? {
        // confidence 0.3 이상 관절만 사용
        func point(_ name: VNHumanBodyPoseObservation.JointName) -> CGPoint? {
            guard let p = try? obs.recognizedPoint(name), p.confidence > 0.3 else { return nil }
            return CGPoint(x: p.x, y: p.y)
        }

        // 관절 각도 계산: B를 꼭짓점으로 A-B-C 각도
        func angle(a: CGPoint, b: CGPoint, c: CGPoint) -> Double {
            let ab = CGPoint(x: a.x - b.x, y: a.y - b.y)
            let cb = CGPoint(x: c.x - b.x, y: c.y - b.y)
            let dot = ab.x * cb.x + ab.y * cb.y
            let magAB = sqrt(ab.x * ab.x + ab.y * ab.y)
            let magCB = sqrt(cb.x * cb.x + cb.y * cb.y)
            guard magAB > 0, magCB > 0 else { return 0 }
            let cosAngle = max(-1.0, min(1.0, Double(dot / (magAB * magCB))))
            return acos(cosAngle) * 180 / .pi
        }

        var results: [PoseAnalysisResult.JointAngle] = []
        var detectedSides: Set<String> = []

        // 우측 팔꿈치: 어깨 → 팔꿈치 → 손목
        if let rs = point(.rightShoulder), let re = point(.rightElbow), let rw = point(.rightWrist) {
            let deg = angle(a: rs, b: re, c: rw)
            results.append(.init(name: "우측 팔꿈치", degrees: deg))
            detectedSides.insert("우")
        }
        // 좌측 팔꿈치
        if let ls = point(.leftShoulder), let le = point(.leftElbow), let lw = point(.leftWrist) {
            let deg = angle(a: ls, b: le, c: lw)
            results.append(.init(name: "좌측 팔꿈치", degrees: deg))
            detectedSides.insert("좌")
        }
        // 우측 어깨: 엉덩이 → 어깨 → 팔꿈치
        if let rh = point(.rightHip), let rs = point(.rightShoulder), let re = point(.rightElbow) {
            let deg = angle(a: rh, b: rs, c: re)
            results.append(.init(name: "우측 어깨", degrees: deg))
        }
        // 좌측 어깨
        if let lh = point(.leftHip), let ls = point(.leftShoulder), let le = point(.leftElbow) {
            let deg = angle(a: lh, b: ls, c: le)
            results.append(.init(name: "좌측 어깨", degrees: deg))
        }
        // 우측 무릎: 엉덩이 → 무릎 → 발목
        if let rh = point(.rightHip), let rk = point(.rightKnee), let ra = point(.rightAnkle) {
            let deg = angle(a: rh, b: rk, c: ra)
            results.append(.init(name: "우측 무릎", degrees: deg))
            detectedSides.insert("우")
        }
        // 좌측 무릎
        if let lh = point(.leftHip), let lk = point(.leftKnee), let la = point(.leftAnkle) {
            let deg = angle(a: lh, b: lk, c: la)
            results.append(.init(name: "좌측 무릎", degrees: deg))
            detectedSides.insert("좌")
        }
        // 우측 엉덩이: 어깨 → 엉덩이 → 무릎
        if let rs = point(.rightShoulder), let rh = point(.rightHip), let rk = point(.rightKnee) {
            let deg = angle(a: rs, b: rh, c: rk)
            results.append(.init(name: "우측 고관절", degrees: deg))
        }
        // 좌측 엉덩이
        if let ls = point(.leftShoulder), let lh = point(.leftHip), let lk = point(.leftKnee) {
            let deg = angle(a: ls, b: lh, c: lk)
            results.append(.init(name: "좌측 고관절", degrees: deg))
        }

        guard !results.isEmpty else { return nil }

        let side: String
        if detectedSides.contains("좌") && detectedSides.contains("우") { side = "양측" }
        else if detectedSides.contains("우") { side = "우측" }
        else { side = "좌측" }

        let angleLines = results
            .map { String(format: "  • \($0.name): %.1f°", $0.degrees) }
            .joined(separator: "\n")

        let summary = "[\(side) 관절 각도 측정]\n\(angleLines)"

        return PoseAnalysisResult(angles: results, detectedSide: side, summary: summary)
    }
}
