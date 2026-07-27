"""Pydantic request/response models for the auth module."""

import uuid

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    challenge_id: uuid.UUID
    message: str = "OTP sent to your registered contact."


class OtpVerifyRequest(BaseModel):
    challenge_id: uuid.UUID
    code: str = Field(min_length=6, max_length=6)


class SessionResponse(BaseModel):
    token: str
    force_password_change: bool


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10)


class PasswordResetRequest(BaseModel):
    username: str


class PasswordResetConfirm(BaseModel):
    challenge_id: uuid.UUID
    code: str = Field(min_length=6, max_length=6)
    new_password: str = Field(min_length=10)


class DeviceRegisterRequest(BaseModel):
    fingerprint: str = Field(min_length=8, max_length=200)


class DeviceOut(BaseModel):
    id: uuid.UUID
    fingerprint: str
    status: str

    model_config = {"from_attributes": True}


class DeviceChangeRequestIn(BaseModel):
    new_fingerprint: str = Field(min_length=8, max_length=200)


class DeviceChangeRequestOut(BaseModel):
    id: uuid.UUID
    status: str
    new_fingerprint: str

    model_config = {"from_attributes": True}


class ConsentIn(BaseModel):
    notice_version: str
    geolocation_consent: bool = False


class MeResponse(BaseModel):
    user_id: str
    roles: tuple[str, ...]
