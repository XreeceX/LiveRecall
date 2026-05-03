// BackendClient.swift
//
// Thin async client for the LiveRecall FastAPI control plane. Today we only
// need POST /token (issues the LiveKit JWT and stamps capture_mode on the
// session document). /snap is supported too for parity with the phone HTML
// page if we ever add a "Snap & ask" button to this app.

import Foundation

struct TokenResponse: Decodable {
    let token: String
    let url: String
    let room: String
    let capture_mode: String
}

enum BackendError: LocalizedError {
    case badURL
    case http(Int, String)
    case decode(String)

    var errorDescription: String? {
        switch self {
        case .badURL: return "Backend URL is not a valid http(s) URL."
        case .http(let code, let body): return "Backend HTTP \(code): \(body)"
        case .decode(let detail): return "Backend response decode failed: \(detail)"
        }
    }
}

actor BackendClient {
    static let shared = BackendClient()

    /// Hits POST {backend}/token — same body shape as phone/glasses.html.
    /// `captureMode` is "glasses" by default so Vision uses the first-person POV hint.
    func requestToken(
        backendURL: String,
        identity: String,
        room: String,
        captureMode: String = "glasses"
    ) async throws -> TokenResponse {
        guard let base = URL(string: backendURL.trimmingCharacters(in: .whitespacesAndNewlines)),
              base.scheme == "http" || base.scheme == "https" else {
            throw BackendError.badURL
        }
        var req = URLRequest(url: base.appendingPathComponent("token"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "identity": identity,
            "room": room,
            "capture_mode": captureMode
        ]
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let code = (resp as? HTTPURLResponse)?.statusCode ?? -1
            throw BackendError.http(code, String(data: data, encoding: .utf8) ?? "")
        }
        do {
            return try JSONDecoder().decode(TokenResponse.self, from: data)
        } catch {
            throw BackendError.decode(error.localizedDescription)
        }
    }
}
