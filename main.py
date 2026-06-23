import os
import logging
from contextlib import asynccontextmanager
from datetime import date

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import ApiClient, Configuration, MessagingApi
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError
from sqlalchemy.orm import Session

from database import init_db, get_db, Todo, RecurringReminder, SessionLocal
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
            "content": t.content,
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
    todo = Todo(content=content, due_date=due_date)
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
