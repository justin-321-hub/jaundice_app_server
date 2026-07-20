from pydantic import BaseModel, Field


class CreateBabyAccountReq(BaseModel):
    baby_name: str
    baby_code: str
    gender: str
    birth_date: str  # 先用字串，例：2026-03-24
    gestational_weeks: float = Field(ge=20, le=45)
    parent_email: str
    parent_password: str
    medical_record_no: str | None = ""
    ward_no: str | None = ""
    bed_no: str | None = ""