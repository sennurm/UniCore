"use client";

import { useEffect, useState } from "react";
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

  // Arriving here means the previous session is gone (or never existed).
  useEffect(() => {
    setToken(null);
  }, []);

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
    <div className="uc-login">
      <div className="uc-login-card">
        <div className="uc-brand" style={{ marginBottom: 4 }}>
          <div className="uc-brand-name">UniCore</div>
          <div className="uc-brand-sub">University operations core</div>
        </div>
        {stage === "password" && (
          <form onSubmit={submitPassword}>
            <div className="card-kicker" style={{ marginBottom: 12 }}>Sign in</div>
            <div className="field">
              <label>Username</label>
              <input
                className="input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                required
              />
            </div>
            <div className="field">
              <label>Password</label>
              <input
                className="input"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            <button className="btn btn-primary" style={{ width: "100%" }} type="submit">
              Continue
            </button>
          </form>
        )}
        {stage === "otp" && (
          <form onSubmit={submitOtp}>
            <div className="card-kicker" style={{ marginBottom: 6 }}>One-time password</div>
            <p className="text-muted" style={{ fontSize: 13 }}>
              An OTP was sent to your registered contact. It is valid for 5 minutes.
            </p>
            <div className="field">
              <label>6-digit OTP</label>
              <input
                className="input"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                maxLength={6}
                autoFocus
                required
              />
            </div>
            <button className="btn btn-primary" style={{ width: "100%" }} type="submit">
              Verify
            </button>
          </form>
        )}
        {stage === "force-change" && (
          <form onSubmit={submitNewPassword}>
            <div className="card-kicker" style={{ marginBottom: 6 }}>First login</div>
            <p className="text-muted" style={{ fontSize: 13 }}>
              Choose a new password — at least 10 characters.
            </p>
            <div className="field">
              <label>New password</label>
              <input
                className="input"
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                minLength={10}
                autoFocus
                required
              />
            </div>
            <button className="btn btn-primary" style={{ width: "100%" }} type="submit">
              Set password
            </button>
          </form>
        )}
        {error && <p className="error">{error}</p>}
      </div>
    </div>
  );
}
