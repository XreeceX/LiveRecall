// BackendClient.swift
//
// Thin async client for the LiveRecall FastAPI control plane. Two endpoints:
//
//   - POST /token : issues the LiveKit JWT and stamps capture_mode on the
//                   session document. Called once at connect-time.
//   - POST /snap  : single-image retrieval. Takes a base64 JPEG (and an
//                   optional question), runs Vision synchronously, writes a
//                   scene_context row, and optionally creates a `questions`
//                   doc that triggers the rest of the pipeline. Used by the
//                   "Snap & ask" path that pulls a still off the glasses
//                   stream via WearablesController.capturePhoto().

import Foundation

struct TokenResponse: Decodable {
    let token: String
    let url: String
    let room: String
    let capture_mode: String
}

/// Mirrors backend SnapResp (backend/main.py lines 127-133). The dashboard
/// reasoning trace is keyed off `question_id` when it's present, so we
/// surface that to the UI for a one-tap "open in dashboard" follow-up.
struct SnapResponse: Decodable {
    let scene_context_id: String
    let question_id: String?
    let objects: [String]
    let text_visible: [String]
    let text_summary: String
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
        let req = try makeRequest(backendURL: backendURL, path: "token", body: [
            "identity": identity,
            "room": room,
            "capture_mode": captureMode,
        ])
        return try await send(req)
    }

    /// Hits POST {backend}/snap — same body shape as phone/glasses.html
    /// (lines 536-567). `imageB64` is a plain base64 string, no `data:` prefix
    /// needed (the backend strips it but it's cheaper to skip); `question`
    /// is optional and, when non-empty, also creates a `questions` doc that
    /// kicks off Router → Retrievers → Reranker → Answerer.
    func snap(
        backendURL: String,
        sessionId: String,
        imageB64: String,
        question: String? = nil,
        captureMode: String = "glasses"
    ) async throws -> SnapResponse {
        var body: [String: Any] = [
            "session_id": sessionId,
            "image_b64": imageB64,
            "capture_mode": captureMode,
        ]
        if let q = question?.trimmingCharacters(in: .whitespacesAndNewlines), !q.isEmpty {
            body["question"] = q
        }
        let req = try makeRequest(backendURL: backendURL, path: "snap", body: body)
        return try await send(req)
    }

    // MARK: - Private helpers

    private func makeRequest(backendURL: String, path: String, body: [String: Any]) throws -> URLRequest {
        guard let base = URL(string: backendURL.trimmingCharacters(in: .whitespacesAndNewlines)),
              base.scheme == "http" || base.scheme == "https" else {
            throw BackendError.badURL
        }
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        // /snap can take 2-3s when Vision runs synchronously. Default
        // URLSession timeout (60s) is plenty, but be explicit so a stuck
        // backend doesn't stall the UI for a full minute.
        req.timeoutInterval = 30
        return req
    }

    private func send<T: Decodable>(_ req: URLRequest) async throws -> T {
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let code = (resp as? HTTPURLResponse)?.statusCode ?? -1
            throw BackendError.http(code, String(data: data, encoding: .utf8) ?? "")
        }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw BackendError.decode(error.localizedDescription)
        }
    }
}
