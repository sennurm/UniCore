"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, setToken } from "@/lib/api";

type Stage = "password" | "otp" | "force-change";

export default function LoginPage() {
  const router = useRouter();
  const [stage, setStage] = useState<Stage>("password");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [challengeId, setChallengeId] = useState("");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState("");

  async function submitPassword(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const res = await api<{
        challenge_id: string | null;
        token: string | null;
        force_password_change: boolean | null;
      }>("/auth/login", { method: "POST", body: { username, password } });
      if (res.token) {
        // OTP disabled in this environment: session issued directly.
        setToken(res.token);
        if (res.force_password_change) setStage("force-change");
        else router.push("/dashboard");
        return;
      }
      setChallengeId(res.challenge_id ?? "");
      setStage("otp");
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function submitOtp(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const res = await api<{ token: string; force_password_change: boolean }>(
        "/auth/otp/verify",
        { method: "POST", body: { challenge_id: challengeId, code } },
      );
      setToken(res.token);
      if (res.force_password_change) setStage("force-change");
      else router.push("/dashboard");
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function submitNewPassword(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/auth/password", {
        method: "POST",
        body: { current_password: password, new_password: newPassword },
      });
      router.push("/dashboard");
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  return (
    <main>
      <h1>UniCore sign in</h1>
      <div className="panel">
        {stage === "password" && (
          <form onSubmit={submitPassword}>
            <label>Username</label>
            <input value={username} onChange={(e) => setUsername(e.target.value)} required />
            <label>Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            <button type="submit">Continue</button>
          </form>
        )}
        {stage === "otp" && (
          <form onSubmit={submitOtp}>
            <p>An OTP was sent to your registered contact.</p>
            <label>6-digit OTP</label>
            <input value={code} onChange={(e) => setCode(e.target.value)} maxLength={6} required />
            <button type="submit">Verify</button>
          </form>
        )}
        {stage === "force-change" && (
          <form onSubmit={submitNewPassword}>
            <p>First login: choose a new password (minimum 10 characters).</p>
            <label>New password</label>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              minLength={10}
              required
            />
            <button type="submit">Set password</button>
          </form>
        )}
        {error && <p className="error">{error}</p>}
      </div>
    </main>
  );
}
