import SwiftUI

enum DS {
    enum ColorToken {
        static let success = Color.green
        static let warning = Color.orange
        static let danger = Color.red
        static let primary = Color.indigo
        static let surface = Color.black.opacity(0.55)
        static let surfaceSoft = Color.black.opacity(0.42)
    }

    enum Spacing {
        static let xxs: CGFloat = 4
        static let xs: CGFloat = 8
        static let sm: CGFloat = 12
        static let md: CGFloat = 16
    }

    enum Radius {
        static let pill: CGFloat = 18
        static let card: CGFloat = 12
    }

    enum FontSize {
        static let caption: CGFloat = 12
        static let body: CGFloat = 15
    }
}
