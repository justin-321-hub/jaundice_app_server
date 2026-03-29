from pydantic import BaseModel, EmailStr


class CreateClinicianAccountReq(BaseModel):
    name: str
    email: EmailStr
    password: str
    department: str | None = ""
    phone: str | None = ""