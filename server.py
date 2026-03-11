import json
import random
from PIL import Image
import numpy as np
import io
from fastapi import FastAPI, Depends, Form, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from database import Base, engine, get_db
from models import User, Record
from schemas import RegisterReq, LoginReq, TokenResp, RoleReq, RecordCreateReq
from auth import hash_password, verify_password, create_access_token
from deps import get_current_user

# 建表
Base.metadata.create_all(bind=engine)

app = FastAPI(title="JaundiceGuardian SQLite API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # demo 先開放
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True}


# 註冊
@app.post("/auth/register", response_model=TokenResp)
def register(req: RegisterReq, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already exists")

    user = User(
        email=req.email,
        password_hash=hash_password(req.password),
        role="parent",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id)
    return TokenResp(access_token=token)


# 登入
@app.post("/auth/login", response_model=TokenResp)
def login(req: LoginReq, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user.id)
    return TokenResp(access_token=token)


# 更新角色
@app.post("/me/role")
def update_role(
    req: RoleReq,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.role = req.role
    db.commit()
    return {"ok": True, "role": current_user.role}


# 取得自己資料
@app.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "role": current_user.role,
    }

# 黃色指數計算
def compute_yellow_index_from_image_bytes(
    image_bytes: bytes,
    roi_x: float | None = None,
    roi_y: float | None = None,
    roi_w: float | None = None,
    roi_h: float | None = None,
):
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    arr = np.array(image).astype(np.float32)

    h, w, _ = arr.shape

    # 有手動 ROI 就只取 ROI，否則用整張圖
    if None not in (roi_x, roi_y, roi_w, roi_h):
        # 先把 ROI 限制在 0~1
        roi_x = max(0.0, min(float(roi_x), 1.0))
        roi_y = max(0.0, min(float(roi_y), 1.0))
        roi_w = max(0.01, min(float(roi_w), 1.0))
        roi_h = max(0.01, min(float(roi_h), 1.0))

        x1 = int(roi_x * w)
        y1 = int(roi_y * h)
        x2 = int((roi_x + roi_w) * w)
        y2 = int((roi_y + roi_h) * h)

        # 再限制一次邊界，避免空陣列
        x1 = max(0, min(x1, w - 1))
        y1 = max(0, min(y1, h - 1))
        x2 = max(x1 + 1, min(x2, w))
        y2 = max(y1 + 1, min(y2, h))

        roi = arr[y1:y2, x1:x2, :]
    else:
        roi = arr

    if roi.size == 0:
        return 0.0, 0.0, "low"

    r = roi[:, :, 0]
    g = roi[:, :, 1]
    b = roi[:, :, 2]

    lum = 0.299 * r + 0.587 * g + 0.114 * b
    mask = (lum >= 20) & (lum <= 245)

    if mask.sum() == 0:
        return 0.0, 0.0, "low"

    r_mean = float(r[mask].mean())
    g_mean = float(g[mask].mean())
    b_mean = float(b[mask].mean())

    rgb_sum = r_mean + g_mean + b_mean
    if rgb_sum <= 0:
        yellow_raw = 0.0
    else:
        yellow_raw = (((r_mean + g_mean) / 2.0) - b_mean) / rgb_sum

    yellow_index = max(0.0, min(100.0, yellow_raw * 400.0))
    score01 = max(0.0, min(1.0, yellow_index / 100.0))

    if yellow_index < 35:
        risk_level = "low"
    elif yellow_index < 60:
        risk_level = "medium"
    else:
        risk_level = "high"

    return yellow_index, score01, risk_level


@app.post("/analyze")
async def analyze(
    image: UploadFile = File(...),
    roi_x: float | None = Form(None),
    roi_y: float | None = Form(None),
    roi_w: float | None = Form(None),
    roi_h: float | None = Form(None),
    current_user = Depends(get_current_user),
    
):
    try:
        image_bytes = await image.read()

        yellow_index, score01, risk_level = compute_yellow_index_from_image_bytes(
            image_bytes=image_bytes,
            roi_x=roi_x,
            roi_y=roi_y,
            roi_w=roi_w,
            roi_h=roi_h,
        )

        advice = {
            "low": "屬低風險區間，建議持續觀察。",
            "medium": "屬中風險區間，建議 6–12 小時內加強觀察。",
            "high": "屬高風險區間，建議盡快就醫評估。",
        }[risk_level]

        rois = []
        if None not in (roi_x, roi_y, roi_w, roi_h):
            roi_x = max(0.0, min(float(roi_x), 1.0))
            roi_y = max(0.0, min(float(roi_y), 1.0))
            roi_w = max(0.01, min(float(roi_w), 1.0))
            roi_h = max(0.01, min(float(roi_h), 1.0))

            rois = [
                {
                    "type": "circle",
                    "cx": roi_x + roi_w / 2.0,
                    "cy": roi_y + roi_h / 2.0,
                    "r": min(roi_w, roi_h) / 2.0,
                    "label": "selected_roi",
                }
            ]

        return {
            "quality": {"ok": True, "warnings": []},
            "rois": rois,
            "yellow_index": round(yellow_index, 1),
            "score01": round(score01, 2),
            "risk_level": risk_level,
            "advice": advice,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"analyze failed: {str(e)}")
        
# 新增紀錄
@app.post("/records")
def create_record(
    req: RecordCreateReq,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = Record(
        user_id=current_user.id,
        baby_name=req.baby_name,
        image_path=req.image_path,
        yellow_index=req.yellow_index,
        score01=req.score01,
        risk_level=req.risk_level,
        warnings_json=json.dumps(req.warnings, ensure_ascii=False),
        advice=req.advice,
        rois_json=json.dumps([r.model_dump() for r in req.rois], ensure_ascii=False),
        roi_rect_json=json.dumps(req.roi_rect.model_dump(), ensure_ascii=False) if req.roi_rect else "{}",
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return {"ok": True, "record_id": record.id}

# 刪除一筆資料
@app.delete("/records/{record_id}")
def delete_record(
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(Record)
        .filter(Record.id == record_id, Record.user_id == current_user.id)
        .first()
    )

    if not row:
        raise HTTPException(status_code=404, detail="Record not found")

    db.delete(row)
    db.commit()

    return {"ok": True}

# 刪除多筆資料
from pydantic import BaseModel

class RecordDeleteReq(BaseModel):
    ids: list[int]
    
@app.post("/records/delete")
def delete_records(
    req: RecordDeleteReq,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Record)
        .filter(Record.user_id == current_user.id, Record.id.in_(req.ids))
        .all()
    )

    for row in rows:
        db.delete(row)

    db.commit()
    return {"ok": True, "deleted": len(rows)}

# 取得自己的紀錄
@app.get("/records")
def get_records(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Record)
        .filter(Record.user_id == current_user.id)
        .order_by(Record.created_at.desc())
        .all()
    )

    return [
        {
            "id": r.id,
            "baby_name": r.baby_name,
            "image_path": r.image_path,
            "yellow_index": r.yellow_index,
            "score01": r.score01,
            "risk_level": r.risk_level,
            "warnings": json.loads(r.warnings_json or "[]"),
            "advice": r.advice,
            "rois": json.loads(r.rois_json or "[]"),
            "roi_rect": json.loads(r.roi_rect_json or "{}"),
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in rows
    ]