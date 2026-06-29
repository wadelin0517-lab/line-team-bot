import os
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import List, Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    ApiClient, Configuration, MessagingApi,
    PushMessageRequest, TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import init_db, get_db, Todo, RecurringReminder, Member, SessionLocal
from line_handler import handle_message
from scheduler import start_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

line_configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
webhook_handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler = start_scheduler()
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


# ── LINE Webhook ──────────────────────────────────────────────────────────────

@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    try:
        webhook_handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return {"status": "ok"}


@webhook_handler.add(MessageEvent, message=TextMessageContent)
def on_text_message(event: MessageEvent):
    db = SessionLocal()
    try:
        with ApiClient(line_configuration) as api_client:
            messaging_api = MessagingApi(api_client)
            handle_message(event.source.user_id, event.message.text, event.reply_token, db, messaging_api)
    finally:
        db.close()


# ── 網頁介面 ──────────────────────────────────────────────────────────────────

_WEEKDAY_NAMES = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]


def _recurrence_label(r: RecurringReminder) -> str:
    if r.repeat_type == "weekly":
        return f"每{_WEEKDAY_NAMES[r.day_of_week]}"
    return f"每月{r.day_of_month}號"


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db)):
    todos = db.query(Todo).order_by(Todo.due_date).all()
    today = date.today()
    todo_list = [
        {
            "id": t.id,
            "title": t.title,
            "content": t.content,
            "description": t.description,
            "photo_url": t.photo_url,
            "due_date": str(t.due_date),
            "days_left": (t.due_date - today).days,
        }
        for t in todos
    ]
    reminders = db.query(RecurringReminder).order_by(RecurringReminder.id).all()
    reminder_list = [
        {"id": r.id, "content": r.content, "label": _recurrence_label(r)}
        for r in reminders
    ]
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "todos": todo_list, "today": str(today), "reminders": reminder_list},
    )


@app.post("/add")
async def add_todo(
    content: str = Form(...),
    due_date: str = Form(...),
    db: Session = Depends(get_db),
):
    todo = Todo(title=content, due_date=due_date)
    db.add(todo)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/delete/{todo_id}")
async def delete_todo(todo_id: int, db: Session = Depends(get_db)):
    todo = db.query(Todo).filter(Todo.id == todo_id).first()
    if todo:
        db.delete(todo)
        db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/recurring/add")
async def add_recurring(
    content: str = Form(...),
    repeat_type: str = Form(...),
    day_of_week: str = Form(None),
    day_of_month: str = Form(None),
    db: Session = Depends(get_db),
):
    dow = int(day_of_week) if day_of_week not in (None, "") else None
    dom = int(day_of_month) if day_of_month not in (None, "") else None
    reminder = RecurringReminder(
        content=content, repeat_type=repeat_type, day_of_week=dow, day_of_month=dom
    )
    db.add(reminder)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/recurring/delete/{reminder_id}")
async def delete_recurring(reminder_id: int, db: Session = Depends(get_db)):
    reminder = db.query(RecurringReminder).filter(RecurringReminder.id == reminder_id).first()
    if reminder:
        db.delete(reminder)
        db.commit()
    return RedirectResponse(url="/", status_code=303)


# ── LIFF 頁面 ─────────────────────────────────────────────────────────────────

@app.get("/liff", response_class=HTMLResponse)
async def liff_page(request: Request):
    return templates.TemplateResponse("liff.html", {"request": request})


# ── LIFF API ──────────────────────────────────────────────────────────────────

_OFFSET_DELTA: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
}


class CreateTodoRequest(BaseModel):
    title: str
    content: Optional[str] = None
    description: Optional[str] = None
    photo_url: Optional[str] = None
    due_date: str  # YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS
    created_by: str
    notify_enabled: bool = False
    notify_immediate: bool = False  # True → 建立時直接推播，不走 scheduler
    notify_offset: Optional[str] = None  # 15m | 1h | 1d | custom（排程提醒用）
    custom_notify_time: Optional[str] = None  # ISO datetime，notify_offset=custom 時使用
    notify_targets: List[str] = []


