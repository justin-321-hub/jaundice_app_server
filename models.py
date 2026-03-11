from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="parent")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    records = relationship("Record", back_populates="user")


class Record(Base):
    __tablename__ = "records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    baby_name = Column(String)
    image_path = Column(String, default="")
    yellow_index = Column(Float, nullable=False)
    score01 = Column(Float, nullable=False)
    risk_level = Column(String, nullable=False)
    warnings_json = Column(Text, default="[]")
    advice = Column(Text, default="")
    rois_json = Column(Text, default="[]")
    roi_rect_json = Column(Text, default="{}")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="records")