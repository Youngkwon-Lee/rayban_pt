import SwiftUI

struct PatientPickerView: View {
    @Binding var selectedPatient: Patient?
    let store: PatientStore
    let onSelect: (Patient) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @State private var showNewPatient = false
    @State private var newName = ""
    @FocusState private var newNameFocused: Bool

    private var results: [Patient] { store.search(query) }

    var body: some View {
        NavigationStack {
            List {
                // 새 환자 입력
                if showNewPatient {
                    Section {
                        HStack(spacing: 12) {
                            Image(systemName: "person.badge.plus")
                                .foregroundStyle(.blue)
                            TextField("환자 이름 입력", text: $newName)
                                .focused($newNameFocused)
                                .submitLabel(.done)
                                .onSubmit { confirmNew() }
                            if !newName.isEmpty {
                                Button {
                                    confirmNew()
                                } label: {
                                    Image(systemName: "checkmark.circle.fill")
                                        .foregroundStyle(.green)
                                        .font(.title3)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(.vertical, 4)
                    } header: {
                        Text("새 환자")
                    }
                }

                // 환자 목록
                if results.isEmpty && !query.isEmpty {
                    Section {
                        Button {
                            newName = query
                            showNewPatient = true
                            newNameFocused = true
                        } label: {
                            Label("\"\(query)\" 새 환자로 추가", systemImage: "person.badge.plus")
                        }
                    }
                } else {
                    Section {
                        ForEach(results) { patient in
                            PatientRow(patient: patient) {
                                select(patient)
                            }
                        }
                        .onDelete { idx in
                            idx.forEach { store.delete(results[$0]) }
                        }
                    } header: {
                        Text(query.isEmpty ? "최근 환자" : "검색 결과")
                    }
                }
            }
            .navigationTitle("환자 선택")
            .navigationBarTitleDisplayMode(.large)
            .searchable(text: $query, prompt: "이름 검색")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("취소") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        withAnimation {
                            showNewPatient = true
                            newNameFocused = true
                        }
                    } label: {
                        Image(systemName: "person.badge.plus")
                    }
                }
            }
            .animation(.spring(response: 0.3), value: showNewPatient)
        }
    }

    private func select(_ patient: Patient) {
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        store.touch(name: patient.name)
        selectedPatient = patient
        onSelect(patient)
        dismiss()
    }

    private func confirmNew() {
        let trimmed = newName.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }
        let patient = store.touch(name: trimmed)
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        selectedPatient = patient
        onSelect(patient)
        dismiss()
    }
}

// MARK: - 환자 행

private struct PatientRow: View {
    let patient: Patient
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 14) {
                // 아바타
                ZStack {
                    Circle()
                        .fill(avatarColor(for: patient.name).opacity(0.15))
                        .frame(width: 40, height: 40)
                    Text(initials(patient.name))
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(avatarColor(for: patient.name))
                }

                VStack(alignment: .leading, spacing: 2) {
                    Text(patient.name)
                        .font(.body)
                        .foregroundStyle(.primary)
                    Text(relativeDate(patient.lastSeen))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Spacer()

                Image(systemName: "chevron.right")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func initials(_ name: String) -> String {
        let parts = name.components(separatedBy: " ").filter { !$0.isEmpty }
        if parts.count >= 2 {
            return String(parts[0].prefix(1)) + String(parts[1].prefix(1))
        }
        return String(name.prefix(2))
    }

    private func relativeDate(_ date: Date) -> String {
        let formatter = RelativeDateTimeFormatter()
        formatter.locale = Locale(identifier: "ko_KR")
        formatter.unitsStyle = .abbreviated
        return "마지막 방문: " + formatter.localizedString(for: date, relativeTo: .now)
    }

    private func avatarColor(for name: String) -> Color {
        let colors: [Color] = [.blue, .purple, .pink, .orange, .teal, .indigo, .green]
        let hash = abs(name.hashValue)
        return colors[hash % colors.count]
    }
}
