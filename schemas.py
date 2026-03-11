from pydantic import BaseModel, EmailStr
from typing import List, Optional


class RegisterReq(BaseModel):
    email: EmailStr
    password: str


class LoginReq(BaseModel):
    email: EmailStr
    password: str


class TokenResp(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RoleReq(BaseModel):
    role: str


class RoiSchema(BaseModel):
    type: str
    cx: float
    cy: float
    r: float
    label: str

class RoiRectSchema(BaseModel):
    x: float
    y: float
    w: float
    h: float

class RecordCreateReq(BaseModel):
    baby_name: str
    image_path: str = ""
    yellow_index: float
    score01: float
    risk_level: str
    warnings: List[str] = []
    advice: str = ""
    rois: List[RoiSchema] = []
    roi_rect: Optional[RoiRectSchema] = None