def _parse_due_datetime(due_date_str: str) -> datetime:
    try:
        return datetime.fromisoformat(due_date_str)
    except ValueError:
        return datetime.fromisoformat(due_date_str + "T00:00:00")


def _calc_notify_time(
    due_date_str: str,
    notify_offset: str,
    custom_notify_time: Optional[str],
) -> datetime:
    if notify_offset == "custom":
        if not custom_notify_time:
            raise HTTPException(status_code=422, detail="notify_offset=custom 時必須提供 custom_notify_time")
        return datetime.fromisoformat(custom_notify_time)
    delta = _OFFSET_DELTA.get(notify_offset)
    if not delta:
        raise HTTPException(status_code=422, detail=f"不支援的 notify_offset: {notify_offset}")
    return _parse_due_datetime(due_date_str) - delta


def _push_immediate_notification(todo: Todo, targets: List[str], db: Session) -> None:
    """直接呼叫 LINE Push Message API 發送立即通知，繞過 scheduler。"""
    from database import Member

    if "all" in targets:
        members = db.query(Member).all()
        user_ids = [m.line_user_id for m in members]
    else:
        user_ids = list(targets)

    if not user_ids:
        logger.warning("立即通知：找不到任何收件人，略過")
        return

    lines = [f"【待辦通知】{todo.title}", f"到期日：{todo.due_date}"]
    if todo.description:
        lines.append(f"備註：{todo.description}")
    msg_text = "\n".join(lines)

    try:
        with ApiClient(line_configuration) as api_client:
            messaging_api = MessagingApi(api_client)
            for uid in user_ids:
                messaging_api.push_message(
                    PushMessageRequest(to=uid, messages=[TextMessage(text=msg_text)])
                )
        todo.notified_at = datetime.now()
        db.commit()
        logger.info(f"立即通知已推播給 {len(user_ids)} 位使用者，todo_id={todo.id}")
    except Exception as e:
        logger.error(f"立即通知推播失敗 todo_id={todo.id}: {e}")


@app.post("/api/todos", status_code=201)
async def api_create_todo(payload: CreateTodoRequest, db: Session = Depends(get_db)):
    targets = payload.notify_targets if payload.notify_targets else [payload.created_by]

    # notify_offset / notify_time 只給「設定提醒時間」路徑使用
    notify_time = None
    effective_offset = None
    if payload.notify_enabled and not payload.notify_immediate and payload.notify_offset:
        notify_time = _calc_notify_time(payload.due_date, payload.notify_offset, payload.custom_notify_time)
        effective_offset = payload.notify_offset

    due_date_obj = _parse_due_datetime(payload.due_date).date()

    todo = Todo(
        title=payload.title,
        content=payload.content,
        description=payload.description,
        photo_url=payload.photo_url,
        due_date=due_date_obj,
        created_by=payload.created_by,
        notify_enabled=payload.notify_enabled,
        notify_offset=effective_offset,
        notify_time=notify_time,
        notify_targets=targets,
    )
    db.add(todo)
    db.commit()
    db.refresh(todo)

    if payload.notify_enabled and payload.notify_immediate:
        _push_immediate_notification(todo, targets, db)

    return {
        "id": todo.id,
        "title": todo.title,
        "content": todo.content,
        "description": todo.description,
        "photo_url": todo.photo_url,
        "due_date": str(todo.due_date),
        "notify_time": notify_time.isoformat() if notify_time else None,
    }


@app.get("/api/members")
async def api_get_members(db: Session = Depends(get_db)):
    members = db.query(Member).order_by(Member.display_name).all()
    return [
        {"line_user_id": m.line_user_id, "display_name": m.display_name or m.line_user_id}
        for m in members
    ]
