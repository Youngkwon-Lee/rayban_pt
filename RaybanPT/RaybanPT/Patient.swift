import Foundation

// MARK: - Patient 모델

struct Patient: Identifiable, Codable, Hashable {
    let id: UUID
    var name: String
    var lastSeen: Date

    init(id: UUID = UUID(), name: String, lastSeen: Date = .now) {
        self.id = id
        self.name = name
        self.lastSeen = lastSeen
    }


}

// MARK: - PatientStore

@Observable
final class PatientStore {
    private(set) var patients: [Patient] = []

    private let key = "rayban_pt.patients"

    init() { load() }

    // 최근 본 순 정렬
    var recent: [Patient] {
        patients.sorted { $0.lastSeen > $1.lastSeen }
    }

    func search(_ query: String) -> [Patient] {
        guard !query.trimmingCharacters(in: .whitespaces).isEmpty else { return recent }
        return recent.filter {
            $0.name.localizedCaseInsensitiveContains(query)
        }
    }

    /// 새 환자 추가 or 기존 환자 lastSeen 갱신
    @discardableResult
    func touch(name: String) -> Patient {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        if let idx = patients.firstIndex(where: {
            $0.name.localizedCaseInsensitiveCompare(trimmed) == .orderedSame
        }) {
            patients[idx].lastSeen = .now
            save()
            return patients[idx]
        } else {
            let p = Patient(name: trimmed)
            patients.append(p)
            save()
            return p
        }
    }

    func delete(_ patient: Patient) {
        patients.removeAll { $0.id == patient.id }
        save()
    }

    // MARK: - Persistence

    private func load() {
        guard let data = UserDefaults.standard.data(forKey: key),
              let decoded = try? JSONDecoder().decode([Patient].self, from: data)
        else { return }
        patients = decoded
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(patients) else { return }
        UserDefaults.standard.set(data, forKey: key)
    }
}
