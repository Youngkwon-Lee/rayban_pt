import SwiftUI

enum DS {
    enum ColorToken {
        static let success = Color(red: 0.05, green: 0.72, blue: 0.56)
        static let warning = Color(red: 0.95, green: 0.60, blue: 0.18)
        static let danger = Color(red: 0.92, green: 0.22, blue: 0.28)
        static let primary = Color(red: 0.35, green: 0.34, blue: 0.84)
        static let primaryAlt = Color(red: 0.18, green: 0.64, blue: 0.95)
        static let cameraBackground = Color(red: 0.02, green: 0.03, blue: 0.07)
        static let surface = Color(red: 0.08, green: 0.10, blue: 0.16).opacity(0.78)
        static let surfaceSoft = Color(red: 0.09, green: 0.12, blue: 0.18).opacity(0.58)
        static let controlSurface = Color(red: 0.13, green: 0.15, blue: 0.20).opacity(0.92)
        static let controlStroke = Color.white.opacity(0.12)
        static let panel = Color(.secondarySystemGroupedBackground)
    }

    enum Spacing {
        static let xxs: CGFloat = 4
        static let xs: CGFloat = 8
        static let sm: CGFloat = 12
        static let md: CGFloat = 16
    }

    enum Radius {
        static let pill: CGFloat = 16
        static let card: CGFloat = 8
    }

    enum FontSize {
        static let caption: CGFloat = 12
        static let body: CGFloat = 15
    }
}
