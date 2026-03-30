import io
from typing import Optional
from firebase_admin import auth, firestore
from pydantic import BaseModel
from .schemas_baby import CreateBabyAccountReq
from .schemas_admin import CreateClinicianAccountReq
import numpy as np
from PIL import Image
from fastapi import FastAPI, Depends, Form, HTTPException, UploadFile, File, Header
from fastapi.middleware.cors import CORSMiddleware
from .firebase_verify import verify_firebase_user
from .firebase_config import init_firebase

init_firebase()

db = firestore.client()

app = FastAPI(title="JaundiceGuardian AI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # demo先開
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True}


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

    if None not in (roi_x, roi_y, roi_w, roi_h):
        roi_x = max(0.0, min(float(roi_x), 1.0))
        roi_y = max(0.0, min(float(roi_y), 1.0))
        roi_w = max(0.01, min(float(roi_w), 1.0))
        roi_h = max(0.01, min(float(roi_h), 1.0))

        x1 = int(roi_x * w)
        y1 = int(roi_y * h)
        x2 = int((roi_x + roi_w) * w)
        y2 = int((roi_y + roi_h) * h)

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
    roi_x: Optional[float] = Form(None),
    roi_y: Optional[float] = Form(None),
    roi_w: Optional[float] = Form(None),
    roi_h: Optional[float] = Form(None),
    authorization: Optional[str] = Header(None),
):
    try:
        # 這裡可拿到 Firebase uid
        uid = None
        if authorization:
          try:
              token = authorization.replace("Bearer ", "")
              decoded = auth.verify_id_token(token)
              uid = decoded.get("uid")
          except:
              uid = None

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
            "ok": True,
            "uid": uid,
            "quality": {"ok": True, "warnings": []},
            "rois": rois,
            "yellow_index": round(yellow_index, 1),
            "score01": round(score01, 2),
            "risk_level": risk_level,
            "advice": advice,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"analyze failed: {str(e)}")
    
@app.post("/create-baby-account")
async def create_baby_account(
    req: CreateBabyAccountReq,
    firebase_user: dict = Depends(verify_firebase_user),
):
    try:
        clinician_uid = firebase_user.get("uid")
        clinician_doc = db.collection("users").document(clinician_uid).get()

        if not clinician_doc.exists:
            raise HTTPException(status_code=403, detail="Clinician profile not found")

        clinician_data = clinician_doc.to_dict() or {}
        if clinician_data.get("role") != "clinician":
            raise HTTPException(status_code=403, detail="Only clinician can create baby accounts")

        parent_uid = None
        created_new_parent = False

        # 先檢查這個 parent email 是否已存在 Firebase Auth
        try:
            existing_user = auth.get_user_by_email(req.parent_email)
            parent_uid = existing_user.uid
        except auth.UserNotFoundError:
            existing_user = None

        # 不存在才建立新 parent 帳號
        if existing_user is None:
            parent_user = auth.create_user(
                email=req.parent_email,
                password=req.parent_password,
            )
            parent_uid = parent_user.uid
            created_new_parent = True

        # 建立 baby 文件
        baby_ref = db.collection("babies").document()
        baby_id = baby_ref.id

        # 先讀 parent user 文件
        parent_doc_ref = db.collection("users").document(parent_uid)
        parent_doc = parent_doc_ref.get()

        if parent_doc.exists:
            parent_data = parent_doc.to_dict() or {}
            current_baby_ids = parent_data.get("babyIds", [])
            if not isinstance(current_baby_ids, list):
                current_baby_ids = []

            # 相容舊單一 babyId
            single_baby_id = parent_data.get("babyId")
            if isinstance(single_baby_id, str) and single_baby_id:
                if single_baby_id not in current_baby_ids:
                    current_baby_ids.append(single_baby_id)

            if baby_id not in current_baby_ids:
                current_baby_ids.append(baby_id)

            parent_doc_ref.set({
                "name": parent_data.get("name", f"{req.baby_name} Parent"),
                "email": req.parent_email,
                "role": "parent",
                "babyIds": current_baby_ids,
                "isActive": True,
                "updatedAt": firestore.SERVER_TIMESTAMP,
            }, merge=True)
        else:
            parent_doc_ref.set({
                "name": f"{req.baby_name} Parent",
                "email": req.parent_email,
                "role": "parent",
                "babyIds": [baby_id],
                "isActive": True,
                "createdAt": firestore.SERVER_TIMESTAMP,
                "updatedAt": firestore.SERVER_TIMESTAMP,
            })

        # 建立 babies/{babyId}
        baby_ref.set({
            "babyName": req.baby_name,
            "babyCode": req.baby_code,
            "gender": req.gender,
            "birthDate": req.birth_date,
            "parentOwnerIds": [parent_uid],
            "createdByStaffId": clinician_uid,
            "medicalRecordNo": req.medical_record_no or "",
            "wardNo": req.ward_no or "",
            "bedNo": req.bed_no or "",
            "dischargeStatus": "admitted",
            "createdAt": firestore.SERVER_TIMESTAMP,
            "updatedAt": firestore.SERVER_TIMESTAMP,
        })

        return {
            "ok": True,
            "babyId": baby_id,
            "parentUid": parent_uid,
            "parentEmail": req.parent_email,
            "createdNewParent": created_new_parent,
        }

    except auth.EmailAlreadyExistsError:
        raise HTTPException(status_code=400, detail="Parent email already exists")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"create baby account failed: {e}")
    
@app.post("/create-clinician-account")
async def create_clinician_account(
    req: CreateClinicianAccountReq,
    firebase_user: dict = Depends(verify_firebase_user),
):
    try:
        requester_uid = firebase_user.get("uid")
        requester_doc = db.collection("users").document(requester_uid).get()

        if not requester_doc.exists:
            raise HTTPException(status_code=403, detail="User profile not found")

        requester_data = requester_doc.to_dict() or {}
        if requester_data.get("role") != "superuser":
            raise HTTPException(status_code=403, detail="Only superuser can create clinician accounts")

        try:
            existing_user = auth.get_user_by_email(req.email)
            raise HTTPException(status_code=400, detail="Clinician email already exists")
        except auth.UserNotFoundError:
            pass

        clinician_user = auth.create_user(
            email=req.email,
            password=req.password,
        )

        clinician_uid = clinician_user.uid

        db.collection("users").document(clinician_uid).set({
            "name": req.name,
            "email": req.email,
            "role": "clinician",
            "department": req.department or "",
            "phone": req.phone or "",
            "isActive": True,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "createdBy": requester_uid,
        })

        return {
            "ok": True,
            "uid": clinician_uid,
            "email": req.email,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"create clinician failed: {e}")
    
@app.get("/clinicians")
async def get_clinicians(firebase_user: dict = Depends(verify_firebase_user)):
    uid = firebase_user.get("uid")

    user_doc = db.collection("users").document(uid).get()
    if not user_doc.exists or user_doc.to_dict().get("role") != "superuser":
        raise HTTPException(status_code=403, detail="Permission denied")

    docs = db.collection("users").where("role", "==", "clinician").stream()

    result = []
    for doc in docs:
        d = doc.to_dict()
        result.append({
            "uid": doc.id,
            "name": d.get("name"),
            "email": d.get("email"),
            "department": d.get("department"),
            "phone": d.get("phone"),
            "isActive": d.get("isActive", True),
        })

    return {"data": result}

class ToggleClinicianReq(BaseModel):
    uid: str
    isActive: bool


@app.post("/toggle-clinician")
async def toggle_clinician(
    req: ToggleClinicianReq,
    firebase_user: dict = Depends(verify_firebase_user),
):
    requester_uid = firebase_user.get("uid")

    user_doc = db.collection("users").document(requester_uid).get()
    if not user_doc.exists or user_doc.to_dict().get("role") != "superuser":
        raise HTTPException(status_code=403, detail="Permission denied")

    db.collection("users").document(req.uid).update({
        "isActive": req.isActive
    })

    return {"ok": True}

@app.get("/parents")
async def get_parents(firebase_user: dict = Depends(verify_firebase_user)):
    uid = firebase_user.get("uid")

    user_doc = db.collection("users").document(uid).get()
    if not user_doc.exists or user_doc.to_dict().get("role") != "superuser":
        raise HTTPException(status_code=403, detail="Permission denied")

    docs = db.collection("users").where("role", "==", "parent").stream()

    result = []
    for doc in docs:
        d = doc.to_dict() or {}
        baby_ids = d.get("babyIds", [])
        if not isinstance(baby_ids, list):
            baby_ids = []

        single_baby_id = d.get("babyId")
        if isinstance(single_baby_id, str) and single_baby_id:
            if single_baby_id not in baby_ids:
                baby_ids.append(single_baby_id)

        result.append({
            "uid": doc.id,
            "name": d.get("name"),
            "email": d.get("email"),
            "phone": d.get("phone"),
            "isActive": d.get("isActive", True),
            "babyIds": baby_ids,
            "babyCount": len(baby_ids),
        })

    return {"data": result}

class ToggleParentReq(BaseModel):
    uid: str
    isActive: bool

@app.post("/toggle-parent")
async def toggle_parent(
    req: ToggleParentReq,
    firebase_user: dict = Depends(verify_firebase_user),
):
    requester_uid = firebase_user.get("uid")

    user_doc = db.collection("users").document(requester_uid).get()
    if not user_doc.exists or user_doc.to_dict().get("role") != "superuser":
        raise HTTPException(status_code=403, detail="Permission denied")

    db.collection("users").document(req.uid).update({
        "isActive": req.isActive
    })

    return {"ok": True}